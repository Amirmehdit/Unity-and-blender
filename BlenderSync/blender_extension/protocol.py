"""
BSYN v1 Binary Protocol encoder/decoder for BlenderSync.
Used identically for live TCP streaming and .bsync snapshot files.
"""

from __future__ import annotations
import struct
import zlib
from enum import IntEnum
from typing import Any

MAGIC = b"BSYN"
PROTOCOL_VERSION = 1
FLAG_COMPRESSED = 0x0001
MAX_FRAME_PAYLOAD = 512 * 1024 * 1024  # 512 MiB guard

HEADER_STRUCT = struct.Struct("<4sHHIQ")  # magic(4), version(2), flags(2), msg_type(4), payload_len(8) = 20 bytes


class MessageType(IntEnum):
    HELLO = 1
    HELLO_ACK = 2
    SCENE_BEGIN = 3
    MESH_ASSET = 4
    MATERIAL_UPSERT = 5
    TEXTURE_FILE = 6
    OBJECT_UPSERT = 7
    OBJECT_REMOVE = 8
    TRANSFORM_BATCH = 9
    INSTANCE_SET = 10
    ATTRIBUTE_MAP = 11
    SCENE_END = 12
    PING = 13
    PONG = 14
    LOG_ERROR = 15
    REQUEST_FULL_RESYNC = 16
    REQUEST_OBJECT = 17
    SELECT_OBJECT = 18


class VertexSemantic(IntEnum):
    POSITION = 0
    NORMAL = 1
    TANGENT = 2
    COLOR = 3
    TEXCOORD0 = 4
    TEXCOORD1 = 5
    TEXCOORD2 = 6
    TEXCOORD3 = 7
    TEXCOORD4 = 8
    TEXCOORD5 = 9
    TEXCOORD6 = 10
    TEXCOORD7 = 11


class VertexFormat(IntEnum):
    FLOAT32 = 0
    FLOAT16 = 1
    UNORM8 = 2
    SINT32 = 3


def pack_str(s: str) -> bytes:
    encoded = (s or "").encode("utf-8", errors="replace")
    return struct.pack("<I", len(encoded)) + encoded


def unpack_str(buf: memoryview | bytes, offset: int) -> tuple[str, int]:
    if offset + 4 > len(buf):
        raise ValueError("Buffer underflow reading string length")
    (length,) = struct.unpack_from("<I", buf, offset)
    offset += 4
    if length > 64 * 1024 * 1024 or offset + length > len(buf):
        raise ValueError(f"Invalid or truncated string length: {length}")
    s = bytes(buf[offset : offset + length]).decode("utf-8", errors="replace")
    return s, offset + length


def frame_message(msg_type: MessageType, payload: bytes, compress: bool = False) -> bytes:
    flags = 0
    if compress and len(payload) >= 1024:
        uncompressed_len = len(payload)
        compressed = zlib.compress(payload, level=1)
        if len(compressed) + 8 < uncompressed_len:
            payload = struct.pack("<Q", uncompressed_len) + compressed
            flags |= FLAG_COMPRESSED
    header = HEADER_STRUCT.pack(MAGIC, PROTOCOL_VERSION, flags, int(msg_type), len(payload))
    return header + payload


def decode_frame_header(header_bytes: bytes) -> tuple[int, int, MessageType, int]:
    if len(header_bytes) < HEADER_STRUCT.size:
        raise ValueError("Incomplete BSYN frame header")
    magic, version, flags, msg_type_raw, payload_len = HEADER_STRUCT.unpack(header_bytes[:HEADER_STRUCT.size])
    if magic != MAGIC:
        raise ValueError(f"Invalid protocol magic: {magic!r} (expected b'BSYN')")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"Protocol version mismatch: wire={version}, expected={PROTOCOL_VERSION}")
    if payload_len > MAX_FRAME_PAYLOAD:
        raise ValueError(f"Frame payload exceeds maximum allowed size ({payload_len} bytes)")
    return version, flags, MessageType(msg_type_raw), payload_len


