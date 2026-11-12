"""
Blender 3D Viewport Sidebar UI ("Unity Sync" tab) and Operators (Section 5.8).
"""

from __future__ import annotations
import bpy
from bpy.types import Operator, Panel


class BLENDERSYNC_OT_SyncNow(Operator):
    bl_idname = "blendersync.sync_now"
    bl_label = "Sync Now"
    bl_description = "Immediately extract and send incremental updates to Unity"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import trigger_manual_sync
        trigger_manual_sync(context, full_resync=False)
        return {"FINISHED"}


class BLENDERSYNC_OT_FullResync(Operator):
    bl_idname = "blendersync.full_resync"
    bl_label = "Full Resync"
    bl_description = "Clear remote cache state and send the entire visible map to Unity"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from . import trigger_manual_sync
        trigger_manual_sync(context, full_resync=True)
        return {"FINISHED"}


class BLENDERSYNC_OT_BakeMaterials(Operator):
    bl_idname = "blendersync.bake_materials"
    bl_label = "Bake Materials (Cycles)"
    bl_description = "Explicitly bake procedural materials on selected or visible objects via Cycles"

    def execute(self, context: bpy.types.Context) -> set[str]:
        from .bake import bake_object_materials_cycles
        from . import trigger_manual_sync

        settings = context.scene.blendersync
        targets = list(context.selected_objects) if context.selected_objects else list(context.scene.objects)
        baked_count = 0
        for obj in targets:
            if getattr(obj, "type", "") == "MESH":
                bake_object_materials_cycles(
                    context,
                    obj,
                    target=settings.bake_target,
                    resolution=settings.bake_resolution,
                )
                baked_count += 1
        self.report({"INFO"}, f"Baked materials on {baked_count} mesh object(s)")
        trigger_manual_sync(context, full_resync=True)
        return {"FINISHED"}


class BLENDERSYNC_OT_RefreshMaterialList(Operator):
    bl_idname = "blendersync.refresh_materials"
    bl_label = "Refresh Material List"

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.blendersync
        existing = {item.material_name: item.strategy for item in settings.material_modes}
        settings.material_modes.clear()
        for mat in bpy.data.materials:
            item = settings.material_modes.add()
            item.material_name = mat.name
            item.strategy = existing.get(mat.name, "AUTO")
        return {"FINISHED"}


class BLENDERSYNC_OT_AddAttributeOverride(Operator):
    bl_idname = "blendersync.add_attribute_override"
    bl_label = "Add Attribute Override"

    def execute(self, context: bpy.types.Context) -> set[str]:
        settings = context.scene.blendersync
        item = settings.attribute_map.add()
        item.name = "custom_attr"
        item.uv_channel = 2
        item.component_offset = 0
        return {"FINISHED"}


class BLENDERSYNC_PT_MainPanel(Panel):
    bl_label = "BlenderSync (Unity 6 URP)"
    bl_idname = "BLENDERSYNC_PT_MainPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Unity Sync"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        settings = context.scene.blendersync
        from . import get_tcp_client

        client = get_tcp_client()
        box = layout.box()
        row = box.row(align=True)
        status_text = "CONNECTED (LIVE)" if client.is_connected else "DISCONNECTED"
        row.label(text=f"Status: {status_text}", icon="LINKED" if client.is_connected else "UNLINKED")
        box.prop(settings, "live_sync_enabled", toggle=True)

        col = box.column(align=True)
        col.prop(settings, "host")
        col.prop(settings, "port")
        col.prop(settings, "token")
        col.prop(settings, "snapshot_cache_path")

        row = layout.row(align=True)
        row.operator("blendersync.sync_now", icon="FILE_REFRESH")
        row.operator("blendersync.full_resync", icon="EXPORT")

        # Visibility Section
        vis_box = layout.box()
        vis_box.label(text="Visibility & Filters", icon="RESTRICT_VIEW_OFF")
        vis_box.prop(settings, "filter_mode")
        if settings.filter_mode == "COLLECTIONS":
            vis_box.prop(settings, "collection_filter_csv")
        vis_box.prop(settings, "include_wire_bounds")
        vis_box.prop(settings, "respect_hide_render")
        row = vis_box.row(align=True)
        row.prop(settings, "include_lights", text="Lights")
        row.prop(settings, "include_cameras", text="Cameras")
        row.prop(settings, "include_empties", text="Empties")

        # Mesh & Instancing Section
        mesh_box = layout.box()
        mesh_box.label(text="Geometry & Instancing", icon="MESH_DATA")
        mesh_box.prop(settings, "apply_unit_scale")
        mesh_box.prop(settings, "enable_chunking")
        if settings.enable_chunking:
            row = mesh_box.row(align=True)
            row.prop(settings, "chunk_tri_threshold")
            row.prop(settings, "chunk_cell_size")
        mesh_box.prop(settings, "instance_threshold")
        mesh_box.prop(settings, "tangent_uv_name")
        mesh_box.prop(settings, "lightmap_uv_name")

        # Attribute Map Section
        attr_box = layout.box()
        row = attr_box.row()
        row.label(text="Attribute Map (TEXCOORD2..7)", icon="GROUP_VCOL")
        row.operator("blendersync.add_attribute_override", text="", icon="ADD")
        for item in settings.attribute_map:
            r = attr_box.row(align=True)
            r.prop(item, "name", text="")
            r.prop(item, "uv_channel", text="UV")
            r.prop(item, "component_offset", text="Comp")

        # Materials & Baking Section
        mat_box = layout.box()
        row = mat_box.row()
        row.label(text="Materials & Cycles Bake", icon="MATERIAL")
        row.operator("blendersync.refresh_materials", text="", icon="FILE_REFRESH")
        for mitem in settings.material_modes:
            r = mat_box.row(align=True)
            r.label(text=mitem.material_name)
            r.prop(mitem, "strategy", text="")
        mat_box.prop(settings, "bake_target")
        mat_box.prop(settings, "bake_resolution")
        mat_box.operator("blendersync.bake_materials", icon="RENDER_STILL")

        # Telemetry & Errors
        stat_box = layout.box()
        stat_box.label(text="Telemetry", icon="INFO")
        stat_box.label(
            text=f"Objs: {settings.stat_objects} | Tris: {settings.stat_triangles} | Insts: {settings.stat_instances}"
        )
        stat_box.label(
            text=f"Last Sync: {settings.stat_last_sync_ms:.1f} ms | Sent: {client.bytes_sent // 1024} KB"
        )
        err = settings.last_error or client.last_error
        if err:
            stat_box.label(text=f"Error: {err}", icon="ERROR")
