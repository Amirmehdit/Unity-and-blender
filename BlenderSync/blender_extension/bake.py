"""
Explicit Cycles material baking to Image Textures or Linear Color Attributes (Section 5.6 Mode 3).
Never runs automatically during live sync; invoked only by user action ("Bake Materials").
Caches bakes by content hash of (node_tree + mesh + settings).
"""

from __future__ import annotations
import hashlib
import os
from typing import Any
from .materials import _extract_image_bytes_and_hash


def compute_bake_cache_key(obj: Any, material: Any, target: str, resolution: int) -> str:
    hasher = hashlib.blake2b(digest_size=8)
    hasher.update(f"{obj.name}:{material.name if material else ''}:{target}:{resolution}".encode("utf-8"))
    if material and getattr(material, "node_tree", None):
        for node in material.node_tree.nodes:
            hasher.update(f"{node.name}:{node.type};".encode("utf-8"))
    if getattr(obj, "data", None) and hasattr(obj.data, "vertices"):
        hasher.update(f"v{len(obj.data.vertices)}".encode("utf-8"))
    return hasher.hexdigest()


def bake_object_materials_cycles(
    context: Any,
    obj: Any,
    target: str = "IMAGE_TEXTURES",  # 'IMAGE_TEXTURES' or 'VERTEX_COLORS'
    resolution: int = 1024,
    cache_dir: str = "",
    force_rebake: bool = False,
) -> list[dict[str, Any]]:
    """
    Bake procedural materials on `obj` using Cycles (`bpy.ops.object.bake` verified for Blender 5.x).
    - If `target == 'VERTEX_COLORS'`: ensures an active `FLOAT_COLOR` color attribute exists on
      `obj.data.color_attributes` and bakes directly into it (`target='VERTEX_COLORS'`).
    - If `target == 'IMAGE_TEXTURES'`: creates/reuses a bake target image (`<MatName>_BakedBase`),
      temporarily attaches an active `ShaderNodeTexImage` in each material's node tree, runs
      Cycles DIFFUSE color pass bake, saves PNG to `cache_dir`, and removes the temporary node.
    """
    import bpy

    if obj is None or getattr(obj, "type", "") != "MESH" or obj.data is None:
        return []

    scene = context.scene
    prev_engine = scene.render.engine
    prev_active = context.view_layer.objects.active
    prev_selected = [o for o in context.selected_objects]
    extracted_textures: list[dict[str, Any]] = []

    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 16
        scene.cycles.bake_type = "DIFFUSE"
        scene.render.bake.use_pass_direct = False
        scene.render.bake.use_pass_indirect = False
        scene.render.bake.use_pass_color = True

        for o in prev_selected:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        mesh = obj.data

        if target == "VERTEX_COLORS":
            attr_name = "BakedColor"
            if attr_name not in mesh.color_attributes:
                mesh.color_attributes.new(name=attr_name, type="FLOAT_COLOR", domain="CORNER")
            mesh.color_attributes.active_color_name = attr_name
            for idx, ca in enumerate(mesh.color_attributes):
                if ca.name == attr_name:
                    mesh.color_attributes.render_color_index = idx
                    break
            bpy.ops.object.bake(type="DIFFUSE", target="VERTEX_COLORS", pass_filter={"COLOR"})
            return []

        # Target == IMAGE_TEXTURES
        temp_nodes: list[tuple[Any, Any]] = []
        created_images: list[tuple[Any, Any]] = []

        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or getattr(mat, "node_tree", None) is None:
                continue
            cache_key = compute_bake_cache_key(obj, mat, target, resolution)
            if not force_rebake and mat.get("blendersync_bake_key") == cache_key:
                continue

            img_name = f"BSync_{mat.name}_BakedColor"
            img = bpy.data.images.get(img_name)
            if img is None or img.size[0] != resolution or img.size[1] != resolution:
                if img is not None:
                    bpy.data.images.remove(img)
                img = bpy.data.images.new(img_name, width=resolution, height=resolution, alpha=True)

            nt = mat.node_tree
            for n in nt.nodes:
                n.select = False
            tex_node = nt.nodes.new("ShaderNodeTexImage")
            tex_node.image = img
            tex_node.select = True
            nt.nodes.active = tex_node
            temp_nodes.append((nt, tex_node))
            created_images.append((mat, img, cache_key))

        if temp_nodes:
            bpy.ops.object.bake(
                type="DIFFUSE",
                target="IMAGE_TEXTURES",
                pass_filter={"COLOR"},
                margin=4,
            )

        for nt, tex_node in temp_nodes:
            nt.nodes.remove(tex_node)

        for mat, img, cache_key in created_images:
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
                out_path = os.path.join(cache_dir, f"{img.name}.png")
                img.filepath_raw = out_path
                img.file_format = "PNG"
                img.save()
            else:
                img.pack()

            tex_info = _extract_image_bytes_and_hash(img, is_srgb=True)
            if tex_info is not None:
                mat["blendersync_baked_base_hash"] = tex_info["content_hash"]
                mat["blendersync_bake_key"] = cache_key
                extracted_textures.append(tex_info)

    finally:
        scene.render.engine = prev_engine
        for o in context.selected_objects:
            o.select_set(False)
        for o in prev_selected:
            try:
                o.select_set(True)
            except Exception:
                pass
        context.view_layer.objects.active = prev_active

    return extracted_textures
