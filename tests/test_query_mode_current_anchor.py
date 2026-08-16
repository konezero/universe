from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".ai" / "skills" / "common"))

from query_mode_current_anchor import query  # noqa: E402


class QueryModeCurrentAnchorTests(unittest.TestCase):
    def test_returns_stored_current_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / ".ai" / "runtime" / "state"
            store.mkdir(parents=True)
            connection = sqlite3.connect(store / "project_runtime.sqlite3")
            connection.execute(
                """
                CREATE TABLE mode_current_anchor (
                    mode TEXT PRIMARY KEY,
                    frame_id TEXT NOT NULL,
                    anchor_id TEXT NOT NULL,
                    state TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO mode_current_anchor VALUES ('MASTER', 'current', 'MASTER-CURRENT-1', 'CURRENT')"
            )
            connection.commit()
            connection.close()
            result = query(root, {"mode": "MASTER", "session_id": "demo-session"})
            self.assertEqual("MODE_CURRENT_ANCHOR_READY", result["status"])
            self.assertEqual("MASTER-CURRENT-1", result["anchor_id"])
            self.assertTrue(result["companions_are_refs"])
            self.assertFalse(result["session_sql_present"])

    def test_mismatch_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / ".ai" / "runtime" / "state"
            store.mkdir(parents=True)
            connection = sqlite3.connect(store / "project_runtime.sqlite3")
            connection.execute(
                """
                CREATE TABLE mode_current_anchor (
                    mode TEXT PRIMARY KEY,
                    frame_id TEXT NOT NULL,
                    anchor_id TEXT NOT NULL,
                    state TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO mode_current_anchor VALUES ('MASTER', 'current', 'MASTER-CURRENT-1', 'CURRENT')"
            )
            connection.commit()
            connection.close()
            result = query(root, {"mode": "MASTER", "anchor_id": "MASTER-CURRENT-OTHER"})
            self.assertEqual("MODE_CURRENT_ANCHOR_MISMATCH", result["status"])


if __name__ == "__main__":
    unittest.main()
