from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai" / "skills" / "common" / "standalone-bootstrap" / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

from bootstrap_standalone import bootstrap  # noqa: E402


class StandaloneBootstrapTests(unittest.TestCase):
    def test_unmanaged_creates_session_anchor_and_updates_mode_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / ".ai" / "runtime" / "state"
            store.mkdir(parents=True)
            db = store / "project_runtime.sqlite3"
            connection = sqlite3.connect(str(db))
            connection.execute(
                """
                CREATE TABLE mode_current_anchor (
                    mode TEXT PRIMARY KEY,
                    anchor_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO mode_current_anchor VALUES (?, ?, ?, ?)",
                (
                    "MASTER",
                    "MASTER-CURRENT-KEEP",
                    "2026-01-01T00:00:00Z",
                    json.dumps({"anchor_id": "MASTER-CURRENT-KEEP"}),
                ),
            )
            connection.commit()
            connection.close()
            result = bootstrap(
                root,
                {
                    "provider": "GROK",
                    "session_id": "cli-session-1",
                    "requested_mode": "MASTER",
                },
                {},
            )
            self.assertEqual("STANDALONE_BOOTSTRAP_COMPLETE", result["status"])
            self.assertEqual("UNMANAGED", result["host_state"])
            self.assertEqual("MODE_ANCHOR_UPDATED", result["mode_anchor_status"])
            self.assertTrue(Path(result["session_sql_path"]).is_file())
            connection = sqlite3.connect(str(db))
            row = connection.execute(
                "SELECT mode, anchor_id, observed_at, snapshot_json FROM mode_current_anchor"
            ).fetchone()
            connection.close()
            self.assertEqual("MASTER", row[0])
            self.assertEqual("MASTER-CURRENT-KEEP", row[1])
            self.assertEqual("2026-01-01T00:00:00Z", row[2])
            snapshot = json.loads(str(row[3]))
            members = snapshot["unmanaged_session_anchors"]
            self.assertEqual(1, len(members))
            self.assertEqual(result["unmanaged_anchor_id"], members[0]["session_anchor_id"])
            self.assertEqual("UNMANAGED", members[0]["host_state"])

    def test_managed_host_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = bootstrap(
                Path(tmp),
                {"provider": "GROK", "session_id": "cli-session-2"},
                {"UNIVERSE_SUPERVISOR_SESSION_ID": "session_managed"},
            )
        self.assertEqual("MANAGED_HOST_USE_MANAGED_BOOTSTRAP", result["status"])
        self.assertEqual("MANAGED", result["host_state"])


if __name__ == "__main__":
    unittest.main()
