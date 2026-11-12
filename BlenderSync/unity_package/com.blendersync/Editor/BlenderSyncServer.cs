using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using UnityEditor;
using UnityEngine;

namespace BlenderSync.Editor
{
    /// <summary>
    /// Loopback TCP server (`127.0.0.1`) hosted in the Unity Editor (Section 4.2a & 7.1).
    /// - Survives Unity domain reloads (`AssemblyReloadEvents.beforeAssemblyReload`) and Play Mode transitions.
    /// - Automatically loads cached snapshots or triggers headless Blender refresh when a scene opens (Requirement R1).
    /// - Drains incoming frames on the main thread within the configured per-frame time budget.
    /// </summary>
    [InitializeOnLoad]
    public static class BlenderSyncServer
    {
        private static TcpListener _listener;
        private static TcpClient _activeClient;
        private static Thread _acceptThread;
        private static volatile bool _running;
        private static int _boundPort = -1;

        private static readonly ConcurrentQueue<ParsedBsynFrame> _wireQueue = new ConcurrentQueue<ParsedBsynFrame>();
        private static readonly ConcurrentQueue<byte[]> _outboundQueue = new ConcurrentQueue<byte[]>();
        private static readonly SceneApplier _applier = new SceneApplier();

        private static BlenderMapLink _activeLink;

        public static SceneApplier Applier => _applier;
        public static bool IsClientConnected => _activeClient != null && _activeClient.Connected;

        static BlenderSyncServer()
        {
            BlenderMapLink.OnLinkEnabled += HandleLinkEnabled;
            BlenderMapLink.OnLinkDisabled += HandleLinkDisabled;
            AssemblyReloadEvents.beforeAssemblyReload += StopServer;
            EditorApplication.quitting += StopServer;
            EditorApplication.update += OnEditorUpdate;
            EditorApplication.delayCall += DiscoverExistingSceneLink;
        }

        private static void DiscoverExistingSceneLink()
        {
            if (_activeLink == null)
            {
                var found = UnityEngine.Object.FindFirstObjectByType<BlenderMapLink>();
                if (found != null)
                    HandleLinkEnabled(found);
            }
        }

        private static void HandleLinkEnabled(BlenderMapLink link)
        {
            _activeLink = link;
            _applier.RebuildObjectLookupFromHierarchy(link);
            EnsureServerListening(link.Port);

            // R1 Unity-first workflow: if not live-connected, load cached snapshot immediately,
            // and if stale, trigger headless refresh if enabled.
            if (!IsClientConnected)
            {
                string snapPath = SnapshotImporter.GetSnapshotFilePath(link);
                if (File.Exists(snapPath))
                {
                    try
                    {
                        SnapshotImporter.LoadSnapshotIntoApplier(snapPath, _applier);
                        bool stale = SnapshotImporter.IsSnapshotStale(link);
                        link.ConnectionState = stale ? SyncConnectionState.Stale : SyncConnectionState.Cached;
                        if (stale && link.AutoRefreshStaleCacheOnOpen && !string.IsNullOrEmpty(link.BlendFilePath))
                        {
                            SnapshotImporter.RunHeadlessBlenderRefreshAsync(link, _applier);
                        }
                    }
                    catch (Exception ex)
                    {
                        link.ConnectionState = SyncConnectionState.Error;
                        link.LastStatusMessage = $"Snapshot load error: {ex.Message}";
                    }
                }
                else if (link.AutoRefreshStaleCacheOnOpen && !string.IsNullOrEmpty(link.BlendFilePath))
                {
                    SnapshotImporter.RunHeadlessBlenderRefreshAsync(link, _applier);
                }
            }
        }

        private static void HandleLinkDisabled(BlenderMapLink link)
        {
            if (_activeLink == link)
                _activeLink = null;
        }

        public static void EnsureServerListening(int port)
        {
            if (_running && _boundPort == port)
                return;

            StopServer();
            _boundPort = port;
            _running = true;
            _acceptThread = new Thread(ServerLoop)
            {
                Name = "BlenderSyncLoopbackServer",
                IsBackground = true
            };
            _acceptThread.Start();
        }

        public static void StopServer()
        {
            _running = false;
            try { _activeClient?.Close(); } catch { /* ignore */ }
            _activeClient = null;
            try { _listener?.Stop(); } catch { /* ignore */ }
            _listener = null;
            if (_acceptThread != null && _acceptThread.IsAlive)
            {
                _acceptThread.Join(250);
            }
            _acceptThread = null;
        }

        public static void RequestFullResyncFromBlender()
        {
            byte[] frame = BlenderSyncProtocol.BuildFrame(BsynMessageType.RequestFullResync, Array.Empty<byte>());
            _outboundQueue.Enqueue(frame);
        }

