"""
Blender 5.x Material node-tree inspection, strategy auto-detection, texture extraction,
and optional whitelisted node-to-HLSL URP shader transpiler (Section 5.6 & Milestone M7).

Note: Does NOT rely on removed/legacy `material.use_nodes`; inspects `material.node_tree` directly.
"""

from __future__ import annotations
import hashlib
import json
import os
from typing import Any


class MaterialStrategy:
    AUTO = "AUTO"
    PBR_PASSTHROUGH = "PBR_PASSTHROUGH"
    VERTEX_COLOR_DRIVEN = "VERTEX_COLOR_DRIVEN"
    BAKED = "BAKED"
    HAND_AUTHORED = "HAND_AUTHORED"
    TRANSPILED_HLSL = "TRANSPILED_HLSL"


WHITELISTED_TRANSPILER_NODES = {
    "OUTPUT_MATERIAL",
    "BSDF_PRINCIPLED",
    "TEX_COORD",
    "MAPPING",
    "TEX_IMAGE",
    "VALTORGB",
    "MATH",
    "VECT_MATH",
    "MIX",
    "MIX_RGB",
    "TEX_NOISE",
    "TEX_VORONOI",
    "FRESNEL",
    "VERTEX_COLOR",
    "ATTRIBUTE",
    " NORMAL_MAP",
    "NORMAL_MAP",
    "SEPARATE_COLOR",
    "COMBINE_COLOR",
    "SEPARATE_XYZ",
    "COMBINE_XYZ",
}


def _extract_image_bytes_and_hash(image: Any, is_srgb: bool) -> dict[str, Any] | None:
    """
    Extract encoded PNG/EXR bytes for a Blender Image datablock (packed or on disk)
    and compute its 64-bit content hash.
    """
    if image is None:
        return None
    raw_bytes: bytes | None = None
    ext = ".png"

    try:
        if getattr(image, "packed_file", None) is not None:
            raw_bytes = bytes(image.packed_file.data)
            filepath = getattr(image, "filepath_raw", "") or image.name
            _, guessed_ext = os.path.splitext(filepath)
            if guessed_ext:
                ext = guessed_ext.lower()
        else:
            import bpy
            abspath = bpy.path.abspath(getattr(image, "filepath", ""))
            if abspath and os.path.isfile(abspath):
                _, guessed_ext = os.path.splitext(abspath)
                if guessed_ext:
                    ext = guessed_ext.lower()
                with open(abspath, "rb") as f:
                    raw_bytes = f.read()
    except Exception:
        raw_bytes = None

    if not raw_bytes:
        return None

    hasher = hashlib.blake2b(digest_size=8)
    hasher.update(b"SRGB" if is_srgb else b"LINR")
    hasher.update(raw_bytes)
    content_hash = int.from_bytes(hasher.digest(), byteorder="little", signed=False)
    width = int(image.size[0]) if hasattr(image, "size") and len(image.size) >= 2 else 0
    height = int(image.size[1]) if hasattr(image, "size") and len(image.size) >= 2 else 0

    return {
        "content_hash": content_hash,
        "name": image.name,
        "ext": ext,
        "is_srgb": is_srgb,
        "width": width,
        "height": height,
        "bytes": raw_bytes,
    }


def _trace_upstream_nodes(start_node: Any) -> list[Any]:
    visited: set[Any] = set()
    ordered: list[Any] = []

    def _dfs(node: Any) -> None:
        if node is None or node in visited:
            return
        visited.add(node)
        for inp in getattr(node, "inputs", ()):
            for link in getattr(inp, "links", ()):
                _dfs(getattr(link, "from_node", None))
        ordered.append(node)

    _dfs(start_node)
    return ordered


