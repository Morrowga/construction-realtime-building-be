# app/services/bim_service.py
import base64
import json
import logging
import os
import tempfile
from typing import Any

import fitz  # PyMuPDF — NEW dependency, see note at bottom of file
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger("app.bim")

try:
    import ifcopenshell
except ImportError:
    ifcopenshell = None

client = AsyncOpenAI(api_key=settings.openai_api_key or "not-set")

PDF_ZONE_PROMPT = """You are analysing an architectural floor plan. Identify every room, \
corridor, stairwell, mechanical space, and other named zone visible in the drawing.

Respond with ONLY valid JSON (no markdown fences) in this exact schema:
{
  "zones": [
    {"mesh_id": "Zone_<SanitisedName>_<Index>", "name": "<room name as written>", "approx_position": "<e.g. north-east corner>"}
  ]
}

Rules:
- "mesh_id" must be a stable ASCII identifier derived from the room name (no spaces).
- Include every distinct labelled space; skip dimension lines and annotations.
- If no zones can be identified, return {"zones": []}."""

PDF_RENDER_MAX_PAGES = 3   # floor-plan PDFs are rarely more than a couple pages
PDF_RENDER_ZOOM = 2.0      # ~144 DPI equivalent — enough detail for room labels


def parse_ifc(file_bytes: bytes) -> dict[str, str]:
    """Extract spaces/zones from an IFC file. Returns {mesh_id: zone_name}.

    Raises RuntimeError if IfcOpenShell is not installed. Unaffected by the
    OpenAI switch — this path never calls any LLM at all.
    """
    if ifcopenshell is None:
        raise RuntimeError(
            "IfcOpenShell is not installed. Install it with `pip install ifcopenshell` "
            "to enable IFC parsing."
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        model = ifcopenshell.open(tmp_path)
        zone_map: dict[str, str] = {}
        for space in model.by_type("IfcSpace"):
            mesh_id = getattr(space, "GlobalId", None) or f"space_{space.id()}"
            name = (
                getattr(space, "LongName", None)
                or getattr(space, "Name", None)
                or f"Space {space.id()}"
            )
            zone_map[str(mesh_id)] = str(name)
        logger.info("Parsed IFC file: %d spaces found", len(zone_map))
        return zone_map
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def _pdf_pages_to_png_b64(file_bytes: bytes, max_pages: int = PDF_RENDER_MAX_PAGES) -> list[str]:
    """Render each PDF page to a base64 PNG.

    IMPORTANT: this whole function exists because of a real capability gap
    between providers — Anthropic's API accepts a raw PDF directly as a
    "document" content block; OpenAI's chat completions API has no
    equivalent, only image content blocks. So the PDF has to be rasterised
    to images first. Capped at a few pages since floor-plan PDFs are
    typically single-page, and each extra page is an extra image the model
    has to process (cost + latency).
    """
    images_b64: list[str] = []
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        page_count = min(len(doc), max_pages)
        for page_index in range(page_count):
            page = doc[page_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(PDF_RENDER_ZOOM, PDF_RENDER_ZOOM))
            images_b64.append(base64.standard_b64encode(pix.tobytes("png")).decode("utf-8"))
    finally:
        doc.close()
    return images_b64


async def extract_pdf_zones(file_bytes: bytes) -> dict[str, str]:
    """Render a floor-plan PDF's pages to images, send to OpenAI, return {mesh_id: zone_name}."""
    images_b64 = _pdf_pages_to_png_b64(file_bytes)
    if not images_b64:
        logger.warning("PDF had no renderable pages")
        return {}

    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        for b64 in images_b64
    ]
    content.append({"type": "text", "text": PDF_ZONE_PROMPT})

    response = await client.chat.completions.create(
        model=settings.ai_model,
        max_tokens=2048,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": content}],
    )
    raw_text = response.choices[0].message.content or ""
    data: dict[str, Any] = json.loads(_strip_json_fences(raw_text))
    zone_map = {z["mesh_id"]: z["name"] for z in data.get("zones", []) if z.get("mesh_id")}
    logger.info("Parsed PDF floor plan (%d page(s) rendered): %d zones found", len(images_b64), len(zone_map))
    return zone_map


# ─────────────────────────────────────────────────────────────────────────
# REQUIRED additions — same config changes as ai_service.py (openai_api_key,
# AI_MODEL updated to an OpenAI model string), PLUS a new dependency:
#
# requirements.txt / pyproject.toml — add:
#     pymupdf>=1.24.0
#
# No system-level packages needed (unlike pdf2image, which would require
# poppler installed separately) — PyMuPDF bundles its own PDF rendering,
# verified working with a real render-to-PNG smoke test before shipping
# this file.
# ─────────────────────────────────────────────────────────────────────────