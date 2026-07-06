#!/usr/bin/env python3
# generate_envelope.py — Sakura Residence — produces envelope.glb
# ============================================================================
# Embedded GLB helper library (GLTF 2.0 binary writer, numpy + struct only)
# ============================================================================
import struct
import json
import math
import numpy as np
from pathlib import Path


def hex_to_rgb(h):
    """'#RRGGBB' -> [r, g, b] floats in 0..1"""
    h = h.lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


class Mesh:
    """One logical zone = one Mesh = one merged glTF mesh with one primitive."""

    def __init__(self, name, blend=False, double_sided=False):
        self.name = name
        self.blend = blend
        self.double_sided = double_sided
        self.pos = []   # [x, y, z]
        self.nrm = []   # [nx, ny, nz]
        self.col = []   # [r, g, b, a]
        self.idx = []   # uint32 triangle indices

    def _raw_quad(self, a, b, c, d, n, rgb, alpha):
        base = len(self.pos)
        for p in (a, b, c, d):
            self.pos.append([float(p[0]), float(p[1]), float(p[2])])
            self.nrm.append([float(n[0]), float(n[1]), float(n[2])])
            self.col.append([rgb[0], rgb[1], rgb[2], alpha])
        self.idx.extend([base, base + 1, base + 2, base, base + 2, base + 3])

    def add_quad(self, a, b, c, d, rgb, alpha=1.0):
        """Quad a-b-c-d (CCW seen from the normal side). Normal auto-computed."""
        av = np.asarray(a, dtype=float)
        bv = np.asarray(b, dtype=float)
        cv = np.asarray(c, dtype=float)
        n = np.cross(bv - av, cv - av)
        ln = np.linalg.norm(n)
        n = (n / ln) if ln > 1e-12 else np.array([0.0, 1.0, 0.0])
        self._raw_quad(a, b, c, d, n.tolist(), rgb, alpha)

    def add_box(self, x0, y0, z0, x1, y1, z1, rgb, alpha=1.0):
        """Axis-aligned box with outward flat normals (24 verts, 12 tris)."""
        q = self.add_quad
        q((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0), rgb, alpha)  # +Y
        q((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1), rgb, alpha)  # -Y
        q((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), rgb, alpha)  # +Z
        q((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0), rgb, alpha)  # -Z
        q((x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1), rgb, alpha)  # +X
        q((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0), rgb, alpha)  # -X


def build_glb(meshes, generator="sakura-residence-glb-generator"):
    """Assemble a valid GLTF 2.0 binary from a list of Mesh objects.
    Returns (gltf_dict, glb_bytes)."""
    bin_chunks = []
    byte_len = 0
    buffer_views = []
    accessors = []
    materials = []
    gltf_meshes = []
    nodes = []

    def push(data, target):
        nonlocal byte_len
        pad = (4 - byte_len % 4) % 4
        if pad:
            bin_chunks.append(b"\x00" * pad)
            byte_len += pad
        buffer_views.append({
            "buffer": 0,
            "byteOffset": byte_len,
            "byteLength": len(data),
            "target": target,
        })
        bin_chunks.append(data)
        byte_len += len(data)
        return len(buffer_views) - 1

    for mi, m in enumerate(meshes):
        if not m.pos:
            raise RuntimeError(f"Mesh '{m.name}' has no geometry")
        pos = np.asarray(m.pos, dtype=np.float32)
        nrm = np.asarray(m.nrm, dtype=np.float32)
        col = np.asarray(m.col, dtype=np.float32)
        idx = np.asarray(m.idx, dtype=np.uint32)

        bv = push(pos.tobytes(), 34962)
        accessors.append({
            "bufferView": bv, "componentType": 5126, "count": int(len(pos)),
            "type": "VEC3",
            "min": [float(v) for v in pos.min(axis=0)],
            "max": [float(v) for v in pos.max(axis=0)],
        })
        acc_pos = len(accessors) - 1

        bv = push(nrm.tobytes(), 34962)
        accessors.append({
            "bufferView": bv, "componentType": 5126, "count": int(len(nrm)),
            "type": "VEC3",
        })
        acc_nrm = len(accessors) - 1

        bv = push(col.tobytes(), 34962)
        accessors.append({
            "bufferView": bv, "componentType": 5126, "count": int(len(col)),
            "type": "VEC4",
        })
        acc_col = len(accessors) - 1

        bv = push(idx.tobytes(), 34963)
        accessors.append({
            "bufferView": bv, "componentType": 5125, "count": int(len(idx)),
            "type": "SCALAR",
        })
        acc_idx = len(accessors) - 1

        mat = {
            "name": m.name + "_mat",
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
            "doubleSided": bool(m.double_sided),
        }
        if m.blend:
            mat["alphaMode"] = "BLEND"
        materials.append(mat)

        gltf_meshes.append({
            "name": m.name,
            "primitives": [{
                "attributes": {
                    "POSITION": acc_pos,
                    "NORMAL": acc_nrm,
                    "COLOR_0": acc_col,
                },
                "indices": acc_idx,
                "material": mi,
                "mode": 4,
            }],
        })
        nodes.append({"name": m.name, "mesh": mi})

    bin_blob = b"".join(bin_chunks)
    bin_pad = (4 - len(bin_blob) % 4) % 4
    bin_blob += b"\x00" * bin_pad

    gltf = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"name": "SakuraResidence", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_blob)}],
    }

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * json_pad

    total = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)
    glb = b"".join([
        struct.pack("<III", 0x46546C67, 2, total),          # 'glTF' header
        struct.pack("<II", len(json_bytes), 0x4E4F534A),    # JSON chunk
        json_bytes,
        struct.pack("<II", len(bin_blob), 0x004E4942),      # BIN chunk
        bin_blob,
    ])
    return gltf, glb


