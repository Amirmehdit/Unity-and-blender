"""
Evaluated depsgraph scene walker (Sections 5.1, 5.2, 5.3, 5.5, 5.6).
Walks `depsgraph.object_instances` for unified extraction of standalone visible objects
and Geometry Nodes / Collection instances, pairs `to_mesh()` with `to_mesh_clear()`,
and generates a complete stream of BSYN protocol frames.
"""

from __future__ import annotations
import json
import time
import numpy as np
from typing import Any

from .visibility import (
    VisibilitySettings,
    build_excluded_collections_set,
    is_object_visible,
    get_object_collection_path,
)
from .coords import convert_matrix_world
from .attributes import AttributeMappingManager
from .mesh_pack import pack_evaluated_mesh
from .instances import InstanceAccumulator
from .materials import analyze_and_export_material
from .protocol import (
    encode_scene_begin,
    encode_mesh_asset,
    encode_material_upsert,
    encode_texture_file,
    encode_object_upsert,
    encode_object_remove,
    encode_instance_set,
    encode_attribute_map,
    encode_scene_end,
    encode_log_error,
)


def make_stable_object_id(orig_obj: Any, is_instance: bool = False, persistent_id: Any = None, parent_orig: Any = None) -> str:
    base_name = getattr(orig_obj, "name_full", None) or getattr(orig_obj, "name", "Object")
    if not is_instance:
        return f"OBJ:{base_name}"
    parent_name = getattr(parent_orig, "name_full", None) or getattr(parent_orig, "name", "Instancer")
    pid_str = "_".join(str(x) for x in persistent_id) if persistent_id else "0"
    return f"INST:{parent_name}:{base_name}:{pid_str}"


def inspect_unsupported_geometry_set_components(eval_obj: Any) -> list[str]:
    """
    Check Blender 5.0+ `eval_obj.evaluated_geometry()` for unsupported non-mesh
    components (volumes, raw point clouds, grease pencil) so we can log a single
    aggregated human-readable warning.
    """
    unsupported: list[str] = []
    if not hasattr(eval_obj, "evaluated_geometry"):
        return unsupported
    try:
        # Hold reference to GeometrySet for the scope of inspection
        geom_set = eval_obj.evaluated_geometry()
        if geom_set is not None:
            if getattr(geom_set, "volume", None) is not None:
                unsupported.append("Volume")
            if getattr(geom_set, "pointcloud", None) is not None:
                unsupported.append("PointCloud")
            if getattr(geom_set, "grease_pencil", None) is not None:
                unsupported.append("GreasePencil")
    except Exception:
        pass
    return unsupported


