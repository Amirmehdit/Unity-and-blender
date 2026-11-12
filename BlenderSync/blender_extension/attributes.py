"""
Color attributes and Geometry Nodes custom attributes extraction & packing (Section 5.4).

Rules:
- Blender's Python API returns BOTH BYTE_COLOR and FLOAT_COLOR in scene-linear space
  via `attr.data.foreach_get("color", ...)`. Never apply sRGB conversion.
- Active/render color attribute goes to Unity's COLOR vertex channel.
- Additional color attributes and user named attributes (FLOAT, FLOAT2, FLOAT_VECTOR,
  FLOAT_COLOR, BYTE_COLOR, INT, INT8, BOOLEAN) on POINT, CORNER, or FACE domains are
  expanded to corners and packed into float4 channels TEXCOORD2..TEXCOORD7.
- TEXCOORD2 and TEXCOORD3 are allocated first so Unity Shader Graph's UV node (UV0..UV3)
  can read the highest-priority custom attributes directly.
"""

from __future__ import annotations
import json
import numpy as np
from typing import Any
from .coords import convert_directions_inplace

INTERNAL_ATTR_PREFIXES = (".",)
INTERNAL_ATTR_NAMES = {
    "position",
    "normal",
    "sharp_face",
    "sharp_edge",
    "material_index",
    "crease_edge",
    "crease_vert",
    "bevel_weight_edge",
    "bevel_weight_vert",
    "uv_seam",
    "id",
}

SUPPORTED_DATA_TYPES = {
    "FLOAT": (1, "value", np.float32),
    "INT": (1, "value", np.int32),
    "INT8": (1, "value", np.int32),
    "BOOLEAN": (1, "value", np.bool_),
    "FLOAT2": (2, "vector", np.float32),
    "FLOAT_VECTOR": (3, "vector", np.float32),
    "FLOAT_COLOR": (4, "color", np.float32),
    "BYTE_COLOR": (4, "color", np.float32),
}


def extract_attribute_to_corners(
    mesh: Any,
    attr: Any,
    num_loops: int,
    loop_vertex_indices: np.ndarray,
    polygon_loop_starts: np.ndarray | None,
    polygon_loop_totals: np.ndarray | None,
    convert_vectors_to_unity: bool = False,
) -> tuple[np.ndarray | None, str | None]:
    """
    Read a Blender Mesh Attribute on POINT, CORNER, or FACE domain and expand it
    to a (num_loops, K) float32 array.
    Returns (corner_array, warning_msg).
    """
    data_type = getattr(attr, "data_type", "")
    domain = getattr(attr, "domain", "")
    if data_type not in SUPPORTED_DATA_TYPES:
        return None, None

    comp_count, prop_name, raw_dtype = SUPPORTED_DATA_TYPES[data_type]

    if domain == "EDGE":
        # Average edge attribute to points if possible, or warn
        num_edges = len(mesh.edges)
        if num_edges == 0:
            return None, f"Skipped empty EDGE attribute '{attr.name}'"
        edge_verts = np.empty(num_edges * 2, dtype=np.int32)
        mesh.edges.foreach_get("vertices", edge_verts)
        edge_verts = edge_verts.reshape((num_edges, 2))
        raw_vals = np.empty(num_edges * comp_count, dtype=raw_dtype)
        attr.data.foreach_get(prop_name, raw_vals)
        vals = raw_vals.astype(np.float32).reshape((num_edges, comp_count))

        num_verts = len(mesh.vertices)
        vert_accum = np.zeros((num_verts, comp_count), dtype=np.float32)
        vert_counts = np.zeros((num_verts, 1), dtype=np.float32)
        np.add.at(vert_accum, edge_verts[:, 0], vals)
        np.add.at(vert_accum, edge_verts[:, 1], vals)
        np.add.at(vert_counts, edge_verts[:, 0], 1.0)
        np.add.at(vert_counts, edge_verts[:, 1], 1.0)
        vert_vals = vert_accum / np.maximum(vert_counts, 1.0)
        out = vert_vals[loop_vertex_indices]
    elif domain == "POINT":
        num_verts = len(mesh.vertices)
        raw_vals = np.empty(num_verts * comp_count, dtype=raw_dtype)
        attr.data.foreach_get(prop_name, raw_vals)
        vert_vals = raw_vals.astype(np.float32).reshape((num_verts, comp_count))
        out = vert_vals[loop_vertex_indices]
    elif domain == "CORNER":
        raw_vals = np.empty(num_loops * comp_count, dtype=raw_dtype)
        attr.data.foreach_get(prop_name, raw_vals)
        out = raw_vals.astype(np.float32).reshape((num_loops, comp_count))
    elif domain == "FACE":
        num_polys = len(mesh.polygons)
        raw_vals = np.empty(num_polys * comp_count, dtype=raw_dtype)
        attr.data.foreach_get(prop_name, raw_vals)
        face_vals = raw_vals.astype(np.float32).reshape((num_polys, comp_count))
        if polygon_loop_totals is not None:
            out = np.repeat(face_vals, polygon_loop_totals, axis=0)
        else:
            out = np.zeros((num_loops, comp_count), dtype=np.float32)
    else:
        return None, f"Unsupported domain '{domain}' for attribute '{attr.name}'"

    if convert_vectors_to_unity and data_type == "FLOAT_VECTOR" and comp_count == 3:
        convert_directions_inplace(out)

    return out, None