def validate_and_save(meshes, expected_names, filename):
    """Write the GLB, print mesh names, and validate against the expected list."""
    gltf, glb = build_glb(meshes)

    out = Path(filename)
    out.write_bytes(glb)
    print(f"Generated: {out.name}")
    print(f"Size: {len(glb)/1024:.1f} KB")

    # Validate mesh names
    EXPECTED_NAMES = list(expected_names)
    generated_names = [m["name"] for m in gltf["meshes"]]
    print(f"\nMesh names ({len(generated_names)}):")
    for name in generated_names:
        status = "\u2713" if name in EXPECTED_NAMES else "\u2717 UNEXPECTED"
        print(f"  {status} {name}")

    missing = set(EXPECTED_NAMES) - set(generated_names)
    unexpected = set(generated_names) - set(EXPECTED_NAMES)

    if missing:
        print("\nERROR: mesh name mismatch")
        print(f"Missing meshes: {missing}")
        exit(1)
    if unexpected:
        print("\nERROR: mesh name mismatch")
        print(f"Unexpected meshes: {unexpected}")
        exit(1)

    print("\n\u2705 All mesh names validated successfully")


# ---------------------------------------------------------------------------
# Shared building dimensions (from reference image / brief)
# ---------------------------------------------------------------------------
BLD_W = 12.0        # X — building width
BLD_D = 10.0        # Z — building depth
WALL_T = 0.35       # exterior wall thickness
GF_H = 4.0          # ground floor height (taller lobby)
FL_H = 3.2          # upper floor height
# Floor bands: (tag, y_bottom, y_top)
FLOORS = [
    ("1F", 0.0, 4.0),
    ("2F", 4.0, 7.2),
    ("3F", 7.2, 10.4),
    ("4F", 10.4, 13.6),
    ("5F", 13.6, 16.8),
    ("RF", 16.8, 20.0),
]
SLAB_LEVELS = [4.0, 7.2, 10.4, 13.6, 16.8]   # tops of 1F..5F
ROOF_LEVEL = 20.0

# ============================================================================
# generate_envelope.py — exterior building shell -> envelope.glb
# Matches reference image: light stone ground floor, warm brick upper floors,
# stone-white window frames (arched on 2F-4F), cornice ledges, parapet roof.
# ============================================================================

EXPECTED = [
    "Envelope_Facade_1F", "Envelope_Facade_2F", "Envelope_Facade_3F",
    "Envelope_Facade_4F", "Envelope_Facade_5F", "Envelope_Facade_RF",
    "Envelope_Windows_1F", "Envelope_Windows_2F", "Envelope_Windows_3F",
    "Envelope_Windows_4F", "Envelope_Windows_5F",
    "Envelope_Balcony_2F", "Envelope_Balcony_3F", "Envelope_Balcony_4F",
    "Envelope_Balcony_5F",
    "Envelope_Roof", "Envelope_Door_Main",
]

FRAME_RGB = hex_to_rgb("#F0E8D8")   # stone white frames
GLASS_RGB = hex_to_rgb("#B0C8D8")   # blue-grey glass
LEDGE_RGB = hex_to_rgb("#D8CFC0")   # cornice / balcony stone

