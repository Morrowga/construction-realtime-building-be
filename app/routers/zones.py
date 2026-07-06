# app/routers/zones.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.floor import Floor
from app.models.task import TaskTemplate, ZoneTask
from app.models.user import User
from app.models.zone import Zone
from app.schemas.zone import ZoneCreate, ZoneOut, ZoneProgressOut, ZoneUpdate
from app.schemas.task import ZoneTaskOut
from app.utils.permissions import check_project_access

router = APIRouter(prefix="/api/v1/floors/{floor_id}/zones", tags=["zones"])


async def _get_floor_with_access(
    db: AsyncSession,
    floor_id: uuid.UUID,
    user: User,
    allowed_project_roles: set[str] | None = None,
) -> Floor:
    floor = await db.get(Floor, floor_id)
    if floor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Floor not found")
    await check_project_access(db, floor.project_id, user, allowed_project_roles)
    return floor


def _zone_pct(tasks: list[ZoneTask]) -> float:
    return round(sum(t.progress_pct for t in tasks) / len(tasks), 1) if tasks else 0.0


@router.post("", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
async def create_zone(
    body: ZoneCreate,
    floor_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Zone:
    """Create a zone; optionally attach a task template immediately."""
    await _get_floor_with_access(db, floor_id, current_user, allowed_project_roles={"manager"})

    zone = Zone(
        floor_id=floor_id,
        name=body.name,
        zone_type=body.zone_type,
        model_mesh_id=body.model_mesh_id,
        finish_data=body.finish_data,
    )
    db.add(zone)
    await db.flush()

    if body.task_template_id is not None:
        template = await db.get(TaskTemplate, body.task_template_id)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task template not found"
            )
        db.add(ZoneTask(zone_id=zone.id, task_template_id=template.id))

    await db.commit()

    result = await db.execute(
        select(Zone)
        .where(Zone.id == zone.id)
        .options(selectinload(Zone.tasks).selectinload(ZoneTask.template))
    )
    return result.scalar_one()


@router.get("", response_model=list[ZoneOut])
async def list_zones(
    floor_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Zone]:
    """List zones on a floor, including per-task progress."""
    await _get_floor_with_access(db, floor_id, current_user)

    result = await db.execute(
        select(Zone)
        .where(Zone.floor_id == floor_id)
        .options(selectinload(Zone.tasks).selectinload(ZoneTask.template))
        .order_by(Zone.created_at)
    )
    return list(result.scalars().all())


@router.patch("/{zone_id}", response_model=ZoneOut)
async def update_zone(
    body: ZoneUpdate,
    floor_id: uuid.UUID,
    zone_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Zone:
    """Update mesh_id, finish_data, or other zone metadata."""
    await _get_floor_with_access(db, floor_id, current_user, allowed_project_roles={"manager"})

    result = await db.execute(
        select(Zone)
        .where(Zone.id == zone_id, Zone.floor_id == floor_id)
        .options(selectinload(Zone.tasks).selectinload(ZoneTask.template))
    )
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(zone, field, value)
    await db.commit()
    await db.refresh(zone)
    return zone


@router.get("/{zone_id}/progress", response_model=ZoneProgressOut)
async def zone_progress(
    floor_id: uuid.UUID,
    zone_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ZoneProgressOut:
    """Full task breakdown for a single zone."""
    await _get_floor_with_access(db, floor_id, current_user)

    result = await db.execute(
        select(Zone)
        .where(Zone.id == zone_id, Zone.floor_id == floor_id)
        .options(selectinload(Zone.tasks).selectinload(ZoneTask.template))
    )
    zone = result.scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")

    return ZoneProgressOut(
        zone_id=zone.id,
        name=zone.name,
        mesh_id=zone.model_mesh_id,
        pct=_zone_pct(zone.tasks),
        tasks=[ZoneTaskOut.model_validate(t) for t in zone.tasks],
    )
