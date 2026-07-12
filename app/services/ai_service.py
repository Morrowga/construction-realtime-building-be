# app/services/ai_service.py
import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger("app.ai")

# NOTE: switched from Anthropic to OpenAI for cost reasons. settings.ai_model
# should now hold an OpenAI model string (e.g. "gpt-4o-mini" — recommended:
# cheapest OpenAI model that still supports vision input; "gpt-4o" also
# works if quality matters more than cost). settings.openai_api_key is a
# NEW config field — see the note at the bottom of this file for exactly
# what to add to app/config.py and your .env.
client = AsyncOpenAI(api_key=settings.openai_api_key or "not-set")

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
    """Kept as a defensive measure even though response_format={"type":
    "json_object"} should already guarantee fence-free JSON from OpenAI."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
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
    """OpenAI's vision content shape differs from Anthropic's — images are
    "image_url" blocks with a data: URI, not "image"/base64 source objects."""
    content: list[dict[str, Any]] = []
    for b64, media_type in zip(photos_b64, media_types):
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
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
    """Send photos + note to OpenAI and return the structured analysis dict.

    Retries up to MAX_RETRIES times on API or parse errors. On total failure,
    returns a conservative fallback so the report never gets stuck.
    """
    if media_types is None:
        media_types = ["image/jpeg"] * len(photos_b64)

    content = _build_user_content(photos_b64, media_types, note, task_name, zone_name, previous_pct)

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=settings.ai_model,
                max_tokens=1024,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
            )
            raw_text = response.choices[0].message.content or ""
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


# ─────────────────────────────────────────────────────────────────────────
# Floor-level AI summary (viewer.html floor-arrow click → side panel)
# ─────────────────────────────────────────────────────────────────────────
# IMPORTANT DIFFERENCE from analyse_report above: this call is never trusted
# for numbers. The router (app/routers/floor_ai.py) computes every room's
# actual pct/colour_signal from real approved-report data BEFORE calling
# this — the AI only ever writes the narrative/trend text on top of numbers
# we already know are correct. This avoids the AI ever inventing or
# contradicting a percentage a manager already approved.
#
# ALSO IMPORTANT (per earlier conversation): this function is only ever
# invoked from routers/approvals.py, right after an approve/rollback
# commits — never on every floor-view/click. GET /floors/{id}/ai-summary
# is a pure database read of the stored result; it never calls this
# function itself. If AI credits ever run out, the fallback below is what
# renders in the viewer, not a repeated failed call.

FLOOR_SUMMARY_SYSTEM_PROMPT = """You are a construction progress analyst summarising one \
floor of a building for a Japanese construction management dashboard. You will receive \
the floor name, its overall percentage, and a list of rooms (zones) — each with its \
current percentage, status, and a short chronological history of submitted/approved \
progress reports.

Your job: write a concise plain-language summary in Japanese describing the floor's \
overall status — which rooms are complete, which are lagging behind, and any notable \
trend (e.g. a room that regressed after a rollback, or one progressing unusually slowly \
compared to the rest of the floor). Then write one short room-specific note per room.

Respond with ONLY valid JSON (no markdown fences, no commentary) in this exact schema:
{
  "summary": "2-4 sentences in Japanese describing the floor overall",
  "rooms": [
    {"zone_id": "...", "analysis": "one sentence in Japanese about this room's trend/status"}
  ]
}

Rules:
- Base your analysis ONLY on the data provided — never invent a percentage, date, or \
event that isn't in the input.
- If a room has no report history yet, say so plainly (e.g. "まだ報告がありません") rather \
than speculating about its state.
- Keep language plain and non-technical — this is read by construction managers and \
clients, not engineers.
- Every zone_id given in the input must appear exactly once in "rooms"."""

FLOOR_SUMMARY_MAX_RETRIES = 1  # lower stakes than photo analysis — this has a full fallback


async def summarize_floor_progress(
    floor_name: str,
    overall_pct: float,
    rooms: list[dict[str, Any]],
) -> dict[str, Any]:
    """rooms: [{ zone_id, name, pct, colour_signal, history: [str, ...] }, ...]

    Returns { "summary": str, "rooms": [{ "zone_id": str, "analysis": str }] }.
    On any failure, returns a safe fallback with an empty narrative per room —
    the router still has the real numbers regardless of whether this succeeds.
    """
    user_payload = {
        "floor_name": floor_name,
        "overall_pct": overall_pct,
        "rooms": rooms,
    }

    last_error: Exception | None = None
    for attempt in range(FLOOR_SUMMARY_MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=settings.ai_model,
                max_tokens=800,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": FLOOR_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
            )
            raw_text = response.choices[0].message.content or ""
            data = json.loads(_strip_json_fences(raw_text))
            if "summary" not in data or "rooms" not in data:
                raise ValueError("AI floor summary missing required keys")
            return data
        except Exception as exc:  # noqa: BLE001 — retry once, then fall back
            last_error = exc
            logger.warning(
                "Floor AI summary attempt %d/%d failed: %s",
                attempt + 1, FLOOR_SUMMARY_MAX_RETRIES + 1, exc,
            )
            if attempt < FLOOR_SUMMARY_MAX_RETRIES:
                await asyncio.sleep(1)

    logger.error("Floor AI summary failed after retries: %s", last_error)
    return {
        "summary": "AI分析は現在利用できません。各部屋の実際の進捗データは以下に表示されています。",
        "rooms": [{"zone_id": r["zone_id"], "analysis": ""} for r in rooms],
    }


# ─────────────────────────────────────────────────────────────────────────
# REQUIRED config changes (app/config.py + .env) — this file alone isn't
# enough to run:
#
# app/config.py — add:
#     openai_api_key: str = ""
#
# .env — add/change:
#     OPENAI_API_KEY=sk-...           (new)
#     AI_MODEL=gpt-4o-mini            (was a Claude model string before —
#                                       gpt-4o-mini is the cheapest OpenAI
#                                       model that still supports vision;
#                                       use gpt-4o instead if quality matters
#                                       more than cost)
#
# requirements.txt / pyproject.toml — add:
#     openai>=1.0.0
# (anthropic package can stay installed harmlessly, or be removed once
# nothing imports it anymore — check bim_service.py too, see its own note)
# ─────────────────────────────────────────────────────────────────────────