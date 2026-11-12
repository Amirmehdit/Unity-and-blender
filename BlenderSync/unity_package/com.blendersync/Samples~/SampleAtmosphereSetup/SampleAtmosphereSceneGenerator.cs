#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace BlenderSync.Samples
{
    /// <summary>
    /// Creates a sample Unity 6 URP scene pre-populated with a Directional Sun Light, Skybox,
    /// exponential Fog, a Global Volume, Reflection Probe, and a <see cref="BlenderMapLink"/>
    /// to verify Requirement R6 (Atmosphere Untouched).
    /// </summary>
    public static class SampleAtmosphereSceneGenerator
    {
        [MenuItem("Window/BlenderSync/Create Sample Atmosphere Scene")]
        public static void CreateSampleScene()
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            scene.name = "BlenderSync_SampleAtmosphere";

            // 1. Main Camera
            var camGo = new GameObject("Main Camera");
            camGo.tag = "MainCamera";
            var cam = camGo.AddComponent<Camera>();
            camGo.transform.position = new Vector3(0f, 8f, -15f);
            camGo.transform.rotation = Quaternion.Euler(20f, 0f, 0f);

            // 2. Directional Sun Light
            var sunGo = new GameObject("Directional Sun Light");
            var sun = sunGo.AddComponent<Light>();
            sun.type = LightType.Directional;
            sun.color = new Color(1.0f, 0.95f, 0.85f);
            sun.intensity = 2.0f;
            sun.shadows = LightShadows.Soft;
            sunGo.transform.rotation = Quaternion.Euler(45f, -35f, 0f);

            // 3. Atmosphere RenderSettings (Fog & Ambient)
            RenderSettings.sun = sun;
            RenderSettings.fog = true;
            RenderSettings.fogMode = FogMode.ExponentialSquared;
            RenderSettings.fogColor = new Color(0.55f, 0.68f, 0.82f);
            RenderSettings.fogDensity = 0.008f;

            // 4. Global Volume
            var volGo = new GameObject("Global Atmosphere Volume");
            var volume = volGo.AddComponent<Volume>();
            volume.isGlobal = true;
            volume.sharedProfile = ScriptableObject.CreateInstance<VolumeProfile>();

            // 5. Reflection Probe
            var probeGo = new GameObject("Atmosphere Reflection Probe");
            var probe = probeGo.AddComponent<ReflectionProbe>();
            probe.mode = ReflectionProbeMode.Realtime;
            probe.size = new Vector3(100f, 50f, 100f);

            // 6. Blender


// Continuation of unity_package/com.blendersync/Samples~/SampleAtmosphereSetup/SampleAtmosphereSceneGenerator.cs
            // 6. BlenderMapLink Anchor
            var linkGo = new GameObject("BlenderMapLink");
            var link = linkGo.AddComponent<BlenderMapLink>();
            link.EnsureMapRoot();
            Selection.activeGameObject = linkGo;

            Debug.Log("[BlenderSync] Created sample Unity 6 URP Atmosphere Scene with Sun, Fog, Global Volume, Reflection Probe, and BlenderMapLink.");
        }
    }
}
#endif
