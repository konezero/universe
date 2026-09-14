"""Todo-level BLOCKED reason: a narrow, dedicated write path.

2026-09-14: a Todo's own BLOCKED state previously carried no stored reason
anywhere in the data model (confirmed by search — no such field existed).
set_todo_blocked_reason / POST /v1/todos/{id}/blocked-reason is a narrow
addition, deliberately separate from the full-body todo.update replace path,
so it can never be silently wiped by an unrelated title/detail/priority edit.
Both Fleet and the flat Todo list read the same blocked_reason column via
_todo_row, so the two screens are structurally guaranteed to agree.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures


class TodoBlockedReasonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def make_todo(self, title, state="BLOCKED"):
        return self.server.store.create_todo({
            "scope_kind": "PROJECT", "project_id": "TEST", "title": title, "detail": "",
            "priority": "P1", "state": state, "source_kind": "USER", "sort_order": 0,
        })

    def set_reason(self, todo_id, expected_revision, blocked_reason):
        return self.request(
            "POST", f"/v1/todos/{todo_id}/blocked-reason",
            {"expected_revision": expected_revision, "blocked_reason": blocked_reason},
        )

    def test_unset_reason_reads_as_null_not_a_guess(self):
        todo = self.make_todo("t1")
        self.assertIsNone(todo["blocked_reason"])

    def test_write_and_readback(self):
        todo = self.make_todo("t2")
        status, result = self.set_reason(todo["todo_id"], todo["revision"], "Task Frame 미배정 — Goal 승인 대기")
        self.assertEqual(status, 200)
        self.assertEqual(result["todo"]["blocked_reason"], "Task Frame 미배정 — Goal 승인 대기")
        self.assertEqual(result["todo"]["revision"], todo["revision"] + 1)
        # Independent read confirms it is genuinely persisted, not just echoed.
        fresh = self.server.store.get_todo(todo["todo_id"])
        self.assertEqual(fresh["blocked_reason"], "Task Frame 미배정 — Goal 승인 대기")

    def test_stale_revision_rejected(self):
        todo = self.make_todo("t3")
        status, result = self.set_reason(todo["todo_id"], todo["revision"] + 5, "reason")
        self.assertEqual(status, 409)
        self.assertEqual(result["error_code"], "TODO_REVISION_CONFLICT")

    def test_unrelated_todo_update_never_wipes_the_reason(self):
        todo = self.make_todo("t4")
        status, result = self.set_reason(todo["todo_id"], todo["revision"], "waiting on review")
        self.assertEqual(status, 200)
        current = result["todo"]
        # A completely unrelated metadata edit through the existing, separate
        # todo.update action must leave blocked_reason untouched.
        updated = self.server.store.update_todo(current["todo_id"], {
            "scope_kind": current["scope_kind"], "title": "t4 renamed", "detail": current["detail"],
            "priority": current["priority"], "state": current["state"], "source_kind": current["source_kind"],
            "sort_order": current["sort_order"], "revision": current["revision"], "project_id": current["project_id"],
        })
        self.assertEqual(updated["blocked_reason"], "waiting on review")

    def test_clearing_the_reason_with_empty_string_stores_null(self):
        todo = self.make_todo("t5")
        status1, result1 = self.set_reason(todo["todo_id"], todo["revision"], "some reason")
        self.assertEqual(status1, 200)
        status2, result2 = self.set_reason(todo["todo_id"], result1["todo"]["revision"], "")
        self.assertEqual(status2, 200)
        self.assertIsNone(result2["todo"]["blocked_reason"])


if __name__ == "__main__":
    unittest.main()