class SceneSyncExtractor:
    """
    Extracts evaluated scene state from Blender 5.x into content-addressed BSYN frames.
    Supports both full snapshots and incremental diffs.
    """

    def __init__(self) -> None:
        self.revision: int = 0
        self.sent_hashes: set[int] = set()
        self.last_visible_ids: set[str] = set()
        self.mesh_datablock_cache: dict[tuple[int, int], list[dict[str, Any]]] = {}

    def reset_remote_cache(self, known_hashes: set[int] | None = None) -> None:
        self.sent_hashes = set(known_hashes) if known_hashes else set()
        self.last_visible_ids.clear()
        self.mesh_datablock_cache.clear()

    def extract_sync_frames(
        self,
        context: Any,
        vis_settings: VisibilitySettings,
        dirty_object_names: set[str] | None = None,
        is_full_sync: bool = True,
        apply_unit_scale: bool = True,
        enable_chunking: bool = True,
        chunk_tri_threshold: int = 250_000,
        chunk_cell_size: float = 64.0,
        compress_buffers: bool = False,
        material_mode_overrides: dict[str, str] | None = None,
        attribute_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[list[bytes], dict[str, Any]]:
        t0 = time.perf_counter()
        self.revision += 1

        # Ensure live edits in Edit Mode are flushed to the mesh before evaluating depsgraph
        active_obj = getattr(context, "active_object", None)
        if active_obj is not None and getattr(active_obj, "mode", "OBJECT") == "EDIT":
            try:
                active_obj.update_from_editmode()
            except Exception:
                pass

        depsgraph = context.evaluated_depsgraph_get()
        scene = depsgraph.scene_eval
        view_layer = depsgraph.view_layer_eval

        unit_scale = float(scene.unit_settings.scale_length) if apply_unit_scale else 1.0
        excluded_collections = build_excluded_collections_set(view_layer)
        attr_manager = AttributeMappingManager(user_overrides=attribute_overrides)
        inst_accumulator = InstanceAccumulator(unit_scale=unit_scale)
        mat_overrides = material_mode_overrides or {}

        frames: list[bytes] = []
        frames.append(
            encode_scene_begin(
                revision=self.revision,
                is_full_sync=is_full_sync,
                unit_scale=unit_scale,
                scene_name=scene.name,
                view_layer_name=view_layer.name,
            )
        )

        current_visible_ids: set[str] = set()
        unsupported_warnings: list[str] = []
        all_warnings: list[str] = []
        processed_materials: set[str] = set()

        total_objects = 0
        total_triangles = 0
        total_instances = 0

        # Track per-instancer instance index for custom instance-domain attribute lookup
        instancer_counters: dict[int, int] = {}

        for obj_inst in depsgraph.object_instances:
            eval_obj = obj_inst.object
            if eval_obj is None:
                continue
            orig_obj = getattr(eval_obj, "original", eval_obj)
            is_inst = bool(obj_inst.is_instance)

            if is_inst:
                parent_eval = obj_inst.parent
                parent_orig = getattr(parent_eval, "original", parent_eval)
                # Instancer itself must be visible in viewport
                if not is_object_visible(parent_orig, view_layer, vis_settings, excluded_collections, is_instance=False):
                    continue
                if not is_object_visible(orig_obj, view_layer, vis_settings, excluded_collections, is_instance=True):
                    continue
            else:
                if not is_object_visible(orig_obj, view_layer, vis_settings, excluded_collections, is_instance=False):
                    continue

            obj_type = getattr(eval_obj, "type", "EMPTY")

            # Check for unsupported GeometrySet components on non-instances
            if not is_inst and obj_type in {"VOLUME", "POINTCLOUD", "GPENCIL", "GREASEPENCIL", "MESH", "CURVE"}:
                unsup = inspect_unsupported_geometry_set_components(eval_obj)
                if unsup:
                    unsupported_warnings.append(f"{orig_obj.name} ({', '.join(unsup)})")
                if obj_type in {"VOLUME", "POINTCLOUD", "GPENCIL", "GREASEPENCIL"}:
                    continue

            # Extract materials used by this object
            mat_names: list[str] = []
            for slot in getattr(eval_obj, "material_slots", ()):
                mat = slot.material
                if mat is not None:
                    mat_names.append(mat.name)
                    if mat.name not in processed_materials:
                        processed_materials.add(mat.name)
                        mode_ov = mat_overrides.get(mat.name, "AUTO")
                        mat_pkt, tex_list = analyze_and_export_material(mat, user_mode_override=mode_ov)
                        for tex in tex_list:
                            if tex["content_hash"] not in self.sent_hashes:
                                self.sent_hashes.add(tex["content_hash"])
                                frames.append(
                                    encode_texture_file(
                                        tex["content_hash"],
                                        tex["name"],
                                        tex["ext"],
                                        tex["is_srgb"],
                                        tex["width"],
                                        tex["height"],
                                        tex["bytes"],
                                    )
                                )
                        if mat_pkt["content_hash"] not in self.sent_hashes:
                            self.sent_hashes.add(mat_pkt["content_hash"])
                            frames.append(
                                encode_material_upsert(
                                    mat_pkt["content_hash"],
                                    mat_pkt["name"],
                                    mat_pkt["json_descriptor"],
                                )
                            )
                else:
                    mat_names.append("DefaultMaterial")

            if not mat_names and obj_type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
                mat_names.append("DefaultMaterial")
                if "DefaultMaterial" not in processed_materials:
                    processed_materials.add("DefaultMaterial")
                    mat_pkt, _ = analyze_and_export_material(None)
                    if mat_pkt["content_hash"] not in self.sent_hashes:
                        self.sent_hashes.add(mat_pkt["content_hash"])
                        frames.append(
                            encode_material_upsert(
                                mat_pkt["content_hash"],
                                mat_pkt["name"],
                                mat_pkt["json_descriptor"],
                            )
                        )

            # Handle Mesh / Curve-with-Mesh-Representation
            if obj_type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
                # Extract packed mesh chunks (cached per evaluated mesh pointer within this sync if instanced)
                data_ptr = eval_obj.data.as_pointer() if getattr(eval_obj, "data", None) and hasattr(eval_obj.data, "as_pointer") else id(eval_obj)
                cache_key = (data_ptr, 0 if is_inst else id(eval_obj))

                need_repack = (
                    is_full_sync
                    or dirty_object_names is None
                    or orig_obj.name in dirty_object_names
                    or cache_key not in self.mesh_datablock_cache
                )

                if need_repack:
                    temp_mesh = None
                    try:
                        temp_mesh = eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
                        if temp_mesh is None or len(temp_mesh.polygons) == 0:
                            if obj_type == "CURVE":
                                unsupported_warnings.append(f"{orig_obj.name} (Curve without bevel/extrude mesh)")
                            continue
                        chunks, mesh_warns = pack_evaluated_mesh(
                            temp_mesh,
                            attr_manager=attr_manager,
                            unit_scale=unit_scale,
                            enable_chunking=(enable_chunking and not is_inst),
                            chunk_tri_threshold=chunk_tri_threshold,
                            chunk_cell_size=chunk_cell_size,
                        )
                        all_warnings.extend(mesh_warns)
                        self.mesh_datablock_cache[cache_key] = chunks
                    finally:
                        if temp_mesh is not None:
                            eval_obj.to_mesh_clear()
                else:
                    chunks = self.mesh_datablock_cache[cache_key]

                if not chunks:
                    continue

                # Emit any unseen MESH_ASSET frames
                for ch_mesh in chunks:
                    m_hash = ch_mesh["content_hash"]
                    if m_hash not in self.sent_hashes:
                        self.sent_hashes.add(m_hash)
                        frames.append(encode_mesh_asset(ch_mesh, compress=compress_buffers))

                if is_inst:
                    parent_eval = obj_inst.parent
                    parent_orig = getattr(parent_eval, "original", parent_eval)
                    owner_id = make_stable_object_id(parent_orig, is_instance=False)
                    current_visible_ids.add(owner_id)
                    col_path = get_object_collection_path(parent_orig, scene.collection)

                    p_ptr = parent_eval.as_pointer() if hasattr(parent_eval, "as_pointer") else id(parent_eval)
                    inst_idx = instancer_counters.get(p_ptr, 0)
                    instancer_counters[p_ptr] = inst_idx + 1

                    inst_attrs = inst_accumulator.extract_instancer_attributes(parent_eval)
                    inst_data_f4 = None
                    if inst_attrs is not None and inst_idx < inst_attrs.shape[0]:
                        inst_data_f4 = inst_attrs[inst_idx]

                    inst_accumulator.add_instance(
                        owner_stable_id=owner_id,
                        source_object_name=orig_obj.name,
                        collection_path=col_path,
                        mesh_hash=chunks[0]["content_hash"],
                        material_names=mat_names,
                        matrix_world_blender=np.asarray(obj_inst.matrix_world, dtype=np.float32),
                        instance_data_float4=inst_data_f4,
                    )
                    total_instances += 1
                    total_triangles += sum(c["triangle_count"] for c in chunks)
                else:
                    stable_id = make_stable_object_id(orig_obj, is_instance=False)
                    current_visible_ids.add(stable_id)
                    if not is_full_sync and dirty_object_names is not None and orig_obj.name not in dirty_object_names:
                        continue

                    col_path = get_object_collection_path(orig_obj, scene.collection)
                    unity_mat = convert_matrix_world(np.asarray(obj_inst.matrix_world, dtype=np.float32), unit_scale)
                    chunk_hashes = [c["content_hash"] for c in chunks]
                    frames.append(
                        encode_object_upsert(
                            stable_id=stable_id,
                            object_name=orig_obj.name,
                            collection_path=col_path,
                            object_type=0,  # Mesh
                            world_matrix_flat=unity_mat.ravel().tolist(),
                            flags=3,  # cast & receive shadows
                            mesh_chunk_hashes=chunk_hashes,
                            material_names=mat_names,
                        )
                    )
                    total_objects += 1
                    total_triangles += sum(c["triangle_count"] for c in chunks)

            elif not is_inst and obj_type in {"LIGHT", "CAMERA", "EMPTY"}:
                stable_id = make_stable_object_id(orig_obj, is_instance=False)
                current_visible_ids.add(stable_id)
                col_path = get_object_collection_path(orig_obj, scene.collection)
                unity_mat = convert_matrix_world(np.asarray(obj_inst.matrix_world, dtype=np.float32), unit_scale)
                type_code = {"LIGHT": 1, "CAMERA": 2, "EMPTY": 3}[obj_type]
                extra_props: dict[str, Any] = {}
                if obj_type == "LIGHT" and getattr(eval_obj, "data", None):
                    l_data = eval_obj.data
                    extra_props = {
                        "light_type": getattr(l_data, "type", "POINT"),
                        "color": list(getattr(l_data, "color", (1.0, 1.0, 1.0))),
                        "energy": float(getattr(l_data, "energy", 10.0)),
                    }
                elif obj_type == "CAMERA" and getattr(eval_obj, "data", None):
                    c_data = eval_obj.data
                    extra_props = {
                        "lens": float(getattr(c_data, "lens", 50.0)),
                        "clip_start": float(getattr(c_data, "clip_start", 0.1)),
                        "clip_end": float(getattr(c_data, "clip_end", 1000.0)),
                    }
                frames.append(
                    encode_object_upsert(
                        stable_id=stable_id,
                        object_name=orig_obj.name,
                        collection_path=col_path,
                        object_type=type_code,
                        world_matrix_flat=unity_mat.ravel().tolist(),
                        flags=0,
                        mesh_chunk_hashes=[],
                        material_names=[],
                        custom_props_json=json.dumps(extra_props),
                    )
                )
                total_objects += 1

        # Emit batched INSTANCE_SET frames
        for batch in inst_accumulator.finalize_batches():
            frames.append(
                encode_instance_set(
                    owner_stable_id=batch["owner_stable_id"],
                    source_object_name=batch["source_object_name"],
                    collection_path=batch["collection_path"],
                    mesh_hash=batch["mesh_hash"],
                    material_names=batch["material_names"],
                    instance_count=batch["instance_count"],
                    matrices_bytes=batch["matrices_bytes"],
                    instance_data_bytes=batch["instance_data_bytes"],
                    compress=compress_buffers,
                )
            )

        # Diff visible object set to detect hidden/deleted objects (Section 5.1)
        removed_ids = sorted(self.last_visible_ids - current_visible_ids)
        if removed_ids:
            frames.append(encode_object_remove(removed_ids))
        self.last_visible_ids = current_visible_ids

        # Emit attribute map manifest
        if attr_manager.manifest:
            frames.append(encode_attribute_map(attr_manager.to_json()))

        # Emit aggregated warning for unsupported evaluated components
        if unsupported_warnings:
            msg = f"Skipped {len(unsupported_warnings)} unsupported non-mesh objects: " + ", ".join(unsupported_warnings[:10])
            all_warnings.append(msg)
            frames.append(encode_log_error(1, msg))

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        frames.append(
            encode_scene_end(
                revision=self.revision,
                total_objects=total_objects,
                total_triangles=total_triangles,
                total_instances=total_instances,
                elapsed_ms=elapsed_ms,
            )
        )

        stats = {
            "revision": self.revision,
            "objects": total_objects,
            "triangles": total_triangles,
            "instances": total_instances,
            "elapsed_ms": elapsed_ms,
            "warnings": all_warnings,
        }
        return frames, stats