class AttributeMappingManager:
    """
    Manages the deterministic allocation of color attributes and Geometry Nodes custom
    attributes into COLOR and TEXCOORD2..TEXCOORD7.
    """

    def __init__(self, user_overrides: dict[str, dict[str, Any]] | None = None) -> None:
        self.user_overrides = user_overrides or {}
        self.manifest: dict[str, dict[str, Any]] = {}

    def pack_mesh_attributes(
        self,
        mesh: Any,
        num_loops: int,
        loop_vertex_indices: np.ndarray,
        polygon_loop_starts: np.ndarray,
        polygon_loop_totals: np.ndarray,
        uv_map_names: set[str],
    ) -> tuple[np.ndarray | None, dict[int, np.ndarray], list[str]]:
        """
        Extract primary linear color attribute (for COLOR channel) and pack all remaining
        color attributes and custom attributes into TEXCOORD2..TEXCOORD7 (float4 per channel).

        Returns:
          - primary_color_corners: (num_loops, 4) float32 or None
          - packed_uv_channels: dict mapping channel_index (2..7) -> (num_loops, 4) float32
          - warnings: list of warning strings
        """
        warnings: list[str] = []
        primary_color: np.ndarray | None = None

        # 1. Identify primary color attribute from mesh.color_attributes
        color_attrs = getattr(mesh, "color_attributes", None)
        primary_color_name: str | None = None
        if color_attrs is not None and len(color_attrs) > 0:
            render_idx = getattr(color_attrs, "render_color_index", -1)
            if 0 <= render_idx < len(color_attrs):
                primary_color_name = color_attrs[render_idx].name
            elif getattr(color_attrs, "active_color_name", ""):
                primary_color_name = color_attrs.active_color_name
            else:
                primary_color_name = color_attrs[0].name

            primary_attr = mesh.attributes.get(primary_color_name)
            if primary_attr is not None:
                primary_color, warn = extract_attribute_to_corners(
                    mesh, primary_attr, num_loops, loop_vertex_indices,
                    polygon_loop_starts, polygon_loop_totals, False
                )
                if warn:
                    warnings.append(warn)
                if primary_color is not None:
                    self.manifest[primary_color_name] = {
                        "name": primary_color_name,
                        "data_type": primary_attr.data_type,
                        "domain": primary_attr.domain,
                        "target_semantic": "COLOR",
                        "uv_channel": -1,
                        "component_offset": 0,
                        "component_count": 4,
                    }

        # 2. Collect all remaining user attributes (including secondary color attributes)
        extra_attrs: list[tuple[str, Any, np.ndarray]] = []
        for attr in getattr(mesh, "attributes", ()):
            name = attr.name
            if not name or name.startswith(INTERNAL_ATTR_PREFIXES) or name in INTERNAL_ATTR_NAMES:
                continue
            if name in uv_map_names or name == primary_color_name:
                continue
            if getattr(attr, "is_internal", False):
                continue

            arr, warn = extract_attribute_to_corners(
                mesh, attr, num_loops, loop_vertex_indices,
                polygon_loop_starts, polygon_loop_totals, convert_vectors_to_unity=True
            )
            if warn:
                warnings.append(warn)
            if arr is not None:
                extra_attrs.append((name, attr, arr))

        # Sort deterministically: user overrides first, then vectors/colors, then scalars
        extra_attrs.sort(key=lambda item: (0 if item[0] in self.user_overrides else 1, -item[2].shape[1], item[0]))

        # Allocate into TEXCOORD2..TEXCOORD7 (channels 2..7, each has 4 float components)
        channel_buffers: dict[int, np.ndarray] = {}
        channel_used_comps: dict[int, int] = {ch: 0 for ch in range(2, 8)}

        for name, attr, arr in extra_attrs:
            comp_count = arr.shape[1]
            override = self.user_overrides.get(name)
            assigned_ch = None
            assigned_comp = 0

            if override and 2 <= int(override.get("uv_channel", 2)) <= 7:
                ch = int(override["uv_channel"])
                comp = int(override.get("component_offset", 0))
                if comp + comp_count <= 4:
                    assigned_ch = ch
                    assigned_comp = comp
                    channel_used_comps[ch] = max(channel_used_comps[ch], comp + comp_count)

            if assigned_ch is None:
                # Find first channel in 2..7 that has space for `comp_count` floats
                for ch in range(2, 8):
                    used = channel_used_comps[ch]
                    if used + comp_count <= 4:
                        assigned_ch = ch
                        assigned_comp = used
                        channel_used_comps[ch] = used + comp_count
                        break

            if assigned_ch is None:
                warnings.append(
                    f"Attribute '{name}' ({comp_count}D) skipped: all TEXCOORD2..TEXCOORD7 slots full"
                )
                continue

            if assigned_ch not in channel_buffers:
                channel_buffers[assigned_ch] = np.zeros((num_loops, 4), dtype=np.float32)

            # Optional remap range from user override
            if override and "remap_min" in override and "remap_max" in override:
                rmin = float(override["remap_min"])
                rmax = float(override["remap_max"])
                if abs(rmax - rmin) > 1e-7:
                    arr = (arr - rmin) / (rmax - rmin)

            channel_buffers[assigned_ch][:, assigned_comp : assigned_comp + comp_count] = arr
            self.manifest[name] = {
                "name": name,
                "data_type": attr.data_type,
                "domain": attr.domain,
                "target_semantic": f"TEXCOORD{assigned_ch}",
                "uv_channel": assigned_ch,
                "component_offset": assigned_comp,
                "component_count": comp_count,
            }

        return primary_color, channel_buffers, warnings

    def to_json(self) -> str:
        return json.dumps({"attributes": list(self.manifest.values())}, indent=2)
