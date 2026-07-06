# app/schemas/floor.py
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FloorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)  # e.g. "1F", "B1", "RF"
    level_number: int
    display_order: int | None = None


class FloorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    level_number: int | None = None
    display_order: int | None = None


class FloorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str | None
    level_number: int | None
    display_order: int | None
    created_at: datetime


class FloorWithZoneCountOut(FloorOut):
    zone_count: int = 0
