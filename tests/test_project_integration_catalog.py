from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_integration_catalog import (  # noqa: E402
    CATALOG_RELATIVE_ROOT,
    CATALOG_SCHEMA,
    CATALOG_STATUS,
    PROPOSAL_SCHEMA,
    ProjectIntegrationCatalogError,
    build_project_integration_proposal,
    load_project_integration_catalog,
)


class ProjectIntegrationCatalogTests(unittest.TestCase):
    def test_loads_the_committed_catalog_without_project_side_effects(self) -> None:
        catalog = load_project_integration_catalog(ROOT)

        self.assertEqual(CATALOG_SCHEMA, catalog["schema"])
        self.assertEqual(CATALOG_STATUS, catalog["status"])
        self.assertEqual(CATALOG_RELATIVE_ROOT.as_posix(), catalog["catalog_root"])
        self.assertEqual("LOCAL_ONLY", catalog["project_binding"]["workspace_tracking"])
        self.assertEqual(
            ["connection", "node_memory", "project_binding", "todo_policy"],
            sorted(template["template_id"] for template in catalog["templates"]),
        )
        self.assertEqual("NONE", catalog["effects"]["project_source_write"])
        self.assertEqual("NONE", catalog["effects"]["project_runtime_state_write"])

    def test_rejects_missing_or_invalid_binding_template(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_root = root / CATALOG_RELATIVE_ROOT
            catalog_root.mkdir(parents=True)
            for source in (ROOT / CATALOG_RELATIVE_ROOT).iterdir():
                if source.name == "project-binding.example.json":
                    continue
                (catalog_root / source.name).write_bytes(source.read_bytes())
            with self.assertRaisesRegex(
                ProjectIntegrationCatalogError,
                "PROJECT_INTEGRATION_TEMPLATE_UNAVAILABLE",
            ):
                load_project_integration_catalog(root)
            (catalog_root / "project-binding.example.json").write_text(
                json.dumps({"schema": "wrong"}), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                ProjectIntegrationCatalogError,
                "PROJECT_INTEGRATION_BINDING_INVALID",
            ):
                load_project_integration_catalog(root)

    def test_builds_a_split_scope_proposal_without_writing_a_project(self) -> None:
        proposal = build_project_integration_proposal("GCS", root=ROOT)

        self.assertEqual(PROPOSAL_SCHEMA, proposal["schema"])
        self.assertEqual("GCS", proposal["project_id"])
        self.assertEqual("NOT_STARTED", proposal["apply_contract"]["execution"])
        self.assertEqual("PROPOSED", proposal["effects"]["project_source_write"])
        self.assertEqual("PROPOSED", proposal["effects"]["project_runtime_state_write"])
        self.assertEqual(
            ["LOCAL_RUNTIME", "PROJECT_SOURCE"],
            sorted({asset["scope"] for asset in proposal["assets"]}),
        )
        self.assertEqual(
            ".universe/project.json",
            next(
                asset["target_path"]
                for asset in proposal["assets"]
                if asset["scope"] == "PROJECT_SOURCE"
            ),
        )
        binding_asset = next(
            asset
            for asset in proposal["assets"]
            if asset["target_path"] == ".universe/project.json"
        )
        binding_content = base64.b64decode(binding_asset["content_base64"])
        self.assertEqual(
            "GCS",
            json.loads(binding_content.decode("utf-8"))["project_id"],
        )
        for asset in proposal["assets"]:
            content = base64.b64decode(asset["content_base64"])
            self.assertEqual(asset["sha256"], hashlib.sha256(content).hexdigest())

if __name__ == "__main__":
    unittest.main()