FACADE_RGB = {
    "1F": hex_to_rgb("#E8E0D0"),    # light stone ground floor
    "2F": hex_to_rgb("#C0785A"),    # warm brick red
    "3F": hex_to_rgb("#C0785A"),
    "4F": hex_to_rgb("#C0785A"),
    "5F": hex_to_rgb("#C0785A"),
    "RF": hex_to_rgb("#D8CFC0"),    # lighter parapet band
}

# Wall shells (exterior faces at x/z = 0 and BLD_W/BLD_D)
FRONT = ("front", 0.0, WALL_T)                  # wall along X, z in [0, 0.35]
BACK = ("back", BLD_D - WALL_T, BLD_D)          # wall along X, z in [9.65, 10]
LEFT = ("left", 0.0, WALL_T)                    # wall along Z, x in [0, 0.35]
RIGHT = ("right", BLD_W - WALL_T, BLD_W)        # wall along Z, x in [11.65, 12]

# Window centres (from reference image: 3 across the front, 2 on each side)
FRONT_CENTRES = [2.4, 6.0, 9.6]
SIDE_CENTRES = [3.0, 7.0]
WIN_W, WIN_H = 1.4, 2.0
DOOR_U0, DOOR_U1, DOOR_H = 5.0, 7.0, 3.0


def add_wall_with_openings(mesh, side, u0, u1, y0, y1, openings, rgb):
    """Build one wall as merged boxes with rectangular holes cut out.
    side: one of FRONT/BACK/LEFT/RIGHT tuples. u = coordinate along the wall.
    openings: list of (u_lo, u_hi, y_lo, y_hi)."""
    name, t0, t1 = side
    along_x = name in ("front", "back")

    def seg(ua, ub, ya, yb):
        if ub - ua <= 1e-6 or yb - ya <= 1e-6:
            return
        if along_x:
            mesh.add_box(ua, ya, t0, ub, yb, t1, rgb)
        else:
            mesh.add_box(t0, ya, ua, t1, yb, ub, rgb)

    prev = u0
    for (a0, a1, b0, b1) in sorted(openings):
        seg(prev, a0, y0, y1)      # solid strip before the opening
        seg(a0, a1, y0, b0)        # spandrel below the opening
        seg(a0, a1, b1, y1)        # wall above the opening
        prev = a1
    seg(prev, u1, y0, y1)          # solid strip after the last opening


def _to3(side, u, v, d):
    """Map wall-local (u along wall, v vertical, d through wall) to world xyz."""
    name = side[0]
    if name in ("front", "back"):
        return (u, v, d)
    return (d, v, u)


def add_window(mesh, side, u0, u1, v0, v1, arched):
    """Frame (4 boxes) + glass plane + optional arch ring, on one wall side."""
    name = side[0]
    fw = 0.10                       # frame profile width
    # Frame protrudes 0.06 beyond the exterior face, embeds 0.05 into the wall
    if name in ("front", "left"):
        d0, d1 = -0.06, 0.05
        g0, g1 = 0.17, 0.18         # glass mid-wall
    else:
        ext = BLD_D if name == "back" else BLD_W
        d0, d1 = ext - 0.05, ext + 0.06
        g0, g1 = ext - 0.18, ext - 0.17

    def fbox(ua, ub, va, vb, da, db, rgb, alpha=1.0):
        if name in ("front", "back"):
            mesh.add_box(ua, va, da, ub, vb, db, rgb, alpha)
        else:
            mesh.add_box(da, va, ua, db, vb, ub, rgb, alpha)

    # Sill, head, jambs (outer frame, 0.06m proud of the wall)
    fbox(u0 - fw, u1 + fw, v0 - fw, v0, d0, d1, FRAME_RGB)          # sill
    fbox(u0 - fw, u1 + fw, v1, v1 + fw, d0, d1, FRAME_RGB)          # head
    fbox(u0 - fw, u0, v0, v1, d0, d1, FRAME_RGB)                    # left jamb
    fbox(u1, u1 + fw, v0, v1, d0, d1, FRAME_RGB)                    # right jamb
    # Central mullion (matches the divided sashes in the reference image)
    fbox((u0 + u1) / 2 - 0.03, (u0 + u1) / 2 + 0.03, v0, v1,
         d0 + 0.04, d1 - 0.04, FRAME_RGB)
    # Glass: thin 0.01m plane, slightly blue-grey, 60% opaque
    fbox(u0, u1, v0, v1, g0, g1, GLASS_RGB, 0.6)

    if arched:
        # Semicircular arch ring above the head trim (reference image style)
        uc, vb = (u0 + u1) / 2.0, v1 + fw
        r_in = (u1 - u0) / 2.0 + fw
        r_out = r_in + 0.12
        segs = 10
        for i in range(segs):
            a0 = math.pi * i / segs
            a1 = math.pi * (i + 1) / segs
            i0 = (uc + r_in * math.cos(a0), vb + r_in * math.sin(a0))
            o0 = (uc + r_out * math.cos(a0), vb + r_out * math.sin(a0))
            o1 = (uc + r_out * math.cos(a1), vb + r_out * math.sin(a1))
            i1 = (uc + r_in * math.cos(a1), vb + r_in * math.sin(a1))
            # front + back caps and outer + inner surfaces of the ring segment
            mesh.add_quad(_to3(side, *i0, d1), _to3(side, *o0, d1),
                          _to3(side, *o1, d1), _to3(side, *i1, d1), FRAME_RGB)
            mesh.add_quad(_to3(side, *i1, d0), _to3(side, *o1, d0),
                          _to3(side, *o0, d0), _to3(side, *i0, d0), FRAME_RGB)
            mesh.add_quad(_to3(side, *o0, d0), _to3(side, *o1, d0),
                          _to3(side, *o1, d1), _to3(side, *o0, d1), FRAME_RGB)
            mesh.add_quad(_to3(side, *i1, d0), _to3(side, *i0, d0),
                          _to3(side, *i0, d1), _to3(side, *i1, d1), FRAME_RGB)


