"""One-shot argv-safe launcher for a session-long Boot executor.

The launcher exits after relaying the executor's first raw JSON result. The
child executor owns the loopback endpoint and SQLite ``:memory:`` Runtime Image;
launcher exit is not executor termination evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--frame-id", default="current")
    parser.add_argument("--anchor-id", required=True)
    parser.add_argument("--host-action", required=True)
    parser.add_argument("--session-location", required=True)
    parser.add_argument("--commander-surface", required=True)
    parser.add_argument("--execution-surface", required=True)
    parser.add_argument("--repository-location", required=True)
    parser.add_argument("--port", default="0")
    parser.add_argument("--token", default="")
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    return parser


def _readline(stream: object, output: queue.Queue[str]) -> None:
    try:
        line = stream.readline()  # type: ignore[attr-defined]
    except (OSError, UnicodeError) as error:
        output.put(f"__READ_ERROR__:{error}")
        return
    output.put(line)


def _emit_failure(error_code: str, detail: str) -> int:
    print(
        json.dumps(
            {"status": "UNKNOWN", "error_code": error_code, "detail": detail},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )
    return 3


def main() -> int:
    args = _parser().parse_args()
    cli_path = Path(__file__).with_name("cli.py").resolve()
    command = [
        sys.executable,
        str(cli_path),
        "session-boot",
        "serve",
        "--repo-root",
        str(Path(args.repo_root).resolve()),
        "--session-id",
        args.session_id,
        "--frame-id",
        args.frame_id,
        "--anchor-id",
        args.anchor_id,
        "--host-action",
        args.host_action,
        "--session-location",
        args.session_location,
        "--commander-surface",
        args.commander_surface,
        "--execution-surface",
        args.execution_surface,
        "--repository-location",
        args.repository_location,
        "--port",
        args.port,
        "--token",
        args.token,
    ]

    popen_options: dict[str, object] = {
        "cwd": str(Path(args.repo_root).resolve()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        popen_options["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_options)
    except OSError as error:
        return _emit_failure("SESSION_BOOT_EXECUTOR_UNAVAILABLE", str(error))

    stdout_queue: queue.Queue[str] = queue.Queue(maxsize=1)
    stderr_queue: queue.Queue[str] = queue.Queue(maxsize=1)
    assert process.stdout is not None
    assert process.stderr is not None
    threading.Thread(
        target=_readline, args=(process.stdout, stdout_queue), daemon=True
    ).start()
    threading.Thread(
        target=_readline, args=(process.stderr, stderr_queue), daemon=True
    ).start()

    try:
        raw_result = stdout_queue.get(timeout=args.startup_timeout)
    except queue.Empty:
        process.terminate()
        detail = "Session Boot did not emit its first JSON result before timeout"
        if not stderr_queue.empty():
            detail = stderr_queue.get_nowait().strip() or detail
        return _emit_failure("SESSION_BOOT_STARTUP_TIMEOUT", detail)

    if raw_result.startswith("__READ_ERROR__:"):
        process.terminate()
        return _emit_failure("SESSION_BOOT_RESULT_UNAVAILABLE", raw_result[15:])
    if not raw_result:
        return_code = process.poll()
        detail = f"Session Boot exited before returning JSON (exit={return_code})"
        if not stderr_queue.empty():
            detail = stderr_queue.get_nowait().strip() or detail
        return _emit_failure("SESSION_BOOT_RESULT_UNAVAILABLE", detail)

    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        process.terminate()
        return _emit_failure(
            "SESSION_BOOT_RESULT_INVALID", "Session Boot did not return one JSON object"
        )
    if not isinstance(payload, dict):
        process.terminate()
        return _emit_failure(
            "SESSION_BOOT_RESULT_INVALID", "Session Boot result must be a JSON object"
        )

    # Preserve the Runtime result exactly. The Host verifies child liveness by
    # probing the returned endpoint after this one-shot launcher exits.
    print(raw_result.rstrip("\r\n"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
