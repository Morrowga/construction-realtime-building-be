# app/routers/projects.py
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.floor import Floor
from app.models.project import Project, ProjectMember
from app.models.task import ZoneTask
from app.models.user import User, UserRole
from app.models.zone import Zone
from app.schemas.project import (
    MemberInvite,
    ProjectCreate,
    ProjectDetailOut,
    ProjectMemberOut,
    ProjectOut,
    ProjectUpdate,
)
from app.services import s3_service
from app.utils.permissions import check_project_access, require_project_member

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

PROJECT_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
PROJECT_IMAGE_MIME_EXT = {"image/jpeg": "jpg", "image/png": "png"}


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def _zone_colour_signal(zone_tasks: list) -> str:
    """Derive zone-level colour signal from the task with highest progress."""
    if not zone_tasks:
        return "grey"
    active = max(zone_tasks, key=lambda t: t.progress_pct)
    return active.colour_signal or (
        "green" if active.progress_pct >= 100.0 else
        "amber" if active.progress_pct > 0.0 else "grey"
    )


async def build_progress_tree(db: AsyncSession, project_id: uuid.UUID) -> dict[str, Any]:
    """Aggregate progress per floor and zone for a project.

    Returns colour_signal, active_layer_name, active_layer_pct, and layer_order
    per zone and task so the 3D viewer can colour meshes correctly.
    """
    stmt = (
        select(Floor)
        .where(Floor.project_id == project_id)
        .options(selectinload(Floor.zones).selectinload(Zone.tasks).selectinload(ZoneTask.template))
        .order_by(Floor.display_order, Floor.level_number)
    )
    floors = (await db.execute(stmt)).scalars().all()

    all_pcts: list[float] = []
    floors_out: list[dict[str, Any]] = []

    for floor in floors:
        floor_pcts: list[float] = []
        zones_out: list[dict[str, Any]] = []

        for zone in floor.zones:
            task_pcts = [t.progress_pct for t in zone.tasks]

            # Sort tasks by layer_order for consistent display
            sorted_tasks = sorted(zone.tasks, key=lambda t: t.layer_order or 0)

            tasks_out = [
                {
                    "zone_task_id": str(t.id),
                    "task_name": t.template.name if t.template else None,
                    "task_category": t.template.category.value if t.template and t.template.category else None,
                    "pct": t.progress_pct,
                    "layer_order": t.layer_order,
                    "colour_signal": t.colour_signal or (
                        "green" if t.progress_pct >= 100 else
                        "amber" if t.progress_pct > 0 else "grey"
                    ),
                    "active_layer_name": t.active_layer_name,
                    "active_layer_pct": t.active_layer_pct,
                }
                for t in sorted_tasks
            ]

            # Zone colour signal — use stored value from first task or derive
            zone_signal = _zone_colour_signal(zone.tasks)

            # Active layer = lowest layer_order task that is not yet 100%
            active_task = next(
                (t for t in sorted_tasks if t.progress_pct < 100.0 and t.progress_pct > 0.0),
                None
            )
            if not active_task:
                active_task = next(
                    (t for t in sorted_tasks if t.progress_pct < 100.0),
                    None
                )

            zones_out.append(
                {
                    "zone_id": str(zone.id),
                    "name": zone.name,
                    "mesh_id": zone.model_mesh_id,
                    "pct": _mean(task_pcts),
                    "colour_signal": zone_signal,
                    "active_layer_name": active_task.template.name if (active_task and active_task.template) else None,
                    "active_layer_category": active_task.template.category.value if (active_task and active_task.template and active_task.template.category) else None,
                    "active_layer_pct": active_task.progress_pct if active_task else 0.0,
                    "finish_data": zone.finish_data or {},
                    "tasks": tasks_out,
                }
            )
            floor_pcts.extend(task_pcts)

        all_pcts.extend(floor_pcts)
        floors_out.append(
            {
                "floor_id": str(floor.id),
                "name": floor.name,
                "pct": _mean(floor_pcts),
                "zones": zones_out,
            }
        )

    return {"overall_pct": _mean(all_pcts), "floors": floors_out}


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Admin creates a new project — always scoped to their own organization."""
    project = Project(
        **body.model_dump(),
        admin_id=current_user.id,
        organization_id=current_user.organization_id,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Project]:
    """List projects the current user belongs to.

    Admins see every project in their OWN organization only.
    """
    if current_user.role == UserRole.admin:
        result = await db.execute(
            select(Project)
            .where(Project.organization_id == current_user.organization_id)
            .order_by(Project.created_at.desc())
        )
        return list(result.scalars().all())

    stmt = (
        select(Project)
        .outerjoin(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            Project.organization_id == current_user.organization_id,
            (ProjectMember.user_id == current_user.id)
            | (Project.admin_id == current_user.id)
            | (Project.client_id == current_user.id),
        )
        .distinct()
        .order_by(Project.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{project_id}", response_model=ProjectDetailOut)
async def get_project(
    project: Project = Depends(require_project_member()),
    db: AsyncSession = Depends(get_db),
) -> ProjectDetailOut:
    """Project detail including overall progress percentage."""
    tree = await build_progress_tree(db, project.id)
    detail = ProjectDetailOut.model_validate(project)
    detail.overall_pct = tree["overall_pct"]
    return detail


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    body: ProjectUpdate,
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Update project metadata (project managers and admins only)."""
    project = await check_project_access(
        db, project_id, current_user, allowed_project_roles={"manager"}
    )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await db.commit()
    await db.refresh(project)
    return project


