from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from memory_noise_preview import (  # noqa: E402
    SCHEMA, NoisePreviewError, build_provider_request, normalize_decisions,
)


def candidate(candidate_id: str, kind: str) -> dict:
    return {
        "candidate_id": candidate_id, "candidate_digest": "a" * 64 if candidate_id == "one" else "b" * 64,
        "project_id": "TEST", "stage": "FAST_EXTRACT", "kind": "MEMORY",
        "summary": "A useful source-backed project direction.",
        "knowledge": {"kind": kind},
    }


class NoisePreviewContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = [candidate("one", "USER_IDEA"), candidate("two", "REUSABLE_EXPERIENCE")]
        self.result = {
            "schema": SCHEMA,
            "decisions": [
                {"candidate_id": "two", "candidate_digest": "b" * 64,
                 "disposition": "NOISE", "route": "NONE"},
                {"candidate_id": "one", "candidate_digest": "a" * 64,
                 "disposition": "KEEP", "route": "PROPOSAL_SOURCE"},
            ],
        }

    def test_request_is_bounded_and_read_only(self) -> None:
        binding = {
            "task_frame_ref": "frame", "frame_id": "frame", "session_id": "session",
            "turn_id": "turn", "endpoint": "http://127.0.0.1:1", "token": "secret",
            "invoker_actor_ref": "actor",
        }
        request = build_provider_request(
            project_id="TEST", candidates=self.inputs, runtime_binding=binding,
            invocation_id="run", config_digest="c" * 64,
        )
        self.assertEqual("NONE", request["repository_write_scope"])
        self.assertEqual([], request["mutation_scope"]["operations"])
        self.assertEqual(2, len(request["context_pack"]["candidates"]))
        self.assertNotIn("token", request["context_pack"])

    def test_exhaustive_decisions_are_sorted_and_pinned(self) -> None:
        decisions = normalize_decisions(self.result, self.inputs, "TEST")
        self.assertEqual(["one", "two"], [item["candidate_id"] for item in decisions])
        self.assertEqual(["KEEP", "NOISE"], [item["disposition"] for item in decisions])

    def test_missing_duplicate_unknown_or_wrong_digest_fails(self) -> None:
        variants = [
            self.result["decisions"][:1],
            [self.result["decisions"][0]] * 2,
            [{**self.result["decisions"][0], "candidate_id": "unknown"}, self.result["decisions"][1]],
            [{**self.result["decisions"][0], "candidate_digest": "c" * 64}, self.result["decisions"][1]],
            [{**self.result["decisions"][0], "candidate_id": []}, self.result["decisions"][1]],
            [{**self.result["decisions"][0], "route": []}, self.result["decisions"][1]],
        ]
        for decisions in variants:
            with self.subTest(decisions=decisions), self.assertRaises(NoisePreviewError):
                normalize_decisions({"schema": SCHEMA, "decisions": decisions}, self.inputs, "TEST")

    def test_user_authored_candidate_cannot_be_discarded(self) -> None:
        self.result["decisions"][1]["disposition"] = "NOISE"
        self.result["decisions"][1]["route"] = "NONE"
        with self.assertRaises(NoisePreviewError) as rejected:
            normalize_decisions(self.result, self.inputs, "TEST")
        self.assertEqual("MEMORY_NOISE_USER_SOURCE_REJECTED", rejected.exception.code)

    def test_secret_like_summary_is_rejected_before_provider_request(self) -> None:
        self.inputs[0]["summary"] = "api_key=verysecretvalue"
        with self.assertRaises(NoisePreviewError) as rejected:
            normalize_decisions(self.result, self.inputs, "TEST")
        self.assertEqual("MEMORY_NOISE_INPUT_SECRET_FORBIDDEN", rejected.exception.code)


if __name__ == "__main__":
    unittest.main()
