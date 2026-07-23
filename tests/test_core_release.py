from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from core_release import (  # noqa: E402
    CoreReleaseError,
    build_release,
    verify_release,
)


class CoreReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.database = self.root / "release.sqlite3"
        self.manifest = self.root / "release.manifest.json"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Universe Tests")
        self._git("config", "user.email", "universe-tests@example.invalid")
        self._write_fixture()
        self.commit = self._commit("initial")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if completed.returncode != 0:
            self.fail(completed.stderr)
        return completed.stdout.strip()

    def _write(self, relative: str, content: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_fixture(self, extra_paths: list[str] | None = None) -> None:
        source_index_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "project_runtime_source_index.json"
        )
        manifest_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "project_runtime_distribution_manifest.json"
        )
        installer_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "project_runtime_installer.py"
        )
        core_path = ".ai/core/CORE_SURFACE_REGISTRY.md"
        paths = [source_index_path, manifest_path, installer_path, core_path]
        paths.extend(extra_paths or [])
        source_index = {
            "schema": "ai-career.project-runtime-source-index.v1",
            "core_registry_path": core_path,
            "installer_path": installer_path,
            "package_manifest_path": manifest_path,
            "paths": paths,
        }
        distribution = {
            "schema": "ai-career.project-runtime-distribution.v1",
            "package": {
                "name": "fixture-runtime",
                "source_index_path": source_index_path,
            },
        }
        self._write(source_index_path, json.dumps(source_index, sort_keys=True))
        self._write(manifest_path, json.dumps(distribution, sort_keys=True))
        self._write(installer_path, "raise RuntimeError('must not execute')\n")
        self._write(core_path, "# Fixture Core Registry\n")

    def _commit(self, message: str) -> str:
        self._git("add", "--all")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _build(self) -> dict[str, object]:
        return build_release(
            source_repo=self.repo,
            source_ref=self.commit,
            expected_commit=self.commit,
            source_repository="fixture/ai-career",
            database_path=self.database,
            manifest_path=self.manifest,
        )

    def test_builds_and_verifies_git_objects_without_candidate_execution(self) -> None:
        manifest = self._build()
        verified = verify_release(
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual("CORE_RELEASE_VERIFIED", verified["status"])
        self.assertEqual(self.commit, verified["source_commit"])
        self.assertEqual(4, verified["file_count"])
        self.assertEqual("FORBIDDEN", verified["candidate_execution"])
        self.assertEqual(self.commit, manifest["source_commit"])

        connection = sqlite3.connect(self.database)
        try:
            installer = connection.execute(
                "SELECT content FROM release_file WHERE path LIKE '%installer.py'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn(b"must not execute", installer)

    def test_database_tampering_is_rejected(self) -> None:
        self._build()
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                connection.execute(
                    "UPDATE release_file SET content = ? WHERE path LIKE '%installer.py'",
                    (b"tampered",),
                )
        finally:
            connection.close()

        with self.assertRaisesRegex(CoreReleaseError, "database digest"):
            verify_release(
                database_path=self.database,
                manifest_path=self.manifest,
            )

    def test_source_index_path_escape_is_rejected(self) -> None:
        self._write_fixture(extra_paths=["../outside.txt"])
        self.commit = self._commit("malicious-index")

        with self.assertRaisesRegex(CoreReleaseError, "escapes"):
            self._build()

    def test_expected_commit_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(CoreReleaseError, "expected_commit"):
            build_release(
                source_repo=self.repo,
                source_ref=self.commit,
                expected_commit="0" * 40,
                source_repository="fixture/ai-career",
                database_path=self.database,
                manifest_path=self.manifest,
            )


if __name__ == "__main__":
    unittest.main()
