from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from legacy_executor_classifier import (  # noqa: E402
    classify_executor,
    classify_inventory,
    collect_windows_session_boot_executors,
)
import legacy_executor_classifier  # noqa: E402


def identity(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "pid": 42,
        "process_created_at": "2026-08-02T12:00:00Z",
        "executable": "C:\\Python\\python.exe",
        "command": ["python", "cli.py", "session-boot", "serve"],
        "endpoint": "http://127.0.0.1:51702",
        "handshake_fingerprint": hashlib.sha256(b"token").hexdigest(),
    }
    value.update(overrides)
    return value


def session() -> dict[str, object]:
    return {
        "session_id": "session-one",
        "process_lease": {
            "lease_state": "OWNED",
            "process_identity": identity(),
        },
    }


class LegacyExecutorClassifierTests(unittest.TestCase):
    def test_exact_match_is_managed_but_still_requires_supervisor_route(self) -> None:
        result = classify_executor(identity(), [session()])
        self.assertEqual("MANAGED_EXACT", result["status"])
        self.assertEqual("SESSION_SUPERVISOR", result["required_route"])
        self.assertFalse(result["destructive_action_permitted"])

    def test_partial_match_is_unknown_and_cannot_be_adopted_or_stopped(self) -> None:
        observed = identity(endpoint=None, handshake_fingerprint=None)
        result = classify_executor(observed, [session()])
        self.assertEqual("UNKNOWN", result["status"])
        self.assertEqual(["session-one"], result["candidate_session_ids"])
        self.assertFalse(result["destructive_action_permitted"])

    def test_unmatched_legacy_executor_is_unmanaged_not_auto_adopted(self) -> None:
        observed = identity(
            pid=99,
            endpoint=None,
            handshake_fingerprint=None,
            command=[
                "python",
                "cli.py",
                "session-boot",
                "serve",
                "positional-legacy-secret",
            ],
        )
        result = classify_executor(observed, [session()])
        self.assertEqual("UNMANAGED", result["status"])
        self.assertFalse(result["destructive_action_permitted"])
        public = result["observation"]
        self.assertNotIn("command", public)
        self.assertEqual("SESSION_BOOT_SERVE", public["command_profile"])
        self.assertEqual(64, len(public["command_fingerprint"]))
        self.assertNotIn("positional-legacy-secret", repr(result))

    def test_non_session_boot_process_is_excluded(self) -> None:
        ordinary = identity(command=["python", "-m", "pytest"])
        self.assertEqual([], classify_inventory([ordinary], [session()]))

    def test_windows_inventory_hides_the_powershell_process_window(self) -> None:
        calls: list[dict[str, object]] = []

        def runner(*_args: object, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(returncode=0, stdout="[]", stderr="")

        with (
            patch.object(legacy_executor_classifier.os, "name", "nt"),
            patch.object(
                legacy_executor_classifier.shutil,
                "which",
                return_value="C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
            ),
            patch.object(
                legacy_executor_classifier.subprocess,
                "CREATE_NO_WINDOW",
                0x08000000,
                create=True,
            ),
        ):
            result = collect_windows_session_boot_executors(runner=runner)

        self.assertEqual("HOST_INVENTORY_OBSERVED", result["status"])
        self.assertEqual(0x08000000, calls[0]["creationflags"])


if __name__ == "__main__":
    unittest.main()
