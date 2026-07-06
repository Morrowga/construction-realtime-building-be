# app/models/user.py
import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class UserRole(str, enum.Enum):
    engineer = "engineer"
    manager = "manager"
    admin = "admin"
    client = "client"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    # Every user belongs to exactly one organization. This is the boundary
    # that makes "admin" mean "admin of my company" instead of "admin of
    # the entire platform" — see require_role() in dependencies.py and the
    # org-scoped queries in routers/projects.py.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="user_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="members")  # noqa: F821