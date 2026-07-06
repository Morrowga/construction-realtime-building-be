"""
Construction Progress Platform — Complete Test Data Seeder
==========================================================
Run this once against your live API to seed one organization, its team,
one project, and progress data that lands at ~90% overall.

Usage:
    pip install httpx
    python seed.py

REQUIRES settings.debug = True on the backend (see app/config.py) — the
script creates teammates via POST /api/v1/organizations/members, which
only returns a usable temp_password in the response when debug mode is
on. In production that endpoint only ever emails the password; nothing
this script does bypasses that, it just relies on the dev-mode return
value so you don't need a working SMTP server to seed a local database.

Mesh IDs are aligned with the skeleton/envelope/interior GLBs from Fable 5.
"""

import base64
import io
import json
import sys
import time
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"
PASSWORD = "Test1234!"

ORGANIZATION_NAME = "サクラ建設株式会社"

# First user creates the organization via /auth/register.
# Everyone else is added afterwards via POST /organizations/members
# (as the admin), which is the only way to create a non-owner user now.
ADMIN_USER = {"email": "admin@sakura.com", "full_name": "Tanaka Admin"}

TEAMMATES = [
    {"email": "manager@sakura.com",  "full_name": "Yamamoto Manager", "role": "manager"},
    {"email": "engineer@sakura.com", "full_name": "Sato Engineer",    "role": "engineer"},
    {"email": "client@sakura.com",   "full_name": "Suzuki Client",    "role": "client"},
]

TASK_TEMPLATES = [
    {"name": "Concrete Pour",       "category": "concrete"},
    {"name": "Rebar Installation",  "category": "rebar"},
    {"name": "Formwork",            "category": "formwork"},
    {"name": "Waterproofing",       "category": "waterproofing"},
    {"name": "Electrical Wiring",   "category": "electrical"},
    {"name": "Plumbing",            "category": "plumbing"},
    {"name": "Tiling",              "category": "tiling"},
    {"name": "Painting",            "category": "painting"},
    {"name": "Fixtures & Fittings", "category": "fixtures"},
    {"name": "Glazing",             "category": "glazing"},
    {"name": "Roofing",             "category": "roofing"},
    {"name": "Insulation",          "category": "insulation"},
]

PROJECT = {
    "name": "サクラレジデンス新築工事",
    "address": "Tokyo, Minato-ku, Japan",
    "planned_end_date": "2026-12-31",
    "report_format": "nikken",
    "geo_lat": 35.6584,
    "geo_lng": 139.7454,
    "geo_radius_m": 300,
}

FLOORS = [
    ("基礎",  -1, 1),
    ("1F",     1, 2),
    ("2F",     2, 3),
    ("3F",     3, 4),
    ("4F",     4, 5),
    ("5F",     5, 6),
    ("RF",     6, 7),
]

# 3D model GLBs — expected alongside this script (construction-backend/).
# Only "skeleton" gets uploaded through the API right now: model_files
# doesn't have envelope_s3_key/interior_s3_key columns yet (that's Step 3
# in CURRENT_STATE.md, not built). See the printed docker cp instructions
# at the end of this script for placing the other two.
SKELETON_GLB_PATH = Path(__file__).parent / "skeleton.glb"
ENVELOPE_GLB_PATH = Path(__file__).parent / "envelope.glb"
INTERIOR_GLB_PATH = Path(__file__).parent / "interior.glb"

# ---------------------------------------------------------------------------
# Zones — mesh_id aligned with skeleton/envelope/interior GLB mesh names
#
# Percentages below are chosen so the weighted overall (the backend
# averages every individual task's progress_pct, not zone percentages —
# zones with more tasks pull the average harder) lands at ~90.2%.
# ---------------------------------------------------------------------------

