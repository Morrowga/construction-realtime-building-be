# 建設現場リアルタイム3D進捗管理プラットフォーム
## Development Summary — Phase 1 Complete

**Date:** July 4, 2026  
**Stack:** Python / FastAPI / PostgreSQL / Redis / Celery / Three.js  
**Status:** Backend complete, Web viewer functional, Ready for React Native

---

## Architecture

```
Mobile App (React Native + Expo)
        ↕ REST API + WebSocket
FastAPI Backend (Docker)
        ↕
PostgreSQL 15    Redis 7    AWS S3 / Local Storage
        ↕
Celery Worker → Claude API (claude-sonnet-4-6)
```

---

## Backend — Complete ✅

### Tech stack
- **Runtime:** Python 3.11
- **Framework:** FastAPI (async)
- **Database:** PostgreSQL 15 — SQLAlchemy 2.0 async + asyncpg
- **Migrations:** Alembic
- **Cache / Pub-Sub:** Redis 7
- **Background jobs:** Celery + Redis broker
- **File storage:** Local storage (S3-ready, toggle via `USE_LOCAL_STORAGE`)
- **AI analysis:** Anthropic Claude API (`claude-sonnet-4-6`, multimodal)
- **Auth:** JWT (python-jose) + bcrypt
- **Realtime:** WebSocket (FastAPI native) + Redis pub/sub fan-out
- **Container:** Docker + docker-compose (4 services: api, worker, postgres, redis)

### Database schema — 12 tables

| Table | Purpose |
|---|---|
| `users` | 4 roles: admin, manager, engineer, client |
| `projects` | Project metadata, GPS geofencing |
| `project_members` | Role-based project membership |
| `floors` | Building floors with level_number and display_order |
| `zones` | Rooms/areas with mesh_id (maps to GLTF), finish_data (JSON) |
| `task_templates` | 12 construction categories |
| `zone_tasks` | Tasks per zone with layer_order, colour_signal, active_layer fields |
| `reports` | Engineer photo reports with AI analysis results |
| `report_photos` | S3 photo storage with AI tags |
| `approvals` | Manager approvals with rollback support |
| `model_files` | GLB/IFC/PDF model file tracking |

### Key schema features
- `zone_tasks.layer_order` — construction sequence order per zone
- `zone_tasks.colour_signal` — `green` / `amber` / `grey` for 3D rendering
- `zone_tasks.active_layer_name` / `active_layer_category` — current construction phase
- `approvals.is_rolled_back` — full rollback audit trail, nothing deleted
- `approvals` has no UNIQUE constraint — supports multiple approvals per report after rollback

### API endpoints — 35 endpoints

#### Auth `/api/v1/auth`
- `POST /register` — create user
- `POST /login` — email + password → JWT tokens
- `POST /refresh` — refresh token
- `GET  /me` — current user

#### Projects `/api/v1/projects`
- `POST /` — create project
- `GET  /` — list user's projects
- `GET  /{id}` — project detail + overall %
- `PATCH /{id}` — update metadata
- `POST /{id}/members` — invite user by email
- `GET  /{id}/progress` — **main 3D data endpoint** (floor/zone/task/colour_signal)

#### Floors `/api/v1/projects/{id}/floors`
- `POST /` — create floor
- `GET  /` — list with zone count

#### Zones `/api/v1/floors/{id}/zones`
- `POST /` — create zone with mesh_id and finish_data
- `GET  /` — list with progress per task
- `PATCH /{id}` — update mesh_id / finish_data
- `GET  /{id}/progress` — full task breakdown

#### Tasks `/api/v1`
- `GET  /task-templates` — list all templates
- `POST /task-templates` — create custom template
- `POST /zones/{id}/tasks` — assign template to zone with layer_order

#### Reports `/api/v1/reports`
- `POST /` — submit report (multipart: JSON + up to 5 photos)
- `GET  /` — list with filters (project, zone, status, engineer)
- `GET  /{id}` — detail with photos and AI analysis
- `DELETE /{id}` — delete own pending report

#### Approvals `/api/v1/reports/{id}/approval`
- `POST /` — approve or reject
- `POST /rollback` — roll back approved report → 3D reverts live
- `GET  /` — get approval record

#### Model files `/api/v1/projects/{id}/model`
- `POST /upload` — upload GLB / GLTF / IFC / PDF
- `GET  /` — model status + presigned URL
- `POST /manual` — save manual zone map
- `GET  /zone-map` — get parsed zone map

