from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_ANCHOR = "session_anchor_test"
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.terminal_host import (  # noqa: E402
    _ANSI_ESCAPE_RE,
    _DEV_CHANNEL_PROMPT_RE,
    MANAGED_SHELL_IDENTITY_MISSING,
    ProcessIdentity,
    TerminalHost,
    TerminalHostError,
    resolve_cli_executable,
    resume_argv,
    startup_argv,
    startup_input,
)


class FakePty:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self.size = (120, 32)
        self.pid = 4242

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def read(self, timeout: float = 0.2) -> bytes:
        del timeout
        return b""

    def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)

    def close(self) -> None:
        self.closed = True


class FakeReconnectionClient:
    def __init__(self, anchor_ref: str) -> None:
        self.state = SimpleNamespace(
            anchor_ref=anchor_ref,
            host_kind="SESSION",
            owner_ref=anchor_ref,
            host_id="host-test",
            pid=5252,
            started_at_unix_ms=1000,
            child_pid=4242,
        )
        self.attached_supervisor_id: str | None = None
        self.generation = 0
        self.executions: list[bytes] = []
        self.writes: list[bytes] = []
        self.shutdown_called = False

    def _host(self) -> dict[str, object]:
        return {
            "host_id": self.state.host_id,
            "anchor_ref": self.state.anchor_ref,
            "host_kind": self.state.host_kind,
            "owner_ref": self.state.owner_ref,
            "pid": self.state.pid,
            "started_at_unix_ms": self.state.started_at_unix_ms,
            "child_pid": self.state.child_pid,
            "attachment_generation": self.generation,
            "attached_supervisor_id": self.attached_supervisor_id,
            "runtime_state": "LIVE",
        }

    def request(self, action: str, **fields):
        if action == "attach":
            self.attached_supervisor_id = fields["supervisor_id"]
            self.generation += 1
            return {"host": self._host()}
        if action == "detach":
            self.attached_supervisor_id = None
            return {"host": self._host()}
        if action == "execute":
            self.executions.append(base64.b64decode(fields["input_base64"]))
            return {"host": self._host()}
        if action == "write":
            self.writes.append(base64.b64decode(fields["input_base64"]))
            return {"host": self._host()}
        if action == "read":
            return {
                "host": self._host(),
                "output": {
                    "data_base64": "",
                    "start_cursor": fields.get("after_cursor", 0),
                    "next_cursor": fields.get("after_cursor", 0),
                    "truncated": False,
                },
            }
        return {"host": self._host()}

    def status(self):
        return self._host()

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeReconnectionRegistry:
    def __init__(self) -> None:
        self.clients: dict[str, FakeReconnectionClient] = {}
        self.launches: list[dict[str, object]] = []

    def launch(self, anchor_ref: str, **config):
        self.launches.append({"anchor_ref": anchor_ref, **config})
        return self.clients.setdefault(anchor_ref, FakeReconnectionClient(anchor_ref))

    def discover(self, anchor_ref: str):
        return self.clients[anchor_ref]

    def list_live_clients(self):
        return list(self.clients.values())


