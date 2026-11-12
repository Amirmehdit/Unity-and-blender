"""
Non-blocking loopback TCP client with background sender thread and `.bsync` snapshot writer (Section 4.2).
- Only the main Blender thread touches `bpy` data.
- A background worker thread handles socket connection, framing transmission, and incoming control messages.
"""

from __future__ import annotations
import os
import queue
import socket
import threading
import time
import uuid
from typing import Any, Callable

from .protocol import (
    HEADER_STRUCT,
    MessageType,
    decode_frame_header,
    decode_frame_payload,
    encode_hello,
    decode_hello_ack,
    frame_message,
    unpack_str,
)


def write_snapshot_file(filepath: str, frames: list[bytes]) -> int:
    """
    Write an ordered list of BSYN protocol frames to a `.bsync` snapshot file atomically.
    Returns total bytes written.
    """
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    tmp_path = filepath + ".tmp"
    total_bytes = 0
    with open(tmp_path, "wb") as f:
        for frame in frames:
            f.write(frame)
            total_bytes += len(frame)
    os.replace(tmp_path, filepath)
    return total_bytes


class LiveTcpClient:
    """
    Loopback TCP client connecting to Unity Editor (127.0.0.1:port).
    Automatically reconnects on connection drop and processes HELLO / HELLO_ACK handshakes.
    """

    def __init__(self) -> None:
        self.host: str = "127.0.0.1"
        self.port: int = 52731
        self.token: str = ""
        self.session_id: str = str(uuid.uuid4())
        self.blender_version: str = "5.0.0"
        self.blend_filepath: str = ""

        self._send_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=4096)
        self._incoming_queue: queue.Queue[tuple[MessageType, Any]] = queue.Queue()
        self._running: bool = False
        self._connected: bool = False
        self._handshake_complete: bool = False
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self.bytes_sent: int = 0
        self.last_error: str = ""

    @property
    def is_connected(self) -> bool:
        return self._connected and self._handshake_complete

    def start(self, host: str, port: int, token: str, blender_version: str, blend_filepath: str) -> None:
        self.stop()
        # Security requirement (Section 4.2 & 10): Bind/connect to loopback only
        if host not in {"127.0.0.1", "localhost", "::1"}:
            self.last_error = f"Refusing non-loopback host '{host}'; using 127.0.0.1"
            host = "127.0.0.1"
        self.host = host
        self.port = int(port)
        self.token = token
        self.blender_version = blender_version
        self.blend_filepath = blend_filepath
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="BlenderSyncTcpWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._connected = False
        self._handshake_complete = False
        try:
            self._send_queue.put_nowait(None)
        except Exception:
            pass
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def enqueue_frames(self, frames: list[bytes]) -> None:
        if not self._running:
            return
        for frame in frames:
            try:
                self._send_queue.put_nowait(frame)
            except queue.Full:
                self.last_error = "Send queue full; dropping intermediate frame"
                break

    def poll_incoming_events(self) -> list[tuple[MessageType, Any]]:
        events: list[tuple[MessageType, Any]] = []
        while True:
            try:
                events.append(self._incoming_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n and self._running:
            try:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf.extend(chunk)
            except socket.timeout:
                continue
            except OSError:
                return None
        return bytes(buf) if len(buf) == n else None

    def _worker_loop(self) -> None:
        while self._running:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(2.0)
                sock.connect((self.host, self.port))
                self._sock = sock
                self._connected = True
                self.last_error = ""

                # Send HELLO handshake
                hello_frame = encode_hello(
                    self.token, self.session_id, self.blender_version, self.blend_filepath
                )
                sock.sendall(hello_frame)
                self.bytes_sent += len(hello_frame)

                # Expect HELLO_ACK
                hdr_bytes = self._recv_exact(sock, HEADER_STRUCT.size)
                if not hdr_bytes:
                    raise ConnectionError("Connection closed during HELLO_ACK")
                _, flags, msg_type, payload_len = decode_frame_header(hdr_bytes)
                raw_payload = self._recv_exact(sock, payload_len) if payload_len > 0 else b""
                if raw_payload is None:
                    raise ConnectionError("Truncated HELLO_ACK payload")
                payload = decode_frame_payload(flags, raw_payload)
                if msg_type != MessageType.HELLO_ACK:
                    raise ConnectionError(f"Expected HELLO_ACK, got {msg_type}")

                ack = decode_hello_ack(payload)
                if not ack["accepted"]:
                    self.last_error = f"Handshake rejected by Unity: {ack['message']}"
                    time.sleep(2.0)
                    continue

                self._handshake_complete = True
                self._incoming_queue.put((MessageType.HELLO_ACK, ack))
                sock.setblocking(False)

                # Main non-blocking IO pump
                recv_buffer = bytearray()
                while self._running and self._connected:
                    # 1. Drain outbound queue
                    sent_any = False
                    while self._running:
                        try:
                            frame = self._send_queue.get_nowait()
                        except queue.Empty:
                            break
                        if frame is None:
                            return
                        sock.setblocking(True)
                        sock.settimeout(5.0)
                        sock.sendall(frame)
                        sock.setblocking(False)
                        self.bytes_sent += len(frame)
                        sent_any = True

                    # 2. Check inbound control messages from Unity
                    try:
                        chunk = sock.recv(65536)
                        if chunk == b"":
                            raise ConnectionError("Unity server closed connection")
                        recv_buffer.extend(chunk)
                    except BlockingIOError:
                        pass

                    while len(recv_buffer) >= HEADER_STRUCT.size:
                        _, flags, mtype, plen = decode_frame_header(bytes(recv_buffer[:HEADER_STRUCT.size]))
                        if len(recv_buffer) < HEADER_STRUCT.size + plen:
                            break
                        raw_p = bytes(recv_buffer[HEADER_STRUCT.size : HEADER_STRUCT.size + plen])
                        del recv_buffer[: HEADER_STRUCT.size + plen]
                        dec_p = decode_frame_payload(flags, raw_p)
                        if mtype == MessageType.PING:
                            pong = frame_message(MessageType.PONG, dec_p)
                            self._send_queue.put_nowait(pong)
                        elif mtype in {MessageType.REQUEST_FULL_RESYNC, MessageType.REQUEST_OBJECT, MessageType.SELECT_OBJECT}:
                            arg_str = ""
                            if len(dec_p) >= 4:
                                arg_str, _ = unpack_str(dec_p, 0)
                            self._incoming_queue.put((mtype, arg_str))

                    if not sent_any:
                        time.sleep(0.005)

            except Exception as exc:
                self._connected = False
                self._handshake_complete = False
                if self._running:
                    self.last_error = str(exc)
                    time.sleep(1.0)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