def _find_connected_image_and_mapping(socket: Any, is_srgb: bool) -> tuple[dict[str, Any] | None, tuple[float, float, float], tuple[float, float, float]]:
    """
    Follow links into `socket` to locate an Image Texture node (and optional Mapping node).
    """
    scale = (1.0, 1.0, 1.0)
    offset = (0.0, 0.0, 0.0)
    if socket is None or not getattr(socket, "is_linked", False):
        return None, scale, offset

    links = getattr(socket, "links", ())
    if not links:
        return None, scale, offset

    from_node = links[0].from_node
    # Step through Normal Map node if present
    if getattr(from_node, "type", "") == "NORMAL_MAP":
        color_inp = from_node.inputs.get("Color")
        if color_inp and color_inp.is_linked:
            from_node = color_inp.links[0].from_node

    if getattr(from_node, "type", "") == "TEX_IMAGE":
        vec_inp = from_node.inputs.get("Vector")
        if vec_inp and vec_inp.is_linked:
            map_node = vec_inp.links[0].from_node
            if getattr(map_node, "type", "") == "MAPPING":
                loc_in = map_node.inputs.get("Location")
                scl_in = map_node.inputs.get("Scale")
                if loc_in:
                    offset = tuple(float(x) for x in loc_in.default_value[:3])
                if scl_in:
                    scale = tuple(float(x) for x in scl_in.default_value[:3])
        tex_info = _extract_image_bytes_and_hash(getattr(from_node, "image", None), is_srgb=is_srgb)
        return tex_info, scale, offset

    return None, scale, offset


def generate_transpiled_hlsl_snippet(material_name: str, nodes: list[Any]) -> str:
    """
    Milestone M7 (Experimental Stretch): Transpiles a whitelisted Blender node chain
    into a self-contained HLSL surface evaluation function for Unity 6 URP.
    """
    lines = [
        f"// Auto-transpiled from Blender 5.x Material '{material_name}'",
        "void EvaluateTranspiledBlenderNodes(float3 worldPos, float3 worldNormal, float2 uv0, float4 vertexColor, float4 attrUV2, out float3 outBaseColor, out float outRoughness, out float outMetallic)",
        "{",
        "    outBaseColor = float3(0.8, 0.8, 0.8);",
        "    outRoughness = 0.5;",
        "    outMetallic = 0.0;",
    ]
    for idx, node in enumerate(nodes):
        ntype = getattr(node, "type", "")
        var = f"n_{idx}"
        if ntype == "VERTEX_COLOR":
            lines.append(f"    float4 {var} = vertexColor;")
            lines.append(f"    outBaseColor = {var}.rgb;")
        elif ntype == "ATTRIBUTE":
            lines.append(f"    float4 {var} = attrUV2;")
        elif ntype == "FRESNEL":
            lines.append(f"    float {var} = pow(1.0 - saturate(dot(worldNormal, float3(0,1,0))), 5.0);")
        elif ntype == "TEX_NOISE":
            lines.append(f"    float {var} = frac(sin(dot(worldPos.xyz, float3(12.9898, 78.233, 37.719))) * 43758.5453);")
        elif ntype == "BSDF_PRINCIPLED":
            bc = node.inputs.get("Base Color")
            rough = node.inputs.get("Roughness")
            met = node.inputs.get("Metallic")
            if bc and not bc.is_linked:
                c = bc.default_value
                lines.append(f"    outBaseColor = float3({float(c[0]):.4f}, {float(c[1]):.4f}, {float(c[2]):.4f});")
            if rough and not rough.is_linked:
                lines.append(f"    outRoughness = {float(rough.default_value):.4f};")
            if met and not met.is_linked:
                lines.append(f"    outMetallic = {float(met.default_value):.4f};")
    lines.append("}")
    return "\n".join(lines)


