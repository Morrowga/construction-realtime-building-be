# app/workers/tasks.py
import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis as sync_redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.floor import Floor
from app.models.floor_ai_summary import FloorAISummary
from app.models.model_file import ModelFile, ModelSourceType, ParseStatus
from app.models.report import ApprovalAction, Report
from app.models.task import ZoneTask
from app.models.zone import Zone
from app.services import ai_service, bim_service, s3_service
from app.workers.celery_app import celery_app

logger = logging.getLogger("app.worker")

WORKER_ORIGIN = "worker"


def _publish(project_id: str, message: dict[str, Any]) -> None:
    """Publish a realtime event to Redis pub/sub so API instances broadcast it."""
    try:
        client = sync_redis.Redis.from_url(settings.redis_url)
        client.publish(
            f"progress:{project_id}",
            json.dumps({"origin": WORKER_ORIGIN, "data": message}),
        )
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("Worker Redis publish failed for project=%s: %s", project_id, exc)


def _session_factory():
    """Fresh engine per task invocation — required because each asyncio.run()
    creates a new event loop and asyncpg connections are loop-bound."""
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _detect_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "image/jpeg"


# --------------------------------------------------------------------------
# AI analysis of a submitted report
# --------------------------------------------------------------------------


async def _run_ai_analysis(report_id: str) -> None:
    engine, factory = _session_factory()
    try:
        async with factory() as session:
            stmt = (
                select(Report)
                .where(Report.id == uuid.UUID(report_id))
                .options(
                    selectinload(Report.photos),
                    selectinload(Report.zone_task)
                    .selectinload(ZoneTask.zone)
                    .selectinload(Zone.floor),
                    selectinload(Report.zone_task).selectinload(ZoneTask.template),
                )
            )
            report = (await session.execute(stmt)).scalar_one_or_none()
            if report is None:
                logger.error("run_ai_analysis: report %s not found", report_id)
                return

            zone_task = report.zone_task
            zone = zone_task.zone
            floor = zone.floor
            project_id = str(floor.project_id)
            task_name = zone_task.template.name if zone_task.template else "unknown task"
            zone_name = zone.name or "unknown zone"

            # Download photos from S3 (blocking is fine inside a Celery worker).
            photos_b64: list[str] = []
            media_types: list[str] = []
            for photo in report.photos:
                data = s3_service.download_file_sync(photo.s3_key)
                photos_b64.append(base64.standard_b64encode(data).decode("utf-8"))
                media_types.append(_detect_media_type(data))

            analysis = await ai_service.analyse_report(
                photos_b64=photos_b64,
                note=report.note or "",
                task_name=task_name,
                zone_name=zone_name,
                previous_pct=zone_task.progress_pct,
                media_types=media_types,
            )

            # ai_service.analyse_report returns a FALLBACK dict (not an
            # exception) when the AI call fails after all retries — it
            # includes "ai_progress_pct": previous_pct and "confidence": 0.0
            # so the report never gets stuck, but writing those specific
            # numbers to the report makes it LOOK like a real (if
            # low-confidence) analysis happened, when actually nothing was
            # ever analysed at all. Detect the failure flag and leave the
            # numeric fields null instead — the UI can then correctly show
            # "analysis failed" rather than a misleading percentage.
            analysis_failed = "ai_analysis_failed" in (analysis.get("flags") or [])

            if analysis_failed:
                report.ai_progress_pct = None
                report.ai_confidence = None
                report.ai_analysis = analysis  # keep for the error/summary text, just not the numbers
            else:
                report.ai_progress_pct = analysis.get("progress_pct")
                report.ai_confidence = analysis.get("confidence")
                report.ai_analysis = analysis

                photo_analysis = analysis.get("photo_analysis") or []
                for photo, label in zip(report.photos, photo_analysis):
                    photo.ai_tags = {"analysis": label}

            await session.commit()

            # Notify managers over WebSocket that the AI analysis is ready.
            _publish(
                project_id,
                {
                    "type": "ai_analysis_complete",
                    "report_id": str(report.id),
                    "zone_task_id": str(zone_task.id),
                    "ai_progress_pct": report.ai_progress_pct,
                    "ai_confidence": report.ai_confidence,
                    "flags": analysis.get("flags", []),
                },
            )
    finally:
        await engine.dispose()


