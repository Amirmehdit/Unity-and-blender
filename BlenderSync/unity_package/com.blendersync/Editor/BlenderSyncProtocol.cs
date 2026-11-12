using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using UnityEngine;
using UnityEngine.Rendering;

namespace BlenderSync.Editor
{
    public enum BsynMessageType : uint
    {
        Hello = 1,
        HelloAck = 2,
        SceneBegin = 3,
        MeshAsset = 4,
        MaterialUpsert = 5,
        TextureFile = 6,
        ObjectUpsert = 7,
        ObjectRemove = 8,
        TransformBatch = 9,
        InstanceSet = 10,
        AttributeMap = 11,
        SceneEnd = 12,
        Ping = 13,
        Pong = 14,
        LogError = 15,
        RequestFullResync = 16,
        RequestObject = 17,
        SelectObject = 18
    }

    public sealed class ParsedBsynFrame
    {
        public BsynMessageType MessageType;
        public byte[] Payload;
    }

    public struct WireSubMeshDescriptor
    {
        public int IndexStart;
        public int IndexCount;
        public int MaterialSlot;
        public Vector3 BoundsCenter;
        public Vector3 BoundsExtents;
    }

    public sealed class WireMeshAsset
    {
        public ulong ContentHash;
        public string Name;
        public Bounds Bounds;
        public int VertexCount;
        public int VertexStride;
        public VertexAttributeDescriptor[] Layout;
        public IndexFormat IndexFormat;
        public int IndexCount;
        public WireSubMeshDescriptor[] SubMeshes;
        public byte[] VertexBuffer;
        public byte[] IndexBuffer;
    }

    public sealed class WireObjectUpsert
    {
        public string StableId;
        public string ObjectName;
        public string CollectionPath;
        public byte ObjectType;
        public Matrix4x4 WorldMatrix;
        public byte Flags;
        public ulong[] MeshChunkHashes;
        public string[] MaterialNames;
        public string CustomPropsJson;
    }

    public sealed class WireInstanceSet
    {
        public string OwnerStableId;
        public string SourceObjectName;
        public string CollectionPath;
        public ulong MeshHash;
        public string[] MaterialNames;
        public int InstanceCount;
        public Matrix4x4[] WorldMatrices;
        public Vector4[] CustomInstanceData;
    }

    /// <summary>
    /// Strict, bounds-checked binary parser and encoder for the BSYN v1 protocol (Section 6 & 10).
    /// Rejects any malformed, truncated, or oversized frames (> 512 MiB).
    /// </summary>
    public static class BlenderSyncProtocol
    {
        public const int HeaderByteSize = 20;
        public const ushort ExpectedVersion = 1;
        public const ushort FlagCompressed = 0x0001;
        public const ulong MaxPayloadBytes = 512UL * 1024UL * 1024UL; // 512 MiB guard

        public static void ParseHeader(byte[] header, out ushort version, out ushort flags, out BsynMessageType msgType, out ulong payloadLen)
        {
            if (header == null || header.Length < HeaderByteSize)
                throw new InvalidDataException("Incomplete BSYN frame header.");

            if (header[0] != (byte)'B' || header[1] != (byte)'S' || header[2] != (byte)'Y' || header[3] != (byte)'N')
                throw new InvalidDataException("Invalid protocol magic (expected 'BSYN').");

            version = BitConverter.ToUInt16(header, 4);
            if (version != ExpectedVersion)
                throw new InvalidDataException($"Protocol version mismatch: wire={version}, expected={ExpectedVersion}. Please update both Blender extension and Unity package.");

            flags = BitConverter.ToUInt16(header, 6);
            uint rawType = BitConverter.ToUInt32(header, 8);
            if (rawType < 1 || rawType > 18)
                throw new InvalidDataException($"Unknown BSYN message type ID: {rawType}");

            msgType = (BsynMessageType)rawType;
            payloadLen = BitConverter.ToUInt64(header, 12);
            if (payloadLen > MaxPayloadBytes)
                throw new InvalidDataException($"Frame payload length ({payloadLen} bytes) exceeds 512 MiB security limit.");
        }

