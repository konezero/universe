from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_session_inject_hook import (  # noqa: E402
    resolve_mode,
    resolve_project_id,
    resolve_provider_and_ref,
    run_hook,
)


def _args(**overrides: object) -> Namespace:
    base = dict(
        repo_root=Path.cwd(),
        project_id="",
        mode="",
        provider="",
        session_ref="",
        provider_session_ref="",
        room_type="PROJECT",
        slot_role="MASTER",
        node="",
        make_default=None,
        state_file=None,
        from_stdin=False,
        trigger="test",
        update_session_md=False,
        strict=False,
        dry_run=False,
    )
    base.update(overrides)
    return Namespace(**base)


class SessionInjectHookTests(unittest.TestCase):
    def test_resolve_from_cli(self) -> None:
        provider, ref, source = resolve_provider_and_ref(
            args=_args(provider="CODEX", session_ref="thread-1"),
            stdin_payload=None,
            session_fields={},
            environment={},
        )
        self.assertEqual("CODEX", provider)
        self.assertEqual("thread-1", ref)
        self.assertEqual("CLI", source)

    def test_resolve_from_codex_env(self) -> None:
        provider, ref, source = resolve_provider_and_ref(
            args=_args(),
            stdin_payload=None,
            session_fields={},
            environment={"CODEX_THREAD_ID": " thr-env "},
        )
        self.assertEqual("CODEX", provider)
        self.assertEqual("thr-env", ref)
        self.assertEqual("CODEX_THREAD_ID", source)

    def test_resolve_from_claude_stdin(self) -> None:
        provider, ref, source = resolve_provider_and_ref(
            args=_args(),
            stdin_payload={"session_id": "claude-sess-9", "source": "startup"},
            session_fields={},
            environment={},
        )
        self.assertEqual("CLAUDE", provider)
        self.assertEqual("claude-sess-9", ref)
        self.assertEqual("STDIN.session_id", source)

    def test_resolve_grok_from_active_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grok_home = root / "grok-home"
            (grok_home).mkdir()
            active = [
                {
                    "session_id": "019fd10b-other-workspace",
                    "pid": 1,
                    "cwd": str(root / "other"),
                    "opened_at": "2026-08-01T00:00:00Z",
                },
                {
                    "session_id": "019fd10b-current-universe",
                    "pid": 2,
                    "cwd": str(root / "universe"),
                    "opened_at": "2026-08-06T00:00:00Z",
                },
            ]
            (grok_home / "active_sessions.json").write_text(
                json.dumps(active), encoding="utf-8"
            )
            repo = root / "universe"
            repo.mkdir()
            provider, ref, source = resolve_provider_and_ref(
                args=_args(),
                stdin_payload=None,
                session_fields={},
                environment={
                    "GROK_AGENT": "1",
                    "GROK_HOME": str(grok_home),
                },
                repo_root=repo,
            )
        self.assertEqual("GROK", provider)
        self.assertEqual("019fd10b-current-universe", ref)
        self.assertEqual("GROK.active_sessions.json", source)

    def test_resolve_grok_prefers_activity_over_opened_at(self) -> None:
        """Duplicate active_sessions rows: short new agent must not beat real chat."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grok_home = root / "grok-home"
            repo = root / "universe"
            repo.mkdir()
            from urllib.parse import quote

            encoded = quote(str(repo.resolve()), safe="")
            sess_root = grok_home / "sessions" / encoded
            real = sess_root / "real-long-session"
            short = sess_root / "short-dup-session"
            real.mkdir(parents=True)
            short.mkdir(parents=True)
            (real / "summary.json").write_text(
                json.dumps(
                    {
                        "last_active_at": "2026-08-06T04:12:00Z",
                        "num_chat_messages": 400,
                    }
                ),
                encoding="utf-8",
            )
            (short / "summary.json").write_text(
                json.dumps(
                    {
                        "last_active_at": "2026-08-06T04:09:00Z",
                        "num_chat_messages": 10,
                    }
                ),
                encoding="utf-8",
            )
            active = [
                {
                    "session_id": "real-long-session",
                    "pid": 1,
                    "cwd": str(repo),
                    "opened_at": "2026-08-05T08:00:00Z",
                },
                {
                    "session_id": "short-dup-session",
                    "pid": 1,
                    "cwd": str(repo),
                    "opened_at": "2026-08-06T04:08:00Z",  # newer open, less activity
                },
            ]
            (grok_home / "active_sessions.json").write_text(
                json.dumps(active), encoding="utf-8"
            )
            provider, ref, source = resolve_provider_and_ref(
                args=_args(),
                stdin_payload=None,
                session_fields={},
                environment={
                    "GROK_AGENT": "1",
                    "GROK_HOME": str(grok_home),
                },
                repo_root=repo,
            )
        self.assertEqual("GROK", provider)
        self.assertEqual("real-long-session", ref)
        self.assertEqual("GROK.active_sessions.activity", source)

    def test_resolve_grok_from_sessions_mtime_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grok_home = root / "grok-home"
            repo = root / "proj"
            repo.mkdir()
            from urllib.parse import quote

            encoded = quote(str(repo.resolve()), safe="")
            sess_root = grok_home / "sessions" / encoded
            older = sess_root / "old-session-id"
            newer = sess_root / "new-session-id"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            # Ensure mtime order: touch newer last.
            older.joinpath("marker").write_text("o", encoding="utf-8")
            newer.joinpath("marker").write_text("n", encoding="utf-8")
            provider, ref, source = resolve_provider_and_ref(
                args=_args(provider="GROK"),
                stdin_payload=None,
                session_fields={},
                environment={"GROK_HOME": str(grok_home)},
                repo_root=repo,
            )
        self.assertEqual("GROK", provider)
        self.assertEqual("new-session-id", ref)
        self.assertEqual("GROK.sessions_activity", source)

    def test_grok_env_still_wins_over_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grok_home = root / "g"
            grok_home.mkdir()
            (grok_home / "active_sessions.json").write_text(
                json.dumps(
                    [
                        {
                            "session_id": "from-active",
                            "cwd": str(root),
                            "opened_at": "2026-08-06T00:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            provider, ref, source = resolve_provider_and_ref(
                args=_args(),
                stdin_payload=None,
                session_fields={},
                environment={
                    "GROK_AGENT": "1",
                    "GROK_HOME": str(grok_home),
                    "GROK_SESSION_ID": "from-env",
                },
                repo_root=root,
            )
        self.assertEqual("GROK", provider)
        self.assertEqual("from-env", ref)
        self.assertEqual("GROK_SESSION_ID", source)

    def test_project_and_mode_from_session_md_fields(self) -> None:
        fields = {"Project": "universe", "Mode": "MASTER", "Node": "universe"}
        project = resolve_project_id(
            args=_args(),
            session_fields=fields,
            environment={},
            repo_root=None,
        )
        mode = resolve_mode(args=_args(), session_fields=fields, environment={})
        self.assertEqual("universe", project)
        self.assertEqual("MASTER", mode)

    def test_dry_run_skips_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai" / "runtime" / "tmp").mkdir(parents=True)
            result = run_hook(
                _args(
                    repo_root=root,
                    project_id="proj_a",
                    provider="GROK",
                    session_ref="g-1",
                    dry_run=True,
                    trigger="mode_change",
                ),
                environment={},
            )
        self.assertEqual("DRY_RUN", result["status"])
        self.assertEqual("GROK", result["provider"])
        self.assertEqual("g-1", result["session_ref"])
        self.assertEqual("UNASSIGNED", result["authority"])
        self.assertTrue(result.get("observation_path"))

    def test_offline_when_state_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing_state = root / "missing-server.json"
            result = run_hook(
                _args(
                    repo_root=root,
                    project_id="proj_a",
                    provider="CLAUDE",
                    session_ref="c-1",
                    state_file=missing_state,
                ),
                environment={},
            )
        self.assertEqual("OFFLINE", result["status"])

    def test_injected_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "server.json"
            state.write_text(
                json.dumps(
                    {
                        "endpoint": "http://127.0.0.1:59999",
                        "token": "test-token",
                    }
                ),
                encoding="utf-8",
            )
            fake_response = {
                "status": "SESSION_REF_INJECTED",
                "supervisor_session_created": True,
                "make_default": True,
                "resident_runtime_reload": "REQUIRED",
                "binding": {"binding_id": "bind_1"},
                "room": {"room_id": "room_1"},
                "bridge_line": "bridge",
            }
            with mock.patch(
                "universe_session_inject_hook.endpoint_reachable",
                return_value=True,
            ), mock.patch(
                "universe_session_inject_hook.post_inject",
                return_value=(200, fake_response, None),
            ):
                result = run_hook(
                    _args(
                        repo_root=root,
                        project_id="proj_a",
                        provider="CLAUDE",
                        session_ref="c-2",
                        state_file=state,
                        trigger="session_start",
                    ),
                    environment={},
                )
        self.assertEqual("INJECTED", result["status"])
        self.assertEqual("bind_1", result["inject_response"]["binding_id"])
        self.assertEqual("session_start", result["trigger"])


if __name__ == "__main__":
    unittest.main()
