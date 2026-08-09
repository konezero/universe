from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from core_release import CoreReleaseError, build_release  # noqa: E402
from release_runtime import ReleaseRuntime, ReleaseRuntimeError  # noqa: E402


class ReleaseRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.target = self.root / "target"
        self.database = self.root / "release.sqlite3"
        self.manifest = self.root / "release.manifest.json"
        self.repo.mkdir()
        self.target.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Universe Tests")
        self._git("config", "user.email", "universe-tests@example.invalid")
        self._write_source(with_profiles=True)
        self.commit = self._commit("initial")
        self._build()

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

    def _write_source(self, *, with_profiles: bool) -> None:
        base = ".ai/distribution/context_management_runtime_pack"
        index_path = f"{base}/project_runtime_source_index.json"
        manifest_path = f"{base}/project_runtime_distribution_manifest.json"
        installer_path = f"{base}/project_runtime_installer.py"
        catalog_path = f"{base}/release_profile_catalog.json"
        core_path = ".ai/core/CORE_SURFACE_REGISTRY.md"
        paths = [index_path, manifest_path, installer_path, core_path]
        source_index: dict[str, object] = {
            "schema": "ai-career.project-runtime-source-index.v1",
            "core_registry_path": core_path,
            "installer_path": installer_path,
            "package_manifest_path": manifest_path,
            "paths": paths,
        }
        if with_profiles:
            paths.append(catalog_path)
            source_index["release_profile_catalog_path"] = catalog_path
        distribution = {
            "schema": "ai-career.project-runtime-distribution.v1",
            "package": {
                "name": "fixture-runtime",
                "source_index_path": index_path,
            },
        }
        self._write(index_path, json.dumps(source_index, sort_keys=True))
        self._write(manifest_path, json.dumps(distribution, sort_keys=True))
        self._write(installer_path, "raise RuntimeError('must not execute')\n")
        self._write(core_path, "# Core v1\n")
        if with_profiles:
            self._write(
                catalog_path,
                json.dumps(
                    {
                        "schema": "ai-career.release-profile-catalog.v1",
                        "owner": "fixture/ai-career",
                        "load_profiles": [
                            {
                                "profile_id": "BOOT_CORE",
                                "description": "Boot surfaces",
                                "surfaces": [
                                    {"path": core_path, "required": True},
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
                    },
                    sort_keys=True,
                ),
            )

    def _commit(self, message: str) -> str:
        self._git("add", "--all")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _build(self) -> None:
        build_release(
            source_repo=self.repo,
            source_ref=self.commit,
            expected_commit=self.commit,
            source_repository="fixture/ai-career",
            database_path=self.database,
            manifest_path=self.manifest,
        )

    def test_resolves_skill_and_mode_profiles_in_database_order(self) -> None:
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            skill = runtime.resolve_skill("boot")
            mode = runtime.resolve_mode_profile("master_base")

        self.assertEqual("BOOT_CORE", skill["profile_id"])
        self.assertEqual(
            [
                ".ai/core/CORE_SURFACE_REGISTRY.md",
                ".ai/distribution/context_management_runtime_pack/"
                "project_runtime_installer.py",
            ],
            [surface["path"] for surface in skill["surfaces"]],
        )
        self.assertEqual(["BOOT_CORE"], mode["load_profiles"])
        self.assertEqual("APPEND_ONLY", mode["overlay_policy"])

    def test_legacy_release_keeps_governance_context_absent(self) -> None:
        selector = {
            "role": "PROJECT_MASTER",
            "mode": "MASTER",
            "operation": "DELEGATE_TO_BOSS",
            "scope": "FEATURE",
            "risk": "GUARDED",
            "capability": "TASK_FRAME",
        }
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            context = runtime.select_governance_context(selector)

        self.assertEqual("ABSENT", context["status"])
        self.assertEqual("GOVERNANCE_CATALOG_ABSENT", context["reason"])

    @staticmethod
    def _governance_runtime() -> ReleaseRuntime:
        runtime = object.__new__(ReleaseRuntime)
        runtime.metadata = {
            "release_id": "core-test",
            "source_commit": "a" * 40,
            "governance_catalog_status": "PRESENT",
            "governance_catalog_digest": "b" * 64,
        }
        runtime._connection = sqlite3.connect(":memory:")
        runtime._connection.row_factory = sqlite3.Row
        runtime._connection.executescript(
            """
            CREATE TABLE governance_index (
                role TEXT, mode TEXT, operation TEXT, scope TEXT, risk TEXT,
                capability TEXT, governance_id TEXT, required INTEGER, priority INTEGER
            );
            CREATE TABLE governance_override (
                base_governance_id TEXT, overriding_governance_id TEXT,
                applies_when_json TEXT
            );
            CREATE TABLE governance_dependency (
                governance_id TEXT, requires_governance_id TEXT
            );
            CREATE TABLE governance_unit (
                governance_id TEXT, kind TEXT, source_ref TEXT,
                source_digest TEXT, compact_instruction TEXT
            );
            CREATE TABLE release_file (path TEXT, content BLOB);
            """
        )
        return runtime

    def test_governance_context_rejects_unmatched_selector(self) -> None:
        runtime = self._governance_runtime()
        self.addCleanup(runtime.close)
        selector = {
            "role": "PROJECT_MASTER",
            "mode": "MASTER",
            "operation": "DELEGATE_TO_BOSS",
            "scope": "FEATURE",
            "risk": "GUARDED",
            "capability": "TASK_FRAME",
        }

        with self.assertRaisesRegex(ReleaseRuntimeError, "no matching units"):
            runtime.select_governance_context(selector)

    def test_governance_context_uses_canonical_selector_digest(self) -> None:
        runtime = self._governance_runtime()
        self.addCleanup(runtime.close)
        selector = {
            "role": "PROJECT_MASTER",
            "mode": "MASTER",
            "operation": "DELEGATE_TO_BOSS",
            "scope": "FEATURE",
            "risk": "GUARDED",
            "capability": "TASK_FRAME",
        }
        runtime._connection.execute(
            "INSERT INTO governance_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*selector.values(), "CORE_INVARIANTS", 1, 0),
        )
        content = b"Core contract.\n"
        source_digest = hashlib.sha256(content).hexdigest()
        runtime._connection.execute(
            "INSERT INTO governance_unit VALUES (?, ?, ?, ?, ?)",
            (
                "CORE_INVARIANTS",
                "CORE_INVARIANT",
                ".ai/core.md",
                source_digest,
                "Load core.",
            ),
        )
        runtime._connection.execute(
            "INSERT INTO release_file VALUES (?, ?)",
            (".ai/core.md", content),
        )
        context = runtime.select_governance_context(selector)
        expected = hashlib.sha256(
            json.dumps(
                {
                    "catalog_digest": "b" * 64,
                    "selector": selector,
                    "matched_entries": [
                        {
                            **selector,
                            "governance_id": "CORE_INVARIANTS",
                            "required": True,
                            "priority": 0,
                        }
                    ],
                    "dependency_closure": ["CORE_INVARIANTS"],
                    "source_digests": [source_digest],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(expected, context["selector_digest"])
        self.assertEqual("b" * 64, context["catalog_digest"])

    def test_applies_fresh_release_without_executing_packaged_installer(self) -> None:
        marker = self.root / "installer-executed"
        self.assertFalse(marker.exists())
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            plan = runtime.plan_project_install(self.target)
            result = runtime.apply_project_install(
                target_root=self.target,
                approved_plan_digest=plan["plan_digest"],
            )

        self.assertEqual("PROJECT_RELEASE_APPLIED", result["status"])
        self.assertEqual("FRESH_INSTALL", result["operation"])
        self.assertTrue(
            (
                self.target
                / ".ai"
                / "runtime"
                / "project_instance"
                / "UNIVERSE_RELEASE_INSTALL.json"
            ).is_file()
        )
        self.assertIn(
            "must not execute",
            (
                self.target
                / ".ai"
                / "distribution"
                / "context_management_runtime_pack"
                / "project_runtime_installer.py"
            ).read_text(encoding="utf-8"),
        )
        self.assertFalse(marker.exists())

    def test_update_replaces_only_untouched_managed_content(self) -> None:
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            plan = runtime.plan_project_install(self.target)
            runtime.apply_project_install(
                target_root=self.target,
                approved_plan_digest=plan["plan_digest"],
            )

        self._write(".ai/core/CORE_SURFACE_REGISTRY.md", "# Core v2\n")
        self.commit = self._commit("core-v2")
        self._build()
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            plan = runtime.plan_project_install(self.target)
            update = runtime.apply_project_install(
                target_root=self.target,
                approved_plan_digest=plan["plan_digest"],
            )

        self.assertEqual("UPDATE", update["operation"])
        self.assertEqual(
            "# Core v2\n",
            (self.target / ".ai/core/CORE_SURFACE_REGISTRY.md").read_text(
                encoding="utf-8"
            ),
        )

    def test_modified_managed_file_is_an_unmanaged_collision(self) -> None:
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            plan = runtime.plan_project_install(self.target)
            runtime.apply_project_install(
                target_root=self.target,
                approved_plan_digest=plan["plan_digest"],
            )
        (self.target / ".ai/core/CORE_SURFACE_REGISTRY.md").write_text(
            "project-owned edit\n",
            encoding="utf-8",
        )
        before_apply = {
            path.relative_to(self.target).as_posix(): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }

        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            plan = runtime.plan_project_install(self.target)
            self.assertEqual("PROJECT_RELEASE_PLAN_BLOCKED", plan["status"])
            with self.assertRaisesRegex(
                ReleaseRuntimeError,
                "unmanaged collisions",
            ):
                runtime.apply_project_install(
                    target_root=self.target,
                    approved_plan_digest=plan["plan_digest"],
                )
        after_apply = {
            path.relative_to(self.target).as_posix(): path.read_bytes()
            for path in self.target.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before_apply, after_apply)

    def test_stale_plan_digest_is_rejected_before_writes(self) -> None:
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            with self.assertRaisesRegex(ReleaseRuntimeError, "stale"):
                runtime.apply_project_install(
                    target_root=self.target,
                    approved_plan_digest="0" * 64,
                )
        self.assertEqual([], list(self.target.iterdir()))

    def test_tampered_release_is_rejected_before_project_mutation(self) -> None:
        tampered_database = self.root / "tampered-release.sqlite3"
        tampered_manifest = self.root / "tampered-release.manifest.json"
        tampered_database.write_bytes(self.database.read_bytes())
        tampered_manifest.write_bytes(self.manifest.read_bytes())
        with tampered_database.open("ab") as stream:
            stream.write(b"tampered")

        with self.assertRaisesRegex(CoreReleaseError, "database digest"):
            ReleaseRuntime(
                database_path=tampered_database,
                manifest_path=tampered_manifest,
            )
        self.assertEqual([], list(self.target.iterdir()))

    def test_materializes_provider_attested_source_bundle(self) -> None:
        bundle = self.root / "bundle"
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            evidence = runtime.materialize_source_bundle(bundle)
            expected_files = list(runtime.iter_release_files())

        manifest_path = bundle / "SOURCE_BUNDLE.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("universe-release-db", evidence["provider"])
        self.assertEqual("provider-attested", evidence["binding"])
        self.assertEqual(self.commit, evidence["source_commit"])
        self.assertEqual(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            evidence["bundle_manifest_sha256"],
        )
        self.assertEqual(
            [item.path for item in expected_files],
            [item["path"] for item in manifest["files"]],
        )
        for source, entry in zip(expected_files, manifest["files"], strict=True):
            self.assertEqual(source.git_object_id, entry["blob_oid"])
            self.assertEqual(
                source.content,
                (bundle / "objects" / "sha256" / entry["sha256"]).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
