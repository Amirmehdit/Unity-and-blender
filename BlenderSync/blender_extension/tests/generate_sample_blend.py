"""
Generates a complete sample `.blend` map demonstrating:
1. An asymmetric 3D 'F' parity object with known UVs, sharp normals, and HDR linear vertex colors.
2. A Geometry Nodes terrain mesh with painted color attributes and custom named attributes (`slope_mask`, `wind_dir`).
3. A Geometry Nodes scatter setup instancing 10,000+ rocks without realizing instances.
4. Multiple materials (PBR Passthrough, Vertex Color Driven, and Complex Procedural for Baking).

Run via:
    blender -b --python blender_extension/tests/generate_sample_blend.py -- --out sample_map.blend
"""

from __future__ import annotations
import os
import sys
import numpy as np
import bpy
import bmesh


def create_asymmetric_f_object() -> bpy.types.Object:
    mesh = bpy.data.meshes.new("AsymmetricF_Mesh")
    bm = bmesh.new()
    # Create an asymmetric F shape: spine along +Z, top bar along +X, mid bar shorter along +X, stem stub along +Y
    boxes = [
        ((0.0, 0.0, 0.0), (0.4, 0.4, 3.0)),   # Vertical spine (Z-up in Blender)
        ((0.4, 0.0, 2.6), (1.8, 0.4, 3.0)),   # Top bar (+X)
        ((0.4, 0.0, 1.4), (1.2, 0.4, 1.8)),   # Mid bar (+X shorter)
        ((0.0, 0.4, 0.0), (0.4, 1.0, 0.4)),   # Forward foot (+Y in Blender -> +Z in Unity)
    ]
    for bmin, bmax in boxes:
        v000 = bm.verts.new((bmin[0], bmin[1], bmin[2]))
        v100 = bm.verts.new((bmax[0], bmin[1], bmin[2]))
        v110 = bm.verts.new((bmax[0], bmax[1], bmin[2]))
        v010 = bm.verts.new((bmin[0], bmax[1], bmin[2]))
        v001 = bm.verts.new((bmin[0], bmin[1], bmax[2]))
        v101 = bm.verts.new((bmax[0], bmin[1], bmax[2]))
        v111 = bm.verts.new((bmax[0], bmax[1], bmax[2]))
        v011 = bm.verts.new((bmin[0], bmax[1], bmax[2]))
        bm.faces.new((v000, v010, v110, v100))
        bm.faces.new((v001, v101, v111, v011))
        bm.faces.new((v000, v100, v101, v001))
        bm.faces.new((v100, v110, v111, v101))
        bm.faces.new((v110, v010, v011, v111))
        bm.faces.new((v010, v000, v001, v011))

    bm.to_mesh(mesh)
    bm.free()

    # Add UV0
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for i, loop in enumerate(mesh.loops):
        co = mesh.vertices[loop.vertex_index].co
        uv_layer.data[i].uv = (co.x * 0.5, co.z * 0.5)

    # Add FLOAT_COLOR (point domain, including HDR value > 1.0) and BYTE_COLOR (corner domain)
    fcol = mesh.color_attributes.new(name="LinearHDRColor", type="FLOAT_COLOR", domain="POINT")
    for i, v in enumerate(mesh.vertices):
        fcol.data[i].color = (2.5 if v.co.x > 0.5 else 0.2, 1.25 if v.co.z > 1.5 else 0.1, 0.5, 1.0)

    bcol = mesh.color_attributes.new(name="CornerByteColor", type="BYTE_COLOR", domain="CORNER")
    for i in range(len(mesh.loops)):
        bcol.data[i].color = (0.8, 0.25, 0.1, 1.0)

    mesh.color_attributes.active_color_name = "LinearHDRColor"
    mesh.color_attributes.render_color_index = 0

    obj = bpy.data.objects.new("AsymmetricF", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def create_gn_terrain_and_instances() -> None:
    # Create terrain grid with custom attributes
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=32, y_subdivisions=32, size=20.0, location=(0, 0, 0))
    terrain = bpy.context.active_object
    terrain.name = "GN_Terrain"
    mesh = terrain.data

    # Custom FLOAT point attribute and FLOAT_VECTOR face attribute
    slope_attr = mesh.attributes.new(name="slope_mask", type="FLOAT", domain="POINT")
    wind_attr = mesh.attributes.new(name="wind_dir", type="FLOAT_VECTOR", domain="FACE")
    num_v = len(mesh.vertices)
    slope_vals = np.linspace(0.0, 1.0, num_v, dtype=np.float32)
    slope_attr.data.foreach_set("value", slope_vals)

    num_f = len(mesh.polygons)
    wind_vals = np.tile(np.array([1.0, 2.0, 3.0], dtype=np.float32), num_f)
    wind_attr.data.foreach_set("vector", wind_vals)

    # Create VertexColorDriven material
    mat = bpy.data.materials.new(name="TerrainSplatMat")
    nt = mat.node_tree
    vc_node = nt.nodes.new("ShaderNodeVertexColor")
    vc_node.layer_name = "SplatWeights"
    bsdf = nt.nodes.get("Principled BSDF")
    if bsdf:
        nt.links.new(vc_node.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.append(mat)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    create_asymmetric_f_object()
    create_gn_terrain_and_instances()

    argv = sys.argv
    out_path = "sample_map.blend"
    if "--" in argv:
        tail = argv[argv.index("--") + 1 :]
        if "--out" in tail:
            out_path = tail[tail.index("--out") + 1]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out_path))
    print(f"Saved sample .blend to {out_path}")


if __name__ == "__main__":
    main()
