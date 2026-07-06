# app/routers/approvals.py
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.floor import Floor
from app.models.report import Approval, ApprovalAction, Report, ReportStatus
from app.models.task import ZoneTask
from app.models.user import User
from app.models.zone import Zone
from app.schemas.report import ApprovalCreate, ApprovalOut
from app.services import realtime_service
from app.utils.permissions import check_project_access

router = APIRouter(prefix="/api/v1/reports/{report_id}/approval", tags=["approvals"])


async def _load_report_with_chain(db: AsyncSession, report_id: uuid.UUID):
    stmt = (
        select(Report, ZoneTask, Zone, Floor)
        .join(ZoneTask, Report.zone_task_id == ZoneTask.id)
        .join(Zone, ZoneTask.zone_id == Zone.id)
        .join(Floor, Zone.floor_id == Floor.id)
        .where(Report.id == report_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return row


async def _get_template_name(db: AsyncSession, zone_task_id: uuid.UUID) -> str | None:
    """Eagerly load template name — never use lazy relationship in async context."""
    stmt = (
        select(ZoneTask)
        .where(ZoneTask.id == zone_task_id)
        .options(selectinload(ZoneTask.template))
    )
    zt = (await db.execute(stmt)).scalar_one_or_none()
    return zt.template.name if (zt and zt.template) else None


async def _recalculate_zone_task(
    db: AsyncSession,
    zone_task: ZoneTask,
    template_name: str | None = None,
) -> None:
    """Recalculate progress_pct and colour_signal from latest non-rolled-back approval.
    template_name passed explicitly — never accessed via lazy relationship."""
    stmt = (
        select(Approval)
        .join(Report, Approval.report_id == Report.id)
        .where(
            Report.zone_task_id == zone_task.id,
            Approval.action == ApprovalAction.approved,
            Approval.is_rolled_back == False,  # noqa: E712
        )
        .order_by(Approval.approved_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    latest_approval = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if latest_approval is None:
        zone_task.progress_pct = 0.0
        zone_task.colour_signal = "grey"
        zone_task.active_layer_pct = 0.0
        zone_task.active_layer_name = template_name
    else:
        pct = latest_approval.final_pct or 0.0
        zone_task.progress_pct = pct
        zone_task.active_layer_pct = pct
        zone_task.active_layer_name = template_name
        zone_task.colour_signal = (
            "green" if pct >= 100.0 else "amber" if pct > 0.0 else "grey"
        )

    zone_task.last_updated_at = now


@router.post("", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
async def create_approval(
    body: ApprovalCreate,
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Approval:
    report, zone_task, zone, floor = await _load_report_with_chain(db, report_id)
    project_id = floor.project_id

    await check_project_access(db, project_id, current_user, allowed_project_roles={"manager"})

    if report.status != ReportStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Report has already been {report.status.value}",
        )

    # Fetch template name eagerly before any flush
    template_name = await _get_template_name(db, zone_task.id)
    now = datetime.now(timezone.utc)

    if body.action == ApprovalAction.approved:
        final_pct = body.final_pct
        if final_pct is None:
            final_pct = report.ai_progress_pct
        if final_pct is None:
            final_pct = report.engineer_progress_pct
        if final_pct is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="final_pct is required when no AI or engineer estimate exists",
            )
        final_pct = max(0.0, min(100.0, float(final_pct)))

        approval = Approval(
            report_id=report.id,
            manager_id=current_user.id,
            action=ApprovalAction.approved,
            comment=body.comment,
            final_pct=final_pct,
            approved_at=now,
            is_rolled_back=False,
        )
        db.add(approval)
        report.status = ReportStatus.approved
        report.final_progress_pct = final_pct

        await db.flush()
        await _recalculate_zone_task(db, zone_task, template_name)
        await db.commit()
        await db.refresh(approval)

        await realtime_service.broadcast(
            project_id,
            {
                "type": "progress_update",
                "zone_task_id": str(zone_task.id),
                "zone_id": str(zone.id),
                "floor_id": str(floor.id),
                "mesh_id": zone.model_mesh_id,
                "new_pct": zone_task.progress_pct,
                "colour_signal": zone_task.colour_signal,
                "active_layer_name": zone_task.active_layer_name,
                "active_layer_pct": zone_task.active_layer_pct,
                "layer_order": zone_task.layer_order,
            },
        )
        return approval

    # Rejection
    approval = Approval(
        report_id=report.id,
        manager_id=current_user.id,
        action=ApprovalAction.rejected,
        comment=body.comment,
        final_pct=None,
        approved_at=now,
        is_rolled_back=False,
    )
    db.add(approval)
    report.status = ReportStatus.rejected
    await db.commit()
    await db.refresh(approval)

    await realtime_service.broadcast(
        project_id,
        {
            "type": "report_rejected",
            "report_id": str(report.id),
            "engineer_id": str(report.engineer_id),
            "zone_task_id": str(zone_task.id),
            "comment": body.comment,
        },
    )
    return approval


@router.post("/rollback", status_code=status.HTTP_200_OK)
async def rollback_approval(
    report_id: uuid.UUID,
    reason: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    report, zone_task, zone, floor = await _load_report_with_chain(db, report_id)
    project_id = floor.project_id

    await check_project_access(db, project_id, current_user, allowed_project_roles={"manager"})

    if report.status != ReportStatus.approved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only approved reports can be rolled back",
        )

    result = await db.execute(
        select(Approval).where(
            Approval.report_id == report.id,
            Approval.action == ApprovalAction.approved,
            Approval.is_rolled_back == False,  # noqa: E712
        )
    )
    approval = result.scalar_one_or_none()
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active approval found for this report",
        )

    template_name = await _get_template_name(db, zone_task.id)
    now = datetime.now(timezone.utc)
    previous_pct = zone_task.progress_pct

    approval.is_rolled_back = True
    approval.rolled_back_at = now
    approval.rolled_back_by = current_user.id
    approval.rollback_reason = reason

    report.status = ReportStatus.pending
    report.final_progress_pct = None

    await db.flush()
    await _recalculate_zone_task(db, zone_task, template_name)
    await db.commit()

    reverted_pct = zone_task.progress_pct

    await realtime_service.broadcast(
        project_id,
        {
            "type": "progress_rollback",
            "zone_task_id": str(zone_task.id),
            "zone_id": str(zone.id),
            "floor_id": str(floor.id),
            "mesh_id": zone.model_mesh_id,
            "previous_pct": previous_pct,
            "reverted_to_pct": reverted_pct,
            "colour_signal": zone_task.colour_signal,
            "active_layer_name": zone_task.active_layer_name,
            "active_layer_pct": zone_task.active_layer_pct,
            "rolled_back_by": current_user.full_name or str(current_user.id),
            "reason": reason,
        },
    )

    return {
        "message": "Report rolled back successfully",
        "report_id": str(report.id),
        "previous_pct": previous_pct,
        "reverted_to_pct": reverted_pct,
        "colour_signal": zone_task.colour_signal,
        "rolled_back_by": current_user.full_name,
        "reason": reason,
    }


@router.get("", response_model=ApprovalOut)
async def get_approval(
    report_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Approval:
    report, _, _, floor = await _load_report_with_chain(db, report_id)
    await check_project_access(db, floor.project_id, current_user)

    result = await db.execute(
        select(Approval)
        .where(Approval.report_id == report.id)
        .order_by(Approval.approved_at.desc())
        .limit(1)
    )
    approval = result.scalar_one_or_none()
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No approval exists for this report"
        )
    return approval