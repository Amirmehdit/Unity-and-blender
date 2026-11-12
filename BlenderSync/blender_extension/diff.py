"""
Depsgraph dirty tracking, fast-path transform batching, debounce, and time-slicing (Section 5.7).
- `depsgraph_update_post` ONLY marks dirty IDs (never runs heavy work inside the handler).
- `bpy.app.timers` drains pending updates after a debounce window or throttled modal rate.
"""

from __future__ import annotations
import struct
import time
import numpy as np
from typing import Any

from .coords import convert_matrix_world
from .protocol import encode_transform_batch, encode_object_remove
from .scene_walker import make_stable_object_id
from .visibility import VisibilitySettings, build_excluded_collections_set, is_object_visible


class DirtyStateTracker:
    def __init__(self) -> None:
        self.dirty_transforms: set[str] = set()
        self.dirty_geometry: set[str] = set()
        self.dirty_materials: set[str] = set()
        self.full_resync_needed: bool = True
        self.last_event_timestamp: float = 0.0
        self.last_sync_timestamp: float = 0.0
        self.last_known_visible_names: set[str] = set()

    def clear(self) -> None:
        self.dirty_transforms.clear()
        self.dirty_geometry.clear()
        self.dirty_materials.clear()
        self.full_resync_needed = False

    def mark_full_resync(self) -> None:
        self.full_resync_needed = True
        self.last_event_timestamp = time.perf_counter()

    def on_depsgraph_update(self, scene: Any, depsgraph: Any) -> None:
        """
        Lightweight handler for `bpy.app.handlers.depsgraph_update_post`.
        Maps updated Mesh/Material/NodeTree/Collection datablocks back to dependent objects.
        """
        now = time.perf_counter()
        self.last_event_timestamp = now

        if depsgraph is None or not hasattr(depsgraph, "updates"):
            return

        updated_meshes: set[str] = set()
        updated_materials: set[str] = set()

        for update in depsgraph.updates:
            uid = getattr(update, "id", None)
            if uid is None:
                continue
            id_type = type(uid).__name__

            if id_type == "Object":
                obj_name = uid.name
                if getattr(update, "is_updated_geometry", False) or getattr(update, "is_updated_shading", False):
                    self.dirty_geometry.add(obj_name)
                elif getattr(update, "is_updated_transform", False):
                    self.dirty_transforms.add(obj_name)
            elif id_type in {"Mesh", "Curve", "SurfaceCurve", "TextCurve", "MetaBall"}:
                updated_meshes.add(uid.name)
            elif id_type == "Material":
                updated_materials.add(uid.name)
            elif id_type in {"GeometryNodeTree", "ShaderNodeTree", "NodeTree"}:
                # NodeTree changed: mark objects with GeometryNodes or Materials dirty
                self.full_resync_needed = True
            elif id_type in {"Collection", "Scene"}:
                # Collection hierarchy or visibility may have changed
                pass

        if updated_meshes or updated_materials:
            for obj in getattr(scene, "objects", ()):
                if getattr(obj, "data", None) and getattr(obj.data, "name", "") in updated_meshes:
                    self.dirty_geometry.add(obj.name)
                for slot in getattr(obj, "material_slots", ()):
                    if slot.material and slot.material.name in updated_materials:
                        self.dirty_geometry.add(obj.name)
                        self.dirty_materials.add(slot.material.name)

    def check_visibility_diff(self, context: Any, vis_settings: VisibilitySettings) -> tuple[set[str], set[str]]:
        """
        Cheap O(N) visibility set check on every timer tick so eye-icon / hide_viewport
        toggles are caught immediately even when Blender doesn't emit a depsgraph update.
        Returns (newly_visible_names, newly_hidden_names).
        """
        view_layer = context.view_layer
        scene = context.scene
        excl = build_excluded_collections_set(view_layer)
        current_vis: set[str] = set()
        for obj in getattr(scene, "objects", ()):
            if is_object_visible(obj, view_layer, vis_settings, excl, is_instance=False):
                current_vis.add(obj.name)

        newly_visible = current_vis - self.last_known_visible_names
        newly_hidden = self.last_known_visible_names - current_vis
        self.last_known_visible_names = current_vis
        return newly_visible, newly_hidden

    def build_fast_transform_batch(
        self,
        context: Any,
        transform_only_names: set[str],
        apply_unit_scale: bool = True,
    ) -> list[bytes]:
        """
        Fast-path (<5ms): extract only world matrices for objects whose transform changed
        without touching meshes.
        """
        depsgraph = context.evaluated_depsgraph_get()
        scene = depsgraph.scene_eval
        unit_scale = float(scene.unit_settings.scale_length) if apply_unit_scale else 1.0
        pairs: list[tuple[str, bytes]] = []

        for name in transform_only_names:
            orig_obj = scene.objects.get(name)
            if orig_obj is None:
                continue
            eval_obj = orig_obj.evaluated_get(depsgraph)
            stable_id = make_stable_object_id(orig_obj, is_instance=False)
            mat_u = convert_matrix_world(np.asarray(eval_obj.matrix_world, dtype=np.float32), unit_scale)
            mat_bytes = struct.pack("<16f", *mat_u.ravel().tolist())
            pairs.append((stable_id, mat_bytes))

        if not pairs:
            return []
        return [encode_transform_batch(pairs)]