def floor_plan(tag, yb, yt):
    """Return (facade_openings, window_openings) per wall side for one floor.
    facade_openings includes the entrance opening on 1F; window_openings
    only lists openings that receive frame geometry."""
    fac = {FRONT: [], BACK: [], LEFT: [], RIGHT: []}
    win = {FRONT: [], BACK: [], LEFT: [], RIGHT: []}

    if tag == "RF":
        # small unframed attic openings across the front (reference image top band)
        for c in FRONT_CENTRES:
            fac[FRONT].append((c - 0.45, c + 0.45, yb + 1.2, yb + 2.1))
        return fac, win

    sill = yb + (1.0 if tag == "1F" else 0.9)
    v0, v1 = sill, sill + WIN_H

    front_centres = [2.4, 9.6] if tag == "1F" else FRONT_CENTRES  # door replaces
    for c in front_centres:                                       # the middle bay
        op = (c - WIN_W / 2, c + WIN_W / 2, v0, v1)
        fac[FRONT].append(op)
        win[FRONT].append(op)
    for c in FRONT_CENTRES:
        op = (c - WIN_W / 2, c + WIN_W / 2, v0, v1)
        fac[BACK].append(op)
        win[BACK].append(op)
    for side in (LEFT, RIGHT):
        for c in SIDE_CENTRES:
            op = (c - WIN_W / 2, c + WIN_W / 2, v0, v1)
            fac[side].append(op)
            win[side].append(op)

    if tag == "1F":
        fac[FRONT].append((DOOR_U0, DOOR_U1, yb, yb + DOOR_H))    # entrance

    return fac, win


def add_cornice(mesh, y, proud=0.18, h=0.22):
    """Stone ledge wrapping the building perimeter (between-floor detail)."""
    mesh.add_box(-proud, y - h, -proud, BLD_W + proud, y, BLD_D + proud,
                 LEDGE_RGB)


def add_balcony(mesh, yb, rgb):
    """Balcony slab + vertical bar railing along the front facade."""
    slab_top = yb + 0.075
    mesh.add_box(0.0, yb - 0.075, -1.2, BLD_W, slab_top, 0.0, rgb)  # 0.15 slab
    bar, pitch, rail_h = 0.04, 0.19, 1.10                # bars 0.15m apart
    # front row of balusters
    x = 0.03
    while x + bar <= BLD_W - 0.03:
        mesh.add_box(x, slab_top, -1.18, x + bar, slab_top + rail_h, -1.14, rgb)
        x += pitch
    # side rows of balusters
    for xs in (0.03, BLD_W - 0.07):
        z = -1.05
        while z + bar <= -0.05:
            mesh.add_box(xs, slab_top, z, xs + bar, slab_top + rail_h,
                         z + bar, rgb)
            z += pitch
    # top rails: 0.06 x 0.06 horizontal bars
    h0, h1 = slab_top + rail_h, slab_top + rail_h + 0.06
    mesh.add_box(0.0, h0, -1.19, BLD_W, h1, -1.13, rgb)             # front
    mesh.add_box(0.0, h0, -1.13, 0.06, h1, 0.0, rgb)                # left
    mesh.add_box(BLD_W - 0.06, h0, -1.13, BLD_W, h1, 0.0, rgb)      # right


