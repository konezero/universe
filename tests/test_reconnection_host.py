from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.reconnection_host import (  # noqa: E402
    CURRENT_RUNTIME_VERSIONS,
    ReconnectionHostError,
    ReconnectionHostRegistry,
    ReconnectionPty,
    STATE_SCHEMA,
    evaluate_runtime_compatibility,
    provision_private_registry_directory,
)


class ReconnectionHostRegistryTests(unittest.TestCase):
    def test_declared_matrix_projects_current_old_and_incompatible_tuples(self) -> None:
        current_tuple = tuple(CURRENT_RUNTIME_VERSIONS.values())
        old_tuple = (
            "UniverseLocal/0",
            "UniverseSupervisor/0",
            "UniverseSessionHost/0",
            "UniverseConPty/0",
        )
        matrix = {"CURRENT": {current_tuple}, "COMPATIBLE_OLD": {old_tuple}}

        self.assertEqual(
            evaluate_runtime_compatibility(CURRENT_RUNTIME_VERSIONS, matrix=matrix),
            "CURRENT",
        )
        self.assertEqual(
            evaluate_runtime_compatibility(
                dict(
                    zip(
                        CURRENT_RUNTIME_VERSIONS,
                        old_tuple,
                        strict=True,
                    )
                ),
                matrix=matrix,
            ),
            "COMPATIBLE_OLD",
        )
        incompatible = dict(CURRENT_RUNTIME_VERSIONS)
        incompatible["pty_version"] = "UniverseConPty/99"
        self.assertEqual(
            evaluate_runtime_compatibility(incompatible, matrix=matrix),
            "INCOMPATIBLE",
        )

    def test_replaced_host_history_is_redacted_and_never_reconnect_eligible(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "universe_app.reconnection_host.provision_private_registry_directory"
            ),
        ):
            root = Path(temp)
            registry = ReconnectionHostRegistry(root, root / "host.exe")
            state_path = self.write_state(
                registry,
                "anchor-history",
                pid=1001,
                started_at=10,
            )
            registry._archive_state_record(
                state_path, reason="INCOMPATIBLE_HOST_REPLACED"
            )
            archived_path = next((root / "history").glob("host-*.json"))
            archived = json.loads(archived_path.read_text(encoding="utf-8"))
            self.assertNotIn("auth_token", archived)
            self.assertEqual("REPLACED", archived["runtime_state"])
            self.assertFalse(archived["reconnect_eligible"])

            with patch(
                "universe_app.reconnection_host.process_is_alive",
                return_value=False,
            ):
                projected = registry.list_observed_hosts()
            historical = next(
                item for item in projected if item.get("runtime_state") == "REPLACED"
            )
            self.assertEqual("host-1001", historical["host_session_ref"])
            self.assertFalse(historical["reconnect_eligible"])

    def write_state(
        self,
        registry: ReconnectionHostRegistry,
        anchor_ref: str,
        *,
        pid: int,
        started_at: float,
        path: Path | None = None,
    ) -> Path:
        target = path or registry.state_path(anchor_ref)
        target.write_text(
            json.dumps(
                {
                    "schema": STATE_SCHEMA,
                    "anchor_ref": anchor_ref,
                    "host_kind": "SESSION",
                    "owner_ref": anchor_ref,
                    "host_id": f"host-{pid}",
                    "endpoint": "tcp://127.0.0.1:50000",
                    "pid": pid,
                    "started_at_unix_ms": int(started_at * 1000),
                    "auth_token": "test-token",
                    "child_pid": None,
                    **CURRENT_RUNTIME_VERSIONS,
                }
            ),
            encoding="utf-8",
        )
        return target

    def test_pty_liveness_keeps_ipc_failure_unknown(self) -> None:
        class FailingStatusClient:
            def request(self, operation: str, **kwargs: object) -> dict[str, object]:
                self.assert_operation(operation)
                return {"host": {"child_pid": 1234}}

            @staticmethod
            def assert_operation(operation: str) -> None:
                if operation != "attach":
                    raise AssertionError(operation)

            @staticmethod
            def status() -> dict[str, object]:
                raise ReconnectionHostError("transient IPC failure")

        pty = ReconnectionPty(FailingStatusClient(), "supervisor-test")
        with self.assertRaisesRegex(ReconnectionHostError, "transient IPC failure"):
            pty.is_alive()

    def test_pty_chunks_large_writes_below_host_request_limit(self) -> None:
        class RecordingClient:
            def __init__(self) -> None:
                self.state = type(
                    "State", (), {"child_pid": 1234, "host_id": "host-test", "anchor_ref": "anchor-test"}
                )()
                self.writes: list[bytes] = []

            def request(self, action: str, **fields: object) -> dict[str, object]:
                if action == "attach":
                    return {"host": {"child_pid": 1234}}
                if action == "write":
                    import base64

                    self.writes.append(
                        base64.b64decode(str(fields["input_base64"]))
                    )
                    return {"host": {"child_pid": 1234}}
                raise AssertionError(action)

        client = RecordingClient()
        pty = ReconnectionPty(client, "supervisor-test")
        payload = b"x" * 20000

        pty.write(payload)

        self.assertEqual(payload, b"".join(client.writes))
        self.assertEqual([8192, 8192, 3616], [len(item) for item in client.writes])

    def test_windows_acl_uses_exact_current_user_and_system_argv(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "universe_app.reconnection_host.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run,
        ):
            root = Path(temp) / "registry"
            provision_private_registry_directory(
                root,
                platform_name="nt",
                environment={
                    "USERNAME": "konezero",
                    "USERDOMAIN": "UNIVERSE",
                    "SystemRoot": r"C:\Windows",
                },
            )
        argv = run.call_args.args[0]
        self.assertEqual(r"C:\Windows\System32\icacls.exe", argv[0])
        self.assertIn(r"UNIVERSE\konezero:(OI)(CI)F", argv)
        self.assertIn("*S-1-5-18:(OI)(CI)F", argv)
        self.assertIn("/inheritance:r", argv)
        self.assertNotIn("/t", argv)
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_windows_acl_failure_is_fail_closed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "universe_app.reconnection_host.subprocess.run",
                return_value=subprocess.CompletedProcess([], 5, "", "access denied"),
            ),
        ):
            with self.assertRaisesRegex(
                ReconnectionHostError, "ACL provisioning failed"
            ):
                provision_private_registry_directory(
                    Path(temp) / "registry",
                    platform_name="nt",
                    environment={"USERNAME": "user", "SystemRoot": r"C:\Windows"},
                )

    def test_cleanup_removes_only_validated_dead_records_after_retention(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "universe_app.reconnection_host.provision_private_registry_directory"
            ) as provision,
        ):
            root = Path(temp)
            registry = ReconnectionHostRegistry(
                root,
                root / "host.exe",
                start_tolerance=1,
                stale_after_seconds=10,
            )
            dead = self.write_state(registry, "anchor-dead", pid=1001, started_at=10)
            recent = self.write_state(
                registry, "anchor-recent", pid=1002, started_at=20
            )
            live = self.write_state(registry, "anchor-live", pid=1003, started_at=30)
            recycled = self.write_state(
                registry, "anchor-recycled", pid=1004, started_at=40
            )
            invalid = root / "anchor-invalid.json"
            invalid.write_text("not-json", encoding="utf-8")
            os.utime(dead, (1000, 1000))
            os.utime(recent, (9995, 9995))
            os.utime(live, (1000, 1000))
            os.utime(recycled, (1000, 1000))
            os.utime(invalid, (1000, 1000))
            with (
                patch(
                    "universe_app.reconnection_host.process_is_alive",
                    side_effect=lambda pid: pid in {1003, 1004},
                ),
                patch(
                    "universe_app.reconnection_host.process_start_time",
                    side_effect=lambda pid: {1003: 30.0, 1004: 99.0}.get(pid),
                ),
            ):
                results = registry.cleanup_stale_records(now=10000)
                registry.cleanup_stale_records(now=10000)

            statuses = {Path(item["path"]).name: item["status"] for item in results}
            self.assertEqual("STALE_RECORD_REMOVED", statuses[dead.name])
            self.assertEqual("STALE_RECORD_DEFERRED", statuses[recent.name])
            self.assertEqual("LIVE_RECORD_PRESERVED", statuses[live.name])
            self.assertEqual("STALE_RECORD_REMOVED", statuses[recycled.name])
            self.assertEqual("INVALID_RECORD_PRESERVED", statuses[invalid.name])
            self.assertFalse(dead.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(live.exists())
            self.assertFalse(recycled.exists())
            self.assertTrue(invalid.exists())
            provision.assert_called_once_with(root)


if __name__ == "__main__":
    unittest.main()
