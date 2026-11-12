# BlenderSync Binary Protocol Specification (v1)

The BlenderSync wire protocol (`BSYN` v1) is a versioned, little-endian binary stream format used identically for:
1. **Live TCP socket streaming** (`127.0.0.1:52731` by default).
2. **Snapshot cache files** (`.bsync` files in `Library/BlenderSync/<link-guid>/snapshot.bsync`).

All multi-byte integers and floats are **little-endian (`<`)**. All strings are UTF-8 encoded and prefixed with a `u32` byte length (`u32 len` + `byte[len]`).

---

## 1. Stream & Frame Envelope

Every message frame starts with a 20-byte fixed header:

| Offset | Type | Name | Description |
|--------|------|------|-------------|
| `0` | `char[4]` | `magic` | ASCII `"BSYN"` (`0x42, 0x53, 0x59, 0x4E`) |
| `4` | `u16` | `version` | Protocol version (`1`) |
| `6` | `u16` | `flags` | Bitfield: `0x0001` = Payload is `zlib`-compressed (`u64` uncompressed size prefixes compressed bytes) |
| `8` | `u32` | `msg_type` | Message Type enum (see Section 2) |
| `12` | `u64` | `payload_len` | Byte length of the message payload following offset 20 (max allowed: 512 MiB) |

If `flags & 0x0001` is set:
- The first 8 bytes of the payload are `u64 uncompressed_len` (validated $\le 512\text{ MiB}$), followed by `payload_len - 8` bytes of standard `zlib` deflate stream.

---

## 2. Message Types (`msg_type`)

| ID | Name | Direction | Purpose |
|----|------|-----------|---------|
| `1` | `HELLO` | Blender $\to$ Unity | Handshake: protocol version, auth token, session ID, Blender version |
| `2` | `HELLO_ACK` | Unity $\to$ Blender | Handshake accepted + list of cached `u64` content hashes already in Unity |
| `3` | `SCENE_BEGIN` | Blender $\to$ Unity | Begins a sync revision (full or incremental), unit scale, scene/view-layer info |
| `4` | `MESH_ASSET` | Blender $\to$ Unity | Content-addressed packed vertex/index buffers + vertex layout + submeshes + AABB |
| `5` | `MATERIAL_UPSERT` | Blender $\to$ Unity | Content-addressed JSON material descriptor + referenced texture hashes + optional HLSL |
| `6` | `TEXTURE_FILE` | Blender $\to$ Unity | Content-addressed image file bytes + color space flag (sRGB vs Linear) |
| `7` | `OBJECT_UPSERT` | Blender $\to$ Unity | Stable object ID, name, collection path, mesh chunk hashes, materials, world matrix |
| `8` | `OBJECT_REMOVE` | Blender $\to$ Unity | List of removed stable object IDs |
| `9` | `TRANSFORM_BATCH` | Blender $\to$ Unity | Fast-path array of `(stable_id, float4x4_unity_world)` pairs |
| `10` | `INSTANCE_SET` | Blender $\to$ Unity | Owner stable ID, source mesh hash, material names, world matrices, per-instance `float4` data |
| `11` | `ATTRIBUTE_MAP` | Blender $\to$ Unity | JSON manifest mapping custom/color attributes to Unity `TEXCOORD2..7` / `COLOR` |
| `12` | `SCENE_END` | Blender $\to$ Unity | Ends sync revision; carries stats (objects, triangles, instances, elapsed ms) |
| `13` | `PING` | Bidirectional | Keep-alive heartbeat (`u64 timestamp_ms`) |
| `14` | `PONG` | Bidirectional | Keep-alive reply (`u64 timestamp_ms`) |
| `15` | `LOG_ERROR` | Bidirectional | Severity (`u8`: 0=Info, 1=Warn, 2=Error) + UTF-8 message |
| `16` | `REQUEST_FULL_RESYNC` | Unity $\to$ Blender | Requests Blender to clear remote cache state and emit a full scene sync |
| `17` | `REQUEST_OBJECT` | Unity $\to$ Blender | Requests immediate re-export of a specific `stable_id` |
| `18` | `SELECT_OBJECT` | Unity $\to$ Blender | Pings/selects an object by `stable_id` or name inside Blender |

---

## 3. Payload Layouts