def main():
    meshes = []

    facade_meshes = {}
    window_meshes = {}

    for tag, yb, yt in FLOORS:
        fac, win = floor_plan(tag, yb, yt)
        rgb = FACADE_RGB[tag]

        # --- Envelope_Facade_<tag>: 4 exterior walls with real openings ---
        fm = Mesh(f"Envelope_Facade_{tag}")
        add_wall_with_openings(fm, FRONT, 0.0, BLD_W, yb, yt, fac[FRONT], rgb)
        add_wall_with_openings(fm, BACK, 0.0, BLD_W, yb, yt, fac[BACK], rgb)
        add_wall_with_openings(fm, LEFT, WALL_T, BLD_D - WALL_T, yb, yt,
                               fac[LEFT], rgb)
        add_wall_with_openings(fm, RIGHT, WALL_T, BLD_D - WALL_T, yb, yt,
                               fac[RIGHT], rgb)
        # cornice ledge at the top of every floor band (reference image)
        if tag != "RF":
            add_cornice(fm, yt)
        facade_meshes[tag] = fm

        # --- Envelope_Windows_<tag>: frames + glass (1F-5F only) ---
        if tag != "RF":
            wm = Mesh(f"Envelope_Windows_{tag}", blend=True, double_sided=True)
            arched = tag in ("2F", "3F", "4F")   # arched tops per reference
            for side, ops in win.items():
                for (a0, a1, b0, b1) in ops:
                    add_window(wm, side, a0, a1, b0, b1, arched)
            window_meshes[tag] = wm

    # Keep the expected output order: facades, then windows, then the rest
    for tag, _, _ in FLOORS:
        meshes.append(facade_meshes[tag])
    for tag in ("1F", "2F", "3F", "4F", "5F"):
        meshes.append(window_meshes[tag])

    # --- Envelope_Balcony_2F .. 5F: slab + railing at each floor line ---
    for tag, yb, _ in FLOORS:
        if tag in ("2F", "3F", "4F", "5F"):
            bm = Mesh(f"Envelope_Balcony_{tag}")
            add_balcony(bm, yb, LEDGE_RGB)
            meshes.append(bm)

    # --- Envelope_Roof: deck + 0.8m parapet + raised central section ---
    rm = Mesh("Envelope_Roof")
    roof_rgb = hex_to_rgb("#B8B0A0")
    deck_y = ROOF_LEVEL + 0.30                       # top of roof slab
    rm.add_box(0.0, deck_y, 0.0, BLD_W, deck_y + 0.05, BLD_D, roof_rgb)
    p, ph = 0.2, 0.8                                 # parapet thickness/height
    rm.add_box(-p, deck_y, -p, BLD_W + p, deck_y + ph, 0.0, roof_rgb)
    rm.add_box(-p, deck_y, BLD_D, BLD_W + p, deck_y + ph, BLD_D + p, roof_rgb)
    rm.add_box(-p, deck_y, 0.0, 0.0, deck_y + ph, BLD_D, roof_rgb)
    rm.add_box(BLD_W, deck_y, 0.0, BLD_W + p, deck_y + ph, BLD_D, roof_rgb)
    # central raised section matching the reference image top profile
    rm.add_box(3.5, deck_y, 2.5, 8.5, deck_y + 1.0, 7.5, roof_rgb)
    rm.add_box(3.2, deck_y + 1.0, 2.2, 8.8, deck_y + 1.15, 7.8, roof_rgb)
    meshes.append(rm)

    # --- Envelope_Door_Main: panelled dark-wood entrance door ---
    dm = Mesh("Envelope_Door_Main")
    door_rgb = hex_to_rgb("#5C4030")
    dm.add_box(DOOR_U0, 0.0, 0.28, DOOR_U1, DOOR_H, 0.33, door_rgb)  # leaf
    for px0, px1 in ((5.15, 5.90), (6.10, 6.85)):                    # raised
        for py0, py1 in ((0.25, 1.30), (1.50, 2.75)):                # panels
            dm.add_box(px0, py0, 0.26, px1, py1, 0.28, door_rgb)
    meshes.append(dm)

    validate_and_save(meshes, EXPECTED, "envelope.glb")


if __name__ == "__main__":
    main()
