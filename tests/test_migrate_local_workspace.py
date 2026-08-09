from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from migrate_local_workspace import (  # noqa: E402
    WorkspaceMigrationError,
    migrate_workspace,
    restore_workspace,
    tracked_workspace_paths,
)


class LocalWorkspaceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.backup = Path(self.temp.name) / "backup"
        self.root.mkdir()
        self._git("init")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Universe Test")
        (self.root / ".gitignore").write_text(".ai/\n", encoding="utf-8")
        workspace = self.root / ".ai"
        (workspace / "runtime").mkdir(parents=True)
        (workspace / "runtime" / "contract.md").write_text(
            "runtime contract\n", encoding="utf-8"
        )
        (workspace / "state").mkdir()
        (workspace / "state" / "local.db").write_bytes(b"local-state")
        self._git("add", "-f", ".ai/runtime/contract.md")
        self._git("add", ".gitignore")
        self._git("commit", "-m", "legacy tracked workspace")
        (workspace / "runtime" / "contract.md").write_text(
            "locally updated runtime\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )

    def test_migrate_backs_up_and_removes_only_the_git_index_entry(self) -> None:
        result = migrate_workspace(
            self.root,
            self.backup,
            quiescence_evidence_ref="operator:hosts-stopped:1",
        )

        self.assertEqual("LOCAL_WORKSPACE_UNTRACKED", result["status"])
        self.assertEqual([], tracked_workspace_paths(self.root))
        self.assertEqual(
            "locally updated runtime\n",
            (self.root / ".ai/runtime/contract.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            b"local-state", (self.root / ".ai/state/local.db").read_bytes()
        )
        manifest = json.loads(
            (self.backup / "workspace-backup.json").read_text(encoding="utf-8")
        )
        self.assertEqual(2, len(manifest["files"]))
        self.assertEqual(
            "operator:hosts-stopped:1", result["quiescence_evidence_ref"]
        )

    def test_migrate_requires_explicit_quiescence_evidence(self) -> None:
        with self.assertRaisesRegex(
            WorkspaceMigrationError, "LOCAL_WORKSPACE_QUIESCENCE_EVIDENCE_REQUIRED"
        ):
            migrate_workspace(
                self.root,
                self.backup,
                quiescence_evidence_ref="UNKNOWN",
            )

    def test_restore_recreates_missing_files_but_never_overwrites_conflicts(self) -> None:
        migrate_workspace(
            self.root,
            self.backup,
            quiescence_evidence_ref="operator:hosts-stopped:2",
        )
        contract = self.root / ".ai/runtime/contract.md"
        contract.unlink()

        restored = restore_workspace(self.root, self.backup)

        self.assertEqual(1, restored["restored_file_count"])
        self.assertEqual("locally updated runtime\n", contract.read_text(encoding="utf-8"))

        contract.write_text("newer local value\n", encoding="utf-8")
        with self.assertRaisesRegex(
            WorkspaceMigrationError, "LOCAL_WORKSPACE_RESTORE_CONFLICT"
        ):
            restore_workspace(self.root, self.backup)

    def test_restore_rejects_another_project_without_explicit_relocation(self) -> None:
        migrate_workspace(
            self.root,
            self.backup,
            quiescence_evidence_ref="operator:hosts-stopped:3",
        )
        relocated = Path(self.temp.name) / "relocated"
        relocated.mkdir()

        with self.assertRaisesRegex(
            WorkspaceMigrationError, "BACKUP_PROJECT_ROOT_MISMATCH"
        ):
            restore_workspace(relocated, self.backup)

        restored = restore_workspace(relocated, self.backup, allow_relocated=True)
        self.assertEqual("LOCAL_WORKSPACE_RESTORED", restored["status"])
        self.assertTrue((relocated / ".ai/runtime/contract.md").is_file())


if __name__ == "__main__":
    unittest.main()
