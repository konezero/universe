from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_file_index import (  # noqa: E402
    FileIndexError,
    open_project_index_readonly,
    project_index_path,
    require_mode_current_anchor,
    search_index,
    should_skip,
    sync_index,
    sync_project_index_from_hook,
)


class UniverseFileIndexTests(unittest.TestCase):
    def test_skips_runtime_tmp_and_git(self) -> None:
        self.assertTrue(should_skip(".git/HEAD"))
        self.assertTrue(should_skip(".ai/runtime/tmp/foo.json"))
        self.assertTrue(should_skip(".ai/runtime/state/project_runtime.sqlite3"))
        self.assertTrue(should_skip(".ai/runtime/session_store/session.sqlite3"))
        self.assertTrue(should_skip(".ai/runtime/task_frames/frame.sqlite3-wal"))
        self.assertTrue(should_skip(".ai/runtime/release_db/release.sqlite3"))
        self.assertTrue(should_skip(".ai/inbox/MASTER/message.json"))
        self.assertFalse(should_skip(".ai/skills/common/example/SKILL.md"))
        self.assertFalse(should_skip(".ai/core/README.md"))
        self.assertFalse(
            should_skip(".ai/runtime/reference_runtime/contracts/frame.md")
        )
        self.assertFalse(
            should_skip(".ai/runtime/project_instance/boot_command_entry.md")
        )
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

    def test_project_owns_index_database_and_universe_reader_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            state = root / ".ai" / "runtime" / "state"
            state.mkdir(parents=True)
            runtime = sqlite3.connect(state / "project_runtime.sqlite3")
            runtime.execute(
                "CREATE TABLE mode_current_anchor (mode TEXT PRIMARY KEY, frame_id TEXT, anchor_id TEXT, state TEXT)"
            )
            runtime.execute(
                "INSERT INTO mode_current_anchor VALUES ('MASTER', 'current', 'MASTER-CURRENT-1', 'CURRENT')"
            )
            runtime.commit()
            runtime.close()

            synced = sync_project_index_from_hook(
                project_id="demo",
                project_root=root,
                mode="MASTER",
                anchor_id="MASTER-CURRENT-1",
            )
            self.assertEqual("PROJECT_INDEX_HOOK_SYNCED", synced["status"])
            index_path = project_index_path(root)
            self.assertTrue(index_path.is_file())
            moved_path = index_path.with_suffix(".closed-check")
            index_path.replace(moved_path)
            moved_path.replace(index_path)

            reader = open_project_index_readonly(project_id="demo", project_root=root)
            try:
                self.assertEqual(1, reader.execute("PRAGMA query_only").fetchone()[0])
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute(
                        "DELETE FROM project_file_index WHERE project_id = 'demo'"
                    )
            finally:
                reader.close()
            index_path.replace(moved_path)
            moved_path.replace(index_path)


if __name__ == "__main__":
    unittest.main()
