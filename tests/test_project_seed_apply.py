from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_seed_apply import (  # noqa: E402
    apply_project_seed_asset_proposal,
    build_project_seed_asset_approval,
)
from project_seed_assets import (  # noqa: E402
    ProjectSeedAssetError,
    build_project_seed_asset_proposal,
    load_project_seed_assets,
)


class FakeMutationGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def apply_file(
        self,
        *,
        target: Path,
        content: bytes,
        operation: str,
        boundary: str,
        approval_evidence_ref: str,
        request_ref: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "target": target,
                "content": content,
                "operation": operation,
                "boundary": boundary,
                "approval_evidence_ref": approval_evidence_ref,
                "request_ref": request_ref,
            }
        )
        target.write_bytes(content)
        return {
            "status": "FILE_MUTATION_APPLIED",
            "receipt_id": f"permit-{len(self.calls)}",
            "postimage": {"status": "PRESENT"},
        }


class ProjectSeedApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proposal = build_project_seed_asset_proposal(self._seed())
        self.approval = build_project_seed_asset_approval(
            project_id="GCS",
            proposal=self.proposal,
            evidence_ref="universe://approval/seed-assets-001",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_approved_bundle_is_applied_by_project_gateway(self) -> None:
        (self.root / ".ai" / "universe").mkdir(parents=True)
        gateway = FakeMutationGateway()

        receipt = apply_project_seed_asset_proposal(
            project_root=self.root,
            project_id="GCS",
            proposal=self.proposal,
            approval=self.approval,
            mutation_gateway=gateway,
        )

        self.assertEqual("PROJECT_SEED_ASSETS_APPLIED", receipt["status"])
        self.assertEqual(5, len(gateway.calls))
        self.assertEqual("manifest.json", gateway.calls[-1]["target"].name)
        self.assertEqual(
            "seed-001",
            load_project_seed_assets(self.root)["seed_id"],
        )
        self.assertTrue(
            all(
                call["approval_evidence_ref"]
                == "universe://approval/seed-assets-001"
                for call in gateway.calls
            )
        )

    def test_repeated_apply_is_read_only_when_all_digests_match(self) -> None:
        (self.root / ".ai" / "universe").mkdir(parents=True)
        first_gateway = FakeMutationGateway()
        apply_project_seed_asset_proposal(
            project_root=self.root,
            project_id="GCS",
            proposal=self.proposal,
            approval=self.approval,
            mutation_gateway=first_gateway,
        )
        repeated_gateway = FakeMutationGateway()

        receipt = apply_project_seed_asset_proposal(
            project_root=self.root,
            project_id="GCS",
            proposal=self.proposal,
            approval=self.approval,
            mutation_gateway=repeated_gateway,
        )

        self.assertEqual("PROJECT_SEED_ASSETS_ALREADY_APPLIED", receipt["status"])
        self.assertEqual([], repeated_gateway.calls)
        self.assertEqual(5, len(receipt["unchanged"]))

    def test_apply_rejects_stale_approval_before_write(self) -> None:
        (self.root / ".ai" / "universe").mkdir(parents=True)
        gateway = FakeMutationGateway()
        approval = dict(self.approval)
        approval["proposal_digest"] = "f" * 64

        with self.assertRaisesRegex(
            ProjectSeedAssetError,
            "PROJECT_SEED_ASSET_APPROVAL_MISMATCH",
        ):
            apply_project_seed_asset_proposal(
                project_root=self.root,
                project_id="GCS",
                proposal=self.proposal,
                approval=approval,
                mutation_gateway=gateway,
            )

        self.assertEqual([], gateway.calls)

    def test_apply_does_not_create_missing_runtime_state_root(self) -> None:
        gateway = FakeMutationGateway()

        with self.assertRaisesRegex(
            ProjectSeedAssetError,
            "PROJECT_SEED_ASSET_ROOT_REQUIRED",
        ):
            apply_project_seed_asset_proposal(
                project_root=self.root,
                project_id="GCS",
                proposal=self.proposal,
                approval=self.approval,
                mutation_gateway=gateway,
            )

        self.assertFalse((self.root / ".ai" / "universe").exists())
        self.assertEqual([], gateway.calls)

    @staticmethod
    def _seed() -> dict[str, Any]:
        return {
            "project_id": "GCS",
            "seed_id": "seed-001",
            "seed_digest": "a" * 64,
            "source": {
                "kind": "PROJECT_DISCOVERY",
                "ref": "project://GCS/discovery/001",
            },
            "project": {
                "name": "GCS",
                "goal": "Validate Project Seed application.",
            },
            "nodes": [],
            "edges": [],
            "implementation": {"nodes": []},
            "implementation_bindings": [],
            "documents": [],
        }


if __name__ == "__main__":
    unittest.main()
