using System;
using UnityEngine;

namespace BlenderSync
{
    public enum SyncConnectionState
    {
        Disconnected = 0,
        Live = 1,
        Cached = 2,
        Stale = 3,
        Error = 4
    }

    public enum SyncPersistenceMode
    {
        LiveDontSave = 0,
        PersistedInScene = 1
    }

    /// <summary>
    /// Anchor component placed in a Unity 6 URP scene (Section 4.5 & 7.3).
    /// Owns the "BlenderMap" root GameObject and never touches scene lighting, skybox,
    /// fog, Volumes, or cameras (Requirement R6).
    /// </summary>
    [ExecuteAlways]
    [DisallowMultipleComponent]
    [AddComponentMenu("BlenderSync/Blender Map Link")]
    public sealed class BlenderMapLink : MonoBehaviour
    {
        [Header("Source .blend Link")]
        [Tooltip("Path to the source Blender 5.x .blend file (relative to project root or absolute).")]
        public string BlendFilePath = string.Empty;

        [Tooltip("Unique identifier for this link's snapshot cache in Library/BlenderSync/<LinkGuid>.")]
        public string LinkGuid = string.Empty;

        [Header("Network & Security")]
        public int Port = 52731;
        public string AuthToken = string.Empty;

        [Header("Sync & Persistence")]
        public SyncPersistenceMode PersistenceMode = SyncPersistenceMode.LiveDontSave;
        public bool AutoRefreshStaleCacheOnOpen = true;
        public bool KeepMeshesReadable;
        public int InstanceGameObjectThreshold = 2000;

        [Header("Opt-in Imports (Default Off to Protect Atmosphere)")]
        public bool ImportLights;
        public bool ImportCameras;
        public bool ImportEmpties;

        [Header("Assets & Mappings")]
        public MaterialMapping MaterialMappingAsset;
        public MeshAttributeMap AttributeMapAsset;
        public Transform MapRootTransform;

        [Header("Telemetry (Read-Only)")]
        public SyncConnectionState ConnectionState = SyncConnectionState.Disconnected;
        public ulong LastRevision;
        public int SyncedObjectCount;
        public long SyncedTriangleCount;
        public int SyncedInstanceCount;
        public float LastSyncDurationMs;
        public string LastStatusMessage = "Ready";

        public static event Action<BlenderMapLink> OnLinkEnabled;
        public static event Action<BlenderMapLink> OnLinkDisabled;

        private void OnEnable()
        {
            if (string.IsNullOrEmpty(LinkGuid))
            {
                LinkGuid = Guid.NewGuid().ToString("N");
            }
            EnsureMapRoot();
            OnLinkEnabled?.Invoke(this);
        }

        private void OnDisable()
        {
            OnLinkDisabled?.Invoke(this);
        }

        public Transform EnsureMapRoot()
        {
            if (MapRootTransform != null)
                return MapRootTransform;

            Transform existing = transform.Find("BlenderMap");
            if (existing != null)
            {
                MapRootTransform = existing;
            }
            else
            {
                var rootGo = new GameObject("BlenderMap");
                rootGo.transform.SetParent(transform, false);
                MapRootTransform = rootGo.transform;
            }

            UpdateRootHideFlags();
            return MapRootTransform;
        }

        public void UpdateRootHideFlags()
        {
            if (MapRootTransform == null)
                return;

            HideFlags flags = PersistenceMode == SyncPersistenceMode.LiveDontSave
                ? (HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild)
                : HideFlags.None;

            MapRootTransform.gameObject.hideFlags = flags;
        }
    }
}
