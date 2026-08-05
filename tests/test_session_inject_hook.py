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
