#!/usr/bin/env python3
# generate_skeleton.py — Sakura Residence — produces skeleton.glb
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
# generate_skeleton.py — raw structural skeleton -> skeleton.glb
# Visual style: raw concrete construction site, grey tones, no finishes.
# ============================================================================

EXPECTED = [
    "Site_Ground",
    "Structure_Foundation",
    "Structure_Columns",
    "Structure_Beams",
    "Structure_Slab_1F",
    "Structure_Slab_2F",
    "Structure_Slab_3F",
    "Structure_Slab_4F",
    "Structure_Slab_5F",
    "Structure_Slab_Roof",
    "Structure_Core",
    "Structure_Stairs",
]


def main():
    meshes = []
    cx, cz = BLD_W / 2.0, BLD_D / 2.0   # building centre (6, 5)

    # --- Site_Ground: 40m x 40m plane, 0.1m thick, centred under building ---
    m = Mesh("Site_Ground")
    m.add_box(cx - 20.0, -0.1, cz - 20.0, cx + 20.0, 0.0, cz + 20.0,
              hex_to_rgb("#2A2E38"))
    meshes.append(m)

    # --- Structure_Foundation: 12 x 10 x 1.5 pad below grade ---
    m = Mesh("Structure_Foundation")
    m.add_box(0.0, -1.5, 0.0, BLD_W, 0.0, BLD_D, hex_to_rgb("#555566"))
    meshes.append(m)

    # --- Structure_Columns: 8 columns, 0.4 x 0.4, corners + mid-spans (X) ---
    m = Mesh("Structure_Columns")
    col_rgb = hex_to_rgb("#666677")
    col_x = [0.0, 4.0, 7.6, BLD_W - 0.4]          # 4 positions along long axis
    col_z = [0.0, BLD_D - 0.4]                     # front + rear rows
    for x in col_x:
        for z in col_z:
            m.add_box(x, 0.0, z, x + 0.4, ROOF_LEVEL, z + 0.4, col_rgb)
    meshes.append(m)

    # --- Structure_Beams: perimeter + centre beams below every slab level ---
    m = Mesh("Structure_Beams")
    beam_rgb = hex_to_rgb("#666677")
    bw, bd = 0.3, 0.4                              # 0.3 wide x 0.4 deep
    for lvl in SLAB_LEVELS + [ROOF_LEVEL]:
        y0, y1 = lvl - bd, lvl
        # beams running along X (front, centre, rear)
        for z in (0.0, cz - bw / 2, BLD_D - bw):
            m.add_box(0.0, y0, z, BLD_W, y1, z + bw, beam_rgb)
        # beams running along Z (left, centre, right)
        for x in (0.0, cx - bw / 2, BLD_W - bw):
            m.add_box(x, y0, 0.0, x + bw, y1, BLD_D, beam_rgb)
    meshes.append(m)

    # --- Structure_Slab_1F .. 5F: full footprint, 0.25m thick ---
    slab_rgb = hex_to_rgb("#707080")
    for i, lvl in enumerate(SLAB_LEVELS, start=1):
        m = Mesh(f"Structure_Slab_{i}F")
        m.add_box(0.0, lvl, 0.0, BLD_W, lvl + 0.25, BLD_D, slab_rgb)
        meshes.append(m)

    # --- Structure_Slab_Roof: 0.3m thick with 0.2m overhang each side ---
    m = Mesh("Structure_Slab_Roof")
    m.add_box(-0.2, ROOF_LEVEL, -0.2, BLD_W + 0.2, ROOF_LEVEL + 0.3,
              BLD_D + 0.2, hex_to_rgb("#505060"))
    meshes.append(m)

    # --- Structure_Core: 2.5 x 2.5 shaft, centred X, rear quarter Z ---
    m = Mesh("Structure_Core")
    core_x0, core_x1 = cx - 1.25, cx + 1.25
    core_z0, core_z1 = 6.25, 8.75
    m.add_box(core_x0, 0.0, core_z0, core_x1, ROOF_LEVEL, core_z1,
              hex_to_rgb("#606070"))
    meshes.append(m)

    # --- Structure_Stairs: two 2 x 3 stairwell volumes flanking the core ---
    m = Mesh("Structure_Stairs")
    stair_rgb = hex_to_rgb("#606070")
    m.add_box(core_x0 - 2.3, 0.0, 6.0, core_x0 - 0.3, ROOF_LEVEL, 9.0, stair_rgb)
    m.add_box(core_x1 + 0.3, 0.0, 6.0, core_x1 + 2.3, ROOF_LEVEL, 9.0, stair_rgb)
    meshes.append(m)

    validate_and_save(meshes, EXPECTED, "skeleton.glb")


if __name__ == "__main__":
    main()