ZONE_TASK_SEQUENCES = {
    "Structure_Slab_1F":   [("Rebar Installation",1),("Formwork",2),("Concrete Pour",3),("Waterproofing",4)],
    "Zone_Lobby_1F":       [("Concrete Pour",1),("Electrical Wiring",2),("Plumbing",3),("Tiling",4),("Painting",5),("Fixtures & Fittings",6)],
    "Zone_LivingRoom_1F":  [("Concrete Pour",1),("Electrical Wiring",2),("Tiling",3),("Painting",4),("Fixtures & Fittings",5)],
    "Zone_Bathroom_1F":    [("Concrete Pour",1),("Plumbing",2),("Waterproofing",3),("Tiling",4),("Fixtures & Fittings",5)],
    "Structure_Slab_2F":   [("Rebar Installation",1),("Formwork",2),("Concrete Pour",3)],
    "Zone_BedroomA_2F":    [("Concrete Pour",1),("Electrical Wiring",2),("Insulation",3),("Tiling",4),("Painting",5),("Fixtures & Fittings",6)],
    "Zone_BedroomB_2F":    [("Concrete Pour",1),("Electrical Wiring",2),("Insulation",3),("Tiling",4),("Painting",5),("Fixtures & Fittings",6)],
    "Structure_Slab_3F":   [("Rebar Installation",1),("Formwork",2),("Concrete Pour",3)],
    "Zone_MasterSuite_3F": [("Concrete Pour",1),("Electrical Wiring",2),("Plumbing",3),("Insulation",4),("Tiling",5),("Painting",6),("Fixtures & Fittings",7)],
    "Zone_Terrace_3F":     [("Concrete Pour",1),("Waterproofing",2),("Tiling",3),("Glazing",4)],
    "Structure_Slab_4F":   [("Rebar Installation",1),("Formwork",2),("Concrete Pour",3)],
    "Structure_Slab_5F":   [("Rebar Installation",1),("Formwork",2),("Concrete Pour",3)],
    "Structure_Slab_Roof": [("Concrete Pour",1),("Waterproofing",2),("Roofing",3)],
}

# (floor_name, zone_name, zone_type, mesh_id, finish_data, progress_pct)
ZONES = [
    ("基礎", "基礎工事", "structural", "Structure_Slab_1F",
        {"floor": {"type": "concrete", "color": "#888888"},
         "wall":  {"type": "concrete", "color": "#888888"},
         "ceiling": {"type": "concrete"}, "fixtures": []},
        100.0),
    ("1F", "ロビー", "room", "Zone_Lobby_1F",
        {"floor": {"type": "marble",   "color": "#F5F0E8"},
         "wall":  {"type": "paint",    "color": "#FAFAFA"},
         "ceiling": {"type": "plaster"}, "fixtures": ["lobby_desk", "elevator"]},
        100.0),
    ("1F", "リビングルーム", "room", "Zone_LivingRoom_1F",
        {"floor": {"type": "hardwood", "color": "#C8A882"},
         "wall":  {"type": "paint",    "color": "#FFFFFF"},
         "ceiling": {"type": "plaster"}, "fixtures": ["lighting"]},
        100.0),
    ("1F", "バスルーム", "room", "Zone_Bathroom_1F",
        {"floor": {"type": "tile",  "color": "#E0E0E0"},
         "wall":  {"type": "tile",  "color": "#FFFFFF"},
         "ceiling": {"type": "plaster"}, "fixtures": ["toilet", "basin", "shower"]},
        100.0),
    ("2F", "スラブ工事", "structural", "Structure_Slab_2F",
        {"floor": {"type": "concrete", "color": "#AAAAAA"},
         "wall":  {"type": "concrete", "color": "#AAAAAA"},
         "ceiling": {"type": "concrete"}, "fixtures": []},
        100.0),
    ("2F", "寝室A", "room", "Zone_BedroomA_2F",
        {"floor": {"type": "hardwood", "color": "#C8A882"},
         "wall":  {"type": "paint",    "color": "#F0EDE8"},
         "ceiling": {"type": "plaster"}, "fixtures": ["lighting", "ac_unit"]},
        100.0),
    ("2F", "寝室B", "room", "Zone_BedroomB_2F",
        {"floor": {"type": "hardwood", "color": "#C8A882"},
         "wall":  {"type": "paint",    "color": "#F0EDE8"},
         "ceiling": {"type": "plaster"}, "fixtures": ["lighting", "ac_unit"]},
        100.0),
    ("3F", "スラブ工事", "structural", "Structure_Slab_3F",
        {"floor": {"type": "concrete", "color": "#AAAAAA"},
         "wall":  {"type": "concrete", "color": "#AAAAAA"},
         "ceiling": {"type": "concrete"}, "fixtures": []},
        100.0),
    ("3F", "マスタースイート", "room", "Zone_MasterSuite_3F",
        {"floor": {"type": "hardwood", "color": "#8B6914"},
         "wall":  {"type": "paint",    "color": "#EDE8E0"},
         "ceiling": {"type": "plaster"}, "fixtures": ["lighting", "ac_unit", "walk_in_closet"]},
        90.0),
    ("3F", "テラス", "open_area", "Zone_Terrace_3F",
        {"floor": {"type": "tile",     "color": "#D4C4A0"},
         "wall":  {"type": "concrete", "color": "#CCCCCC"},
         "ceiling": {"type": "open"}, "fixtures": ["railing"]},
        80.0),
    ("4F", "スラブ工事", "structural", "Structure_Slab_4F",
        {"floor": {"type": "concrete", "color": "#AAAAAA"},
         "wall":  {"type": "concrete", "color": "#AAAAAA"},
         "ceiling": {"type": "concrete"}, "fixtures": []},
        70.0),
    ("5F", "スラブ工事", "structural", "Structure_Slab_5F",
        {"floor": {"type": "concrete", "color": "#AAAAAA"},
         "wall":  {"type": "concrete", "color": "#AAAAAA"},
         "ceiling": {"type": "concrete"}, "fixtures": []},
        50.0),
    ("RF", "屋上防水", "roof", "Structure_Slab_Roof",
        {"floor": {"type": "waterproof_membrane", "color": "#555555"},
         "wall":  {"type": "concrete",            "color": "#888888"},
         "ceiling": {"type": "open"}, "fixtures": ["parapet"]},
        40.0),
]
# Weighted overall (58 total tasks across these 13 zones) ≈ 90.17%