#### WebSocket `ws://{host}/ws/{project_id}?token={jwt}`
- On connect: sends `initial_state`
- On approval: broadcasts `progress_update` with `colour_signal`
- On rollback: broadcasts `progress_rollback` with `reverted_to_pct`
- On rejection: broadcasts `report_rejected`
- On AI complete: broadcasts `ai_analysis_complete`
- On model parse: broadcasts `model_parse_complete`

### AI analysis pipeline
1. Engineer submits report with photos
2. API stores photos to S3, creates report record, returns immediately
3. Celery task `run_ai_analysis` fires async
4. Claude API (multimodal) analyses all photos + text note
5. Returns structured JSON: `progress_pct`, `confidence`, `note_match`, `flags`, `summary`
6. Worker updates report with AI results
7. WebSocket broadcasts `ai_analysis_complete` to manager

### Rollback system
- Manager calls `POST /reports/{id}/approval/rollback` with reason
- Approval marked `is_rolled_back = true` (never deleted)
- `zone_task.progress_pct` recalculated from previous valid approval
- `colour_signal` recalculated automatically
- WebSocket broadcasts `progress_rollback` — 3D model reverts live

### Construction layer system
Each zone has ordered tasks matching real construction sequence:

```
Bedroom zone:
  1. Concrete Pour     (concrete)      → grey #909090
  2. Electrical Wiring (electrical)    → yellow #E8C040
  3. Insulation        (insulation)    → yellow #D4A84B
  4. Tiling            (tiling)        → beige #C8B89A
  5. Painting          (painting)      → light warm #E0D5C5
  6. Fixtures          (fixtures)      → cream #D0C0A8

Structural slab:
  1. Rebar Installation (rebar)        → rust brown #8B4513
  2. Formwork           (formwork)     → wood brown #8B7355
  3. Concrete Pour      (concrete)     → grey #909090

Bathroom:
  1. Concrete Pour      (concrete)
  2. Plumbing           (plumbing)     → teal #3A9080
  3. Waterproofing      (waterproofing)→ blue #4A7FA5
  4. Tiling             (tiling)
  5. Fixtures           (fixtures)
```

---

## Test Data — Sakura Residence (サクラレジデンス新築工事)

### Users
| Email | Role | Password |
|---|---|---|
| admin@sakura.com | admin | Test1234! |
| manager@sakura.com | manager | Test1234! |
| engineer@sakura.com | engineer | Test1234! |
| client@sakura.com | client | Test1234! |

### Building structure
```
RF   — 屋上防水        Structure_Slab_Roof   0%
5F   — スラブ工事       Structure_Slab_5F     0%
4F   — スラブ工事       Structure_Slab_4F     0%
3F   — テラス          Zone_Terrace_3F       30%  (waterproofing active → blue)
3F   — マスタースイート  Zone_MasterSuite_3F   40%  (electrical active → yellow)
3F   — スラブ工事       Structure_Slab_3F     60%  (concrete active → grey)
2F   — 寝室B           Zone_BedroomB_2F      60%  (concrete active → grey)
2F   — 寝室A           Zone_BedroomA_2F      65%  (concrete active → grey)
2F   — スラブ工事       Structure_Slab_2F     70%  (concrete active → grey)
1F   — バスルーム       Zone_Bathroom_1F     100%  (complete → finish colour)
1F   — リビングルーム    Zone_LivingRoom_1F   100%  (complete → finish colour)
1F   — ロビー          Zone_Lobby_1F        100%  (complete → finish colour)
基礎  — 基礎工事        Structure_Slab_1F    100%  (complete → green)
```

### 3D Model files
- `skeleton.glb` — raw concrete structure (92KB)
- `finished.glb` — fully dressed building with zone materials (143KB)
- Mesh naming: `Zone_*`, `Structure_Slab_*`, `Structure_Columns`, `Structure_Beams`, `Furniture_*`

---

## Web Viewer — Functional ✅

**File:** `viewer.html` — single HTML file, Three.js via CDN  
**Access:** `http://localhost:8000/viewer`

### Features
- Loads skeleton.glb + finished.glb simultaneously
- Colours meshes by `colour_signal` from progress API
- Category-based colour system (12 construction categories → distinct colours)
- Floor filter — click floor nav to isolate one floor
- Zone click — detail panel shows ordered construction layers
- 3 view modes: 進捗 (progress) / 骨格 (skeleton) / 完成図 (finished design)
- WebSocket live updates — mesh recolours on approval without refresh
- Rollback reflected live — mesh reverts when manager rolls back
- Cookie-persisted config (API URL, token, project ID, GLB keys)
- View presets: isometric / top / front / reset