        public static byte[] DecodePayload(ushort flags, byte[] rawPayload)
        {
            if ((flags & FlagCompressed) == 0)
                return rawPayload;

            if (rawPayload == null || rawPayload.Length < 10)
                throw new InvalidDataException("Compressed BSYN payload is too short.");

            ulong uncompressedLen = BitConverter.ToUInt64(rawPayload, 0);
            if (uncompressedLen > MaxPayloadBytes)
                throw new InvalidDataException($"Decompressed length ({uncompressedLen}) exceeds 512 MiB limit.");

            // Skip 8-byte size + 2-byte zlib header (RFC 1950)
            var output = new byte[(int)uncompressedLen];
            using (var ms = new MemoryStream(rawPayload, 10, rawPayload.Length - 10))
            using (var deflate = new DeflateStream(ms, CompressionMode.Decompress))
            {
                int totalRead = 0;
                while (totalRead < output.Length)
                {
                    int read = deflate.Read(output, totalRead, output.Length - totalRead);
                    if (read <= 0)
                        break;
                    totalRead += read;
                }
                if (totalRead != output.Length)
                    throw new InvalidDataException($"Decompressed byte count mismatch: got {totalRead}, expected {output.Length}");
            }
            return output;
        }

        public static string ReadString(byte[] buf, ref int offset)
        {
            if (offset < 0 || offset + 4 > buf.Length)
                throw new InvalidDataException("Buffer underflow reading string length.");

            uint byteLen = BitConverter.ToUInt32(buf, offset);
            offset += 4;
            if (byteLen > 64 * 1024 * 1024 || offset + (int)byteLen > buf.Length)
                throw new InvalidDataException($"Invalid or truncated string length: {byteLen}");

            string s = Encoding.UTF8.GetString(buf, offset, (int)byteLen);
            offset += (int)byteLen;
            return s;
        }

        public static Matrix4x4 ReadMatrix4x4RowMajor(byte[] buf, ref int offset)
        {
            if (offset + 64 > buf.Length)
                throw new InvalidDataException("Buffer underflow reading Matrix4x4.");

            var m = new Matrix4x4();
            for (int r = 0; r < 4; r++)
            {
                for (int c = 0; c < 4; c++)
                {
                    m[r, c] = BitConverter.ToSingle(buf, offset);
                    offset += 4;
                }
            }
            return m;
        }

        public static byte[] BuildFrame(BsynMessageType msgType, byte[] payload)
        {
            payload = payload ?? Array.Empty<byte>();
            var frame = new byte[HeaderByteSize + payload.Length];
            frame[0] = (byte)'B';
            frame[1] = (byte)'S';
            frame[2] = (byte)'Y';
            frame[3] = (byte)'N';
            Buffer.BlockCopy(BitConverter.GetBytes(ExpectedVersion), 0, frame, 4, 2);
            Buffer.BlockCopy(BitConverter.GetBytes((ushort)0), 0, frame, 6, 2);
            Buffer.BlockCopy(BitConverter.GetBytes((uint)msgType), 0, frame, 8, 4);
            Buffer.BlockCopy(BitConverter.GetBytes((ulong)payload.Length), 0, frame, 12, 8);
            if (payload.Length > 0)
                Buffer.BlockCopy(payload, 0, frame, HeaderByteSize, payload.Length);
            return frame;
        }

        public static byte[] EncodeHelloAck(bool accepted, string message, IReadOnlyCollection<ulong> cachedHashes)
        {
            byte[] msgBytes = Encoding.UTF8.GetBytes(message ?? string.Empty);
            int hashCount = cachedHashes != null ? cachedHashes.Count : 0;
            var payload = new byte[1 + 4 + msgBytes.Length + 4 + hashCount * 8];
            int off = 0;
            payload[off++] = (byte)(accepted ? 1 : 0);
            Buffer.BlockCopy(BitConverter.GetBytes((uint)msgBytes.Length), 0, payload, off, 4);
            off += 4;
            Buffer.BlockCopy(msgBytes, 0, payload, off, msgBytes.Length);
            off += msgBytes.Length;
            Buffer.BlockCopy(BitConverter.GetBytes((uint)hashCount), 0, payload, off, 4);
            off += 4;
            if (cachedHashes != null)
            {
                foreach (ulong h in cachedHashes)
                {
                    Buffer.BlockCopy(BitConverter.GetBytes(h), 0, payload, off, 8);
                    off += 8;
                }
            }
            return BuildFrame(BsynMessageType.HelloAck, payload);
        }

