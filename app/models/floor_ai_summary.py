# app/models/floor_ai_summary.py
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class FloorAISummary(TimestampMixin, Base):
    """One row per floor — the stored result of the last AI analysis run.

    PREVIOUSLY there was no storage for this at all: the viewer called the
    AI live, on every single floor-arrow click, re-running the full Claude
    call each time even when nothing had changed since the last click.
    Now: generation happens exactly once, triggered by an actual approval
    /rejection/rollback event (see workers/tasks.py generate_floor_ai_summary,
    called from routers/approvals.py), and the viewer just reads this
    stored row — a plain DB read, no AI call, no matter how many times
    someone clicks that floor.
    """

    __tablename__ = "floor_ai_summaries"

    floor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("floors.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rooms: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # {zone_id: {name, pct, colour_signal, analysis}}
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    floor: Mapped["Floor"] = relationship()  # noqa: F821