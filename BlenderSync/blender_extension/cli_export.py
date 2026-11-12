"""
Headless CLI export entry point for Unity's automatic background refresh (Requirement R1c & Section 4.2c):
Usage:
    blender -b <file.blend> --python <addon>/cli_export.py -- --out <snapshot.bsync> [--settings <settings.json>]
"""

from __future__ import annotations
import argparse
import json
import os
import sys

# Ensure package imports work when invoked directly via `blender -b --python cli_export.py`
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_THIS_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import bpy  # noqa: E402
from blender_extension.visibility import VisibilitySettings  # noqa: E402
from blender_extension.scene_walker import SceneSyncExtractor  # noqa: E402
from blender_extension.transport import write_snapshot_file  # noqa: E402


def main() -> int:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []

    parser = argparse.ArgumentParser(description="BlenderSync Headless Snapshot Exporter")
    parser.add_argument("--out", required=True, help="Output .bsync snapshot file path")
    parser.add_argument("--settings", required=False, default="", help="Optional JSON settings path")
    args = parser.parse_args(argv)

    cfg: dict = {}
    if args.settings and os.path.isfile(args.settings):
        with open(args.settings, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    vis = VisibilitySettings(
        filter_mode=cfg.get("filter_mode", "ALL_VISIBLE"),
        include_wire_bounds=bool(cfg.get("include_wire_bounds", False)),
        respect_hide_render=bool(cfg.get("respect_hide_render", False)),
        include_lights=bool(cfg.get("include_lights", False)),
        include_cameras=bool(cfg.get("include_cameras", False)),
        include_empties=bool(cfg.get("include_empties", False)),
    )

    extractor = SceneSyncExtractor()
    frames, stats = extractor.extract_sync_frames(
        context=bpy.context,
        vis_settings=vis,
        is_full_sync=True,
        apply_unit_scale=bool(cfg.get("apply_unit_scale", True)),
        enable_chunking=bool(cfg.get("enable_chunking", True)),
        chunk_tri_threshold=int(cfg.get("chunk_tri_threshold", 250_000)),
        chunk_cell_size=float(cfg.get("chunk_cell_size", 64.0)),
        compress_buffers=bool(cfg.get("compress_buffers", False)),
    )

    total_bytes = write_snapshot_file(args.out, frames)
    print(
        f"[BlenderSync CLI] Wrote {total_bytes} bytes to '{args.out}' "
        f"(objects={stats['objects']}, tris={stats['triangles']}, instances={stats['instances']}, "
        f"time={stats['elapsed_ms']:.1f}ms)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
