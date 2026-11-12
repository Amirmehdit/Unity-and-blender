using System;
using System.Diagnostics;
using System.IO;
using UnityEditor;
using UnityEngine;
using Debug = UnityEngine.Debug;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Manages `.bsync` snapshot file reading from `Library/BlenderSync/<LinkGuid>/snapshot.bsync`
    /// and headless background Blender export (`blender -b <file.blend> --python cli_export.py`)
    /// so maps appear in Unity even when no Blender UI is open (Requirement R1).
    /// </summary>
    public static class SnapshotImporter
    {
        public static string GetSnapshotDirectory(BlenderMapLink link)
        {
            string projectRoot = Path.GetDirectoryName(Application.dataPath) ?? ".";
            string guid = string.IsNullOrEmpty(link.LinkGuid) ? "default" : link.LinkGuid;
            return Path.Combine(projectRoot, "Library", "BlenderSync", guid);
        }

        public static string GetSnapshotFilePath(BlenderMapLink link)
        {
            return Path.Combine(GetSnapshotDirectory(link), "snapshot.bsync");
        }

        public static string ResolveBlendFilePath(BlenderMapLink link)
        {
            if (string.IsNullOrEmpty(link.BlendFilePath))
                return string.Empty;
            if (Path.IsPathRooted(link.BlendFilePath))
                return link.BlendFilePath;
            string projectRoot = Path.GetDirectoryName(Application.dataPath) ?? ".";
            return Path.GetFullPath(Path.Combine(projectRoot, link.BlendFilePath));
        }

        public static bool IsSnapshotStale(BlenderMapLink link)
        {
            string snapPath = GetSnapshotFilePath(link);
            if (!File.Exists(snapPath))
                return true;

            string blendPath = ResolveBlendFilePath(link);
            if (!string.IsNullOrEmpty(blendPath) && File.Exists(blendPath))
            {
                DateTime blendTime = File.GetLastWriteTimeUtc(blendPath);
                DateTime snapTime = File.GetLastWriteTimeUtc(snapPath);
                return blendTime > snapTime;
            }
            return false;
        }

        public static bool LoadSnapshotIntoApplier(string snapshotPath, SceneApplier applier)
        {
            if (!File.Exists(snapshotPath) || applier == null)
                return false;

            using (var fs = new FileStream(snapshotPath, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                var header = new byte[BlenderSyncProtocol.HeaderByteSize];
                while (fs.Position < fs.Length)
                {
                    int readHdr = fs.Read(header, 0, header.Length);
                    if (readHdr == 0)
                        break;
                    if (readHdr < header.Length)
                        throw new InvalidDataException("Truncated frame header in .bsync snapshot.");

                    BlenderSyncProtocol.ParseHeader(header, out _, out ushort flags, out BsynMessageType msgType, out ulong payloadLen);
                    var rawPayload = new byte[(int)payloadLen];
                    int totalRead = 0;
                    while (totalRead < rawPayload.Length)
                    {
                        int r = fs.Read(rawPayload, totalRead, rawPayload.Length - totalRead);
                        if (r <= 0)
                            throw new InvalidDataException("Truncated frame payload in .bsync snapshot.");
                        totalRead += r;
                    }

                    byte[] decoded = BlenderSyncProtocol.DecodePayload(flags, rawPayload);
                    applier.EnqueueFrame(new ParsedBsynFrame
                    {
                        MessageType = msgType,
                        Payload = decoded
                    });
                }
            }
            return true;
        }

        public static void RunHeadlessBlenderRefreshAsync(BlenderMapLink link, SceneApplier applier, Action onComplete = null)
        {
            string blenderExe = BlenderSyncPreferences.BlenderExecutablePath;
            if (string.IsNullOrEmpty(blenderExe) || !File.Exists(blenderExe))
            {
                link.ConnectionState = SyncConnectionState.Error;
                link.LastStatusMessage = "Blender 5.x executable not found. Configure path in Edit > Preferences > BlenderSync.";
                return;
            }

            string blendPath = ResolveBlendFilePath(link);
            if (string.IsNullOrEmpty(blendPath) || !File.Exists(blendPath))
            {
                link.ConnectionState = SyncConnectionState.Error;
                link.LastStatusMessage = $"Linked .blend file not found: '{blendPath}'";
                return;
            }

            string projectRoot = Path.GetDirectoryName(Application.dataPath) ?? ".";
            string cliScript = Path.GetFullPath(Path.Combine(projectRoot, "..", "blender_extension", "cli_export.py"));
            if (!File.Exists(cliScript))
            {
                cliScript = Path.GetFullPath(Path.Combine(projectRoot, "blender_extension", "cli_export.py"));
            }

            string snapshotOut = GetSnapshotFilePath(link);
            Directory.CreateDirectory(Path.GetDirectoryName(snapshotOut) ?? ".");

            link.LastStatusMessage = "Running headless Blender 5.x snapshot export...";
            var psi = new ProcessStartInfo
            {
                FileName = blenderExe,
                Arguments = $"-b \"{blendPath}\" --python \"{cliScript}\" -- --out \"{snapshotOut}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };

            try
            {
                var proc = new Process { StartInfo = psi, EnableRaisingEvents = true };
                proc.Exited += (_, __) =>
                {
                    int code = proc.ExitCode;
                    proc.Dispose();
                    EditorApplication.delayCall += () =>
                    {
                        if (link == null)
                            return;
                        if (code == 0 && File.Exists(snapshotOut))
                        {
                            LoadSnapshotIntoApplier(snapshotOut, applier);
                            link.ConnectionState = SyncConnectionState.Cached;
                            link.LastStatusMessage = "Loaded fresh snapshot from headless Blender.";
                        }
                        else
                        {
                            link.ConnectionState = SyncConnectionState.Error;
                            link.LastStatusMessage = $"Headless Blender exited with code {code}.";
                        }
                        onComplete?.Invoke();
                    };
                };
                proc.Start();
            }
            catch (Exception ex)
            {
                link.ConnectionState = SyncConnectionState.Error;
                link.LastStatusMessage = $"Failed to launch headless Blender: {ex.Message}";
                Debug.LogError($"[BlenderSync] {link.LastStatusMessage}");
            }
        }
    }
}
