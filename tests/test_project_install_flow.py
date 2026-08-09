from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_install_flow import (  # noqa: E402
    PROJECT_STANDALONE,
    UNIVERSE_ATTACHED,
    ProjectInstallFlowError,
    apply_project_install_flow,
    plan_project_install_flow,
    preflight_project_install_flow,
)


class ProjectInstallFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "fresh-clone"
        self.project.mkdir()
        self.commit = "a" * 40
        (self.project / "README.md").write_text("local project\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_read_only_fresh_clone_plan_does_not_create_ai(self) -> None:
        plan = preflight_project_install_flow(
            project_root=self.project,
            project_id="demo",
            source_commit=self.commit,
        )
        self.assertEqual("PROJECT_INSTALL_PLAN_READY", plan["status"])
        self.assertEqual("PLAN_READY", plan["state"])
        self.assertEqual("OS_INSTALL", plan["operation"])
        self.assertEqual(UNIVERSE_ATTACHED, plan["install_mode"])
        self.assertFalse((self.project / ".ai").exists())
        self.assertTrue(plan["read_only_preflight"])
        self.assertEqual("FORBIDDEN", plan["candidate_execution"])

    def test_attached_apply_delegates_file_creation_and_verifies_ready(self) -> None:
        plan = plan_project_install_flow(
            project_root=self.project,
            project_id="demo",
            install_mode=UNIVERSE_ATTACHED,
            source_commit=self.commit,
        )
        observed: dict[str, Any] = {}

        def adapter(request: dict[str, Any]) -> dict[str, Any]:
            observed.update(request)
            self._materialize_runtime()
            return self._adapter_result(request)

        receipt = apply_project_install_flow(
            plan=plan,
            approved_plan_digest=plan["plan_digest"],
            lifecycle_adapter=adapter,
        )
        self.assertEqual("PROJECT_INSTALL_READY_FOR_BOOT", receipt["status"])
        self.assertEqual("READY_FOR_BOOT", receipt["state"])
        self.assertEqual(UNIVERSE_ATTACHED, observed["install_mode"])
        self.assertEqual(self.commit, receipt["source_commit"])
        self.assertEqual("local project\n", (self.project / "README.md").read_text())

    def test_standalone_plan_is_explicit_and_host_preference_is_configurable(self) -> None:
        plan = plan_project_install_flow(
            project_root=self.project,
            project_id="demo",
            install_mode=PROJECT_STANDALONE,
            prefer_boot="STANDALONE",
            source_commit=self.commit,
        )
        self.assertEqual(PROJECT_STANDALONE, plan["install_mode"])
        self.assertEqual("STANDALONE", plan["prefer_boot"])
        self.assertEqual("immutable-commit", plan["source"]["binding"])

    def test_false_ready_without_artifacts_is_rejected(self) -> None:
        plan = plan_project_install_flow(
            project_root=self.project,
            project_id="demo",
            source_commit=self.commit,
        )

        def false_ready(request: dict[str, Any]) -> dict[str, Any]:
            return {**self._adapter_result(request), "managed_paths": []}

        with self.assertRaises(ProjectInstallFlowError) as context:
            apply_project_install_flow(
                plan=plan,
                approved_plan_digest=plan["plan_digest"],
                lifecycle_adapter=false_ready,
            )
        self.assertEqual("PROJECT_INSTALL_ARTIFACTS_MISSING", context.exception.code)
        self.assertFalse((self.project / ".ai").exists())

    def test_false_ready_with_wrong_live_commit_is_rejected(self) -> None:
        plan = plan_project_install_flow(
            project_root=self.project,
            project_id="demo",
            source_commit=self.commit,
        )

        def wrong_source(request: dict[str, Any]) -> dict[str, Any]:
            self._materialize_runtime()
            result = self._adapter_result(request)
            result["source"] = {"commit": "b" * 40}
            return result

        with self.assertRaises(ProjectInstallFlowError) as context:
            apply_project_install_flow(
                plan=plan,
                approved_plan_digest=plan["plan_digest"],
                lifecycle_adapter=wrong_source,
            )
        self.assertEqual("PROJECT_INSTALL_LIVE_SOURCE_MISMATCH", context.exception.code)

    def test_unmanaged_local_file_change_is_rejected(self) -> None:
        plan = plan_project_install_flow(
            project_root=self.project,
            project_id="demo",
            source_commit=self.commit,
        )

        def mutating_adapter(request: dict[str, Any]) -> dict[str, Any]:
            self._materialize_runtime()
            (self.project / "README.md").write_text("changed\n", encoding="utf-8")
            return self._adapter_result(request)

        with self.assertRaises(ProjectInstallFlowError) as context:
            apply_project_install_flow(
                plan=plan,
                approved_plan_digest=plan["plan_digest"],
                lifecycle_adapter=mutating_adapter,
            )
        self.assertEqual("PROJECT_INSTALL_LOCAL_FILES_CHANGED", context.exception.code)

    def test_partial_ai_surface_is_blocked_without_overwrite(self) -> None:
        (self.project / ".ai" / "local").mkdir(parents=True)
        (self.project / ".ai" / "local" / "notes.md").write_text(
            "keep\n", encoding="utf-8"
        )
        plan = plan_project_install_flow(
            project_root=self.project,
            project_id="demo",
            source_commit=self.commit,
        )
        self.assertEqual("BLOCKED", plan["state"])
        self.assertEqual(
            "PROJECT_RUNTIME_INSTALLATION_INCOMPLETE",
            plan["blocked_reason"],
        )
        self.assertTrue((self.project / ".ai" / "local" / "notes.md").is_file())

    def _materialize_runtime(self) -> None:
        base = self.project / ".ai" / "runtime" / "project_instance"
        validation = base / "validation"
        validation.mkdir(parents=True)
        manifest = {
            "schema": "ai-career.project-runtime-installation.v1",
            "installation": {"project": "demo"},
            "source": {"commit": self.commit},
        }
        (base / "DISTRIBUTION_MANIFEST.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
        )
        (base / "VERSION_MANIFEST.md").write_text("version\n", encoding="utf-8")
        (base / "project_anchor.md").write_text("anchor\n", encoding="utf-8")
        (validation / "latest.md").write_text("PASS\n", encoding="utf-8")

    def _adapter_result(self, request: dict[str, Any]) -> dict[str, Any]:
        managed = [
            relative.as_posix()
            for relative in (
                Path(".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"),
                Path(".ai/runtime/project_instance/VERSION_MANIFEST.md"),
                Path(".ai/runtime/project_instance/project_anchor.md"),
                Path(".ai/runtime/project_instance/validation/latest.md"),
            )
        ]
        return {
            "schema": "universe.project-install-result.v1",
            "result": "PASS",
            "repository_runtime": "VERIFIED",
            "target": request["target"],
            "operation": request["operation"],
            "install_mode": request["install_mode"],
            "source": {"commit": self.commit},
            "boot_handoff": {"status": "READY_FOR_BOOT"},
            "managed_paths": managed,
        }


if __name__ == "__main__":
    unittest.main()
