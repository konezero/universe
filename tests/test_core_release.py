from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from core_release import (  # noqa: E402
    CoreReleaseError,
    build_release,
    verify_release,
)
from release_profile_catalog import (  # noqa: E402
    ReleaseProfileError,
    parse_release_governance_catalog,
    select_governance,
)


class CoreReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        git = shutil.which("git.exe") or shutil.which("git")
        self.assertIsNotNone(git)
        self.host_tool_patcher = patch(
            "core_release.resolve_host_tool",
            return_value=SimpleNamespace(executable=Path(str(git)).resolve()),
        )
        self.host_tool_patcher.start()
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
        self.host_tool_patcher.stop()
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

    def _write_fixture(
        self,
        extra_paths: list[str] | None = None,
        *,
        with_profiles: bool = False,
        with_governance: bool = False,
        profile_surface: str | None = None,
    ) -> None:
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
        catalog_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "release_profile_catalog.json"
        )
        governance_paths = [
            ".ai/governance/CORE.md",
            ".ai/governance/PROJECT.md",
            ".ai/governance/OVERRIDE.md",
        ]
        if with_governance:
            paths.extend(governance_paths)
        if with_profiles or with_governance:
            paths.append(catalog_path)
        source_index = {
            "schema": "ai-career.project-runtime-source-index.v1",
            "core_registry_path": core_path,
            "installer_path": installer_path,
            "package_manifest_path": manifest_path,
            "paths": paths,
        }
        if with_profiles or with_governance:
            source_index["release_profile_catalog_path"] = catalog_path
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
        if with_profiles or with_governance:
            catalog = {
                "schema": "ai-career.release-profile-catalog.v1",
                "owner": "fixture/ai-career",
                "load_profiles": [
                    {
                        "profile_id": "BOOT_CORE",
                        "description": "Ordered Boot control surfaces.",
                        "surfaces": [
                            {
                                "path": profile_surface or core_path,
                                "required": True,
                            },
                            {"path": installer_path, "required": False},
                        ],
                    }
                ],
                "skill_bindings": [
                    {"skill_id": "boot", "profile_id": "BOOT_CORE"}
                ],
                "mode_profiles": [
                    {
                        "mode_profile_id": "MASTER_BASE",
                        "overlay_policy": "APPEND_ONLY",
                        "load_profiles": ["BOOT_CORE"],
                    }
                ],
            }
            if with_governance:
                for path, content in {
                    governance_paths[0]: "Core governance instructions.\n",
                    governance_paths[1]: "Project governance instructions.\n",
                    governance_paths[2]: "Override governance instructions.\n",
                }.items():
                    self._write(path, content)
                selector = {
                    "role": "PROJECT_MASTER",
                    "mode": "MASTER",
                    "operation": "DELEGATE_TO_BOSS",
                    "scope": "FEATURE",
                    "risk": "GUARDED",
                    "capability": "TASK_FRAME",
                }
                catalog.update(
                    {
                        "schema": "ai-career.release-profile-catalog.v2",
                        "governance_units": [
                            {
                                "governance_id": "CORE",
                                "kind": "CORE",
                                "source_ref": governance_paths[0],
                                "source_digest": hashlib.sha256(
                                    b"Core governance instructions.\n"
                                ).hexdigest(),
                                "compact_instruction": "Load core governance.",
                            },
                            {
                                "governance_id": "PROJECT",
                                "kind": "PROJECT",
                                "source_ref": governance_paths[1],
                                "source_digest": hashlib.sha256(
                                    b"Project governance instructions.\n"
                                ).hexdigest(),
                                "compact_instruction": "Load project governance.",
                            },
                            {
                                "governance_id": "OVERRIDE",
                                "kind": "OVERRIDE",
                                "source_ref": governance_paths[2],
                                "source_digest": hashlib.sha256(
                                    b"Override governance instructions.\n"
                                ).hexdigest(),
                                "compact_instruction": "Load the override.",
                            },
                        ],
                        "governance_index": [
                            {
                                **selector,
                                "governance_id": "CORE",
                                "required": True,
                                "priority": 0,
                            },
                            {
                                **selector,
                                "governance_id": "PROJECT",
                                "required": False,
                                "priority": 10,
                            },
                        ],
                        "governance_dependencies": [
                            {
                                "governance_id": "PROJECT",
                                "requires_governance_id": "CORE",
                            },
                            {
                                "governance_id": "OVERRIDE",
                                "requires_governance_id": "CORE",
                            },
                        ],
                        "governance_overrides": [
                            {
                                "base_governance_id": "PROJECT",
                                "overriding_governance_id": "OVERRIDE",
                                "applies_when": selector,
                            }
                        ],
                    }
                )
            self._write(catalog_path, json.dumps(catalog, sort_keys=True))

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
        self.assertEqual("ABSENT", verified["profile_catalog"]["status"])
        self.assertNotIn("governance_catalog", verified)

        connection = sqlite3.connect(self.database)
        try:
            installer = connection.execute(
                "SELECT content FROM release_file WHERE path LIKE '%installer.py'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn(b"must not execute", installer)

        connection = sqlite3.connect(self.database)
        try:
            governance_rows = connection.execute(
                "SELECT COUNT(*) FROM governance_unit"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(0, governance_rows)

    def test_builds_logical_package_from_nested_git_tree(self) -> None:
        source_tree_root = "core/runtime-source"
        nested_root = self.repo / source_tree_root
        nested_root.mkdir(parents=True)
        (self.repo / ".ai").rename(nested_root / ".ai")
        self.commit = self._commit("nest-runtime-source")

        manifest = build_release(
            source_repo=self.repo,
            source_ref=self.commit,
            expected_commit=self.commit,
            source_repository="fixture/ai-career",
            source_tree_root=source_tree_root,
            database_path=self.database,
            manifest_path=self.manifest,
        )
        verified = verify_release(
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual(source_tree_root, manifest["source_tree_root"])
        self.assertEqual("CORE_RELEASE_VERIFIED", verified["status"])
        connection = sqlite3.connect(self.database)
        try:
            paths = {
                row[0]
                for row in connection.execute("SELECT path FROM release_file")
            }
        finally:
            connection.close()
        self.assertIn(".ai/core/CORE_SURFACE_REGISTRY.md", paths)
        self.assertNotIn(
            "core/runtime-source/.ai/core/CORE_SURFACE_REGISTRY.md",
            paths,
        )

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

    def test_legacy_core_release_v2_without_governance_tables_still_verifies(
        self,
    ) -> None:
        manifest = self._build()
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                for table in (
                    "governance_override",
                    "governance_dependency",
                    "governance_index",
                    "governance_unit",
                ):
                    connection.execute(f"DROP TABLE {table}")
        finally:
            connection.close()
        manifest["database_sha256"] = hashlib.sha256(
            self.database.read_bytes()
        ).hexdigest()
        self.manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        verified = verify_release(
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual("CORE_RELEASE_VERIFIED", verified["status"])
        self.assertNotIn("governance_catalog", verified)

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

    def test_profile_catalog_is_normalized_into_ordered_release_tables(self) -> None:
        self._write_fixture(with_profiles=True)
        self.commit = self._commit("profile-catalog")

        manifest = self._build()
        verified = verify_release(
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual("PRESENT", verified["profile_catalog"]["status"])
        self.assertEqual(1, verified["profile_catalog"]["load_profile_count"])
        self.assertEqual(
            verified["profile_catalog"],
            manifest["profile_catalog"],
        )
        connection = sqlite3.connect(self.database)
        try:
            surfaces = connection.execute(
                """
                SELECT ordinal, path, required
                FROM load_profile_surface
                WHERE profile_id = 'BOOT_CORE'
                ORDER BY ordinal
                """
            ).fetchall()
            skill = connection.execute(
                """
                SELECT profile_id
                FROM skill_profile_binding
                WHERE skill_id = 'boot'
                """
            ).fetchone()
            mode = connection.execute(
                """
                SELECT profile_id
                FROM mode_profile_load
                WHERE mode_profile_id = 'MASTER_BASE'
                ORDER BY ordinal
                """
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [
                (0, ".ai/core/CORE_SURFACE_REGISTRY.md", 1),
                (
                    1,
                    ".ai/distribution/context_management_runtime_pack/"
                    "project_runtime_installer.py",
                    0,
                ),
            ],
            surfaces,
        )
        self.assertEqual(("BOOT_CORE",), skill)
        self.assertEqual([("BOOT_CORE",)], mode)

    def test_profile_catalog_digest_is_independent_of_top_level_input_order(
        self,
    ) -> None:
        self._write_fixture(with_profiles=True)
        catalog_path = (
            self.repo
            / ".ai"
            / "distribution"
            / "context_management_runtime_pack"
            / "release_profile_catalog.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["load_profiles"].append(
            {
                "profile_id": "AUX_CORE",
                "description": "Auxiliary surfaces.",
                "surfaces": [
                    {
                        "path": ".ai/core/CORE_SURFACE_REGISTRY.md",
                        "required": True,
                    }
                ],
            }
        )
        catalog["skill_bindings"].append(
            {"skill_id": "auxiliary", "profile_id": "AUX_CORE"}
        )
        catalog["mode_profiles"].append(
            {
                "mode_profile_id": "AUX_MODE",
                "overlay_policy": "NONE",
                "load_profiles": ["AUX_CORE"],
            }
        )
        catalog_path.write_text(
            json.dumps(catalog, indent=2) + "\n",
            encoding="utf-8",
        )
        self.commit = self._commit("multi-profile-catalog")

        manifest = self._build()
        verified = verify_release(
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual("PRESENT", verified["profile_catalog"]["status"])
        self.assertEqual(2, verified["profile_catalog"]["load_profile_count"])
        self.assertEqual(
            manifest["profile_catalog"]["catalog_digest"],
            verified["profile_catalog"]["catalog_digest"],
        )

    def test_profile_catalog_cannot_reference_an_unpacked_surface(self) -> None:
        self._write_fixture(
            with_profiles=True,
            profile_surface=".ai/core/NOT_PACKAGED.md",
        )
        self.commit = self._commit("invalid-profile-catalog")

        with self.assertRaisesRegex(CoreReleaseError, "not packaged"):
            self._build()

    def test_governance_catalog_validation_rejects_bad_references_and_cycles(
        self,
    ) -> None:
        self._write_fixture(with_governance=True)
        catalog_path = (
            self.repo
            / ".ai"
            / "distribution"
            / "context_management_runtime_pack"
            / "release_profile_catalog.json"
        )
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        source_index_path = (
            self.repo
            / ".ai"
            / "distribution"
            / "context_management_runtime_pack"
            / "project_runtime_source_index.json"
        )
        source_index = json.loads(source_index_path.read_text(encoding="utf-8"))
        paths = source_index["paths"]

        invalid_path = json.loads(json.dumps(catalog))
        invalid_path["governance_units"][0]["source_ref"] = ".ai/MISSING.md"
        with self.assertRaisesRegex(ReleaseProfileError, "not packaged"):
            parse_release_governance_catalog(
                invalid_path,
                packaged_paths=paths,
            )

        cyclic = json.loads(json.dumps(catalog))
        cyclic["governance_dependencies"].append(
            {
                "governance_id": "CORE",
                "requires_governance_id": "OVERRIDE",
            }
        )
        with self.assertRaisesRegex(ReleaseProfileError, "cycle"):
            parse_release_governance_catalog(
                cyclic,
                packaged_paths=paths,
            )

        duplicate = json.loads(json.dumps(catalog))
        duplicate["governance_index"].append(
            duplicate["governance_index"][0]
        )
        with self.assertRaisesRegex(ReleaseProfileError, "duplicate"):
            parse_release_governance_catalog(
                duplicate,
                packaged_paths=paths,
            )

    def test_governance_selector_returns_ordered_closure_and_stable_digest(
        self,
    ) -> None:
        self._write_fixture(with_governance=True)
        catalog_path = (
            self.repo
            / ".ai"
            / "distribution"
            / "context_management_runtime_pack"
            / "release_profile_catalog.json"
        )
        catalog_value = json.loads(catalog_path.read_text(encoding="utf-8"))
        source_index_path = (
            self.repo
            / ".ai"
            / "distribution"
            / "context_management_runtime_pack"
            / "project_runtime_source_index.json"
        )
        paths = json.loads(
            source_index_path.read_text(encoding="utf-8")
        )["paths"]
        catalog = parse_release_governance_catalog(
            catalog_value,
            packaged_paths=paths,
        )
        self.assertEqual(["BOOT_CORE"], [
            profile.profile_id for profile in catalog.load_profiles
        ])
        self.assertEqual([("boot", "BOOT_CORE")], [
            (binding.skill_id, binding.profile_id)
            for binding in catalog.skill_bindings
        ])
        self.assertEqual(["MASTER_BASE"], [
            profile.mode_profile_id for profile in catalog.mode_profiles
        ])
        self.assertEqual(
            {
                "schema",
                "owner",
                "load_profiles",
                "skill_bindings",
                "mode_profiles",
                "governance_units",
                "governance_index",
                "governance_dependencies",
                "governance_overrides",
            },
            set(catalog.as_dict()),
        )
        selector = {
            "capability": "TASK_FRAME",
            "risk": "GUARDED",
            "scope": "FEATURE",
            "operation": "DELEGATE_TO_BOSS",
            "mode": "MASTER",
            "role": "PROJECT_MASTER",
        }
        selection = select_governance(catalog, selector)
        repeat = select_governance(catalog, dict(reversed(list(selector.items()))))

        self.assertEqual(("CORE", "OVERRIDE"), selection.dependency_closure)
        self.assertEqual(selection.selector_digest, repeat.selector_digest)
        self.assertEqual(selection.as_dict(), repeat.as_dict())
        self.assertEqual(["CORE", "OVERRIDE"], [
            unit.governance_id for unit in selection.units
        ])
        with self.assertRaisesRegex(ReleaseProfileError, "requires"):
            select_governance(
                json.loads(json.dumps(catalog_value)),
                selector,
            )

    def test_governance_catalog_builds_and_verifies_release_database(self) -> None:
        self._write_fixture(with_governance=True)
        self.commit = self._commit("governance-catalog")

        manifest = self._build()
        verified = verify_release(
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual("PRESENT", manifest["governance_catalog"]["status"])
        self.assertEqual("PRESENT", manifest["profile_catalog"]["status"])
        self.assertEqual(1, manifest["profile_catalog"]["load_profile_count"])
        self.assertEqual(1, manifest["profile_catalog"]["skill_binding_count"])
        self.assertEqual(1, manifest["profile_catalog"]["mode_profile_count"])
        self.assertEqual(
            manifest["governance_catalog"],
            verified["governance_catalog"],
        )
        self.assertEqual(
            manifest["profile_catalog"],
            verified["profile_catalog"],
        )
        self.assertEqual(3, verified["governance_catalog"]["governance_unit_count"])
        self.assertEqual(2, verified["governance_catalog"]["governance_index_count"])
        self.assertEqual(
            2,
            verified["governance_catalog"]["governance_dependency_count"],
        )
        self.assertEqual(1, verified["governance_catalog"]["governance_override_count"])

        connection = sqlite3.connect(self.database)
        try:
            units = connection.execute(
                """
                SELECT governance_id, release_id
                FROM governance_unit
                ORDER BY governance_id
                """
            ).fetchall()
            tables = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'governance_%'
                    """
                )
            }
            profiles = connection.execute(
                "SELECT profile_id FROM load_profile ORDER BY profile_id"
            ).fetchall()
            skills = connection.execute(
                """
                SELECT skill_id, profile_id
                FROM skill_profile_binding
                ORDER BY skill_id
                """
            ).fetchall()
            modes = connection.execute(
                "SELECT mode_profile_id FROM mode_profile ORDER BY mode_profile_id"
            ).fetchall()
            mode_loads = connection.execute(
                """
                SELECT mode_profile_id, profile_id
                FROM mode_profile_load
                ORDER BY mode_profile_id, ordinal
                """
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(
            [("CORE", manifest["release_id"]),
             ("OVERRIDE", manifest["release_id"]),
             ("PROJECT", manifest["release_id"])],
            units,
        )
        self.assertEqual(
            {
                "governance_dependency",
                "governance_index",
                "governance_override",
                "governance_unit",
            },
            tables,
        )
        self.assertEqual([("BOOT_CORE",)], profiles)
        self.assertEqual([("boot", "BOOT_CORE")], skills)
        self.assertEqual([("MASTER_BASE",)], modes)
        self.assertEqual([("MASTER_BASE", "BOOT_CORE")], mode_loads)


if __name__ == "__main__":
    unittest.main()
