using System;
using System.Collections.Generic;
using UnityEngine;

namespace BlenderSync
{
    public enum BlenderMaterialMode
    {
        Auto = 0,
        PbrPassthrough = 1,
        VertexColorDriven = 2,
        Baked = 3,
        HandAuthored = 4,
        TranspiledHlsl = 5
    }

    [Serializable]
    public sealed class MaterialMapEntry
    {
        public string BlenderMaterialName = string.Empty;
        public BlenderMaterialMode Mode = BlenderMaterialMode.Auto;
        public Material OverrideMaterial;
        public Material GeneratedMaterial;
        public ulong LastDescriptorHash;
    }

    /// <summary>
    /// ScriptableObject mapping Blender material names to Unity material strategies
    /// and preserving user material overrides across re-syncs (Section 7.5).
    /// </summary>
    [CreateAssetMenu(fileName = "BlenderMaterialMapping", menuName = "BlenderSync/Material Mapping")]
    public sealed class MaterialMapping : ScriptableObject
    {
        public List<MaterialMapEntry> Entries = new List<MaterialMapEntry>();
        public string BakedTextureFolder = "Assets/BlenderSync/BakedTextures";

        public MaterialMapEntry GetOrCreateEntry(string blenderMaterialName)
        {
            for (int i = 0; i < Entries.Count; i++)
            {
                if (string.Equals(Entries[i].BlenderMaterialName, blenderMaterialName, StringComparison.Ordinal))
                    return Entries[i];
            }

            var entry = new MaterialMapEntry
            {
                BlenderMaterialName = blenderMaterialName,
                Mode = BlenderMaterialMode.Auto
            };
            Entries.Add(entry);
            return entry;
        }

        public Material ResolveMaterial(string blenderMaterialName)
        {
            for (int i = 0; i < Entries.Count; i++)
            {
                var e = Entries[i];
                if (string.Equals(e.BlenderMaterialName, blenderMaterialName, StringComparison.Ordinal))
                {
                    if (e.OverrideMaterial != null)
                        return e.OverrideMaterial;
                    if (e.GeneratedMaterial != null)
                        return e.GeneratedMaterial;
                }
            }
            return null;
        }
    }
}
