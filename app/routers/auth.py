# app/routers/auth.py
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.schemas.organization import OrganizationRegisterRequest
from app.schemas.user import LoginRequest, RefreshRequest, TokenPair, UserOut
from app.services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "org"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    """Append -2, -3, ... until we find a slug that isn't taken."""
    slug = base
    suffix = 1
    while True:
        existing = await db.execute(select(Organization).where(Organization.slug == slug))
        if existing.scalar_one_or_none() is None:
            return slug
        suffix += 1
        slug = f"{base}-{suffix}"


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(body: OrganizationRegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    """Sign up a brand-new company.

    Creates an Organization plus its first owner/admin user in one call.
    This is now the ONLY way a user gets created without already belonging
    to an org — every other user is added by an existing admin via
    POST /api/v1/organizations/members, which ties them to that admin's
    organization automatically. There is no path to a user existing
    without an organization, which is what keeps companies isolated from
    each other.
    """
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    org = Organization(
        name=body.organization_name,
        slug=await _unique_slug(db, _slugify(body.organization_name)),
    )
    db.add(org)
    await db.flush()  # need org.id before creating the user that references it

    user = User(
        organization_id=org.id,
        email=body.email,
        password_hash=auth_service.hash_password(body.password),
        full_name=body.full_name,
        role=UserRole.admin,  # the person who creates the org is always its first admin/owner
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return TokenPair(
        access_token=auth_service.create_access_token(user),
        refresh_token=auth_service.create_refresh_token(user),
    )


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    """Authenticate with email + password; returns access + refresh tokens."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not auth_service.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    return TokenPair(
        access_token=auth_service.create_access_token(user),
        refresh_token=auth_service.create_refresh_token(user),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenPair:
    """Exchange a valid refresh token for a new access token."""
    payload = auth_service.decode_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    return TokenPair(access_token=auth_service.create_access_token(user))


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)) -> User:
    """Return the currently authenticated user."""
    return current_user