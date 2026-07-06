# app/routers/organizations.py
import secrets
import string
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import require_role
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.organization import (
    OrganizationOut,
    TeamMemberCreate,
    TeamMemberCreateOut,
    TeamMemberOut,
    TeamMemberUpdate,
)
from app.services import auth_service
from app.services.email_service import send_temp_credentials_email

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


def _generate_temp_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("/me", response_model=OrganizationOut)
async def get_my_organization(
    current_user: User = Depends(
        require_role(UserRole.admin, UserRole.manager, UserRole.engineer, UserRole.client)
    ),
    db: AsyncSession = Depends(get_db),
) -> Organization:
    """Return the current user's own organization."""
    org = await db.get(Organization, current_user.organization_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


@router.get("/members", response_model=list[TeamMemberOut])
async def list_members(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> list[User]:
    """List everyone in the current admin's organization."""
    result = await db.execute(
        select(User)
        .where(User.organization_id == current_user.organization_id)
        .order_by(User.created_at.asc())
    )
    return list(result.scalars().all())


@router.post("/members", response_model=TeamMemberCreateOut, status_code=status.HTTP_201_CREATED)
async def create_member(
    body: TeamMemberCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> TeamMemberCreateOut:
    """Admin creates a teammate directly inside their own organization.

    A temporary password is generated here and emailed straight to the
    invitee — the admin never sees or sets it in production. In dev
    (settings.debug=True) the same temp password is also returned in the
    response so local testing/seed scripts work without a working SMTP
    setup; this path is never active in production.
    """
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    temp_password = _generate_temp_password()
    user = User(
        organization_id=current_user.organization_id,
        email=body.email,
        password_hash=auth_service.hash_password(temp_password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    org = await db.get(Organization, current_user.organization_id)
    background_tasks.add_task(
        send_temp_credentials_email,
        to_email=user.email,
        full_name=user.full_name,
        temp_password=temp_password,
        organization_name=org.name if org else "your organization",
    )

    response = TeamMemberCreateOut.model_validate(user)
    if getattr(settings, "debug", False):
        response.temp_password = temp_password
    return response


@router.patch("/members/{user_id}", response_model=TeamMemberOut)
async def update_member(
    user_id: uuid.UUID,
    body: TeamMemberUpdate,
    current_user: User = Depends(require_role(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Change a teammate's role, or deactivate their account.

    Scoped to the admin's own organization — looking up a user_id that
    belongs to a different org returns 404, not the other org's user.
    """
    user = await db.get(User, user_id)
    if user is None or user.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found in your organization"
        )
    if user.id == current_user.id and body.role is not None and body.role != UserRole.admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot demote your own account")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user