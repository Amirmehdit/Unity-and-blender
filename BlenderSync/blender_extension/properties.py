"""
Blender 5.x PropertyGroups for persisting Unity Sync configuration, Attribute Map,
and Material Strategy overrides inside the `.blend` file (Section 5.8).
"""

from __future__ import annotations
import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


class BLENDERSYNC_PG_AttributeMapItem(PropertyGroup):
    name: StringProperty(name="Attribute Name", default="")
    uv_channel: IntProperty(name="TEXCOORD Channel", default=2, min=2, max=7)
    component_offset: IntProperty(name="Component Offset", default=0, min=0, max=3)
    use_remap: BoolProperty(name="Remap Range", default=False)
    remap_min: FloatProperty(name="Min", default=0.0)
    remap_max: FloatProperty(name="Max", default=1.0)


class BLENDERSYNC_PG_MaterialModeItem(PropertyGroup):
    material_name: StringProperty(name="Material Name", default="")
    strategy: EnumProperty(
        name="Strategy",
        items=[
            ("AUTO", "Auto-Detect", "Automatically choose strategy from node tree"),
            ("PBR_PASSTHROUGH", "PBR Passthrough", "Direct Principled BSDF parameters and textures"),
            ("VERTEX_COLOR_DRIVEN", "Vertex Color / Attribute", "Driven by linear color or custom attributes"),
            ("BAKED", "Baked (Cycles)", "Bake complex procedural nodes to texture or vertex colors"),
            ("HAND_AUTHORED", "Hand-Authored (Unity)", "Bind by name to an existing Unity Material"),
            ("TRANSPILED_HLSL", "Transpiled HLSL (Experimental)", "Generate URP HLSL from whitelisted nodes"),
        ],
        default="AUTO",
    )


class BLENDERSYNC_PG_SceneSettings(PropertyGroup):
    host: StringProperty(name="Host", default="127.0.0.1")
    port: IntProperty(name="Port", default=52731, min=1024, max=65535)
    token: StringProperty(name="Auth Token", default="")
    snapshot_cache_path: StringProperty(
        name="Snapshot File",
        subtype="FILE_PATH",
        default="",
        description="Optional path to .bsync snapshot file in Unity's Library/BlenderSync folder",
    )
    live_sync_enabled: BoolProperty(
        name="Live Sync",
        default=False,
        description="Stream evaluated map updates to Unity in real time",
    )

    # Visibility & Filtering
    filter_mode: EnumProperty(
        name="Filter",
        items=[
            ("ALL_VISIBLE", "All Visible", "Send all visible objects in the active View Layer"),
            ("SELECTED_ONLY", "Selected Only", "Send only selected visible objects"),
            ("COLLECTIONS", "Specific Collections", "Send only objects inside comma-separated collections"),
        ],
        default="ALL_VISIBLE",
    )
    collection_filter_csv: StringProperty(
        name="Collections CSV",
        default="",
        description="Comma-separated collection names when Filter is Specific Collections",
    )
    include_wire_bounds: BoolProperty(name="Include Wire/Bounds Objects", default=False)
    respect_hide_render: BoolProperty(name="Respect Hide Render", default=False)
    include_lights: BoolProperty(name="Import Lights (Opt-in)", default=False)
    include_cameras: BoolProperty(name="Import Cameras (Opt-in)", default=False)
    include_empties: BoolProperty(name="Import Empties (Opt-in)", default=False)

    # Geometry & Packing
    apply_unit_scale: BoolProperty(name="Apply Scene Unit Scale", default=True)
    enable_chunking: BoolProperty(name="Chunk Large Meshes", default=True)
    chunk_tri_threshold: IntProperty(name="Chunk Threshold (Tris)", default=250000, min=10000)
    chunk_cell_size: FloatProperty(name="Chunk Cell Size (m)", default=64.0, min=4.0)
    instance_threshold: IntProperty(name="Unity Instance Threshold", default=2000, min=1)
    tangent_uv_name: StringProperty(name="Tangent UV Map", default="")
    lightmap_uv_name: StringProperty(name="Lightmap UV Map", default="Lightmap")
    compress_stream: BoolProperty(name="Compress Buffers (zlib)", default=False)

    # Baking
    bake_target: EnumProperty(
        name="Bake Target",
        items=[
            ("IMAGE_TEXTURES", "Image Textures (PNG)", "Bake Cycles passes into per-material textures"),
            ("VERTEX_COLORS", "Linear Color Attribute", "Bake Cycles lighting/color directly into mesh vertex colors"),
        ],
        default="IMAGE_TEXTURES",
    )
    bake_resolution: IntProperty(name="Bake Resolution", default=1024, min=128, max=8192)

    # Scheduling budgets
    debounce_ms: IntProperty(name="Debounce (ms)", default=100, min=10, max=2000)
    tick_budget_ms: IntProperty(name="Tick Budget (ms)", default=8, min=2, max=50)

    # Collections for Attribute Map & Material Strategies
    attribute_map: CollectionProperty(type=BLENDERSYNC_PG_AttributeMapItem)
    active_attribute_index: IntProperty(default=0)
    material_modes: CollectionProperty(type=BLENDERSYNC_PG_MaterialModeItem)
    active_material_index: IntProperty(default=0)

    # Live Telemetry / Stats
    stat_objects: IntProperty(name="Synced Objects", default=0)
    stat_triangles: IntProperty(name="Synced Triangles", default=0)
    stat_instances: IntProperty(name="Synced Instances", default=0)
    stat_last_sync_ms: FloatProperty(name="Last Sync (ms)", default=0.0)
    last_error: StringProperty(name="Last Error", default="")
