# app/routers/tasks.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.floor import Floor
from app.models.task import TaskCategory, TaskTemplate, ZoneTask
from app.models.user import User, UserRole
from app.models.zone import Zone
from app.schemas.task import TaskTemplateCreate, TaskTemplateOut, ZoneTaskAssign, ZoneTaskOut
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
