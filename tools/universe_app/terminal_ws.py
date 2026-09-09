"""Minimal RFC6455 WebSocket pump for one terminal tab."""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import secrets
import select
import socket
import struct
import threading
import time
from typing import Any

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TERMINAL_WS_HEARTBEAT_INTERVAL_SECONDS = 20.0
TERMINAL_WS_HEARTBEAT_TIMEOUT_SECONDS = 45.0
TERMINAL_WS_IO_POLL_SECONDS = 0.2
TERMINAL_WS_SEND_TIMEOUT_SECONDS = 10.0
TERMINAL_WS_OUTBOUND_QUEUE_SIZE = 256


def websocket_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_ws_frame(payload: bytes, opcode: int = 2) -> bytes:
    header = bytearray()
    header.append(0x80 | (opcode & 0x0F))
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    return bytes(header) + payload


def decode_ws_frame(buffer: bytearray) -> tuple[int, bytes, int] | None:
    if len(buffer) < 2:
        return None
    first, second = buffer[0], buffer[1]
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    index = 2
    if length == 126:
        if len(buffer) < 4:
            return None
        length = struct.unpack("!H", buffer[2:4])[0]
        index = 4
    elif length == 127:
        if len(buffer) < 10:
            return None
        length = struct.unpack("!Q", buffer[2:10])[0]
        index = 10
    mask = b""
    if masked:
        if len(buffer) < index + 4:
            return None
        mask = bytes(buffer[index : index + 4])
        index += 4
    if len(buffer) < index + length:
        return None
    payload = bytes(buffer[index : index + length])
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload, index + length


class _SerializedWebSocketSender:
    """Own all writes for one WebSocket so frame bytes cannot interleave."""

    def __init__(
        self,
        sock: socket.socket,
        stop: threading.Event,
        *,
        queue_size: int = TERMINAL_WS_OUTBOUND_QUEUE_SIZE,
        enqueue_poll_seconds: float = TERMINAL_WS_IO_POLL_SECONDS,
    ) -> None:
        self._sock = sock
        self._stop = stop
        self._enqueue_poll_seconds = enqueue_poll_seconds
        self._outbound: queue.PriorityQueue[tuple[int, int, int, bytes]] = (
            queue.PriorityQueue(maxsize=queue_size)
        )
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            name="term-ws-send",
            daemon=True,
        )
        self._thread.start()

    def send(self, payload: bytes, *, opcode: int) -> bool:
        # Control frames jump ahead of queued terminal output. Data frames keep
        # their original enqueue order within the lower-priority lane.
        priority = 0 if opcode in {8, 9, 10} else 1
        with self._sequence_lock:
            sequence = self._sequence
            self._sequence += 1
        item = (priority, sequence, opcode, payload)
        while not self._stop.is_set():
            try:
                self._outbound.put(item, timeout=self._enqueue_poll_seconds)
                return True
            except queue.Full:
                continue
        return False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _priority, _sequence, opcode, payload = self._outbound.get(
                    timeout=self._enqueue_poll_seconds
                )
            except queue.Empty:
                continue
            try:
                self._sock.sendall(encode_ws_frame(payload, opcode=opcode))
            except OSError:
                self._stop.set()
            finally:
                self._outbound.task_done()

    def close(self, *, timeout: float = 1.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)


def pump_terminal_socket(
    handler: Any,
    terminal_id: str,
    host: Any,
    *,
    heartbeat_interval_seconds: float = TERMINAL_WS_HEARTBEAT_INTERVAL_SECONDS,
    heartbeat_timeout_seconds: float = TERMINAL_WS_HEARTBEAT_TIMEOUT_SECONDS,
    io_poll_seconds: float = TERMINAL_WS_IO_POLL_SECONDS,
    send_timeout_seconds: float = TERMINAL_WS_SEND_TIMEOUT_SECONDS,
) -> None:
    sock: socket.socket = handler.connection
    # A short socket-wide timeout also applies to sendall(). Under output
    # backpressure that used to kill only the output thread while leaving the
    # apparently-open WebSocket frozen. select() now owns the short read poll;
    # this timeout is reserved for a genuinely stuck write.
    sock.settimeout(send_timeout_seconds)
    stop = threading.Event()
    waiter = host.subscribe(terminal_id)
    sender = _SerializedWebSocketSender(
        sock,
        stop,
        enqueue_poll_seconds=io_poll_seconds,
    )

    def emit() -> None:
        while not stop.is_set():
            try:
                chunk = waiter.get(timeout=io_poll_seconds)
            except Exception:
                continue
            if chunk is None:
                break
            if chunk and not sender.send(chunk, opcode=2):
                break

    reader = threading.Thread(target=emit, name="term-ws-out", daemon=True)
    reader.start()
    buffer = bytearray()
    last_ping_at = time.monotonic()
    pending_ping: tuple[bytes, float] | None = None
    try:
        while not stop.is_set():
            now = time.monotonic()
            if (
                pending_ping is not None
                and now - pending_ping[1] >= heartbeat_timeout_seconds
            ):
                break
            if (
                pending_ping is None
                and now - last_ping_at >= heartbeat_interval_seconds
            ):
                ping_payload = secrets.token_bytes(8)
                if not sender.send(ping_payload, opcode=9):
                    break
                pending_ping = (ping_payload, now)
                last_ping_at = now
            try:
                readable, _writable, _exceptional = select.select(
                    [sock], [], [], io_poll_seconds
                )
                if not readable:
                    continue
                piece = sock.recv(4096)
            except socket.timeout:
                continue
            except (OSError, ValueError):
                break
            if not piece:
                break
            buffer.extend(piece)
            while True:
                decoded = decode_ws_frame(buffer)
                if decoded is None:
                    break
                opcode, payload, consumed = decoded
                del buffer[:consumed]
                if opcode in {8, 0x8}:
                    stop.set()
                    break
                if opcode == 9:
                    if not sender.send(payload, opcode=10):
                        stop.set()
                        break
                    continue
                if opcode == 10:
                    if pending_ping is not None and payload == pending_ping[0]:
                        pending_ping = None
                    continue
                if opcode == 1:
                    try:
                        message = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        if payload:
                            try:
                                host.write(terminal_id, payload)
                            except Exception:
                                pass
                        continue
                    kind = str(message.get("type") or "").lower()
                    if kind == "resize":
                        try:
                            host.resize(
                                terminal_id,
                                int(message.get("cols") or 120),
                                int(message.get("rows") or 32),
                            )
                        except Exception:
                            pass
                    elif kind == "input":
                        text = str(message.get("data") or "")
                        if text:
                            if not text.endswith(("\n", "\r")):
                                text += "\r"
                            try:
                                host.write(terminal_id, text.encode("utf-8"))
                            except Exception:
                                pass
                    elif payload:
                        try:
                            host.write(terminal_id, payload)
                        except Exception:
                            pass
                    continue
                if opcode == 2 and payload:
                    try:
                        host.write(terminal_id, payload)
                    except Exception:
                        pass
    finally:
        stop.set()
        try:
            host.unsubscribe(terminal_id, waiter)
        except Exception:
            pass
        reader.join(timeout=1)
        sender.close(timeout=1)
