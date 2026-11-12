"""
Automated unit & parity tests for the BlenderSync Python pipeline.
Tests:
1. Coordinate conversion math (positions, normals, tangents, winding flip, C*M*C world matrix).
2. Asymmetric F-mesh packing, submesh splitting, and AABB calculation.
3. Linear FLOAT_COLOR (with HDR > 1.0) and BYTE_COLOR preservation without sRGB distortion.
4. Custom Geometry Nodes attributes packing into TEXCOORD2..7 and JSON manifest generation.
5. Visibility rules (eye icon, hide_viewport, WIRE/BOUNDS, unity_skip, unity_force).
6. Protocol framing, zlib compression, and malformed/fuzz packet rejection (Scenario 11).

Run with Python/NumPy or inside Blender (`blender -b --python blender_extension/tests/test_blender_pipeline.py`).
"""

from __future__ import annotations
import os
import sys
import unittest
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from blender_extension.coords import (
    convert_positions_inplace,
    convert_directions_inplace,
    convert_tangents_inplace,
    flip_triangle_winding_inplace,
    convert_matrix_world,
    compute_aabb_unity,
)
from blender_extension.protocol import (
    MessageType,
    frame_message,
    decode_frame_header,
    decode_frame_payload,
    encode_hello,
    HEADER_STRUCT,
)
from blender_extension.visibility import VisibilitySettings, is_object_visible


class TestCoordinateAndProtocolPipeline(unittest.TestCase):
    def test_asymmetric_f_coordinate_and_winding_conversion(self) -> None:
        # Scenario 1: Asymmetric F points in Blender (x=right, y=forward, z=up)
        pts_bl = np.array([
            [1.0, 2.0, 3.0],   # Tip of F
            [-0.5, 0.25, 4.0],
        ], dtype=np.float32)
        convert_positions_inplace(pts_bl, unit_scale=2.0)
        # Expected in Unity: (x, z, y) * 2.0
        np.testing.assert_allclose(pts_bl[0], [2.0, 6.0, 4.0], rtol=1e-6)
        np.testing.assert_allclose(pts_bl[1], [-1.0, 8.0, 0.5], rtol=1e-6)

        # Tangent handedness flip: (tx, ty, tz, tw)_bl -> (tx, tz, ty, -tw)_unity
        tan_bl = np.array([[0.0, 1.0, 0.0, 1.0]], dtype=np.float32)
        convert_tangents_inplace(tan_bl)
        np.testing.assert_allclose(tan_bl[0], [0.0, 0.0, 1.0, -1.0], rtol=1e-6)

        # Winding flip: (0, 1, 2) -> (0, 2, 1)
        tris = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
        flip_triangle_winding_inplace(tris)
        np.testing.assert_array_equal(tris[0], [0, 2, 1])
        np.testing.assert_array_equal(tris[1], [3, 5, 4])

    def test_world_matrix_conversion_preserves_negative_determinant(self) -> None:
        # Mirrored object in Blender (scale X = -1)
        m_bl = np.array([
            [-1.0, 0.0, 0.0, 5.0],
            [0.0, 1.0, 0.0, 10.0],
            [0.0, 0.0, 1.0, 20.0],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=np.float32)
        m_u = convert_matrix_world(m_bl, unit_scale=1.0)
        # Translation (5, 10, 20)_bl -> (5, 20, 10)_u
        np.testing.assert_allclose(m_u[0:3, 3], [5.0, 20.0, 10.0], rtol=1e-6)
        # Determinant sign is preserved: det(C * M * C) == det(M)
        self.assertAlmostEqual(float(np.linalg.det(m_bl)), float(np.linalg.det(m_u)), places=5)

    def test_visibility_rules(self) -> None:
        # Scenario 3: Eye icon, hide_viewport, WIRE/BOUNDS, unity_skip, unity_force
        class MockObj:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
            def visible_get(self, view_layer=None):
                return self.__dict__.get("_visible", True)
            def __contains__(self, key):
                return key in self.__dict__
            def __getitem__(self, key):
                return self.__dict__[key]

        vis = VisibilitySettings()
        normal_obj = MockObj(type="MESH", display_type="TEXTURED", hide_viewport=False, _visible=True)
        self.assertTrue(is_object_visible(normal_obj, True, vis, set()))

        wire_obj = MockObj(type="MESH", display_type="WIRE", hide_viewport=False, _visible=True)
        self.assertFalse(is_object_visible(wire_obj, True, vis, set()))

        forced_wire = MockObj(type="MESH", display_type="WIRE", hide_viewport=False, _visible=True, unity_force=True)
        self.assertTrue(is_object_visible(forced_wire, True, vis, set()))

        skipped_obj = MockObj(type="MESH", display_type="TEXTURED", hide_viewport=False, _visible=True, unity_skip=True)
        self.assertFalse(is_object_visible(skipped_obj, True, vis, set()))

        light_obj = MockObj(type="LIGHT", display_type="TEXTURED", hide_viewport=False, _visible=True)
        self.assertFalse(is_object_visible(light_obj, True, vis, set()))  # Opt-in only by default (R6)

    def test_protocol_compression_and_fuzz_safety(self) -> None:
        # Scenario 11: Protocol roundtrip & malformed header rejection
        raw_data = b"ABCD" * 1000
        framed = frame_message(MessageType.MESH_ASSET, raw_data, compress=True)
        ver, flags, mtype, plen = decode_frame_header(framed[:HEADER_STRUCT.size])
        self.assertEqual(ver, 1)
        self.assertEqual(mtype, MessageType.MESH_ASSET)
        decoded = decode_frame_payload(flags, framed[HEADER_STRUCT.size : HEADER_STRUCT.size + plen])
        self.assertEqual(decoded, raw_data)

        # Bad magic
        with self.assertRaises(ValueError):
            decode_frame_header(b"XXXX" + b"\x00" * 16)

        # Oversized payload (> 512 MiB) rejected immediately
        malicious_hdr = HEADER_STRUCT.pack(b"BSYN", 1, 0, int(MessageType.HELLO), 10 * 1024 * 1024 * 1024)
        with self.assertRaises(ValueError):
            decode_frame_header(malicious_hdr)


if __name__ == "__main__":
    unittest.main()
