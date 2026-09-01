from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
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
    _claude_settings_hook,
    _supervisor_identity_observation,
    is_universe_managed_host,
    main,
    provider_hook_stdout,
    resolve_mode,
    resolve_project_id,
    resolve_provider_and_ref,
    run_hook,
    setup_provider_hooks,
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
            args=_args(provider="CLAUDE"),
            stdin_payload={"session_id": "claude-sess-9", "source": "startup"},
            session_fields={},
            environment={},
        )
        self.assertEqual("CLAUDE", provider)
        self.assertEqual("claude-sess-9", ref)
        self.assertEqual("STDIN.session_id", source)

    def test_grok_hook_env_overrides_claude_compat_command(self) -> None:
        provider, ref, source = resolve_provider_and_ref(
            args=_args(provider="CLAUDE"),
            stdin_payload={"sessionId": "01a0172e-1c8a-7ae0-bd72-42f97ca90b70"},
            session_fields={},
            environment={"GROK_HOOK_EVENT": "session_start"},
        )
        self.assertEqual("GROK", provider)
        self.assertEqual("01a0172e-1c8a-7ae0-bd72-42f97ca90b70", ref)

    def test_stdin_session_id_without_provider_is_not_claude(self) -> None:
        provider, ref, source = resolve_provider_and_ref(
            args=_args(),
            stdin_payload={"session_id": "maybe-grok", "source": "startup"},
            session_fields={},
            environment={},
        )
        self.assertNotEqual("CLAUDE", provider)
        self.assertIn(provider, {None, "GROK"})

    def test_grok_session_folder_overrides_claude_compat_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "universe"
            repo.mkdir()
            grok_home = root / "grok-home"
            from urllib.parse import quote

            session_id = "01a0172e-grok-session"
            session_dir = grok_home / "sessions" / quote(str(repo.resolve()), safe="") / session_id
            session_dir.mkdir(parents=True)
            provider, ref, source = resolve_provider_and_ref(
                args=_args(provider="CLAUDE"),
                stdin_payload={"sessionId": session_id},
                session_fields={},
                environment={"GROK_HOME": str(grok_home)},
                repo_root=repo,
            )
        self.assertEqual("GROK", provider)
        self.assertEqual(session_id, ref)

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

    def test_project_and_mode_ignore_stale_session_md_fields(self) -> None:
        fields = {"Project": "stale", "Mode": "MASTER", "Node": "stale"}
        project = resolve_project_id(
            args=_args(),
            session_fields=fields,
            environment={"UNIVERSE_PROJECT_ID": "universe"},
            repo_root=None,
        )
        mode = resolve_mode(
            args=_args(),
            session_fields=fields,
            environment={"UNIVERSE_MODE": "CONDUCTOR"},
        )
        self.assertEqual("universe", project)
        self.assertEqual("CONDUCTOR", mode)

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
                environment={
                    "UNIVERSE_SUPERVISOR_SESSION_ID": "session_web_terminal"
                },
            )
        self.assertEqual("DRY_RUN", result["status"])
        self.assertEqual("GROK", result["provider"])
        self.assertEqual("g-1", result["session_ref"])
        self.assertEqual(
            "session_web_terminal",
            result["inject_body"]["supervisor_session_id"],
        )
        self.assertEqual(
            "mode_change",
            result["inject_body"]["hook_observation"]["trigger"],
        )
        self.assertEqual(
            "universe.hook-session-observation.v1",
            result["inject_body"]["hook_observation"]["schema"],
        )
        self.assertEqual("UNASSIGNED", result["authority"])
        self.assertTrue(result.get("observation_path"))

    def test_is_universe_managed_host(self) -> None:
        self.assertFalse(is_universe_managed_host({}))
        self.assertFalse(is_universe_managed_host({"GROK_AGENT": "1"}))
        self.assertTrue(
            is_universe_managed_host(
                {"UNIVERSE_SUPERVISOR_SESSION_ID": "session_1"}
            )
        )
        self.assertTrue(
            is_universe_managed_host(
                {
                    "UNIVERSE_TERMINAL_ID": "term_1",
                    "UNIVERSE_MANAGED_SHELL": "1",
                }
            )
        )

    def test_unmanaged_session_start_skips_inject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai" / "runtime" / "tmp").mkdir(parents=True)
            result = run_hook(
                _args(
                    repo_root=root,
                    project_id="proj_current_mode",
                    provider="CODEX",
                    session_ref="current-provider-ref",
                    dry_run=True,
                    trigger="session_start",
                ),
                environment={"GROK_AGENT": "1"},
            )
        self.assertEqual("SKIPPED", result["status"])
        self.assertEqual("UNMANAGED", result["host_state"])
        self.assertEqual(
            "unmanaged host; managed hook skipped",
            result["detail"],
        )
        self.assertEqual(
            "STANDALONE_BOOTSTRAP_COMPLETE",
            result["standalone_bootstrap"]["status"],
        )
        self.assertNotIn("inject_body", result)

    def test_unmanaged_mode_change_skips_inject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai" / "runtime" / "tmp").mkdir(parents=True)
            result = run_hook(
                _args(
                    repo_root=root,
                    project_id="proj_current_mode",
                    provider="GROK",
                    session_ref="cli-session",
                    dry_run=True,
                    trigger="mode_change",
                ),
                environment={"GROK_AGENT": "1"},
            )
        self.assertEqual("SKIPPED", result["status"])
        self.assertEqual("UNMANAGED", result["host_state"])
        self.assertEqual(
            "unmanaged host; managed hook skipped",
            result["detail"],
        )
        self.assertEqual(
            "STANDALONE_BOOTSTRAP_COMPLETE",
            result["standalone_bootstrap"]["status"],
        )

    def test_managed_session_start_leaves_mode_for_server_identity_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".ai" / "runtime" / "tmp").mkdir(parents=True)
            result = run_hook(
                _args(
                    repo_root=root,
                    project_id="proj_current_mode",
                    provider="CODEX",
                    session_ref="current-provider-ref",
                    dry_run=True,
                    trigger="session_start",
                ),
                environment={
                    "UNIVERSE_SUPERVISOR_SESSION_ID": "session_managed_1"
                },
            )
        self.assertEqual("DRY_RUN", result["status"])
        self.assertEqual("", result["mode"])
        self.assertNotIn("mode", result["inject_body"])
        self.assertNotIn("node", result["inject_body"])

    def test_patch_real_observer_overwrites_existing_observer(self) -> None:
        from universe_session_inject_hook import patch_mode_current_anchor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / ".ai" / "runtime" / "state"
            store.mkdir(parents=True)
            db = store / "project_runtime.sqlite3"
            import sqlite3

            connection = sqlite3.connect(str(db))
            connection.execute(
                "CREATE TABLE mode_current_anchor (mode TEXT PRIMARY KEY, snapshot_json TEXT)"
            )
            connection.execute(
                "INSERT INTO mode_current_anchor(mode, snapshot_json) VALUES (?, ?)",
                (
                    "MASTER",
                    json.dumps(
                        {
                            "snapshot": {
                                "observer_session_ref": "grok-cli:keep-me",
                            }
                        }
                    ),
                ),
            )
            connection.commit()
            connection.close()
            result = patch_mode_current_anchor(
                root, provider="GROK", session_ref="real-grok", mode="MASTER"
            )
        self.assertEqual("ANCHOR_PATCHED", result["status"])
        self.assertEqual("grok-cli:real-grok", result["observer_session_ref"])

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
                "runtime_attachment": {
                    "schema": "universe.session-runtime-attachment.v1",
                    "status": "WORK_READY",
                    "session_id": "session-1",
                    "session_anchor_ref": "anchor-1",
                },
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
                    environment={
                        "UNIVERSE_SUPERVISOR_SESSION_ID": "session_managed_inject"
                    },
                )
        self.assertEqual("INJECTED", result["status"])
        self.assertEqual("bind_1", result["inject_response"]["binding_id"])
        self.assertEqual(
            "WORK_READY",
            result["inject_response"]["runtime_attachment"]["status"],
        )
        self.assertEqual("session_start", result["trigger"])

    def test_session_start_binds_live_pty_before_provider_id_exists(self) -> None:
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
            captured: dict[str, object] = {}

            def post(*, payload, **_kwargs):
                captured.update(payload)
                return 200, {"status": "SESSION_REF_INJECTED"}, None

            with mock.patch(
                "universe_session_inject_hook.endpoint_reachable",
                return_value=True,
            ), mock.patch("universe_session_inject_hook.post_inject", side_effect=post):
                result = run_hook(
                    _args(
                        repo_root=root,
                        project_id="proj_pty_start",
                        provider="CLAUDE",
                        state_file=state,
                        trigger="session_start",
                    ),
                    environment={
                        "UNIVERSE_SUPERVISOR_SESSION_ID": "session_pty_start_1"
                    },
                )
        self.assertEqual("INJECTED", result["status"])
        self.assertEqual("session_pty_start_1", captured["supervisor_session_id"])
        self.assertNotIn("provider_session_ref", captured)
        self.assertEqual("STARTING", captured["state"])
        self.assertEqual("DEFERRED_PROVIDER_IDENTITY", result["anchor_patch"]["status"])

    def test_codex_session_start_is_silent_for_legacy_hook_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = StringIO()
            with (
                mock.patch("sys.stdin", StringIO('{"session_id":"thread-1"}')),
                redirect_stdout(output),
            ):
                code = main(
                    [
                        "--repo-root",
                        tmp,
                        "--provider",
                        "CODEX",
                        "--from-stdin",
                        "--trigger",
                        "session_start",
                        "--dry-run",
                    ]
                )
        self.assertEqual(0, code)
        self.assertEqual("", output.getvalue())

    def test_claude_hook_stdout_uses_common_runtime_event_shape(self) -> None:
        output = provider_hook_stdout(
            {
                "inject_response": {
                    "pending_instruction_dispatch": {
                        "status": "DISPATCHED",
                        "hook_stdout": {
                            "hookSpecificOutput": {
                                "hookEventName": "SessionStart",
                                "additionalContext": "A pending instruction was claimed.",
                            }
                        },
                    }
                }
            },
            provider="CLAUDE",
            trigger="session_start",
        )
        self.assertEqual(
            "SessionStart", output["hookSpecificOutput"]["hookEventName"]
        )

    def test_claude_session_start_is_silent_without_common_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = StringIO()
            with (
                mock.patch("sys.stdin", StringIO('{"session_id":"claude-1"}')),
                redirect_stdout(output),
            ):
                code = main(
                    [
                        "--repo-root",
                        tmp,
                        "--provider",
                        "CLAUDE",
                        "--from-stdin",
                        "--trigger",
                        "session_start",
                        "--dry-run",
                    ]
                )
        self.assertEqual(0, code)
        self.assertEqual("", output.getvalue())

    def test_setup_writes_quiet_project_codex_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = setup_provider_hooks(
                root,
                global_=False,
                providers=["CODEX"],
                python_exe="python",
                script_path="tools/universe_session_inject_hook.py",
            )
            config = (root / ".codex" / "config.toml").read_text(
                encoding="utf-8"
            )
        self.assertEqual("WRITTEN", result["providers"]["CODEX"]["status"])
        self.assertIn("--quiet", config)

    def test_supervisor_identity_file_supplies_owned_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_path = Path(tmp) / "managed-shell.json"
            identity_path.write_text(
                json.dumps(
                    {
                        "schema": "universe.managed-shell-identity.v1",
                        "terminal_id": "term_owned",
                        "session_anchor_ref": "session_anchor_owned",
                        "shell_pid": 4242,
                        "shell_started_at": 123.5,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "universe_session_inject_hook._native_attach_observation",
                return_value={
                    "status": "OBSERVED",
                    "shell_candidates": [
                        {
                            "shell_pid": 4242,
                            "shell_started_at": 123.5,
                            "cli_pid": 4343,
                            "cli_started_at": 124.5,
                        }
                    ],
                },
            ):
                observed = _supervisor_identity_observation(
                    "term_owned",
                    {
                        "UNIVERSE_MANAGED_SHELL_IDENTITY_FILE": str(identity_path),
                        "UNIVERSE_SESSION_ANCHOR_REF": "session_anchor_owned",
                    },
                )
        self.assertIsNotNone(observed)
        self.assertEqual(
            "SUPERVISOR_IDENTITY_FILE+WINDOWS_NATIVE",
            observed["inspection_source"],
        )
        self.assertEqual(4242, observed["shell_candidates"][0]["shell_pid"])
        self.assertEqual(4343, observed["shell_candidates"][0]["cli_pid"])

    def test_supervisor_identity_finds_claude_beside_console_pipe_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_path = Path(tmp) / "managed-shell.json"
            identity_path.write_text(
                json.dumps(
                    {
                        "schema": "universe.managed-shell-identity.v1",
                        "terminal_id": "term_pipe",
                        "session_anchor_ref": "session_anchor_pipe",
                        "shell_pid": 4242,
                        "shell_started_at": 123.5,
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch(
                "universe_session_inject_hook._native_attach_observation",
                return_value={"status": "SHELL_NOT_FOUND"},
            ), mock.patch(
                "universe_app.windows_process.child_pids",
                side_effect=lambda pid: [4300, 4343] if pid == 4242 else [],
            ), mock.patch(
                "universe_app.windows_process.process_name",
                side_effect=lambda pid: "claude.exe" if pid == 4343 else "more.com",
            ), mock.patch(
                "universe_app.windows_process.process_start_time",
                return_value=124.5,
            ):
                observed = _supervisor_identity_observation(
                    "term_pipe",
                    {
                        "UNIVERSE_MANAGED_SHELL_IDENTITY_FILE": str(identity_path),
                        "UNIVERSE_SESSION_ANCHOR_REF": "session_anchor_pipe",
                        "UNIVERSE_PROVIDER": "CLAUDE",
                    },
                )
        self.assertIsNotNone(observed)
        self.assertEqual(
            "SUPERVISOR_IDENTITY_FILE+WINDOWS_DESCENDANT",
            observed["inspection_source"],
        )
        self.assertEqual(4343, observed["shell_candidates"][0]["cli_pid"])
        self.assertEqual(124.5, observed["shell_candidates"][0]["cli_started_at"])

    def test_claude_hook_quotes_windows_script_path_for_shell(self) -> None:
        payload = _claude_settings_hook(
            r"C:\workspace\universe\tools\universe_session_inject_hook.py"
        )
        command = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertEqual(
            "python -c \"import runpy; "
            "runpy.run_path('C:/workspace/universe/tools/"
            "universe_session_inject_hook.py', run_name='__main__')\" "
            "--repo-root . --provider CLAUDE --from-stdin --trigger session_start",
            command,
        )
        self.assertNotIn("\\", command)

    def test_setup_writes_project_claude_and_grok_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = setup_provider_hooks(
                root,
                global_=False,
                providers=["GROK", "CLAUDE"],
                python_exe="python",
                script_path="tools/universe_session_inject_hook.py",
            )
            self.assertEqual("SETUP_HOOKS_DONE", result["status"])
            grok_hook = root / ".grok" / "hooks" / "session-start.json"
            claude_hook = root / ".claude" / "settings.json"
            self.assertEqual("WRITTEN", result["providers"]["GROK_PROJECT"]["status"])
            self.assertEqual("WRITTEN", result["providers"]["CLAUDE_PROJECT"]["status"])
            self.assertTrue(grok_hook.is_file())
            self.assertIn("--provider GROK", grok_hook.read_text(encoding="utf-8"))
            self.assertIn("--provider CLAUDE", claude_hook.read_text(encoding="utf-8"))
            self.assertIn(
                "python -m tools.universe_session_inject_hook",
                claude_hook.read_text(encoding="utf-8"),
            )
            again = setup_provider_hooks(
                root,
                global_=False,
                providers=["GROK", "CLAUDE"],
                python_exe="python",
                script_path="tools/universe_session_inject_hook.py",
            )
            self.assertEqual("CURRENT", again["providers"]["GROK_PROJECT"]["status"])
            self.assertEqual("CURRENT", again["providers"]["CLAUDE_PROJECT"]["status"])

    def test_setup_updates_existing_inject_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grok_path = root / ".grok" / "hooks" / "session-start.json"
            grok_path.parent.mkdir(parents=True)
            grok_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python tools/universe_session_inject_hook.py --repo-root . --from-stdin --trigger session_start",
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = setup_provider_hooks(
                root,
                global_=False,
                providers=["GROK"],
                python_exe="python",
                script_path="tools/universe_session_inject_hook.py",
            )
            self.assertEqual("UPDATED", result["providers"]["GROK_PROJECT"]["status"])
            self.assertIn("--provider GROK", grok_path.read_text(encoding="utf-8"))

    def test_setup_repairs_claude_stamped_grok_observer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from urllib.parse import quote

            session_id = "01a0172e-repair-me"
            grok_home = root / "grok-home"
            session_dir = (
                grok_home / "sessions" / quote(str(root.resolve()), safe="") / session_id
            )
            session_dir.mkdir(parents=True)
            db = root / ".ai" / "runtime" / "state" / "project_runtime.sqlite3"
            db.parent.mkdir(parents=True)
            connection = __import__("sqlite3").connect(str(db))
            connection.execute(
                "CREATE TABLE mode_current_anchor (mode TEXT PRIMARY KEY, snapshot_json TEXT)"
            )
            connection.execute(
                "INSERT INTO mode_current_anchor(mode, snapshot_json) VALUES (?, ?)",
                (
                    "MASTER",
                    json.dumps(
                        {
                            "snapshot": {
                                "observer_session_ref": f"claude-code:{session_id}",
                            }
                        }
                    ),
                ),
            )
            connection.commit()
            connection.close()
            result = setup_provider_hooks(
                root,
                global_=False,
                providers=["GROK"],
                python_exe="python",
                script_path="tools/universe_session_inject_hook.py",
                environment={"GROK_HOME": str(grok_home)},
            )
            repaired = [item for item in result["repairs"] if item.get("status") == "REPAIRED"]
            self.assertEqual(1, len(repaired))
            self.assertEqual(f"grok-cli:{session_id}", repaired[0]["to"])


if __name__ == "__main__":
    unittest.main()
