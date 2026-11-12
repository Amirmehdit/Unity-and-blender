using System;
using System.Collections.Generic;
using UnityEngine;

namespace BlenderSync
{
    [Serializable]
    public struct AttributeChannelBinding
    {
        public string Name;
        public string DataType;
        public string Domain;
        public string TargetSemantic;
        public int UvChannel;
        public int ComponentOffset;
        public int ComponentCount;
    }

    /// <summary>
    /// Stores the read-only attribute mapping table produced by Blender (Section 5.4)
    /// so inspectors and debug shaders can inspect where each Geometry Nodes or color
    /// attribute resides in TEXCOORD2..TEXCOORD7 / COLOR.
    /// </summary>
    [CreateAssetMenu(fileName = "MeshAttributeMap", menuName = "BlenderSync/Mesh Attribute Map")]
    public sealed class MeshAttributeMap : ScriptableObject
    {
        public List<AttributeChannelBinding> Attributes = new List<AttributeChannelBinding>();

        [Serializable]
        private sealed class ManifestJsonWrapper
        {
            public List<AttributeChannelBinding> attributes = new List<AttributeChannelBinding>();
        }

        public void LoadFromJson(string jsonManifest)
        {
            Attributes.Clear();
            if (string.IsNullOrWhiteSpace(jsonManifest))
                return;

            // Convert snake_case JSON keys to PascalCase for JsonUtility
            string normalized = jsonManifest
                .Replace("\"name\"", "\"Name\"")
                .Replace("\"data_type\"", "\"DataType\"")
                .Replace("\"domain\"", "\"Domain\"")
                .Replace("\"target_semantic\"", "\"TargetSemantic\"")
                .Replace("\"uv_channel\"", "\"UvChannel\"")
                .Replace("\"component_offset\"", "\"ComponentOffset\"")
                .Replace("\"component_count\"", "\"ComponentCount\"");

            var wrapper = JsonUtility.FromJson<ManifestJsonWrapper>(normalized);
            if (wrapper != null && wrapper.attributes != null)
            {
                Attributes.AddRange(wrapper.attributes);
            }
        }
    }
}
