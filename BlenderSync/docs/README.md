# BlenderSync — Real-Time Blender 5.x to Unity 6 (URP) Map Pipeline

Real-time evaluated map synchronization from Blender 5.x (Geometry Nodes, linear color attributes, custom attributes, PBR/splat/baked shaders) into existing Unity 6 URP scenes without ever touching your Unity atmosphere.

## Quick Start (Under 10 Steps)

1. **Install the Blender Extension**: In Blender 5.0+, open **Edit > Preferences > Get Extensions**, click the top-right dropdown **Install from Disk...**, and select `blendersync_extension.zip` (or point Blender to `/blender_extension`).
2. **Install the Unity Package**: In Unity 6, open **Window > Package Manager**, click **+ > Add package from disk...**, and select `/unity_package/com.blendersync/package.json`.
3. **Open Your Unity Atmosphere Scene**: Open any existing Unity 6 URP scene containing your lighting, Skybox, Fog, and Volumes.
4. **Create a Map Link**: In Unity's hierarchy, right-click and choose **BlenderSync > Create Blender Map Link** (or add the `BlenderMapLink` component to an empty GameObject).
5. **Link Your `.blend` File**: In the `BlenderMapLink` Inspector, assign your `.blend` map file path. (If Blender is closed, click **Refresh from .blend (Headless)** to immediately import the map in the background).
6. **Enable Live Sync in Blender**: Open the `.blend` file in Blender 5.x, press **N** in the 3D Viewport, open the **Unity Sync** tab, and toggle **Live Sync** ON.
7. **Edit in Real Time**: Move objects, tweak Geometry Nodes parameters, or paint vertex colors in Blender—changes appear in Unity's Scene and Game views in under 300 ms.
8. **Configure Material Strategies**: In the **Unity Sync** panel (or Unity's `MaterialMapping` asset), set each material to `PBR Passthrough`, `Vertex Color Driven`, `Baked`, or `Hand Authored`.
9. **Check GPU Resident Drawer (Optional)**: In Unity, open **Window > BlenderSync > Debug** and click **Check URP Settings** to verify SRP Batcher, Forward+, and GPU Resident Drawer compatibility.
10. **Bake to Scene for Build (Optional)**: When ready to bake lightmaps/APV or build a player, click **Bake to Scene** on the `BlenderMapLink` Inspector to persist `.asset` meshes and static flags.

---

## Color Space & Tonemapping Note
- **Linear Vertex Colors**: Blender's Python API stores both `BYTE_COLOR` and `FLOAT_COLOR` in **scene-linear** space. BlenderSync sends linear floats directly to Unity without gamma conversion, and all `BlenderSync/*` shaders sample vertex colors as linear values (supporting HDR values $> 1.0$). Ensure your Unity project is set to **Linear** Color Space (**Edit > Project Settings > Player > Other Settings > Color Space = Linear**).
- **View Transform Matching**: Blender 5.x defaults to the **AgX** or **Khronos PBR Neutral** view transform, whereas Unity URP defaults to **Neutral** or **ACES** tonemapping on a Global Volume. BlenderSync intentionally never modifies your Unity Volumes (R6). To closely match Blender's Khronos PBR Neutral look in Unity 6, add a **Tonemapping** override to your Unity Volume and select **Neutral**.
