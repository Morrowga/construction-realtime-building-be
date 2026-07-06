# app/schemas/organization.py
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


class OrganizationRegisterRequest(BaseModel):
    """Signup payload — creates a brand-new Organization plus its first
    owner/admin user in a single call. This replaces the old /auth/register
    flow, which let anyone create a bare user with any role and no company
    association at all.
    """
    organization_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    plan: str
    is_active: bool
    created_at: datetime


class TeamMemberCreate(BaseModel):
    """Org admin creates a teammate account. Deliberately has no password
    field — a secure temporary password is generated server-side and
    emailed directly to the invitee, so the admin never sees or has to
    relay a credential by hand.
    """
    email: EmailStr
    full_name: str | None = None
    role: UserRole = UserRole.engineer


class TeamMemberUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


class TeamMemberCreateOut(TeamMemberOut):
    """Response for the create-teammate endpoint specifically.

    temp_password is populated ONLY when settings.debug is True — this is
    a dev/local convenience so seed scripts and manual testing can log in
    as the new user without a working SMTP setup. In production
    (settings.debug=False) this field is always null and the password only
    ever goes out via send_temp_credentials_email — it is never exposed
    over the API.
    """
    temp_password: str | None = None