from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tools" / "session_host" / "Cargo.toml"
UNIVERSE_CARGO = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Universe"
    / "RustToolchain"
    / "cargo"
    / "bin"
    / "cargo.exe"
)


def cargo_executable() -> Path | None:
    explicit = os.environ.get("UNIVERSE_CARGO")
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    resolved = shutil.which("cargo")
    if resolved:
        return Path(resolved)
    return UNIVERSE_CARGO if UNIVERSE_CARGO.is_file() else None


def request(endpoint: str, token: str, action: str, supervisor_id: str | None = None):
    host, port = endpoint.removeprefix("tcp://").rsplit(":", 1)
    body = {"token": token, "action": action}
    if supervisor_id is not None:
        body["supervisor_id"] = supervisor_id
    with socket.create_connection((host, int(port)), timeout=5) as client:
        client.sendall(json.dumps(body).encode("utf-8"))
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    return json.loads(response)


class RustReconnectionHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cargo = cargo_executable()
        if cargo is None:
            raise unittest.SkipTest("Universe Rust toolchain is unavailable")
        environment = dict(os.environ)
        environment["RUSTUP_HOME"] = str(
            Path(environment["LOCALAPPDATA"])
            / "Universe"
            / "RustToolchain"
            / "rustup"
        )
        environment["CARGO_HOME"] = str(
            Path(environment["LOCALAPPDATA"])
            / "Universe"
            / "RustToolchain"
            / "cargo"
        )
        subprocess.run(
            [str(cargo), "build", "--manifest-path", str(MANIFEST)],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        cls.binary = MANIFEST.parent / "target" / "debug" / "universe-session-host.exe"

    def test_replacement_supervisor_attaches_to_same_independent_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "host-state.json"
            token = "test-token"
            process = subprocess.Popen(
                [
                    str(self.binary),
                    "serve",
                    "--state-file",
                    str(state_file),
                    "--anchor-ref",
                    "anchor-test",
                    "--token",
                    token,
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while not state_file.is_file() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if not state_file.is_file():
                    process.terminate()
                    process.wait(timeout=5)
                    stderr = process.stderr.read() if process.stderr else ""
                    self.fail(f"Host did not publish state: {stderr}")
                state = json.loads(state_file.read_text(encoding="utf-8"))
                first = request(state["endpoint"], token, "attach", "supervisor-a")
                self.assertEqual("OK", first["status"])
                original_host_id = first["host"]["host_id"]
                original_pid = first["host"]["pid"]

                # Supervisor A's connection is gone here; the Host remains resident.
                observed = request(state["endpoint"], token, "status")
                self.assertEqual(original_host_id, observed["host"]["host_id"])
                self.assertEqual(original_pid, observed["host"]["pid"])

                second = request(state["endpoint"], token, "attach", "supervisor-b")
                self.assertEqual(original_host_id, second["host"]["host_id"])
                self.assertEqual(original_pid, second["host"]["pid"])
                self.assertEqual(2, second["host"]["attachment_generation"])
                self.assertEqual(
                    "supervisor-b", second["host"]["attached_supervisor_id"]
                )
                request(state["endpoint"], token, "shutdown")
                process.wait(timeout=5)
                self.assertEqual(0, process.returncode)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                if process.stderr is not None:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
