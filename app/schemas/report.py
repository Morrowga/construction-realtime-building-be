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
    manager_name: str | None = None
    action: ApprovalAction
    comment: str | None
    final_pct: float | None
    approved_at: datetime | None
    is_rolled_back: bool = False
    rolled_back_at: datetime | None = None
    rollback_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def resolve_manager_name(cls, data: Any) -> Any:
        """Requires Approval.manager to be eager-loaded by the caller
        (selectinload(Approval.manager)) — accessing it lazily in an async
        context without that would raise, not just silently skip."""
        manager = getattr(data, "manager", None)
        if manager is not None:
            data.__dict__["manager_name"] = manager.full_name or manager.email
        return data


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_task_id: uuid.UUID
    engineer_id: uuid.UUID
    engineer_name: str | None = None
    floor_name: str | None = None
    zone_name: str | None = None
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
    def resolve_related_fields(cls, data: Any) -> Any:
        """Pulls engineer name, floor name, and zone name off eager-loaded
        relationships (Report.engineer, Report.zone_task.zone.floor) — the
        raw Report/ZoneTask/Zone/Floor models only store IDs, these
        human-readable names never existed on the response before this.

        Also keeps the pre-existing approval-list -> single-approval
        resolution (latest non-rolled-back approval), unchanged.

        REQUIRES the router's query to eager-load all of these via
        selectinload — see reports.py. Without that, accessing e.g.
        data.engineer on an async session would raise a lazy-load error,
        not silently return None.
        """
        if hasattr(data, "approvals"):
            valid = [a for a in (data.approvals or []) if not a.is_rolled_back]
            data.__dict__["approval"] = valid[-1] if valid else None

        engineer = getattr(data, "engineer", None)
        if engineer is not None:
            data.__dict__["engineer_name"] = engineer.full_name or engineer.email

        zone_task = getattr(data, "zone_task", None)
        zone = getattr(zone_task, "zone", None) if zone_task is not None else None
        if zone is not None:
            data.__dict__["zone_name"] = zone.name
            floor = getattr(zone, "floor", None)
            if floor is not None:
                data.__dict__["floor_name"] = floor.name

        return data


class ApprovalCreate(BaseModel):
    action: ApprovalAction
    comment: str | None = None
    final_pct: float | None = Field(default=None, ge=0.0, le=100.0)