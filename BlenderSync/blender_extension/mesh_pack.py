"""
Vectorized evaluated mesh extraction, corner welding, material submesh splitting,
and optional spatial grid chunking using NumPy (Section 5.3).
Zero per-vertex Python loops.
"""

from __future__ import annotations
import hashlib
import numpy as np
from typing import Any

from .coords import (
    convert_positions_inplace,
    convert_directions_inplace,
    convert_tangents_inplace,
    flip_triangle_winding_inplace,
    compute_aabb_unity,
)
from .attributes import AttributeMappingManager
from .protocol import VertexSemantic, VertexFormat


def _weld_corners_and_pack(
    corner_columns: list[tuple[int, int, int, np.ndarray]],
    tri_loop_indices: np.ndarray,
    tri_material_indices: np.ndarray,
    corner_positions_unity: np.ndarray,
) -> dict[str, Any]:
    """
    Given a list of per-corner attribute arrays `(semantic, format_enum, dim, array_2d)`,
    weld identical corners using a structured NumPy byte view, remap triangle indices,
    and split triangles into submeshes sorted by material slot.
    """
    num_loops = corner_positions_unity.shape[0]
    layout: list[dict[str, int]] = []
    byte_views: list[np.ndarray] = []
    offset = 0

    for semantic, fmt, dim, arr in corner_columns:
        arr_c = np.ascontiguousarray(arr)
        byte_width = arr_c.dtype.itemsize * dim
        byte_views.append(arr_c.view(np.uint8).reshape((num_loops, byte_width)))
        layout.append({
            "semantic": int(semantic),
            "format": int(fmt),
            "dimension": int(dim),
            "stream": 0,
            "offset": offset,
        })
        offset += byte_width

    vertex_stride = offset
    interleaved_bytes = np.hstack(byte_views)  # shape: (num_loops, vertex_stride) uint8

    # Vectorized deduplication of identical corner vertices via np.void structured view
    contiguous_bytes = np.ascontiguousarray(interleaved_bytes)
    void_view = contiguous_bytes.view(np.dtype((np.void, vertex_stride))).ravel()
    _, unique_first_indices, inverse_indices = np.unique(
        void_view, return_index=True, return_inverse=True
    )

    # Preserve original vertex discovery order for better vertex cache locality
    order = np.argsort(unique_first_indices)
    sorted_first_indices = unique_first_indices[order]
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order), dtype=order.dtype)
    welded_loop_to_vert = remap[inverse_indices].astype(np.uint32)

    welded_vertices_bytes = contiguous_bytes[sorted_first_indices]
    welded_positions = corner_positions_unity[sorted_first_indices]
    vertex_count = int(welded_vertices_bytes.shape[0])

    # Remap triangle corner indices to welded vertex indices
    remapped_tris = welded_loop_to_vert[tri_loop_indices]  # (num_tris, 3)

    # Group triangles by material slot into submeshes
    unique_mats = np.unique(tri_material_indices)
    submeshes: list[dict[str, Any]] = []
    ordered_tri_list: list[np.ndarray] = []
    index_cursor = 0

    for mat_slot in unique_mats:
        mask = tri_material_indices == mat_slot
        mat_tris = remapped_tris[mask]
        idx_count = int(mat_tris.size)
        if idx_count == 0:
            continue
        used_verts = welded_positions[mat_tris.ravel()]
        sm_center, sm_extents = compute_aabb_unity(used_verts)
        submeshes.append({
            "index_start": index_cursor,
            "index_count": idx_count,
            "material_slot": max(0, int(mat_slot)),
            "bounds_center": sm_center,
            "bounds_extents": sm_extents,
        })
        ordered_tri_list.append(mat_tris)
        index_cursor += idx_count

    if ordered_tri_list:
        all_tris = np.vstack(ordered_tri_list)
    else:
        all_tris = np.empty((0, 3), dtype=np.uint32)

    use_u32 = vertex_count > 65535
    if use_u32:
        index_buffer = np.ascontiguousarray(all_tris, dtype="<u4").tobytes()
        index_format = 1
    else:
        index_buffer = np.ascontiguousarray(all_tris, dtype="<u2").tobytes()
        index_format = 0

    vertex_buffer = welded_vertices_bytes.tobytes()
    bounds_center, bounds_extents = compute_aabb_unity(welded_positions)

    # Compute 64-bit content hash over layout + submeshes + vertex_buffer + index_buffer
    hasher = hashlib.blake2b(digest_size=8)
    for attr in layout:
        hasher.update(
            f"{attr['semantic']}:{attr['format']}:{attr['dimension']}:{attr['offset']};".encode("ascii")
        )
    for sm in submeshes:
        hasher.update(f"{sm['index_start']}:{sm['index_count']}:{sm['material_slot']};".encode("ascii"))
    hasher.update(vertex_buffer)
    hasher.update(index_buffer)
    content_hash = int.from_bytes(hasher.digest(), byteorder="little", signed=False)

    return {
        "content_hash": content_hash,
        "bounds_center": bounds_center,
        "bounds_extents": bounds_extents,
        "vertex_count": vertex_count,
        "vertex_stride": vertex_stride,
        "layout": layout,
        "index_format": index_format,
        "index_count": int(all_tris.size),
        "submeshes": submeshes,
        "vertex_buffer": vertex_buffer,
        "index_buffer": index_buffer,
        "triangle_count": int(all_tris.shape[0]),
    }


