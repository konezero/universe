from __future__ import annotations

import base64
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


def request(
    endpoint: str,
    token: str,
    action: str,
    supervisor_id: str | None = None,
    *,
    input_text: str | None = None,
    after_cursor: int | None = None,
):
    host, port = endpoint.removeprefix("tcp://").rsplit(":", 1)
    body = {"token": token, "action": action}
    if supervisor_id is not None:
        body["supervisor_id"] = supervisor_id
    if input_text is not None:
        body["input"] = input_text
    if after_cursor is not None:
        body["after_cursor"] = after_cursor
    with socket.create_connection((host, int(port)), timeout=5) as client:
        client.sendall(json.dumps(body).encode("utf-8") + b"\n")
        response = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
            if b"\n" in response:
                break
    return json.loads(response.split(b"\n", 1)[0])


def wait_for_output(
    endpoint: str,
    token: str,
    supervisor_id: str,
    marker: bytes,
    after_cursor: int,
) -> tuple[bytes, int]:
    deadline = time.monotonic() + 10
    observed = bytearray()
    cursor = after_cursor
    while time.monotonic() < deadline:
        response = request(
            endpoint,
            token,
            "read",
            supervisor_id,
            after_cursor=cursor,
        )
        if response.get("status") != "OK":
            raise AssertionError(f"terminal read failed: {response!r}")
        output = response["output"]
        chunk = base64.b64decode(output["data_base64"])
        observed.extend(chunk)
        cursor = output["next_cursor"]
        if marker in observed:
            return bytes(observed), cursor
        time.sleep(0.05)
    raise AssertionError(f"terminal output did not contain {marker!r}: {observed!r}")


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
                original_child_pid = first["host"]["child_pid"]
                self.assertIsInstance(original_child_pid, int)
                self.assertIn("CONPTY", first["host"]["handle_kinds"])

                terminal_ready = request(
                    state["endpoint"],
                    token,
                    "write",
                    "supervisor-a",
                    input_text="\x1b[1;1R",
                )
                self.assertEqual("OK", terminal_ready["status"])
                first_write = request(
                    state["endpoint"],
                    token,
                    "write",
                    "supervisor-a",
                    input_text="echo UNIVERSE_FIRST_MARKER\r\n",
                )
                self.assertEqual("OK", first_write["status"])
                _, output_cursor = wait_for_output(
                    state["endpoint"],
                    token,
                    "supervisor-a",
                    b"UNIVERSE_FIRST_MARKER",
                    0,
                )

                # Supervisor A's connection is gone here; the Host remains resident.
                observed = request(state["endpoint"], token, "status")
                self.assertEqual(original_host_id, observed["host"]["host_id"])
                self.assertEqual(original_pid, observed["host"]["pid"])
                self.assertEqual(original_child_pid, observed["host"]["child_pid"])

                second = request(state["endpoint"], token, "attach", "supervisor-b")
                self.assertEqual(original_host_id, second["host"]["host_id"])
                self.assertEqual(original_pid, second["host"]["pid"])
                self.assertEqual(original_child_pid, second["host"]["child_pid"])
                self.assertEqual(2, second["host"]["attachment_generation"])
                self.assertEqual(
                    "supervisor-b", second["host"]["attached_supervisor_id"]
                )
                request(
                    state["endpoint"],
                    token,
                    "write",
                    "supervisor-b",
                    input_text="echo UNIVERSE_SECOND_MARKER\r\n",
                )
                second_output, _ = wait_for_output(
                    state["endpoint"],
                    token,
                    "supervisor-b",
                    b"UNIVERSE_SECOND_MARKER",
                    output_cursor,
                )
                self.assertNotIn(b"UNIVERSE_FIRST_MARKER", second_output)
                request(
                    state["endpoint"],
                    token,
                    "write",
                    "supervisor-b",
                    input_text="exit\r\n",
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
