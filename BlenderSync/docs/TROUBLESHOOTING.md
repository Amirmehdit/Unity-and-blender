# Troubleshooting Guide

### 1. Map Does Not Appear When Opening Unity Scene
- Check the status light on `BlenderMapLink`:
  - **Cached (Blue)**: Loaded from `Library/BlenderSync/<GUID>/snapshot.bsync`.
  - **Stale (Yellow)**: The `.blend` file has a newer modification timestamp than the cached snapshot. Click **Refresh from .blend (Headless)** or enable **Auto-Refresh Stale Cache on Open**.
  - **Error (Red)**: Check **Edit > Preferences > BlenderSync** and verify that the **Blender 5.x Executable Path** points to a valid `blender.exe` / `blender` binary.

### 2. Object Is Visible in Blender But Missing in Unity
- Verify the object passes the visibility rules in the **Unity Sync > Visibility** panel:
  - Is the object hidden in the viewport (Eye icon or Monitor `hide_viewport` icon)?
  - Is its `display_type` set to `WIRE` or `BOUNDS`? (Skipped by default; add custom property `unity_force = True` on the object or enable **Include Wire/Bounds** in settings).
  - Does the object or its parent collection have `unity_skip = True`?
  - Is the object a Curve without bevel/extrude or a Volume/PointCloud/GreasePencil? Check the Blender status bar / Unity Sync log for the aggregated unsupported-component warning.

### 3. Shader Graph Cannot Read Custom Attributes on UV4..UV7
- Unity's Shader Graph `UV` node only exposes `UV0` through `UV3`.
- BlenderSync automatically prioritizes custom Geometry Nodes attributes into `TEXCOORD2` (`UV2`) and `TEXCOORD3` (`UV3`) first. You can reorder attribute channel assignments in the Blender **Unity Sync > Attribute Map** list, or use the provided HLSL shaders (`BlenderSync/Lit`, `BlenderSync/VertexColorSplat`, `BlenderSync/AttributeDebug`) which access all channels `TEXCOORD0..TEXCOORD7`.

### 4. Connection Refused or Token Mismatch
- Verify that the **Port** (default `52731`) and **Auth Token** match between the `BlenderMapLink` component in Unity and the **Unity Sync** sidebar in Blender. If `Auth Token` is left blank in both, any local loopback connection is accepted.