### `1: HELLO`
- `str token`
- `str session_id`
- `str blender_version`
- `str blend_filepath`

### `2: HELLO_ACK`
- `u8 accepted` (`1` = ok, `0` = rejected)
- `str message`
- `u32 cached_hash_count`
- `u64[cached_hash_count] cached_hashes` (meshes + textures + materials known to Unity)

### `3: SCENE_BEGIN`
- `u64 revision`
- `u8 is_full_sync` (`1` = full snapshot, `0` = incremental diff)
- `f32 unit_scale`
- `str scene_name`
- `str view_layer_name`

### `4: MESH_ASSET`
- `u64 content_hash`
- `str mesh_name`
- `f32[3] bounds_center` (Unity space)
- `f32[3] bounds_extents` (Unity space half-size)
- `u32 vertex_count`
- `u32 vertex_stride`
- `u8 attr_count`
  - Repeated `attr_count` times:
    - `u8 semantic` (`0`=Position, `1`=Normal, `2`=Tangent, `3`=Color, `4..11`=TexCoord0..7)
    - `u8 format` (`0`=Float32, `1`=Float16, `2`=UNorm8, `3`=SInt32)
    - `u8 dimension` (`1..4`)
    - `u8 stream` (`0`)
    - `u32 offset`
- `u8 index_format` (`0` = UInt16, `1` = UInt32)
- `u32 index_count`
- `u8 submesh_count`
  - Repeated `submesh_count` times:
    - `u32 index_start`
    - `u32 index_count`
    - `u32 material_slot`
    - `f32[3] submesh_bounds_center`
    - `f32[3] submesh_bounds_extents`
- `u32 vertex_buffer_byte_len`
- `byte[vertex_buffer_byte_len] vertex_buffer`
- `u32 index_buffer_byte_len`
- `byte[index_buffer_byte_len] index_buffer`

### `5: MATERIAL_UPSERT`
- `u64 content_hash`
- `str material_name`
- `str json_descriptor` (PBR values, mode, alpha clip, two-sided, attribute bindings, texture map slots, optional generated HLSL code)

### `6: TEXTURE_FILE`
- `u64 content_hash`
- `str texture_name`
- `str file_ext` (e.g., `".png"`, `".exr"`)
- `u8 is_srgb` (`1` = sRGB albedo/emission, `0` = Linear normal/roughness/metallic/AO)
- `u32 width`
- `u32 height`
- `u32 data_len`
- `byte[data_len] image_file_bytes`

### `7: OBJECT_UPSERT`
- `str stable_id`
- `str object_name`
- `str collection_path` (e.g., `"Environment/Props"`)
- `u8 object_type` (`0`=Mesh, `1`=Light, `2`=Camera, `3`=Empty)
- `f32[16] world_matrix` (row-major 4x4 in Unity space)
- `u8 flags` (`bit 0`=cast_shadows, `bit 1`=receive_shadows, `bit 2`=is_static_candidate)
- `u16 chunk_count`
  - `u64[chunk_count] mesh_chunk_hashes`
- `u16 material_slot_count`
  - `str[material_slot_count] material_names`
- `str custom_props_json` (optional light/camera/custom properties)

### `8: OBJECT_REMOVE`
- `u32 count`
- `str[count] stable_ids`

### `9: TRANSFORM_BATCH`
- `u32 count`
- Repeated `count` times:
  - `str stable_id`
  - `f32[16] world_matrix` (row-major 4x4 in Unity space)

### `10: INSTANCE_SET`
- `str owner_stable_id`
- `str source_object_name`
- `str collection_path`
- `u64 mesh_hash`
- `u16 material_count`
  - `str[material_count] material_names`
- `u32 instance_count`
- `u8 has_instance_data` (`1` if per-instance `float4` array is present)
- `byte[instance_count * 64] matrices_buffer` (`float32[instance_count, 16]` row-major Unity world matrices)
- If `has_instance_data == 1`:
  - `byte[instance_count * 16] instance_data_buffer` (`float32[instance_count, 4]`)

### `11: ATTRIBUTE_MAP`
- `str json_manifest` (serialized attribute channel allocations and remap ranges)

### `12: SCENE_END`
- `u64 revision`
- `u32 total_objects`
- `u64 total_triangles`
- `u32 total_instances`
- `f32 blender_extract_ms`
