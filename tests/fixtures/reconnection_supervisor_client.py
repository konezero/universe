from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.reconnection_host import (  # noqa: E402
    ReconnectionHostRegistry,
    ReconnectionPty,
)


def read_until(pty: ReconnectionPty, marker: bytes) -> bytes:
    deadline = time.monotonic() + 10
    observed = bytearray()
    while time.monotonic() < deadline:
        observed.extend(pty.read(timeout=0.1))
        if marker in observed:
            return bytes(observed)
    raise RuntimeError(f"terminal output did not contain {marker!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("launch", "reattach"), required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--anchor", required=True)
    parser.add_argument("--terminal-cwd", type=Path, required=True)
    parser.add_argument("--after-cursor", type=int, default=0)
    args = parser.parse_args()

    registry = ReconnectionHostRegistry(args.registry_root, args.binary)
    if args.role == "launch":
        client = registry.launch(
            args.anchor,
            cwd=args.terminal_cwd,
            shell_args=("/Q",),
            environment={"UNIVERSE_HOST_TEST": "CONFIGURED"},
            cols=101,
            rows=37,
        )
        supervisor_id = "supervisor-process-a"
        marker = b"UNIVERSE_PROCESS_A_CONFIGURED"
        pty = ReconnectionPty(client, supervisor_id)
        pty.write(b"\x1b[1;1R")
        pty.write(b"cd & echo UNIVERSE_PROCESS_A_%UNIVERSE_HOST_TEST%\r\n")
    else:
        client = registry.discover(args.anchor)
        supervisor_id = "supervisor-process-b"
        marker = b"UNIVERSE_PROCESS_B"
        pty = ReconnectionPty(
            client,
            supervisor_id,
            after_cursor=args.after_cursor,
        )
        pty.write(b"echo UNIVERSE_PROCESS_B\r\n")

    output = read_until(pty, marker)
    if args.role == "reattach":
        # Resize can legitimately cause ConPTY to redraw old screen contents.
        # Validate cursor continuity before that redraw, then exercise resize.
        pty.resize(132, 44)
    status = client.status()
    print(
        json.dumps(
            {
                "host_id": status["host_id"],
                "host_pid": status["pid"],
                "host_started_at_unix_ms": status["started_at_unix_ms"],
                "child_pid": status["child_pid"],
                "attachment_generation": status["attachment_generation"],
                "attached_supervisor_id": status["attached_supervisor_id"],
                "runtime_state": status["runtime_state"],
                "cursor": pty.output_cursor,
                "output": output.decode("utf-8", errors="replace"),
            }
        )
    )
    # Deliberately do not detach.  Process A/B disappearance must not own the
    # Host or cmd lifetime; a later Supervisor replaces the attachment.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
