# app/models/project.py
import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class ProjectStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    on_hold = "on_hold"


class ReportFormat(str, enum.Enum):
    standard = "standard"
    nikken = "nikken"


class ProjectMemberRole(str, enum.Enum):
    engineer = "engineer"
    manager = "manager"
    client = "client"


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    # Every project belongs to exactly one organization — set from the
    # creating admin's own org at creation time (see routers/projects.py).
    # This is what list_projects() filters on so one company can never see
    # another company's projects.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"),
        default=ProjectStatus.active,
        server_default="active",
        nullable=False,
    )
    planned_end_date: Mapped[date | None] = mapped_column(Date)
    report_format: Mapped[ReportFormat] = mapped_column(
        Enum(ReportFormat, name="report_format"),
        default=ReportFormat.standard,
        server_default="standard",
        nullable=False,
    )
    geo_lat: Mapped[float | None] = mapped_column(Float)
    geo_lng: Mapped[float | None] = mapped_column(Float)
    geo_radius_m: Mapped[int] = mapped_column(Integer, default=200, server_default="200", nullable=False)

    # Only the storage key is persisted — NEVER a full URL. A stored
    # absolute URL bakes in whatever host was current at upload time
    # (e.g. "http://localhost:8000/..."), which breaks the moment it's
    # accessed from a different device. The frontend always reconstructs
    # `${API_BASE}/files/${image_s3_key}` fresh instead — same fix
    # already applied to model GLBs and report photos.
    image_s3_key: Mapped[str | None] = mapped_column(String(500))

    organization: Mapped["Organization"] = relationship(back_populates="projects")  # noqa: F821
    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    floors: Mapped[list["Floor"]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(TimestampMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[ProjectMemberRole] = mapped_column(
        Enum(ProjectMemberRole, name="project_member_role"), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()  # noqa: F821