# Architectural & Engineering Decisions (DECISIONS.md)

This document records verified API findings against Blender 5.x and Unity 6 (6000.x / URP 17.x) and documents key engineering decisions made for BlenderSync.

## 1. Blender 5.x Python API Verification & Spikes (M0)

### 1.1 `Mesh.corner_normals` vs Removed `calc_normals_split`
- **Finding**: In Blender 4.1+ and 5.0+, `mesh.calc_normals_split()`, `create_normals_split()`, and `free_normals_split()` are removed. Normals are automatically cached and exposed via the `mesh.corner_normals` collection property (`bpy_prop_collection` of `MeshNormalValue`, each having a `vector` of 3 floats).
- **Decision**: Extract corner normals directly via `mesh.corner_normals.foreach_get("vector", buf)` into a flat `float32` NumPy array of length `len(mesh.loops) * 3`.

### 1.2 `Mesh.color_attributes` vs Removed `Mesh.vertex_colors`
- **Finding**: `Mesh.vertex_colors` was removed in Blender 5.0. Both byte colors (`BYTE_COLOR`) and float colors (`FLOAT_COLOR`) live in `mesh.color_attributes` (and `mesh.attributes`). Crucially, `attribute.data.foreach_get("color", ...)` returns **scene-linear** RGBA values for both `BYTE_COLOR` and `FLOAT_COLOR` (`color_srgb` is a separate property).
- **Decision**: Always read `.foreach_get("color", ...)` to obtain scene-linear values and transmit them as linear `Float16x4` (default) or `Float32x4` without any sRGB/gamma conversion.

### 1.3 `Object.evaluated_geometry()` & `bpy.types.GeometrySet` Lifetime
- **Finding**: Blender 4.5/5.0 introduced `eval_obj.evaluated_geometry() -> bpy.types.GeometrySet`, exposing `.mesh`, `.curves`, `.pointcloud`, `.volume`, `.grease_pencil`, `.instances_pointcloud()`, and `.instance_references()`. If the returned `GeometrySet` temporary wrapper is garbage-collected while reading `.mesh`, memory access becomes invalid.
- **Decision**:
  1. Keep a local variable reference `geom_set = eval_obj.evaluated_geometry()` alive for the entire scope of reading components or instance-domain attributes.
  2. Use `depsgraph.object_instances` as the primary unified iterator for all visible objects and flattened instances (GN instances, collection instances, particle instances), and cross-reference `eval_obj.evaluated_geometry().instances_pointcloud()` to extract custom `INSTANCE`-domain attributes produced by Geometry Nodes.
  3. Always pair `eval_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)` with `eval_obj.to_mesh_clear()` inside a `try...finally` block after copying buffers to NumPy.

### 1.4 Edit Mode & Paint Mode Live Evaluation
- **Finding**: In Blender 5.x, while an object is in `EDIT` mode or `VERTEX_PAINT` / `SCULPT` mode, the bmesh/sculpt changes may lag in `eval_obj` unless `obj.update_from_editmode()` is invoked prior to reading the evaluated depsgraph.
- **Decision**: During live sync ticks, if the active object is in `'EDIT'` mode and flagged dirty, call `obj.update_from_editmode()` on the main thread before sampling the evaluated depsgraph.

### 1.5 Material Nodes & `bpy.ops.object.bake` Signature in Blender 5.x
- **Finding**: `material.use_nodes` is legacy/deprecated in Blender 5.x (`material.node_tree` is always present or checked directly for `None`). `bpy.ops.object.bake(type=..., target='IMAGE_TEXTURES' | 'VERTEX_COLORS', pass_filter={'COLOR'}, ...)` is supported when `scene.render.engine == 'CYCLES'`.
- **Decision**: Never check `material.use_nodes`; check `material.node_tree is not None`. Temporarily switch `scene.render.engine` to `'CYCLES'` inside a context guard during explicit user-triggered bakes and restore the previous engine (`BLENDER_EEVEE` in 5.x) in a `finally` block.

### 1.6 Extension Packaging (`blender_manifest.toml`)
- **Finding**: Blender 5.0 extensions require `schema_version = "1.0.0"` and `blender_version_min = "5.0.0"`. Python 3.11 is used in 5.0 and Python 3.13 in 5.1+, so binary wheels are avoided.
- **Decision**: Pure Python + bundled `numpy` only. Declare `[permissions]` for `network` (localhost TCP sync with Unity Editor) and `files` (snapshot export and texture cache).

## 2. Coordinate System & Winding Proof

- **Mapping**: Blender Right-Handed Z-up $(x, y, z)_B \mapsto$ Unity Left-Handed Y-up $(x, z, y)_U$.
- **Matrix Transform**: Let $C = \begin{pmatrix} 1 & 0 & 0 & 0 \\ 0 & 0 & 1 & 0 \\ 0 & 1 & 0 & 0 \\ 0 & 0 & 0 & 1 \end{pmatrix}$. Notice $C = C^T = C^{-1}$ and $\det(C) = -1$.
  - $M_U = C \cdot M_B \cdot C$.
  - Because $\det(C) = -1$, local-space vertex positions undergo a reflection, so triangle indices $(i_0, i_1, i_2)$ are reversed to $(i_0, i_2, i_1)$ and tangent handedness $w$ is negated ($w_U = -w_B$).
  - When an object has a negative scale in Blender ($\det(M_B) < 0$), $\det(M_U) = (-1)\det(M_B)(-1) = \det(M_B) < 0$. Unity's renderer automatically flips backface culling for negative-determinant transforms, so local mesh winding must NOT be double-flipped per instance.

## 3. Unity 6 & URP 17.x Decisions

### 3.1 Zero-Computation Mesh Upload (`MeshDataArray`)
- **Decision**: Use `Mesh.AllocateWritableMeshData(1)`, configure exact `VertexAttributeDescriptor[]` matching the wire layout produced by Blender, copy raw vertex/index bytes directly via `NativeArray<byte>.CopyFrom`, set submeshes and precomputed bounds with `MeshUpdateFlags.DontRecalculateBounds | MeshUpdateFlags.DontValidateIndices | MeshUpdateFlags.DontNotifyMeshUsers`, and apply with `Mesh.ApplyAndDisposeWritableMeshData`.

### 3.2 GPU Resident Drawer & Instancing Threshold
- **Decision**: Below the threshold (default 2,000 instances per mesh+material), create pooled child `GameObject`s with `MeshFilter` + `MeshRenderer` and **no** `MaterialPropertyBlock` (unless custom instance attributes are explicitly active), ensuring 100% compatibility with both SRP Batcher and Unity 6's `GPUResidentDrawerMode.InstancedDrawing`. Above the threshold, render via `BlenderInstanceRenderer` using `Graphics.RenderMeshInstanced` in batches of 1,023 with CPU frustum culling per spatial chunk.
