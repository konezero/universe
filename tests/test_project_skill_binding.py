from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from project_skill_binding import (  # noqa: E402
    ProjectSkillBindingError,
    build_project_skill_binding_proposal,
)


class ProjectSkillBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = (
            self.root / ".ai" / "skills" / "common" / "source-review" / "SKILL.md"
        )
        self.skill.parent.mkdir(parents=True)
        self.skill.write_text(
            "---\nname: source-review\n---\n\n# Source Review\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_installed_skill_becomes_passive_binding_proposal(self) -> None:
        proposal = build_project_skill_binding_proposal(
            project_root=self.root,
            context=self.context(),
        )

        self.assertEqual(
            "PROJECT_SKILL_BINDING_PROPOSAL_READY",
            proposal["status"],
        )
        self.assertEqual("PROJECT_MASTER_BINDING_PROPOSED", proposal["binding_state"])
        self.assertEqual(
            ".ai/skills/common/source-review/SKILL.md",
            proposal["skill_bindings"][0]["skill_ref"],
        )
        self.assertEqual(
            hashlib.sha256(self.skill.read_bytes()).hexdigest(),
            proposal["resolution_evidence"][0]["skill_file_sha256"],
        )
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["task_frame_started"])
        self.assertFalse(proposal["authority_created"])
        self.assertFalse(proposal["repository_write"])

    def test_missing_installed_skill_blocks_resolution(self) -> None:
        self.skill.unlink()

        with self.assertRaisesRegex(
            ProjectSkillBindingError,
            "installed Project Skill is unavailable",
        ):
            build_project_skill_binding_proposal(
                project_root=self.root,
                context=self.context(),
            )

    def test_duplicate_installed_skill_ref_is_ambiguous(self) -> None:
        duplicate = (
            self.root / ".ai" / "skills" / "project" / "source-review" / "SKILL.md"
        )
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text(
            "---\nname: source-review\n---\n\n# Duplicate\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ProjectSkillBindingError,
            "installed Project Skill is ambiguous",
        ):
            build_project_skill_binding_proposal(
                project_root=self.root,
                context=self.context(),
            )

    def test_frontmatter_name_must_match_skill_id(self) -> None:
        self.skill.write_text(
            "---\nname: another-skill\n---\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ProjectSkillBindingError,
            "installed Project Skill is unavailable",
        ):
            build_project_skill_binding_proposal(
                project_root=self.root,
                context=self.context(),
            )

    @staticmethod
    def context() -> dict[str, object]:
        return {
            "project_id": "GCS",
            "handoff_id": "handoff_test",
            "adoption_id": "skilladopt_test",
            "context_digest": "1" * 64,
            "binding_candidates": [
                {
                    "candidate_id": "candidate_test",
                    "skill_id": "source-review",
                    "skill_version": "1.0.0",
                    "operation_class": "READ",
                    "context_pack_digest": "2" * 64,
                    "model_ref": "provider://OPENAI/model/gpt-test",
                    "provider_ref": "OPENAI",
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
