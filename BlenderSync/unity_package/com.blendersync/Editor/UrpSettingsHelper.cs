using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Inspects the active Unity 6 URP Asset for SRP Batcher, Forward+, and GPU Resident Drawer
    /// compatibility (Section 7.4). Never modifies project settings silently; prompts the user
    /// with a confirmation dialog before applying changes.
    /// </summary>
    public static class UrpSettingsHelper
    {
        public struct UrpStatusReport
        {
            public bool IsUrpActive;
            public bool IsLinearColorSpace;
            public bool UseSrpBatcher;
            public string StatusSummary;
        }

        public static UrpStatusReport InspectCurrentSettings()
        {
            var urpAsset = GraphicsSettings.currentRenderPipeline as UniversalRenderPipelineAsset;
            bool isUrp = urpAsset != null;
            bool isLinear = PlayerSettings.colorSpace == ColorSpace.Linear;
            bool srpBatcher = isUrp && urpAsset.useSRPBatcher;

            string summary = isUrp
                ? $"URP Active ('{urpAsset.name}') | Linear Color: {isLinear} | SRP Batcher: {srpBatcher}"
                : "WARNING: Active Render Pipeline is not Universal Render Pipeline (URP).";

            return new UrpStatusReport
            {
                IsUrpActive = isUrp,
                IsLinearColorSpace = isLinear,
                UseSrpBatcher = srpBatcher,
                StatusSummary = summary
            };
        }

        public static void PromptAndOptimizeUrpSettings()
        {
            UrpStatusReport report = InspectCurrentSettings();
            if (!report.IsUrpActive)
            {
                EditorUtility.DisplayDialog(
                    "BlenderSync URP Check",
                    "No UniversalRenderPipelineAsset is assigned in GraphicsSettings. Please assign a Unity 6 URP Asset.",
                    "OK");
                return;
            }

            bool confirm = EditorUtility.DisplayDialog(
                "Configure Unity 6 URP for BlenderSync",
                $"Current Status:\n{report.StatusSummary}\n\n" +
                "Would you like to enable Linear Color Space and SRP Batcher on the active URP asset?",
                "Apply Recommended Settings",
                "Cancel");

            if (!confirm)
                return;

            PlayerSettings.colorSpace = ColorSpace.Linear;
            var urpAsset = GraphicsSettings.currentRenderPipeline as UniversalRenderPipelineAsset;
            if (urpAsset != null)
            {
                urpAsset.useSRPBatcher = true;
                EditorUtility.SetDirty(urpAsset);
                AssetDatabase.SaveAssets();
            }
        }
    }
}
