from __future__ import annotations

from io import StringIO
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
from windows_native_cli import NativeCliResult  # noqa: E402


class FakeProcess:
    def __init__(self, startup: dict[str, Any]) -> None:
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
                    }
                )
                process.command = list(command)
                return process

            runtime = UniverseConductorRuntime(
                root,
                native_runner=native_runner,
                source_commit_resolver=lambda _: "a" * 40,
                process_factory=process_factory,
            )
            with patch(
                "universe_conductor_runtime._required_host_executable",
                return_value=Path(sys.executable),
            ):
                binding = runtime.start()
                observed = runtime.observe("message-001")

            self.assertEqual("UNIVERSE-CURRENT-001", binding["origin_anchor_ref"])
            self.assertEqual("http://127.0.0.1:41991", binding["endpoint"])
            self.assertEqual("COMMANDER_INPUT_OBSERVED", observed["status"])
            self.assertEqual("CONDUCTOR", requests[0]["mode"])
            self.assertEqual("CONDUCTOR", requests[0]["role"])
            self.assertEqual("UNIVERSE_UI", requests[1]["commander_surface"])
            self.assertIn("session-boot", process.command)
            self.assertEqual(
                "UNIVERSE_UI",
                process.command[process.command.index("--commander-surface") + 1],
            )

            runtime.stop()
            self.assertEqual(0, process.return_code)


if __name__ == "__main__":
    unittest.main()
