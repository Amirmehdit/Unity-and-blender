# Known Limitations (v1.0)

1. **Armatures & Skeletal Skinning**: Skinned meshes (`ArmatureModifier`) are exported in their current evaluated pose as static meshes in v1; bone weights and `SkinnedMeshRenderer` hierarchies are not exported.
2. **Shape Keys (BlendShapes)**: Exported in their current evaluated morph state; dynamic BlendShape sliders are not exported to Unity in v1.
3. **Non-Mesh Evaluated Components**: Raw `Volume` (OpenVDB), `PointCloud` (un-instanced points), and `GreasePencil` v3 components cannot be rendered by standard Unity `MeshRenderer`s and are skipped with an aggregated warning. (Use a `Points to Vertices` / `Instance on Points` or `Curve to Mesh` node in Geometry Nodes to render them in Unity).
4. **Maximum UV/Attribute Channels**: Unity `Mesh` supports 1 `COLOR` channel and 8 `TEXCOORD` channels (`TEXCOORD0..7`), each `float4`. With `UV0` in `TEXCOORD0` and `Lightmap` in `TEXCOORD1`, up to 6 `float4` channels (24 scalar floats or 6 vector/color attributes) can be mapped simultaneously per mesh.
5. **Arbitrary Cycles Procedural Shaders**: Complex Cycles-only shader graphs (e.g., Ambient Occlusion node, Bevel shader node, Ray Portal BSDF) cannot run in real-time HLSL and must use **Mode 3 (Baked)** to bake into textures or vertex colors.
2. Blender 5.x Extension (/blender_extension)
