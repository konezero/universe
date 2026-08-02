from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
# Subprocess is limited to fixed-argv, read-only Host inventory collection.
import subprocess  # nosec B404
from typing import Any, Mapping, Sequence

from process_identity import redact_sensitive_argv


IDENTITY_FIELDS = (
    "pid",
    "process_created_at",
    "executable",
    "command",
    "endpoint",
    "handshake_fingerprint",
)


def is_session_boot_executor(command: Any) -> bool:
    if not isinstance(command, list) or not all(
        isinstance(item, str) for item in command
    ):
        return False
    lowered = [item.lower() for item in command]
    return any(
        lowered[index : index + 2] == ["session-boot", "serve"]
        for index in range(max(0, len(lowered) - 1))
    )


def _public_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in observation.items() if key != "command"}
    command = observation.get("command")
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        serialized = json.dumps(
            command, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        public["command_profile"] = (
            "SESSION_BOOT_SERVE" if is_session_boot_executor(command) else "OTHER"
        )
        public["command_fingerprint"] = hashlib.sha256(serialized).hexdigest()
    else:
        public["command_profile"] = "UNKNOWN"
        public["command_fingerprint"] = None
    return public


def classify_executor(
    observation: Mapping[str, Any],
    managed_sessions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    observed = dict(observation)
    public_observation = _public_observation(observed)
    if not is_session_boot_executor(observed.get("command")):
        return {
            "status": "OUT_OF_SCOPE",
            "observation": public_observation,
            "destructive_action_permitted": False,
        }

    partial_matches: list[str] = []
    for session in managed_sessions:
        lease = session.get("process_lease")
        if not isinstance(lease, Mapping):
            continue
        expected = lease.get("process_identity")
        if not isinstance(expected, Mapping):
            continue
        if all(
            field in observed
            and observed.get(field) is not None
            and observed.get(field) == expected.get(field)
            for field in IDENTITY_FIELDS
        ):
            return {
                "status": "MANAGED_EXACT",
                "session_id": session.get("session_id"),
                "lease_state": lease.get("lease_state"),
                "observation": public_observation,
                "destructive_action_permitted": False,
                "required_route": "SESSION_SUPERVISOR",
            }
        strong_overlap = any(
            observed.get(field) is not None
            and observed.get(field) == expected.get(field)
            for field in ("pid", "endpoint", "handshake_fingerprint")
        )
        if strong_overlap:
            partial_matches.append(str(session.get("session_id") or "UNKNOWN"))

    if partial_matches:
        return {
            "status": "UNKNOWN",
            "reason": "PARTIAL_PROCESS_IDENTITY_MATCH",
            "candidate_session_ids": partial_matches,
            "observation": public_observation,
            "destructive_action_permitted": False,
        }
    return {
        "status": "UNMANAGED",
        "reason": "NO_SUPERVISOR_LEASE_MATCH",
        "observation": public_observation,
        "destructive_action_permitted": False,
    }


def classify_inventory(
    observations: Sequence[Mapping[str, Any]],
    managed_sessions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        classified
        for observation in observations
        if (classified := classify_executor(observation, managed_sessions))["status"]
        != "OUT_OF_SCOPE"
    ]


def collect_windows_session_boot_executors() -> dict[str, Any]:
    if os.name != "nt":
        return {
            "status": "HOST_INVENTORY_UNAVAILABLE",
            "reason": "WINDOWS_REQUIRED",
            "observations": [],
        }
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        return {
            "status": "HOST_INVENTORY_UNAVAILABLE",
            "reason": "POWERSHELL_UNAVAILABLE",
            "observations": [],
        }
    script = (
        "$ErrorActionPreference='Stop';"
        "$items=@(Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'session-boot' -and "
        "$_.CommandLine -match 'serve' } | ForEach-Object { "
        "[pscustomobject]@{pid=[int]$_.ProcessId;"
        "process_created_at=$_.CreationDate.ToUniversalTime()."
        "ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ');"
        "executable=[string]$_.ExecutablePath;"
        "command_line=[string]$_.CommandLine} });"
        "$items | ConvertTo-Json -Compress -Depth 3"
    )
    # The executable is resolved to an absolute path and the script is fixed.
    result = subprocess.run(  # nosec B603
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0:
        return {
            "status": "HOST_INVENTORY_UNAVAILABLE",
            "reason": "PROCESS_INVENTORY_FAILED",
            "detail": result.stderr.strip()[:500],
            "observations": [],
        }
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return {
            "status": "HOST_INVENTORY_UNAVAILABLE",
            "reason": "PROCESS_INVENTORY_INVALID",
            "observations": [],
        }
    items = payload if isinstance(payload, list) else [payload]
    observations: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw_command = item.get("command_line")
        if not isinstance(raw_command, str) or not raw_command.strip():
            continue
        observations.append(
            {
                "pid": item.get("pid"),
                "process_created_at": item.get("process_created_at"),
                "executable": item.get("executable"),
                "command": redact_sensitive_argv(
                    _windows_command_line_to_argv(raw_command)
                ),
                # Legacy executors do not expose these values to the Supervisor.
                # Their absence intentionally prevents exact adoption.
                "endpoint": None,
                "handshake_fingerprint": None,
            }
        )
    return {"status": "HOST_INVENTORY_OBSERVED", "observations": observations}


def _windows_command_line_to_argv(command_line: str) -> list[str]:
    argc = ctypes.c_int()
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    shell32.CommandLineToArgvW.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(argc))
    if not argv:
        return [command_line]
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        kernel32.LocalFree(argv)