### Colour system
| Signal | Meaning | 3D colour |
|---|---|---|
| `green` | All tasks 100% | Finish material colour |
| `amber` | Active layer in progress | Category colour (see above) |
| `grey` | Not started | Dark skeleton |

---

## Progress API Response — for 3D viewer

```json
{
  "overall_pct": 19.1,
  "floors": [
    {
      "floor_id": "uuid",
      "name": "1F",
      "pct": 30.0,
      "zones": [
        {
          "zone_id": "uuid",
          "name": "リビングルーム",
          "mesh_id": "Zone_LivingRoom_1F",
          "pct": 100.0,
          "colour_signal": "green",
          "active_layer_name": "Fixtures & Fittings",
          "active_layer_category": "fixtures",
          "active_layer_pct": 100.0,
          "finish_data": {
            "floor": {"type": "hardwood", "color": "#C8A882"},
            "wall": {"type": "paint", "color": "#FFFFFF"}
          },
          "tasks": [
            {
              "zone_task_id": "uuid",
              "task_name": "Concrete Pour",
              "task_category": "concrete",
              "pct": 100.0,
              "layer_order": 1,
              "colour_signal": "green"
            }
          ]
        }
      ]
    }
  ]
}
```

---

## WebSocket Events

```json
// Progress update (on approval)
{
  "type": "progress_update",
  "zone_task_id": "uuid",
  "zone_id": "uuid",
  "floor_id": "uuid",
  "mesh_id": "Zone_LivingRoom_1F",
  "new_pct": 75.0,
  "colour_signal": "amber",
  "active_layer_name": "Tiling",
  "active_layer_category": "tiling",
  "active_layer_pct": 75.0,
  "layer_order": 3
}

// Rollback (on rollback)
{
  "type": "progress_rollback",
  "mesh_id": "Zone_LivingRoom_1F",
  "previous_pct": 75.0,
  "reverted_to_pct": 50.0,
  "colour_signal": "amber",
  "rolled_back_by": "Yamamoto Manager",
  "reason": "Photo does not match reported progress"
}
```

---

## Local Development

```bash
# Start all services
docker compose up -d

# Run migrations
docker compose exec api alembic upgrade head

# Seed test data
python seed.py

# Upload skeleton GLB via Postman
# POST http://localhost:8000/api/v1/projects/{id}/model/upload

# Copy finished GLB
docker compose exec api cp /app/finished.glb \
  /tmp/construction_local_storage/projects/{id}/model/finished.glb

# Open viewer
open http://localhost:8000/viewer

# API docs
open http://localhost:8000/docs
```

---

## Files Changed / Created

| File | Status |
|---|---|
| `app/main.py` | Updated — local storage mount, lifespan handler |
| `app/models/report.py` | Updated — rollback fields, approvals list relationship |
| `app/models/task.py` | Updated — layer_order, colour_signal, active_layer fields |
| `app/routers/approvals.py` | Updated — rollback endpoint, colour_signal recalculation |
| `app/routers/projects.py` | Updated — active_layer_category in progress response |
| `app/routers/reports.py` | Updated — approvals list relationship fix |
| `app/routers/tasks.py` | Updated — layer_order saved on task assignment |
| `app/routers/model_files.py` | Updated — GLB/GLTF upload support |
| `app/schemas/report.py` | Updated — rollback fields, model_validator for approvals |
| `app/schemas/task.py` | Updated — layer_order, colour_signal in schema |
| `app/services/auth_service.py` | Updated — bcrypt direct (passlib removed) |
| `app/services/s3_service.py` | Updated — local storage mock |
| `alembic/versions/a1b2c3d4e5f6_*.py` | New — layer_order + rollback migration |
| `seed.py` | New — complete test data seeder |
| `viewer.html` | New — Three.js 3D progress viewer |

---

## Ready for React Native Frontend

### What the mobile app needs from backend

| Screen | API calls |
|---|---|
| Dashboard | `GET /projects` + `GET /projects/{id}/progress` |
| 3D viewer | `GET /projects/{id}/progress` + `GET /projects/{id}/model` + WebSocket |
| Report submission | `POST /reports` (multipart) |
| Approval (manager) | `GET /reports?status=pending` + `POST /reports/{id}/approval` |
| Rollback | `POST /reports/{id}/approval/rollback` |

### Frontend decisions needed
1. **Expo managed** or bare React Native?
2. **Three.js in WebView** (confirmed working) or Babylon.js native (Phase 2)?
3. **Phase 1 screens:** Dashboard, 3D viewer, Report submission, Manager approvals
4. **Language:** Japanese UI with English field names in API

---

*© 2026 Construction Progress Platform — Phase 1 Backend Complete*
