# app/schemas/report.py
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.report import ApprovalAction, ReportStatus


class ReportCreate(BaseModel):
    """JSON body carried in the `data` field of the multipart request."""

    zone_task_id: uuid.UUID
    note: str | None = None
    engineer_progress_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    geo_lat: float | None = None
    geo_lng: float | None = None


class ReportPhotoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    s3_url: str
    s3_key: str
    ai_tags: dict[str, Any] | None
    order_index: int


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_id: uuid.UUID
    manager_id: uuid.UUID
    action: ApprovalAction
    comment: str | None
    final_pct: float | None
    approved_at: datetime | None
    is_rolled_back: bool = False
    rolled_back_at: datetime | None = None
    rollback_reason: str | None = None


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_task_id: uuid.UUID
    engineer_id: uuid.UUID
    note: str | None
    status: ReportStatus
    engineer_progress_pct: float | None
    ai_progress_pct: float | None
    ai_confidence: float | None
    ai_analysis: dict[str, Any] | None
    final_progress_pct: float | None
    geo_lat: float | None
    geo_lng: float | None
    submitted_at: datetime | None
    created_at: datetime
    photos: list[ReportPhotoOut] = []
    approval: ApprovalOut | None = None

    @model_validator(mode="before")
    @classmethod
    def resolve_approval(cls, data: Any) -> Any:
        """Handle both old single `approval` and new `approvals` list relationship."""
        if hasattr(data, "approvals"):
            # New model — use the @property which returns latest non-rolled-back approval
            valid = [a for a in (data.approvals or []) if not a.is_rolled_back]
            data.__dict__["approval"] = valid[-1] if valid else None
        return data


class ApprovalCreate(BaseModel):
    action: ApprovalAction
    comment: str | None = None
    final_pct: float | None = Field(default=None, ge=0.0, le=100.0)