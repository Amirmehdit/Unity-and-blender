using UnityEditor;
using UnityEngine;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Diagnostic window (`Window > BlenderSync > Debug`) displaying synced objects,
    /// Attribute Map bindings (`TEXCOORD2..7`), URP / GPU Resident Drawer status check,
    /// and a global shader debug visualization switcher (Section 7.6).
    /// </summary>
    public sealed class BlenderSyncDebugWindow : EditorWindow
    {
        private string _searchFilter = string.Empty;
        private Vector2 _scrollPos;
        private int _selectedDebugChannel;
        private static readonly string[] DebugChannelNames =
        {
            "Off (Normal Shading)", "COLOR (Linear Vertex Color)", "NORMAL", "TANGENT",
            "UV0", "UV1 (Lightmap)", "UV2 (Custom Attr 0)", "UV3 (Custom Attr 1)",
            "UV4", "UV5", "UV6", "UV7"
        };

        [MenuItem("Window/BlenderSync/Debug")]
        public static void OpenWindow()
        {
            var win = GetWindow<BlenderSyncDebugWindow>("BlenderSync Debug");
            win.minSize = new Vector2(460, 360);
            win.Show();
        }

        private void OnGUI()
        {
            EditorGUILayout.Space(4);
            EditorGUILayout.LabelField("URP & GPU Resident Drawer Diagnostics", EditorStyles.boldLabel);
            UrpSettingsHelper.UrpStatusReport urpReport = UrpSettingsHelper.InspectCurrentSettings();
            EditorGUILayout.HelpBox(urpReport.StatusSummary, urpReport.IsUrpActive && urpReport.IsLinearColorSpace ? MessageType.Info : MessageType.Warning);
            if (GUILayout.Button("Check & Optimize URP Settings"))
            {
                UrpSettingsHelper.PromptAndOptimizeUrpSettings();
            }

            EditorGUILayout.Space(6);
            EditorGUILayout.LabelField("Attribute Visualization Mode", EditorStyles.boldLabel);
            int newChannel = EditorGUILayout.Popup("Channel Override", _selectedDebugChannel, DebugChannelNames);
            if (newChannel != _selectedDebugChannel)
            {
                _selectedDebugChannel = newChannel;
                ApplyShaderDebugOverride(_selectedDebugChannel);
            }

            var link = FindFirstObjectByType<BlenderMapLink>();
            if (link == null)
            {
                EditorGUILayout.HelpBox("No BlenderMapLink found in the active scene.", MessageType.Info);
                return;
            }

            if (link.AttributeMapAsset != null && link.AttributeMapAsset.Attributes.Count > 0)
            {
                EditorGUILayout.Space(6);
                EditorGUILayout.LabelField("Mapped Geometry Nodes & Color Attributes", EditorStyles.boldLabel);
                foreach (AttributeChannelBinding b in link.AttributeMapAsset.Attributes)
                {
                    EditorGUILayout.LabelField(
                        $"• {b.Name} ({b.DataType}, {b.Domain}) → {b.TargetSemantic} [comp {b.ComponentOffset}..{b.ComponentOffset + b.ComponentCount - 1}]");
                }
            }

            EditorGUILayout.Space(6);
            EditorGUILayout.LabelField("Synced Objects", EditorStyles.boldLabel);
            _searchFilter = EditorGUILayout.TextField("Search Filter", _searchFilter);

            _scrollPos = EditorGUILayout.BeginScrollView(_scrollPos);
            if (link.MapRootTransform != null)
            {
                var ids = link.MapRootTransform.GetComponentsInChildren<BlenderObjectId>(true);
                foreach (BlenderObjectId bid in ids)
                {
                    if (bid.IsSpatialChunk)
                        continue;
                    if (!string.IsNullOrEmpty(_searchFilter) &&
                        !bid.BlenderObjectName.Contains(_searchFilter, System.StringComparison.OrdinalIgnoreCase))
                        continue;

                    EditorGUILayout.BeginHorizontal("box");
                    EditorGUILayout.LabelField(bid.BlenderObjectName, GUILayout.Width(180));
                    EditorGUILayout.LabelField(bid.CollectionPath, EditorStyles.miniLabel);
                    if (GUILayout.Button("Select", GUILayout.Width(55)))
                    {
                        Selection.activeGameObject = bid.gameObject;
                    }
                    if (GUILayout.Button("Ping Blender", GUILayout.Width(90)))
                    {
                        BlenderSyncServer.PingObjectInBlender(bid.StableId);
                    }
                    EditorGUILayout.EndHorizontal();
                }
            }
            EditorGUILayout.EndScrollView();
        }

        private static void ApplyShaderDebugOverride(int modeIndex)
        {
            var link = FindFirstObjectByType<BlenderMapLink>();
            if (link == null || link.MapRootTransform == null)
                return;

            Shader debugShader = Shader.Find("BlenderSync/AttributeDebug");
            Shader litShader = Shader.Find("BlenderSync/Lit");
            if (debugShader == null || litShader == null)
                return;

            var renderers = link.MapRootTransform.GetComponentsInChildren<MeshRenderer>(true);
            foreach (MeshRenderer mr in renderers)
            {
                foreach (Material mat in mr.sharedMaterials)
                {
                    if (mat == null) continue;
                    if (modeIndex == 0)
                    {
                        if (mat.shader == debugShader)
                            mat.shader = litShader;
                    }
                    else
                    {
                        mat.shader = debugShader;
                        mat.SetFloat("_DebugChannel", modeIndex - 1);
                    }
                }
            }
            SceneView.RepaintAll();
        }
    }
}
