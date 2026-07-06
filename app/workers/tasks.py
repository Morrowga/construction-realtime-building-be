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
from app.models.model_file import ModelFile, ModelSourceType, ParseStatus
from app.models.report import Report
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
