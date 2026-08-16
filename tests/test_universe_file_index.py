from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_file_index import (  # noqa: E402
    FileIndexError,
    require_mode_current_anchor,
    search_index,
    should_skip,
    sync_index,
)


class UniverseFileIndexTests(unittest.TestCase):
    def test_skips_runtime_tmp_and_git(self) -> None:
        self.assertTrue(should_skip(".git/HEAD"))
        self.assertTrue(should_skip(".ai/runtime/tmp/foo.json"))
        self.assertTrue(should_skip("src/__pycache__/mod.pyc"))
        self.assertFalse(should_skip("src/app.py"))

    def test_incremental_sync_and_mechanical_search(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            target = src / "broker.py"
            target.write_text("class BrokerClient:\n    pass\n", encoding="utf-8")
            (root / "README.md").write_text("# Broker project\n", encoding="utf-8")
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.executescript(
                """
                CREATE TABLE project_connection (project_id TEXT PRIMARY KEY);
                INSERT INTO project_connection(project_id) VALUES ('GCS');
                """
            )
            from universe_file_index import DDL

            connection.executescript(DDL)
            first = sync_index(connection, project_id="GCS", project_root=root)
            self.assertGreaterEqual(first["created"], 2)
            second = sync_index(connection, project_id="GCS", project_root=root)
            self.assertEqual(0, second["created"])
            self.assertGreaterEqual(second["unchanged"], 2)
            target.write_text("class BrokerClient:\n    def ping(self):\n        return 'ok'\n", encoding="utf-8")
            third = sync_index(connection, project_id="GCS", project_root=root)
            self.assertEqual(1, third["updated"])
            found = search_index(connection, project_id="GCS", query="BrokerClient")
            self.assertEqual("src/broker.py", found["hits"][0]["relative_path"])
            self.assertTrue(found["hits"][0]["match"]["excerpt"])

    def test_require_mode_current_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / ".ai" / "runtime" / "state"
            store.mkdir(parents=True)
            db = store / "project_runtime.sqlite3"
            connection = sqlite3.connect(db)
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
            used = require_mode_current_anchor(
                root, mode="MASTER", anchor_id="MASTER-CURRENT-1"
            )
            self.assertEqual("MASTER-CURRENT-1", used["anchor_id"])
            with self.assertRaises(FileIndexError) as error:
                require_mode_current_anchor(
                    root, mode="MASTER", anchor_id="MASTER-CURRENT-OTHER"
                )
            self.assertEqual("MODE_CURRENT_ANCHOR_MISMATCH", error.exception.error_code)


if __name__ == "__main__":
    unittest.main()
