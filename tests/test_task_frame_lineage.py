from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from task_frame_lineage import TaskFrameLineageError, TaskFrameLineageStore  # noqa: E402


class TaskFrameLineageStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "lineage.sqlite3"
        self.store = TaskFrameLineageStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_frame_result_lineage_is_append_only_and_idempotent(self) -> None:
        frame, created = self.store.create_task_frame(
            frame_ref="frame-001",
            origin_session_anchor_ref="session-anchor-origin",
            target_session_anchor_ref="session-anchor-target",
        )
        self.assertTrue(created)
        self.assertEqual("session-anchor-origin", frame["origin_session_anchor_ref"])
        self.assertEqual(1, frame["revision"])

        replayed, created = self.store.create_task_frame(
            frame_ref="frame-001",
            origin_session_anchor_ref="session-anchor-origin",
            target_session_anchor_ref="session-anchor-target",
        )
        self.assertFalse(created)
        self.assertEqual(frame["frame_digest"], replayed["frame_digest"])

        result, attached = self.store.attach_result(
            result_ref="result-001",
            frame_ref="frame-001",
            origin_session_anchor_ref="session-anchor-origin",
            result={"status": "PASS", "summary": "complete"},
        )
        self.assertTrue(attached)
        self.assertEqual("frame-001", result["frame_ref"])
        replayed_result, attached = self.store.attach_result(
            result_ref="result-001",
            frame_ref="frame-001",
            origin_session_anchor_ref="session-anchor-origin",
            result={"summary": "complete", "status": "PASS"},
        )
        self.assertFalse(attached)
        self.assertEqual(result["result_digest"], replayed_result["result_digest"])

        material = self.store.get_task_frame("frame-001")
        self.assertEqual(2, material["revision"])
        self.assertEqual(["TASK_FRAME_CREATED", "RESULT_ATTACHED"], [
            item["event_type"] for item in material["revisions"]
        ])

    def test_parent_and_result_must_preserve_exact_origin_anchor(self) -> None:
        self.store.create_task_frame(
            frame_ref="frame-parent", origin_session_anchor_ref="anchor-a"
        )
        with self.assertRaisesRegex(TaskFrameLineageError, "preserve"):
            self.store.create_task_frame(
                frame_ref="frame-child",
                origin_session_anchor_ref="anchor-b",
                parent_task_frame_ref="frame-parent",
            )
        with self.assertRaisesRegex(TaskFrameLineageError, "does not match"):
            self.store.attach_result(
                result_ref="result-wrong-origin",
                frame_ref="frame-parent",
                origin_session_anchor_ref="anchor-b",
                result={"status": "PASS"},
            )

    def test_restart_recovery_keeps_multiple_concurrent_session_anchors(self) -> None:
        self.store.create_task_frame(
            frame_ref="frame-a", origin_session_anchor_ref="anchor-a"
        )
        self.store.create_task_frame(
            frame_ref="frame-b", origin_session_anchor_ref="anchor-b"
        )
        reopened = TaskFrameLineageStore(self.database)

        recovered = reopened.recover()

        self.assertEqual("DURABLE_REHYDRATED", recovered["recovery"])
        self.assertEqual(["frame-a", "frame-b"], [
            item["frame_ref"] for item in recovered["task_frames"]
        ])
        self.assertEqual(["frame-a"], [
            item["frame_ref"]
            for item in reopened.list_task_frames(origin_session_anchor_ref="anchor-a")
        ])

    def test_concurrent_origins_do_not_compete_for_a_single_active_lease(self) -> None:
        errors: list[BaseException] = []
        barrier = threading.Barrier(3)

        def create(frame_ref: str, origin: str) -> None:
            try:
                barrier.wait()
                TaskFrameLineageStore(self.database).create_task_frame(
                    frame_ref=frame_ref, origin_session_anchor_ref=origin
                )
            except BaseException as error:  # noqa: BLE001 - test captures failures
                errors.append(error)

        first = threading.Thread(target=create, args=("frame-a", "anchor-a"))
        second = threading.Thread(target=create, args=("frame-b", "anchor-b"))
        first.start()
        second.start()
        barrier.wait()
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(2, len(self.store.list_task_frames()))


if __name__ == "__main__":
    unittest.main()