@router.post("/{project_id}/image", response_model=ProjectOut)
async def upload_project_image(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Upload/replace a project's cover image. Manager/admin only.

    Deterministic key (projects/{id}/image.{ext}) — re-uploading naturally
    overwrites the previous image at the same path rather than
    accumulating orphaned old files, since a project only ever needs one
    current cover image (unlike report photos, which persist historically
    per-report and must never be overwritten).

    Only image_s3_key is ever persisted — never a full URL. See the
    comment on Project.image_s3_key for why.
    """
    project = await check_project_access(
        db, project_id, current_user, allowed_project_roles={"manager"}
    )

    if file.content_type not in PROJECT_IMAGE_MIME_EXT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported image type '{file.content_type}'. Allowed: image/jpeg, image/png",
        )
    contents = await file.read()
    if len(contents) > PROJECT_IMAGE_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image exceeds the 10MB size limit",
        )

    ext = PROJECT_IMAGE_MIME_EXT[file.content_type]
    key = f"projects/{project_id}/image.{ext}"
    await s3_service.upload_file(contents, key, file.content_type)

    project.image_s3_key = key
    await db.commit()
    await db.refresh(project)
    return project


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    body: MemberInvite,
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectMember:
    """Invite an existing user (by email) to the project with a project role.

    The invited user must belong to the same organization as the project —
    otherwise you could pull a user from an unrelated company into your
    project just by knowing their email.
    """
    project = await check_project_access(db, project_id, current_user, allowed_project_roles={"manager"})

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No user with that email exists"
        )
    if user.organization_id != project.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User belongs to a different organization",
        )

    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user.id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="User is already a project member"
        )

    member = ProjectMember(project_id=project_id, user_id=user.id, role=body.role)
    db.add(member)
    await db.commit()
    await db.refresh(member, attribute_names=["user"])
    return member


@router.get("/{project_id}/progress")
async def project_progress(
    project: Project = Depends(require_project_member()),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Aggregated progress per floor and zone — includes colour_signal, layer info."""
    return await build_progress_tree(db, project.id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Admin permanently deletes a project and everything under it —
    floors, zones, task assignments, reports, approvals, model files,
    and project memberships all cascade at the database level.

    Scoped to the admin's own organization: a project_id from a different
    org returns 404, not a cross-tenant deletion. This is irreversible —
    the frontend must confirm before calling it.
    """
    project = await db.get(Project, project_id)
    if project is None or project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    await db.delete(project)
    await db.commit()