REPORT_NOTES = {
    100.0: "工事完了。全工程問題なし。検査済み。",
    90.0:  "内装工事90%完了。仕上げ塗装残りわずか。",
    80.0:  "防水・タイル工事80%完了。ガラス工事進行中。",
    70.0:  "鉄筋コンクリートスラブ施工中。70%完了。",
    50.0:  "鉄筋組み立て中。スラブ工事50%進捗。",
    40.0:  "防水工事施工中。屋上防水40%完了。",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg):  print(f"  {msg}")
def ok(msg):   print(f"  ✓ {msg}")
def fail(msg): print(f"  ✗ {msg}"); sys.exit(1)


def get_sample_photo():
    sample = Path("sample_photo.jpg")
    if sample.exists():
        ok(f"Using {sample}")
        return sample.read_bytes()
    log("No sample_photo.jpg — generating placeholder")
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (800, 600), color=(45, 55, 72))
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 750, 550], outline=(100, 150, 200), width=3)
        draw.text((250, 280), "Construction Site Photo", fill=(200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except ImportError:
        return base64.b64decode(
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDB"
            "kSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAAR"
            "CAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAA"
            "AAAAAAAAAAAAAP/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAA"
            "AAAAAAAA/9oADAMBAAIRAxEAPwCwABmX/9k="
        )


def register_organization_and_login(client, admin_user, org_name):
    """First (and only) call to /auth/register — creates the Organization
    plus its owner/admin in one shot. If the email already exists (e.g.
    re-running this script against a non-fresh DB), just log in instead.
    """
    body = {**admin_user, "password": PASSWORD, "organization_name": org_name}
    r = client.post(f"{BASE_URL}/api/v1/auth/register", json=body)
    if r.status_code == 409:
        log(f"{admin_user['email']} already exists — logging in instead")
    elif r.status_code not in (200, 201):
        fail(f"Register organization: {r.status_code} {r.text}")
    else:
        return r.json()["access_token"]

    r = client.post(f"{BASE_URL}/api/v1/auth/login",
                     json={"email": admin_user["email"], "password": PASSWORD})
    if r.status_code != 200:
        fail(f"Login {admin_user['email']}: {r.status_code} {r.text}")
    return r.json()["access_token"]


def create_teammate_and_login(client, admin_token, member):
    """Admin creates a teammate via POST /organizations/members.

    Requires settings.debug=True on the backend — that's what makes the
    response include temp_password. Without it this call still creates
    the user, but the script has no way to know their password (it only
    goes out by email), so it fails loudly with instructions instead of
    silently producing an unusable account.
    """
    payload = {"email": member["email"], "full_name": member["full_name"], "role": member["role"]}
    r = client.post(f"{BASE_URL}/api/v1/organizations/members", json=payload, headers=h(admin_token))

    if r.status_code == 409:
        log(f"{member['email']} already exists — logging in with the seed default password")
        r = client.post(f"{BASE_URL}/api/v1/auth/login",
                         json={"email": member["email"], "password": PASSWORD})
        if r.status_code != 200:
            fail(
                f"{member['email']} already exists but the seed password doesn't match "
                f"(expected — real temp passwords are random). Delete the user or the DB "
                f"and re-run this script for a clean seed."
            )
        return r.json()["access_token"]

    if r.status_code not in (200, 201):
        fail(f"Create teammate {member['email']}: {r.status_code} {r.text}")

    data = r.json()
    temp_password = data.get("temp_password")
    if not temp_password:
        fail(
            f"Created {member['email']} but the response had no temp_password. "
            f"Set `debug: bool = True` in app/config.py (and restart the API) so "
            f"POST /organizations/members returns it for local seeding — in "
            f"production this field is always null, the password only goes out "
            f"by email."
        )

    r = client.post(f"{BASE_URL}/api/v1/auth/login",
                     json={"email": member["email"], "password": temp_password})
    if r.status_code != 200:
        fail(f"Login {member['email']} with temp password: {r.status_code} {r.text}")
    return r.json()["access_token"], temp_password


def h(token): return {"Authorization": f"Bearer {token}"}

# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------

def seed():
    print("\n🌱 Construction Platform — Test Data Seeder")
    print("=" * 52)

    photo = get_sample_photo()
    printed_credentials = [("admin", ADMIN_USER["email"], PASSWORD)]

    with httpx.Client(timeout=30.0) as client:

        # Health check
        print("\n[0] Health check")
        r = client.get(f"{BASE_URL}/health")
        if r.status_code != 200 or r.json().get("status") != "ok":
            fail(f"API not healthy: {r.text}")
        ok("API healthy")

        # Organization + admin
        print("\n[1] Organization + admin")
        admin_token = register_organization_and_login(client, ADMIN_USER, ORGANIZATION_NAME)
        ok(f"{ORGANIZATION_NAME} — admin: {ADMIN_USER['email']}")
        ah = h(admin_token)

        # Teammates — created BY the admin, not self-registered
        print("\n[2] Team members")
        tokens = {"admin": admin_token}
        for member in TEAMMATES:
            result = create_teammate_and_login(client, admin_token, member)
            token, temp_password = result if isinstance(result, tuple) else (result, PASSWORD)
            tokens[member["role"]] = token
            printed_credentials.append((member["role"], member["email"], temp_password))
            ok(f"{member['role']:10} {member['email']}")
        mh = h(tokens["manager"])
        eh = h(tokens["engineer"])

        # Task templates (global library — shared across all organizations)
        print("\n[3] Task templates")
        tmap = {}
        for tmpl in TASK_TEMPLATES:
            r = client.post(f"{BASE_URL}/api/v1/task-templates", json=tmpl, headers=ah)
            if r.status_code in (200, 201):
                tmap[tmpl["name"]] = r.json()["id"]
                ok(tmpl["name"])
            else:
                r2 = client.get(f"{BASE_URL}/api/v1/task-templates", headers=ah)
                for t in r2.json():
                    if t["name"] == tmpl["name"]:
                        tmap[tmpl["name"]] = t["id"]
                ok(f"{tmpl['name']} (existed)")

        # Project — organization_id is set server-side from the admin's own org
        print("\n[4] Project")
        r = client.post(f"{BASE_URL}/api/v1/projects", json=PROJECT, headers=ah)
        if r.status_code not in (200, 201):
            fail(f"Project: {r.status_code} {r.text}")
        pid = r.json()["id"]
        ok(f"{PROJECT['name']} ({pid})")

        for role, email in [(m["role"], m["email"]) for m in TEAMMATES]:
            r = client.post(f"{BASE_URL}/api/v1/projects/{pid}/members",
                             json={"email": email, "role": role}, headers=ah)
            if r.status_code in (200, 201):
                ok(f"  Member: {email} as {role}")

        # Model — skeleton uploads through the existing endpoint.
        # Envelope/interior have no upload endpoint yet (see note below).
        print("\n[5] 3D model — skeleton")
        if SKELETON_GLB_PATH.exists():
            with open(SKELETON_GLB_PATH, "rb") as f:
                r = client.post(
                    f"{BASE_URL}/api/v1/projects/{pid}/model/upload",
                    files={"file": ("skeleton.glb", f, "model/gltf-binary")},
                    headers=ah,
                )
            if r.status_code in (200, 201, 202):
                ok("skeleton.glb uploaded")
            else:
                log(f"skeleton.glb upload failed: {r.status_code} {r.text}")
        else:
            log(f"skeleton.glb not found at {SKELETON_GLB_PATH} — skipping upload")

        # Floors
        print("\n[6] Floors")
        fmap = {}
        for name, level, order in FLOORS:
            r = client.post(f"{BASE_URL}/api/v1/projects/{pid}/floors",
                json={"name": name, "level_number": level, "display_order": order}, headers=ah)
            if r.status_code not in (200, 201): fail(f"Floor {name}: {r.status_code}")
            fmap[name] = r.json()["id"]
            ok(f"{name}")

        # Zones + tasks + reports + approvals
        print("\n[7] Zones, tasks, reports, approvals")
        for (fname, zname, ztype, mesh_id, finish_data, pct) in ZONES:
            fid = fmap[fname]

            r = client.post(f"{BASE_URL}/api/v1/floors/{fid}/zones",
                json={"name": zname, "zone_type": ztype, "model_mesh_id": mesh_id, "finish_data": finish_data},
                headers=ah)
            if r.status_code not in (200, 201): fail(f"Zone {zname}: {r.status_code} {r.text}")
            zid = r.json()["id"]

            task_sequence = ZONE_TASK_SEQUENCES.get(mesh_id, [])
            zt_ids = []
            for tn, layer_order in task_sequence:
                tid = tmap.get(tn)
                if not tid:
                    log(f"    Template '{tn}' not found, skipping")
                    continue
                r = client.post(f"{BASE_URL}/api/v1/zones/{zid}/tasks",
                    json={"task_template_id": tid, "layer_order": layer_order}, headers=ah)
                if r.status_code in (200, 201):
                    zt_ids.append((tn, r.json()["id"], layer_order))

            if not zt_ids:
                ok(f"{fname} / {zname} [{mesh_id}] — no tasks assigned, skipping report")
                continue

            # IMPORTANT: approving a report only updates the ONE zone_task
            # it was submitted against — it does not cascade to sibling
            # tasks in the same zone. So to land every task in this zone
            # at `pct`, we submit + approve a report for EACH task, not
            # just the first. (Confirmed by working backwards from a real
            # seed run: floor percentages exactly matched "only task #1
            # per zone updated, rest stayed at 0" — e.g. 基礎 came out to
            # 25.0% = 100/4, not 100%.)
            note = REPORT_NOTES.get(pct, "工事進捗報告。")
            approved = 0
            for task_name, zt_id, layer_order in zt_ids:
                r = client.post(f"{BASE_URL}/api/v1/reports",
                    data={"data": json.dumps({"zone_task_id": zt_id, "note": note,
                        "engineer_progress_pct": pct, "geo_lat": 35.6584, "geo_lng": 139.7454})},
                    files={"photos": ("site_photo.jpg", photo, "image/jpeg")},
                    headers=eh)
                if r.status_code not in (200, 201):
                    continue
                rid = r.json()["id"]

                time.sleep(0.3)

                r2 = client.post(f"{BASE_URL}/api/v1/reports/{rid}/approval",
                    json={"action": "approved", "comment": "シード承認", "final_pct": pct},
                    headers=mh)
                if r2.status_code in (200, 201):
                    approved += 1

            ok(f"{fname} / {zname} [{mesh_id}] — {approved}/{len(zt_ids)} tasks approved at {pct:.0f}%")

        # Final progress
        print("\n[8] Final progress")
        r = client.get(f"{BASE_URL}/api/v1/projects/{pid}/progress", headers=ah)
        if r.status_code == 200:
            data = r.json()
            print(f"\n  Overall: {data['overall_pct']:.1f}%")
            for floor in data["floors"]:
                bar = "█" * int(floor["pct"] / 5) + "░" * (20 - int(floor["pct"] / 5))
                print(f"  {floor['name']:6} [{bar}] {floor['pct']:.1f}%")

        print("\n" + "=" * 52)
        print("✅ Seed complete!\n")
        print(f"Organization: {ORGANIZATION_NAME}")
        print("\nCredentials:")
        for role, email, pwd in printed_credentials:
            print(f"  {role:10} {email:24} {pwd}")
        print(f"\nProject ID: {pid}")

        print("\n📦 3D model layers:")
        print(f"  skeleton.glb — uploaded via API above")
        if not ENVELOPE_GLB_PATH.exists() or not INTERIOR_GLB_PATH.exists():
            print(f"  envelope.glb / interior.glb — not found next to this script, skipping instructions")
        else:
            print(
                "  envelope.glb / interior.glb — no upload endpoint exists for these yet\n"
                "  (that's CURRENT_STATE.md Step 3, not built). For now, copy them\n"
                "  directly into local storage on the API container:\n\n"
                f"    docker cp envelope.glb <api-container-name>:/tmp/construction_local_storage/projects/{pid}/model/envelope.glb\n"
                f"    docker cp interior.glb <api-container-name>:/tmp/construction_local_storage/projects/{pid}/model/interior.glb\n\n"
                "  Find <api-container-name> via `docker ps` (likely something like\n"
                "  construction-backend-api-1). These paths match what ViewerEmbed.tsx\n"
                "  builds as the env/int URL params."
            )


if __name__ == "__main__":
    try:
        import httpx
    except ImportError:
        print("Run: pip install httpx"); sys.exit(1)
    seed()