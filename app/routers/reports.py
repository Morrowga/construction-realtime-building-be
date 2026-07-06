# app/routers/reports.py
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.floor import Floor
from app.models.report import Report, ReportPhoto, ReportStatus
from app.models.task import ZoneTask
from app.models.user import User, UserRole
from app.models.zone import Zone
from app.schemas.report import ReportCreate, ReportOut
from app.services import s3_service
from app.utils.permissions import check_project_access
from app.workers.tasks import run_ai_analysis

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

MAX_PHOTOS = 5
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png"}

_EXT_FOR_MIME = {"image/jpeg": "jpg", "image/png": "png"}


async def _get_report_chain(db: AsyncSession, zone_task_id: uuid.UUID):
    """Resolve zone_task -> zone -> floor -> project_id, or raise 404."""
    stmt = (
        select(ZoneTask, Zone, Floor)
        .join(Zone, ZoneTask.zone_id == Zone.id)
        .join(Floor, Zone.floor_id == Floor.id)
        .where(ZoneTask.id == zone_task_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone task not found")
    return row  # (zone_task, zone, floor)


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def submit_report(
    data: str = Form(..., description="JSON-encoded report body (ReportCreate schema)"),
    photos: list[UploadFile] = File(default=[]),
    current_user: User = Depends(
        require_role(UserRole.engineer, UserRole.manager, UserRole.admin)
    ),
    db: AsyncSession = Depends(get_db),
) -> Report:
    """Engineer submits a progress report: multipart with `data` JSON + up to 5 photos."""
    try:
        body = ReportCreate.model_validate_json(data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.errors()
        )

    if len(photos) > MAX_PHOTOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A report may contain at most {MAX_PHOTOS} photos",
        )

    zone_task, zone, floor = await _get_report_chain(db, body.zone_task_id)
    project_id = floor.project_id
    await check_project_access(db, project_id, current_user)

    validated: list[tuple[bytes, str]] = []
    for photo in photos:
        if photo.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported image type '{photo.content_type}'. Allowed: image/jpeg, image/png",
            )
        contents = await photo.read()
        if len(contents) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Photo '{photo.filename}' exceeds the 10MB size limit",
            )
        validated.append((contents, photo.content_type))

    report = Report(
        zone_task_id=body.zone_task_id,
        engineer_id=current_user.id,
        note=body.note,
        status=ReportStatus.pending,
        engineer_progress_pct=body.engineer_progress_pct,
        geo_lat=body.geo_lat,
        geo_lng=body.geo_lng,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(report)
    await db.flush()

    for index, (contents, mime) in enumerate(validated):
        ext = _EXT_FOR_MIME[mime]
        key = f"projects/{project_id}/reports/{report.id}/photo_{index}.{ext}"
        url = await s3_service.upload_file(contents, key, mime)
        db.add(ReportPhoto(report_id=report.id, s3_url=url, s3_key=key, order_index=index))

    await db.commit()

    run_ai_analysis.delay(str(report.id))

    result = await db.execute(
        select(Report)
        .where(Report.id == report.id)
        .options(selectinload(Report.photos), selectinload(Report.approvals))
    )
    return result.scalar_one()


@router.get("", response_model=list[ReportOut])
async def list_reports(
    project_id: uuid.UUID | None = None,
    zone_id: uuid.UUID | None = None,
    zone_task_id: uuid.UUID | None = None,
    report_status: ReportStatus | None = Query(default=None, alias="status"),
    engineer_id: uuid.UUID | None = None,
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Report]:
    """List reports, filterable by project, zone, zone_task, status, and engineer."""
    stmt = (
        select(Report)
        .join(ZoneTask, Report.zone_task_id == ZoneTask.id)
        .join(Zone, ZoneTask.zone_id == Zone.id)
        .join(Floor, Zone.floor_id == Floor.id)
        .options(selectinload(Report.photos), selectinload(Report.approvals))
        .order_by(Report.submitted_at.desc())
        .limit(limit)
    )
    if project_id is not None:
        await check_project_access(db, project_id, current_user)
        stmt = stmt.where(Floor.project_id == project_id)
    if zone_id is not None:
        stmt = stmt.where(Zone.id == zone_id)
    if zone_task_id is not None:
        stmt = stmt.where(Report.zone_task_id == zone_task_id)
    if report_status is not None:
        stmt = stmt.where(Report.status == report_status)
    if engineer_id is not None:
        stmt = stmt.where(Report.engineer_id == engineer_id)

    if project_id is None and current_user.role not in (UserRole.admin, UserRole.manager):
        stmt = stmt.where(Report.engineer_id == current_user.id)

    result = await db.execute(stmt)
    return list(result.scalars().unique().all())


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Report:
    """Report detail with photos and AI analysis."""
    result = await db.execute(
        select(Report)
        .where(Report.id == report_id)
        .options(selectinload(Report.photos), selectinload(Report.approvals))
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    _, _, floor = await _get_report_chain(db, report.zone_task_id)
    await check_project_access(db, floor.project_id, current_user)
    return report


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Engineer deletes their own pending report."""
    result = await db.execute(
        select(Report).where(Report.id == report_id).options(selectinload(Report.photos))
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    if report.engineer_id != current_user.id and current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You can only delete your own reports"
        )
    if report.status != ReportStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only pending reports can be deleted"
        )

    for photo in report.photos:
        try:
            await s3_service.delete_file(photo.s3_key)
        except Exception:  # noqa: BLE001
            pass

    await db.delete(report)
    await db.commit()