def decode_frame_payload(flags: int, raw_payload: bytes) -> bytes:
    if flags & FLAG_COMPRESSED:
        if len(raw_payload) < 8:
            raise ValueError("Compressed payload missing uncompressed size header")
        (uncompressed_len,) = struct.unpack_from("<Q", raw_payload, 0)
        if uncompressed_len > MAX_FRAME_PAYLOAD:
            raise ValueError(f"Decompressed size exceeds limit: {uncompressed_len}")
        decompressed = zlib.decompress(raw_payload[8:])
        if len(decompressed) != uncompressed_len:
            raise ValueError("Decompressed byte length mismatch")
        return decompressed
    return raw_payload


# --- Message Encoders ---

def encode_hello(token: str, session_id: str, blender_version: str, blend_filepath: str) -> bytes:
    payload = b"".join([
        pack_str(token),
        pack_str(session_id),
        pack_str(blender_version),
        pack_str(blend_filepath),
    ])
    return frame_message(MessageType.HELLO, payload)


def decode_hello_ack(payload: bytes) -> dict[str, Any]:
    mv = memoryview(payload)
    if len(mv) < 1:
        raise ValueError("HELLO_ACK payload too short")
    (accepted,) = struct.unpack_from("<B", mv, 0)
    msg, off = unpack_str(mv, 1)
    if off + 4 > len(mv):
        raise ValueError("HELLO_ACK missing hash count")
    (count,) = struct.unpack_from("<I", mv, off)
    off += 4
    if count > 10_000_000 or off + count * 8 > len(mv):
        raise ValueError("HELLO_ACK invalid hash count")
    hashes = set(struct.unpack_from(f"<{count}Q", mv, off)) if count > 0 else set()
    return {"accepted": bool(accepted), "message": msg, "cached_hashes": hashes}


def encode_scene_begin(revision: int, is_full_sync: bool, unit_scale: float, scene_name: str, view_layer_name: str) -> bytes:
    payload = (
        struct.pack("<QBf", revision, 1 if is_full_sync else 0, float(unit_scale))
        + pack_str(scene_name)
        + pack_str(view_layer_name)
    )
    return frame_message(MessageType.SCENE_BEGIN, payload)


def encode_mesh_asset(packed_mesh: dict[str, Any], compress: bool = False) -> bytes:
    parts = [
        struct.pack("<Q", packed_mesh["content_hash"]),
        pack_str(packed_mesh["name"]),
        struct.pack("<3f3fII",
                    *packed_mesh["bounds_center"],
                    *packed_mesh["bounds_extents"],
                    packed_mesh["vertex_count"],
                    packed_mesh["vertex_stride"]),
        struct.pack("<B", len(packed_mesh["layout"])),
    ]
    for attr in packed_mesh["layout"]:
        parts.append(struct.pack("<BBBBI",
                                 int(attr["semantic"]),
                                 int(attr["format"]),
                                 int(attr["dimension"]),
                                 int(attr.get("stream", 0)),
                                 int(attr["offset"])))
    submeshes = packed_mesh["submeshes"]
    parts.append(struct.pack("<BIB",
                             int(packed_mesh["index_format"]),
                             int(packed_mesh["index_count"]),
                             len(submeshes)))
    for sm in submeshes:
        parts.append(struct.pack("<III3f3f",
                                 int(sm["index_start"]),
                                 int(sm["index_count"]),
                                 int(sm["material_slot"]),
                                 *sm["bounds_center"],
                                 *sm["bounds_extents"]))
    vbuf = packed_mesh["vertex_buffer"]
    ibuf = packed_mesh["index_buffer"]
    parts.append(struct.pack("<I", len(vbuf)))
    parts.append(vbuf)
    parts.append(struct.pack("<I", len(ibuf)))
    parts.append(ibuf)
    return frame_message(MessageType.MESH_ASSET, b"".join(parts), compress=compress)


