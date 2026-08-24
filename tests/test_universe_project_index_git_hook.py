from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_file_index import open_project_index_readonly, search_index  # noqa: E402
from universe_project_index_git_hook import (  # noqa: E402
    HOOK_EVENTS,
    HOOK_MARKER,
    install_git_hooks,
    run_git_event,
)


class UniverseProjectIndexGitHookTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def _runtime_anchor(self, root: Path) -> None:
        state = root / ".ai" / "runtime" / "state"
        state.mkdir(parents=True)
        connection = sqlite3.connect(state / "project_runtime.sqlite3")
        connection.execute(
            "CREATE TABLE mode_current_anchor (mode TEXT PRIMARY KEY, frame_id TEXT, anchor_id TEXT, state TEXT)"
        )
        connection.execute(
            "INSERT INTO mode_current_anchor VALUES ('MASTER', 'current', 'MASTER-CURRENT-1', 'CURRENT')"
        )
        connection.commit()
        connection.close()

    def test_installs_idempotent_hooks_without_overwriting_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init", "-q")
            script = ROOT / "tools" / "universe_project_index_git_hook.py"
            first = install_git_hooks(
                project_id="demo",
                project_root=root,
                python_exe=Path(sys.executable),
                script_path=script,
            )
            self.assertEqual(
                ["WRITTEN"] * len(HOOK_EVENTS),
                [item["status"] for item in first["hooks"]],
            )
            for event in HOOK_EVENTS:
                body = (root / ".git" / "hooks" / event).read_text(encoding="utf-8")
                self.assertIn(HOOK_MARKER, body)
                self.assertIn(">/dev/null", body)
            again = install_git_hooks(
                project_id="demo",
                project_root=root,
                python_exe=Path(sys.executable),
                script_path=script,
            )
            self.assertEqual(
                ["CURRENT"] * len(HOOK_EVENTS),
                [item["status"] for item in again["hooks"]],
            )

            conflict = root / ".git" / "hooks" / "post-commit"
            conflict.write_text("#!/bin/sh\necho existing\n", encoding="utf-8")
            conflicted = install_git_hooks(
                project_id="demo",
                project_root=root,
                python_exe=Path(sys.executable),
                script_path=script,
            )
            self.assertEqual("CONFLICT", conflicted["hooks"][0]["status"])
            self.assertEqual("#!/bin/sh\necho existing\n", conflict.read_text(encoding="utf-8"))

    def test_post_commit_bootstrap_and_incremental_ai_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init", "-q")
            self._git(root, "config", "user.name", "Universe Tests")
            self._git(root, "config", "user.email", "universe@example.invalid")
            self._runtime_anchor(root)
            source = root / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("BOOTSTRAP_TOKEN = True\n", encoding="utf-8")
            self._git(root, "add", "src/app.py")
            self._git(root, "commit", "-q", "-m", "source")

            first = run_git_event(
                project_id="demo",
                project_root=root,
                mode="MASTER",
                event="post-commit",
            )
            self.assertTrue(first["bootstrap"])

            skill = root / ".ai" / "skills" / "demo" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("AI_DIRECTORY_TOKEN\n", encoding="utf-8")
            self._git(root, "add", ".ai/skills/demo/SKILL.md")
            self._git(root, "commit", "-q", "-m", "skill")
            second = run_git_event(
                project_id="demo",
                project_root=root,
                mode="MASTER",
                event="post-commit",
            )
            self.assertFalse(second["bootstrap"])
            self.assertEqual([".ai/skills/demo/SKILL.md"], second["changed_paths"])

            reader = open_project_index_readonly(project_id="demo", project_root=root)
            try:
                found = search_index(reader, project_id="demo", query="AI_DIRECTORY_TOKEN")
                self.assertEqual(1, found["hit_count"])
                self.assertEqual(
                    ".ai/skills/demo/SKILL.md", found["hits"][0]["relative_path"]
                )
                raw_db = search_index(reader, project_id="demo", query="project_runtime.sqlite3")
                self.assertEqual(0, raw_db["hit_count"])
            finally:
                reader.close()


if __name__ == "__main__":
    unittest.main()
