# app/schemas/model_file.py
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.model_file import ModelSourceType, ParseStatus


class ManualZoneMap(BaseModel):
    zone_map: dict[str, str]  # {mesh_id: zone_name}


class ModelFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    original_s3_key: str | None
    gltf_s3_key: str | None
    source_type: ModelSourceType | None
    parse_status: ParseStatus
    parse_error: str | None
    zone_map: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    gltf_url: str | None = None  # pre-signed URL, populated at read time
