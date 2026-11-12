# BlenderSync Architecture

## 1. High-Level Data Flow
┌────────────────────────────────────────────────────────────────────────────┐
│ BLENDER 5.x (Main Thread + Background Socket Sender) │
│ │
│ depsgraph_update_post ──► DirtyTracker (IDs: Transform / Geometry / Mat) │
│ │ │
│ bpy.app.timers (8ms budget) ─────▼ │
│ SceneWalker (Visibility & Evaluated Depsgraph + GeometrySet) │
│ ├──► MeshPacker (NumPy foreach_get, Corner Weld, Handedness, Chunks) │
│ ├──► AttributePacker (Linear Color + GN Attributes -> TEXCOORD2..7) │
│ ├──► InstanceExtractor (Persistent IDs, World Matrices, Instance Data) │
│ └──► MaterialAnalyzer / CyclesBaker / NodeTranspiler │
│ │ │
│ BSYN v1 Binary Stream │
└───────────────┬───────────────────────────────────┬────────────────────────┘
│ TCP 127.0.0.1:52731 │ File (.bsync)
▼ ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ UNITY 6 (URP 17.x) │
│ │
│ BlenderSyncServer (Loopback Socket IO Thread -> Frame Queue) │
│ SnapshotImporter / HeadlessExporter (Library/BlenderSync/<GUID>/) │
│ │ │
│ EditorApplication.update (6ms/frame budget) │
│ └──► SceneApplier │
│ ├──► Mesh.AllocateWritableMeshData (Zero-Copy Raw Upload) │
│ ├──► MaterialMapping + URP HLSL Shaders (SRP Batcher + GRD) │
│ ├──► GameObject Diffing by BlenderObjectId (Preserves User Child)│
│ └──► Instance Mode (<2k GameObjects / >=2k RenderMeshInstanced) │
└────────────────────────────────────────────────────────────────────────────┘
Code
## 2. Core Invariants
1. **Zero Computation in Unity**: Unity never recalculates normals, tangents, bounds, or lightmap UVs. All geometry evaluation, welding, tangent calculation, spatial chunking, and coordinate system conversion happens in Blender via vectorized NumPy routines.
2. **Atmosphere Protection (R6)**: Synced content lives exclusively under the `BlenderMap` root owned by `BlenderMapLink`. Scene lighting, `RenderSettings`, `Volume`s, reflection probes, and existing cameras/lights are never touched.
3. **Content-Addressed Deduplication**: Meshes, materials, and textures are hashed with 64-bit Blake2b digests. On reconnect or incremental edits, only unseen hashes are transmitted.
4. **Domain Reload & Play Mode Resilience**: In Live Mode, generated Unity objects use `HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild` so scene files stay tiny and merge-clean, and are reconstructed automatically from the local `.bsync` cache on scene open, domain reload, or Play Mode entry. In Persist Mode ("Bake to Scene"), assets are written with stable GUIDs under `Assets/BlenderSync/<MapName>/`.
