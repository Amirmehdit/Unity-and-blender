using System.IO;
using UnityEditor;
using UnityEngine;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Custom Inspector for <see cref="BlenderMapLink"/> providing status lights, one-click
    /// Live/Headless/Bake actions, telemetry counters, and atmosphere safety indicators (Section 7.6).
    /// </summary>
    [CustomEditor(typeof(BlenderMapLink))]
    public sealed class BlenderMapLinkEditor : UnityEditor.Editor
    {
        private bool _showLogs = true;

        [MenuItem("GameObject/BlenderSync/Create Blender Map Link", false, 10)]
        public static void CreateBlenderMapLinkMenuItem()
        {
            var go = new GameObject("BlenderMapLink");
            var link = go.AddComponent<BlenderMapLink>();
            link.EnsureMapRoot();
            Selection.activeGameObject = go;
            Undo.RegisterCreatedObjectUndo(go, "Create BlenderMapLink");
        }

        public override void OnInspectorGUI()
        {
            var link = (BlenderMapLink)target;
            serializedObject.Update();

            DrawStatusBanner(link);
            EditorGUILayout.Space(4);

            // .blend file picker
            EditorGUILayout.BeginHorizontal();
            EditorGUILayout.PropertyField(serializedObject.FindProperty("BlendFilePath"), new GUIContent(".blend File"));
            if (GUILayout.Button("Browse...", GUILayout.Width(75)))
            {
                string chosen = EditorUtility.OpenFilePanel("Select Blender 5.x Map File", "", "blend");
                if (!string.IsNullOrEmpty(chosen))
                {
                    link.BlendFilePath = chosen;
                    EditorUtility.SetDirty(link);
                }
            }
            EditorGUILayout.EndHorizontal();

            DrawDefaultInspectorExceptBlendPath();

            EditorGUILayout.Space(6);
            EditorGUILayout.LabelField("Actions", EditorStyles.boldLabel);

            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Button("Sync Now (Live)"))
            {
                BlenderSyncServer.RequestFullResyncFromBlender();
            }
            if (GUILayout.Button("Refresh from .blend (Headless)"))
            {
                SnapshotImporter.RunHeadlessBlenderRefreshAsync(link, BlenderSyncServer.Applier);
            }
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Button("Bake to Scene (.asset)"))
            {
                PersistenceBaker.BakeMapToSceneAssets(link, setStaticFlags: true);
            }
            if (GUILayout.Button("Detach Map"))
            {
                if (EditorUtility.DisplayDialog("Detach BlenderMapLink", "Bake assets to scene and remove the BlenderMapLink component?", "Detach", "Cancel"))
                {
                    PersistenceBaker.DetachMapLink(link);
                    return;
                }
            }
            if (GUILayout.Button("Clear Cache"))
            {
                string dir = SnapshotImporter.GetSnapshotDirectory(link);
                if (Directory.Exists(dir))
                    Directory.Delete(dir, true);
                BlenderSyncServer.Applier.ClearMemoryCache();
                link.LastStatusMessage = "Cleared snapshot and memory cache.";
            }
            EditorGUILayout.EndHorizontal();

            EditorGUILayout.Space(4);
            _showLogs = EditorGUILayout.Foldout(_showLogs, "Telemetry & Log", true);
            if (_showLogs)
            {
                EditorGUILayout.HelpBox(
                    $"Revision: {link.LastRevision}\n" +
                    $"Objects: {link.SyncedObjectCount} | Triangles: {link.SyncedTriangleCount:N0} | Instances: {link.SyncedInstanceCount:N0}\n" +
                    $"Last Apply Time: {link.LastSyncDurationMs:F1} ms\n" +
                    $"Status: {link.LastStatusMessage}",
                    MessageType.Info);
            }

            serializedObject.ApplyModifiedProperties();
        }

        private static void DrawStatusBanner(BlenderMapLink link)
        {
            Color bannerColor = Color.gray;
            string stateLabel = "DISCONNECTED";
            switch (link.ConnectionState)
            {
                case SyncConnectionState.Live:
                    bannerColor = new Color(0.2f, 0.75f, 0.3f);
                    stateLabel = "LIVE (CONNECTED TO BLENDER 5.x)";
                    break;
                case SyncConnectionState.Cached:
                    bannerColor = new Color(0.25f, 0.55f, 0.9f);
                    stateLabel = "CACHED SNAPSHOT LOADED";
                    break;
                case SyncConnectionState.Stale:
                    bannerColor = new Color(0.9f, 0.7f, 0.2f);
                    stateLabel = "STALE CACHE (.blend is newer)";
                    break;
                case SyncConnectionState.Error:
                    bannerColor = new Color(0.85f, 0.25f, 0.25f);
                    stateLabel = "ERROR";
                    break;
            }

            Color prev = GUI.backgroundColor;
            GUI.backgroundColor = bannerColor;
            EditorGUILayout.BeginVertical("box");
            EditorGUILayout.LabelField($"BlenderSync Status: {stateLabel}", EditorStyles.boldLabel);
            EditorGUILayout.EndVertical();
            GUI.backgroundColor = prev;
        }

        private void DrawDefaultInspectorExceptBlendPath()
        {
            SerializedProperty prop = serializedObject.GetIterator();
            bool enterChildren = true;
            while (prop.NextVisible(enterChildren))
            {
                enterChildren = false;
                if (prop.name == "m_Script" || prop.name == "BlendFilePath")
                    continue;
                EditorGUILayout.PropertyField(prop, true);
            }
        }
    }
}
