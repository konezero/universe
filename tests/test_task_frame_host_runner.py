"""The role runner sends the Master's persona, scope and feedback, and only reports."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from task_frame_host_runner import RuntimeHostRoleRunner  # noqa: E402
from universe_runtime_host import RuntimeHostError  # noqa: E402


class FakeRuntimeHost:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def invoke_structured_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


SPEC = {
    "repository_root": str(ROOT),
    "task_frame_id": "tf1",
    "todo_id": "todo_1",
    "provider": "CODEX",
    "runtime_binding": {"endpoint": "http://127.0.0.1:1"},
    "todo": {"title": "Fix it", "detail": "the detail"},
    "persona_text": "You are the careful implementer.",
    "worker_write_scope": {
        "repository_write_scope": "BOUNDED",
        "mutation_scope": {"operations": ["MODIFY"], "targets": ["C:/repo/a.py"]},
    },
}
GOOD = {
    "structured_result": {
        "outcome": "DONE",
        "result_text": "changed a.py",
        "evidence_refs": [],
        "validation_state": "UNVERIFIED",
    },
    "result_receipt_ref": "receipt-1",
}


class RoleRunnerTests(unittest.TestCase):
    def test_worker_gets_persona_scope_and_the_masters_feedback(self):
        host = FakeRuntimeHost(GOOD)
        runner = RuntimeHostRoleRunner(SPEC, runtime_host=host)
        result = runner.run("WORKER", attempt=2, feedback="handle empty input")
        call = host.calls[0]
        self.assertEqual("tf1_worker_2", call["frame_id"])
        self.assertEqual("BOUNDED", call["repository_write_scope"])
        self.assertEqual(SPEC["worker_write_scope"]["mutation_scope"], call["mutation_scope"])
        self.assertEqual("You are the careful implementer.", call["context_pack"]["persona"])
        self.assertEqual("handle empty input", call["context_pack"]["master_feedback"])
        self.assertIn("NO_SUBAGENTS", call["constraints"])
        self.assertEqual("COMPLETED", result.status)
        self.assertEqual("changed a.py", result.summary)
        self.assertEqual("task-frame-result://tf1_worker_2/worker-turn/receipt-1", result.result_ref)
        self.assertEqual(64, len(result.result_digest))

    def test_reviewer_is_read_only_and_sees_the_workers_result(self):
        host = FakeRuntimeHost(GOOD)
        runner = RuntimeHostRoleRunner(SPEC, runtime_host=host)
        runner.run("WORKER", attempt=1, feedback=None)
        host.result = {
            "structured_result": {"verdict": "PASS", "evidence_refs": [], "note": "ok", "next_action": ""},
            "result_receipt_ref": "receipt-2",
        }
        result = runner.run("REVIEWER", attempt=1, feedback=None)
        call = host.calls[1]
        self.assertEqual("NONE", call["repository_write_scope"])
        self.assertIn("READ_ONLY", call["constraints"])
        self.assertEqual("changed a.py", call["context_pack"]["worker_result"]["result_text"])
        self.assertEqual("COMPLETED", result.status)

    def test_provider_failures_and_bad_results_are_failed_roles(self):
        error = RuntimeHostError("WORKER_TRANSPORT_FAILED", "codex down")
        failed = RuntimeHostRoleRunner(SPEC, runtime_host=FakeRuntimeHost(error=error))
        result = failed.run("WORKER", attempt=1, feedback=None)
        self.assertEqual(("FAILED", "WORKER_TRANSPORT_FAILED"), (result.status, result.error_code))
        empty = RuntimeHostRoleRunner(SPEC, runtime_host=FakeRuntimeHost({"structured_result": None}))
        self.assertEqual("PROVIDER_RESULT_INVALID", empty.run("WORKER", attempt=1, feedback=None).error_code)


if __name__ == "__main__":
    unittest.main()
