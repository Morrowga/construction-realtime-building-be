# app/schemas/zone.py
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.zone import ZoneType
from app.schemas.task import ZoneTaskOut


class ZoneCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str = Field(min_length=1, max_length=255)
    zone_type: ZoneType = ZoneType.room
    model_mesh_id: str | None = None
    finish_data: dict[str, Any] | None = None
    task_template_id: uuid.UUID | None = None  # optionally attach a task on creation


class ZoneUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str | None = Field(default=None, min_length=1, max_length=255)
    zone_type: ZoneType | None = None
    model_mesh_id: str | None = None
    finish_data: dict[str, Any] | None = None


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    floor_id: uuid.UUID
    name: str | None
    zone_type: ZoneType | None
    model_mesh_id: str | None
    finish_data: dict[str, Any] | None
    created_at: datetime
    tasks: list[ZoneTaskOut] = []


class ZoneProgressOut(BaseModel):
    zone_id: uuid.UUID
    name: str | None
    mesh_id: str | None
    pct: float
    tasks: list[ZoneTaskOut]