def analyze_and_export_material(
    material: Any,
    user_mode_override: str = "AUTO",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Inspect a Blender 5.x Material, extract PBR sockets, textures, used color/custom
    attributes, and determine the active material sync strategy.
    Returns (material_packet_dict, list_of_extracted_texture_dicts).
    """
    textures: list[dict[str, Any]] = []
    if material is None:
        desc = {
            "name": "DefaultMaterial",
            "mode": MaterialStrategy.PBR_PASSTHROUGH,
            "base_color": [0.8, 0.8, 0.8, 1.0],
            "metallic": 0.0,
            "roughness": 0.5,
            "smoothness": 0.5,
            "emission_color": [0.0, 0.0, 0.0, 1.0],
            "emission_strength": 0.0,
            "normal_scale": 1.0,
            "alpha_clip": False,
            "alpha_threshold": 0.5,
            "two_sided": False,
            "blend_mode": "OPAQUE",
            "multiply_vertex_color": False,
            "used_color_attributes": [],
            "used_custom_attributes": [],
            "uv_maps": [],
            "textures": {},
            "tiling": [1.0, 1.0],
            "offset": [0.0, 0.0],
        }
        raw_json = json.dumps(desc, sort_keys=True)
        h = int.from_bytes(hashlib.blake2b(raw_json.encode("utf-8"), digest_size=8).digest(), "little")
        return {"content_hash": h, "name": "DefaultMaterial", "json_descriptor": raw_json}, textures

    mat_name = material.name
    node_tree = getattr(material, "node_tree", None)
    used_color_attrs: list[str] = []
    used_custom_attrs: list[str] = []
    uv_maps: list[str] = []
    all_node_types: set[str] = set()

    base_color = list(getattr(material, "diffuse_color", (0.8, 0.8, 0.8, 1.0)))[:4]
    metallic = float(getattr(material, "metallic", 0.0))
    roughness = float(getattr(material, "roughness", 0.5))
    emission_color = [0.0, 0.0, 0.0, 1.0]
    emission_strength = 0.0
    normal_scale = 1.0
    tiling = [1.0, 1.0]
    offset = [0.0, 0.0]
    tex_slots: dict[str, int] = {}
    upstream_nodes: list[Any] = []

    if node_tree is not None and hasattr(node_tree, "nodes"):
        output_node = None
        for n in node_tree.nodes:
            if getattr(n, "type", "") == "OUTPUT_MATERIAL" and getattr(n, "is_active_output", True):
                output_node = n
                break
        if output_node is None:
            for n in node_tree.nodes:
                if getattr(n, "type", "") == "OUTPUT_MATERIAL":
                    output_node = n
                    break

        if output_node is not None:
            upstream_nodes = _trace_upstream_nodes(output_node)
            for n in upstream_nodes:
                ntype = getattr(n, "type", "")
                all_node_types.add(ntype)
                if ntype == "VERTEX_COLOR":
                    layer_name = getattr(n, "layer_name", "")
                    if layer_name:
                        used_color_attrs.append(layer_name)
                elif ntype == "ATTRIBUTE":
                    aname = getattr(n, "attribute_name", "")
                    if aname:
                        used_custom_attrs.append(aname)
                elif ntype == "UVMAP":
                    uvname = getattr(n, "uv_map", "")
                    if uvname:
                        uv_maps.append(uvname)

            # Find Principled BSDF if present
            principled = None
            for n in upstream_nodes:
                if getattr(n, "type", "") == "BSDF_PRINCIPLED":
                    principled = n
                    break

            if principled is not None:
                bc_sock = principled.inputs.get("Base Color")
                if bc_sock:
                    if not bc_sock.is_linked:
                        base_color = [float(x) for x in bc_sock.default_value[:4]]
                    else:
                        t_info, scl, off = _find_connected_image_and_mapping(bc_sock, is_srgb=True)
                        if t_info:
                            textures.append(t_info)
                            tex_slots["base_map"] = t_info["content_hash"]
                            tiling = [scl[0], scl[1]]
                            offset = [off[0], off[1]]

                met_sock = principled.inputs.get("Metallic")
                if met_sock:
                    if not met_sock.is_linked:
                        metallic = float(met_sock.default_value)
                    else:
                        t_info, _, _ = _find_connected_image_and_mapping(met_sock, is_srgb=False)
                        if t_info:
                            textures.append(t_info)
                            tex_slots["metallic_map"] = t_info["content_hash"]

                rough_sock = principled.inputs.get("Roughness")
                if rough_sock:
                    if not rough_sock.is_linked:
                        roughness = float(rough_sock.default_value)
                    else:
                        t_info, _, _ = _find_connected_image_and_mapping(rough_sock, is_srgb=False)
                        if t_info:
                            textures.append(t_info)
                            tex_slots["roughness_map"] = t_info["content_hash"]

                norm_sock = principled.inputs.get("Normal")
                if norm_sock and norm_sock.is_linked:
                    from_n = norm_sock.links[0].from_node
                    if getattr(from_n, "type", "") == "NORMAL_MAP":
                        str_sock = from_n.inputs.get("Strength")
                        if str_sock:
                            normal_scale = float(str_sock.default_value)
                    t_info, _, _ = _find_connected_image_and_mapping(norm_sock, is_srgb=False)
                    if t_info:
                        textures.append(t_info)
                        tex_slots["normal_map"] = t_info["content_hash"]

                em_sock = principled.inputs.get("Emission Color") or principled.inputs.get("Emission")
                em_str_sock = principled.inputs.get("Emission Strength")
                if em_str_sock and not em_str_sock.is_linked:
                    emission_strength = float(em_str_sock.default_value)
                if em_sock:
                    if not em_sock.is_linked:
                        emission_color = [float(x) for x in em_sock.default_value[:4]]
                    else:
                        t_info, _, _ = _find_connected_image_and_mapping(em_sock, is_srgb=True)
                        if t_info:
                            textures.append(t_info)
                            tex_slots["emission_map"] = t_info["content_hash"]

    # Check if baked textures exist on the material from `bake.py`
    baked_base_hash = material.get("blendersync_baked_base_hash", 0)
    if baked_base_hash:
        tex_slots["baked_base_map"] = int(baked_base_hash)

    # Auto-detect strategy if set to AUTO
    simple_pbr_nodes = {"OUTPUT_MATERIAL", "BSDF_PRINCIPLED", "TEX_IMAGE", "NORMAL_MAP", "MAPPING", "TEX_COORD", "UVMAP"}
    is_simple_pbr = all_node_types.issubset(simple_pbr_nodes)

    if user_mode_override and user_mode_override != MaterialStrategy.AUTO:
        resolved_mode = user_mode_override
    elif baked_base_hash:
        resolved_mode = MaterialStrategy.BAKED
    elif used_color_attrs or used_custom_attrs:
        resolved_mode = MaterialStrategy.VERTEX_COLOR_DRIVEN
    elif is_simple_pbr:
        resolved_mode = MaterialStrategy.PBR_PASSTHROUGH
    else:
        resolved_mode = MaterialStrategy.BAKED

    hlsl_code = ""
    if resolved_mode == MaterialStrategy.TRANSPILED_HLSL and upstream_nodes:
        if all_node_types.issubset(WHITELISTED_TRANSPILER_NODES):
            hlsl_code = generate_transpiled_hlsl_snippet(mat_name, upstream_nodes)

    two_sided = not bool(getattr(material, "use_backface_culling", False))
    surface_render_method = getattr(material, "surface_render_method", "DITHERED")
    blend_mode = "BLEND" if surface_render_method == "BLENDED" else "OPAQUE"

    desc = {
        "name": mat_name,
        "mode": resolved_mode,
        "complexity_hint": "SIMPLE" if is_simple_pbr else "PROCEDURAL_COMPLEX",
        "base_color": base_color,
        "metallic": metallic,
        "roughness": roughness,
        "smoothness": max(0.0, min(1.0, 1.0 - roughness)),
        "emission_color": emission_color,
        "emission_strength": emission_strength,
        "normal_scale": normal_scale,
        "alpha_clip": False,
        "alpha_threshold": float(getattr(material, "alpha_threshold", 0.5)),
        "two_sided": two_sided,
        "blend_mode": blend_mode,
        "multiply_vertex_color": bool(used_color_attrs) or resolved_mode == MaterialStrategy.VERTEX_COLOR_DRIVEN,
        "used_color_attributes": used_color_attrs,
        "used_custom_attributes": used_custom_attrs,
        "uv_maps": uv_maps,
        "textures": tex_slots,
        "tiling": tiling,
        "offset": offset,
        "transpiled_hlsl": hlsl_code,
    }
    raw_json = json.dumps(desc, sort_keys=True)
    content_hash = int.from_bytes(hashlib.blake2b(raw_json.encode("utf-8"), digest_size=8).digest(), "little")

    return {
        "content_hash": content_hash,
        "name": mat_name,
        "json_descriptor": raw_json,
    }, textures
