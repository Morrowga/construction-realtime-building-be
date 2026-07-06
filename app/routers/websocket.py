# app/routers/websocket.py
import logging
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, WebSocketException, status

from app.database import async_session_factory
from app.models.user import User, UserRole
from app.routers.projects import build_progress_tree
from app.services import auth_service
from app.services.realtime_service import manager
from app.utils.permissions import get_membership

router = APIRouter(tags=["websocket"])

logger = logging.getLogger("app.ws")


@router.websocket("/ws/{project_id}")
async def project_websocket(
    websocket: WebSocket,
    project_id: uuid.UUID,
    token: str = Query(...),
) -> None:
    """Realtime progress channel for a project.

    Authenticated via `?token=<jwt>`. On connect the full current progress state
    is sent; afterwards the socket receives `progress_update` broadcasts.
    """
    payload = auth_service.decode_token(token)
    if payload is None or payload.get("type") != "access":
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")

    async with async_session_factory() as db:
        try:
            user = await db.get(User, uuid.UUID(payload["sub"]))
        except (KeyError, ValueError):
            user = None
        if user is None or not user.is_active:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="User not found")

        # Membership check (global admins, project admin/client, or members).
        from app.models.project import Project  # local import to avoid cycles

        project = await db.get(Project, project_id)
        if project is None:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Project not found")

        is_allowed = (
            user.role == UserRole.admin
            or project.admin_id == user.id
            or project.client_id == user.id
            or await get_membership(db, project_id, user.id) is not None
        )
        if not is_allowed:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION, reason="Not a member of this project"
            )

        initial_state = await build_progress_tree(db, project_id)

    await manager.connect(str(project_id), websocket)
    try:
        await websocket.send_json({"type": "initial_state", "progress": initial_state})
        while True:
            # Keep the connection alive; inbound messages are ignored (ping/pong etc.).
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebSocket error on project=%s: %s", project_id, exc)
    finally:
        await manager.disconnect(str(project_id), websocket)
