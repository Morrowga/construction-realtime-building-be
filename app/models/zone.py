# app/models/zone.py
import enum
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class ZoneType(str, enum.Enum):
    room = "room"
    open_area = "open_area"
    corridor = "corridor"
    stairwell = "stairwell"
    mechanical = "mechanical"
    structural = "structural"
    facade = "facade"
    roof = "roof"


class Zone(TimestampMixin, Base):
    __tablename__ = "zones"

    floor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("floors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255))
    zone_type: Mapped[ZoneType | None] = mapped_column(Enum(ZoneType, name="zone_type"))
    model_mesh_id: Mapped[str | None] = mapped_column(String(255))  # maps to GLTF mesh node name
    # {floor: {type, color}, wall: {type, color}, ceiling: {type}, fixtures: []}
    finish_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    floor: Mapped["Floor"] = relationship(back_populates="zones")  # noqa: F821
    tasks: Mapped[list["ZoneTask"]] = relationship(  # noqa: F821
        back_populates="zone", cascade="all, delete-orphan"
    )
