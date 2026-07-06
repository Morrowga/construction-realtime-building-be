#!/usr/bin/env python3
# generate_interior.py — Sakura Residence — produces interior.glb
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
# generate_interior.py — interior room surfaces -> interior.glb
# Floor / wall / ceiling planes per zone. No furniture.
# ============================================================================

EXPECTED = [
    "Zone_Lobby_1F",
    "Zone_LivingRoom_1F",
    "Zone_Bathroom_1F",
    "Zone_Slab_2F",
    "Zone_BedroomA_2F",
    "Zone_BedroomB_2F",
    "Zone_Slab_3F",
    "Zone_MasterSuite_3F",
    "Zone_Terrace_3F",
    "Zone_Slab_4F",
    "Zone_Slab_5F",
    "Zone_Roof_RF",
]

PLASTER = hex_to_rgb("#F8F8F8")


def add_room(mesh, x0, x1, z0, z1, y0, y1, floor_rgb, wall_rgb, ceil_rgb,
             walls=True, ceiling=True):
    """One room = floor + (optional) 4 inward-facing walls + (optional)
    ceiling, all merged into the given mesh. Surfaces are inset slightly so
    adjacent rooms and exterior walls never z-fight."""
    ins = 0.03                       # lateral inset per room
    xa, xb = x0 + ins, x1 - ins
    za, zb = z0 + ins, z1 - ins
    ya, yb = y0 + 0.05, y1 - 0.05    # finished floor / ceiling levels

    q = mesh.add_quad
    # floor (normal up)
    q((xa, ya, za), (xa, ya, zb), (xb, ya, zb), (xb, ya, za), floor_rgb)
    if ceiling:                      # ceiling (normal down)
        q((xa, yb, za), (xb, yb, za), (xb, yb, zb), (xa, yb, zb), ceil_rgb)
    if walls:                        # 4 walls, normals facing into the room
        q((xa, ya, za), (xb, ya, za), (xb, yb, za), (xa, yb, za), wall_rgb)
        q((xb, ya, zb), (xa, ya, zb), (xa, yb, zb), (xb, yb, zb), wall_rgb)
        q((xa, ya, zb), (xa, ya, za), (xa, yb, za), (xa, yb, zb), wall_rgb)
        q((xb, ya, za), (xb, ya, zb), (xb, yb, zb), (xb, yb, za), wall_rgb)


def add_slab(mesh, level, rgb):
    """Exposed structural slab surface over the full footprint (thin plate)."""
    mesh.add_box(0.0, level - 0.01, 0.0, BLD_W, level + 0.01, BLD_D, rgb)


def main():
    meshes = []

    def zone(name):
        m = Mesh(name, double_sided=True)
        meshes.append(m)
        return m

    # ------------------------- 1F rooms (y = 0 .. 4.0) ----------------------
    add_room(zone("Zone_Lobby_1F"), 0.0, 5.0, 0.0, 10.0, 0.0, 4.0,
             floor_rgb=hex_to_rgb("#F5F0E8"),        # marble tile
             wall_rgb=hex_to_rgb("#FAFAFA"),         # white plaster
             ceil_rgb=PLASTER)
    add_room(zone("Zone_LivingRoom_1F"), 5.0, 9.0, 0.0, 10.0, 0.0, 4.0,
             floor_rgb=hex_to_rgb("#C8A882"),        # hardwood
             wall_rgb=hex_to_rgb("#FFFFFF"),         # white paint
             ceil_rgb=PLASTER)
    add_room(zone("Zone_Bathroom_1F"), 9.0, 12.0, 0.0, 10.0, 0.0, 4.0,
             floor_rgb=hex_to_rgb("#E8E8E8"),        # white tile
             wall_rgb=hex_to_rgb("#F5F5F5"),         # white tile
             ceil_rgb=PLASTER)

    # ------------------------- 2F (y = 4.0 .. 7.2) --------------------------
    add_slab(zone("Zone_Slab_2F"), 4.0, hex_to_rgb("#B0B0B0"))  # exposed conc.
    beige = hex_to_rgb("#F0EDE8")
    wood = hex_to_rgb("#C8A882")
    add_room(zone("Zone_BedroomA_2F"), 0.0, 6.0, 0.0, 10.0, 4.0, 7.2,
             floor_rgb=wood, wall_rgb=beige, ceil_rgb=PLASTER)
    add_room(zone("Zone_BedroomB_2F"), 6.0, 12.0, 0.0, 10.0, 4.0, 7.2,
             floor_rgb=wood, wall_rgb=beige, ceil_rgb=PLASTER)

    # ------------------------- 3F (y = 7.2 .. 10.4) -------------------------
    add_slab(zone("Zone_Slab_3F"), 7.2, hex_to_rgb("#B0B0B0"))
    add_room(zone("Zone_MasterSuite_3F"), 0.0, 8.0, 0.0, 10.0, 7.2, 10.4,
             floor_rgb=hex_to_rgb("#8B6914"),        # dark hardwood
             wall_rgb=hex_to_rgb("#EDE8E0"),         # warm off-white
             ceil_rgb=PLASTER)
    add_room(zone("Zone_Terrace_3F"), 8.0, 12.0, 0.0, 10.0, 7.2, 10.4,
             floor_rgb=hex_to_rgb("#D4C4A0"),        # outdoor tile
             wall_rgb=None, ceil_rgb=None,
             walls=False, ceiling=False)             # open terrace: floor only

    # -------------- 4F, 5F, RF — structural only, not yet finished ----------
    add_slab(zone("Zone_Slab_4F"), 10.4, hex_to_rgb("#AAAAAA"))
    add_slab(zone("Zone_Slab_5F"), 13.6, hex_to_rgb("#AAAAAA"))
    # RF: waterproof membrane on top of the roof slab (slab top = 20.3)
    add_slab(zone("Zone_Roof_RF"), ROOF_LEVEL + 0.31, hex_to_rgb("#606870"))

    validate_and_save(meshes, EXPECTED, "interior.glb")


if __name__ == "__main__":
    main()
