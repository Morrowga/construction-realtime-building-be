# app/services/bim_service.py
import base64
import json
import logging
import os
import tempfile
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger("app.bim")

try:
    import ifcopenshell
except ImportError:
    ifcopenshell = None

client = AsyncAnthropic(api_key=settings.anthropic_api_key or "not-set")

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


def parse_ifc(file_bytes: bytes) -> dict[str, str]:
    """Extract spaces/zones from an IFC file. Returns {mesh_id: zone_name}.

    Raises RuntimeError if IfcOpenShell is not installed.
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


async def extract_pdf_zones(file_bytes: bytes) -> dict[str, str]:
    """Send a floor-plan PDF to Claude and return {mesh_id: zone_name}."""
    pdf_b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    response = await client.messages.create(
        model=settings.ai_model,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {"type": "text", "text": PDF_ZONE_PROMPT},
                ],
            }
        ],
    )
    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    data: dict[str, Any] = json.loads(_strip_json_fences(raw_text))
    zone_map = {z["mesh_id"]: z["name"] for z in data.get("zones", []) if z.get("mesh_id")}
    logger.info("Parsed PDF floor plan: %d zones found", len(zone_map))
    return zone_map
