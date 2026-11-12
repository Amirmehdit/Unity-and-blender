using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace BlenderSync
{
    /// <summary>
    /// Renders high-density Geometry Nodes or Collection instances above the GameObject threshold
    /// using <see cref="Graphics.RenderMeshInstanced"/> in spatial chunks with CPU frustum culling
    /// and per-batch limits (1023 instances per call) in both Edit Mode and Play Mode (Section 5.5b & 7.4).
    /// </summary>
    [ExecuteAlways]
    [DisallowMultipleComponent]
    [AddComponentMenu("BlenderSync/Blender Instance Renderer")]
    public sealed class BlenderInstanceRenderer : MonoBehaviour
    {
        private const int MaxInstancesPerBatch = 1023;
        private static readonly int InstanceDataId = Shader.PropertyToID("_BlenderInstanceData");

        [Serializable]
        public sealed class SpatialCellBatch
        {
            public Bounds WorldBounds;
            public Matrix4x4[] Matrices = Array.Empty<Matrix4x4>();
            public Vector4[] CustomData = Array.Empty<Vector4>();
            [NonSerialized] public MaterialPropertyBlock PropertyBlock;
        }

        public Mesh SourceMesh;
        public Material[] SharedMaterials = Array.Empty<Material>();
        public ShadowCastingMode CastShadows = ShadowCastingMode.On;
        public bool ReceiveShadows = true;
        public float SpatialCellSize = 64.0f;
        public bool HasCustomInstanceData;
        public int TotalInstanceCount;

        [HideInInspector]
        public List<SpatialCellBatch> CellBatches = new List<SpatialCellBatch>();

        private readonly Plane[] _frustumPlanes = new Plane[6];

        public void SetInstances(Mesh mesh, Material[] materials, Matrix4x4[] worldMatrices, Vector4[] customData)
        {
            SourceMesh = mesh;
            SharedMaterials = materials ?? Array.Empty<Material>();
            TotalInstanceCount = worldMatrices != null ? worldMatrices.Length : 0;
            HasCustomInstanceData = customData != null && customData.Length == TotalInstanceCount && TotalInstanceCount > 0;
            CellBatches.Clear();

            if (SourceMesh == null || TotalInstanceCount == 0)
                return;

            float cellSize = Mathf.Max(8.0f, SpatialCellSize);
            var cellGroups = new Dictionary<Vector3Int, List<int>>();
            for (int i = 0; i < TotalInstanceCount; i++)
            {
                Vector3 pos = worldMatrices[i].GetColumn(3);
                var cellKey = new Vector3Int(
                    Mathf.FloorToInt(pos.x / cellSize),
                    Mathf.FloorToInt(pos.y / cellSize),
                    Mathf.FloorToInt(pos.z / cellSize));
                if (!cellGroups.TryGetValue(cellKey, out var list))
                {
                    list = new List<int>();
                    cellGroups[cellKey] = list;
                }
                list.Add(i);
            }

            Vector3 meshExtents = SourceMesh.bounds.extents;
            float maxMeshRadius = meshExtents.magnitude;

            foreach (var kvp in cellGroups)
            {
                List<int> indices = kvp.Value;
                for (int offset = 0; offset < indices.Count; offset += MaxInstancesPerBatch)
                {
                    int count = Mathf.Min(MaxInstancesPerBatch, indices.Count - offset);
                    var batchMats = new Matrix4x4[count];
                    var batchData = HasCustomInstanceData ? new Vector4[count] : Array.Empty<Vector4>();

                    Vector3 bMin = new Vector3(float.MaxValue, float.MaxValue, float.MaxValue);
                    Vector3 bMax = new Vector3(float.MinValue, float.MinValue, float.MinValue);

                    for (int j = 0; j < count; j++)
                    {
                        int srcIdx = indices[offset + j];
                        Matrix4x4 m = worldMatrices[srcIdx];
                        batchMats[j] = m;
                        if (HasCustomInstanceData)
                            batchData[j] = customData[srcIdx];

                        Vector3 p = m.GetColumn(3);
                        float scaleMax = Mathf.Max(
                            m.GetColumn(0).magnitude,
                            Mathf.Max(m.GetColumn(1).magnitude, m.GetColumn(2).magnitude));
                        Vector3 pad = Vector3.one * (maxMeshRadius * scaleMax + 0.5f);
                        bMin = Vector3.Min(bMin, p - pad);
                        bMax = Vector3.Max(bMax, p + pad);
                    }

                    var cellBatch = new SpatialCellBatch
                    {
                        WorldBounds = new Bounds((bMin + bMax) * 0.5f, bMax - bMin),
                        Matrices = batchMats,
                        CustomData = batchData
                    };
                    CellBatches.Add(cellBatch);
                }
            }
        }

        private void OnEnable()
        {
            RenderPipelineManager.beginCameraRendering += OnBeginCameraRendering;
        }

        private void OnDisable()
        {
            RenderPipelineManager.beginCameraRendering -= OnBeginCameraRendering;
        }

        private void OnBeginCameraRendering(ScriptableRenderContext context, Camera cam)
        {
            if (SourceMesh == null || SharedMaterials == null || SharedMaterials.Length == 0 || CellBatches.Count == 0)
                return;

            GeometryUtility.CalculateFrustumPlanes(cam, _frustumPlanes);
            int subMeshCount = Mathf.Min(SourceMesh.subMeshCount, SharedMaterials.Length);

            for (int i = 0; i < CellBatches.Count; i++)
            {
                SpatialCellBatch batch = CellBatches[i];
                if (batch.Matrices == null || batch.Matrices.Length == 0)
                    continue;

                if (!GeometryUtility.TestPlanesAABB(_frustumPlanes, batch.WorldBounds))
                    continue;

                if (HasCustomInstanceData && batch.PropertyBlock == null && batch.CustomData.Length == batch.Matrices.Length)
                {
                    batch.PropertyBlock = new MaterialPropertyBlock();
                    batch.PropertyBlock.SetVectorArray(InstanceDataId, batch.CustomData);
                }

                for (int sm = 0; sm < subMeshCount; sm++)
                {
                    Material mat = SharedMaterials[sm];
                    if (mat == null)
                        continue;

                    var rParams = new RenderParams(mat)
                    {
                        camera = cam,
                        worldBounds = batch.WorldBounds,
                        shadowCastingMode = CastShadows,
                        receiveShadows = ReceiveShadows,
                        layer = gameObject.layer,
                        matProps = batch.PropertyBlock
                    };

                    Graphics.RenderMeshInstanced(rParams, SourceMesh, sm, batch.Matrices, batch.Matrices.Length);
                }
            }
        }
    }
}
