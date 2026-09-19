"""Entry point and detached launcher for one independent Task Frame Host.

``launch`` starts this module as its own process (no console, own process
group) so the Host outlives the caller and the server.  The child writes a
heartbeat file (pid, phase, updated_at) that the Master or an operator can read
to tell a live Host from a dead one.  Roles are run by a runner factory named as
``module:callable``; the factory receives the parsed spec and returns a
``RoleRunner``.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_frame_host import HostConfig, TaskFrameHost  # noqa: E402
from task_frame_host_transport import HttpBusPort, HttpRoomPort, HttpTodoPort  # noqa: E402

SPEC_SCHEMA = "universe.task-frame-host-spec.v1"
_REQUIRED = ("base_url", "room_id", "task_frame_id", "todo_id", "bus_to", "bus_from", "runner")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_heartbeat(path: Path, spec: Mapping[str, Any], phase: str, extra: Mapping[str, Any] | None = None) -> None:
    body = {
        "schema": "universe.task-frame-host-heartbeat.v1",
        "pid": os.getpid(),
        "task_frame_id": spec["task_frame_id"],
        "todo_id": spec["todo_id"],
        "room_id": spec["room_id"],
        "phase": phase,
        "updated_at": _now(),
        **dict(extra or {}),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def pid_alive(pid: int) -> bool:
    """True while a process with this pid exists (never signals it)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def validate_spec(spec: Mapping[str, Any]) -> None:
    missing = [key for key in _REQUIRED if not spec.get(key)]
    if missing:
        raise ValueError("host spec is missing: " + ", ".join(missing))
    if ":" not in str(spec["runner"]):
        raise ValueError("runner must be module:callable")


def run_host(spec: Mapping[str, Any], heartbeat_path: Path) -> int:
    validate_spec(spec)
    module_name, _, factory_name = str(spec["runner"]).partition(":")
    runner = getattr(importlib.import_module(module_name), factory_name)(spec)
    base = str(spec["base_url"])
    host = TaskFrameHost(
        HostConfig(
            task_frame_id=str(spec["task_frame_id"]),
            todo_id=str(spec["todo_id"]),
            first_role=str(spec.get("first_role") or "WORKER"),
            idle_timeout_seconds=float(spec.get("idle_timeout_seconds") or 900.0),
            poll_interval_seconds=float(spec.get("poll_interval_seconds") or 2.0),
        ),
        runner=runner,
        room=HttpRoomPort(base, str(spec["room_id"]), author_binding_id=spec.get("author_binding_id")),
        bus=HttpBusPort(base, to=spec["bus_to"], sender=spec["bus_from"],
                        thread_id=str(spec.get("thread_id") or f"task-frame-{spec['task_frame_id']}")),
        todo=HttpTodoPort(base, str(spec["todo_id"])),
        on_tick=lambda phase: _write_heartbeat(heartbeat_path, spec, phase),
    )
    outcome = host.run()
    _write_heartbeat(heartbeat_path, spec, "EXITED", {"exit_reason": outcome.exit_reason})
    return 0


def launch(spec: Mapping[str, Any], spec_path: Path, heartbeat_path: Path) -> int:
    """Start a detached Host and return its pid."""
    validate_spec(spec)
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(dict(spec), ensure_ascii=False), encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--spec", str(spec_path), "--heartbeat", str(heartbeat_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, close_fds=True,
    )
    return process.pid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Task Frame Host")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--heartbeat", required=True)
    args = parser.parse_args(argv)
    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    # The spec carries the runtime token: read it once, then remove it.
    try:
        spec_path.unlink()
    except OSError:
        pass
    return run_host(spec, Path(args.heartbeat))


if __name__ == "__main__":
    raise SystemExit(main())
