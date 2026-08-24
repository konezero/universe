from __future__ import annotations

import base64
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.terminal_host import (  # noqa: E402
    TerminalHost,
    TerminalHostError,
    resolve_cli_executable,
    resume_argv,
    startup_argv,
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


class TerminalHostTests(unittest.TestCase):
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
                (executable, cwd, cols, rows, list(argv or []), dict(environment or {}))
            )
            return FakePty()

        host = TerminalHost(spawn=spawn)
        created = host.create(
            project_id="GCS",
            mode="MASTER",
            cwd=str(ROOT),
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
        self.assertEqual("TERMINAL_CLOSED", closed["status"])
        self.assertEqual([], host.list_sessions())
        with self.assertRaises(TerminalHostError):
            host.get(created["terminal_id"])
        self.assertEqual(1, len(spawned))
        self.assertEqual(
            {
                "UNIVERSE_PROJECT_ID": "GCS",
                "UNIVERSE_MODE": "MASTER",
                "UNIVERSE_PROVIDER": "GROK",
                "UNIVERSE_MODEL_REF": "",
                "UNIVERSE_EFFORT": "AUTO",
                "UNIVERSE_SUPERVISOR_SESSION_ID": "",
                "UNIVERSE_TERMINAL_ID": created["terminal_id"],
                "UNIVERSE_SESSION_INBOX_CLI": str(
                    ROOT / "tools" / "universe_session_inbox.py"
                ),
            },
            spawned[0][5],
        )

    def test_missing_coordinate_is_rejected(self) -> None:
        host = TerminalHost(spawn=lambda *_args: FakePty())
        with self.assertRaises(TerminalHostError):
            host.create(project_id="", mode="MASTER", cwd=str(ROOT))

    def test_resize_keeps_a_usable_cli_box(self) -> None:
        pty = FakePty()
        host = TerminalHost(spawn=lambda *_args: pty)
        created = host.create(project_id="GCS", mode="MASTER", cwd=str(ROOT), provider="GROK")
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

    def test_fresh_claude_terminal_uses_a_new_session_id_without_resume(self) -> None:
        spawned: list[tuple] = []

        def spawn(executable, cwd, cols, rows, argv=None, environment=None):
            spawned.append((executable, cwd, cols, rows, list(argv or []), dict(environment or {})))
            return FakePty()

        host = TerminalHost(spawn=spawn)
        with patch(
            "universe_app.terminal_host.uuid.uuid4",
            return_value=uuid.UUID("12345678-1234-4678-9234-567812345678"),
        ), patch("universe_app.terminal_host.ensure_local_channel_server_registered"):
            created = host.create(
                project_id="universe", mode="MASTER", cwd=str(ROOT), provider="CLAUDE"
            )
        argv = spawned[0][4]
        self.assertIn("--session-id", argv)
        self.assertEqual("12345678-1234-4678-9234-567812345678", argv[argv.index("--session-id") + 1])
        self.assertNotIn("--resume", argv)
        self.assertEqual(
            ["--dangerously-load-development-channels", "server:universe_channel"],
            argv[-2:],
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
            project_id="universe", mode="MASTER", cwd=str(ROOT), provider="CODEX"
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
                (executable, cwd, cols, rows, list(argv or []), dict(environment or {}))
            )
            return FakePty()

        host = TerminalHost(spawn=spawn)
        host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT),
            provider="GROK",
            resume_session_ref="01a00fe6-afff-7bc0-a75a-fe9e1569b3bf",
        )
        self.assertEqual(
            ["--resume", "01a00fe6-afff-7bc0-a75a-fe9e1569b3bf"],
            spawned[0][4],
        )

    def test_find_live_reuses_the_same_coordinate(self) -> None:
        host = TerminalHost(spawn=lambda *_args, **_kwargs: FakePty())
        first = host.create(
            project_id="universe",
            mode="MASTER",
            cwd=str(ROOT),
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
        created = host.create(project_id="universe", mode="MASTER", cwd=str(ROOT), provider="GROK")
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
        created = host.create(project_id="universe", mode="MASTER", cwd=str(ROOT), provider="GROK")
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
            cwd=str(ROOT),
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
            cwd=str(ROOT),
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
                "CLOSE_REQUESTED",
                "TERMINAL_CLOSED",
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
            project_id="universe", mode="MASTER", cwd=str(ROOT), provider="CODEX"
        )
        exited_host.list_sessions()
        exit_events = exited_host.audit_events(terminal_id=exited["terminal_id"])
        self.assertEqual("BACKEND_EXITED", exit_events[0]["event_type"])
        self.assertEqual("PTY_MONITOR", exit_events[0]["source"])