        public static WireMeshAsset ParseMeshAsset(byte[] payload)
        {
            int off = 0;
            if (payload.Length < 40)
                throw new InvalidDataException("MESH_ASSET payload too short.");

            ulong contentHash = BitConverter.ToUInt64(payload, off);
            off += 8;
            string name = ReadString(payload, ref off);

            if (off + 33 > payload.Length)
                throw new InvalidDataException("MESH_ASSET header truncated.");

            Vector3 center = new Vector3(
                BitConverter.ToSingle(payload, off),
                BitConverter.ToSingle(payload, off + 4),
                BitConverter.ToSingle(payload, off + 8));
            off += 12;

            Vector3 extents = new Vector3(
                BitConverter.ToSingle(payload, off),
                BitConverter.ToSingle(payload, off + 4),
                BitConverter.ToSingle(payload, off + 8));
            off += 12;

            int vertexCount = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            int vertexStride = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            int attrCount = payload[off++];

            if (attrCount <= 0 || attrCount > 16 || off + attrCount * 8 > payload.Length)
                throw new InvalidDataException($"Invalid vertex attribute count: {attrCount}");

            var layout = new VertexAttributeDescriptor[attrCount];
            for (int i = 0; i < attrCount; i++)
            {
                byte sem = payload[off++];
                byte fmt = payload[off++];
                byte dim = payload[off++];
                byte stream = payload[off++];
                int attrOffset = (int)BitConverter.ToUInt32(payload, off);
                off += 4;

                VertexAttribute unityAttr = MapSemantic(sem);
                VertexAttributeFormat unityFmt = MapFormat(fmt);
                layout[i] = new VertexAttributeDescriptor(unityAttr, unityFmt, dim, stream);
            }

            if (off + 6 > payload.Length)
                throw new InvalidDataException("MESH_ASSET index descriptor truncated.");

            IndexFormat idxFormat = payload[off++] == 1 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            int indexCount = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            int submeshCount = payload[off++];

            if (off + submeshCount * 36 > payload.Length)
                throw new InvalidDataException("MESH_ASSET submesh table truncated.");

            var submeshes = new WireSubMeshDescriptor[submeshCount];
            for (int i = 0; i < submeshCount; i++)
            {
                int idxStart = (int)BitConverter.ToUInt32(payload, off);
                int idxCnt = (int)BitConverter.ToUInt32(payload, off + 4);
                int matSlot = (int)BitConverter.ToUInt32(payload, off + 8);
                off += 12;

                Vector3 smCenter = new Vector3(
                    BitConverter.ToSingle(payload, off),
                    BitConverter.ToSingle(payload, off + 4),
                    BitConverter.ToSingle(payload, off + 8));
                off += 12;

                Vector3 smExtents = new Vector3(
                    BitConverter.ToSingle(payload, off),
                    BitConverter.ToSingle(payload, off + 4),
                    BitConverter.ToSingle(payload, off + 8));
                off += 12;

                submeshes[i] = new WireSubMeshDescriptor
                {
                    IndexStart = idxStart,
                    IndexCount = idxCnt,
                    MaterialSlot = matSlot,
                    BoundsCenter = smCenter,
                    BoundsExtents = smExtents
                };
            }

            if (off + 4 > payload.Length)
                throw new InvalidDataException("MESH_ASSET vertex buffer length missing.");
            int vbufLen = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            if (vbufLen < 0 || off + vbufLen + 4 > payload.Length || vbufLen != vertexCount * vertexStride)
                throw new InvalidDataException($"MESH_ASSET vertex buffer size mismatch: {vbufLen} vs {vertexCount}*{vertexStride}");

            var vbuf = new byte[vbufLen];
            Buffer.BlockCopy(payload, off, vbuf, 0, vbufLen);
            off += vbufLen;

            int ibufLen = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            int expectedIndexBytes = indexCount * (idxFormat == IndexFormat.UInt32 ? 4 : 2);
            if (ibufLen < 0 || off + ibufLen > payload.Length || ibufLen != expectedIndexBytes)
                throw new InvalidDataException("MESH_ASSET index buffer size mismatch.");

            var ibuf = new byte[ibufLen];
            Buffer.BlockCopy(payload, off, ibuf, 0, ibufLen);

            return new WireMeshAsset
            {
                ContentHash = contentHash,
                Name = name,
                Bounds = new Bounds(center, extents * 2.0f),
                VertexCount = vertexCount,
                VertexStride = vertexStride,
                Layout = layout,
                IndexFormat = idxFormat,
                IndexCount = indexCount,
                SubMeshes = submeshes,
                VertexBuffer = vbuf,
                IndexBuffer = ibuf
            };
        }