@celery_app.task(name="run_ai_analysis", bind=True, max_retries=2, default_retry_delay=10)
def run_ai_analysis(self, report_id: str) -> None:
    """Load report photos from S3, call the AI service, store results, notify managers."""
    try:
        asyncio.run(_run_ai_analysis(report_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("run_ai_analysis failed for report=%s", report_id)
        raise self.retry(exc=exc)


# --------------------------------------------------------------------------
# Floor-level AI summary (viewer.html floor-arrow click → side panel)
# --------------------------------------------------------------------------
# PREVIOUSLY this ran live, in the request path, every single time someone
# clicked a floor arrow in the viewer — a fresh Claude call for identical
# results if nothing had changed. Now: this task runs exactly once per
# actual change, triggered from routers/approvals.py right after an
# approve/rollback commits (not on every click), and stores the result in
# floor_ai_summaries. The GET /floors/{id}/ai-summary endpoint just reads
# that stored row — no AI call in the request path at all anymore.

HISTORY_LINES_PER_ZONE = 10  # cap how much history gets sent to the AI per room


def _zone_pct_and_signal(zone: Zone) -> tuple[float, str]:
    """Mirrors the same aggregation logic used in build_progress_tree
    (routers/projects.py) so these numbers always agree with what the
    dashboard and 3D viewer already show."""
    if not zone.tasks:
        return 0.0, "grey"
    task_pcts = [t.progress_pct for t in zone.tasks]
    pct = round(sum(task_pcts) / len(task_pcts), 1)
    active = max(zone.tasks, key=lambda t: t.progress_pct)
    signal = active.colour_signal or (
        "green" if active.progress_pct >= 100.0 else
        "amber" if active.progress_pct > 0.0 else "grey"
    )
    return pct, signal


async def _build_zone_history(session, zone: Zone) -> list[str]:
    """Chronological, human-readable lines describing what happened to
    this zone's tasks over time — submitted reports, AI estimates,
    approvals, and any rollbacks."""
    task_ids = [t.id for t in zone.tasks]
    if not task_ids:
        return []

    task_name_by_id = {t.id: (t.template.name if t.template else "作業") for t in zone.tasks}

    stmt = (
        select(Report)
        .where(Report.zone_task_id.in_(task_ids))
        .options(selectinload(Report.approvals))
        .order_by(Report.submitted_at.asc())
    )
    reports = (await session.execute(stmt)).scalars().unique().all()

    lines: list[str] = []
    for r in reports:
        task_name = task_name_by_id.get(r.zone_task_id, "作業")
        date_str = r.submitted_at.date().isoformat() if r.submitted_at else "日付不明"
        parts = [f"{date_str} {task_name}: 現場報告 {r.engineer_progress_pct or 0:.0f}%"]

        if r.ai_progress_pct is not None:
            parts.append(f"AI推定 {r.ai_progress_pct:.0f}%")

        approved = next(
            (a for a in r.approvals if not a.is_rolled_back and a.action == ApprovalAction.approved),
            None,
        )
        if approved is not None:
            parts.append(f"承認済み最終 {approved.final_pct:.0f}%" if approved.final_pct is not None else "承認済み")

        rolled_back = [a for a in r.approvals if a.is_rolled_back]
        if rolled_back:
            reason = rolled_back[-1].rollback_reason or "理由未記載"
            parts.append(f"ロールバックあり（理由: {reason}）")

        if r.status.value == "rejected":
            parts.append("却下")

        lines.append("、".join(parts))

    return lines[-HISTORY_LINES_PER_ZONE:]


async def _generate_floor_ai_summary(floor_id: str) -> None:
    engine, factory = _session_factory()
    try:
        async with factory() as session:
            floor = await session.get(Floor, uuid.UUID(floor_id))
            if floor is None:
                logger.error("generate_floor_ai_summary: floor %s not found", floor_id)
                return

            stmt = (
                select(Zone)
                .where(Zone.floor_id == floor.id)
                .options(selectinload(Zone.tasks).selectinload(ZoneTask.template))
            )
            zones = (await session.execute(stmt)).scalars().all()

            zone_meta: dict[str, dict] = {}
            ai_input_rooms: list[dict] = []

            for zone in zones:
                pct, signal = _zone_pct_and_signal(zone)
                history = await _build_zone_history(session, zone)
                zone_id_str = str(zone.id)

                zone_meta[zone_id_str] = {"name": zone.name, "pct": pct, "colour_signal": signal}
                ai_input_rooms.append({
                    "zone_id": zone_id_str,
                    "name": zone.name,
                    "pct": pct,
                    "colour_signal": signal,
                    "history": history,
                })

            overall_pct = (
                round(sum(r["pct"] for r in ai_input_rooms) / len(ai_input_rooms), 1)
                if ai_input_rooms else 0.0
            )

            ai_result = await ai_service.summarize_floor_progress(floor.name, overall_pct, ai_input_rooms)

            analysis_by_zone = {r.get("zone_id"): r.get("analysis", "") for r in ai_result.get("rooms", [])}
            rooms_out = {
                zid: {
                    "name": meta["name"],
                    "pct": meta["pct"],
                    "colour_signal": meta["colour_signal"],
                    "analysis": analysis_by_zone.get(zid, ""),
                }
                for zid, meta in zone_meta.items()
            }

            now = datetime.now(timezone.utc)
            existing = (
                await session.execute(
                    select(FloorAISummary).where(FloorAISummary.floor_id == floor.id)
                )
            ).scalar_one_or_none()

            if existing is None:
                existing = FloorAISummary(
                    floor_id=floor.id,
                    summary=ai_result.get("summary", ""),
                    rooms=rooms_out,
                    generated_at=now,
                )
                session.add(existing)
            else:
                existing.summary = ai_result.get("summary", "")
                existing.rooms = rooms_out
                existing.generated_at = now

            await session.commit()

            _publish(
                str(floor.project_id),
                {
                    "type": "floor_ai_summary_ready",
                    "floor_id": str(floor.id),
                },
            )
    finally:
        await engine.dispose()


@celery_app.task(name="generate_floor_ai_summary", bind=True, max_retries=1, default_retry_delay=15)
def generate_floor_ai_summary(self, floor_id: str) -> None:
    """Regenerate one floor's stored AI summary. Triggered from
    routers/approvals.py after an approve/rollback commits — NOT called
    from any GET endpoint or on any click."""
    try:
        asyncio.run(_generate_floor_ai_summary(floor_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_floor_ai_summary failed for floor=%s", floor_id)
        raise self.retry(exc=exc)


# --------------------------------------------------------------------------
# BIM / floor-plan parsing
# --------------------------------------------------------------------------


async def _process_model_file(model_file_id: str) -> None:
    engine, factory = _session_factory()
    try:
        async with factory() as session:
            model_file = await session.get(ModelFile, uuid.UUID(model_file_id))
            if model_file is None:
                logger.error("process_model_file: model_file %s not found", model_file_id)
                return

            project_id = str(model_file.project_id)
            model_file.parse_status = ParseStatus.processing
            model_file.parse_error = None
            await session.commit()

            try:
                file_bytes = s3_service.download_file_sync(model_file.original_s3_key)

                if model_file.source_type == ModelSourceType.ifc:
                    zone_map = bim_service.parse_ifc(file_bytes)
                elif model_file.source_type == ModelSourceType.pdf:
                    zone_map = await bim_service.extract_pdf_zones(file_bytes)
                else:
                    raise ValueError(f"Unsupported source_type: {model_file.source_type}")

                model_file.zone_map = zone_map
                model_file.parse_status = ParseStatus.done
                await session.commit()

                _publish(
                    project_id,
                    {
                        "type": "model_parse_complete",
                        "model_file_id": str(model_file.id),
                        "status": "done",
                        "zone_count": len(zone_map),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Model parse failed for model_file=%s", model_file_id)
                model_file.parse_status = ParseStatus.failed
                model_file.parse_error = str(exc)
                await session.commit()

                _publish(
                    project_id,
                    {
                        "type": "model_parse_complete",
                        "model_file_id": str(model_file.id),
                        "status": "failed",
                        "error": str(exc),
                    },
                )
    finally:
        await engine.dispose()


@celery_app.task(name="process_model_file", bind=True, max_retries=1, default_retry_delay=15)
def process_model_file(self, model_file_id: str) -> None:
    """Load the uploaded IFC/PDF from S3, extract the zone map, update the record,
    and broadcast completion via Redis pub/sub."""
    try:
        asyncio.run(_process_model_file(model_file_id))
    except Exception as exc:  # noqa: BLE001
        logger.exception("process_model_file failed for model_file=%s", model_file_id)
        raise self.retry(exc=exc)