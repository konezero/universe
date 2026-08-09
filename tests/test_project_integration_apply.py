from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_integration_apply import (  # noqa: E402
    ProjectIntegrationApplyError,
    apply_project_integration_proposal,
    build_project_integration_approval,
)
from project_integration_catalog import build_project_integration_proposal  # noqa: E402


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
        write_roots: tuple[Path, ...],
        task_summary: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "target": target,
                "operation": operation,
                "boundary": boundary,
                "approval_evidence_ref": approval_evidence_ref,
                "request_ref": request_ref,
                "write_roots": write_roots,
                "task_summary": task_summary,
            }
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {
            "status": "FILE_MUTATION_APPLIED",
            "receipt_id": f"permit-{len(self.calls)}",
        }


class ProjectIntegrationApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proposal = build_project_integration_proposal("GCS", root=ROOT)
        self.approval = build_project_integration_approval(
            project_id="GCS",
            proposal=self.proposal,
            project_source_evidence_ref="universe://approval/integration/source-001",
            local_runtime_evidence_ref="universe://approval/integration/runtime-001",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_approved_proposal_applies_split_assets(self) -> None:
        gateway = FakeMutationGateway()

        receipt = apply_project_integration_proposal(
            project_root=self.root,
            project_id="GCS",
            proposal=self.proposal,
            approval=self.approval,
            mutation_gateway=gateway,
        )

        self.assertEqual("PROJECT_INTEGRATION_APPLIED", receipt["status"])
        self.assertEqual("APPLIED", receipt["project_source_write"])
        self.assertEqual("APPLIED", receipt["project_runtime_state_write"])
        self.assertEqual(5, len(gateway.calls))
        source_call = next(
            call
            for call in gateway.calls
            if call["target"].relative_to(self.root).as_posix()
            == ".universe/project.json"
        )
        self.assertEqual(
            "universe://approval/integration/source-001",
            source_call["approval_evidence_ref"],
        )
        self.assertEqual((source_call["target"].parent,), source_call["write_roots"])
        runtime_calls = [call for call in gateway.calls if call is not source_call]
        self.assertTrue(
            all(
                call["approval_evidence_ref"]
                == "universe://approval/integration/runtime-001"
                for call in runtime_calls
            )
        )
        self.assertTrue((self.root / ".universe" / "project.json").is_file())
        self.assertTrue(
            (self.root / ".ai" / "memory" / "universe_nodes" / "README.md").is_file()
        )

    def test_repeated_apply_is_read_only_when_all_digests_match(self) -> None:
        apply_project_integration_proposal(
            project_root=self.root,
            project_id="GCS",
            proposal=self.proposal,
            approval=self.approval,
            mutation_gateway=FakeMutationGateway(),
        )
        gateway = FakeMutationGateway()

        receipt = apply_project_integration_proposal(
            project_root=self.root,
            project_id="GCS",
            proposal=self.proposal,
            approval=self.approval,
            mutation_gateway=gateway,
        )

        self.assertEqual("PROJECT_INTEGRATION_ALREADY_APPLIED", receipt["status"])
        self.assertEqual([], gateway.calls)
        self.assertEqual(5, len(receipt["unchanged"]))

    def test_tampered_proposal_is_rejected_before_write(self) -> None:
        proposal = copy.deepcopy(self.proposal)
        proposal["assets"][0]["content_base64"] = "dGFtcGVyZWQ="
        gateway = FakeMutationGateway()

        with self.assertRaisesRegex(
            ProjectIntegrationApplyError,
            "PROJECT_INTEGRATION_DIGEST_MISMATCH",
        ):
            apply_project_integration_proposal(
                project_root=self.root,
                project_id="GCS",
                proposal=proposal,
                approval=self.approval,
                mutation_gateway=gateway,
            )

        self.assertEqual([], gateway.calls)


if __name__ == "__main__":
    unittest.main()