        public static WireObjectUpsert ParseObjectUpsert(byte[] payload)
        {
            int off = 0;
            string stableId = ReadString(payload, ref off);
            string objectName = ReadString(payload, ref off);
            string colPath = ReadString(payload, ref off);
            if (off + 1 + 64 + 3 > payload.Length)
                throw new InvalidDataException("OBJECT_UPSERT truncated.");

            byte objType = payload[off++];
            Matrix4x4 worldMat = ReadMatrix4x4RowMajor(payload, ref off);
            byte flags = payload[off++];
            ushort chunkCount = BitConverter.ToUInt16(payload, off);
            off += 2;

            if (off + chunkCount * 8 + 2 > payload.Length)
                throw new InvalidDataException("OBJECT_UPSERT chunk hash list truncated.");

            var chunkHashes = new ulong[chunkCount];
            for (int i = 0; i < chunkCount; i++)
            {
                chunkHashes[i] = BitConverter.ToUInt64(payload, off);
                off += 8;
            }

            ushort matCount = BitConverter.ToUInt16(payload, off);
            off += 2;
            var matNames = new string[matCount];
            for (int i = 0; i < matCount; i++)
            {
                matNames[i] = ReadString(payload, ref off);
            }

            string customProps = ReadString(payload, ref off);
            return new WireObjectUpsert
            {
                StableId = stableId,
                ObjectName = objectName,
                CollectionPath = colPath,
                ObjectType = objType,
                WorldMatrix = worldMat,
                Flags = flags,
                MeshChunkHashes = chunkHashes,
                MaterialNames = matNames,
                CustomPropsJson = customProps
            };
        }

        public static WireInstanceSet ParseInstanceSet(byte[] payload)
        {
            int off = 0;
            string ownerId = ReadString(payload, ref off);
            string sourceName = ReadString(payload, ref off);
            string colPath = ReadString(payload, ref off);
            if (off + 10 > payload.Length)
                throw new InvalidDataException("INSTANCE_SET header truncated.");

            ulong meshHash = BitConverter.ToUInt64(payload, off);
            off += 8;
            ushort matCount = BitConverter.ToUInt16(payload, off);
            off += 2;
            var matNames = new string[matCount];
            for (int i = 0; i < matCount; i++)
            {
                matNames[i] = ReadString(payload, ref off);
            }

            if (off + 5 > payload.Length)
                throw new InvalidDataException("INSTANCE_SET count truncated.");

            int instCount = (int)BitConverter.ToUInt32(payload, off);
            off += 4;
            bool hasCustomData = payload[off++] != 0;

            if (instCount < 0 || instCount > 5_000_000 || off + instCount * 64 > payload.Length)
                throw new InvalidDataException($"INSTANCE_SET matrix buffer size invalid for count={instCount}");

            var mats = new Matrix4x4[instCount];
            for (int i = 0; i < instCount; i++)
            {
                mats[i] = ReadMatrix4x4RowMajor(payload, ref off);
            }

            Vector4[] customData = Array.Empty<Vector4>();
            if (hasCustomData && off + instCount * 16 <= payload.Length)
            {
                customData = new Vector4[instCount];
                for (int i = 0; i < instCount; i++)
                {
                    customData[i] = new Vector4(
                        BitConverter.ToSingle(payload, off),
                        BitConverter.ToSingle(payload, off + 4),
                        BitConverter.ToSingle(payload, off + 8),
                        BitConverter.ToSingle(payload, off + 12));
                    off += 16;
                }
            }

            return new WireInstanceSet
            {
                OwnerStableId = ownerId,
                SourceObjectName = sourceName,
                CollectionPath = colPath,
                MeshHash = meshHash,
                MaterialNames = matNames,
                InstanceCount = instCount,
                WorldMatrices = mats,
                CustomInstanceData = customData
            };
        }

        private static VertexAttribute MapSemantic(byte sem)
        {
            switch (sem)
            {
                case 0: return VertexAttribute.Position;
                case 1: return VertexAttribute.Normal;
                case 2: return VertexAttribute.Tangent;
                case 3: return VertexAttribute.Color;
                case 4: return VertexAttribute.TexCoord0;
                case 5: return VertexAttribute.TexCoord1;
                case 6: return VertexAttribute.TexCoord2;
                case 7: return VertexAttribute.TexCoord3;
                case 8: return VertexAttribute.TexCoord4;
                case 9: return VertexAttribute.TexCoord5;
                case 10: return VertexAttribute.TexCoord6;
                case 11: return VertexAttribute.TexCoord7;
                default: return VertexAttribute.TexCoord0;
            }
        }

        private static VertexAttributeFormat MapFormat(byte fmt)
        {
            switch (fmt)
            {
                case 0: return VertexAttributeFormat.Float32;
                case 1: return VertexAttributeFormat.Float16;
                case 2: return VertexAttributeFormat.UNorm8;
                case 3: return VertexAttributeFormat.SInt32;
                default: return VertexAttributeFormat.Float32;
            }
        }
    }
}
