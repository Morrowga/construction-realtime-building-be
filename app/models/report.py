# app/models/report.py
import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class ReportStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ApprovalAction(str, enum.Enum):
    approved = "approved"
    rejected = "rejected"


class Report(TimestampMixin, Base):
    __tablename__ = "reports"

    zone_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zone_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engineer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"),
        default=ReportStatus.pending,
        server_default="pending",
        nullable=False,
        index=True,
    )
    engineer_progress_pct: Mapped[float | None] = mapped_column(Float)
    ai_progress_pct: Mapped[float | None] = mapped_column(Float)
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    ai_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    final_progress_pct: Mapped[float | None] = mapped_column(Float)
    geo_lat: Mapped[float | None] = mapped_column(Float)
    geo_lng: Mapped[float | None] = mapped_column(Float)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    photos: Mapped[list["ReportPhoto"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="ReportPhoto.order_index"
    )
    # Changed to list relationship — multiple approvals per report after rollback support
    approvals: Mapped[list["Approval"]] = relationship(
        back_populates="report", cascade="all, delete-orphan", order_by="Approval.approved_at"
    )
    zone_task: Mapped["ZoneTask"] = relationship()  # noqa: F821
    engineer: Mapped["User"] = relationship()  # noqa: F821

    @property
    def approval(self) -> "Approval | None":
        """Return the latest non-rolled-back approval, for backwards compatibility."""
        valid = [a for a in self.approvals if not a.is_rolled_back]
        return valid[-1] if valid else None


class ReportPhoto(TimestampMixin, Base):
    __tablename__ = "report_photos"

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    s3_url: Mapped[str] = mapped_column(Text, nullable=False)
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    ai_tags: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    report: Mapped["Report"] = relationship(back_populates="photos")


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        # Removed unique=True — multiple approvals per report allowed after rollback
    )
    manager_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    action: Mapped[ApprovalAction] = mapped_column(
        Enum(ApprovalAction, name="approval_action"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text)
    final_pct: Mapped[float | None] = mapped_column(Float)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Rollback fields
    is_rolled_back: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    rollback_reason: Mapped[str | None] = mapped_column(Text)

    report: Mapped["Report"] = relationship(back_populates="approvals")
    manager: Mapped["User"] = relationship(foreign_keys=[manager_id])
    rolled_back_by_user: Mapped["User | None"] = relationship(foreign_keys=[rolled_back_by])  # noqa: F821