# Construction Progress Platform — Backend

FastAPI backend for a realtime 3D construction progress platform. Site engineers submit
photo reports from the field, Claude analyses them and estimates task completion, a site
manager approves the analysis, and the building's 3D model updates live over WebSocket.

## Architecture

- **FastAPI** (async) — REST API + native WebSocket
- **PostgreSQL 15** — SQLAlchemy 2.0 async + asyncpg, migrations via Alembic
- **Redis** — Celery broker/backend + pub/sub fan-out for multi-instance WebSocket sync
- **Celery** — background AI analysis and IFC/PDF model parsing
- **AWS S3** — photo and model file storage
- **Anthropic Claude API** — multimodal photo/note analysis (`claude-sonnet-4-6`)
- **IfcOpenShell** (optional) — IFC space extraction; import-guarded

## Setup instructions

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env: set SECRET_KEY, AWS credentials, and ANTHROPIC_API_KEY
```

### 2. Start the stack

```bash
docker compose up --build -d
```

This starts `api` (port 8000), `worker` (Celery), `postgres`, and `redis`.

### 3. Create and run the initial migration

```bash
docker compose exec api alembic revision --autogenerate -m "initial schema"
docker compose exec api alembic upgrade head
```

The Alembic env enables the `uuid-ossp` extension automatically before migrating
(needed for the `uuid_generate_v4()` primary-key defaults).

### 4. Verify

```bash
curl http://localhost:8000/health
# {"status":"ok","db":"ok","redis":"ok"}
```

Interactive API docs: http://localhost:8000/docs

### 5. (Optional) Enable IFC parsing

IfcOpenShell is import-guarded and not in `requirements.txt` (its wheels are large and
platform-specific). To enable IFC model parsing:

```bash
docker compose exec worker pip install ifcopenshell
docker compose restart worker
```

PDF floor-plan parsing works out of the box via the Claude API.

## Local development without Docker

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Point DATABASE_URL / REDIS_URL in .env at local Postgres/Redis, then:
alembic upgrade head
uvicorn app.main:app --reload                       # terminal 1
celery -A app.workers.celery_app.celery_app worker  # terminal 2
```

## Core flow

1. `POST /api/v1/auth/register` / `login` → JWT access (30 min) + refresh (7 days)
2. Admin: `POST /api/v1/projects`, add floors, zones, and zone tasks
3. Engineer: `POST /api/v1/reports` (multipart: `data` JSON + up to 5 photos ≤10MB,
   JPEG/PNG) → photos stored in S3, AI analysis queued, report returned immediately
4. Worker runs Claude analysis → `ai_analysis_complete` pushed over WebSocket
5. Manager: `POST /api/v1/reports/{id}/approval` → zone task progress updated,
   `progress_update` broadcast to `/ws/{project_id}` on every API instance via Redis
6. Client connects to `ws://host/ws/{project_id}?token=<jwt>` → receives
   `initial_state` then live updates; `GET .../model` returns a pre-signed GLTF URL

## Realtime event types

| type | trigger |
|---|---|
| `initial_state` | on WebSocket connect |
| `progress_update` | manager approves a report |
| `report_rejected` | manager rejects a report |
| `ai_analysis_complete` | Celery worker finishes AI analysis |
| `model_parse_complete` | IFC/PDF zone-map parse finishes |
