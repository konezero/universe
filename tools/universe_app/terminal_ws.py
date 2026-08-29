"""Minimal RFC6455 WebSocket pump for one terminal tab."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from typing import Any

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


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


def pump_terminal_socket(handler: Any, terminal_id: str, host: Any) -> None:
    sock: socket.socket = handler.connection
    sock.settimeout(0.2)
    stop = threading.Event()
    waiter = host.subscribe(terminal_id)

    def emit() -> None:
        while not stop.is_set():
            try:
                chunk = waiter.get(timeout=0.2)
            except Exception:
                continue
            if chunk is None:
                break
            if chunk:
                try:
                    sock.sendall(encode_ws_frame(chunk, opcode=2))
                except OSError:
                    break

    reader = threading.Thread(target=emit, name="term-ws-out", daemon=True)
    reader.start()
    buffer = bytearray()
    try:
        while not stop.is_set():
            try:
                piece = sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
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
                    sock.sendall(encode_ws_frame(payload, opcode=10))
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
