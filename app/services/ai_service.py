# app/services/ai_service.py
import asyncio
import json
import logging
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger("app.ai")

client = AsyncAnthropic(api_key=settings.anthropic_api_key or "not-set")

SYSTEM_PROMPT = """You are a construction progress analyst for a Japanese construction \
progress tracking platform. You will receive one or more site photos plus a short note \
written by a site engineer, along with the task name, zone name, and the previously \
recorded progress percentage.

Your job:
1. Analyse each photo for visible construction progress relevant to the given task.
2. Cross-check the photo content against the engineer's text note. If the note claims \
work that is not visible in the photos, or the photos show something different, flag the mismatch.
3. Compare the implied progress against the previous recorded percentage. If progress \
appears to have gone backwards, add a "progress_regression" flag.

Respond with ONLY valid JSON (no markdown fences, no commentary) in this exact schema:
{
  "progress_pct": 0.0,
  "confidence": 0.0,
  "photo_analysis": ["one string per photo, in order"],
  "note_match": true,
  "mismatch_reason": null,
  "flags": [],
  "summary": "one sentence"
}

Rules:
- "progress_pct" is a float 0.0-100.0 representing your best estimate of task completion.
- "confidence" is a float 0.0-1.0.
- "photo_analysis" must contain exactly one entry per photo.
- "note_match" is false if the note and photos disagree; set "mismatch_reason" to a short \
explanation, otherwise null.
- "flags" may include: "note_mismatch", "progress_regression", "low_photo_quality", \
"wrong_location_suspected".
- "summary" is a single sentence describing the observed state of the work."""

REQUIRED_KEYS = {
    "progress_pct",
    "confidence",
    "photo_analysis",
    "note_match",
    "mismatch_reason",
    "flags",
    "summary",
}

MAX_RETRIES = 2


def _strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Remove opening fence (``` or ```json) and closing fence.
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def _build_user_content(
    photos_b64: list[str],
    media_types: list[str],
    note: str,
    task_name: str,
    zone_name: str,
    previous_pct: float,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for b64, media_type in zip(photos_b64, media_types):
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            }
        )
    content.append(
        {
            "type": "text",
            "text": (
                f"Task: {task_name}\n"
                f"Zone: {zone_name}\n"
                f"Previously recorded progress: {previous_pct:.1f}%\n"
                f"Engineer note: {note or '(no note provided)'}\n\n"
                f"Number of photos attached: {len(photos_b64)}"
            ),
        }
    )
    return content


async def analyse_report(
    photos_b64: list[str],
    note: str,
    task_name: str,
    zone_name: str,
    previous_pct: float,
    media_types: list[str] | None = None,
) -> dict[str, Any]:
    """Send photos + note to Claude and return the structured analysis dict.

    Retries up to MAX_RETRIES times on API or parse errors. On total failure,
    returns a conservative fallback so the report never gets stuck.
    """
    if media_types is None:
        media_types = ["image/jpeg"] * len(photos_b64)

    content = _build_user_content(photos_b64, media_types, note, task_name, zone_name, previous_pct)

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.messages.create(
                model=settings.ai_model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
            )
            raw_text = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
            data = json.loads(_strip_json_fences(raw_text))

            missing = REQUIRED_KEYS - data.keys()
            if missing:
                raise ValueError(f"AI response missing keys: {missing}")

            # Clamp values to sane ranges.
            data["progress_pct"] = max(0.0, min(100.0, float(data["progress_pct"])))
            data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
            return data
        except Exception as exc:  # noqa: BLE001 — retry on any API/parse error
            last_error = exc
            logger.warning(
                "AI analysis attempt %d/%d failed: %s", attempt + 1, MAX_RETRIES + 1, exc
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(2**attempt)

    logger.error("AI analysis failed after %d attempts: %s", MAX_RETRIES + 1, last_error)
    return {
        "progress_pct": previous_pct,
        "confidence": 0.0,
        "photo_analysis": ["analysis unavailable" for _ in photos_b64],
        "note_match": True,
        "mismatch_reason": None,
        "flags": ["ai_analysis_failed"],
        "summary": "AI analysis failed; manual review required.",
        "error": str(last_error),
    }