def encode_material_upsert(content_hash: int, material_name: str, json_descriptor: str) -> bytes:
    payload = struct.pack("<Q", content_hash) + pack_str(material_name) + pack_str(json_descriptor)
    return frame_message(MessageType.MATERIAL_UPSERT, payload)


def encode_texture_file(content_hash: int, name: str, ext: str, is_srgb: bool, width: int, height: int, image_bytes: bytes) -> bytes:
    payload = (
        struct.pack("<Q", content_hash)
        + pack_str(name)
        + pack_str(ext)
        + struct.pack("<BIII", 1 if is_srgb else 0, width, height, len(image_bytes))
        + image_bytes
    )
    return frame_message(MessageType.TEXTURE_FILE, payload)


def encode_object_upsert(
    stable_id: str,
    object_name: str,
    collection_path: str,
    object_type: int,
    world_matrix_flat: list[float] | bytes,
    flags: int,
    mesh_chunk_hashes: list[int],
    material_names: list[str],
    custom_props_json: str = "{}",
) -> bytes:
    mat_bytes = world_matrix_flat if isinstance(world_matrix_flat, bytes) else struct.pack("<16f", *world_matrix_flat)
    parts = [
        pack_str(stable_id),
        pack_str(object_name),
        pack_str(collection_path),
        struct.pack("<B", object_type),
        mat_bytes,
        struct.pack("<BH", flags, len(mesh_chunk_hashes)),
    ]
    if mesh_chunk_hashes:
        parts.append(struct.pack(f"<{len(mesh_chunk_hashes)}Q", *mesh_chunk_hashes))
    parts.append(struct.pack("<H", len(material_names)))
    for mat_name in material_names:
        parts.append(pack_str(mat_name))
    parts.append(pack_str(custom_props_json))
    return frame_message(MessageType.OBJECT_UPSERT, b"".join(parts))


def encode_object_remove(stable_ids: list[str]) -> bytes:
    parts = [struct.pack("<I", len(stable_ids))]
    for sid in stable_ids:
        parts.append(pack_str(sid))
    return frame_message(MessageType.OBJECT_REMOVE, b"".join(parts))


def encode_transform_batch(transforms: list[tuple[str, bytes]]) -> bytes:
    parts = [struct.pack("<I", len(transforms))]
    for sid, mat_bytes in transforms:
        parts.append(pack_str(sid))
        parts.append(mat_bytes)
    return frame_message(MessageType.TRANSFORM_BATCH, b"".join(parts))


def encode_instance_set(
    owner_stable_id: str,
    source_object_name: str,
    collection_path: str,
    mesh_hash: int,
    material_names: list[str],
    instance_count: int,
    matrices_bytes: bytes,
    instance_data_bytes: bytes | None = None,
    compress: bool = False,
) -> bytes:
    has_data = 1 if (instance_data_bytes and len(instance_data_bytes) == instance_count * 16) else 0
    parts = [
        pack_str(owner_stable_id),
        pack_str(source_object_name),
        pack_str(collection_path),
        struct.pack("<QH", mesh_hash, len(material_names)),
    ]
    for m in material_names:
        parts.append(pack_str(m))
    parts.append(struct.pack("<IB", instance_count, has_data))
    parts.append(matrices_bytes)
    if has_data and instance_data_bytes is not None:
        parts.append(instance_data_bytes)
    return frame_message(MessageType.INSTANCE_SET, b"".join(parts), compress=compress)


def encode_attribute_map(json_manifest: str) -> bytes:
    return frame_message(MessageType.ATTRIBUTE_MAP, pack_str(json_manifest))


def encode_scene_end(revision: int, total_objects: int, total_triangles: int, total_instances: int, elapsed_ms: float) -> bytes:
    payload = struct.pack("<QIQIf", revision, total_objects, total_triangles, total_instances, float(elapsed_ms))
    return frame_message(MessageType.SCENE_END, payload)


def encode_log_error(severity: int, message: str) -> bytes:
    return frame_message(MessageType.LOG_ERROR, struct.pack("<B", severity) + pack_str(message))
