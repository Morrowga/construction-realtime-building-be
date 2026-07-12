# app/schemas/project.py
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.project import ProjectMemberRole, ProjectStatus, ReportFormat
from app.schemas.user import UserOut


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    address: str | None = None
    client_id: uuid.UUID | None = None
    planned_end_date: date | None = None
    report_format: ReportFormat = ReportFormat.standard
    geo_lat: float | None = None
    geo_lng: float | None = None
    geo_radius_m: int = 200


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = None
    client_id: uuid.UUID | None = None
    status: ProjectStatus | None = None
    planned_end_date: date | None = None
    report_format: ReportFormat | None = None
    geo_lat: float | None = None
    geo_lng: float | None = None
    geo_radius_m: int | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address: str | None
    client_id: uuid.UUID | None
    admin_id: uuid.UUID | None
    status: ProjectStatus
    planned_end_date: date | None
    report_format: ReportFormat
    geo_lat: float | None
    geo_lng: float | None
    geo_radius_m: int
    image_s3_key: str | None
    created_at: datetime
    updated_at: datetime


class ProjectDetailOut(ProjectOut):
    overall_pct: float = 0.0


class MemberInvite(BaseModel):
    email: EmailStr
    role: ProjectMemberRole


class ProjectMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: ProjectMemberRole
    user: UserOut | None = None