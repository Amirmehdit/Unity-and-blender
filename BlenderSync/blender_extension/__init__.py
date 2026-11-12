"""
BlenderSync — Blender 5.x Extension Entry Point.
Registers PropertyGroups, UI Panels, Operators, Depsgraph/Undo/Redo/Load handlers,
and the non-blocking timer loop for real-time Unity 6 URP synchronization.
"""

from __future__ import annotations
import time
import bpy
from bpy.app.handlers import persistent

from .properties import (
    BLENDERSYNC_PG_AttributeMapItem,
    BLENDERSYNC_PG_MaterialModeItem,
    BLENDERSYNC_PG_SceneSettings,
)
from .ui import (
    BLENDERSYNC_OT_SyncNow,
    BLENDERSYNC_OT_FullResync,
    BLENDERSYNC_OT_BakeMaterials,
    BLENDERSYNC_OT_RefreshMaterialList,
    BLENDERSYNC_OT_AddAttributeOverride,
    BLENDERSYNC_PT_MainPanel,
)
from .visibility import VisibilitySettings
from .scene_walker import SceneSyncExtractor
from .diff import DirtyStateTracker
from .transport import LiveTcpClient, write_snapshot_file
from .protocol import MessageType

bl_info = {
    "name": "BlenderSync (Unity 6 URP Real-Time Pipeline)",
    "author": "BlenderSync Tools Engineering",
    "version": (1, 0, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > Unity Sync",
    "description": "Real-time evaluated map, Geometry Nodes, attribute, and shader sync to Unity 6 URP",
    "category": "Import-Export",
}

_TCP_CLIENT = LiveTcpClient()
_EXTRACTOR = SceneSyncExtractor()
_DIRTY_TRACKER = DirtyStateTracker()


def get_tcp_client() -> LiveTcpClient:
    return _TCP_CLIENT


def _build_vis_settings(settings: BLENDERSYNC_PG_SceneSettings) -> VisibilitySettings:
    allowlist = {s.strip() for s in settings.collection_filter_csv.split(",") if s.strip()}
    return VisibilitySettings(
        filter_mode=settings.filter_mode,
        collection_Allowlist=allowlist,
        include_wire_bounds=settings.include_wire_bounds,
        respect_hide_render=settings.respect_hide_render,
        include_lights=settings.include_lights,
        include_cameras=settings.include_cameras,
        include_empties=settings.include_empties,
    )


def trigger_manual_sync(context: bpy.types.Context, full_resync: bool = False) -> None:
    settings = context.scene.blendersync
    vis = _build_vis_settings(settings)

    if settings.live_sync_enabled and not _TCP_CLIENT.is_connected:
        ver_str = ".".join(str(x) for x in bpy.app.version)
        _TCP_CLIENT.start(settings.host, settings.port, settings.token, ver_str, bpy.data.filepath)

    if full_resync:
        _EXTRACTOR.reset_remote_cache()

    mat_overrides = {m.material_name: m.strategy for m in settings.material_modes}
    attr_overrides = {
        a.name: {
            "uv_channel": a.uv_channel,
            "component_offset": a.component_offset,
            **({"remap_min": a.remap_min, "remap_max": a.remap_max} if a.use_remap else {}),
        }
        for a in settings.attribute_map
    }

    frames, stats = _EXTRACTOR.extract_sync_frames(
        context=context,
        vis_settings=vis,
        dirty_object_names=None if full_resync else (_DIRTY_TRACKER.dirty_geometry | _DIRTY_TRACKER.dirty_transforms),
        is_full_sync=full_resync,
        apply_unit_scale=settings.apply_unit_scale,
        enable_chunking=settings.enable_chunking,
        chunk_tri_threshold=settings.chunk_tri_threshold,
        chunk_cell_size=settings.chunk_cell_size,
        compress_buffers=settings.compress_stream,
        material_mode_overrides=mat_overrides,
        attribute_overrides=attr_overrides,
    )
    _DIRTY_TRACKER.clear()

    settings.stat_objects = stats["objects"]
    settings.stat_triangles = stats["triangles"]
    settings.stat_instances = stats["instances"]
    settings.stat_last_sync_ms = stats["elapsed_ms"]

    if _TCP_CLIENT.is_connected:
        _TCP_CLIENT.enqueue_frames(frames)

    if settings.snapshot_cache_path:
        try:
            write_snapshot_file(bpy.path.abspath(settings.snapshot_cache_path), frames)
        except Exception as exc:
            settings.last_error = f"Snapshot write failed: {exc}"


@persistent
def _on_depsgraph_update_post(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    _DIRTY_TRACKER.on_depsgraph_update(scene, depsgraph)


@persistent
def _on_load_or_undo_redo_post(*_args: object) -> None:
    _DIRTY_TRACKER.mark_full_resync()


@persistent
def _on_frame_change_post(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    for obj in getattr(scene, "objects", ()):
        if getattr(obj, "animation_data", None) is not None:
            _DIRTY_TRACKER.dirty_transforms.add(obj.name)
    _DIRTY_TRACKER.last_event_timestamp = time.perf_counter()


def _timer_sync_tick() -> float:
    """
    Periodic `bpy.app.timers` callback driving live synchronization within budget.
    """
    context = bpy.context
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "blendersync"):
        return 0.1

    settings = scene.blendersync
    if not settings.live_sync_enabled:
        if _TCP_CLIENT.is_connected:
            _TCP_CLIENT.stop()
        return 0.25

    if not _TCP_CLIENT._running:
        ver_str = ".".join(str(x) for x in bpy.app.version)
        _TCP_CLIENT.start(settings.host, settings.port, settings.token, ver_str, bpy.data.filepath)

    # Process incoming control messages from Unity
    for mtype, payload in _TCP_CLIENT.poll_incoming_events():
        if mtype == MessageType.HELLO_ACK:
            _EXTRACTOR.reset_remote_cache(payload.get("cached_hashes", set()))
            _DIRTY_TRACKER.mark_full_resync()
        elif mtype == MessageType.REQUEST_FULL_RESYNC:
            _EXTRACTOR.reset_remote_cache()
            _DIRTY_TRACKER.mark_full_resync()
        elif mtype == MessageType.REQUEST_OBJECT and payload:
            _DIRTY_TRACKER.dirty_geometry.add(payload)
        elif mtype == MessageType.SELECT_OBJECT and payload:
            target_name = payload.split(":")[-1]
            obj = scene.objects.get(target_name)
            if obj is not None:
                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj

    if not _TCP_CLIENT.is_connected:
        return 0.25

    # Check visibility changes on every tick
    vis = _build_vis_settings(settings)
    newly_vis, newly_hid = _DIRTY_TRACKER.check_visibility_diff(context, vis)
    if newly_vis or newly_hid:
        _DIRTY_TRACKER.dirty_geometry.update(newly_vis)
        _DIRTY_TRACKER.last_event_timestamp = time.perf_counter()

    now = time.perf_counter()
    debounce_sec = settings.debounce_ms / 1000.0

    if _DIRTY_TRACKER.full_resync_needed:
        trigger_manual_sync(context, full_resync=True)
        return 0.05

    # Fast-path: transform-only updates (throttled at 20 Hz / 50ms even during modal grab)
    if _DIRTY_TRACKER.dirty_transforms and not _DIRTY_TRACKER.dirty_geometry and not newly_hid:
        if (now - _DIRTY_TRACKER.last_sync_timestamp) >= 0.045:
            t0 = time.perf_counter()
            frames = _DIRTY_TRACKER.build_fast_transform_batch(
                context, _DIRTY_TRACKER.dirty_transforms, settings.apply_unit_scale
            )
            _DIRTY_TRACKER.dirty_transforms.clear()
            _DIRTY_TRACKER.last_sync_timestamp = now
            if frames:
                _TCP_CLIENT.enqueue_frames(frames)
                settings.stat_last_sync_ms = (time.perf_counter() - t0) * 1000.0
        return 0.03

    # Geometry or visibility updates after debounce
    if (_DIRTY_TRACKER.dirty_geometry or newly_hid) and (now - _DIRTY_TRACKER.last_event_timestamp) >= debounce_sec:
        trigger_manual_sync(context, full_resync=False)
        _DIRTY_TRACKER.last_sync_timestamp = now

    return 0.05


_CLASSES = (
    BLENDERSYNC_PG_AttributeMapItem,
    BLENDERSYNC_PG_MaterialModeItem,
    BLENDERSYNC_PG_SceneSettings,
    BLENDERSYNC_OT_SyncNow,
    BLENDERSYNC_OT_FullResync,
    BLENDERSYNC_OT_BakeMaterials,
    BLENDERSYNC_OT_RefreshMaterialList,
    BLENDERSYNC_OT_AddAttributeOverride,
    BLENDERSYNC_PT_MainPanel,
)


def register() -> None:
    if bpy.app.version < (5, 0, 0):
        print(f"[BlenderSync] Warning: Target is Blender 5.0+, running on {bpy.app.version}")

    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.blendersync = bpy.props.PointerProperty(type=BLENDERSYNC_PG_SceneSettings)

    if _on_depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update_post)
    if _on_load_or_undo_redo_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_or_undo_redo_post)
    if _on_load_or_undo_redo_post not in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.append(_on_load_or_undo_redo_post)
    if _on_load_or_undo_redo_post not in bpy.app.handlers.redo_post:
        bpy.app.handlers.redo_post.append(_on_load_or_undo_redo_post)
    if _on_frame_change_post not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame_change_post)

    if not bpy.app.timers.is_registered(_timer_sync_tick):
        bpy.app.timers.register(_timer_sync_tick, first_interval=0.1, persistent=True)


def unregister() -> None:
    _TCP_CLIENT.stop()
    if bpy.app.timers.is_registered(_timer_sync_tick):
        bpy.app.timers.unregister(_timer_sync_tick)

    for handler_list, fn in [
        (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update_post),
        (bpy.app.handlers.load_post, _on_load_or_undo_redo_post),
        (bpy.app.handlers.undo_post, _on_load_or_undo_redo_post),
        (bpy.app.handlers.redo_post, _on_load_or_undo_redo_post),
        (bpy.app.handlers.frame_change_post, _on_frame_change_post),
    ]:
        if fn in handler_list:
            handler_list.remove(fn)

    if hasattr(bpy.types.Scene, "blendersync"):
        del bpy.types.Scene.blendersync
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
