using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Implements Persist Mode ("Bake to Scene") and "Detach" (Section 7.3 & Milestone M6).
    /// Writes generated meshes and materials as `.asset` files under `Assets/BlenderSync/<MapName>/`,
    /// updates existing assets in place to preserve Unity GUIDs, clears `DontSave` flags, and optionally
    /// sets static flags for lightmap / APV baking.
    /// </summary>
    public static class PersistenceBaker
    {
        public static void BakeMapToSceneAssets(BlenderMapLink link, bool setStaticFlags)
        {
            if (link == null || link.MapRootTransform == null)
                return;

            string mapName = string.IsNullOrEmpty(link.BlendFilePath)
                ? link.gameObject.name
                : Path.GetFileNameWithoutExtension(link.BlendFilePath);

            string baseFolder = $"Assets/BlenderSync/{mapName}";
            string meshesFolder = $"{baseFolder}/Meshes";
            string matsFolder = $"{baseFolder}/Materials";

            EnsureAssetFolder("Assets/BlenderSync");
            EnsureAssetFolder(baseFolder);
            EnsureAssetFolder(meshesFolder);
            EnsureAssetFolder(matsFolder);

            link.PersistenceMode = SyncPersistenceMode.PersistedInScene;
            link.UpdateRootHideFlags();

            AssetDatabase.StartAssetEditing();
            try
            {
                // Persist all meshes and materials referenced under MapRootTransform
                var meshFilters = link.MapRootTransform.GetComponentsInChildren<MeshFilter>(true);
                foreach (MeshFilter mf in meshFilters)
                {
                    Mesh m = mf.sharedMesh;
                    if (m == null)
                        continue;

                    m.hideFlags = HideFlags.None;
                    string existingPath = AssetDatabase.GetAssetPath(m);
                    if (string.IsNullOrEmpty(existingPath))
                    {
                        string safeName = SanitizeFileName(m.name);
                        string targetPath = $"{meshesFolder}/{safeName}.asset";
                        Mesh existingAsset = AssetDatabase.LoadAssetAtPath<Mesh>(targetPath);
                        if (existingAsset != null)
                        {
                            EditorUtility.CopySerialized(m, existingAsset);
                            mf.sharedMesh = existingAsset;
                        }
                        else
                        {
                            AssetDatabase.CreateAsset(m, targetPath);
                        }
                    }
                }

                var renderers = link.MapRootTransform.GetComponentsInChildren<MeshRenderer>(true);
                foreach (MeshRenderer mr in renderers)
                {
                    Material[] mats = mr.sharedMaterials;
                    for (int i = 0; i < mats.Length; i++)
                    {
                        Material mat = mats[i];
                        if (mat == null)
                            continue;

                        mat.hideFlags = HideFlags.None;
                        if (string.IsNullOrEmpty(AssetDatabase.GetAssetPath(mat)))
                        {
                            string safeName = SanitizeFileName(mat.name);
                            string targetPath = $"{matsFolder}/{safeName}.mat";
                            Material existingMat = AssetDatabase.LoadAssetAtPath<Material>(targetPath);
                            if (existingMat != null)
                            {
                                EditorUtility.CopySerialized(mat, existingMat);
                                mats[i] = existingMat;
                            }
                            else
                            {
                                AssetDatabase.CreateAsset(mat, targetPath);
                            }
                        }
                    }
                    mr.sharedMaterials = mats;
                }

                // Clear HideFlags on all child GameObjects and optionally assign StaticEditorFlags
                var allTransforms = link.MapRootTransform.GetComponentsInChildren<Transform>(true);
                foreach (Transform t in allTransforms)
                {
                    t.gameObject.hideFlags = HideFlags.None;
                    if (setStaticFlags)
                    {
                        GameObjectUtility.SetStaticEditorFlags(
                            t.gameObject,
                            StaticEditorFlags.ContributeGI |
                            StaticEditorFlags.OccluderStatic |
                            StaticEditorFlags.OccludeeStatic |
                            StaticEditorFlags.ReflectionProbeStatic |
                            StaticEditorFlags.BatchingStatic);
                    }
                }
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
                AssetDatabase.SaveAssets();
                EditorSceneManager.MarkSceneDirty(link.gameObject.scene);
            }
        }

        public static void DetachMapLink(BlenderMapLink link)
        {
            if (link == null)
                return;

            BakeMapToSceneAssets(link, setStaticFlags: false);
            if (link.MapRootTransform != null)
            {
                link.MapRootTransform.SetParent(link.transform.parent, true);
            }
            UnityEngine.Object.DestroyImmediate(link);
        }

        private static void EnsureAssetFolder(string assetPath)
        {
            if (AssetDatabase.IsValidFolder(assetPath))
                return;
            string parent = Path.GetDirectoryName(assetPath)?.Replace('\\', '/') ?? "Assets";
            string folderName = Path.GetFileName(assetPath);
            AssetDatabase.CreateFolder(parent, folderName);
        }

        private static string SanitizeFileName(string name)
        {
            if (string.IsNullOrEmpty(name))
                return "Unnamed";
            foreach (char c in Path.GetInvalidFileNameChars())
            {
                name = name.Replace(c, '_');
            }
            return name;
        }
    }
}
