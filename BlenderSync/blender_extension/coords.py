"""
Coordinate and unit conversion between Blender (Right-Handed, Z-Up)
and Unity (Left-Handed, Y-Up).

Mathematical rules:
- Vector mapping: (x, y, z)_blender -> (x, z, y)_unity
- Basis swap matrix C:
    [[1, 0, 0, 0],
     [0, 0, 1, 0],
     [0, 1, 0, 0],
     [0, 0, 0, 1]]
- World matrix mapping: M_unity = C @ M_blender @ C
  (with translation scaled by unit_scale).
- Because det(C) = -1, local mesh winding is reversed: (i0, i1, i2) -> (i0, i2, i1),
  and tangent w sign is negated: w_unity = -w_blender.
"""

from __future__ import annotations
import numpy as np

BASIS_SWAP_4X4 = np.array([
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
], dtype=np.float32)


def convert_positions_inplace(positions: np.ndarray, unit_scale: float = 1.0) -> np.ndarray:
    """
    Convert (N, 3) float32 positions from Blender (x, y, z) to Unity (x, z, y) in-place.
    """
    if positions.size == 0:
        return positions
    # Swap Y and Z columns
    positions[:, [1, 2]] = positions[:, [2, 1]]
    if abs(unit_scale - 1.0) > 1e-7:
        positions *= np.float32(unit_scale)
    return positions


def convert_directions_inplace(vectors: np.ndarray) -> np.ndarray:
    """
    Convert (N, 3) float32 direction vectors (normals, vector attributes)
    from Blender (x, y, z) to Unity (x, z, y) in-place.
    """
    if vectors.size == 0:
        return vectors
    vectors[:, [1, 2]] = vectors[:, [2, 1]]
    return vectors


def convert_tangents_inplace(tangents_xyzw: np.ndarray) -> np.ndarray:
    """
    Convert (N, 4) float32 tangents from Blender (tx, ty, tz, tw)
    to Unity (tx, tz, ty, -tw) in-place.
    """
    if tangents_xyzw.size == 0:
        return tangents_xyzw
    tangents_xyzw[:, [1, 2]] = tangents_xyzw[:, [2, 1]]
    tangents_xyzw[:, 3] = -tangents_xyzw[:, 3]
    return tangents_xyzw


def flip_triangle_winding_inplace(tris: np.ndarray) -> np.ndarray:
    """
    Reverse triangle winding (N, 3) from (i0, i1, i2) to (i0, i2, i1) in-place.
    """
    if tris.size == 0:
        return tris
    tris[:, [1, 2]] = tris[:, [2, 1]]
    return tris


def convert_matrix_world(matrix_blender: np.ndarray, unit_scale: float = 1.0) -> np.ndarray:
    """
    Convert a single (4, 4) Blender world matrix to a (4, 4) Unity world matrix:
        M_unity = C @ M_blender @ C
    with translation scaled by unit_scale.
    """
    m = np.asarray(matrix_blender, dtype=np.float32)
    # C @ m @ C swaps rows 1 & 2 and swaps cols 1 & 2
    out = m.copy()
    out[[1, 2], :] = out[[2, 1], :]
    out[:, [1, 2]] = out[:, [2, 1]]
    if abs(unit_scale - 1.0) > 1e-7:
        out[0:3, 3] *= np.float32(unit_scale)
    return out


def convert_matrices_batch(matrices_blender: np.ndarray, unit_scale: float = 1.0) -> np.ndarray:
    """
    Vectorized conversion of (N, 4, 4) Blender world matrices to Unity space.
    """
    if matrices_blender.size == 0:
        return np.empty((0, 4, 4), dtype=np.float32)
    out = np.array(matrices_blender, dtype=np.float32, copy=True)
    out[:, [1, 2], :] = out[:, [2, 1], :]
    out[:, :, [1, 2]] = out[:, :, [2, 1]]
    if abs(unit_scale - 1.0) > 1e-7:
        out[:, 0:3, 3] *= np.float32(unit_scale)
    return out


def compute_aabb_unity(positions_unity: np.ndarray) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """
    Compute (center, extents) AABB in Unity coordinates from already-converted (N, 3) positions.
    """
    if positions_unity.shape[0] == 0:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    b_min = positions_unity.min(axis=0)
    b_max = positions_unity.max(axis=0)
    center = ((b_min + b_max) * 0.5).tolist()
    extents = ((b_max - b_min) * 0.5).tolist()
    return (float(center[0]), float(center[1]), float(center[2])), (float(extents[0]), float(extents[1]), float(extents[2]))
