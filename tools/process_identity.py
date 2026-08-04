from __future__ import annotations

import ctypes
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class ProcessIdentityError(RuntimeError):
    pass


class WindowsKillOnCloseJob:
    """Attach a runtime child to a Windows Job owned by its parent host."""

    def __init__(self, process: Any) -> None:
        self._handle: Any | None = None
        if os.name != "nt" or not getattr(process, "_handle", None):
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_void_p),
                ("MaximumWorkingSetSize", ctypes.c_void_p),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [("values", ctypes.c_ulonglong * 6)]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_void_p),
                ("JobMemoryLimit", ctypes.c_void_p),
                ("PeakProcessMemoryUsed", ctypes.c_void_p),
                ("PeakJobMemoryUsed", ctypes.c_void_p),
            ]

        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        if not kernel32.AssignProcessToJobObject(handle, process._handle):
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        self._handle = handle
        self._kernel32 = kernel32

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class OwnedProcess(Protocol):
    pid: int


SENSITIVE_VALUE_FLAGS = frozenset(
    {
        "--token",
        "--api-key",
        "--password",
        "--secret",
        "--auth",
        "--bearer",
        "--access-token",
        "--client-secret",
        "--connection-string",
    }
)


def redact_sensitive_argv(
    command: Sequence[str], *, sensitive_values: Sequence[str] = ()
) -> list[str]:
    normalized = list(command)
    exact_secrets = {value for value in sensitive_values if value}
    result: list[str] = []
    redact_next = False
    for argument in normalized:
        if argument in exact_secrets:
            result.append(
                "sha256:" + hashlib.sha256(argument.encode("utf-8")).hexdigest()
            )
            redact_next = False
            continue
        if redact_next:
            if argument.startswith("sha256:") and len(argument) == 71:
                result.append(argument)
            else:
                result.append(
                    "sha256:" + hashlib.sha256(argument.encode("utf-8")).hexdigest()
                )
            redact_next = False
            continue
        flag, separator, inline_value = argument.partition("=")
        if separator and inline_value in exact_secrets:
            result.append(
                f"{flag}=sha256:"
                + hashlib.sha256(inline_value.encode("utf-8")).hexdigest()
            )
            continue
        if separator and flag.lower() in SENSITIVE_VALUE_FLAGS:
            if inline_value.startswith("sha256:") and len(inline_value) == 71:
                redacted_value = inline_value
            else:
                redacted_value = "sha256:" + hashlib.sha256(
                    inline_value.encode("utf-8")
                ).hexdigest()
            result.append(f"{flag}={redacted_value}")
            continue
        result.append(argument)
        redact_next = argument.lower() in SENSITIVE_VALUE_FLAGS
    return result


def launched_process_identity(
    process: OwnedProcess,
    *,
    executable: Path,
    command: Sequence[str],
    endpoint: str,
    handshake_token: str,
) -> dict[str, Any]:
    normalized_command = list(command)
    if not normalized_command or any(not isinstance(item, str) for item in normalized_command):
        raise ProcessIdentityError("command must be an exact non-empty argument array")
    return {
        "pid": int(process.pid),
        "process_created_at": process_created_at(process),
        "executable": str(executable.expanduser().resolve()),
        "command": redact_sensitive_argv(
            normalized_command, sensitive_values=(handshake_token,)
        ),
        "endpoint": str(endpoint),
        "handshake_fingerprint": hashlib.sha256(
            handshake_token.encode("utf-8")
        ).hexdigest(),
    }


def process_created_at(process: OwnedProcess) -> str:
    if os.name != "nt":
        raise ProcessIdentityError(
            "exact process creation time is unavailable on this Host"
        )
    handle = getattr(process, "_handle", None)
    if handle is None:
        raise ProcessIdentityError("owned process handle is unavailable")

    class FILETIME(ctypes.Structure):
        _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))

    creation = FILETIME()
    exit_time = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    get_process_times = ctypes.windll.kernel32.GetProcessTimes
    get_process_times.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    )
    get_process_times.restype = ctypes.c_int
    if not get_process_times(
        ctypes.c_void_p(int(handle)),
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise ProcessIdentityError("GetProcessTimes failed")
    ticks = (int(creation.high) << 32) | int(creation.low)
    return _filetime_timestamp(ticks)


def process_instance_observation(pid: int, expected_created_at: str) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "UNKNOWN", "reason": "WINDOWS_REQUIRED"}
    try:
        expected = _canonical_timestamp(expected_created_at)
    except (TypeError, ValueError):
        return {"status": "UNKNOWN", "reason": "EXPECTED_CREATION_TIME_INVALID"}

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    handle = open_process(
        process_query_limited_information | synchronize, 0, int(pid)
    )
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return {
                "status": "ORIGINAL_PROCESS_ABSENT",
                "reason": "PID_NOT_RUNNING",
                "pid": int(pid),
                "expected_process_created_at": expected,
            }
        return {
            "status": "UNKNOWN",
            "reason": "PROCESS_QUERY_FAILED",
            "pid": int(pid),
            "win32_error": int(error),
        }

    try:
        wait_object_0 = 0
        wait_timeout = 258
        wait_failed = 0xFFFFFFFF
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        wait_for_single_object.restype = ctypes.c_uint32
        wait_status = int(wait_for_single_object(handle, 0))
        if wait_status == wait_failed:
            return {
                "status": "UNKNOWN",
                "reason": "PROCESS_WAIT_QUERY_FAILED",
                "pid": int(pid),
                "win32_error": int(ctypes.get_last_error()),
            }
        if wait_status == wait_object_0:
            return {
                "status": "ORIGINAL_PROCESS_ABSENT",
                "reason": "PROCESS_EXITED",
                "pid": int(pid),
                "expected_process_created_at": expected,
            }
        if wait_status != wait_timeout:
            return {
                "status": "UNKNOWN",
                "reason": "PROCESS_WAIT_STATUS_UNKNOWN",
                "pid": int(pid),
                "wait_status": wait_status,
            }

        class FILETIME(ctypes.Structure):
            _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))

        creation = FILETIME()
        exit_time = FILETIME()
        kernel = FILETIME()
        user = FILETIME()
        get_process_times = kernel32.GetProcessTimes
        get_process_times.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        )
        get_process_times.restype = ctypes.c_int
        if not get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return {
                "status": "UNKNOWN",
                "reason": "PROCESS_TIME_QUERY_FAILED",
                "pid": int(pid),
                "win32_error": int(ctypes.get_last_error()),
            }
        observed = _filetime_timestamp((int(creation.high) << 32) | int(creation.low))
    finally:
        kernel32.CloseHandle(handle)
    if observed != expected:
        return {
            "status": "ORIGINAL_PROCESS_ABSENT",
            "reason": "PID_REUSED",
            "pid": int(pid),
            "expected_process_created_at": expected,
            "observed_process_created_at": observed,
        }
    return {
        "status": "PROCESS_PRESENT_EXACT",
        "pid": int(pid),
        "process_created_at": observed,
    }


def _canonical_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _filetime_timestamp(ticks: int) -> str:
    unix_ticks = ticks - 116444736000000000
    seconds, remainder = divmod(unix_ticks, 10_000_000)
    observed = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=seconds,
        microseconds=remainder // 10,
    )
    return observed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def exact_identity_match(
    expected: Mapping[str, Any], observed: Mapping[str, Any]
) -> bool:
    fields = (
        "pid",
        "process_created_at",
        "executable",
        "command",
        "endpoint",
        "handshake_fingerprint",
    )
    return all(expected.get(field) == observed.get(field) for field in fields)
