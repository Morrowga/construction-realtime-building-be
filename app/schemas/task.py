# app/schemas/task.py
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.task import TaskCategory


class TaskTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    category: TaskCategory = TaskCategory.other


class TaskTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    category: TaskCategory | None
    created_at: datetime


class ZoneTaskAssign(BaseModel):
    task_template_id: uuid.UUID
    layer_order: int = Field(default=0, ge=0, description="Construction sequence order. Lower = earlier. e.g. framing=1, concrete=2, tiling=3")


class ZoneTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_id: uuid.UUID
    task_template_id: uuid.UUID
    progress_pct: float
    layer_order: int
    colour_signal: str        # 'grey' | 'amber' | 'green'
    active_layer_name: str | None
    active_layer_pct: float
    last_updated_at: datetime | None
    template: TaskTemplateOut | None = None