# app/models/floor.py
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class Floor(TimestampMixin, Base):
    __tablename__ = "floors"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(100))  # e.g. "1F", "B1", "RF"
    level_number: Mapped[int | None] = mapped_column(Integer)  # -1 for B1, 0 ground, 1 for 1F...
    display_order: Mapped[int | None] = mapped_column(Integer)

    project: Mapped["Project"] = relationship(back_populates="floors")  # noqa: F821
    zones: Mapped[list["Zone"]] = relationship(  # noqa: F821
        back_populates="floor", cascade="all, delete-orphan"
    )
