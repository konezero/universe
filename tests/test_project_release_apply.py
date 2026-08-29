from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from core_release import build_release  # noqa: E402
from project_release_apply import (  # noqa: E402
    ProjectReleaseApplyError,
    apply_project_release_plan,
    apply_project_release_proposal,
    build_project_release_approval,
    plan_project_release_lifecycle,
)
from release_runtime import ReleaseRuntime  # noqa: E402


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class ProjectReleaseApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.source = self.root / "source"
        self.database = self.root / "release.sqlite3"
        self.manifest = self.root / "release.manifest.json"
        self.project.mkdir()
        self.source.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Universe Tests")
        self._git("config", "user.email", "universe-tests@example.invalid")
        self._write_release_source()
        self._git("add", "--all")
        self._git("commit", "-q", "-m", "fixture")
        self.commit = self._git("rev-parse", "HEAD")
        build_release(
            source_repo=self.source,
            source_ref=self.commit,
            expected_commit=self.commit,
            source_repository="fixture/ai-career",
            database_path=self.database,
            manifest_path=self.manifest,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.source), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def _write(self, relative: str, content: str) -> None:
        target = self.source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _write_release_source(self) -> None:
        base = ".ai/distribution/context_management_runtime_pack"
        source_index = f"{base}/project_runtime_source_index.json"
        distribution = f"{base}/project_runtime_distribution_manifest.json"
        installer = f"{base}/project_runtime_installer.py"
        host = f"{base}/host_fresh_install.py"
        catalog = f"{base}/release_profile_catalog.json"
        core = ".ai/core/CORE_SURFACE_REGISTRY.md"
        paths = [source_index, distribution, installer, host, catalog, core]
        self._write(
            source_index,
            json.dumps(
                {
                    "schema": "ai-career.project-runtime-source-index.v1",
                    "core_registry_path": core,
                    "installer_path": installer,
                    "package_manifest_path": distribution,
                    "release_profile_catalog_path": catalog,
                    "paths": paths,
                },
                sort_keys=True,
            ),
        )
        self._write(
            distribution,
            json.dumps(
                {
                    "schema": "ai-career.project-runtime-distribution.v1",
                    "package": {
                        "name": "fixture-runtime",
                        "source_index_path": source_index,
                    },
                },
                sort_keys=True,
            ),
        )
        self._write(
            installer,
            "raise SystemExit(\"release database installation must not invoke runtime installer\")\n",
        )
        self._write(host, "HOST = True\n")
        self._write(core, "# Core\n")
        self._write(
            catalog,
            json.dumps(
                {
                    "schema": "ai-career.release-profile-catalog.v1",
                    "owner": "fixture/ai-career",
                    "load_profiles": [
                        {
                            "profile_id": "BOOT_CORE",
                            "description": "Boot surfaces",
                            "surfaces": [{"path": core, "required": True}],
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

    def _proposal(self) -> dict[str, Any]:
        with ReleaseRuntime(
            database_path=self.database,
            manifest_path=self.manifest,
        ) as runtime:
            plan = plan_project_release_lifecycle(
                project_root=self.project,
                project_id="demo",
                release_id=runtime.release_id,
                source_commit=self.commit,
            )
            material = {
                "schema": "universe.project-release-proposal.v1",
                "project_id": "demo",
                "release_id": runtime.release_id,
                "mode": "MASTER",
                "release_database_sha256": runtime.verification["database_sha256"],
                "plan": plan,
                "approval": "REQUIRED",
                "execution_owner": "PROJECT_HOST",
                "effects": {"project_write": "NONE", "files_changed": 0},
                "next_operation": "USER_APPROVAL_AND_PROJECT_HOST_APPLY",
            }
        proposal_digest = digest(material)
        return {
            **material,
            "proposal_digest": proposal_digest,
            "proposal_id": "release_proposal_" + proposal_digest[:20],
            "status": "PROJECT_RELEASE_PROPOSAL_READY",
        }

    def test_fresh_project_plan_uses_os_install(self) -> None:
        proposal = self._proposal()
        self.assertEqual("FRESH_INSTALL", proposal["plan"]["operation"])
        self.assertEqual("OS_INSTALL", proposal["plan"]["user_command"])
        self.assertEqual("ABSENT", proposal["plan"]["installed_runtime"]["state"])

    def test_fresh_install_writes_release_files(self) -> None:
        proposal = self._proposal()
        approval = build_project_release_approval(
            project_id="demo",
            proposal=proposal,
            evidence_ref="universe://approval/demo",
        )

        receipt = apply_project_release_proposal(
            project_root=self.project,
            project_id="demo",
            proposal=proposal,
            approval=approval,
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual("PROJECT_RELEASE_APPLIED", receipt["status"])
        self.assertEqual("FRESH_INSTALL", receipt["operation"])
        self.assertIn("changed_count", receipt)
        self.assertGreater(receipt["changed_count"], 0)
        self.assertEqual(
            "PROJECT_INDEX_RELEASE_EVENT_DEFERRED",
            receipt["project_index"]["status"],
        )
        state_file = (
            self.project
            / ".ai"
            / "runtime"
            / "project_instance"
            / "UNIVERSE_RELEASE_INSTALL.json"
        )
        self.assertTrue(state_file.exists())
        state = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(proposal["release_id"], state["release_id"])
        core_file = self.project / ".ai" / "core" / "CORE_SURFACE_REGISTRY.md"
        self.assertTrue(core_file.exists())
        self.assertFalse((self.project / ".ai" / "START_HERE.md").exists())
        self.assertNotIn("runtime_surface_result", receipt)

    def test_direct_plan_application_returns_no_proposal_or_approval_evidence(self) -> None:
        proposal = self._proposal()

        receipt = apply_project_release_plan(
            project_root=self.project,
            project_id="demo",
            plan=proposal["plan"],
            release_database_sha256=proposal["release_database_sha256"],
            instruction_ref="universe://direct-command/project-connections/test",
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual("PROJECT_RUNTIME_LIFECYCLE_APPLIED", receipt["status"])
        self.assertEqual(
            "universe://direct-command/project-connections/test",
            receipt["instruction_ref"],
        )
        self.assertNotIn("proposal_id", receipt)
        self.assertNotIn("proposal_digest", receipt)
        self.assertNotIn("approval_evidence_ref", receipt)

    def test_release_event_indexes_changed_ai_text_when_anchor_exists(self) -> None:
        state = self.project / ".ai" / "runtime" / "state"
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
        proposal = self._proposal()
        approval = build_project_release_approval(
            project_id="demo",
            proposal=proposal,
            evidence_ref="universe://approval/demo",
        )

        receipt = apply_project_release_proposal(
            project_root=self.project,
            project_id="demo",
            proposal=proposal,
            approval=approval,
            database_path=self.database,
            manifest_path=self.manifest,
        )

        self.assertEqual(
            "PROJECT_INDEX_RELEASE_EVENT_SYNCED",
            receipt["project_index"]["status"],
        )
        index = sqlite3.connect(
            self.project / ".ai" / "runtime" / "state" / "project_file_index.sqlite3"
        )
        try:
            row = index.execute(
                "SELECT text_excerpt FROM project_file_index WHERE relative_path = ?",
                (".ai/core/CORE_SURFACE_REGISTRY.md",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("# Core", row[0])
        finally:
            index.close()

    def test_collision_blocks_apply_for_unmanaged_modified_file(self) -> None:
        proposal = self._proposal()
        approval = build_project_release_approval(
            project_id="demo",
            proposal=proposal,
            evidence_ref="universe://approval/demo",
        )
        core_file = self.project / ".ai" / "core" / "CORE_SURFACE_REGISTRY.md"
        core_file.parent.mkdir(parents=True)
        core_file.write_text("# UNMANAGED DIFFERENT CONTENT\n", encoding="utf-8")

        with self.assertRaises(ProjectReleaseApplyError) as ctx:
            apply_project_release_proposal(
                project_root=self.project,
                project_id="demo",
                proposal=proposal,
                approval=approval,
                database_path=self.database,
                manifest_path=self.manifest,
            )
        self.assertEqual("UNMANAGED_TARGET_COLLISION", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
