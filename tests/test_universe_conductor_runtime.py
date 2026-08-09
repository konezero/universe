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
    def test_prepare_without_mode_boot_binding_fails_before_process_start(self) -> None:
        prepared = {
            "status": "SESSION_PREPARED",
            "mode_current_anchor": {
                "status": "MODE_CURRENT_ANCHOR_CREATED",
                "snapshot": {
                    "snapshot": {"anchor_id": "UNIVERSE-CURRENT-001"}
                },
            },
        }

        anchor_id = UniverseConductorRuntime._prepared_anchor_id(prepared)
        with self.assertRaisesRegex(
            RuntimeError, "UNIVERSE_MODE_BOOT_BINDING_UNAVAILABLE"
        ):
            UniverseConductorRuntime._prepared_mode_boot_binding(
                prepared,
                anchor_id=anchor_id,
            )

    def test_start_prepares_conductor_mode_and_owns_session_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
            runtime_cli.parent.mkdir(parents=True)
            runtime_cli.write_text("# runtime cli\n", encoding="utf-8")
            registry = (
                root / ".ai" / "runtime" / "project_instance" / "mode_registry.json"
            )
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps(
                    {
                        "modes": {
                            "CONDUCTOR": {
                                "role": "CONDUCTOR",
                                "scope": "project-network/navigation/distribution",
                                "mode_profile": "GOVERNANCE_ONLY",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            requests: list[dict[str, Any]] = []

            def native_runner(request: Any) -> NativeCliResult:
                request_path = Path(request.arguments[-1])
                requests.append(json.loads(request_path.read_text(encoding="utf-8")))
                payload = (
                    {
                        "status": "SESSION_PREPARED",
                        "mode_current_anchor": {
                            "status": "MODE_CURRENT_ANCHOR_CREATED",
                            "snapshot": {
                                "snapshot": {
                                    "anchor_id": "UNIVERSE-CURRENT-001"
                                }
                            },
                        },
                        "mode_boot_binding": {
                            "status": "PREPARED",
                            "binding_id": "mode-boot-conductor-001",
                            "mode": "CONDUCTOR",
                            "role": "CONDUCTOR",
                            "frame_id": "current",
                            "anchor_id": "UNIVERSE-CURRENT-001",
                        },
                    }
                    if len(requests) == 1
                    else {"status": "COMMANDER_INPUT_OBSERVED"}
                )
                return NativeCliResult(
                    contract="universe.windows-native-cli.v1",
                    status="COMPLETED",
                    return_code=0,
                    duration_ms=1,
                    stdout=json.dumps(payload),
                    stderr="",
                    stdout_truncated=False,
                    stderr_truncated=False,
                )

            process: FakeProcess | None = None

            def process_factory(command: list[str], **_: Any) -> FakeProcess:
                nonlocal process
                token = command[command.index("--token") + 1]
                session_id = command[command.index("--session-id") + 1]
                process = FakeProcess(
                    {
                        "status": "SESSION_BOOT_IMAGE_CREATED",
                        "host_adapter": {
                            "endpoint": "http://127.0.0.1:41991",
                            "token": token,
                        },
                        "runtime_state": {
                            "anchor_id": "UNIVERSE-CURRENT-001",
                            "mode": "CONDUCTOR",
                            "role": "CONDUCTOR",
                            "session_id": session_id,
                            "executable_runtime_currentness": "CURRENT",
                        },
                        "mode_boot_binding": {
                            "status": "ACTIVE",
                            "binding_id": "mode-boot-conductor-001",
                        },
                    }
                )
                process.command = list(command)
                return process

            runtime = UniverseConductorRuntime(
                root,
                native_runner=native_runner,
                source_commit_resolver=lambda _: "a" * 40,
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

            self.assertEqual("UNIVERSE-CURRENT-001", binding["origin_anchor_ref"])
            self.assertEqual(
                "CURRENT", binding["runtime_currentness_observation"]
            )
            self.assertEqual("http://127.0.0.1:41991", binding["endpoint"])
            self.assertEqual("COMMANDER_INPUT_OBSERVED", observed["status"])
            self.assertEqual("CONDUCTOR", requests[0]["mode"])
            self.assertEqual("CONDUCTOR", requests[0]["role"])
            self.assertEqual("UNIVERSE_UI", requests[1]["commander_surface"])
            self.assertIn("session-boot", process.command)
            self.assertEqual(
                "mode-boot-conductor-001",
                process.command[process.command.index("--boot-binding-id") + 1],
            )
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
                source_commit_resolver=lambda _: "a" * 40,
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
