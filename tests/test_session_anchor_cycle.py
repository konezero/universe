"""A collected Task Frame result is appended to its Session Anchor's own history.

Appending is the revision: the store's append-only event ordinal only grows and
nothing already written is rewritten.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".ai" / "runtime"))

from reference_runtime.anchor_session_memory_runtime import AnchorSessionMemoryRuntime  # noqa: E402
from session_anchor_cycle import append_cycle_result, session_store_path  # noqa: E402

ANCHOR = "session_anchor_cycletest0001"
SESSION = "session_cycletest0001"


class SessionAnchorCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        path = session_store_path(self.repo, SESSION)
        path.parent.mkdir(parents=True)
        runtime = AnchorSessionMemoryRuntime(database_path=path)
        outcome = runtime.record_snapshot(
            snapshot={
                "anchor_id": ANCHOR, "frame_id": "current", "state": "READY",
                "observed_at": "2026-09-19T01:00:00Z", "session_id": SESSION,
                "task_frame_refs": [],
            },
            source_ref="file:///registry.json",
        )
        self.assertIn(outcome["status"], {"SNAPSHOT_RECORDED", "SNAPSHOT_UPDATED"})
        runtime.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def append(self, **overrides):
        kwargs = dict(
            session_id=SESSION, session_anchor_ref=ANCHOR, task_frame_id="frame-1",
            status="COMPLETED", result_ref="task-frame-result://frame-1/worker/1",
            result_digest="a" * 64, detail={"role": "IMPLEMENTER"},
            observed_at="2026-09-19T02:00:00Z",
        )
        kwargs.update(overrides)
        return append_cycle_result(self.repo, **kwargs)

    def history(self):
        runtime = AnchorSessionMemoryRuntime(database_path=session_store_path(self.repo, SESSION))
        try:
            return runtime.event_history(), runtime.stored_snapshot()
        finally:
            runtime.close()

    def test_collected_result_is_appended_and_the_revision_goes_up(self) -> None:
        before, snapshot_before = self.history()
        first = self.append()
        self.assertEqual("SESSION_ANCHOR_CYCLE_APPENDED", first["status"], first)
        self.assertEqual(len(before) + 1, first["revision"])
        second = self.append(task_frame_id="frame-2", result_ref="task-frame-result://frame-2/reviewer/1")
        self.assertEqual("SESSION_ANCHOR_CYCLE_APPENDED", second["status"], second)
        self.assertEqual(first["revision"] + 1, second["revision"])
        events, snapshot_after = self.history()
        collected = [e for e in events if e["action"] == "TASK_FRAME_RESULT_COLLECTED"]
        self.assertEqual(["frame-1", "frame-2"], [e["details"]["task_frame_id"] for e in collected])
        self.assertEqual("COMPLETED", collected[0]["details"]["status"])
        self.assertEqual("task-frame-result://frame-1/worker/1", collected[0]["details"]["result_ref"])
        self.assertEqual("a" * 64, collected[0]["details"]["result_digest"])
        # The session reads how far it got from its own history alone, and the
        # snapshot itself is never rewritten by an append.
        self.assertEqual(snapshot_before["snapshot"], snapshot_after["snapshot"])
        self.assertEqual(snapshot_before["observed_at"], snapshot_after["observed_at"])

    def test_retrying_a_collected_result_does_not_append_again(self) -> None:
        first = self.append()
        replay = self.append()
        self.assertEqual("SESSION_ANCHOR_CYCLE_REPLAYED", replay["status"], replay)
        self.assertEqual(first["revision"], replay["revision"])
        events, _ = self.history()
        self.assertEqual(1, sum(1 for e in events if e["action"] == "TASK_FRAME_RESULT_COLLECTED"))
        # A different terminal status of the same frame is its own record.
        failed = self.append(status="FAILED", result_ref=None, result_digest=None,
                             detail={"error_code": "PROVIDER_FAILED"})
        self.assertEqual("SESSION_ANCHOR_CYCLE_APPENDED", failed["status"], failed)
        self.assertEqual(first["revision"] + 1, failed["revision"])

    def test_history_never_moves_backwards_in_time(self) -> None:
        # An earlier clock than the anchor's own observation is clamped forward.
        result = self.append(observed_at="2026-09-19T00:00:00Z")
        self.assertEqual("SESSION_ANCHOR_CYCLE_APPENDED", result["status"], result)
        self.assertEqual("2026-09-19T01:00:00Z", result["observed_at"])

    def test_refuses_another_sessions_anchor_and_bad_input(self) -> None:
        mismatch = self.append(session_anchor_ref="session_anchor_someone_else")
        self.assertEqual("SESSION_ANCHOR_IDENTITY_MISMATCH", mismatch["status"], mismatch)
        self.assertEqual("SESSION_ANCHOR_STORE_MISSING", self.append(session_id="session_unknown")["status"])
        self.assertEqual("SESSION_ANCHOR_CYCLE_STATUS_INVALID", self.append(status="STARTED")["status"])
        self.assertEqual("SESSION_ANCHOR_CYCLE_IDENTITY_REQUIRED", self.append(task_frame_id="")["status"])
        events, _ = self.history()
        self.assertEqual(0, sum(1 for e in events if e["action"] == "TASK_FRAME_RESULT_COLLECTED"))


if __name__ == "__main__":
    unittest.main()