class TerminalHostTests(unittest.TestCase):
    def test_claude_development_channel_prompt_matches_real_screen_forms(self) -> None:
        ordinary = b"Yes, I am using this for local development"
        cursor_positioned = (
            b"Yes, I am using this for local"
            b"\x1b[12;42Hdevelopment"
        )

        for screen in (ordinary, cursor_positioned):
            with self.subTest(screen=screen):
                plain = _ANSI_ESCAPE_RE.sub(b"", screen)
                self.assertIsNotNone(_DEV_CHANNEL_PROMPT_RE.search(plain))

        self.assertIsNone(
            _DEV_CHANNEL_PROMPT_RE.search(b"local production environment")
        )

    def test_rust_host_creation_and_audit_reconstruction_share_one_terminal(self) -> None:
        registry = FakeReconnectionRegistry()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_cli_executable", return_value="cmd.exe"
        ), patch(
            "universe_app.terminal_host.startup_argv",
            return_value=["/c", "echo", "RUST_HOST_MARKER"],
        ), patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            root = Path(tmp)
            audit_path = root / "audit.sqlite3"
            first = TerminalHost(
                audit_database_path=audit_path,
                reconnection_registry=registry,
            )
            created = first.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref="anchor-rust-host",
                provider="CODEX",
                supervisor_session_id="provider-session",
            )
            first_session = first.get(created["terminal_id"])
            first_session.pump_stop.set()
            if first_session.pump_thread is not None:
                first_session.pump_thread.join(timeout=1)
                first_session.pump_thread = None
            client = registry.clients["anchor-rust-host"]
            self.assertEqual("RUST_RECONNECTION_HOST", created["backend_owner"])
            self.assertEqual("host-test", created["reconnection_host_id"])
            self.assertEqual([], client.writes)
            self.assertEqual(
                [b"\x1b[1;1Rcmd.exe /c echo RUST_HOST_MARKER\r\n"],
                client.executions,
            )
            launch = registry.launches[0]
            self.assertEqual(("/d", "/q", "/k"), launch["shell_args"])
            self.assertEqual("SESSION", launch["host_kind"])
            self.assertEqual("anchor-rust-host", launch["owner_ref"])
            self.assertNotIn("startup_input", launch)
            self.assertEqual("xterm-256color", launch["environment"]["TERM"])

            second = TerminalHost(
                audit_database_path=audit_path,
                reconnection_registry=registry,
            )
            preserved = second.reclaim_orphaned_managed_shells(
                start_time_of=lambda _pid: 123.5,
                terminate_instance=lambda *_args: self.fail(
                    "a confirmed Host-owned cmd must not be terminated"
                ),
            )
            self.assertEqual("HOST_OWNED_ORPHAN_PRESERVED", preserved[0]["status"])
            reconciled = second.reconcile_reconnection_hosts()
            self.assertEqual("TERMINAL_REATTACHED", reconciled[0]["status"])
            recovered = second.get(created["terminal_id"])
            self.assertEqual(created["terminal_id"], recovered.terminal_id)
            self.assertEqual(created["pid"], recovered.live_pid())
            self.assertEqual("host-test", recovered.reconnection_host_id)
            self.assertEqual(2, client.generation)
            detached = second.close(created["terminal_id"])
            self.assertEqual("TERMINAL_DETACHED", detached["status"])
            self.assertFalse(client.shutdown_called)

            third = TerminalHost(
                audit_database_path=audit_path,
                reconnection_registry=registry,
            )
            self.assertEqual(
                "TERMINAL_REATTACHED",
                third.reconcile_reconnection_hosts()[0]["status"],
            )
            terminated = third.terminate(created["terminal_id"])
            self.assertEqual("TERMINAL_TERMINATED", terminated["status"])
            self.assertTrue(client.shutdown_called)

    def test_rust_host_defers_claude_json_schema_through_environment(self) -> None:
        registry = FakeReconnectionRegistry()
        schema = '{"type":"object","description":"A&B %PATH% !literal!"}'
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_cli_executable",
            return_value="claude.exe",
        ), patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(reconnection_registry=registry)
            created = host.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref="anchor-rust-host-schema",
                provider="CLAUDE",
                supervisor_session_id="worker-session",
                launch_profile="SUPERVISED_STDIO",
                provider_arguments=["-p", "--json-schema", schema],
            )
            try:
                launch = registry.launches[0]
                self.assertEqual(("/d", "/q", "/v:on", "/k"), launch["shell_args"])
                name = "UNIVERSE_PROVIDER_ARGUMENT_0003"
                self.assertEqual(
                    subprocess.list2cmdline([schema]),
                    launch["environment"][name],
                )
                self.assertEqual(
                    [
                        (
                            "\x1b[1;1Rmore | claude.exe -p --json-schema "
                            f"!{name}!\r\n"
                        ).encode("utf-8")
                    ],
                    registry.clients["anchor-rust-host-schema"].executions,
                )
            finally:
                host.terminate(created["terminal_id"])

    def test_session_host_registers_identity_before_cli_execute(self) -> None:
        registry = FakeReconnectionRegistry()
        observations: list[tuple[int, bool]] = []
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_cli_executable", return_value="cmd.exe"
        ), patch(
            "universe_app.terminal_host.startup_argv", return_value=["/c", "echo", "ORDERED"]
        ), patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(
                audit_database_path=Path(tmp) / "audit.sqlite3",
                reconnection_registry=registry,
            )
            original_launch = registry.launch

            def launch(anchor_ref: str, **config):
                client = original_launch(anchor_ref, **config)
                original_request = client.request

                def request(action: str, **fields):
                    if action == "execute":
                        sessions = list(host._sessions.values())
                        observations.append(
                            (
                                len(sessions),
                                bool(sessions)
                                and Path(sessions[0].managed_shell_identity_file).is_file(),
                            )
                        )
                    return original_request(action, **fields)

                client.request = request
                return client

            registry.launch = launch
            created = host.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref="anchor-ordered-host",
                provider="CODEX",
                supervisor_session_id="provider-session",
            )
            self.assertEqual([(1, True)], observations)
            host.terminate(created["terminal_id"])

    def test_create_serializes_host_attach_against_background_reconcile(self) -> None:
        registry = FakeReconnectionRegistry()
        reconcile_started = threading.Event()
        reconcile_finished = threading.Event()
        reconcile_thread: threading.Thread | None = None
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_cli_executable", return_value="cmd.exe"
        ), patch(
            "universe_app.terminal_host.startup_argv", return_value=["/c", "echo", "ATOMIC"]
        ), patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(
                audit_database_path=Path(tmp) / "audit.sqlite3",
                reconnection_registry=registry,
            )
            host.record_audit_event(
                "TERMINAL_CREATED",
                terminal={
                    "terminal_id": "term_previous",
                    "project_id": "universe",
                    "mode": "MASTER",
                    "provider": "CODEX",
                    "supervisor_session_id": "provider-session-old",
                },
                details={
                    "backend_owner": "RUST_RECONNECTION_HOST",
                    "reconnection_host_id": "host-test",
                    "session_anchor_ref": "anchor-atomic-host",
                    "cwd": tmp,
                    "executable": "cmd.exe",
                    "created_at": "2026-08-29T00:00:00Z",
                },
            )
            original_launch = registry.launch

            def launch(anchor_ref: str, **config):
                client = original_launch(anchor_ref, **config)
                original_request = client.request

                def request(action: str, **fields):
                    nonlocal reconcile_thread
                    if action == "execute":
                        def reconcile() -> None:
                            reconcile_started.set()
                            host.reconcile_reconnection_hosts()
                            reconcile_finished.set()

                        reconcile_thread = threading.Thread(target=reconcile)
                        reconcile_thread.start()
                        self.assertTrue(reconcile_started.wait(1))
                        self.assertFalse(reconcile_finished.wait(0.05))
                    return original_request(action, **fields)

                client.request = request
                return client

            registry.launch = launch
            created = host.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref="anchor-atomic-host",
                provider="CODEX",
                supervisor_session_id="provider-session-new",
            )
            self.assertIsNotNone(reconcile_thread)
            reconcile_thread.join(timeout=1)
            self.assertTrue(reconcile_finished.is_set())
            self.assertEqual(1, registry.clients["anchor-atomic-host"].generation)
            host.terminate(created["terminal_id"])

    def test_reconcile_uses_complete_creation_history_for_live_registry_hosts(self) -> None:
        registry = FakeReconnectionRegistry()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_cli_executable", return_value="cmd.exe"
        ), patch(
            "universe_app.terminal_host.startup_argv", return_value=["/c", "echo", "OLD"]
        ), patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            audit_path = Path(tmp) / "audit.sqlite3"
            first = TerminalHost(
                audit_database_path=audit_path,
                reconnection_registry=registry,
            )
            created = first.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref="anchor-old-live-host",
                provider="CODEX",
                supervisor_session_id="provider-session-old",
            )
            first_session = first.get(created["terminal_id"])
            first_session.pump_stop.set()
            if first_session.pump_thread is not None:
                first_session.pump_thread.join(timeout=1)
                first_session.pump_thread = None
            for index in range(1005):
                first.record_audit_event(
                    "NOISE",
                    terminal={"terminal_id": f"noise-{index}"},
                    context={"source": "TEST", "access_surface": "TEST"},
                )
            first.record_audit_event(
                "TERMINAL_CLOSED",
                terminal=created,
                context={"source": "TEST", "access_surface": "TEST"},
            )

            second = TerminalHost(
                audit_database_path=audit_path,
                reconnection_registry=registry,
            )
            reconciled = second.reconcile_reconnection_hosts()
            self.assertEqual("TERMINAL_REATTACHED", reconciled[0]["status"])
            self.assertEqual(created["terminal_id"], reconciled[0]["terminal_id"])
            second.close(created["terminal_id"])

    def test_create_list_and_close_without_vendor_jsonl(self) -> None:
        spawned: list[tuple] = []

        def spawn(
            executable: str,
            cwd: str,
            cols: int,
            rows: int,
            argv=None,
            environment=None,
        ) -> FakePty:
            spawned.append(
                (executable, cwd, cols, rows, (argv if isinstance(argv, str) else list(argv or [])), dict(environment or {}))
            )
            return FakePty()

        host = TerminalHost(spawn=spawn)
        created = host.create(
            project_id="GCS",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR,
            provider="GROK",
        )
        self.assertEqual("GCS", created["project_id"])
        self.assertEqual("MASTER", created["mode"])
        self.assertEqual("LIVE", created["state"])
        self.assertEqual(4242, created["pid"])
        self.assertTrue(created["terminal_id"].startswith("term_"))
        listed = host.list_sessions()
        self.assertEqual(1, len(listed))
        self.assertEqual(created["terminal_id"], listed[0]["terminal_id"])
        host.write(created["terminal_id"], b"hi")
        closed = host.close(created["terminal_id"])
        self.assertEqual("TERMINAL_DETACHED", closed["status"])
        self.assertEqual([], host.list_sessions())
        with self.assertRaises(TerminalHostError):
            host.get(created["terminal_id"])
        self.assertEqual(1, len(spawned))
        self.assertEqual(
            {
                "UNIVERSE_PROJECT_ID": "GCS",
                "UNIVERSE_MODE": "MASTER",
                "UNIVERSE_PROVIDER": "GROK",
                # The managed shell tells the SessionStart hook it is running
                # inside a Supervisor-owned cmd bound to this exact Anchor.
                "UNIVERSE_MANAGED_SHELL": "1",
                "UNIVERSE_SESSION_ANCHOR_REF": TEST_ANCHOR,
                "GROK_CLAUDE_HOOKS_ENABLED": "0",
                "UNIVERSE_MODEL_REF": "",
                "UNIVERSE_EFFORT": "AUTO",
                "UNIVERSE_SUPERVISOR_SESSION_ID": "",
                "UNIVERSE_TERMINAL_ID": created["terminal_id"],
                "UNIVERSE_MANAGED_SHELL_IDENTITY_FILE": str(
                    ROOT
                    / ".ai"
                    / "runtime"
                    / "tmp"
                    / "managed-shells"
                    / f"{created['terminal_id']}.json"
                ),
                "UNIVERSE_SESSION_INBOX_CLI": str(
                    ROOT / "tools" / "universe_session_inbox.py"
                ),
            },
            spawned[0][5],
        )

    def test_managed_shell_identity_file_is_written_and_cleaned(self) -> None:
        spawned_environment: dict[str, str] = {}

        def spawn(_executable, _cwd, _cols, _rows, argv=None, environment=None):
            del argv
            spawned_environment.update(environment or {})
            return FakePty()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(spawn=spawn)
            created = host.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref=TEST_ANCHOR,
                provider="GROK",
            )
            identity_path = Path(
                spawned_environment["UNIVERSE_MANAGED_SHELL_IDENTITY_FILE"]
            )
            self.assertTrue(identity_path.is_file())
            self.assertIn("\"shell_pid\": 4242", identity_path.read_text())
            host.close(created["terminal_id"])
            self.assertFalse(identity_path.exists())

    def test_supervised_stdio_uses_one_managed_cmd_and_provider_protocol(self) -> None:
        captured: dict[str, object] = {}

        def spawn(executable, cwd, cols, rows, argv, environment):
            captured.update(
                executable=executable,
                cwd=cwd,
                cols=cols,
                rows=rows,
                argv=argv,
                environment=dict(environment),
            )
            return FakePty()

        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_cli_executable",
            return_value="claude.exe",
        ), patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(spawn=spawn)
            created = host.create(
                project_id="universe",
                mode="CONDUCTOR",
                cwd=tmp,
                session_anchor_ref=TEST_ANCHOR,
                provider="CLAUDE",
                supervisor_session_id="session-owned",
                launch_profile="SUPERVISED_STDIO",
                provider_arguments=[
                    "-p",
                    "--input-format",
                    "stream-json",
                    "--output-format",
                    "stream-json",
                ],
                provider_environment={
                    "PROVIDER_TEST": "present",
                    "UNIVERSE_MODE": "MUST_NOT_OVERRIDE",
                },
            )
            try:
                self.assertEqual("SUPERVISED_STDIO", created["launch_profile"])
                self.assertEqual("cmd.exe", Path(str(captured["executable"])).name)
                self.assertIn("more | claude.exe -p", str(captured["argv"]))
                environment = captured["environment"]
                self.assertEqual("present", environment["PROVIDER_TEST"])
                self.assertEqual("CONDUCTOR", environment["UNIVERSE_MODE"])
                self.assertEqual(
                    {"pid": 4242, "started_at": 123.5},
                    created["shell_process"],
                )
                self.assertTrue(Path(created["managed_shell_identity_file"]).is_file())
            finally:
                host.close(created["terminal_id"])

    def test_supervised_stdio_subscription_replays_raw_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_cli_executable",
            return_value="claude.exe",
        ), patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(spawn=lambda *_a, **_k: FakePty())
            created = host.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref=TEST_ANCHOR,
                provider="CLAUDE",
                supervisor_session_id="session-owned",
                launch_profile="SUPERVISED_STDIO",
                provider_arguments=["-p", "--output-format", "stream-json"],
            )
            waiter = None
            try:
                session = host.get(created["terminal_id"])
                raw = b'{"type":"result","subtype":"success"}\r\n'
                with session.lock:
                    host._record_output(session, raw)
                waiter = host.subscribe(created["terminal_id"])
                self.assertEqual(raw, waiter.get_nowait())
            finally:
                if waiter is not None:
                    host.unsubscribe(created["terminal_id"], waiter)
                host.close(created["terminal_id"])

    def test_managed_shell_polling_starts_without_ui_subscription(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(spawn=lambda *_a, **_k: FakePty())
            created = host.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref=TEST_ANCHOR,
                provider="GROK",
                supervisor_session_id="session-owned",
            )
            session = host.get(created["terminal_id"])
            self.assertIsNotNone(session.pump_thread)
            self.assertTrue(session.pump_thread.is_alive())
            host.close(created["terminal_id"])

    def test_missing_identity_file_is_polled_and_reclaimed(self) -> None:
        pty = FakePty()
        with tempfile.TemporaryDirectory() as tmp, patch(
            "universe_app.terminal_host.resolve_shell_identity",
            return_value=ProcessIdentity(pid=4242, started_at=123.5),
        ):
            host = TerminalHost(spawn=lambda *_a, **_k: pty)
            created = host.create(
                project_id="universe",
                mode="MASTER",
                cwd=tmp,
                session_anchor_ref=TEST_ANCHOR,
                provider="GROK",
                supervisor_session_id="session-owned",
            )
            session = host.get(created["terminal_id"])
            identity_path = Path(session.managed_shell_identity_file)
            identity_path.unlink()
            result = host.poll_managed_shell(
                created["terminal_id"],
                probes={
                    "is_alive": lambda _pid: True,
                    "children_of": lambda _pid: [],
                    "start_time_of": lambda _pid: 123.5,
                    "source": "TEST",
                },
            )
            self.assertEqual(MANAGED_SHELL_IDENTITY_MISSING, result["state"])
            self.assertTrue(result["reclaimed"])
            self.assertTrue(pty.closed)
            with self.assertRaises(TerminalHostError):
                host.get(created["terminal_id"])

    def test_supervisor_reclaims_exact_orphan_from_identity_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = TerminalHost(audit_database_path=root / "audit.sqlite3")
            identity_path = root / "managed-shell.json"
            identity_path.write_text(
                json.dumps(
                    {
                        "schema": "universe.managed-shell-identity.v1",
                        "terminal_id": "term-orphan",
                        "session_anchor_ref": TEST_ANCHOR,
                        "shell_pid": 5151,
                        "shell_started_at": 123.5,
                    }
                ),
                encoding="utf-8",
            )
            host.record_audit_event(
                "TERMINAL_CREATED",
                terminal={
                    "terminal_id": "term-orphan",
                    "pid": 5151,
                    "project_id": "universe",
                    "mode": "MASTER",
                    "provider": "CLAUDE",
                    "supervisor_session_id": "session-owned",
                },
                details={"managed_shell_identity_file": str(identity_path)},
            )
            terminated: list[tuple[int, float]] = []
            result = host.reclaim_orphaned_managed_shells(
                start_time_of=lambda pid: 123.5 if pid == 5151 else None,
                terminate_instance=lambda pid, started: (
                    terminated.append((pid, started)) or True
                ),
            )
            self.assertEqual([(5151, 123.5)], terminated)
            self.assertEqual("TERMINAL_ORPHAN_RECLAIMED", result[0]["status"])
            self.assertFalse(identity_path.exists())
            self.assertEqual(
                "TERMINAL_ORPHAN_RECLAIMED",
                host.audit_events(terminal_id="term-orphan")[0]["event_type"],
            )

    def test_rust_host_owned_shell_is_never_reclaimed_as_legacy_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = TerminalHost(audit_database_path=root / "audit.sqlite3")
            identity_path = root / "managed-shell.json"
            identity_path.write_text(
                json.dumps(
                    {
                        "schema": "universe.managed-shell-identity.v1",
                        "terminal_id": "term-host-owned",
                        "session_anchor_ref": TEST_ANCHOR,
                        "shell_pid": 6262,
                        "shell_started_at": 456.5,
                    }
                ),
                encoding="utf-8",
            )
            host.record_audit_event(
                "TERMINAL_CREATED",
                terminal={
                    "terminal_id": "term-host-owned",
                    "pid": 6262,
                    "project_id": "universe",
                    "mode": "MASTER",
                    "provider": "CODEX",
                    "supervisor_session_id": "session-host-owned",
                },
                details={
                    "managed_shell_identity_file": str(identity_path),
                    "backend_owner": "RUST_RECONNECTION_HOST",
                    "reconnection_host_id": "host-preserved",
                    "session_anchor_ref": TEST_ANCHOR,
                },
            )
            result = host.reclaim_orphaned_managed_shells(
                start_time_of=lambda _pid: 456.5,
                terminate_instance=lambda *_args: self.fail(
                    "legacy reclaim must never terminate a Rust Host-owned shell"
                ),
            )
            self.assertEqual("HOST_OWNED_ORPHAN_DEFERRED", result[0]["status"])
            self.assertEqual("host-preserved", result[0]["host_id"])
            self.assertTrue(identity_path.exists())

    def test_pid_reuse_never_terminates_an_unrelated_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = TerminalHost(audit_database_path=root / "audit.sqlite3")
            identity_path = root / "managed-shell.json"
            identity_path.write_text(
                json.dumps(
                    {
                        "schema": "universe.managed-shell-identity.v1",
                        "terminal_id": "term-stale",
                        "session_anchor_ref": TEST_ANCHOR,
                        "shell_pid": 5151,
                        "shell_started_at": 123.5,
                    }
                ),
                encoding="utf-8",
            )
            host.record_audit_event(
                "TERMINAL_CREATED",
                terminal={"terminal_id": "term-stale", "pid": 5151},
                details={"managed_shell_identity_file": str(identity_path)},
            )
            terminated: list[int] = []
            result = host.reclaim_orphaned_managed_shells(
                start_time_of=lambda _pid: 999.0,
                terminate_instance=lambda pid, _started: (
                    terminated.append(pid) or True
                ),
            )
            self.assertEqual([], terminated)
            self.assertEqual("STALE_IDENTITY_REMOVED", result[0]["status"])
            self.assertFalse(identity_path.exists())

    def test_missing_coordinate_is_rejected(self) -> None:
        host = TerminalHost(spawn=lambda *_args: FakePty())
        with self.assertRaises(TerminalHostError):
            host.create(project_id="", mode="MASTER", cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR)

    def test_resize_keeps_a_usable_cli_box(self) -> None:
        pty = FakePty()
        host = TerminalHost(spawn=lambda *_args: pty)
        created = host.create(project_id="GCS", mode="MASTER", cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR, provider="GROK")
        host.resize(created["terminal_id"], 20, 8)
        session = host.get(created["terminal_id"])
        self.assertGreaterEqual(session.cols, 80)
        self.assertGreaterEqual(session.rows, 24)
        self.assertEqual((session.cols, session.rows), pty.size)

    def test_resolve_cli_executable_never_falls_back_to_another_provider(self) -> None:
        with patch("universe_app.terminal_host.resolve_host_tool", return_value=None) as resolver:
            with self.assertRaises(TerminalHostError) as missing:
                resolve_cli_executable("CODEX")
        self.assertEqual("CLI_EXECUTABLE_UNAVAILABLE", missing.exception.code)
        resolver.assert_called_once_with("codex")

        with self.assertRaises(TerminalHostError) as unselected:
            resolve_cli_executable("AUTO")
        self.assertEqual("TERMINAL_PROVIDER_REQUIRED", unselected.exception.code)

    def test_resume_argv_uses_provider_cli_flags(self) -> None:
        self.assertEqual(["--resume", "abc"], resume_argv("GROK", "abc"))
        self.assertEqual(["--resume", "abc"], resume_argv("CLAUDE", "abc"))
        self.assertEqual(["resume", "abc"], resume_argv("CODEX", "abc"))
        self.assertEqual([], resume_argv("GROK", "UNKNOWN"))
        self.assertEqual([], resume_argv("GROK", ""))

    def test_startup_argv_keeps_model_and_effort_with_provider_resume(self) -> None:
        self.assertEqual(
            ["--model", "grok-4.6", "--reasoning-effort", "max", "--resume", "abc"],
            startup_argv("GROK", "abc", model_ref="grok-4.6", effort="MAX"),
        )
        self.assertEqual(
            ["--model", "opus", "--effort", "medium", "--resume", "abc", "--dangerously-skip-permissions"],
            startup_argv("CLAUDE", "abc", model_ref="opus", effort="MEDIUM"),
        )
        self.assertEqual(
            [
                "--dangerously-skip-permissions",
                "--dangerously-load-development-channels",
                "server:universe_channel",
            ],
            startup_argv(
                "CLAUDE",
                "",
                claude_channel_enabled=True,
            ),
        )
        self.assertEqual(
            [
                "--model",
                "gpt-5.6",
                "--config",
                "model_reasoning_effort=high",
                "resume",
                "abc",
            ],
            startup_argv("CODEX", "abc", model_ref="gpt-5.6", effort="HIGH"),
        )

    def test_fresh_codex_and_grok_sessions_receive_bounded_bootstrap_input(self) -> None:
        expected = (
            b"Initialize this Universe session without calling tools. "
            b"Reply exactly SESSION_READY and wait for user instructions.\r"
        )
        for provider in ("CODEX", "GROK"):
            self.assertEqual(expected, startup_input(provider, ""))
        self.assertEqual(b"", startup_input("CODEX", "abc"))
        self.assertEqual(b"", startup_input("GROK", "abc"))
        self.assertEqual(b"", startup_input("CLAUDE", ""))

    def test_fresh_claude_terminal_uses_a_new_session_id_without_resume(self) -> None:
        spawned: list[tuple] = []

        def spawn(executable, cwd, cols, rows, argv=None, environment=None):
            spawned.append((executable, cwd, cols, rows, (argv if isinstance(argv, str) else list(argv or [])), dict(environment or {})))
            return FakePty()

        host = TerminalHost(spawn=spawn)
        with patch(
            "universe_app.terminal_host.uuid.uuid4",
            return_value=uuid.UUID("12345678-1234-4678-9234-567812345678"),
        ), patch("universe_app.terminal_host.ensure_local_channel_server_registered"):
            created = host.create(
                project_id="universe", mode="MASTER", cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR, provider="CLAUDE"
            )
        cmdline = spawned[0][4]
        self.assertIsInstance(
            cmdline, str, "the managed shell needs a raw command line, not argv"
        )
        self.assertTrue(cmdline.startswith("/d /q /s /k "), cmdline)
        self.assertIn("--session-id 12345678-1234-4678-9234-567812345678", cmdline)
        self.assertNotIn("--resume", cmdline)
        self.assertTrue(
            cmdline.rstrip('"').endswith(
                "--dangerously-load-development-channels server:universe_channel"
            ),
            cmdline,
        )
        host.close(created["terminal_id"])

    def test_resume_rejects_universe_internal_session_ids(self) -> None:
        for internal_ref in (
            "session_123b6a5dac26bd0a91575526",
            "session-2",
        ):
            with self.subTest(internal_ref=internal_ref):
                with self.assertRaises(TerminalHostError) as invalid:
                    resume_argv("CODEX", internal_ref)
                self.assertEqual("TERMINAL_RESUME_REF_INVALID", invalid.exception.code)
        self.assertEqual(
            ["resume", "vendor-thread"],
            resume_argv("CODEX", "codex-app-server:vendor-thread"),
        )

    def test_exited_cli_is_failed_and_retains_startup_diagnostic(self) -> None:
        class ExitedPty(FakePty):
            def __init__(self) -> None:
                super().__init__()
                self.pending = [b"ERROR: No saved session found with ID bad-ref"]

            def read(self, timeout: float = 0.2) -> bytes:
                del timeout
                return self.pending.pop(0) if self.pending else b""

            def is_alive(self) -> bool:
                return False

        host = TerminalHost(spawn=lambda *_args, **_kwargs: ExitedPty())
        created = host.create(
            project_id="universe", mode="MASTER", cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR, provider="CODEX"
        )
        listed = host.list_sessions()
        self.assertEqual("FAILED", listed[0]["state"])
        self.assertIn("No saved session found", listed[0]["exit_detail"])
        self.assertIsNone(
            host.find_live(project_id="universe", mode="MASTER", provider="CODEX")
        )
        waiter = host.subscribe(created["terminal_id"])
        self.assertIsNone(waiter.get(timeout=0.1))
        self.assertEqual(created["terminal_id"], listed[0]["terminal_id"])

    def test_create_passes_resume_argv_to_spawn(self) -> None:
        spawned: list[tuple] = []

        def spawn(executable, cwd, cols, rows, argv=None, environment=None):
            spawned.append(
                (executable, cwd, cols, rows, (argv if isinstance(argv, str) else list(argv or [])), dict(environment or {}))
            )
            return FakePty()

        host = TerminalHost(spawn=spawn)
        host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR,
            provider="GROK",
            resume_session_ref="01a00fe6-afff-7bc0-a75a-fe9e1569b3bf",
        )
        cmdline = spawned[0][4]
        self.assertIsInstance(cmdline, str)
        self.assertTrue(cmdline.startswith("/d /q /s /k "), cmdline)
        self.assertIn("--resume 01a00fe6-afff-7bc0-a75a-fe9e1569b3bf", cmdline)

    def test_find_live_reuses_the_same_coordinate(self) -> None:
        host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
        first = host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR,
            provider="GROK",
            supervisor_session_id="session_a",
        )
        found = host.find_live(
            project_id="universe",
            mode="MASTER",
            provider="GROK",
            supervisor_session_id="session_a",
        )
        self.assertEqual(first["terminal_id"], found["terminal_id"])
        self.assertIsNone(
            host.find_live(
                project_id="universe",
                mode="MASTER",
                provider="GROK",
                supervisor_session_id="session_b",
            )
        )
        self.assertIsNone(host.find_live(project_id="GCS", mode="MASTER", provider="GROK"))

    def test_subscribers_receive_the_same_output(self) -> None:
        class FeedingPty(FakePty):
            def __init__(self) -> None:
                super().__init__()
                self.pending: list[bytes] = []

            def read(self, timeout: float = 0.2) -> bytes:
                del timeout
                if self.pending:
                    return self.pending.pop(0)
                time.sleep(0.01)
                return b""

        pty = FeedingPty()
        host = TerminalHost(spawn=lambda *_args, **_kwargs: pty)
        created = host.create(project_id="universe", mode="MASTER", cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR, provider="GROK")
        first = host.subscribe(created["terminal_id"])
        second = host.subscribe(created["terminal_id"])
        pty.pending.append(b"hello-both")
        seen = []
        deadline = time.time() + 1
        while time.time() < deadline and len(seen) < 2:
            for waiter in (first, second):
                try:
                    chunk = waiter.get(timeout=0.05)
                except Exception:
                    continue
                if chunk:
                    seen.append(chunk)
            if seen.count(b"hello-both") >= 2:
                break
        self.assertGreaterEqual(seen.count(b"hello-both"), 2)
        host.close(created["terminal_id"])

    def test_late_subscriber_replays_bounded_history(self) -> None:
        class FeedingPty(FakePty):
            def __init__(self) -> None:
                super().__init__()
                self.pending = [b"old-chunk"]

            def read(self, timeout: float = 0.2) -> bytes:
                del timeout
                if self.pending:
                    return self.pending.pop(0)
                time.sleep(0.01)
                return b""

        host = TerminalHost(spawn=lambda *_args, **_kwargs: FeedingPty())
        created = host.create(project_id="universe", mode="MASTER", cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR, provider="GROK")
        first = host.subscribe(created["terminal_id"])
        deadline = time.time() + 1
        seen = b""
        while time.time() < deadline and b"old-chunk" not in seen:
            try:
                seen += first.get(timeout=0.05) or b""
            except Exception:
                continue
        self.assertIn(b"old-chunk", seen)
        late = host.subscribe(created["terminal_id"])
        dumped = b""
        try:
            dumped = late.get(timeout=0.2) or b""
        except Exception:
            dumped = b""
        self.assertIn(b"\x1b[2J\x1b[H", dumped)
        self.assertIn(b"old-chunk", dumped)
        host.close(created["terminal_id"])

    def test_output_history_is_cursor_paged_and_snapshot_is_row_bounded(self) -> None:
        host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
        created = host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR,
            provider="CODEX",
        )
        terminal_id = created["terminal_id"]
        for index in range(105):
            host.emit_output(terminal_id, f"row-{index}\n".encode("utf-8"))

        latest = host.history(terminal_id, limit=5)
        self.assertEqual([101, 102, 103, 104, 105], [
            chunk["cursor"] for chunk in latest["chunks"]
        ])
        self.assertTrue(latest["has_more"])
        older = host.history(
            terminal_id,
            before_cursor=latest["next_before_cursor"],
            limit=5,
        )
        self.assertEqual([96, 97, 98, 99, 100], [
            chunk["cursor"] for chunk in older["chunks"]
        ])
        decoded = b"".join(
            base64.b64decode(chunk["data_base64"]) for chunk in older["chunks"]
        )
        self.assertIn(b"row-99", decoded)

        snapshot = base64.b64decode(
            host.terminal_snapshot(terminal_id)["data_base64"]
        )
        self.assertTrue(snapshot.startswith(b"\x1b[2J\x1b[H"))
        self.assertIn(b"row-104", snapshot)
        self.assertNotIn(b"row-0\n", snapshot)

        host.emit_output(terminal_id, b"x" * (10 * 32 * 1024))
        bounded = host.history(terminal_id, limit=100)
        self.assertLessEqual(
            sum(chunk["byte_count"] for chunk in bounded["chunks"]),
            256 * 1024,
        )
        self.assertEqual(8, len(bounded["chunks"]))
        host.close(terminal_id)

    def test_screen_snapshot_tracks_split_unicode_and_fullscreen_cursor_updates(self) -> None:
        host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
        created = host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR,
            provider="CODEX",
        )
        terminal_id = created["terminal_id"]
        host.emit_output(terminal_id, b"shell history\r\n")
        host.emit_output(terminal_id, b"\x1b[?1049h\x1b[2J\x1b[H")
        host.emit_output(terminal_id, b"\x1b(B\x1bPignored-control\x1b\\")
        encoded = "상태: 대기".encode("utf-8")
        host.emit_output(terminal_id, b"\x1b[32m" + encoded[:1])
        host.emit_output(terminal_id, encoded[1:] + b"\x1b[0m")
        host.emit_output(terminal_id, b"\x1b[2;5H\x1b[31mREADY\x1b[0m")
        host.emit_output(terminal_id, b"\x1b[1;1HOK")

        snapshot = base64.b64decode(
            host.terminal_snapshot(terminal_id)["data_base64"]
        ).decode("utf-8")
        self.assertTrue(snapshot.startswith("\x1b[2J\x1b[H"))
        self.assertIn("OK태: 대기", snapshot)
        self.assertIn("    READY", snapshot)
        self.assertNotIn("shell history", snapshot)
        self.assertNotIn("ignored-control", snapshot)
        self.assertTrue(snapshot.endswith("\x1b[1;3H"))
        late = host.subscribe(terminal_id)
        self.assertEqual(snapshot.encode("utf-8"), late.get(timeout=0.2))
        host.unsubscribe(terminal_id, late)

        history = b"".join(
            base64.b64decode(chunk["data_base64"])
            for chunk in host.history(terminal_id, limit=100)["chunks"]
        )
        self.assertIn(b"shell history", history)
        self.assertIn(b"\x1b[?1049h", history)
        self.assertIn(encoded, history)

        host.emit_output(terminal_id, b"\x1b[?1049l")
        restored = base64.b64decode(
            host.terminal_snapshot(terminal_id)["data_base64"]
        ).decode("utf-8")
        self.assertIn("shell history", restored)
        self.assertNotIn("READY", restored)
        host.close(terminal_id)

    def test_transient_backend_read_failure_keeps_live_terminal_attached(self) -> None:
        class TransientPty(FakePty):
            def __init__(self) -> None:
                super().__init__()
                self.read_count = 0

            def read(self, timeout: float = 0.2) -> bytes:
                self.read_count += 1
                if self.read_count == 1:
                    raise OSError("transient host IPC failure")
                if self.read_count == 2:
                    return b"DOGFOOD_RECOVERED"
                time.sleep(min(timeout, 0.01))
                return b""

            def is_alive(self) -> bool:
                return True

        pty = TransientPty()
        host = TerminalHost(spawn=lambda *_args, **_kwargs: pty)
        created = host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT),
            session_anchor_ref=TEST_ANCHOR,
            provider="CODEX",
        )
        session = host.get(created["terminal_id"])
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            observed = b"".join(chunk for _cursor, chunk in session.output_chunks)
            if b"DOGFOOD_RECOVERED" in observed:
                break
            time.sleep(0.01)

        self.assertEqual("LIVE", session.state)
        self.assertIn(b"DOGFOOD_RECOVERED", observed)
        self.assertNotIn(
            "BACKEND_EXITED",
            [event["event_type"] for event in host.audit_events(terminal_id=created["terminal_id"])],
        )
        host.close(created["terminal_id"])

    def test_durable_audit_distinguishes_control_close_and_backend_exit(self) -> None:
        database_path = Path(tempfile.mkdtemp(prefix="terminal-audit-")) / "audit.sqlite3"
        pty = FakePty()
        host = TerminalHost(
            spawn=lambda *_args, **_kwargs: pty,
            audit_database_path=database_path,
        )
        created = host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR,
            provider="CODEX",
            supervisor_session_id="session_master_1",
            audit_context={"source": "TEST_CREATE", "request_id": "req-create"},
        )
        host.write(created["terminal_id"], b"plain-text")
        host.write(
            created["terminal_id"],
            b"\x03",
            audit_context={"source": "TEST_STREAM", "request_id": "req-input"},
        )
        host.close(
            created["terminal_id"],
            audit_context={"source": "TEST_DELETE", "request_id": "req-close"},
        )

        events = list(reversed(host.audit_events(terminal_id=created["terminal_id"])))
        self.assertEqual(
            [
                "TERMINAL_CREATED",
                "INPUT_CONTROL_WRITTEN",
                "DETACH_REQUESTED",
                "TERMINAL_DETACHED",
            ],
            [event["event_type"] for event in events],
        )
        self.assertEqual(4242, events[0]["pid"])
        self.assertEqual("TEST_STREAM", events[1]["source"])
        self.assertEqual(["CTRL_C"], events[1]["details"]["control_classes"])
        self.assertFalse(events[1]["details"]["content_persisted"])
        self.assertEqual("TEST_DELETE", events[2]["source"])

        class ExitedPty(FakePty):
            def is_alive(self) -> bool:
                return False

        exited_host = TerminalHost(
            spawn=lambda *_args, **_kwargs: ExitedPty(),
            audit_database_path=database_path,
        )
        exited = exited_host.create(
            project_id="universe", mode="MASTER", cwd=str(ROOT), session_anchor_ref=TEST_ANCHOR, provider="CODEX"
        )
        exited_host.list_sessions()
        exit_events = exited_host.audit_events(terminal_id=exited["terminal_id"])
        self.assertEqual("BACKEND_EXITED", exit_events[0]["event_type"])
        self.assertEqual("PTY_MONITOR", exit_events[0]["source"])
