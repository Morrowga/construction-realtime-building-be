# app/utils/permissions.py
import uuid

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.project import Project, ProjectMember, ProjectMemberRole
from app.models.user import User, UserRole


async def get_project_or_404(db: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


async def get_membership(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> ProjectMember | None:
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def check_project_access(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    allowed_project_roles: set[str] | None = None,
) -> Project:
    """Raise 403 unless the user is a global admin, the project admin/client,
    or a project member (optionally restricted to specific project roles)."""
    project = await get_project_or_404(db, project_id)

    # Global admins and the project's own admin always have access.
    if user.role == UserRole.admin or project.admin_id == user.id:
        return project

    # The assigned client counts as a member with role "client".
    if project.client_id == user.id:
        if allowed_project_roles is None or ProjectMemberRole.client.value in allowed_project_roles:
            return project
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient project role")

    membership = await get_membership(db, project_id, user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this project")

    if allowed_project_roles is not None and membership.role.value not in allowed_project_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient project role")

    return project


def require_project_member():
    """Dependency: current user must be a member of the project in the path."""

    async def dep(
        project_id: uuid.UUID = Path(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Project:
        return await check_project_access(db, project_id, current_user)

    return dep


def require_project_role(*roles: ProjectMemberRole | str):
    """Dependency: member of the project AND holding one of the given project roles."""
    allowed = {r.value if isinstance(r, ProjectMemberRole) else r for r in roles}

    async def dep(
        project_id: uuid.UUID = Path(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Project:
        return await check_project_access(db, project_id, current_user, allowed_project_roles=allowed)

    return dep
