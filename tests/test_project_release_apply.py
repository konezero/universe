from __future__ import annotations

import hashlib
import json
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
from windows_native_cli import NativeCliResult  # noqa: E402


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
        self._write(installer, "INSTALLER = True\n")
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

    def test_applies_exact_approval_through_lifecycle_host(self) -> None:
        proposal = self._proposal()
        approval = build_project_release_approval(
            project_id="demo",
            proposal=proposal,
            evidence_ref="universe://approval/demo",
        )
        observed: dict[str, Any] = {}

        def runner(request: Any) -> NativeCliResult:
            request_path = Path(request.arguments[2])
            lifecycle_request = json.loads(request_path.read_text(encoding="utf-8"))
            observed.update(lifecycle_request)
            bundle = Path(lifecycle_request["source"]["path"])
            source_bundle = json.loads(
                (bundle / "SOURCE_BUNDLE.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                "universe-release-db",
                source_bundle["source"]["provider"],
            )
            payload = {
                "result": "PASS",
                "operation": "FRESH_INSTALL",
                "user_command": "OS_INSTALL",
                "repository_runtime": "VERIFIED",
                "target": str(self.project.resolve()),
                "source": {
                    "provider": "universe-release-db",
                    "binding": "provider-attested",
                    "commit": self.commit,
                },
                "validate": {"validation_id": "validation-fixture"},
                "boot_handoff": {"status": "READY_FOR_BOOT"},
                "permit_receipt": {"receipt_id": "permit-fixture"},
            }
            return NativeCliResult(
                contract="windows-native-cli.v1",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(payload),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        receipt = apply_project_release_proposal(
            project_root=self.project,
            project_id="demo",
            proposal=proposal,
            approval=approval,
            database_path=self.database,
            manifest_path=self.manifest,
            native_runner=runner,
        )

        self.assertEqual("PROJECT_RELEASE_APPLIED", receipt["status"])
        self.assertEqual("OS_INSTALL", receipt["user_command"])
        self.assertEqual("universe-project-lifecycle-host", observed["host"])
        self.assertEqual(
            "provider-attested",
            receipt["lifecycle_result"]["source"]["binding"],
        )

    def test_direct_plan_application_returns_no_proposal_or_approval_evidence(self) -> None:
        proposal = self._proposal()

        def runner(request: Any) -> NativeCliResult:
            lifecycle_request = json.loads(
                Path(request.arguments[2]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                "universe://direct-command/project-connections/test",
                lifecycle_request["approval"]["evidence_ref"],
            )
            payload = {
                "result": "PASS",
                "operation": "FRESH_INSTALL",
                "user_command": "OS_INSTALL",
                "repository_runtime": "VERIFIED",
                "target": str(self.project.resolve()),
                "source": {
                    "provider": "universe-release-db",
                    "binding": "provider-attested",
                    "commit": self.commit,
                },
                "validate": {"validation_id": "validation-fixture"},
                "boot_handoff": {"status": "READY_FOR_BOOT"},
                "permit_receipt": {"receipt_id": "permit-fixture"},
            }
            return NativeCliResult(
                contract="windows-native-cli.v1",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(payload),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        receipt = apply_project_release_plan(
            project_root=self.project,
            project_id="demo",
            plan=proposal["plan"],
            release_database_sha256=proposal["release_database_sha256"],
            instruction_ref="universe://direct-command/project-connections/test",
            database_path=self.database,
            manifest_path=self.manifest,
            native_runner=runner,
        )

        self.assertEqual("PROJECT_RUNTIME_LIFECYCLE_APPLIED", receipt["status"])
        self.assertNotIn("proposal_id", receipt)
        self.assertNotIn("proposal_digest", receipt)
        self.assertNotIn("approval_evidence_ref", receipt)

    def test_project_state_change_rejects_stale_proposal_before_execution(self) -> None:
        proposal = self._proposal()
        approval = build_project_release_approval(
            project_id="demo",
            proposal=proposal,
            evidence_ref="universe://approval/demo",
        )
        manifest = (
            self.project
            / ".ai"
            / "runtime"
            / "project_instance"
            / "DISTRIBUTION_MANIFEST.json"
        )
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema": "ai-career.project-runtime-installation.v1",
                    "installation": {"project": "demo"},
                    "source": {"commit": self.commit},
                }
            ),
            encoding="utf-8",
        )
        update_plan = plan_project_release_lifecycle(
            project_root=self.project,
            project_id="demo",
            release_id=proposal["release_id"],
            source_commit=self.commit,
        )
        self.assertEqual("RUNTIME_UPDATE", update_plan["operation"])
        self.assertEqual("OS_UPDATE", update_plan["user_command"])
        called = False

        def runner(_: Any) -> NativeCliResult:
            nonlocal called
            called = True
            raise AssertionError("stale proposal must not execute")

        with self.assertRaisesRegex(ProjectReleaseApplyError, "state changed"):
            apply_project_release_proposal(
                project_root=self.project,
                project_id="demo",
                proposal=proposal,
                approval=approval,
                database_path=self.database,
                manifest_path=self.manifest,
                native_runner=runner,
            )
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
