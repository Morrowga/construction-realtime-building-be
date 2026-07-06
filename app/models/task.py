# app/models/task.py
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class TaskCategory(str, enum.Enum):
    concrete = "concrete"
    rebar = "rebar"
    formwork = "formwork"
    waterproofing = "waterproofing"
    electrical = "electrical"
    plumbing = "plumbing"
    tiling = "tiling"
    painting = "painting"
    fixtures = "fixtures"
    glazing = "glazing"
    roofing = "roofing"
    insulation = "insulation"
    finishing = "finishing"
    structural = "structural"
    other = "other"


class TaskTemplate(TimestampMixin, Base):
    __tablename__ = "task_templates"

    name: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[TaskCategory | None] = mapped_column(Enum(TaskCategory, name="task_category"))


class ZoneTask(TimestampMixin, Base):
    __tablename__ = "zone_tasks"

    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_templates.id"), nullable=False
    )
    progress_pct: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0", nullable=False
    )
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Layer ordering — lower number = earlier in construction sequence
    # e.g. framing=1, concrete=2, waterproofing=3, tiling=4, painting=5, fixtures=6, finishing=7
    layer_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    # 3D colour signal — recalculated on every approval/rollback
    # 'grey'  = not started (0%)
    # 'amber' = active layer in progress (1-99%)
    # 'green' = this layer complete (100%)
    colour_signal: Mapped[str] = mapped_column(
        String(10), default="grey", server_default="grey", nullable=False
    )

    # The currently active layer's name and % for viewer display
    active_layer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active_layer_pct: Mapped[float] = mapped_column(
        Float, default=0.0, server_default="0.0", nullable=False
    )

    zone: Mapped["Zone"] = relationship(back_populates="tasks")  # noqa: F821
    template: Mapped["TaskTemplate"] = relationship()