def pack_evaluated_mesh(
    mesh: Any,
    attr_manager: AttributeMappingManager,
    unit_scale: float = 1.0,
    lightmap_uv_name: str = "Lightmap",
    tangent_uv_name: str = "",
    use_float16_colors: bool = True,
    use_float16_normals: bool = False,
    enable_chunking: bool = True,
    chunk_tri_threshold: int = 250_000,
    chunk_cell_size: float = 64.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Pack an evaluated Blender 5.x Mesh datablock into one or more Unity-ready mesh chunks.
    Returns (list_of_packed_mesh_dicts, list_of_warnings).
    """
    warnings: list[str] = []
    num_loops = len(mesh.loops)
    num_verts = len(mesh.vertices)
    num_polys = len(mesh.polygons)

    if num_loops == 0 or num_verts == 0 or num_polys == 0:
        return [], warnings

    mesh.calc_loop_triangles()
    num_tris = len(mesh.loop_triangles)
    if num_tris == 0:
        return [], warnings

    # 1. Positions -> corners -> Unity space
    vert_positions = np.empty(num_verts * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", vert_positions)
    vert_positions = vert_positions.reshape((num_verts, 3))
    convert_positions_inplace(vert_positions, unit_scale)

    loop_vertex_indices = np.empty(num_loops, dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertex_indices)
    corner_positions = vert_positions[loop_vertex_indices]

    # 2. Corner Normals (Blender 5.x `mesh.corner_normals`) -> Unity space
    corner_normals = np.empty(num_loops * 3, dtype=np.float32)
    mesh.corner_normals.foreach_get("vector", corner_normals)
    corner_normals = corner_normals.reshape((num_loops, 3))
    convert_directions_inplace(corner_normals)

    # 3. UV layers (UV0 -> TEXCOORD0, Lightmap -> TEXCOORD1, extra UVs -> higher slots if free)
    uv_layers = getattr(mesh, "uv_layers", ())
    uv_map_names = {uv.name for uv in uv_layers}
    uv0_data: np.ndarray | None = None
    uv1_lightmap_data: np.ndarray | None = None
    active_tangent_uv_layer: str | None = None

    if len(uv_layers) > 0:
        primary_uv = uv_layers.active or uv_layers[0]
        active_tangent_uv_layer = tangent_uv_name if (tangent_uv_name and tangent_uv_name in uv_map_names) else primary_uv.name
        uv0_raw = np.empty(num_loops * 2, dtype=np.float32)
        primary_uv.data.foreach_get("uv", uv0_raw)
        uv0_data = uv0_raw.reshape((num_loops, 2))

        if lightmap_uv_name in uv_map_names and lightmap_uv_name != primary_uv.name:
            lm_layer = uv_layers[lightmap_uv_name]
            uv1_raw = np.empty(num_loops * 2, dtype=np.float32)
            lm_layer.data.foreach_get("uv", uv1_raw)
            uv1_lightmap_data = uv1_raw.reshape((num_loops, 2))
        elif len(uv_layers) > 1:
            # Second UV layer defaults to TEXCOORD1
            for uv_l in uv_layers:
                if uv_l.name != primary_uv.name:
                    uv1_raw = np.empty(num_loops * 2, dtype=np.float32)
                    uv_l.data.foreach_get("uv", uv1_raw)
                    uv1_lightmap_data = uv1_raw.reshape((num_loops, 2))
                    break

    # 4. Tangents (computed via `mesh.calc_tangents` if UV map exists, else orthonormal fallback)
    corner_tangents = np.empty((num_loops, 4), dtype=np.float32)
    tangents_calculated = False
    if active_tangent_uv_layer:
        try:
            mesh.calc_tangents(uvmap=active_tangent_uv_layer)
            t_xyz = np.empty(num_loops * 3, dtype=np.float32)
            t_sign = np.empty(num_loops, dtype=np.float32)
            mesh.loops.foreach_get("tangent", t_xyz)
            mesh.loops.foreach_get("bitangent_sign", t_sign)
            corner_tangents[:, 0:3] = t_xyz.reshape((num_loops, 3))
            corner_tangents[:, 3] = t_sign
            convert_tangents_inplace(corner_tangents)
            tangents_calculated = True
            mesh.free_tangents()
        except Exception:
            tangents_calculated = False

    if not tangents_calculated:
        # Vectorized orthonormal tangent from Unity corner_normals
        up = np.zeros_like(corner_normals)
        mask_y = np.abs(corner_normals[:, 1]) < 0.999
        up[mask_y, 1] = 1.0
        up[~mask_y, 0] = 1.0
        t_vec = np.cross(up, corner_normals)
        norms = np.linalg.norm(t_vec, axis=1, keepdims=True)
        t_vec = t_vec / np.maximum(norms, 1e-6)
        corner_tangents[:, 0:3] = t_vec
        corner_tangents[:, 3] = -1.0

    # 5. Polygon loop starts & totals for FACE-domain attribute expansion
    poly_loop_starts = np.empty(num_polys, dtype=np.int32)
    poly_loop_totals = np.empty(num_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", poly_loop_starts)
    mesh.polygons.foreach_get("loop_total", poly_loop_totals)

    # 6. Color attributes + Geometry Nodes custom attributes
    primary_color, packed_attr_channels, attr_warns = attr_manager.pack_mesh_attributes(
        mesh, num_loops, loop_vertex_indices, poly_loop_starts, poly_loop_totals, uv_map_names
    )
    warnings.extend(attr_warns)

    # 7. Assemble ordered vertex columns matching Unity VertexAttribute ordering
    corner_columns: list[tuple[int, int, int, np.ndarray]] = [
        (VertexSemantic.POSITION, VertexFormat.FLOAT32, 3, corner_positions),
    ]
    if use_float16_normals:
        norm_pad = np.zeros((num_loops, 4), dtype=np.float16)
        norm_pad[:, 0:3] = corner_normals.astype(np.float16)
        corner_columns.append((VertexSemantic.NORMAL, VertexFormat.FLOAT16, 4, norm_pad))
    else:
        corner_columns.append((VertexSemantic.NORMAL, VertexFormat.FLOAT32, 3, corner_normals))

    corner_columns.append((VertexSemantic.TANGENT, VertexFormat.FLOAT32, 4, corner_tangents))

    if primary_color is not None:
        if use_float16_colors:
            corner_columns.append((VertexSemantic.COLOR, VertexFormat.FLOAT16, 4, primary_color.astype(np.float16)))
        else:
            corner_columns.append((VertexSemantic.COLOR, VertexFormat.FLOAT32, 4, primary_color.astype(np.float32)))

    if uv0_data is not None:
        corner_columns.append((VertexSemantic.TEXCOORD0, VertexFormat.FLOAT32, 2, uv0_data))
    if uv1_lightmap_data is not None:
        corner_columns.append((VertexSemantic.TEXCOORD1, VertexFormat.FLOAT32, 2, uv1_lightmap_data))

    for ch in sorted(packed_attr_channels.keys()):
        semantic = VertexSemantic(int(VertexSemantic.TEXCOORD0) + ch)
        corner_columns.append((semantic, VertexFormat.FLOAT32, 4, packed_attr_channels[ch]))

    # 8. Extract triangles and flip winding for handedness conversion
    tri_loops = np.empty(num_tris * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    tri_loops = tri_loops.reshape((num_tris, 3))
    flip_triangle_winding_inplace(tri_loops)

    tri_mat_indices = np.zeros(num_tris, dtype=np.int32)
    try:
        mesh.loop_triangles.foreach_get("material_index", tri_mat_indices)
    except Exception:
        pass

    mesh_name = getattr(mesh, "name", "Mesh")

    # 9. Spatial Grid Chunking for huge meshes (> chunk_tri_threshold)
    if enable_chunking and num_tris > chunk_tri_threshold and chunk_cell_size > 0.0:
        tri_p0 = corner_positions[tri_loops[:, 0]]
        tri_p1 = corner_positions[tri_loops[:, 1]]
        tri_p2 = corner_positions[tri_loops[:, 2]]
        tri_centroids = (tri_p0 + tri_p1 + tri_p2) * (1.0 / 3.0)
        cell_coords = np.floor(tri_centroids / np.float32(chunk_cell_size)).astype(np.int32)
        cell_view = np.ascontiguousarray(cell_coords).view(np.dtype((np.void, 12))).ravel()
        _, unique_cell_idx, cell_inverse = np.unique(cell_view, return_index=True, return_inverse=True)

        chunks: list[dict[str, Any]] = []
        for cid in range(len(unique_cell_idx)):
            mask = cell_inverse == cid
            sub_tri_loops = tri_loops[mask]
            sub_tri_mats = tri_mat_indices[mask]
            # Slice only loops referenced by this chunk to keep welding fast
            unique_loops, local_tri_loops = np.unique(sub_tri_loops, return_inverse=True)
            local_tri_loops = local_tri_loops.reshape(sub_tri_loops.shape)
            local_columns = [
                (sem, fmt, dim, arr[unique_loops]) for (sem, fmt, dim, arr) in corner_columns
            ]
            local_pos = corner_positions[unique_loops]
            packed = _weld_corners_and_pack(local_columns, local_tri_loops, sub_tri_mats, local_pos)
            packed["name"] = f"{mesh_name}_chunk{cid}"
            chunks.append(packed)
        return chunks, warnings

    # Single mesh asset
    packed = _weld_corners_and_pack(corner_columns, tri_loops, tri_mat_indices, corner_positions)
    packed["name"] = mesh_name
    return [packed], warnings
