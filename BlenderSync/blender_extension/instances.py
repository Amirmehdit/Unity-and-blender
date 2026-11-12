"""
Geometry Nodes instances & Collection instances extraction (Section 5.5).
Preserves instancing (never realizes identical instance meshes), converts
world transforms to Unity space in vectorized batches, and reads optional
instance-domain attributes via Blender 5.x `Object.evaluated_geometry()`.
"""

from __future__ import annotations
import numpy as np
from typing import Any
from .coords import convert_matrices_batch


class InstanceAccumulator:
    """
    Groups instances emitted during scene walking by (instancer_stable_id, mesh_hash, materials_tuple)
    so each unique source mesh is sent once and all its instance transforms + per-instance float4
    attributes are sent in a single `INSTANCE_SET` batch.
    """

    def __init__(self, unit_scale: float = 1.0) -> None:
        self.unit_scale = unit_scale
        self._groups: dict[tuple[str, int, tuple[str, ...]], dict[str, Any]] = {}
        self._instancer_attr_cache: dict[int, np.ndarray | None] = {}

    def extract_instancer_attributes(self, eval_parent_obj: Any) -> np.ndarray | None:
        """
        Inspect `eval_parent_obj.evaluated_geometry()` (Blender 5.0+ `GeometrySet`)
        while keeping the GeometrySet reference alive, and extract the first custom
        instance-domain attribute (or `instance_color` / `id` / user attribute)
        from `geom_set.instances_pointcloud()`.
        """
        if eval_parent_obj is None or not hasattr(eval_parent_obj, "evaluated_geometry"):
            return None
        ptr = eval_parent_obj.as_pointer() if hasattr(eval_parent_obj, "as_pointer") else id(eval_parent_obj)
        if ptr in self._instancer_attr_cache:
            return self._instancer_attr_cache[ptr]

        result: np.ndarray | None = None
        try:
            # Keep GeometrySet alive for the entire attribute extraction block
            geom_set = eval_parent_obj.evaluated_geometry()
            if geom_set is not None and hasattr(geom_set, "instances_pointcloud"):
                inst_pc = geom_set.instances_pointcloud()
                if inst_pc is not None and hasattr(inst_pc, "attributes"):
                    num_pts = len(inst_pc.points) if hasattr(inst_pc, "points") else 0
                    if num_pts > 0:
                        for attr in inst_pc.attributes:
                            if attr.name.startswith(".") or attr.name in {
                                "position", "instance_transform", "reference_index"
                            }:
                                continue
                            dtype = getattr(attr, "data_type", "")

# Continuation of blender_extension/instances.py
                            dtype = getattr(attr, "data_type", "")
                            out = np.zeros((num_pts, 4), dtype=np.float32)
                            if dtype in {"FLOAT", "INT", "INT8", "BOOLEAN"}:
                                buf = np.empty(num_pts, dtype=np.float32)
                                attr.data.foreach_get("value", buf)
                                out[:, 0] = buf
                                result = out
                                break
                            elif dtype == "FLOAT2":
                                buf = np.empty(num_pts * 2, dtype=np.float32)
                                attr.data.foreach_get("vector", buf)
                                out[:, 0:2] = buf.reshape((num_pts, 2))
                                result = out
                                break
                            elif dtype == "FLOAT_VECTOR":
                                buf = np.empty(num_pts * 3, dtype=np.float32)
                                attr.data.foreach_get("vector", buf)
                                out[:, 0:3] = buf.reshape((num_pts, 3))
                                result = out
                                break
                            elif dtype in {"FLOAT_COLOR", "BYTE_COLOR"}:
                                buf = np.empty(num_pts * 4, dtype=np.float32)
                                attr.data.foreach_get("color", buf)
                                result = buf.reshape((num_pts, 4))
                                break
        except Exception:
            result = None

        self._instancer_attr_cache[ptr] = result
        return result

    def add_instance(
        self,
        owner_stable_id: str,
        source_object_name: str,
        collection_path: str,
        mesh_hash: int,
        material_names: list[str],
        matrix_world_blender: np.ndarray,
        instance_data_float4: np.ndarray | None = None,
    ) -> None:
        key = (owner_stable_id, int(mesh_hash), tuple(material_names))
        grp = self._groups.get(key)
        if grp is None:
            grp = {
                "owner_stable_id": owner_stable_id,
                "source_object_name": source_object_name,
                "collection_path": collection_path,
                "mesh_hash": int(mesh_hash),
                "material_names": list(material_names),
                "matrices_blender": [],
                "instance_data": [],
                "has_custom_data": False,
            }
            self._groups[key] = grp

        grp["matrices_blender"].append(np.asarray(matrix_world_blender, dtype=np.float32))
        if instance_data_float4 is not None:
            grp["instance_data"].append(np.asarray(instance_data_float4, dtype=np.float32))
            grp["has_custom_data"] = True
        else:
            grp["instance_data"].append(np.zeros(4, dtype=np.float32))

    def finalize_batches(self) -> list[dict[str, Any]]:
        batches: list[dict[str, Any]] = []
        for grp in self._groups.values():
            mats_bl = np.asarray(grp["matrices_blender"], dtype=np.float32)
            count = int(mats_bl.shape[0])
            if count == 0:
                continue
            mats_unity = convert_matrices_batch(mats_bl, self.unit_scale)
            matrices_bytes = np.ascontiguousarray(mats_unity, dtype="<f4").tobytes()
            inst_data_bytes = None
            if grp["has_custom_data"]:
                data_arr = np.asarray(grp["instance_data"], dtype="<f4")
                inst_data_bytes = np.ascontiguousarray(data_arr).tobytes()

            batches.append({
                "owner_stable_id": grp["owner_stable_id"],
                "source_object_name": grp["source_object_name"],
                "collection_path": grp["collection_path"],
                "mesh_hash": grp["mesh_hash"],
                "material_names": grp["material_names"],
                "instance_count": count,
                "matrices_bytes": matrices_bytes,
                "instance_data_bytes": inst_data_bytes,
            })
        return batches

