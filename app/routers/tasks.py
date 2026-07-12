# app/routers/tasks.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.floor import Floor
from app.models.report import Report
from app.models.task import TaskCategory, TaskTemplate, ZoneTask
from app.models.user import User, UserRole
from app.models.zone import Zone
from app.schemas.task import TaskTemplateCreate, TaskTemplateOut, ZoneTaskAssign, ZoneTaskOut
from app.services import s3_service
from app.utils.permissions import check_project_access

router = APIRouter(prefix="/api/v1", tags=["tasks"])


@router.get("/task-templates", response_model=list[TaskTemplateOut])
async def list_task_templates(
    category: TaskCategory | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TaskTemplate]:
    """List all task templates, optionally filtered by category."""
    stmt = select(TaskTemplate).order_by(TaskTemplate.name)
    if category is not None:
        stmt = stmt.where(TaskTemplate.category == category)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "/task-templates", response_model=TaskTemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_task_template(
    body: TaskTemplateCreate,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> TaskTemplate:
    """Admin creates a custom task template."""
    template = TaskTemplate(name=body.name, category=body.category)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.post(
    "/zones/{zone_id}/tasks", response_model=ZoneTaskOut, status_code=status.HTTP_201_CREATED
)
async def assign_task_to_zone(
    body: ZoneTaskAssign,
    zone_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ZoneTask:
    """Assign a task template to a zone (project managers and admins)."""
    zone = await db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")

    floor = await db.get(Floor, zone.floor_id)
    await check_project_access(
        db, floor.project_id, current_user, allowed_project_roles={"manager"}
    )

    template = await db.get(TaskTemplate, body.task_template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task template not found")

    existing = await db.execute(
        select(ZoneTask).where(
            ZoneTask.zone_id == zone_id, ZoneTask.task_template_id == template.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This task template is already assigned to the zone",
        )

    zone_task = ZoneTask(zone_id=zone_id, task_template_id=template.id, layer_order=body.layer_order)
    db.add(zone_task)
    await db.commit()

    result = await db.execute(
        select(ZoneTask)
        .where(ZoneTask.id == zone_task.id)
        .options(selectinload(ZoneTask.template))
    )
    return result.scalar_one()


@router.delete("/zones/{zone_id}/tasks/{zone_task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone_task(
    zone_id: uuid.UUID,
    zone_task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a task assignment from a zone (project managers and admins).

    Cascades at the database level to delete any reports/approvals ever
    submitted against this task (Report.zone_task_id is ondelete=CASCADE)
    — this is irreversible, the frontend confirms before calling it.

    IMPORTANT: the database cascade only removes ROWS — it does nothing
    to the actual photo files sitting in S3/local storage, since that's
    outside the database entirely. Without this cleanup step, every
    report's photos would become orphaned files taking up storage forever
    with no way to find or remove them afterward. So: explicitly delete
    each report's photo files first (same pattern as reports.py's own
    delete_report endpoint), THEN let the DB cascade handle the rows.
    """
    zone_task = await db.get(ZoneTask, zone_task_id)
    if zone_task is None or zone_task.zone_id != zone_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone task not found")

    zone = await db.get(Zone, zone_id)
    floor = await db.get(Floor, zone.floor_id)
    await check_project_access(
        db, floor.project_id, current_user, allowed_project_roles={"manager"}
    )

    reports = (
        await db.execute(
            select(Report)
            .where(Report.zone_task_id == zone_task_id)
            .options(selectinload(Report.photos))
        )
    ).scalars().unique().all()

    for report in reports:
        for photo in report.photos:
            try:
                await s3_service.delete_file(photo.s3_key)
            except Exception:  # noqa: BLE001 — best-effort cleanup, don't block the delete
                pass

    await db.delete(zone_task)
    await db.commit()