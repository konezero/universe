from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from architecture_drift_observer import (  # noqa: E402
    ArchitectureDriftError,
    ArchitectureDriftStore,
)


class ArchitectureDriftStoreTests(unittest.TestCase):
    def test_redacted_incident_requires_test_and_commit_to_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchitectureDriftStore(Path(directory) / "drift.sqlite3")
            incident = store.record(
                {
                    "expected_contract_ref": "docs/session-observatory-tailing-and-approval-hardening-plan.md",
                    "observed_behavior_code": "PWD_GROUP_USED_FOR_BOUND_SESSION",
                    "drift_class": "ARCHITECTURE_TO_IMPLEMENTATION",
                    "source_commit": "f" * 40,
                    "validation_ref": "test://session-observatory/current-location-grouping",
                }
            )
            self.assertEqual("OPEN", incident["state"])
            with self.assertRaises(ArchitectureDriftError):
                store.close(
                    incident["incident_id"],
                    correction_commit="UNKNOWN",
                    regression_test_ref="test://rail-grouping",
                )
            closed = store.close(
                incident["incident_id"],
                correction_commit="a" * 40,
                regression_test_ref="tests/test_universe_ui_session_observatory.py",
            )
            self.assertEqual("CLOSED", closed["state"])

    def test_raw_paths_and_session_refs_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ArchitectureDriftStore(Path(directory) / "drift.sqlite3")
            with self.assertRaisesRegex(ArchitectureDriftError, "forbidden"):
                store.record(
                    {
                        "expected_contract_ref": "docs/plan.md",
                        "observed_behavior_code": "RAW_PATH_LEAK",
                        "drift_class": "PRIVACY",
                        "source_commit": "f" * 40,
                        "validation_ref": "test://privacy",
                        "source_path": r"C:\private\session.jsonl",
                    }
                )


if __name__ == "__main__":
    unittest.main()
