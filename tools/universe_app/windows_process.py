"""Windows-native process inspection for the managed shell lifecycle.

The managed cmd shell needs three facts about processes it owns: whether one is
still running, when it started, and which processes are its direct children.
``psutil`` is not installed on the service or test interpreters, so relying on
it left the whole lifecycle permanently degraded. This module answers those
questions with ctypes against kernel32 and keeps ``psutil`` as an optional
fallback for non-Windows hosts.

Start time is always paired with the PID by callers: Windows reuses PIDs, and a
bare PID would let an unrelated process inherit a terminal's lifecycle.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any, Callable


# 100ns ticks between 1601-01-01 (FILETIME epoch) and 1970-01-01 (Unix epoch).
_FILETIME_UNIX_DELTA = 116_444_736_000_000_000
_TICKS_PER_SECOND = 10_000_000

_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102
_MAX_PATH = 260


def native_inspection_available() -> bool:
    """True when this Host can answer process questions natively."""

    return sys.platform == "win32"


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


_KERNEL32: Any = None


def _kernel32() -> Any:
    """Bind kernel32 once with explicit prototypes.

    Without restype/argtypes ctypes defaults a HANDLE-returning call to
    ``c_int``.  On 64-bit Python that truncates or sign-extends real handle
    values, so a call can appear to fail or operate on a bogus handle.  Every
    API used here is declared explicitly.
    """

    global _KERNEL32
    if _KERNEL32 is not None:
        return _KERNEL32
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]

    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32W),
    ]

    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessEntry32W),
    ]

    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]

    _KERNEL32 = kernel32
    return _KERNEL32


def process_start_time(pid: int) -> float | None:
    """Return the process creation time as Unix epoch seconds, or None."""

    if not native_inspection_available() or not (isinstance(pid, int) and pid > 0):
        return None
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid)
    )
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            wintypes.HANDLE(handle),
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return (ticks - _FILETIME_UNIX_DELTA) / _TICKS_PER_SECOND
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def process_is_alive(pid: int) -> bool:
    """True when the process is still running.

    Uses a zero-timeout wait rather than GetExitCodeProcess, because a process
    may legitimately exit with code 259 (STILL_ACTIVE) and be misread as live.
    """

    if not native_inspection_available() or not (isinstance(pid, int) and pid > 0):
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid)
    )
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(wintypes.HANDLE(handle), 0) == _WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def terminate_process_instance(
    pid: int,
    expected_started_at: float,
    *,
    tolerance: float = 0.5,
) -> bool:
    """Terminate only the exact PID/start-time instance owned by the Supervisor."""

    if not native_inspection_available() or not (isinstance(pid, int) and pid > 0):
        return False
    observed = process_start_time(pid)
    if observed is None or abs(float(observed) - float(expected_started_at)) > tolerance:
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        wintypes.DWORD(pid),
    )
    if not handle:
        return False
    try:
        # Re-read after opening the kill-capable handle.  This closes the PID
        # reuse window between the first observation and TerminateProcess.
        current = process_start_time(pid)
        if current is None or abs(float(current) - float(expected_started_at)) > tolerance:
            return False
        return bool(kernel32.TerminateProcess(wintypes.HANDLE(handle), 0))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def child_pids(pid: int) -> list[int]:
    """Return direct child PIDs of one process via a Toolhelp32 snapshot."""

    if not native_inspection_available() or not (isinstance(pid, int) and pid > 0):
        return []
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE or not snapshot:
        return []
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not kernel32.Process32FirstW(
            wintypes.HANDLE(snapshot), ctypes.byref(entry)
        ):
            return []
        children: list[int] = []
        while True:
            if int(entry.th32ParentProcessID) == int(pid):
                children.append(int(entry.th32ProcessID))
            if not kernel32.Process32NextW(
                wintypes.HANDLE(snapshot), ctypes.byref(entry)
            ):
                break
        return children
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(snapshot))


def parent_pid(pid: int) -> int | None:
    """Return the parent PID of one process, or None."""

    if not native_inspection_available() or not (isinstance(pid, int) and pid > 0):
        return None
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE or not snapshot:
        return None
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not kernel32.Process32FirstW(
            wintypes.HANDLE(snapshot), ctypes.byref(entry)
        ):
            return None
        while True:
            if int(entry.th32ProcessID) == int(pid):
                return int(entry.th32ParentProcessID)
            if not kernel32.Process32NextW(
                wintypes.HANDLE(snapshot), ctypes.byref(entry)
            ):
                break
        return None
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(snapshot))


def process_name(pid: int) -> str:
    """Return the executable name of one process, or an empty string."""

    if not native_inspection_available() or not (isinstance(pid, int) and pid > 0):
        return ""
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE or not snapshot:
        return ""
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        if not kernel32.Process32FirstW(
            wintypes.HANDLE(snapshot), ctypes.byref(entry)
        ):
            return ""
        while True:
            if int(entry.th32ProcessID) == int(pid):
                return str(entry.szExeFile)
            if not kernel32.Process32NextW(
                wintypes.HANDLE(snapshot), ctypes.byref(entry)
            ):
                break
        return ""
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(snapshot))


def process_image_path(pid: int) -> str:
    """Return the full image path of one process, or an empty string."""

    if not native_inspection_available() or not (isinstance(pid, int) and pid > 0):
        return ""
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid)
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(
            wintypes.HANDLE(handle), 0, buf, ctypes.byref(size)
        )
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def native_probes() -> dict[str, Callable[..., Any]] | None:
    """Build native process probes, or None when this Host cannot inspect."""

    if not native_inspection_available():
        return None
    return {
        "is_alive": process_is_alive,
        "start_time_of": process_start_time,
        "child_pids": child_pids,
        "parent_pid": parent_pid,
        "process_name": process_name,
        "process_image_path": process_image_path,
    }
