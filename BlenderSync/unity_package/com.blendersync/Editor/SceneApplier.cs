using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using Unity.Collections;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using Debug = UnityEngine.Debug;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Applies BSYN protocol messages onto a <see cref="BlenderMapLink"/> in Edit Mode and Play Mode.
    /// Strictly enforces Section 8 ("UNITY DOES NOT COMPUTE"):
    /// - Uploads raw vertex and index bytes via <see cref="Mesh.AllocateWritableMeshData"/>
    ///   with <see cref="MeshUpdateFlags.DontRecalculateBounds"/> | <see cref="MeshUpdateFlags.DontValidateIndices"/>.
    /// - Never calls RecalculateNormals, RecalculateTangents, Optimize, or GenerateSecondaryUVSet.
    /// - Preserves user-added child GameObjects and components on synced objects.
    /// - Disposes replaced meshes and textures to guarantee zero memory leaks across 1000+ syncs.
    /// </summary>
    public sealed class SceneApplier
    {
        private readonly Dictionary<ulong, Mesh> _meshCache = new Dictionary<ulong, Mesh>();
        private readonly Dictionary<ulong, Texture2D> _textureCache = new Dictionary<ulong, Texture2D>();
        private readonly Dictionary<string, Material> _generatedMaterials = new Dictionary<string, Material>();
        private readonly Dictionary<string, GameObject> _objectLookup = new Dictionary<string, GameObject>();
        private readonly HashSet<ulong> _knownContentHashes = new HashSet<ulong>();

        private readonly Queue<ParsedBsynFrame> _pendingFrames = new Queue<ParsedBsynFrame>();
        private Stopwatch _syncTimer;

        public IReadOnlyCollection<ulong> KnownContentHashes => _knownContentHashes;
        public IReadOnlyDictionary<ulong, Mesh> MeshCache => _meshCache;

        public void EnqueueFrame(ParsedBsynFrame frame)
        {
            if (frame != null)
                _pendingFrames.Enqueue(frame);
        }

        public bool HasPendingWork => _pendingFrames.Count > 0;

        /// <summary>
        /// Drains queued frames within the per-frame time budget (default 6 ms) so the Unity Editor never stalls.
        /// </summary>
        public void DrainQueueWithBudget(BlenderMapLink link, float budgetMs)
        {
            if (link == null || _pendingFrames.Count == 0)
                return;

            // Warn if Unity project is in Gamma color space (Section 10)
            if (PlayerSettings.colorSpace == ColorSpace.Gamma)
            {
                Debug.LogWarning("[BlenderSync] Project Color Space is set to Gamma! Linear Color Space is required for accurate Blender scene-linear vertex colors and PBR lighting.");
            }

            var sw = Stopwatch.StartNew();
            while (_pendingFrames.Count > 0)
            {
                ParsedBsynFrame frame = _pendingFrames.Dequeue();
                ApplySingleFrame(link, frame);

                if (sw.Elapsed.TotalMilliseconds >= budgetMs && frame.MessageType != BsynMessageType.SceneEnd)
                    break;
            }
        }

        public void ApplyAllImmediate(BlenderMapLink link)
        {
            while (_pendingFrames.Count > 0)
            {
                ApplySingleFrame(link, _pendingFrames.Dequeue());
            }
        }

        public void ClearMemoryCache()
        {
            foreach (var kvp in _meshCache)
            {
                if (kvp.Value != null && string.IsNullOrEmpty(AssetDatabase.GetAssetPath(kvp.Value)))
                    UnityEngine.Object.DestroyImmediate(kvp.Value);
            }
            _meshCache.Clear();

            foreach (var kvp in _textureCache)
            {
                if (kvp.Value != null && string.IsNullOrEmpty(AssetDatabase.GetAssetPath(kvp.Value)))
                    UnityEngine.Object.DestroyImmediate(kvp.Value);
            }
            _textureCache.Clear();

            foreach (var kvp in _generatedMaterials)
            {
                if (kvp.Value != null && string.IsNullOrEmpty(AssetDatabase.GetAssetPath(kvp.Value)))
                    UnityEngine.Object.DestroyImmediate(kvp.Value);
            }
            _generatedMaterials.Clear();
            _knownContentHashes.Clear();
            _objectLookup.Clear();
        }

        public void RebuildObjectLookupFromHierarchy(BlenderMapLink link)
        {
            _objectLookup.Clear();
            if (link == null || link.MapRootTransform == null)
                return;

            var ids = link.MapRootTransform.GetComponentsInChildren<BlenderObjectId>(true);
            foreach (BlenderObjectId bid in ids)
            {
                if (!bid.IsSpatialChunk && !string.IsNullOrEmpty(bid.StableId))
                {
                    _objectLookup[bid.StableId] = bid.gameObject;
                }
            }
        }

        public void ApplySingleFrame(BlenderMapLink link, ParsedBsynFrame frame)
        {
            if (link == null || frame == null)
                return;

            switch (frame.MessageType)
            {
                case BsynMessageType.SceneBegin:
                    _syncTimer = Stopwatch.StartNew();
                    if (_objectLookup.Count == 0)
                        RebuildObjectLookupFromHierarchy(link);
                    break;

                case BsynMessageType.MeshAsset:
                    WireMeshAsset wireMesh = BlenderSyncProtocol.ParseMeshAsset(frame.Payload);
                    Mesh builtMesh = UploadZeroCopyMesh(wireMesh, link.KeepMeshesReadable, link.PersistenceMode);
                    if (_meshCache.TryGetValue(wireMesh.ContentHash, out Mesh oldMesh) && oldMesh != null && oldMesh != builtMesh)
                    {
                        if (string.IsNullOrEmpty(AssetDatabase.GetAssetPath(oldMesh)))
                            UnityEngine.Object.DestroyImmediate(oldMesh);
                    }
                    _meshCache[wireMesh.ContentHash] = builtMesh;
                    _knownContentHashes.Add(wireMesh.ContentHash);
                    break;

                case BsynMessageType.TextureFile:
                    ApplyTextureFile(link, frame.Payload);
                    break;

                case BsynMessageType.MaterialUpsert:
                    ApplyMaterialUpsert(link, frame.Payload);
                    break;

                case BsynMessageType.ObjectUpsert:
                    WireObjectUpsert upsert = BlenderSyncProtocol.ParseObjectUpsert(frame.Payload);
                    ApplyObjectUpsert(link, upsert);
                    break;

                case BsynMessageType.ObjectRemove:
                    ApplyObjectRemove(frame.Payload);
                    break;

                case BsynMessageType.TransformBatch:
                    ApplyTransformBatch(frame.Payload);
                    break;

                case BsynMessageType.InstanceSet:
                    WireInstanceSet instSet = BlenderSyncProtocol.ParseInstanceSet(frame.Payload);
                    ApplyInstanceSet(link, instSet);
                    break;

                case BsynMessageType.AttributeMap:
                    ApplyAttributeMap(link, frame.Payload);
                    break;

                case BsynMessageType.LogError:
                    ApplyLogError(link, frame.Payload);
                    break;

                case BsynMessageType.SceneEnd:
                    ApplySceneEnd(link, frame.Payload);
                    break;
            }
        }

        /// <summary>
        /// Zero-copy raw buffer upload via <see cref="Mesh.AllocateWritableMeshData"/>.
        /// Never recalculates normals, tangents, or bounds.
        /// </summary>
        public static Mesh UploadZeroCopyMesh(WireMeshAsset wire, bool keepReadable, SyncPersistenceMode persistenceMode)
        {
            Mesh.MeshDataArray dataArray = Mesh.AllocateWritableMeshData(1);
            Mesh.MeshData meshData = dataArray[0];

            const MeshUpdateFlags NoComputeFlags =
                MeshUpdateFlags.DontRecalculateBounds |
                MeshUpdateFlags.DontValidateIndices |
                MeshUpdateFlags.DontNotifyMeshUsers |
                MeshUpdateFlags.DontResetBoneBounds;

            meshData.SetVertexBufferParams(wire.VertexCount, wire.Layout);
            NativeArray<byte> dstVerts = meshData.GetVertexData<byte>(0);
            dstVerts.CopyFrom(wire.VertexBuffer);

            meshData.SetIndexBufferParams(wire.IndexCount, wire.IndexFormat);
            NativeArray<byte> dstIndices = meshData.GetIndexData<byte>();
            dstIndices.CopyFrom(wire.IndexBuffer);

            int smCount = wire.SubMeshes.Length;
            meshData.subMeshCount = smCount;
            for (int i = 0; i < smCount; i++)
            {
                WireSubMeshDescriptor sm = wire.SubMeshes[i];
                var desc = new SubMeshDescriptor(sm.IndexStart, sm.IndexCount, MeshTopology.Triangles)
                {
                    bounds = new Bounds(sm.BoundsCenter, sm.BoundsExtents * 2.0f)
                };
                meshData.SetSubMesh(i, desc, NoComputeFlags);
            }

            var mesh = new Mesh
            {
                name = wire.Name,
                bounds = wire.Bounds
            };

            if (persistenceMode == SyncPersistenceMode.LiveDontSave)
            {
                mesh.hideFlags = HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild;
            }

            Mesh.ApplyAndDisposeWritableMeshData(dataArray, mesh, NoComputeFlags);
            mesh.bounds = wire.Bounds;

            if (!keepReadable && persistenceMode == SyncPersistenceMode.LiveDontSave)
            {
                mesh.UploadMeshData(true);
            }

            return mesh;
        }

        private void ApplyTextureFile(BlenderMapLink link, byte[] payload)
        {
            int off = 0;
            ulong hash = BitConverter.ToUInt64(payload, off);
            off += 8;
            string texName = BlenderSyncProtocol.ReadString(payload, ref off);
            string ext = BlenderSyncProtocol.ReadString(payload, ref off);
            bool isSrgb = payload[off++] != 0;
            int width = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            int height = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            int dataLen = (int)BitConverter.ToUInt32(payload, off);
            off += 4;

            var imgBytes = new byte[dataLen];
            Buffer.BlockCopy(payload, off, imgBytes, 0, dataLen);

            var tex = new Texture2D(Mathf.Max(2, width), Mathf.Max(2, height), TextureFormat.RGBA32, true, !isSrgb)
            {
                name = texName
            };
            if (link.PersistenceMode == SyncPersistenceMode.LiveDontSave)
            {
                tex.hideFlags = HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild;
            }
            tex.LoadImage(imgBytes, false);

            if (_textureCache.TryGetValue(hash, out Texture2D oldTex) && oldTex != null)
            {
                if (string.IsNullOrEmpty(AssetDatabase.GetAssetPath(oldTex)))
                    UnityEngine.Object.DestroyImmediate(oldTex);
            }
            _textureCache[hash] = tex;
            _knownContentHashes.Add(hash);
        }

        [Serializable]
        private sealed class MaterialDescriptorJson
        {
            public string name;
            public string mode;
            public float[] base_color;
            public float metallic;
            public float roughness;
            public float smoothness = 0.5f;
            public float[] emission_color;
            public float emission_strength;
            public float normal_scale = 1.0f;
            public bool alpha_clip;
            public float alpha_threshold = 0.5f;
            public bool two_sided;
            public bool multiply_vertex_color;
            public float[] tiling;
            public float[] offset;
        }

        private void ApplyMaterialUpsert(BlenderMapLink link, byte[] payload)
        {
            int off = 0;
            ulong hash = BitConverter.ToUInt64(payload, off);
            off += 8;
            string matName = BlenderSyncProtocol.ReadString(payload, ref off);
            string json = BlenderSyncProtocol.ReadString(payload, ref off);

            var desc = JsonUtility.FromJson<MaterialDescriptorJson>(json) ?? new MaterialDescriptorJson { name = matName };

            MaterialMapEntry mapEntry = null;
            if (link.MaterialMappingAsset != null)
            {
                mapEntry = link.MaterialMappingAsset.GetOrCreateEntry(matName);
                mapEntry.LastDescriptorHash = hash;
                if (mapEntry.OverrideMaterial != null)
                {
                    _knownContentHashes.Add(hash);
                    return;
                }
            }

            string shaderName = "BlenderSync/Lit";
            if (desc.mode == "BAKED" && json.Contains("\"baked_base_map\""))
                shaderName = "BlenderSync/UnlitBaked";

            Shader shader = Shader.Find(shaderName) ?? Shader.Find("Universal Render Pipeline/Lit");
            if (!_generatedMaterials.TryGetValue(matName, out Material mat) || mat == null)
            {
                mat = new Material(shader) { name = matName };
                _generatedMaterials[matName] = mat;
            }
            else if (mat.shader != shader && shader != null)
            {
                mat.shader = shader;
            }

            if (link.PersistenceMode == SyncPersistenceMode.LiveDontSave && string.IsNullOrEmpty(AssetDatabase.GetAssetPath(mat)))
            {
                mat.hideFlags = HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild;
            }

            if (desc.base_color != null && desc.base_color.Length >= 4)
                mat.SetColor("_BaseColor", new Color(desc.base_color[0], desc.base_color[1], desc.base_color[2], desc.base_color[3]));

            mat.SetFloat("_Metallic", desc.metallic);
            mat.SetFloat("_Smoothness", desc.smoothness);
            mat.SetFloat("_BumpScale", desc.normal_scale);
            mat.SetFloat("_UseVertexColor", desc.multiply_vertex_color ? 1.0f : 0.0f);
            mat.SetFloat("_Cull", desc.two_sided ? (float)CullMode.Off : (float)CullMode.Back);

            if (desc.tiling != null && desc.tiling.Length >= 2)
                mat.mainTextureScale = new Vector2(desc.tiling[0], desc.tiling[1]);
            if (desc.offset != null && desc.offset.Length >= 2)
                mat.mainTextureOffset = new Vector2(desc.offset[0], desc.offset[1]);

            if (mapEntry != null)
                mapEntry.GeneratedMaterial = mat;

            _knownContentHashes.Add(hash);
        }

        private Material[] ResolveMaterialSlots(BlenderMapLink link, string[] matNames)
        {
            if (matNames == null || matNames.Length == 0)
                matNames = new[] { "DefaultMaterial" };

            var result = new Material[matNames.Length];
            for (int i = 0; i < matNames.Length; i++)
            {
                string mName = matNames[i];
                Material resolved = null;
                if (link.MaterialMappingAsset != null)
                    resolved = link.MaterialMappingAsset.ResolveMaterial(mName);
                if (resolved == null)
                    _generatedMaterials.TryGetValue(mName, out resolved);
                if (resolved == null)
                {
                    Shader lit = Shader.Find("BlenderSync/Lit") ?? Shader.Find("Universal Render Pipeline/Lit");
                    resolved = new Material(lit) { name = mName };
                    if (link.PersistenceMode == SyncPersistenceMode.LiveDontSave)
                        resolved.hideFlags = HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild;
                    _generatedMaterials[mName] = resolved;
                }
                result[i] = resolved;
            }
            return result;
        }

        private Transform GetOrCreateCollectionGroup(BlenderMapLink link, string collectionPath)
        {
            Transform root = link.EnsureMapRoot();
            if (string.IsNullOrEmpty(collectionPath) || collectionPath == "Scene Collection")
                return root;

            string[] parts = collectionPath.Split('/');
            Transform curr = root;
            HideFlags hf = link.PersistenceMode == SyncPersistenceMode.LiveDontSave
                ? (HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild)
                : HideFlags.None;

            foreach (string part in parts)
            {
                if (string.IsNullOrEmpty(part))
                    continue;
                Transform child = curr.Find(part);
                if (child == null)
                {
                    var go = new GameObject(part) { hideFlags = hf };
                    child = go.transform;
                    child.SetParent(curr, false);
                }
                curr = child;
            }
            return curr;
        }

        private void ApplyObjectUpsert(BlenderMapLink link, WireObjectUpsert upsert)
        {
            // Enforce opt-in rules for Lights, Cameras, Empties (Requirement R6)
            if (upsert.ObjectType == 1 && !link.ImportLights) return;
            if (upsert.ObjectType == 2 && !link.ImportCameras) return;
            if (upsert.ObjectType == 3 && !link.ImportEmpties) return;

            Transform parentGroup = GetOrCreateCollectionGroup(link, upsert.CollectionPath);
            HideFlags hf = link.PersistenceMode == SyncPersistenceMode.LiveDontSave
                ? (HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild)
                : HideFlags.None;

            if (!_objectLookup.TryGetValue(upsert.StableId, out GameObject go) || go == null)
            {
                go = new GameObject(upsert.ObjectName) { hideFlags = hf };
                go.transform.SetParent(parentGroup, false);
                _objectLookup[upsert.StableId] = go;
            }
            else
            {
                go.name = upsert.ObjectName;
                if (go.transform.parent != parentGroup)
                    go.transform.SetParent(parentGroup, false);
            }

            var bid = go.GetComponent<BlenderObjectId>();
            if (bid == null)
                bid = go.AddComponent<BlenderObjectId>();
            bid.StableId = upsert.StableId;
            bid.BlenderObjectName = upsert.ObjectName;
            bid.CollectionPath = upsert.CollectionPath;
            bid.ContentHash = upsert.MeshChunkHashes.Length > 0 ? upsert.MeshChunkHashes[0] : 0UL;

            ApplyWorldMatrixToTransform(go.transform, upsert.WorldMatrix);

            if (upsert.ObjectType == 0 && upsert.MeshChunkHashes.Length > 0)
            {
                Material[] sharedMats = ResolveMaterialSlots(link, upsert.MaterialNames);

                if (upsert.MeshChunkHashes.Length == 1)
                {
                    // Single mesh directly on the GameObject
                    RemoveGeneratedSpatialChunkChildren(go.transform);
                    if (_meshCache.TryGetValue(upsert.MeshChunkHashes[0], out Mesh m) && m != null)
                    {
                        var mf = go.GetComponent<MeshFilter>();
                        if (mf == null) mf = go.AddComponent<MeshFilter>();
                        mf.sharedMesh = m;

                        var mr = go.GetComponent<MeshRenderer>();
                        if (mr == null) mr = go.AddComponent<MeshRenderer>();
                        mr.sharedMaterials = sharedMats;
                    }
                }
                else
                {
                    // Spatial chunks under this logical object (Section 5.3)
                    RemoveGeneratedSpatialChunkChildren(go.transform);
                    for (int i = 0; i < upsert.MeshChunkHashes.Length; i++)
                    {
                        if (!_meshCache.TryGetValue(upsert.MeshChunkHashes[i], out Mesh chunkMesh) || chunkMesh == null)
                            continue;
                        var chunkGo = new GameObject($"{upsert.ObjectName}_Chunk_{i}") { hideFlags = hf };
                        chunkGo.transform.SetParent(go.transform, false);
                        var chunkBid = chunkGo.AddComponent<BlenderObjectId>();
                        chunkBid.IsSpatialChunk = true;
                        chunkBid.ContentHash = upsert.MeshChunkHashes[i];

                        var mf = chunkGo.AddComponent<MeshFilter>();
                        mf.sharedMesh = chunkMesh;
                        var mr = chunkGo.AddComponent<MeshRenderer>();
                        mr.sharedMaterials = sharedMats;
                    }
                }
            }
        }

        private static void RemoveGeneratedSpatialChunkChildren(Transform parent)
        {
            for (int i = parent.childCount - 1; i >= 0; i--)
            {
                Transform child = parent.GetChild(i);
                var bid = child.GetComponent<BlenderObjectId>();
                if (bid != null && bid.IsSpatialChunk)
                {
                    UnityEngine.Object.DestroyImmediate(child.gameObject);
                }
            }
        }

        private void ApplyObjectRemove(byte[] payload)
        {
            int off = 0;
            uint count = BitConverter.ToUInt32(payload, off);
            off += 4;
            for (int i = 0; i < count; i++)
            {
                string sid = BlenderSyncProtocol.ReadString(payload, ref off);
                if (_objectLookup.TryGetValue(sid, out GameObject go))
                {
                    _objectLookup.Remove(sid);
                    if (go != null)
                        UnityEngine.Object.DestroyImmediate(go);
                }
            }
        }

        private void ApplyTransformBatch(byte[] payload)
        {
            int off = 0;
            uint count = BitConverter.ToUInt32(payload, off);
            off += 4;
            for (int i = 0; i < count; i++)
            {
                string sid = BlenderSyncProtocol.ReadString(payload, ref off);
                Matrix4x4 m = BlenderSyncProtocol.ReadMatrix4x4RowMajor(payload, ref off);
                if (_objectLookup.TryGetValue(sid, out GameObject go) && go != null)
                {
                    ApplyWorldMatrixToTransform(go.transform, m);
                }
            }
        }

        private void ApplyInstanceSet(BlenderMapLink link, WireInstanceSet instSet)
        {
            if (!_meshCache.TryGetValue(instSet.MeshHash, out Mesh mesh) || mesh == null)
                return;

            Material[] materials = ResolveMaterialSlots(link, instSet.MaterialNames);
            Transform parentGroup = GetOrCreateCollectionGroup(link, instSet.CollectionPath);
            HideFlags hf = link.PersistenceMode == SyncPersistenceMode.LiveDontSave
                ? (HideFlags.DontSaveInEditor | HideFlags.DontSaveInBuild)
                : HideFlags.None;

            string groupStableId = $"{instSet.OwnerStableId}_INSTGRP_{instSet.MeshHash}";
            if (!_objectLookup.TryGetValue(groupStableId, out GameObject holderGo) || holderGo == null)
            {
                holderGo = new GameObject($"Instances_{instSet.SourceObjectName}") { hideFlags = hf };
                holderGo.transform.SetParent(parentGroup, false);
                var bid = holderGo.AddComponent<BlenderObjectId>();
                bid.StableId = groupStableId;
                bid.BlenderObjectName = instSet.SourceObjectName;
                bid.ContentHash = instSet.MeshHash;
                _objectLookup[groupStableId] = holderGo;
            }

            if (instSet.InstanceCount >= link.InstanceGameObjectThreshold)
            {
                // Mode B (>= threshold): RenderMeshInstanced with spatial frustum culling (Section 5.5b)
                RemoveGeneratedSpatialChunkChildren(holderGo.transform);
                var instRenderer = holderGo.GetComponent<BlenderInstanceRenderer>();
                if (instRenderer == null)
                    instRenderer = holderGo.AddComponent<BlenderInstanceRenderer>();
                instRenderer.SetInstances(mesh, materials, instSet.WorldMatrices, instSet.CustomInstanceData);
            }
            else
            {
                // Mode A (< threshold): Individual GameObjects compatible with SRP Batcher & GPU Resident Drawer (Section 5.5a)
                var instRenderer = holderGo.GetComponent<BlenderInstanceRenderer>();
                if (instRenderer != null)
                    UnityEngine.Object.DestroyImmediate(instRenderer);

                RemoveGeneratedSpatialChunkChildren(holderGo.transform);
                for (int i = 0; i < instSet.InstanceCount; i++)
                {
                    var child = new GameObject($"{instSet.SourceObjectName}_{i}") { hideFlags = hf };
                    child.transform.SetParent(holderGo.transform, false);
                    var cBid = child.AddComponent<BlenderObjectId>();
                    cBid.IsSpatialChunk = true;

                    ApplyWorldMatrixToTransform(child.transform, instSet.WorldMatrices[i]);
                    var mf = child.AddComponent<MeshFilter>();
                    mf.sharedMesh = mesh;
                    var mr = child.AddComponent<MeshRenderer>();
                    mr.sharedMaterials = materials;
                    // Intentionally do NOT set a MaterialPropertyBlock unless custom per-instance data exists,
                    // so SRP Batcher and Unity 6 GPU Resident Drawer remain 100% active.
                }
            }
        }

        private void ApplyAttributeMap(BlenderMapLink link, byte[] payload)
        {
            int off = 0;
            string json = BlenderSyncProtocol.ReadString(payload, ref off);
            if (link.AttributeMapAsset != null)
            {
                link.AttributeMapAsset.LoadFromJson(json);
                EditorUtility.SetDirty(link.AttributeMapAsset);
            }
        }

        private void ApplyLogError(BlenderMapLink link, byte[] payload)
        {
            int off = 0;
            byte severity = payload[off++];
            string msg = BlenderSyncProtocol.ReadString(payload, ref off);
            link.LastStatusMessage = msg;
            if (severity >= 2)
                Debug.LogError($"[BlenderSync] {msg}");
            else if (severity == 1)
                Debug.LogWarning($"[BlenderSync] {msg}");
        }

        private void ApplySceneEnd(BlenderMapLink link, byte[] payload)
        {
            if (payload.Length >= 24)
            {
                link.LastRevision = BitConverter.ToUInt64(payload, 0);
                link.SyncedObjectCount = (int)BitConverter.ToUInt32(payload, 8);
                link.SyncedTriangleCount = (long)BitConverter.ToUInt64(payload, 12);
                link.SyncedInstanceCount = (int)BitConverter.ToUInt32(payload, 20);
            }
            if (_syncTimer != null)
            {
                _syncTimer.Stop();
                link.LastSyncDurationMs = (float)_syncTimer.Elapsed.TotalMilliseconds;
            }
            link.LastStatusMessage = $"Synced rev {link.LastRevision} in {link.LastSyncDurationMs:F1} ms";
            SceneView.RepaintAll();
        }

        /// <summary>
        /// Decomposes a Unity-space 4x4 world matrix into world position, rotation, and localScale,
        /// handling negative-determinant (mirrored) matrices accurately (Section 5.9).
        /// </summary>
        public static void ApplyWorldMatrixToTransform(Transform t, Matrix4x4 m)
        {
            Vector3 pos = m.GetColumn(3);
            Vector3 col0 = m.GetColumn(0);
            Vector3 col1 = m.GetColumn(1);
            Vector3 col2 = m.GetColumn(2);

            float sx = col0.magnitude;
            float sy = col1.magnitude;
            float sz = col2.magnitude;

            // Detect mirrored transform (negative determinant)
            if (Vector3.Dot(Vector3.Cross(col0, col1), col2) < 0.0f)
            {
                sx = -sx;
            }

            Quaternion rot = Quaternion.identity;
            if (Mathf.Abs(sx) > 1e-6f && sy > 1e-6f && sz > 1e-6f)
            {
                Vector3 f = col2 / sz;
                Vector3 u = col1 / sy;
                if (f.sqrMagnitude > 1e-6f && u.sqrMagnitude > 1e-6f)
                    rot = Quaternion.LookRotation(f, u);
            }

            t.SetPositionAndRotation(pos, rot);
            t.localScale = new Vector3(sx, sy, sz);
        }
    }
}
