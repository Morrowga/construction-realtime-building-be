# app/services/realtime_service.py
import asyncio
import json
import logging
import uuid
from collections import defaultdict
from typing import Any

import redis.asyncio as aioredis
from fastapi import WebSocket

from app.config import settings

logger = logging.getLogger("app.realtime")

# Unique per API process so we can skip our own Redis echoes.
INSTANCE_ID = uuid.uuid4().hex

redis_client: aioredis.Redis = aioredis.from_url(settings.redis_url, decode_responses=True)

CHANNEL_PREFIX = "progress"


class ConnectionManager:
    """Tracks active WebSocket connections per project (this process only)."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, project_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[str(project_id)].add(websocket)
        logger.info("WS connected project=%s (local total=%d)", project_id, len(self._connections[str(project_id)]))

    async def disconnect(self, project_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[str(project_id)].discard(websocket)
        logger.info("WS disconnected project=%s", project_id)

    async def send_local(self, project_id: str, message: dict[str, Any]) -> None:
        """Send a JSON message to all sockets for this project on this process."""
        sockets = list(self._connections.get(str(project_id), set()))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 — drop broken sockets
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections[str(project_id)].discard(ws)


manager = ConnectionManager()


async def broadcast(project_id: str | uuid.UUID, message: dict[str, Any]) -> None:
    """Send to local sockets immediately, then publish to Redis pub/sub so every
    other API instance delivers the message to its own connected clients."""
    pid = str(project_id)
    await manager.send_local(pid, message)
    envelope = json.dumps({"origin": INSTANCE_ID, "data": message})
    try:
        await redis_client.publish(f"{CHANNEL_PREFIX}:{pid}", envelope)
    except Exception as exc:  # noqa: BLE001
        logger.error("Redis publish failed for project=%s: %s", pid, exc)


async def redis_listener() -> None:
    """Background task: subscribe to progress:* and rebroadcast messages that
    originated from other instances (or from Celery workers)."""
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe(f"{CHANNEL_PREFIX}:*")
    logger.info("Redis listener subscribed to %s:* (instance=%s)", CHANNEL_PREFIX, INSTANCE_ID)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            try:
                channel: str = message["channel"]
                project_id = channel.split(":", 1)[1]
                envelope = json.loads(message["data"])
                if envelope.get("origin") == INSTANCE_ID:
                    continue  # already delivered locally by broadcast()
                await manager.send_local(project_id, envelope["data"])
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to handle pub/sub message: %s", exc)
    except asyncio.CancelledError:
        await pubsub.close()
        raise
