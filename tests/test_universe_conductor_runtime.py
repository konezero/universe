from __future__ import annotations

from io import StringIO
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_conductor_runtime import (  # noqa: E402
    UniverseConductorRuntime,
    UniverseConductorRuntimeError,
    invoke_server_action,
)
from session_supervisor import SessionSupervisorError, SessionSupervisorStore  # noqa: E402
from windows_native_cli import NativeCliResult  # noqa: E402


class FakeProcess:
    def __init__(self, startup: dict[str, Any]) -> None:
        self.pid = 4242
        self.stdout = StringIO(json.dumps(startup) + "\n")
        self.stderr = StringIO("")
        self.return_code: int | None = None
        self.command: list[str] = []

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.return_code = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.return_code = 0
        return 0

    def kill(self) -> None:
        self.return_code = -9


class UniverseConductorRuntimeTests(unittest.TestCase):
    def test_action_client_uses_one_server_envelope_and_rejects_context_claims(self) -> None:
        class ActionResponse:
            status = 200

            @staticmethod
            def read() -> bytes:
                return b'{"status":"FEATURE_GOAL_START_REPLAYED"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
            runtime_cli.parent.mkdir(parents=True)
            runtime_cli.write_text("# runtime cli\n", encoding="utf-8")
            runtime = UniverseConductorRuntime(
                root, action_endpoint="http://127.0.0.1:41992"
            )
            runtime._binding = {
                "endpoint": "http://127.0.0.1:41991",
                "token": "credential-ref-only",
            }
            with patch(
                "universe_conductor_runtime.urlopen",
                return_value=ActionResponse(),
            ) as opened:
                result = runtime.invoke_action(
                    "feature.goal.start", {"feature_id": "feature-1"}
                )
            request = opened.call_args.args[0]
            payload = json.loads(request.data.decode("utf-8"))
            self.assertEqual("http://127.0.0.1:41992/v1/actions", request.full_url)
            self.assertEqual("FEATURE_GOAL_START_REPLAYED", result["status"])
            self.assertEqual("feature.goal.start", payload["action_id"])
            self.assertEqual({"feature_id": "feature-1"}, payload["request"])
            self.assertNotIn("actor", payload)
            self.assertNotIn("context", payload)
            self.assertNotIn("mode", payload["request"])
            with self.assertRaisesRegex(
                UniverseConductorRuntimeError, "ACTION_CALLER_CONTEXT_FORBIDDEN"
            ):
                invoke_server_action(
                    "http://127.0.0.1:41991",
                    "credential-ref-only",
                    "feature.goal.start",
                    {"feature_id": "feature-1", "mode": "MASTER"},
                )

    def test_exact_session_attachment_does_not_replace_host_process_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
            runtime_cli.parent.mkdir(parents=True)
            runtime_cli.write_text("# runtime cli\n", encoding="utf-8")
            supervisor = SessionSupervisorStore(root / "universe.sqlite3")
            session, _ = supervisor.register_session(
                {
                    "session_id": "session-host-001",
                    "node": "GCS",
                    "project_id": "GCS",
                    "mode": "MASTER",
                    "provider": "CLAUDE",
                    "provider_session_ref": "claude-session-001",
                    "anchor_ref": "SESSION-ANCHOR-001",
                    "state": "STARTING",
                }
            )
            captured: list[str] = []

            def process_factory(command: list[str], **_: Any) -> FakeProcess:
                captured.extend(command)
                token = command[command.index("--token") + 1]
                return FakeProcess(
                    {
                        "status": "PERSISTENT_SESSION_ATTACHED",
                        "host_adapter": {
                            "endpoint": "http://127.0.0.1:41993",
                            "token": token,
                        },
                        "runtime_state": {
                            "anchor_id": session["session_anchor_ref"],
                            "mode": "MASTER",
                            "role": "UNASSIGNED",
                            "session_id": session["session_id"],
                            "executable_runtime_currentness": "CURRENT",
                        },
                        "attachment_path": "ANCHOR_GRAPH",
                    }
                )

            runtime = UniverseConductorRuntime(
                root,
                session_node="GCS",
                requested_mode="MASTER",
                exact_session_id=session["session_id"],
                session_location="UNIVERSE_SESSION_HOST",
                parent_actor_ref="universe-session-host:session-host-001",
                register_process_lease=False,
                source_binding_resolver=lambda _root: {
                    "status": "SELECTED",
                    "release_id": "core-test",
                    "source_repository": "fixture/universe-private",
                    "source_commit": "b" * 40,
                    "database_sha256": "c" * 64,
                },
                process_factory=process_factory,
                session_supervisor=supervisor,
            )
            with patch(
                "universe_conductor_runtime._required_host_executable",
                return_value=Path(sys.executable),
            ):
                binding = runtime.start()

            self.assertEqual(
                session["session_anchor_ref"], binding["origin_anchor_ref"]
            )
            self.assertEqual(
                "UNIVERSE_SESSION_HOST",
                captured[captured.index("--session-location") + 1],
            )
            self.assertIsNone(
                supervisor.get_session(session["session_id"])["process_lease"]
            )
            runtime.stop()

    def test_start_requires_selected_release_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
            runtime_cli.parent.mkdir(parents=True)
            runtime_cli.write_text("# runtime cli\n", encoding="utf-8")

            runtime = UniverseConductorRuntime(root)
            with self.assertRaisesRegex(
                UniverseConductorRuntimeError,
                "UNIVERSE_RELEASE_SELECTION_REQUIRED",
            ):
                runtime._resolved_source_binding()

    def test_start_requires_supervised_conductor_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
            runtime_cli.parent.mkdir(parents=True)
            runtime_cli.write_text("# runtime cli\n", encoding="utf-8")
            runtime = UniverseConductorRuntime(
                root,
                source_binding_resolver=lambda _root: {
                    "status": "SELECTED",
                    "release_id": "core-test",
                    "source_repository": "fixture/universe-private",
                    "source_commit": "b" * 40,
                    "database_sha256": "c" * 64,
                },
            )
        with self.assertRaisesRegex(
            UniverseConductorRuntimeError,
            "UNIVERSE_CONDUCTOR_SESSION_ANCHOR_UNAVAILABLE",
        ):
            runtime.start()

    def test_start_prepares_conductor_mode_and_owns_session_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
            runtime_cli.parent.mkdir(parents=True)
            runtime_cli.write_text("# runtime cli\n", encoding="utf-8")
            requests: list[dict[str, Any]] = []

            def native_runner(request: Any) -> NativeCliResult:
                del request
                raise AssertionError("Anchor Graph runtime must not invoke Runtime Boot")

            process: FakeProcess | None = None

            def process_factory(command: list[str], **_: Any) -> FakeProcess:
                nonlocal process
                token = command[command.index("--token") + 1]
                session_id = command[command.index("--session-id") + 1]
                anchor_id = command[command.index("--anchor-id") + 1]
                process = FakeProcess(
                    {
                        "status": "PERSISTENT_SESSION_ATTACHED",
                        "host_adapter": {
                            "endpoint": "http://127.0.0.1:41991",
                            "token": token,
                        },
                        "runtime_state": {
                            "anchor_id": anchor_id,
                            "mode": "CONDUCTOR",
                            "role": "UNASSIGNED",
                            "session_id": session_id,
                            "executable_runtime_currentness": "CURRENT",
                        },
                        "attachment_path": "ANCHOR_GRAPH",
                    }
                )
                process.command = list(command)
                return process

            runtime = UniverseConductorRuntime(
                root,
                native_runner=native_runner,
                source_binding_resolver=lambda _root: {
                    "status": "SELECTED",
                    "release_id": "core-test",
                    "source_repository": "fixture/universe-private",
                    "source_commit": "b" * 40,
                    "database_sha256": "c" * 64,
                },
                process_factory=process_factory,
                session_supervisor=(
                    supervisor := SessionSupervisorStore(
                        root / "universe.sqlite3",
                        process_observer=lambda pid, created: {
                            "status": "PROCESS_PRESENT_EXACT",
                            "pid": pid,
                            "process_created_at": created,
                        },
                    )
                ),
            )
            session, _ = supervisor.register_session(
                {
                    "session_id": "conductor-session",
                    "node": "CONDUCTOR",
                    "mode": "CONDUCTOR",
                    "provider": "CODEX",
                    "provider_session_ref": "codex-session",
                    "anchor_ref": "CONDUCTOR-CURRENT-001",
                    "currentness": "CURRENT",
                }
            )
            supervisor.set_default(
                session["session_id"], expected_pointer_version=0
            )
            with patch(
                "universe_conductor_runtime._required_host_executable",
                return_value=Path(sys.executable),
            ), patch(
                "universe_conductor_runtime.launched_process_identity",
                side_effect=lambda _process, **values: {
                    "pid": 4242,
                    "process_created_at": "2026-08-02T12:00:00Z",
                    "executable": str(values["executable"].resolve()),
                    "command": values["command"],
                    "endpoint": values["endpoint"],
                    "handshake_fingerprint": hashlib.sha256(
                        values["handshake_token"].encode("utf-8")
                    ).hexdigest(),
                },
            ):
                binding = runtime.start()
                observed = runtime.observe("message-001")

            self.assertEqual(session["session_anchor_ref"], binding["origin_anchor_ref"])
            self.assertEqual(
                "CURRENT", binding["runtime_currentness_observation"]
            )
            self.assertEqual("http://127.0.0.1:41991", binding["endpoint"])
            self.assertEqual(
                f"universe-release-db://core-test@{'c' * 64}",
                binding["source_ref"],
            )
            self.assertEqual("b" * 40, binding["source_commit"])
            self.assertEqual(
                "fixture/universe-private", binding["source_repository"]
            )
            self.assertEqual("COMMANDER_INPUT_OBSERVED", observed["status"])
            self.assertEqual([], requests)
            self.assertIn("project-runtime", process.command)
            self.assertNotIn("session-boot", process.command)
            self.assertNotIn("--boot-binding-id", process.command)
            self.assertEqual("CONDUCTOR", process.command[process.command.index("--mode") + 1])
            self.assertEqual(
                "current",
                process.command[process.command.index("--frame-id") + 1],
            )
            self.assertEqual(
                "OWNED",
                supervisor.get_session("conductor-session")["process_lease"][
                    "lease_state"
                ],
            )
            self.assertEqual(
                "UNIVERSE_UI",
                process.command[process.command.index("--commander-surface") + 1],
            )
            coordinate = runtime.continuity_coordinate()
            self.assertIsNotNone(coordinate)
            self.assertEqual(binding["session_id"], coordinate["session_id"])
            self.assertEqual("CONDUCTOR", coordinate["mode"])
            self.assertEqual(
                binding["runtime_currentness_observation"],
                coordinate["currentness"],
            )

            runtime.stop()
            self.assertEqual(0, process.return_code)
            self.assertEqual(
                "STOPPED", supervisor.get_session("conductor-session")["state"]
            )

    def test_stop_denial_retains_process_and_refreshes_lease_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
            runtime_cli.parent.mkdir(parents=True)
            runtime_cli.write_text("# runtime cli\n", encoding="utf-8")

            class DenyingSupervisor:
                @staticmethod
                def authorize_stop(*_args, **_kwargs):
                    raise SessionSupervisorError(
                        "STOP_AUTHORIZATION_DENIED", "identity mismatch"
                    )

                @staticmethod
                def get_session(_session_id):
                    return {"process_lease": {"lease_version": 7}}

            runtime = UniverseConductorRuntime(
                root,
                session_supervisor=DenyingSupervisor(),
            )
            process = FakeProcess({})
            runtime._process = process
            runtime._binding = {
                "session_id": "runtime-session",
                "origin_frame_id": "conductor",
                "origin_anchor_ref": "anchor",
                "runtime_currentness_observation": "CURRENT",
            }
            runtime._supervisor_session_id = "supervisor-session"
            runtime._lease_token = "lease-token"
            runtime._lease_version = 6
            runtime._process_identity = {
                "pid": 4242,
                "process_created_at": "2026-08-02T12:00:00Z",
                "executable": "python.exe",
                "command": ["python.exe"],
                "endpoint": "http://127.0.0.1:51702",
                "handshake_fingerprint": "a" * 64,
            }

            with self.assertRaisesRegex(
                SessionSupervisorError, "identity mismatch"
            ):
                runtime.stop()

            self.assertIs(runtime._process, process)
            self.assertIsNotNone(runtime._binding)
            self.assertIsNone(process.return_code)
            self.assertEqual(7, runtime._lease_version)


if __name__ == "__main__":
    unittest.main()
