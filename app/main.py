# app/main.py
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import text


from app.config import settings
from app.database import engine
from app.routers import (
    approvals,
    auth,
    contact,
    floors,
    model_files,
    organizations,
    projects,
    reports,
    tasks,
    websocket,
    zones,
)
from app.services import realtime_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")
request_logger = logging.getLogger("app.request")


@asynccontextmanager
async def lifespan(app: FastAPI):
    listener_task = asyncio.create_task(realtime_service.redis_listener())
    logger.info("Application startup complete (env=%s)", settings.environment)
    yield
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass
    await realtime_service.redis_client.close()
    await engine.dispose()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="Construction Progress Platform API",
    description=(
        "Backend for a realtime 3D construction progress platform. Site engineers "
        "submit photo reports, AI estimates completion, managers approve, and the "
        "3D building model updates live over WebSocket."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# --- CORS -------------------------------------------------------------------
if settings.environment == "development" or settings.cors_origins.strip() == "*":
    allow_origins = ["*"]
else:
    allow_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request logging middleware ---------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    request_logger.info(
        "%s %s -> %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# --- Global exception handler -----------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# --- Health check -----------------------------------------------------------
@app.get("/health", tags=["health"])
async def health() -> dict:
    db_status = "ok"
    redis_status = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_status = "error"

    try:
        await realtime_service.redis_client.ping()
    except Exception:  # noqa: BLE001
        redis_status = "error"

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"
    return {"status": overall, "db": db_status, "redis": redis_status}

@app.get("/viewer", include_in_schema=False)
async def viewer():
    return FileResponse("viewer.html")
    
# --- Local file serving (only when USE_LOCAL_STORAGE=true) ------------------
if os.environ.get("USE_LOCAL_STORAGE", "").lower() in ("true", "1", "yes"):
    _local_root = Path(os.environ.get("LOCAL_STORAGE_PATH", "/tmp/construction_local_storage"))
    _local_root.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(_local_root)), name="local_files")
    logger.info("Local file storage mounted at /files -> %s", _local_root)


# --- Routers ----------------------------------------------------------------
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(contact.router)
app.include_router(projects.router)
app.include_router(floors.router)
app.include_router(zones.router)
app.include_router(tasks.router)
app.include_router(reports.router)
app.include_router(approvals.router)
app.include_router(model_files.router)
app.include_router(websocket.router)