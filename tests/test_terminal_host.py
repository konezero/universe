from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.terminal_host import (  # noqa: E402
    TerminalHost,
    TerminalHostError,
    resume_argv,
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
        spawned: list[tuple[str, str, int, int]] = []

        def spawn(executable: str, cwd: str, cols: int, rows: int, argv=None) -> FakePty:
            spawned.append((executable, cwd, cols, rows, list(argv or [])))
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

    def test_missing_coordinate_is_rejected(self) -> None:
        host = TerminalHost(spawn=lambda *_args: FakePty())
        with self.assertRaises(TerminalHostError):
            host.create(project_id="", mode="MASTER", cwd=str(ROOT))

    def test_resize_keeps_a_usable_cli_box(self) -> None:
        pty = FakePty()
        host = TerminalHost(spawn=lambda *_args: pty)
        created = host.create(project_id="GCS", mode="MASTER", cwd=str(ROOT))
        host.resize(created["terminal_id"], 20, 8)
        session = host.get(created["terminal_id"])
        self.assertGreaterEqual(session.cols, 80)
        self.assertGreaterEqual(session.rows, 24)
        self.assertEqual((session.cols, session.rows), pty.size)

    def test_resume_argv_uses_provider_cli_flags(self) -> None:
        self.assertEqual(["--resume", "abc"], resume_argv("GROK", "abc"))
        self.assertEqual(["--resume", "abc"], resume_argv("CLAUDE", "abc"))
        self.assertEqual(["resume", "abc"], resume_argv("CODEX", "abc"))
        self.assertEqual([], resume_argv("GROK", "UNKNOWN"))
        self.assertEqual([], resume_argv("GROK", ""))

    def test_create_passes_resume_argv_to_spawn(self) -> None:
        spawned: list[tuple] = []

        def spawn(executable, cwd, cols, rows, argv=None):
            spawned.append((executable, cwd, cols, rows, list(argv or [])))
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
        first = host.create(project_id="universe", mode="MASTER", cwd=str(ROOT), provider="GROK")
        found = host.find_live(project_id="universe", mode="MASTER", provider="GROK")
        self.assertEqual(first["terminal_id"], found["terminal_id"])
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
        created = host.create(project_id="universe", mode="MASTER", cwd=str(ROOT))
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

    def test_late_subscriber_does_not_replay_history(self) -> None:
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
        created = host.create(project_id="universe", mode="MASTER", cwd=str(ROOT))
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
        self.assertNotIn(b"old-chunk", dumped)
        host.close(created["terminal_id"])
