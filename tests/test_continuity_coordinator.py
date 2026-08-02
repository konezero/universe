from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from continuity_coordinator import (  # noqa: E402
    ContinuityCoordinatorError,
    ProjectContinuityCoordinator,
)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def save(
        self,
        *,
        project_root: Path,
        checkpoint_request: Mapping[str, Any],
        resume_request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "project_root": project_root,
                "checkpoint_request": dict(checkpoint_request),
                "resume_request": dict(resume_request),
            }
        )
        serial = len(self.calls)
        return {
            "checkpoint": {"record_id": f"checkpoint-{serial}"},
            "resume": {"record_id": f"resume-{serial}"},
        }


class FailOnceBackend(FakeBackend):
    def save(
        self,
        *,
        project_root: Path,
        checkpoint_request: Mapping[str, Any],
        resume_request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if not self.calls:
            self.calls.append(
                {
                    "project_root": project_root,
                    "checkpoint_request": dict(checkpoint_request),
                    "resume_request": dict(resume_request),
                }
            )
            raise ContinuityCoordinatorError(
                "PROJECT_CONTINUITY_SAVE_FAILED",
                "simulated transient failure",
            )
        return super().save(
            project_root=project_root,
            checkpoint_request=checkpoint_request,
            resume_request=resume_request,
        )


class ProjectContinuityCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "GCS"
        state = self.root / ".ai" / "runtime" / "state"
        state.mkdir(parents=True)
        self.session_path = state / "session.md"
        self.anchor_path = state / "current_anchor_frame.md"
        self.backend = FakeBackend()
        self.coordinator = ProjectContinuityCoordinator(
            Path(self.temp.name) / "universe.sqlite3",
            self.backend,
            clock=lambda: "2026-08-02T12:00:00Z",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_coordinates(self, *, session_id: str = "session-1") -> None:
        self.session_path.write_text(
            "\n".join(
                (
                    "Node: GCS",
                    "Mode: MASTER",
                    f"Session ID: {session_id}",
                    "Frame ID: frame-1",
                    "Executable Runtime Currentness: CURRENT",
                    "Source Commit: " + "a" * 40,
                )
            ),
            encoding="utf-8",
        )
        self.anchor_path.write_text(
            "\n".join(
                (
                    f"Session ID: {session_id}",
                    "Frame ID: frame-1",
                    "Anchor ID: anchor-1",
                    "Executable Runtime Currentness: CURRENT",
                )
            ),
            encoding="utf-8",
        )

    def test_save_writes_checkpoint_and_resume_without_git_publication(self) -> None:
        self.write_coordinates()
        result = self.coordinator.save(
            project_root=self.root,
            trigger="TASK_COMPLETED",
            compressed_context="Completed broker review; next inspect API contract.",
            summary="Broker review complete",
        )
        self.assertEqual("AUTO_CONTINUITY_SAVED", result["status"])
        self.assertEqual("NOT_PERFORMED", result["git_publication"])
        self.assertEqual(1, len(self.backend.calls))
        call = self.backend.calls[0]
        self.assertEqual("GCS", call["checkpoint_request"]["snapshot"]["node"])
        self.assertEqual(
            "Completed broker review; next inspect API contract.",
            call["resume_request"]["snapshot"]["compressed_context"],
        )

    def test_identical_payload_is_idempotent_across_coordinator_restart(self) -> None:
        self.write_coordinates()
        values = {
            "project_root": self.root,
            "trigger": "IDLE",
            "compressed_context": "Stable bounded recovery context.",
        }
        first = self.coordinator.save(**values)
        reopened = ProjectContinuityCoordinator(
            Path(self.temp.name) / "universe.sqlite3",
            self.backend,
            clock=lambda: "2026-08-02T12:05:00Z",
        )
        second = reopened.save(**values)
        self.assertEqual("AUTO_CONTINUITY_SAVED", first["status"])
        self.assertEqual("AUTO_CONTINUITY_ALREADY_SAVED", second["status"])
        self.assertEqual(1, len(self.backend.calls))

    def test_flush_trigger_does_not_duplicate_identical_bounded_state(self) -> None:
        self.write_coordinates()
        values = {
            "project_root": self.root,
            "compressed_context": "Stable bounded recovery context.",
            "summary": "Stable project state",
        }
        first = self.coordinator.save(trigger="TASK_COMPLETED", **values)
        second = self.coordinator.save(trigger="IDLE", **values)
        self.assertEqual("AUTO_CONTINUITY_SAVED", first["status"])
        self.assertEqual("AUTO_CONTINUITY_ALREADY_SAVED", second["status"])
        self.assertEqual(1, len(self.backend.calls))

    def test_failed_save_does_not_suppress_identical_retry(self) -> None:
        self.write_coordinates()
        backend = FailOnceBackend()
        coordinator = ProjectContinuityCoordinator(
            Path(self.temp.name) / "retry.sqlite3",
            backend,
            clock=lambda: "2026-08-02T12:06:00Z",
        )
        values = {
            "project_root": self.root,
            "trigger": "IDLE",
            "compressed_context": "Retry this bounded recovery context.",
        }
        with self.assertRaisesRegex(
            ContinuityCoordinatorError, "simulated transient failure"
        ):
            coordinator.save(**values)

        state = coordinator.status(self.root)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual("FAILED", state["last_status"])

        retried = coordinator.save(**values)
        self.assertEqual("AUTO_CONTINUITY_SAVED", retried["status"])
        self.assertEqual(2, len(backend.calls))

    def test_unknown_coordinates_skip_without_writing_project_store(self) -> None:
        self.session_path.write_text("Node: GCS\nMode: MASTER\nSession ID: UNKNOWN\n")
        self.anchor_path.write_text("Anchor ID: UNKNOWN\n")
        result = self.coordinator.save(
            project_root=self.root,
            trigger="NORMAL_STOP",
            compressed_context="Context is present but coordinates are not.",
        )
        self.assertEqual("AUTO_CONTINUITY_SKIPPED", result["status"])
        self.assertEqual("SESSION_COORDINATES_UNAVAILABLE", result["reason"])
        self.assertEqual([], self.backend.calls)

    def test_live_runtime_coordinate_overrides_stale_static_state(self) -> None:
        self.session_path.write_text("Node: universe\nSession ID: UNKNOWN\n")
        self.anchor_path.write_text("Anchor ID: UNKNOWN\n")
        result = self.coordinator.save(
            project_root=self.root,
            trigger="NORMAL_STOP",
            compressed_context="Live Supervisor coordinate.",
            runtime_coordinate={
                "node": "universe",
                "mode": "CONDUCTOR",
                "session_id": "session-live",
                "frame_id": "conductor",
                "anchor_id": "anchor-live",
                "currentness": "CURRENT",
                "source_ref": "git-object-database://universe@" + "a" * 40,
            },
        )
        self.assertEqual("AUTO_CONTINUITY_SAVED", result["status"])
        request = self.backend.calls[0]["resume_request"]
        self.assertEqual("session-live", request["session_id"])
        self.assertEqual("CONDUCTOR", request["mode"])

    def test_unknown_runtime_currentness_is_saved_without_overclaim(self) -> None:
        result = self.coordinator.save(
            project_root=self.root,
            trigger="NORMAL_STOP",
            compressed_context="Preserve state while currentness is unresolved.",
            runtime_coordinate={
                "node": "GCS",
                "mode": "MASTER",
                "session_id": "session-live",
                "frame_id": "master",
                "anchor_id": "anchor-live",
                "currentness": "UNKNOWN",
                "source_ref": "git-object-database://GCS@" + "a" * 40,
            },
        )
        self.assertEqual("AUTO_CONTINUITY_SAVED", result["status"])
        snapshot = self.backend.calls[0]["checkpoint_request"]["snapshot"]
        self.assertEqual("UNKNOWN", snapshot["currentness"])

    def test_dirty_end_preserves_last_good_record_and_never_creates_summary(self) -> None:
        self.write_coordinates()
        saved = self.coordinator.save(
            project_root=self.root,
            trigger="TASK_COMPLETED",
            compressed_context="Last known good context.",
        )
        state = self.coordinator.mark_dirty_end(self.root, "PROCESS_DISAPPEARED")
        self.assertEqual("DIRTY_END", state["last_status"])
        self.assertEqual(saved["resume_id"], state["last_resume_id"])
        self.assertEqual(1, state["dirty_end"])

    def test_archive_publication_requires_explicit_command_and_separate_approval(self) -> None:
        self.write_coordinates()
        self.coordinator.save(
            project_root=self.root,
            trigger="TASK_COMPLETED",
            compressed_context="Ready for explicit publication.",
        )
        with self.assertRaisesRegex(ContinuityCoordinatorError, "explicit command"):
            self.coordinator.prepare_archive_publication(
                project_root=self.root,
                explicit_command=False,
                approved=True,
            )
        prepared = self.coordinator.prepare_archive_publication(
            project_root=self.root,
            explicit_command=True,
            approved=True,
        )
        self.assertEqual("ARCHIVE_PUBLICATION_PREPARED", prepared["status"])
        self.assertEqual("NOT_PERFORMED", prepared["git_publication"])
        self.assertTrue(prepared["separate_git_approval_required"])


if __name__ == "__main__":
    unittest.main()
