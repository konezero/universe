"""OS-observed Windows loopback peer identity; never trust a requester PID."""
from __future__ import annotations

import ctypes
import socket
import sys
from ctypes import wintypes

from universe_app import windows_process as processes


class _TcpRow(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in
                ("state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]


def connection_peer(client, server):
    """Resolve the client half of this exact established IPv4 TCP connection."""
    if sys.platform != "win32" or client[0] != "127.0.0.1" or server[0] != "127.0.0.1":
        return None
    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD()
    if api(None, ctypes.byref(size), False, socket.AF_INET, 5, 0) != 122:
        return None
    for _ in range(3):
        buf = ctypes.create_string_buffer(size.value)
        result = api(buf, ctypes.byref(size), False, socket.AF_INET, 5, 0)
        if result == 122:
            continue
        if result:
            return None
        count = wintypes.DWORD.from_buffer(buf).value
        address = int.from_bytes(socket.inet_aton("127.0.0.1"), "little")
        matches = []
        for i in range(count):
            row = _TcpRow.from_buffer(buf, 4 + i * ctypes.sizeof(_TcpRow))
            if (row.state == 5 and row.local_addr == address and row.remote_addr == address
                    and socket.ntohs(row.local_port & 0xffff) == client[1]
                    and socket.ntohs(row.remote_port & 0xffff) == server[1]):
                matches.append(int(row.pid))
        return process_identity(matches[0]) if len(matches) == 1 else None
    return None


def process_identity(pid):
    started = processes.process_start_time(pid)
    parent = processes.parent_pid(pid)
    parent_started = processes.process_start_time(parent) if parent else None
    image = processes.process_image_path(pid)
    if not started or not parent_started or not image or not processes.process_is_alive(pid):
        return None
    return {"pid": pid, "started": started, "parent": parent,
            "parent_started": parent_started, "image": image.casefold()}


def replacement_allowed(previous, current):
    if not previous or not current or previous == current:
        return False
    if any(previous[k] != current[k] for k in ("parent", "parent_started", "image")):
        return False
    # Do not rotate while a live old client might still be executing, nor after
    # owner exit/PID reuse. A new process must postdate the authenticated client.
    if current["started"] <= previous["started"]:
        return False
    if (processes.process_start_time(previous["pid"]) == previous["started"]
            and processes.process_is_alive(previous["pid"])):
        return False
    return (processes.process_is_alive(current["parent"])
            and processes.process_start_time(current["parent"]) == current["parent_started"]
            and process_identity(current["pid"]) == current)
