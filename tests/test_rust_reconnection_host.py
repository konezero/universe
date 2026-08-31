from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.reconnection_host import (  # noqa: E402
    CURRENT_RUNTIME_VERSIONS,
    ReconnectionHostRegistry,
    ReconnectionPty,
    evaluate_runtime_compatibility,
)
from universe_app.windows_process import process_is_alive  # noqa: E402
from universe_app.terminal_host import TerminalHost  # noqa: E402


MANIFEST = ROOT / "tools" / "session_host" / "Cargo.toml"
SUPERVISOR_CLIENT = ROOT / "tests" / "fixtures" / "reconnection_supervisor_client.py"
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

    def run_supervisor(
        self,
        role: str,
        registry_root: Path,
        terminal_cwd: Path,
        *,
        after_cursor: int = 0,
    ) -> dict[str, object]:
        completed = subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR_CLIENT),
                "--role",
                role,
                "--registry-root",
                str(registry_root),
                "--binary",
                str(self.binary),
                "--anchor",
                "anchor-cross-process",
                "--terminal-cwd",
                str(terminal_cwd),
                "--after-cursor",
                str(after_cursor),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            self.fail(
                f"Supervisor helper {role} failed with {completed.returncode}: "
                f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
            )
        return json.loads(completed.stdout)

    def test_anchor_state_filename_does_not_expose_anchor_text(self) -> None:
        registry = ReconnectionHostRegistry(Path("registry"), self.binary)
        state_path = registry.state_path("anchor/with/user-visible/details")
        self.assertTrue(state_path.name.startswith("anchor-"))
        self.assertNotIn("with", state_path.name)
        self.assertEqual(".json", state_path.suffix)

    def test_two_supervisor_processes_reattach_same_host_and_cmd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_root = root / "registry"
            terminal_cwd = root / "terminal-cwd"
            terminal_cwd.mkdir()
            registry = ReconnectionHostRegistry(registry_root, self.binary)
            host_pid: int | None = None
            try:
                first = self.run_supervisor("launch", registry_root, terminal_cwd)
                host_pid = int(first["host_pid"])
                self.assertTrue(process_is_alive(host_pid))
                self.assertEqual("LIVE", first["runtime_state"])
                self.assertEqual(1, first["attachment_generation"])
                self.assertEqual("supervisor-process-a", first["attached_supervisor_id"])
                self.assertIn("UNIVERSE_PROCESS_A_CONFIGURED", first["output"])
                self.assertIn(str(terminal_cwd).lower(), str(first["output"]).lower())

                reused = registry.launch(
                    "anchor-cross-process",
                    cwd=terminal_cwd,
                    environment={"UNIVERSE_HOST_TEST": "MUST_NOT_RELAUNCH"},
                ).status()
                self.assertEqual(first["host_id"], reused["host_id"])
                self.assertEqual(first["host_pid"], reused["pid"])
                self.assertEqual(first["child_pid"], reused["child_pid"])
                self.assertEqual(
                    CURRENT_RUNTIME_VERSIONS,
                    {
                        field: reused[field]
                        for field in CURRENT_RUNTIME_VERSIONS
                    },
                )
                self.assertEqual("CURRENT", evaluate_runtime_compatibility(reused))
                persisted = json.loads(
                    registry.state_path("anchor-cross-process").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    CURRENT_RUNTIME_VERSIONS,
                    {
                        field: persisted[field]
                        for field in CURRENT_RUNTIME_VERSIONS
                    },
                )

                second = self.run_supervisor(
                    "reattach",
                    registry_root,
                    terminal_cwd,
                    after_cursor=int(first["cursor"]),
                )
                for field in (
                    "host_id",
                    "host_pid",
                    "host_started_at_unix_ms",
                    "child_pid",
                ):
                    self.assertEqual(first[field], second[field], field)
                self.assertEqual(2, second["attachment_generation"])
                self.assertEqual("supervisor-process-b", second["attached_supervisor_id"])
                self.assertIn("UNIVERSE_PROCESS_B", second["output"])
                self.assertNotIn("UNIVERSE_PROCESS_A_CONFIGURED", second["output"])
            finally:
                try:
                    client = registry.discover("anchor-cross-process")
                    client.request("attach", supervisor_id="supervisor-test-cleanup")
                    client.request(
                        "write",
                        supervisor_id="supervisor-test-cleanup",
                        input_base64=base64.b64encode(b"exit\r\n").decode("ascii"),
                    )
                    deadline = time.monotonic() + 5
                    while (
                        client.status().get("runtime_state") == "LIVE"
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    client.shutdown()
                    registry.reap_launched_process("anchor-cross-process")
                except Exception:
                    pass
                if host_pid is not None:
                    deadline = time.monotonic() + 5
                    while process_is_alive(host_pid) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertFalse(process_is_alive(host_pid))

    def test_launch_replaces_exact_authenticated_host_after_child_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ReconnectionHostRegistry(root / "registry", self.binary)
            anchor_ref = "anchor-stopped-runtime-recovery"
            first = registry.launch(anchor_ref, cwd=root, shell_args=("/Q",))
            first_status = first.status()
            first_host_pid = int(first_status["pid"])
            replacement = None
            try:
                import ctypes

                child_pid = int(first_status["child_pid"])
                handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, child_pid)
                self.assertTrue(handle)
                try:
                    self.assertTrue(ctypes.windll.kernel32.TerminateProcess(handle, 0))
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
                deadline = time.monotonic() + 5
                while first.status().get("runtime_state") == "LIVE" and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertNotEqual("LIVE", first.status().get("runtime_state"))

                replacement = registry.launch(anchor_ref, cwd=root, shell_args=("/Q",))
                replacement_status = replacement.status()
                self.assertEqual("LIVE", replacement_status["runtime_state"])
                self.assertNotEqual(first_status["host_id"], replacement_status["host_id"])
                self.assertNotEqual(first_host_pid, int(replacement_status["pid"]))
            finally:
                active = replacement or first
                try:
                    active.shutdown()
                except Exception:
                    pass
                registry.reap_launched_process(anchor_ref)

    def test_shutdown_terminates_host_owned_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ReconnectionHostRegistry(root / "registry", self.binary)
            client = registry.launch(
                "anchor-shutdown-owned-shell",
                cwd=root,
                shell_args=("/Q",),
                host_kind="SESSION",
                owner_ref="anchor-shutdown-owned-shell",
            )
            status = client.status()
            self.assertEqual("SESSION", status["host_kind"])
            self.assertEqual("anchor-shutdown-owned-shell", status["owner_ref"])
            host_pid = int(status["pid"])
            child_pid = int(status["child_pid"])

            client.shutdown()
            registry.reap_launched_process("anchor-shutdown-owned-shell")
            deadline = time.monotonic() + 5
            while (
                process_is_alive(host_pid) or process_is_alive(child_pid)
            ) and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertFalse(process_is_alive(host_pid))
            self.assertFalse(process_is_alive(child_pid))

    def test_message_channel_survives_supervisor_reattach(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ReconnectionHostRegistry(root / "registry", self.binary)
            lookup_path = root / "channel.json"
            anchor_ref = "anchor-channel-reattach"
            bootstrap_token = "bootstrap-test-token"
            session_token = "session-test-token"
            client = registry.launch(
                anchor_ref,
                cwd=root,
                shell_args=("/Q",),
                host_kind="SESSION",
                owner_ref=anchor_ref,
                channel_lookup_file=lookup_path,
                channel_bootstrap_token=bootstrap_token,
                channel_session_token=session_token,
            )

            def channel_request(token: str, action: str, channel: dict[str, object]) -> dict[str, object]:
                endpoint = json.loads(lookup_path.read_text(encoding="utf-8"))["endpoint"]
                host, port_text = str(endpoint).removeprefix("tcp://").rsplit(":", 1)
                request = json.dumps(
                    {"token": token, "action": action, "channel": channel},
                    separators=(",", ":"),
                ).encode("utf-8") + b"\n"
                with socket.create_connection((host, int(port_text)), timeout=5) as connection:
                    connection.sendall(request)
                    response = bytearray()
                    while b"\n" not in response:
                        response.extend(connection.recv(8192))
                return json.loads(response.split(b"\n", 1)[0])["channel"]

            first = ReconnectionPty(client, supervisor_id="supervisor-channel-a")
            second: ReconnectionPty | None = None
            try:
                lookup = json.loads(lookup_path.read_text(encoding="utf-8"))
                self.assertEqual(bootstrap_token, lookup["bootstrap_token"])
                exchanged = channel_request(bootstrap_token, "channel_exchange", {})
                self.assertEqual("REGISTERED", exchanged["status"])
                self.assertEqual(session_token, exchanged["session_token"])
                self.assertEqual("READY", first.channel_state())

                queued = first.channel_push(
                    {
                        "message_id": "message-before-reattach",
                        "content": "continue after supervisor replacement",
                        "session_anchor_ref": anchor_ref,
                        "meta": {"provider": "CLAUDE"},
                    }
                )
                self.assertEqual("QUEUED", queued["status"])
                first.close()

                second = ReconnectionPty(
                    registry.discover(anchor_ref),
                    supervisor_id="supervisor-channel-b",
                )
                self.assertEqual("READY", second.channel_state())
                delivered = channel_request(session_token, "channel_poll", {})
                self.assertEqual("EVENT", delivered["status"])
                self.assertEqual(
                    "message-before-reattach",
                    delivered["event"]["message_id"],
                )
                accepted = channel_request(
                    session_token,
                    "channel_result",
                    {
                        "message_id": "message-before-reattach",
                        "body_text": "reattached result",
                        "outcome": "COMPLETED",
                    },
                )
                self.assertEqual("ACCEPTED", accepted["status"])
                self.assertEqual(
                    "reattached result",
                    second.channel_result("message-before-reattach")["body_text"],
                )
            finally:
                if second is not None:
                    second.close()
                client.shutdown()
                registry.reap_launched_process(anchor_ref)
            self.assertFalse(lookup_path.exists())

    def test_production_terminal_host_reconstructs_same_rust_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            terminal_cwd = root / "terminal-cwd"
            terminal_cwd.mkdir()
            audit_path = root / "universe.sqlite3"
            registry = ReconnectionHostRegistry(root / "registry", self.binary)
            anchor_ref = "anchor-production-terminal-host"
            host_pid: int | None = None
            terminal_id = ""
            second: TerminalHost | None = None
            try:
                with patch(
                    "universe_app.terminal_host.resolve_cli_executable",
                    return_value="cmd.exe",
                ), patch(
                    "universe_app.terminal_host.startup_argv",
                    return_value=["/c", "echo", "PRODUCTION_RECONNECT_MARKER"],
                ):
                    first = TerminalHost(
                        audit_database_path=audit_path,
                        reconnection_registry=registry,
                    )
                    created = first.create(
                        project_id="universe",
                        mode="MASTER",
                        cwd=str(terminal_cwd),
                        session_anchor_ref=anchor_ref,
                        provider="CODEX",
                        supervisor_session_id="provider-session",
                    )
                terminal_id = str(created["terminal_id"])
                host_pid = registry.discover(anchor_ref).state.pid
                deadline = time.monotonic() + 10
                snapshot = b""
                while time.monotonic() < deadline:
                    snapshot = base64.b64decode(
                        first.terminal_snapshot(terminal_id)["data_base64"]
                    )
                    if b"PRODUCTION_RECONNECT_MARKER" in snapshot:
                        break
                    time.sleep(0.05)
                self.assertIn(b"PRODUCTION_RECONNECT_MARKER", snapshot)
                first_session = first.get(terminal_id)
                first_session.pump_stop.set()
                if first_session.pump_thread is not None:
                    first_session.pump_thread.join(timeout=2)
                    first_session.pump_thread = None

                second = TerminalHost(
                    audit_database_path=audit_path,
                    reconnection_registry=registry,
                )
                preserved = second.reclaim_orphaned_managed_shells(
                    terminate_instance=lambda *_args: self.fail(
                        "confirmed Host-owned cmd must survive Supervisor replacement"
                    )
                )
                self.assertEqual(
                    "HOST_OWNED_ORPHAN_PRESERVED", preserved[0]["status"]
                )
                reconciled = second.reconcile_reconnection_hosts()
                self.assertEqual("TERMINAL_REATTACHED", reconciled[0]["status"])
                recovered = second.get(terminal_id).public()
                self.assertEqual(created["pid"], recovered["pid"])
                self.assertEqual(created["reconnection_host_id"], recovered["reconnection_host_id"])
                self.assertEqual("RUST_RECONNECTION_HOST", recovered["backend_owner"])
                self.assertTrue(process_is_alive(host_pid))
            finally:
                if second is not None and terminal_id:
                    try:
                        second.close(terminal_id)
                    except Exception:
                        pass
                try:
                    client = registry.discover(anchor_ref)
                    client.request("attach", supervisor_id="production-test-cleanup")
                    try:
                        client.request(
                            "write",
                            supervisor_id="production-test-cleanup",
                            input_base64=base64.b64encode(b"\x1b[1;1R").decode("ascii"),
                        )
                        client.request(
                            "write",
                            supervisor_id="production-test-cleanup",
                            input_base64=base64.b64encode(b"exit\r\n").decode("ascii"),
                        )
                        deadline = time.monotonic() + 5
                        while (
                            client.status().get("runtime_state") == "LIVE"
                            and time.monotonic() < deadline
                        ):
                            time.sleep(0.05)
                    finally:
                        client.shutdown()
                        registry.reap_launched_process(anchor_ref)
                except Exception:
                    pass
                if host_pid is not None:
                    deadline = time.monotonic() + 5
                    while process_is_alive(host_pid) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertFalse(process_is_alive(host_pid))


if __name__ == "__main__":
    unittest.main()
