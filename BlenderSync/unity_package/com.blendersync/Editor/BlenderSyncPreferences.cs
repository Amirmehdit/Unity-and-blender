using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Auto-detects Blender 5.x installations across Windows, macOS, and Linux,
    /// and exposes global BlenderSync Preferences in Unity (Section 3 & 7.6).
    /// </summary>
    public static class BlenderSyncPreferences
    {
        private const string PrefBlenderExe = "BlenderSync.BlenderExecutablePath";
        private const string PrefDefaultPort = "BlenderSync.DefaultPort";
        private const string PrefFrameBudgetMs = "BlenderSync.MainThreadBudgetMs";

        public static string BlenderExecutablePath
        {
            get
            {
                string saved = EditorPrefs.GetString(PrefBlenderExe, string.Empty);
                if (!string.IsNullOrEmpty(saved) && File.Exists(saved))
                    return saved;

                string detected = AutoDetectBlenderExecutable();
                if (!string.IsNullOrEmpty(detected))
                {
                    EditorPrefs.SetString(PrefBlenderExe, detected);
                    return detected;
                }
                return saved;
            }
            set => EditorPrefs.SetString(PrefBlenderExe, value ?? string.Empty);
        }

        public static int DefaultPort
        {
            get => EditorPrefs.GetInt(PrefDefaultPort, 52731);
            set => EditorPrefs.SetInt(PrefDefaultPort, Mathf.Clamp(value, 1024, 65535));
        }

        public static float MainThreadBudgetMs
        {
            get => EditorPrefs.GetFloat(PrefFrameBudgetMs, 6.0f);
            set => EditorPrefs.SetFloat(PrefFrameBudgetMs, Mathf.Clamp(value, 1.0f, 33.0f));
        }

        public static string AutoDetectBlenderExecutable()
        {
            var candidates = new List<string>();

            // Windows standard & Steam locations (prefer 5.2, 5.1, 5.0)
            string progFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            if (!string.IsNullOrEmpty(progFiles))
            {
                string bfDir = Path.Combine(progFiles, "Blender Foundation");
                if (Directory.Exists(bfDir))
                {
                    string[] dirs = Directory.GetDirectories(bfDir, "Blender*");
                    Array.Sort(dirs);
                    Array.Reverse(dirs);
                    foreach (string d in dirs)
                    {
                        candidates.Add(Path.Combine(d, "blender.exe"));
                    }
                }
                candidates.Add(Path.Combine(progFiles, "Steam", "steamapps", "common", "Blender", "blender.exe"));
            }

            // macOS & Linux standard locations
            candidates.Add("/Applications/Blender.app/Contents/MacOS/Blender");
            candidates.Add("/usr/bin/blender");
            candidates.Add("/usr/local/bin/blender");
            candidates.Add("/snap/bin/blender");

            foreach (string path in candidates)
            {
                if (File.Exists(path))
                    return path;
            }
            return string.Empty;
        }

        [SettingsProvider]
        public static SettingsProvider CreateSettingsProvider()
        {
            return new SettingsProvider("Preferences/BlenderSync", SettingsScope.User)
            {
                label = "BlenderSync",
                guiHandler = _ =>
                {
                    EditorGUILayout.Space();
                    EditorGUILayout.LabelField("Blender 5.x Integration", EditorStyles.boldLabel);

                    EditorGUILayout.BeginHorizontal();
                    BlenderExecutablePath = EditorGUILayout.TextField("Blender Executable", BlenderExecutablePath);
                    if (GUILayout.Button("Browse...", GUILayout.Width(80)))
                    {
                        string chosen = EditorUtility.OpenFilePanel("Locate Blender 5.x Executable", "", "");
                        if (!string.IsNullOrEmpty(chosen))
                            BlenderExecutablePath = chosen;
                    }
                    if (GUILayout.Button("Auto-Detect", GUILayout.Width(95)))
                    {
                        BlenderExecutablePath = AutoDetectBlenderExecutable();
                    }
                    EditorGUILayout.EndHorizontal();

                    DefaultPort = EditorGUILayout.IntField("Default Loopback Port", DefaultPort);
                    MainThreadBudgetMs = EditorGUILayout.Slider("Main Thread Apply Budget (ms/frame)", MainThreadBudgetMs, 2.0f, 16.0f);
                }
            };
        }
    }
}
