# app/models/model_file.py
import enum
import uuid
from typing import Any

from sqlalchemy import Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, TimestampMixin


class ModelSourceType(str, enum.Enum):
    ifc = "ifc"
    pdf = "pdf"
    manual = "manual"


class ParseStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class ModelFile(TimestampMixin, Base):
    __tablename__ = "model_files"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    original_s3_key: Mapped[str | None] = mapped_column(Text)
    gltf_s3_key: Mapped[str | None] = mapped_column(Text)

    # 3-layer viewer support (skeleton = gltf_s3_key above, these two are
    # the additional layers). Both nullable — a project can have just a
    # skeleton with no envelope/interior uploaded yet, viewer.html already
    # handles that gracefully.
    envelope_s3_key: Mapped[str | None] = mapped_column(Text)
    interior_s3_key: Mapped[str | None] = mapped_column(Text)

    source_type: Mapped[ModelSourceType | None] = mapped_column(
        Enum(ModelSourceType, name="model_source_type")
    )
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus, name="parse_status"),
        default=ParseStatus.pending,
        server_default="pending",
        nullable=False,
    )
    parse_error: Mapped[str | None] = mapped_column(Text)
    zone_map: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {mesh_id: zone_name}

    project: Mapped["Project"] = relationship()  # noqa: F821