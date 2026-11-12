using UnityEngine;

namespace BlenderSync
{
    /// <summary>
    /// Attached to every GameObject managed by BlenderSync so objects are traceable across
    /// incremental updates, domain reloads, and Play Mode transitions without destroying
    /// user-added child objects or components (Section 7.2).
    /// </summary>
    [DisallowMultipleComponent]
    [AddComponentMenu("BlenderSync/Blender Object ID")]
    public sealed class BlenderObjectId : MonoBehaviour
    {
        [Tooltip("Stable deterministic identifier from Blender (e.g. OBJ:<name> or INST:<owner>:<source>:<persistent_id>).")]
        public string StableId = string.Empty;

        [Tooltip("Original Blender object name.")]
        public string BlenderObjectName = string.Empty;

        [Tooltip("Blender collection hierarchy path.")]
        public string CollectionPath = string.Empty;

        [Tooltip("64-bit content hash of the applied mesh or instance set.")]
        public ulong ContentHash;

        [Tooltip("True if this child GameObject was generated as a spatial mesh chunk.")]
        public bool IsSpatialChunk;
    }
}
