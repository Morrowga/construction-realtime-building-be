# app/routers/floor_ai.py
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.floor import Floor
from app.models.floor_ai_summary import FloorAISummary
from app.models.task import ZoneTask
from app.models.user import User
from app.models.zone import Zone
from app.utils.permissions import check_project_access

router = APIRouter(prefix="/api/v1/floors/{floor_id}", tags=["floor-ai"])


def _zone_pct_and_signal(zone: Zone) -> tuple[float, str]:
    """Mirrors build_progress_tree's aggregation — used only for the
    no-summary-yet fallback below, so numbers still agree with the rest
    of the app even before any AI summary has ever been generated."""
    if not zone.tasks:
        return 0.0, "grey"
    task_pcts = [t.progress_pct for t in zone.tasks]
    pct = round(sum(task_pcts) / len(task_pcts), 1)
    active = max(zone.tasks, key=lambda t: t.progress_pct)
    signal = active.colour_signal or (
        "green" if active.progress_pct >= 100.0 else
        "amber" if active.progress_pct > 0.0 else "grey"
    )
    return pct, signal


@router.get("/ai-summary")
async def floor_ai_summary(
    floor_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Room-by-room AI summary for one floor, shown in the 3D viewer's
    side panel.

    PREVIOUSLY this endpoint called Claude live, on every single request
    — meaning every floor-arrow click in the viewer triggered a fresh AI
    call, even if nothing had changed since the last click. Now it's a
    plain read of the stored floor_ai_summaries row, which only gets
    (re)generated once, asynchronously, right after an approval/rollback
    actually changes something (see routers/approvals.py +
    workers/tasks.py generate_floor_ai_summary). This endpoint never
    calls the AI itself anymore.
    """
    floor = await db.get(Floor, floor_id)
    if floor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Floor not found")
    await check_project_access(db, floor.project_id, current_user)

    stored = (
        await db.execute(
            select(FloorAISummary).where(FloorAISummary.floor_id == floor_id)
        )
    ).scalar_one_or_none()

    if stored is not None:
        return {
            "summary": stored.summary,
            "rooms": list(stored.rooms.values()),
            "generated_at": stored.generated_at.isoformat(),
        }

    # No summary generated yet for this floor (no approval has ever fired
    # for it) — fall back to real numbers with no AI narrative, rather
    # than an error or a fake placeholder summary.
    stmt = (
        select(Zone)
        .where(Zone.floor_id == floor_id)
        .options(selectinload(Zone.tasks).selectinload(ZoneTask.template))
    )
    zones = (await db.execute(stmt)).scalars().all()

    rooms_out = []
    for zone in zones:
        pct, signal = _zone_pct_and_signal(zone)
        rooms_out.append({
            "zone_id": str(zone.id),
            "name": zone.name,
            "pct": pct,
            "colour_signal": signal,
            "analysis": "",
        })

    return {
        "summary": "この階はまだAI分析が実行されていません。承認が行われると自動的に分析されます。",
        "rooms": rooms_out,
        "generated_at": None,
    }