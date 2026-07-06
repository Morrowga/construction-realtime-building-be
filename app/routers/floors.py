# app/routers/floors.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.floor import Floor
from app.models.project import Project
from app.models.user import User
from app.models.zone import Zone
from app.schemas.floor import FloorCreate, FloorOut, FloorUpdate, FloorWithZoneCountOut
from app.utils.permissions import check_project_access, require_project_member

router = APIRouter(prefix="/api/v1/projects/{project_id}/floors", tags=["floors"])


@router.post("", response_model=FloorOut, status_code=status.HTTP_201_CREATED)
async def create_floor(
    body: FloorCreate,
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Floor:
    """Create a floor within the project (managers and admins)."""
    await check_project_access(db, project_id, current_user, allowed_project_roles={"manager"})

    display_order = body.display_order
    if display_order is None:
        result = await db.execute(
            select(func.coalesce(func.max(Floor.display_order), -1)).where(
                Floor.project_id == project_id
            )
        )
        display_order = result.scalar_one() + 1

    floor = Floor(
        project_id=project_id,
        name=body.name,
        level_number=body.level_number,
        display_order=display_order,
    )
    db.add(floor)
    await db.commit()
    await db.refresh(floor)
    return floor


@router.get("", response_model=list[FloorWithZoneCountOut])
async def list_floors(
    project: Project = Depends(require_project_member()),
    db: AsyncSession = Depends(get_db),
) -> list[FloorWithZoneCountOut]:
    """List floors with the number of zones on each."""
    stmt = (
        select(Floor, func.count(Zone.id).label("zone_count"))
        .outerjoin(Zone, Zone.floor_id == Floor.id)
        .where(Floor.project_id == project.id)
        .group_by(Floor.id)
        .order_by(Floor.display_order, Floor.level_number)
    )
    rows = (await db.execute(stmt)).all()
    out: list[FloorWithZoneCountOut] = []
    for floor, zone_count in rows:
        item = FloorWithZoneCountOut.model_validate(floor)
        item.zone_count = zone_count
        out.append(item)
    return out


@router.patch("/{floor_id}", response_model=FloorOut)
async def update_floor(
    body: FloorUpdate,
    project_id: uuid.UUID,
    floor_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Floor:
    """Rename or reorder a floor (managers and admins)."""
    await check_project_access(db, project_id, current_user, allowed_project_roles={"manager"})

    floor = await db.get(Floor, floor_id)
    if floor is None or floor.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Floor not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(floor, field, value)
    await db.commit()
    await db.refresh(floor)
    return floor