        public static void PingObjectInBlender(string stableId)
        {
            byte[] strBytes = System.Text.Encoding.UTF8.GetBytes(stableId ?? string.Empty);
            var payload = new byte[4 + strBytes.Length];
            Buffer.BlockCopy(BitConverter.GetBytes((uint)strBytes.Length), 0, payload, 0, 4);
            Buffer.BlockCopy(strBytes, 0, payload, 4, strBytes.Length);
            _outboundQueue.Enqueue(BlenderSyncProtocol.BuildFrame(BsynMessageType.SelectObject, payload));
        }

        private static void OnEditorUpdate()
        {
            if (_activeLink == null)
                return;

            if (IsClientConnected)
                _activeLink.ConnectionState = SyncConnectionState.Live;

            // Move parsed wire frames into SceneApplier queue
            while (_wireQueue.TryDequeue(out ParsedBsynFrame frame))
            {
                _applier.EnqueueFrame(frame);
            }

            if (_applier.HasPendingWork)
            {
                _applier.DrainQueueWithBudget(_activeLink, BlenderSyncPreferences.MainThreadBudgetMs);
            }
        }

        private static void ServerLoop()
        {
            try
            {
                // Security requirement: Bind to loopback (127.0.0.1) only
                _listener = new TcpListener(IPAddress.Loopback, _boundPort);
                _listener.Start();

                while (_running)
                {
                    if (!_listener.Pending())
                    {
                        Thread.Sleep(25);
                        continue;
                    }

                    TcpClient client = _listener.AcceptTcpClient();
                    client.NoDelay = true;
                    _activeClient = client;
                    HandleClientConnection(client);
                }
            }
            catch (SocketException)
            {
                // Normal on domain reload shutdown
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[BlenderSync] Server listener stopped: {ex.Message}");
            }
        }

        private static void HandleClientConnection(TcpClient client)
        {
            try
            {
                NetworkStream stream = client.GetStream();
                var headerBuf = new byte[BlenderSyncProtocol.HeaderByteSize];

                // 1. Read HELLO frame
                if (!ReadExact(stream, headerBuf, headerBuf.Length))
                    return;

                BlenderSyncProtocol.ParseHeader(headerBuf, out _, out ushort flags, out BsynMessageType msgType, out ulong payloadLen);
                if (msgType != BsynMessageType.Hello)
                    return;

                var helloRaw = new byte[(int)payloadLen];
                if (!ReadExact(stream, helloRaw, helloRaw.Length))
                    return;

                byte[] helloPayload = BlenderSyncProtocol.DecodePayload(flags, helloRaw);
                int off = 0;
                string wireToken = BlenderSyncProtocol.ReadString(helloPayload, ref off);

                string expectedToken = _activeLink != null ? _activeLink.AuthToken : string.Empty;
                bool tokenOk = string.IsNullOrEmpty(expectedToken) || string.Equals(wireToken, expectedToken, StringComparison.Ordinal);

                byte[] ackFrame = BlenderSyncProtocol.EncodeHelloAck(
                    tokenOk,
                    tokenOk ? "OK" : "Auth token mismatch",
                    _applier.KnownContentHashes);
                stream.Write(ackFrame, 0, ackFrame.Length);

                if (!tokenOk)
                    return;

                // 2. Main receive/send loop
                while (_running && client.Connected)
                {
                    while (_outboundQueue.TryDequeue(out byte[] outbound))
                    {
                        stream.Write(outbound, 0, outbound.Length);
                    }

                    if (client.Available >= BlenderSyncProtocol.HeaderByteSize)
                    {
                        if (!ReadExact(stream, headerBuf, headerBuf.Length))
                            break;

                        BlenderSyncProtocol.ParseHeader(headerBuf, out _, out ushort fFlags, out BsynMessageType fType, out ulong fLen);
                        var rawPayload = new byte[(int)fLen];
                        if (fLen > 0 && !ReadExact(stream, rawPayload, rawPayload.Length))
                            break;

                        byte[] decoded = BlenderSyncProtocol.DecodePayload(fFlags, rawPayload);
                        if (fType == BsynMessageType.Ping)
                        {
                            byte[] pong = BlenderSyncProtocol.BuildFrame(BsynMessageType.Pong, decoded);
                            stream.Write(pong, 0, pong.Length);
                        }
                        else
                        {
                            _wireQueue.Enqueue(new ParsedBsynFrame
                            {
                                MessageType = fType,
                                Payload = decoded
                            });
                        }
                    }
                    else
                    {
                        Thread.Sleep(4);
                    }
                }
            }
            catch (Exception)
            {
                // Connection closed or reset; client will auto-reconnect
            }
            finally
            {
                try { client.Close(); } catch { /* ignore */ }
            }
        }

        private static bool ReadExact(NetworkStream stream, byte[] buffer, int count)
        {
            int offset = 0;
            while (offset < count && _running)
            {
                int read = stream.Read(buffer, offset, count - offset);
                if (read <= 0)
                    return false;
                offset += read;
            }
            return offset == count;
        }
    }
}
