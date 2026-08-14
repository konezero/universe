from __future__ import annotations

import hashlib
from io import StringIO
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from agent_session_gateway import AgentSessionError  # noqa: E402
from claude_resident_session import ClaudeResidentError  # noqa: E402
from project_master_bridge import (  # noqa: E402
    MASTER_BRIDGE_ENVELOPE_SCHEMA,
    ProjectMasterBridgeError,
)
from project_seed_apply import build_project_seed_asset_approval  # noqa: E402
from project_seed_assets import build_project_seed_asset_proposal  # noqa: E402
from project_integration_apply import build_project_integration_approval  # noqa: E402
from project_integration_catalog import build_project_integration_proposal  # noqa: E402
from project_skill_plan_apply import build_project_skill_plan_approval  # noqa: E402
from project_master_host import (  # noqa: E402
    ClaudeProjectMasterRuntime,
    CodexProjectMasterRuntime,
    GrokProjectMasterRuntime,
    LiveProjectMasterBridgeHost,
    ProjectMasterHostError,
    ProjectMasterConversationWorker,
    ProjectModeCoordinator,
    _WindowsKillOnCloseJob,
    ProjectMasterSessionStore,
    ResidentModeSessionHost,
    ResidentProjectMasterHostManager,
    ResidentRoomParticipantHostManager,
    _default_state_db,
    _project_master_system_prompt,
)
from windows_native_cli import NativeCliResult  # noqa: E402
from session_supervisor import SessionSupervisorError, SessionSupervisorStore  # noqa: E402


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _skill_plan_handoff() -> dict[str, Any]:
    adoption: dict[str, Any] = {
        "schema": "universe.project-skill-plan-adoption.v1",
        "project_id": "GCS",
        "proposal_id": "skillplan_host_test",
        "proposal_digest": "1" * 64,
        "context_pack_id": "context_host_test",
        "selected_candidates": [
            {
                "candidate_id": "skill_candidate_host_test",
                "skill": {
                    "skill_id": "source-review",
                    "skill_version": "1.0.0",
                    "operation_class": "READ",
                    "context_pack_digest": "2" * 64,
                },
                "model_ref": "provider://OPENAI/model/gpt-test",
                "provider_ref": "OPENAI",
            }
        ],
        "binding_state": "PROJECT_MASTER_BINDING_REQUIRED",
        "effects": {
            "project_source_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
        "next_operation": "PROJECT_MASTER_HANDOFF_CANDIDATE",
    }
    adoption["selection_digest"] = _digest(adoption)
    adoption["adoption_id"] = "skilladopt_" + adoption["selection_digest"][:24]
    adoption["status"] = "SKILL_PLAN_ADOPTED"
    handoff: dict[str, Any] = {
        "schema": "universe.project-master-handoff.v1",
        "project_id": "GCS",
        "source": {"kind": "SKILL_PLAN", "adoption": adoption},
        "delivery_state": "PROPOSAL_ONLY",
        "effects": {
            "project_source_write": "NONE",
            "project_runtime_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "task_frame": "NONE",
        },
        "next_operation": "USER_APPROVAL_REQUIRED_FOR_MASTER_DELIVERY",
    }
    handoff["handoff_digest"] = _digest(handoff)
    handoff["handoff_id"] = "handoff_" + handoff["handoff_digest"][:24]
    handoff["status"] = "PROJECT_MASTER_HANDOFF_PROPOSAL_READY"
    return handoff


class FakeProvider:
    session_ref = "fake-provider:session"

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def reply(self, message: Mapping[str, Any]) -> str:
        self.messages.append(dict(message))
        return "Project Master answer"


class StreamingFakeProvider(FakeProvider):
    def reply_stream(self, message: Mapping[str, Any], on_delta) -> str:
        self.messages.append(dict(message))
        on_delta("Project ")
        on_delta("Master answer")
        return "Project Master answer"


class FailingStreamingFakeProvider(FakeProvider):
    def reply_stream(self, message: Mapping[str, Any], on_delta) -> str:
        self.messages.append(dict(message))
        raise RuntimeError("provider disconnected")


class PermissionFakeProvider(StreamingFakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.requester = None
        self.selected_option = None

    def set_permission_requester(self, requester) -> None:
        self.requester = requester

    def reply_stream(self, message: Mapping[str, Any], on_delta) -> str:
        self.messages.append(dict(message))
        assert self.requester is not None
        self.selected_option = self.requester(
            {
                "schema": "universe.agent-permission-request.v1",
                "request_id": "permission_worker_001",
                "provider": "GROK",
                "session_id": "session-001",
                "tool_call": {"toolCallId": "tool-001"},
                "options": [
                    {
                        "optionId": "allow-once",
                        "name": "Allow once",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                ],
            }
        )
        on_delta("Approved answer")
        return "Approved answer"


class PreparedFakeProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.session_ref = "fake-provider:pending"
        self.prepare_count = 0
        self.permission_requester = None
        self.closed = False

    def set_permission_requester(self, requester) -> None:
        self.permission_requester = requester

    def prepare_session(self) -> None:
        self.prepare_count += 1
        self.session_ref = "fake-provider:actual-session"

    def close(self) -> None:
        self.closed = True


class StreamingPreparedFakeProvider(PreparedFakeProvider):
    def reply_stream(self, message: Mapping[str, Any], on_delta) -> str:
        self.messages.append(dict(message))
        on_delta("live ")
        on_delta("answer")
        return "live answer"


class RoomPermissionFakeProvider(StreamingPreparedFakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.permission_requester = None
        self.permission_requested = threading.Event()
        self.selected_option: str | None = None

    def set_permission_requester(self, requester) -> None:
        self.permission_requester = requester

    def reply_stream(self, message: Mapping[str, Any], on_delta) -> str:
        self.messages.append(dict(message))
        assert self.permission_requester is not None
        self.selected_option = self.permission_requester(
            {
                "schema": "universe.agent-permission-request.v1",
                "request_id": "permission_room_001",
                "provider": "CLAUDE",
                "session_id": self.session_ref,
                "tool_call": {
                    "toolCallId": "tool-room-001",
                    "title": "Inspect repository status",
                },
                "options": [
                    {
                        "optionId": "allow-once",
                        "name": "Allow once",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                ],
            }
        )
        self.permission_requested.set()
        on_delta("permission resolved")
        return "permission resolved"


class FakeContinuityCoordinator:
    def __init__(self) -> None:
        self.saves: list[dict[str, Any]] = []
        self.dirty_ends: list[dict[str, Any]] = []

    def save(self, **values) -> Mapping[str, Any]:
        self.saves.append(dict(values))
        return {"status": "AUTO_CONTINUITY_SAVED"}

    def mark_dirty_end(self, project_root: Path, reason: str) -> Mapping[str, Any]:
        value = {"project_root": project_root, "reason": reason}
        self.dirty_ends.append(value)
        return {"status": "DIRTY_END", **value}


class FakeAgentGateway:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: list[str] = []
        self.closed = False
        self.permission_requester = None

    @property
    def session_ref(self) -> str:
        return "fake-acp:session"

    def reply_stream(self, prompt: str, on_delta) -> str:
        self.prompts.append(prompt)
        on_delta(self.answer)
        return self.answer

    def set_permission_requester(self, requester) -> None:
        self.permission_requester = requester

    def close(self) -> None:
        self.closed = True


class FakeSurfaceObserver:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, Any]] = []
        self.room_events: list[dict[str, Any]] = []
        self.prepare_count = 0
        self.mutations: list[dict[str, Any]] = []

    def prepare(self) -> Mapping[str, Any]:
        self.prepare_count += 1
        return {"status": "SESSION_PREPARED"}

    def observe(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        self.messages.append(dict(message))
        if self.fail:
            raise ProjectMasterHostError("PROJECT_COMMANDER_SURFACE_OBSERVATION_FAILED")
        return {
            "status": "COMMANDER_INPUT_OBSERVED",
            "anchor_mode": "MASTER",
            "snapshot": {
                "anchor_id": "MASTER-CURRENT-TEST",
                "observed_at": "2026-07-30T00:00:01Z",
                "snapshot": {
                    "coordinates": {
                        "mode": "MASTER",
                        "commander_surface": "UNIVERSE_UI",
                    }
                },
            },
        }

    def observe_room_event(self, event: Mapping[str, Any]) -> Mapping[str, Any]:
        self.room_events.append(dict(event))
        if self.fail:
            raise ProjectMasterHostError("PROJECT_COMMANDER_SURFACE_OBSERVATION_FAILED")
        return {
            "status": "COMMANDER_INPUT_OBSERVED",
            "anchor_mode": "MASTER",
            "snapshot": {
                "anchor_id": "MASTER-CURRENT-ROOM-TEST",
                "observed_at": "2026-08-09T00:00:01Z",
                "snapshot": {
                    "coordinates": {
                        "mode": "MASTER",
                        "commander_surface": "UNIVERSE_UI",
                    }
                },
            },
        }

    def apply_file(
        self,
        *,
        target: Path,
        content: bytes,
        operation: str,
        boundary: str,
        approval_evidence_ref: str,
        request_ref: str,
        write_roots: tuple[Path, ...] | None = None,
        task_summary: str = "Apply one approved Universe Project Seed asset",
    ) -> Mapping[str, Any]:
        self.mutations.append(
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
            "receipt_id": f"permit-{len(self.mutations)}",
        }


class ProjectMasterHostTests(unittest.TestCase):
    @staticmethod
    def _selected_release() -> dict[str, str]:
        return {
            "status": "SELECTED",
            "release_id": "core-test",
            "source_repository": "fixture/universe-private",
            "source_commit": "b" * 40,
            "database_sha256": "c" * 64,
        }

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".ai" / "master" / "inbox").mkdir(parents=True)
        self.state = ProjectMasterSessionStore(self.root / "state.sqlite", "GCS")
        self.provider = FakeProvider()
        self.surface_observer = FakeSurfaceObserver()
        self.replies: list[dict[str, Any]] = []
        self.streams: list[dict[str, Any]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_runtime_job_kills_children_when_host_handle_closes(self) -> None:
        class FakeProcess:
            _handle = 123

        class FakeKernel:
            def __init__(self) -> None:
                self.closed = 0
                self.assigned = []

            def CreateJobObjectW(self, _security, _name):
                return 456

            def SetInformationJobObject(self, *_args):
                return 1

            def AssignProcessToJobObject(self, handle, process_handle):
                self.assigned.append((handle, process_handle))
                return 1

            def CloseHandle(self, _handle):
                self.closed += 1

        kernel = FakeKernel()
        with patch("project_master_host.os.name", "nt"), patch(
            "process_identity.ctypes.WinDLL", return_value=kernel
        ):
            job = _WindowsKillOnCloseJob(FakeProcess())
            job.close()
            job.close()

        self.assertEqual([(456, 123)], kernel.assigned)
        self.assertEqual(1, kernel.closed)

    def test_provider_session_store_keeps_one_last_coordinate(self) -> None:
        self.assertIsNone(self.state.last_provider_session())
        self.assertEqual(
            "NEW",
            self.state.observe_provider_session("GROK", "grok-session-1"),
        )
        self.assertEqual(
            {
                "provider": "GROK",
                "session_ref": "grok-session-1",
            },
            self.state.last_provider_session(),
        )
        self.assertEqual(
            "REUSED",
            self.state.observe_provider_session("GROK", "grok-session-1"),
        )
        self.assertEqual(
            "REPLACED",
            self.state.observe_provider_session("CODEX", "codex-session-1"),
        )
        self.assertIsNone(self.state.session_ref_for("GROK"))
        self.assertEqual("codex-session-1", self.state.session_ref_for("CODEX"))

    def test_provider_sessions_move_to_neutral_supervisor_default(self) -> None:
        supervisor = SessionSupervisorStore(self.root / "universe.sqlite3")
        state = ProjectMasterSessionStore(
            self.root / "project-state.sqlite",
            "GCS",
            session_supervisor=supervisor,
            requested_mode="MASTER",
        )
        self.assertEqual("NEW", state.observe_provider_session("GROK", "grok-1"))
        anchored = state.observe_current_anchor("MASTER-CURRENT-GCS")
        self.assertIsNotNone(anchored)
        self.assertEqual(
            "REPLACED", state.observe_provider_session("CODEX", "codex-1")
        )
        sessions = supervisor.list_sessions(node="GCS", mode="MASTER")
        self.assertEqual(1, len(sessions))
        self.assertEqual(
            ["CODEX"], [item["provider"] for item in sessions if item["is_default"]]
        )
        self.assertEqual(2, len(sessions[0]["binding_history"]))
        self.assertEqual("MASTER-CURRENT-GCS", sessions[0]["anchor_ref"])
        self.assertEqual("CURRENT", sessions[0]["currentness"])
        self.assertEqual("ATTACHED", sessions[0]["current_activity_state"])
        event_types = {
            event["event_type"] for event in supervisor.list_events(limit=20)
        }
        self.assertIn("PROVIDER_SESSION_ATTACHED", event_types)
        self.assertEqual("codex-1", state.session_ref_for("CODEX"))
        self.assertIsNone(state.session_ref_for("GROK"))

    def test_provider_rebind_uses_identity_owner_after_stale_default_pointer(self) -> None:
        supervisor = SessionSupervisorStore(self.root / "stale-pointer.sqlite3")
        state = ProjectMasterSessionStore(
            self.root / "project-state.sqlite",
            "universe",
            session_supervisor=supervisor,
            requested_mode="CONDUCTOR",
        )
        session, _ = supervisor.register_session(
            {
                "session_id": "session-conductor-actual",
                "node": "universe",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "codex-thread",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
            }
        )
        supervisor.set_default(
            session["session_id"],
            expected_pointer_version=session["default_pointer_version"],
        )
        connection = sqlite3.connect(self.root / "stale-pointer.sqlite3")
        try:
            connection.execute(
                """
                UPDATE target_default_session
                SET node = 'CONDUCTOR'
                WHERE node = 'universe' AND mode = 'CONDUCTOR'
                """
            )
            connection.commit()
        finally:
            connection.close()

        state.observe_provider_session("CODEX", "codex-thread")

        current = next(
            item
            for item in supervisor.list_sessions(node="universe", mode="CONDUCTOR")
            if item["is_default"]
        )
        self.assertEqual("session-conductor-actual", current["session_id"])
        self.assertEqual("codex-thread", current["provider_session_ref"])

    def test_provider_activity_uses_provider_binding_not_deterministic_session_id(
        self,
    ) -> None:
        supervisor = SessionSupervisorStore(self.root / "activity-pointer.sqlite3")
        state = ProjectMasterSessionStore(
            self.root / "project-state.sqlite",
            "universe",
            session_supervisor=supervisor,
            requested_mode="CONDUCTOR",
        )
        session, _ = supervisor.register_session(
            {
                "session_id": "session-conductor-activity",
                "node": "universe",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "codex-thread",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
            }
        )
        supervisor.set_default(
            session["session_id"],
            expected_pointer_version=session["default_pointer_version"],
        )
        connection = sqlite3.connect(self.root / "activity-pointer.sqlite3")
        try:
            connection.execute(
                """
                UPDATE target_default_session
                SET node = 'CONDUCTOR'
                WHERE node = 'universe' AND mode = 'CONDUCTOR'
                """
            )
            connection.commit()
        finally:
            connection.close()

        observed = state.observe_session_activity(
            "CODEX",
            "codex-app-server:codex-thread",
            event_type="COMMANDER_MESSAGE_OBSERVED",
            activity_state="ACTIVE",
            evidence_ref="universe://test/activity",
        )

        self.assertIsNotNone(observed)
        self.assertEqual(
            "SESSION_ACTIVITY_OBSERVED",
            observed["observation"]["status"],
        )
        self.assertEqual("session-conductor-activity", observed["session_id"])

    def test_legacy_binding_migration_uses_identity_owner_session(self) -> None:
        supervisor = SessionSupervisorStore(self.root / "migration-pointer.sqlite3")
        session, _ = supervisor.register_session(
            {
                "session_id": "session-conductor-migration",
                "node": "universe",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "codex-thread",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
            }
        )
        supervisor.set_default(
            session["session_id"],
            expected_pointer_version=session["default_pointer_version"],
        )
        connection = sqlite3.connect(self.root / "migration-pointer.sqlite3")
        try:
            connection.execute(
                """
                UPDATE target_default_session
                SET node = 'CONDUCTOR'
                WHERE node = 'universe' AND mode = 'CONDUCTOR'
                """
            )
            connection.commit()
        finally:
            connection.close()

        state_database = self.root / "migration-state.sqlite"
        legacy = ProjectMasterSessionStore(state_database, "universe")
        with legacy._connection() as legacy_connection:
            legacy_connection.execute(
                "INSERT OR REPLACE INTO host_metadata(key, value) VALUES(?, ?)",
                ("last_provider", "CODEX"),
            )
            legacy_connection.execute(
                "INSERT OR REPLACE INTO host_metadata(key, value) VALUES(?, ?)",
                ("last_session_ref", "codex-thread"),
            )

        ProjectMasterSessionStore(
            state_database,
            "universe",
            session_node="universe",
            session_supervisor=supervisor,
            requested_mode="CONDUCTOR",
        )

        current = next(
            item
            for item in supervisor.list_sessions(node="universe", mode="CONDUCTOR")
            if item["is_default"]
        )
        self.assertEqual("session-conductor-migration", current["session_id"])

    def test_project_master_prompt_inherits_primary_approval_for_task_frame(self) -> None:
        prompt = _project_master_system_prompt("Project Master")
        self.assertIn("not a second Commander decision", prompt)
        self.assertIn("same commander_surface and evidence_ref", prompt)
        self.assertIn("scope or boundary changes", prompt)

    def test_legacy_provider_coordinate_is_migrated_without_deletion(self) -> None:
        database = self.root / "legacy-state.sqlite"
        legacy = ProjectMasterSessionStore(database, "GCS")
        with legacy._connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO host_metadata(key, value) VALUES(?, ?)",
                ("provider_session_id:CLAUDE", "claude-legacy"),
            )
            connection.execute(
                "INSERT OR REPLACE INTO host_metadata(key, value) VALUES(?, ?)",
                ("provider_session_initialized:CLAUDE", "true"),
            )
        supervisor = SessionSupervisorStore(self.root / "universe.sqlite3")
        migrated = ProjectMasterSessionStore(
            database,
            "GCS",
            session_supervisor=supervisor,
        )
        self.assertEqual(
            {"provider": "CLAUDE", "session_ref": "claude-legacy"},
            migrated.last_provider_session(),
        )
        connection = sqlite3.connect(database)
        try:
            keys = {
                row[0]
                for row in connection.execute(
                    "SELECT key FROM host_metadata WHERE key LIKE 'provider_session_%'"
                )
            }
        finally:
            connection.close()
        self.assertIn("provider_session_id:CLAUDE", keys)
        self.assertIn("provider_session_initialized:CLAUDE", keys)

    def test_project_master_greets_only_new_provider_session(self) -> None:
        prompts: list[str] = []

        class FakeSession:
            def __init__(self, *, session_id, session_observer, **_kwargs) -> None:
                self.session_id = session_id or "grok-session-new"
                self.session_ref = f"grok-acp:{self.session_id}"
                session_observer(self.session_id)

            def close(self) -> None:
                return

        class CapturingGateway:
            def __init__(self, session) -> None:
                self.session = session
                self.session_ref = session.session_ref

            def reply_stream(self, prompt, on_delta) -> str:
                prompts.append(prompt)
                on_delta("ok")
                return "ok"

            def close(self) -> None:
                self.session.close()

        message = {
            "message_id": "message-1",
            "kind": "QUESTION",
            "sender": "UNIVERSE_CONDUCTOR",
            "body": "status?",
        }
        with (
            patch(
                "project_master_host._resolve_grok",
                return_value=(self.root / "grok.exe", {}, "grok-build"),
            ),
            patch("project_master_host.GrokAcpSession", FakeSession),
            patch("project_master_host.UniverseAcpGateway", CapturingGateway),
        ):
            first = GrokProjectMasterRuntime(self.root, "GCS", self.state)
            first.set_permission_requester(lambda _request: None)
            first.reply(message)
            first.reply(message)
            first.close()

            resumed = GrokProjectMasterRuntime(self.root, "GCS", self.state)
            resumed.set_permission_requester(lambda _request: None)
            resumed.reply(message)
            resumed.close()

        self.assertIn("Enter MASTER Mode", prompts[0])
        self.assertIn("Project Room message is the current work request", prompts[0])
        self.assertIn("status?", prompts[0])
        self.assertNotIn("Enter MASTER Mode", prompts[1])
        self.assertNotIn("Enter MASTER Mode", prompts[2])

    def test_mode_greeting_is_not_repeated_after_failed_first_turn(self) -> None:
        prompts: list[str] = []

        class FakeSession:
            def __init__(self, *, session_id, session_observer, **_kwargs) -> None:
                self.session_id = session_id or "grok-session-new"
                self.session_ref = f"grok-acp:{self.session_id}"
                session_observer(self.session_id)

            def close(self) -> None:
                return

        class FailingOnceGateway:
            def __init__(self, session) -> None:
                self.session = session
                self.session_ref = session.session_ref
                self.calls = 0

            def reply_stream(self, prompt, on_delta) -> str:
                self.calls += 1
                prompts.append(prompt)
                if self.calls == 1:
                    raise AgentSessionError("TURN_FAILED")
                on_delta("ok")
                return "ok"

            def close(self) -> None:
                self.session.close()

        message = {
            "message_id": "message-1",
            "kind": "QUESTION",
            "sender": "UNIVERSE_CONDUCTOR",
            "body": "status?",
        }
        with (
            patch(
                "project_master_host._resolve_grok",
                return_value=(self.root / "grok.exe", {}, "grok-build"),
            ),
            patch("project_master_host.GrokAcpSession", FakeSession),
            patch("project_master_host.UniverseAcpGateway", FailingOnceGateway),
        ):
            runtime = GrokProjectMasterRuntime(self.root, "GCS", self.state)
            runtime.set_permission_requester(lambda _request: None)
            with self.assertRaisesRegex(ProjectMasterHostError, "TURN_FAILED"):
                runtime.reply(message)
            runtime.reply(message)
            runtime.close()

        self.assertIn("Enter MASTER Mode", prompts[0])
        self.assertNotIn("Enter MASTER Mode", prompts[1])

    def test_resident_mode_session_switches_provider_without_parallel_map(self) -> None:
        created: list[tuple[str, str, str, PreparedFakeProvider]] = []

        def factory(
            provider,
            _root,
            _target,
            _store,
            requested_mode,
            actor_label,
        ):
            instance = PreparedFakeProvider()
            created.append((provider, requested_mode, actor_label, instance))
            return instance

        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor.sqlite",
            actor_label="Universe Conductor",
            provider_factory=factory,
        )
        try:
            host.reply("GROK", self._envelope()["message"])
            host.reply("GROK", self._envelope()["message"])
            host.reply("CODEX", self._envelope()["message"])
        finally:
            host.close()

        self.assertEqual(["GROK", "CODEX"], [item[0] for item in created])
        self.assertEqual(["CONDUCTOR", "CONDUCTOR"], [item[1] for item in created])
        self.assertTrue(created[0][3].closed)
        self.assertTrue(created[1][3].closed)

    def test_resident_mode_session_replaces_changed_provider_profile(self) -> None:
        created: list[PreparedFakeProvider] = []

        def factory(*_args):
            instance = PreparedFakeProvider()
            created.append(instance)
            return instance

        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-profile.sqlite",
            actor_label="Universe Conductor",
            provider_factory=factory,
        )
        try:
            first = host.prepare("CODEX", model="gpt-a", effort="HIGH")
            host.prepare("CODEX", model="gpt-a", effort="HIGH")
            second = host.prepare("CODEX", model="gpt-b", effort="MAX")
        finally:
            host.close()

        self.assertEqual(2, len(created))
        self.assertTrue(created[0].closed)
        self.assertEqual("gpt-a", first["model_ref"])
        self.assertEqual("HIGH", first["effort"])
        self.assertEqual("gpt-b", second["model_ref"])
        self.assertEqual("MAX", second["effort"])

    def test_resident_mode_session_can_start_new_thread_for_same_provider(self) -> None:
        created: list[PreparedFakeProvider] = []

        def factory(*_args):
            instance = PreparedFakeProvider()
            created.append(instance)
            return instance

        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-new-session.sqlite",
            actor_label="Universe Conductor",
            provider_factory=factory,
        )
        try:
            host.prepare("CODEX", model="gpt-a", effort="HIGH")
            host.prepare(
                "CODEX",
                model="gpt-a",
                effort="HIGH",
                session_action="NEW",
            )
        finally:
            host.close()

        self.assertEqual(2, len(created))
        self.assertTrue(created[0].closed)
        self.assertTrue(created[1].closed)

    def test_invalid_session_action_is_rejected(self) -> None:
        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-invalid-session-action.sqlite",
            actor_label="Universe Conductor",
            provider_factory=lambda *_args: PreparedFakeProvider(),
        )
        try:
            with self.assertRaisesRegex(
                ProjectMasterHostError,
                "MODE_SESSION_ACTION_INVALID",
            ):
                host.prepare("CODEX", session_action="FORK")
        finally:
            host.close()

    def test_resident_mode_session_records_commander_and_provider_observations(
        self,
    ) -> None:
        supervisor = SessionSupervisorStore(self.root / "observer-supervisor.sqlite3")
        provider = PreparedFakeProvider()
        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-observer.sqlite",
            actor_label="Universe Conductor",
            session_supervisor=supervisor,
            provider_factory=lambda *_args: provider,
        )
        try:
            host.reply("CODEX", self._envelope()["message"])
        finally:
            host.close()

        sessions = supervisor.list_sessions(node="CONDUCTOR", mode="CONDUCTOR")
        self.assertEqual(1, len(sessions))
        self.assertEqual("CURRENT", sessions[0]["currentness"])
        self.assertEqual("COMPLETED", sessions[0]["current_activity_state"])
        events = [
            item["event_type"] for item in supervisor.list_events(limit=20)
        ]
        self.assertIn("COMMANDER_MESSAGE_OBSERVED", events)
        self.assertIn("PROVIDER_REPLY_OBSERVED", events)

    def test_resident_mode_session_tracks_provider_process_lease_lifecycle(self) -> None:
        supervisor = SessionSupervisorStore(self.root / "conductor-provider-lease.sqlite3")

        class LeasePreparedProvider(PreparedFakeProvider):
            def supervisor_process_identity(
                self, endpoint: str, handshake_token: str
            ) -> dict[str, Any]:
                return {
                    "pid": 4322,
                    "process_created_at": "2026-08-14T00:00:00Z",
                    "executable": "C:\\fake\\provider.exe",
                    "command": ["C:\\fake\\provider.exe", "stdio"],
                    "endpoint": endpoint,
                    "handshake_fingerprint": hashlib.sha256(
                        handshake_token.encode("utf-8")
                    ).hexdigest(),
                }

        provider = LeasePreparedProvider()
        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-provider-lease.sqlite",
            actor_label="Universe Conductor",
            session_supervisor=supervisor,
            supervisor_endpoint="http://127.0.0.1:52973",
            provider_factory=lambda *_args: provider,
        )
        try:
            host.prepare("CODEX")
            live = next(
                item
                for item in supervisor.list_sessions(
                    node="CONDUCTOR", mode="CONDUCTOR"
                )
                if item["is_default"]
            )
            self.assertEqual("LIVE", live["state"])
            self.assertEqual("OWNED", live["process_lease"]["lease_state"])
            self.assertEqual(
                4322,
                live["process_lease"]["process_identity"]["pid"],
            )
        finally:
            host.close()

        closed = supervisor.get_session(live["session_id"])
        self.assertEqual("DISCONNECTED", closed["state"])
        self.assertEqual("STALE", closed["process_lease"]["lease_state"])
        self.assertTrue(provider.closed)

    def test_resident_mode_session_rebinds_lease_after_provider_process_replacement(
        self,
    ) -> None:
        supervisor = SessionSupervisorStore(self.root / "provider-replacement-lease.sqlite3")

        class ReplacingLeaseProvider(PreparedFakeProvider):
            def __init__(self) -> None:
                super().__init__()
                self.pid = 4322

            def supervisor_process_identity(
                self, endpoint: str, handshake_token: str
            ) -> dict[str, Any]:
                return {
                    "pid": self.pid,
                    "process_created_at": f"2026-08-14T00:00:{self.pid - 4322:02d}Z",
                    "executable": "C:\\fake\\provider.exe",
                    "command": ["C:\\fake\\provider.exe", "stdio"],
                    "endpoint": endpoint,
                    "handshake_fingerprint": hashlib.sha256(
                        handshake_token.encode("utf-8")
                    ).hexdigest(),
                }

        provider = ReplacingLeaseProvider()
        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-provider-replacement.sqlite",
            actor_label="Universe Conductor",
            session_supervisor=supervisor,
            supervisor_endpoint="http://127.0.0.1:52974",
            provider_factory=lambda *_args: provider,
        )
        try:
            with patch.object(supervisor, "sweep_stale_live_sessions", return_value={}):
                host.prepare("CODEX")
                provider.pid = 4323
                host.prepare("CODEX")
            live = next(
                item
                for item in supervisor.list_sessions(
                    node="CONDUCTOR", mode="CONDUCTOR"
                )
                if item["is_default"]
            )
            self.assertEqual("OWNED", live["process_lease"]["lease_state"])
            self.assertEqual(4323, live["process_lease"]["process_identity"]["pid"])
            events = [item["event_type"] for item in supervisor.list_events(limit=20)]
            self.assertIn("PROCESS_LEASE_STALE", events)
            self.assertIn("PROCESS_LEASE_ACQUIRED", events)
        finally:
            host.close()

    def test_resident_mode_session_streams_current_turn_through_native_provider(
        self,
    ) -> None:
        provider = StreamingPreparedFakeProvider()
        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-stream.sqlite",
            actor_label="Universe Conductor",
            provider_factory=lambda *_args: provider,
        )
        deltas: list[str] = []
        try:
            result = host.reply_stream(
                "CODEX",
                {
                    "message_id": "current-only",
                    "body": "current input",
                    "runtime_context": {"requested_mode": "CONDUCTOR"},
                },
                deltas.append,
            )
        finally:
            host.close()

        self.assertEqual(["live ", "answer"], deltas)
        self.assertEqual("live answer", result["text"])
        self.assertEqual("current input", provider.messages[0]["body"])
        self.assertNotIn("history", provider.messages[0]["runtime_context"])

    def test_resident_mode_session_binds_declared_permission_requester(self) -> None:
        provider = PreparedFakeProvider()

        def requester(_request: Mapping[str, Any]) -> str:
            return "allow-once"

        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-permission.sqlite",
            actor_label="Universe Conductor",
            permission_requester=requester,
            provider_factory=lambda *_args: provider,
        )
        try:
            host.prepare("CODEX")
            self.assertIs(requester, provider.permission_requester)
            self.assertEqual("allow-once", provider.permission_requester({}))
        finally:
            host.close()

    def test_resident_mode_session_reopens_when_default_session_changes(self) -> None:
        supervisor = SessionSupervisorStore(self.root / "supervisor.sqlite3")
        first, _ = supervisor.register_session(
            {
                "session_id": "session-conductor-one",
                "node": "CONDUCTOR",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "thread-one",
                "state": "DISCONNECTED",
            }
        )
        supervisor.set_default(
            first["session_id"],
            expected_pointer_version=first["default_pointer_version"],
        )
        created: list[PreparedFakeProvider] = []

        def factory(provider, _root, _target, store, _mode, _actor):
            instance = PreparedFakeProvider()
            instance.session_id = store.session_ref_for(provider)
            instance.session_ref = f"codex-app-server:{instance.session_id}"
            created.append(instance)
            return instance

        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-selected.sqlite",
            actor_label="Universe Conductor",
            session_supervisor=supervisor,
            provider_factory=factory,
        )
        try:
            host.prepare("CODEX")
            second, _ = supervisor.register_session(
                {
                    "session_id": "session-conductor-two",
                    "node": "CONDUCTOR",
                    "mode": "CONDUCTOR",
                    "provider": "CODEX",
                    "provider_session_ref": "thread-two",
                    "state": "DISCONNECTED",
                }
            )
            supervisor.set_default(
                second["session_id"],
                expected_pointer_version=second["default_pointer_version"],
            )
            host.prepare("CODEX")
        finally:
            host.close()

        self.assertEqual(2, len(created))
        self.assertEqual("thread-one", created[0].session_id)
        self.assertEqual("thread-two", created[1].session_id)
        self.assertTrue(created[0].closed)

    def test_resident_mode_session_saves_idle_and_preserves_provider_on_close(
        self,
    ) -> None:
        continuity = FakeContinuityCoordinator()

        def factory(provider, _root, _target, _store, _mode, _actor):
            instance = PreparedFakeProvider()
            instance.session_ref = f"{provider.lower()}:session"
            return instance

        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-continuity.sqlite",
            actor_label="Universe Conductor",
            continuity_coordinator=continuity,
            provider_factory=factory,
        )
        host.reply("GROK", self._envelope()["message"])
        host.save_idle(0)
        host.close()

        self.assertEqual(["IDLE", "NORMAL_STOP"], [item["trigger"] for item in continuity.saves])
        closing = json.loads(continuity.saves[-1]["compressed_context"])
        self.assertEqual("GROK", closing["provider"])

    def test_resident_mode_session_records_dirty_end_when_provider_reply_fails(
        self,
    ) -> None:
        continuity = FakeContinuityCoordinator()

        class FailingProvider(PreparedFakeProvider):
            def reply(self, message: Mapping[str, Any]) -> str:
                raise ProjectMasterHostError("PROVIDER_DIED")

        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-failure.sqlite",
            actor_label="Universe Conductor",
            continuity_coordinator=continuity,
            provider_factory=lambda *_args: FailingProvider(),
        )
        try:
            with self.assertRaisesRegex(ProjectMasterHostError, "PROVIDER_DIED"):
                host.reply("GROK", self._envelope()["message"])
        finally:
            host.close()
        self.assertEqual(1, len(continuity.dirty_ends))
        self.assertIn("GROK_REPLY_FAILED", continuity.dirty_ends[0]["reason"])

    def test_resident_mode_session_checkpoints_quota_without_replacing_session(
        self,
    ) -> None:
        continuity = FakeContinuityCoordinator()

        class QuotaProvider(PreparedFakeProvider):
            def reply(self, message: Mapping[str, Any]) -> str:
                raise ProjectMasterHostError("CLAUDE_QUOTA_EXHAUSTED")

            def runtime_observation(self) -> dict[str, Any]:
                return {
                    "schema": "universe.provider-runtime-observation.v1",
                    "provider": "CLAUDE",
                    "session_ref": self.session_ref,
                    "state": "QUOTA_EXHAUSTED",
                    "quota_state": "EXHAUSTED",
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                }

        provider = QuotaProvider()
        host = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / "conductor-quota.sqlite",
            actor_label="Universe Conductor",
            continuity_coordinator=continuity,
            provider_factory=lambda *_args: provider,
        )
        try:
            with self.assertRaisesRegex(ProjectMasterHostError, "QUOTA_EXHAUSTED"):
                host.reply("CLAUDE", self._envelope()["message"])
            self.assertIs(provider, host._provider)
            self.assertFalse(provider.closed)
            self.assertEqual("EXHAUSTED", host.status()["runtime_observation"]["quota_state"])
        finally:
            host.close()
        quota_saves = [
            item for item in continuity.saves if item["trigger"] == "PROVIDER_QUOTA"
        ]
        self.assertEqual(1, len(quota_saves))
        saved_context = json.loads(quota_saves[0]["compressed_context"])
        self.assertEqual("fake-provider:actual-session", saved_context["provider_session_ref"])
        self.assertEqual("EXHAUSTED", saved_context["quota_state"])
        self.assertEqual([], continuity.dirty_ends)

    def test_resident_mode_session_restores_last_coordinate_after_restart(self) -> None:
        serial = {"value": 0}

        class StoreAwareProvider(PreparedFakeProvider):
            def __init__(self, provider, store, requested_mode) -> None:
                super().__init__()
                self.provider = provider
                self.store = store
                self.requested_mode = requested_mode
                self.connection_state = "UNKNOWN"
                self.session_id = store.session_ref_for(provider)

            def prepare_session(self) -> None:
                self.prepare_count += 1
                if self.session_id is None:
                    serial["value"] += 1
                    self.session_id = f"session-{serial['value']}"
                self.connection_state = self.store.observe_provider_session(
                    self.provider,
                    self.session_id,
                )
                self.session_ref = f"{self.provider.lower()}:{self.session_id}"

        def factory(provider, _root, _target, store, mode, _actor):
            return StoreAwareProvider(provider, store, mode)

        database = self.root / "conductor.sqlite"
        first = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            database,
            actor_label="Universe Conductor",
            provider_factory=factory,
        )
        first_status = first.prepare("GROK")
        active_status = first.prepare("GROK")
        first.close()

        reopened = ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            database,
            actor_label="Universe Conductor",
            provider_factory=factory,
        )
        reused_status = reopened.prepare("GROK")
        replaced_status = reopened.prepare("CODEX")
        reopened.close()

        self.assertEqual("NEW", first_status["connection_state"])
        self.assertEqual("REUSED", active_status["connection_state"])
        self.assertEqual(
            first_status["last_session_ref"],
            active_status["last_session_ref"],
        )
        self.assertEqual("REUSED", reused_status["connection_state"])
        self.assertEqual("REPLACED", replaced_status["connection_state"])
        self.assertEqual("CODEX", replaced_status["last_provider"])
        self.assertEqual("CONDUCTOR", replaced_status["requested_mode"])
        self.assertNotIn("authority", replaced_status)
        self.assertNotIn("currentness", replaced_status)

    def test_live_bridge_invokes_provider_and_posts_reply_once(self) -> None:
        worker = self._worker()
        host = LiveProjectMasterBridgeHost(
            self.root,
            "bridge-token",
            ".ai/missing-live-inbox",
            worker,
            self.surface_observer,
        )
        worker.start()
        try:
            first = host.record(self._envelope())
            repeated = host.record(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual("ACCEPTED", first["status"])
        self.assertEqual("ALREADY_ACCEPTED", repeated["status"])
        self.assertFalse(first["repository_write"])
        self.assertEqual(
            [],
            list((self.root / ".ai" / "master" / "inbox").glob("universe-room-*.json")),
        )
        self.assertEqual(1, len(self.provider.messages))
        self.assertEqual(1, len(self.surface_observer.messages))
        self.assertEqual(
            "UNIVERSE_UI",
            self.provider.messages[0]["runtime_context"]["commander_surface"],
        )
        self.assertEqual(
            "MASTER-CURRENT-TEST",
            self.provider.messages[0]["runtime_context"]["mode_current_anchor"],
        )
        self.assertEqual(1, len(self.replies))
        self.assertEqual("Project Master answer", self.replies[0]["body"])
        self.assertEqual("COMPLETE", self.state.state(first["message_id"]))

    def test_live_bridge_applies_seed_assets_through_coordinator_gateway(self) -> None:
        worker = self._worker()
        host = LiveProjectMasterBridgeHost(
            self.root,
            "bridge-token",
            ".ai/master/inbox",
            worker,
            self.surface_observer,
        )
        (self.root / ".ai" / "universe").mkdir()
        proposal = build_project_seed_asset_proposal(
            {
                "project_id": "GCS",
                "seed_id": "seed-host-001",
                "seed_digest": "a" * 64,
                "source": {"kind": "TEST", "ref": "test://seed"},
                "project": {"name": "GCS"},
                "nodes": [],
                "edges": [],
                "implementation": {"nodes": []},
                "implementation_bindings": [],
                "documents": [],
            }
        )
        approval = build_project_seed_asset_approval(
            project_id="GCS",
            proposal=proposal,
            evidence_ref="universe://approval/seed-host-001",
        )

        receipt = host.apply_seed_assets(
            {
                "project_id": "GCS",
                "proposal": proposal,
                "approval": approval,
            }
        )

        self.assertEqual("PROJECT_SEED_ASSETS_APPLIED", receipt["status"])
        self.assertEqual(5, len(self.surface_observer.mutations))

    def test_live_bridge_applies_integration_assets_through_coordinator_gateway(
        self,
    ) -> None:
        worker = self._worker()
        host = LiveProjectMasterBridgeHost(
            self.root,
            "bridge-token",
            ".ai/master/inbox",
            worker,
            self.surface_observer,
        )
        proposal = build_project_integration_proposal("GCS", root=ROOT)
        approval = build_project_integration_approval(
            project_id="GCS",
            proposal=proposal,
            project_source_evidence_ref="universe://approval/integration/source-host",
            local_runtime_evidence_ref="universe://approval/integration/runtime-host",
        )

        receipt = host.apply_integration_assets(
            {
                "project_id": "GCS",
                "proposal": proposal,
                "approval": approval,
            }
        )

        self.assertEqual("PROJECT_INTEGRATION_APPLIED", receipt["status"])
        self.assertEqual(5, len(self.surface_observer.mutations))
        self.assertTrue((self.root / ".universe" / "project.json").is_file())

    def test_live_bridge_binds_skill_plan_context_idempotently(self) -> None:
        skill = self.root / ".ai" / "skills" / "common" / "source-review" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: source-review\n---\n\n# Source Review\n",
            encoding="utf-8",
        )
        worker = self._worker()
        host = LiveProjectMasterBridgeHost(
            self.root,
            "bridge-token",
            ".ai/master/inbox",
            worker,
            self.surface_observer,
        )
        handoff = _skill_plan_handoff()
        approval = build_project_skill_plan_approval(
            project_id="GCS",
            handoff=handoff,
            evidence_ref="universe://projects/GCS/skill-plan-approval/host-test",
        )
        request = {
            "project_id": "GCS",
            "handoff": handoff,
            "approval": approval,
        }

        first = host.apply_skill_plan(request)
        repeated = host.apply_skill_plan(request)
        worker.start()
        try:
            host.record(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(
            "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT",
            first["status"],
        )
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(repeated["idempotent_replay"])
        self.assertEqual(1, len(self.state.skill_plan_contexts()))
        context = self.provider.messages[0]["skill_plan_context"][0]
        self.assertEqual("PROJECT_MASTER_CONTEXT_BOUND", context["binding_state"])
        self.assertEqual("UNRESOLVED", context["binding_candidates"][0]["skill_ref"])
        self.assertEqual("NOT_CREATED", context["task_frame_binding"])
        proposal = self.provider.messages[0]["skill_binding_proposals"][0]
        self.assertEqual(
            "PROJECT_SKILL_BINDING_PROPOSAL_READY",
            proposal["status"],
        )
        self.assertEqual(
            ".ai/skills/common/source-review/SKILL.md",
            proposal["skill_bindings"][0]["skill_ref"],
        )
        self.assertFalse(proposal["task_frame_started"])
        self.assertEqual(
            proposal["proposal_id"],
            first["binding_proposal"]["proposal_id"],
        )

    def test_skill_plan_is_not_stored_when_local_skill_is_unavailable(self) -> None:
        worker = self._worker()
        host = LiveProjectMasterBridgeHost(
            self.root,
            "bridge-token",
            ".ai/master/inbox",
            worker,
            self.surface_observer,
        )
        handoff = _skill_plan_handoff()
        approval = build_project_skill_plan_approval(
            project_id="GCS",
            handoff=handoff,
            evidence_ref="universe://projects/GCS/skill-plan-approval/missing-skill",
        )

        with self.assertRaisesRegex(
            ProjectMasterBridgeError,
            "installed Project Skill root is unavailable",
        ):
            host.apply_skill_plan(
                {
                    "project_id": "GCS",
                    "handoff": handoff,
                    "approval": approval,
                }
            )

        self.assertEqual([], self.state.skill_plan_contexts())
        self.assertEqual([], self.state.skill_binding_proposals())

    def test_pending_message_is_recovered_after_host_restart(self) -> None:
        self.assertTrue(self.state.register(self._envelope()))
        worker = self._worker()
        worker.start()
        try:
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(1, len(self.provider.messages))
        self.assertEqual("COMPLETE", self.state.state(self._message_id()))

    def test_pending_message_recovery_rebinds_current_bridge_transport(self) -> None:
        self.assertTrue(self.state.register(self._envelope()))
        worker = self._worker()
        worker.start(
            recovery_bridge_id="bridge_abcdef0123456789abcd",
            recovery_session_ref="grok-acp:resumed-master-session",
        )
        try:
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(1, len(self.replies))
        self.assertEqual("bridge_abcdef0123456789abcd", self.replies[0]["bridge_id"])
        self.assertEqual("COMPLETE", self.state.state(self._message_id()))
        connection = sqlite3.connect(self.state.database_path)
        try:
            row = connection.execute(
                "SELECT envelope_json FROM inbox_message WHERE message_id = ?",
                (self._message_id(),),
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row)
        recovered = json.loads(str(row[0]))
        self.assertEqual("bridge_abcdef0123456789abcd", recovered["bridge_id"])
        self.assertEqual(
            "grok-acp:resumed-master-session",
            recovered["master_session_ref"],
        )

    def test_recovery_rejects_partial_transport_rebinding(self) -> None:
        with self.assertRaisesRegex(
            ProjectMasterHostError, "MASTER_RECOVERY_TRANSPORT_INCOMPLETE"
        ):
            self.state.recover(bridge_id="bridge_abcdef0123456789abcd")

    def test_failed_message_requires_explicit_cancel_and_reregister(self) -> None:
        self.assertTrue(self.state.register(self._envelope()))
        self.assertTrue(self.state.claim(self._message_id()))
        self.state.fail(self._message_id(), "provider timeout")

        replay = self._worker()
        replay.start()
        try:
            self.assertTrue(replay.wait_idle())
        finally:
            replay.close()

        self.assertEqual([], self.provider.messages)
        self.assertEqual("FAILED", self.state.state(self._message_id()))
        self.assertTrue(self.state.cancel(self._message_id()))
        self.assertEqual("CANCELLED", self.state.state(self._message_id()))
        self.assertTrue(self.state.register(self._envelope()))

        retried = self._worker()
        retried.start()
        try:
            self.assertTrue(retried.wait_idle())
        finally:
            retried.close()

        self.assertEqual(1, len(self.provider.messages))
        self.assertEqual("COMPLETE", self.state.state(self._message_id()))

    def test_stale_bridge_reply_failure_is_recovered_on_reprepare(self) -> None:
        self.assertTrue(self.state.register(self._envelope()))
        self.assertTrue(self.state.claim(self._message_id()))
        self.state.fail(
            self._message_id(),
            "ProjectMasterBridgeError: UNIVERSE_REPLY_HTTP_409",
        )

        worker = self._worker()
        worker.start(
            recovery_bridge_id="bridge_abcdef0123456789abcd",
            recovery_session_ref="grok-acp:resumed-master-session",
        )
        try:
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(1, len(self.provider.messages))
        self.assertEqual("COMPLETE", self.state.state(self._message_id()))
        self.assertEqual("bridge_abcdef0123456789abcd", self.replies[0]["bridge_id"])

    def test_provider_start_timeout_is_requeued_only_by_explicit_recovery(self) -> None:
        self.assertTrue(self.state.register(self._envelope()))
        self.assertTrue(self.state.claim(self._message_id()))
        self.state.fail(
            self._message_id(),
            "ProjectMasterHostError: AGENT_RPC_TIMEOUT:session/prompt",
        )

        self.assertEqual(1, self.state.requeue_provider_start_timeouts())
        worker = self._worker()
        worker.start()
        try:
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(1, len(self.provider.messages))
        self.assertEqual("COMPLETE", self.state.state(self._message_id()))

    def test_grok_runtime_routes_project_message_through_acp_gateway(self) -> None:
        runtime = GrokProjectMasterRuntime(
            self.root,
            "GCS",
            self.state,
        )
        gateway = FakeAgentGateway("grok-answer")
        runtime._gateway = gateway

        answer = runtime.reply(self._envelope()["message"])

        self.assertEqual("grok-answer", answer)
        self.assertIn("Universe Project Room message", gateway.prompts[0])
        self.assertTrue(runtime.session_ref.startswith("grok-acp:"))

    def test_codex_runtime_routes_project_message_through_app_server(self) -> None:
        runtime = CodexProjectMasterRuntime(
            self.root,
            "GCS",
            self.state,
        )
        gateway = FakeAgentGateway("codex-answer")
        runtime._gateway = gateway

        answer = runtime.reply(self._envelope()["message"])

        self.assertEqual("codex-answer", answer)
        self.assertIn("Universe Project Room message", gateway.prompts[0])
        self.assertTrue(runtime.session_ref.startswith("codex-app-server:"))

    def test_codex_runtime_replaces_stale_resumed_session_once(self) -> None:
        class FailingGateway(FakeAgentGateway):
            def reply_stream(self, prompt: str, on_delta) -> str:
                del on_delta
                self.prompts.append(prompt)
                raise AgentSessionError("CODEX_TURN_FAILED")

        self.state.observe_provider_session("CODEX", "stale-thread")
        runtime = CodexProjectMasterRuntime(
            self.root,
            "GCS",
            self.state,
        )
        runtime.connection_state = "REUSED"
        failed = FailingGateway("unused")
        recovered = FakeAgentGateway("recovered-answer")
        gateways = iter((failed, recovered))

        def next_gateway():
            gateway = next(gateways)
            runtime._gateway = gateway
            if gateway is recovered:
                runtime.session_id = "fresh-thread"
                runtime.connection_state = "REPLACED"
                runtime._greeting_pending = True
            return gateway

        with patch.object(runtime, "_acp_gateway", side_effect=next_gateway):
            answer = runtime.reply(self._envelope()["message"])

        self.assertEqual("recovered-answer", answer)
        self.assertTrue(failed.closed)
        self.assertEqual(1, len(failed.prompts))
        self.assertEqual(1, len(recovered.prompts))
        self.assertTrue(recovered.prompts[0].startswith("Enter MASTER Mode"))

    def test_claude_runtime_routes_project_message_through_cli_session(self) -> None:
        runtime = ClaudeProjectMasterRuntime(
            self.root,
            "GCS",
            self.state,
        )
        gateway = FakeAgentGateway("claude-answer")
        runtime._gateway = gateway

        answer = runtime.reply(self._envelope()["message"])

        self.assertEqual("claude-answer", answer)
        self.assertIn("Universe Project Room message", gateway.prompts[0])
        self.assertTrue(runtime.session_ref.startswith("claude-code:"))

    def test_claude_runtime_replaces_missing_resumed_session_once(self) -> None:
        class MissingResumeGateway(FakeAgentGateway):
            def reply_stream(self, prompt: str, on_delta) -> str:
                del on_delta
                self.prompts.append(prompt)
                raise ClaudeResidentError("CLAUDE_SESSION_RESUME_NOT_FOUND")

        self.state.observe_provider_session("CLAUDE", "stale-session")
        runtime = ClaudeProjectMasterRuntime(self.root, "GCS", self.state)
        failed = MissingResumeGateway("unused")
        recovered = FakeAgentGateway("recovered-answer")
        gateways = iter((failed, recovered))

        def next_gateway():
            gateway = next(gateways)
            runtime._gateway = gateway
            if gateway is recovered:
                runtime.session_id = "fresh-session"
                runtime.connection_state = "REPLACED"
                runtime._greeting_pending = True
            return gateway

        with patch.object(runtime, "_acp_gateway", side_effect=next_gateway):
            answer = runtime.reply(self._envelope()["message"])

        self.assertEqual("recovered-answer", answer)
        self.assertTrue(failed.closed)
        self.assertEqual(1, len(failed.prompts))
        self.assertEqual(1, len(recovered.prompts))
        self.assertTrue(recovered.prompts[0].startswith("Enter MASTER Mode"))

    def test_claude_runtime_cleans_mcp_config_when_resident_launch_fails(self) -> None:
        runtime = ClaudeProjectMasterRuntime(self.root, "GCS", self.state)
        runtime.set_permission_requester(lambda _request: "allow-once")
        config_root = self.root / "claude-launch-failure"

        with patch(
            "project_master_host._resolve_claude",
            return_value=(Path("claude.exe"), {}, "default"),
        ), patch(
            "project_master_host.tempfile.mkdtemp",
            return_value=str(config_root),
        ), patch(
            "project_master_host.ClaudeResidentSession",
            side_effect=RuntimeError("launch failed"),
        ), self.assertRaises(RuntimeError):
            runtime.prepare_session()

        self.assertFalse(config_root.exists())
        self.assertIsNone(runtime._permission_broker)
        self.assertIsNone(runtime._mcp_config_root)

    def test_resident_provider_permission_requester_is_rebound_after_prepare(
        self,
    ) -> None:
        def requester(_request):
            return "allow-once"

        for runtime_type in (
            GrokProjectMasterRuntime,
            CodexProjectMasterRuntime,
            ClaudeProjectMasterRuntime,
        ):
            with self.subTest(runtime=runtime_type.__name__):
                runtime = runtime_type(self.root, "GCS", self.state)
                gateway = FakeAgentGateway("unused")
                runtime._gateway = gateway

                runtime.set_permission_requester(requester)

                self.assertIs(requester, runtime._permission_requester)
                self.assertIs(requester, gateway.permission_requester)

    def test_streaming_provider_emits_started_deltas_and_completed(self) -> None:
        self.provider = StreamingFakeProvider()
        worker = self._worker()
        worker.start()
        try:
            worker.submit(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(
            ["STARTED", "DELTA", "DELTA", "COMPLETED"],
            [item["event"] for item in self.streams],
        )
        self.assertEqual(
            ["Project ", "Master answer"],
            [item["delta"] for item in self.streams if item["event"] == "DELTA"],
        )
        self.assertEqual("Project Master answer", self.replies[0]["body"])

    def test_native_room_event_sends_only_incremental_input_and_observes_output(
        self,
    ) -> None:
        self.provider = StreamingFakeProvider()
        observed: list[dict[str, Any]] = []
        worker = ProjectMasterConversationWorker(
            provider=self.provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            surface_observer=self.surface_observer,
            reply_poster=lambda **values: self.replies.append(values) or {},
            stream_poster=lambda **values: self.streams.append(values) or {},
            room_event_observer=lambda event: observed.append(dict(event)),
        )
        room_event = {
            "room_id": "room_native",
            "room_event_id": "room_evt_native_001",
            "room_sequence": 7,
            "correlation_id": None,
            "message": {
                "room_event_id": "room_evt_native_001",
                "author_role": "USER",
                "body_text": "Review only this new line",
            },
        }
        worker.start()
        try:
            worker.submit_room_event(
                binding={
                    "binding_id": "bind_native_001",
                    "provider": "CODEX",
                    "provider_session_ref": self.provider.session_ref,
                },
                event=room_event,
                bridge_id="bridge_native_001",
            )
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(1, len(self.provider.messages))
        provider_message = self.provider.messages[0]
        self.assertEqual("Review only this new line", provider_message["body"])
        self.assertEqual("room_evt_native_001", provider_message["message_id"])
        self.assertNotIn("history", provider_message)
        self.assertNotIn("messages", provider_message)
        self.assertNotIn("skill_plan_context", provider_message)
        self.assertEqual([room_event], self.surface_observer.room_events)
        self.assertEqual(
            [
                "DELIVERY_ACCEPTED",
                "DELTA",
                "DELTA",
                "COMPLETED",
            ],
            [event["event"] for event in observed],
        )
        self.assertEqual("Project Master answer", observed[-1]["body"])
        self.assertEqual([], self.replies)
        self.assertEqual([], self.streams)

    def test_native_room_failure_is_uncertain_and_does_not_fake_completion(
        self,
    ) -> None:
        self.provider = FailingStreamingFakeProvider()
        observed: list[dict[str, Any]] = []
        worker = ProjectMasterConversationWorker(
            provider=self.provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            surface_observer=self.surface_observer,
            room_event_observer=lambda event: observed.append(dict(event)),
        )
        worker.start()
        try:
            worker.submit_room_event(
                binding={
                    "binding_id": "bind_native_failure",
                    "provider": "CLAUDE",
                    "provider_session_ref": self.provider.session_ref,
                },
                event={
                    "room_id": "room_native",
                    "room_event_id": "room_evt_native_failure",
                    "room_sequence": 1,
                    "message": {
                        "room_event_id": "room_evt_native_failure",
                        "author_role": "USER",
                        "body_text": "fail closed",
                    },
                },
                bridge_id="bridge_native_failure",
            )
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(["FAILED"], [event["event"] for event in observed])
        self.assertEqual("UNCERTAIN", observed[0]["delivery_status"])
        self.assertIn("provider disconnected", observed[0]["reason"])

    def test_selected_governance_context_is_injected_for_provider(self) -> None:
        selected = {
            "schema": "universe.release-governance-context.v1",
            "status": "SELECTED",
            "release_id": "core-test",
            "selector_digest": "a" * 64,
            "units": [{"governance_id": "CORE", "content": "Use the contract."}],
        }
        worker = ProjectMasterConversationWorker(
            provider=self.provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            surface_observer=self.surface_observer,
            reply_poster=lambda **values: self.replies.append(values) or {},
            stream_poster=lambda **values: self.streams.append(values) or {},
            governance_context_resolver=lambda project_id: (
                selected if project_id == "GCS" else {"status": "ABSENT"}
            ),
        )
        worker.start()
        try:
            worker.submit(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(selected, self.provider.messages[0]["governance_context"])

    def test_startup_governance_snapshot_is_not_reread_per_message(self) -> None:
        selected = {
            "schema": "universe.release-governance-context.v1",
            "status": "SELECTED",
            "release_id": "core-startup",
            "source_commit": "b" * 40,
            "catalog_digest": "c" * 64,
            "selector_digest": "d" * 64,
            "units": [{"governance_id": "CORE", "content": "Use the contract."}],
        }

        def resolver(_project_id: str) -> Mapping[str, Any]:
            raise AssertionError("startup snapshot must not resolve per message")

        worker = ProjectMasterConversationWorker(
            provider=self.provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            surface_observer=self.surface_observer,
            reply_poster=lambda **values: self.replies.append(values) or {},
            stream_poster=lambda **values: self.streams.append(values) or {},
            governance_context_resolver=resolver,
            governance_context=selected,
        )
        worker.start()
        try:
            worker.submit(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(selected, self.provider.messages[0]["governance_context"])

    def test_startup_governance_snapshot_is_injected_for_native_room_event(self) -> None:
        selected = {
            "schema": "universe.release-governance-context.v1",
            "status": "SELECTED",
            "release_id": "core-room",
            "source_commit": "e" * 40,
            "catalog_digest": "f" * 64,
            "selector_digest": "0" * 64,
            "units": [{"governance_id": "CORE", "content": "Room contract."}],
        }
        provider = StreamingFakeProvider()
        worker = ProjectMasterConversationWorker(
            provider=provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            surface_observer=self.surface_observer,
            reply_poster=lambda **values: self.replies.append(values) or {},
            stream_poster=lambda **values: self.streams.append(values) or {},
            governance_context=selected,
        )
        worker.start()
        try:
            self.assertTrue(
                worker.submit_room_event(
                    binding={
                        "binding_id": "binding-room-context",
                        "provider": "GROK",
                        "provider_session_ref": provider.session_ref,
                    },
                    event={
                        "room_id": "room-context",
                        "room_event_id": "room-event-context",
                        "room_sequence": 1,
                        "message": {
                            "room_event_id": "room-event-context",
                            "author_role": "USER",
                            "body_text": "Use the selected context.",
                        },
                    },
                    bridge_id="bridge-room-context",
                )
            )
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual(selected, provider.messages[0]["governance_context"])

    def test_resident_manager_exposes_selected_release_context_metadata(self) -> None:
        selected = {
            "schema": "universe.release-governance-context.v1",
            "status": "SELECTED",
            "release_id": "core-manager",
            "source_commit": "1" * 40,
            "catalog_digest": "2" * 64,
            "selector_digest": "3" * 64,
            "units": [{"governance_id": "CORE", "content": "Manager contract."}],
        }
        resolver_calls = 0

        def resolver(_project_id: str) -> Mapping[str, Any]:
            nonlocal resolver_calls
            resolver_calls += 1
            return selected

        registrations: list[dict[str, Any]] = []

        def register(project_id, value):
            registrations.append({"project_id": project_id, **dict(value)})
            return {"bridge_id": "bridge-manager-context", **dict(value)}, True

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                provider_factory=lambda _root, _project_id, _store: FakeProvider(),
                coordinator_factory=lambda _root, _project_id, _session: (
                    self.surface_observer
                ),
                governance_context_resolver=resolver,
            )
            try:
                manager.ensure({"project_id": "GCS", "project_root": str(self.root)})
                worker = manager._handles["GCS"].worker
                worker.submit(self._envelope())
                self.assertTrue(worker.wait_idle())
                connection = manager.connection_status("GCS")
            finally:
                manager.close()

        self.assertEqual(1, resolver_calls)
        self.assertEqual(
            "universe.provider-runtime-observation.v1",
            connection["runtime_observation"]["schema"],
        )
        self.assertEqual("UNKNOWN", connection["runtime_observation"]["quota_state"])
        self.assertEqual(
            {
                "status": "SELECTED",
                "release_id": "core-manager",
                "source_commit": "1" * 40,
                "catalog_digest": "2" * 64,
                "selector_digest": "3" * 64,
            },
            connection["governance_context"],
        )

    def test_permission_request_blocks_until_ui_decision_is_delivered(self) -> None:
        provider = PermissionFakeProvider()
        permission_posted = threading.Event()
        permissions: list[dict[str, Any]] = []

        def post_permission(**values):
            permissions.append(values)
            permission_posted.set()
            return {"status": "AGENT_PERMISSION_REQUESTED"}

        worker = ProjectMasterConversationWorker(
            provider=provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            surface_observer=self.surface_observer,
            reply_poster=lambda **values: self.replies.append(values) or {},
            stream_poster=lambda **values: self.streams.append(values) or {},
            permission_poster=post_permission,
        )
        worker.start()
        try:
            worker.submit(self._envelope())
            self.assertTrue(permission_posted.wait(2))
            self.assertTrue(
                worker.resolve_permission(
                    "permission_worker_001",
                    "allow-once",
                )
            )
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual("allow-once", provider.selected_option)
        self.assertEqual(
            "permission_worker_001",
            permissions[0]["permission"]["request_id"],
        )
        self.assertEqual("COMPLETE", self.state.state(self._message_id()))

    def test_surface_observation_failure_blocks_provider_call(self) -> None:
        self.surface_observer = FakeSurfaceObserver(fail=True)
        worker = self._worker()
        worker.start()
        try:
            worker.submit(self._envelope())
            self.assertTrue(worker.wait_idle())
        finally:
            worker.close()

        self.assertEqual([], self.provider.messages)
        self.assertEqual("FAILED", self.state.state(self._message_id()))
        self.assertEqual(
            ["STARTED", "FAILED"],
            [item["event"] for item in self.streams],
        )

    def test_project_mode_coordinator_prepares_and_observes_universe_surface(
        self,
    ) -> None:
        runtime_cli = self.root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        registry = (
            self.root / ".ai" / "runtime" / "project_instance" / "mode_registry.json"
        )
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps(
                {
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        requests: list[dict[str, Any]] = []

        def runner(request):
            request_path = Path(
                request.arguments[request.arguments.index("--request") + 1]
            )
            requests.append(json.loads(request_path.read_text(encoding="utf-8")))
            payload = (
                {
                    "status": "SESSION_PREPARED",
                    "mode_current_anchor": {
                        "status": "MODE_CURRENT_ANCHOR_OBSERVED",
                        "snapshot": {
                            "snapshot": {"anchor_id": "MASTER-CURRENT-001"}
                        },
                    },
                    "mode_boot_binding": {
                        "status": "PREPARED",
                        "binding_id": "mode-boot-master-001",
                        "mode": "MASTER",
                        "role": "MASTER",
                        "frame_id": "current",
                        "anchor_id": "MASTER-CURRENT-001",
                    },
                }
                if "prepare-session" in request.arguments
                else {"status": "COMMANDER_INPUT_OBSERVED"}
            )
            return NativeCliResult(
                contract="universe.windows-native-cli.v1",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(payload),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "grok-cli:session-001",
            native_runner=runner,
            source_binding_resolver=lambda _root: {
                "status": "SELECTED",
                "release_id": "core-test",
                "source_repository": "fixture/universe-private",
                "source_commit": "b" * 40,
                "database_sha256": "c" * 64,
            },
        )
        with patch(
            "project_master_host._required_host_executable",
            return_value=Path(sys.executable),
        ):
            coordinator.prepare()
            coordinator.observe(self._envelope()["message"])

        self.assertEqual("MASTER", requests[0]["mode"])
        self.assertEqual(
            f"universe-release-db://core-test@{'c' * 64}",
            requests[0]["source_ref"],
        )
        self.assertEqual("b" * 40, requests[0]["source_commit"])
        self.assertEqual(
            "fixture/universe-private", requests[0]["source_repository"]
        )
        self.assertEqual("grok-cli:session-001", requests[0]["host_session_ref"])
        self.assertEqual("UNIVERSE_UI", requests[1]["commander_surface"])
        self.assertEqual(
            f"universe://project-room/messages/{self._message_id()}",
            requests[1]["evidence_ref"],
        )

    def test_project_mode_coordinator_resolves_role_from_selected_mode(self) -> None:
        runtime_cli = self.root / ".ai/runtime/reference_runtime/cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        registry = self.root / ".ai/runtime/project_instance/mode_registry.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps(
                {
                    "modes": {
                        "DESIGN": {
                            "role": "DESIGNER",
                            "scope": "product/ux",
                            "mode_profile": "GOVERNANCE_ONLY",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        requests: list[dict[str, Any]] = []

        def runner(request):
            request_path = Path(
                request.arguments[request.arguments.index("--request") + 1]
            )
            requests.append(json.loads(request_path.read_text(encoding="utf-8")))
            payload = {
                "status": "SESSION_PREPARED",
                "mode_current_anchor": {
                    "status": "MODE_CURRENT_ANCHOR_OBSERVED",
                    "snapshot": {"snapshot": {"anchor_id": "DESIGN-CURRENT-001"}},
                },
                "mode_boot_binding": {
                    "status": "PREPARED",
                    "binding_id": "mode-boot-design-001",
                    "mode": "DESIGN",
                    "role": "DESIGNER",
                    "frame_id": "current",
                    "anchor_id": "DESIGN-CURRENT-001",
                },
            }
            return NativeCliResult(
                contract="universe.windows-native-cli.v1",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(payload),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "codex:session-design-001",
            session_node="universe",
            requested_mode="DESIGN",
            native_runner=runner,
            source_binding_resolver=lambda _root: self._selected_release(),
        )

        coordinator.prepare()

        self.assertEqual("universe", coordinator.session_node)
        self.assertEqual("DESIGN", coordinator.requested_mode)
        self.assertEqual("DESIGNER", coordinator._mode_role)
        self.assertEqual("DESIGN", requests[0]["mode"])
        self.assertEqual("DESIGNER", requests[0]["role"])

    def test_project_mode_coordinator_requires_mode_boot_binding(self) -> None:
        runtime_cli = self.root / ".ai/runtime/reference_runtime/cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        registry = self.root / ".ai/runtime/project_instance/mode_registry.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps(
                {
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "codex:session-001",
            source_binding_resolver=lambda _root: self._selected_release(),
            native_runner=lambda _request: NativeCliResult(
                contract="universe.windows-native-cli.v1",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(
                    {
                        "status": "SESSION_PREPARED",
                        "mode_current_anchor": {
                            "status": "MODE_CURRENT_ANCHOR_CREATED",
                            "snapshot": {
                                "snapshot": {"anchor_id": "MASTER-CURRENT-001"}
                            },
                        },
                    }
                ),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            ),
        )

        with self.assertRaisesRegex(
            ProjectMasterHostError,
            "PROJECT_MASTER_MODE_BOOT_BINDING_UNAVAILABLE",
        ):
            coordinator.prepare()

    def test_project_mode_runtime_uses_prepared_binding_and_anchor_frame(self) -> None:
        runtime_cli = self.root / ".ai/runtime/reference_runtime/cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "codex:session-001",
            source_binding_resolver=lambda _root: self._selected_release(),
        )
        coordinator._prepared = {
            "status": "SESSION_PREPARED",
            "mode_current_anchor": {
                "status": "MODE_CURRENT_ANCHOR_CREATED",
                "snapshot": {
                    "snapshot": {"anchor_id": "MASTER-CURRENT-001"}
                },
            },
            "mode_boot_binding": {
                "status": "PREPARED",
                "binding_id": "mode-boot-master-001",
                "mode": "MASTER",
                "role": "MASTER",
                "frame_id": "current",
                "anchor_id": "MASTER-CURRENT-001",
            },
        }
        process_holder: list[Any] = []

        class FakeRuntimeProcess:
            def __init__(self, startup: Mapping[str, Any]) -> None:
                self.pid = 5151
                self.stdout = StringIO(json.dumps(startup) + "\n")
                self.stderr = StringIO("")
                self.return_code = None

            def poll(self):
                return self.return_code

            def terminate(self):
                self.return_code = 0

            def wait(self, timeout=None):
                del timeout
                self.return_code = 0
                return 0

            def kill(self):
                self.return_code = -9

        class FakeJob:
            def close(self):
                return None

        def start_process(command, **_options):
            token = command[command.index("--token") + 1]
            session_id = command[command.index("--session-id") + 1]
            process = FakeRuntimeProcess(
                {
                    "status": "SESSION_BOOT_IMAGE_CREATED",
                    "host_adapter": {
                        "endpoint": "http://127.0.0.1:41992",
                        "token": token,
                    },
                    "runtime_state": {
                        "anchor_id": "MASTER-CURRENT-001",
                        "mode": "MASTER",
                        "role": "MASTER",
                        "session_id": session_id,
                        "executable_runtime_currentness": "CURRENT",
                    },
                    "mode_boot_binding": {
                        "status": "ACTIVE",
                        "binding_id": "mode-boot-master-001",
                    },
                }
            )
            process.command = list(command)
            process_holder.append(process)
            return process

        with patch(
            "project_master_host._required_host_executable",
            return_value=Path(sys.executable),
        ), patch(
            "project_master_host.subprocess.Popen",
            side_effect=start_process,
        ), patch(
            "project_master_host._WindowsKillOnCloseJob",
            return_value=FakeJob(),
        ):
            binding = coordinator._ensure_runtime()

        command = process_holder[0].command
        self.assertEqual("current", binding["frame_id"])
        self.assertEqual(
            "mode-boot-master-001", binding["mode_boot_binding_id"]
        )
        self.assertEqual(
            "mode-boot-master-001",
            command[command.index("--boot-binding-id") + 1],
        )
        self.assertEqual("current", command[command.index("--frame-id") + 1])
        coordinator.close()

    def test_project_mode_coordinator_preserves_runtime_error_code(self) -> None:
        runtime_cli = self.root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")

        def runner(_request):
            return NativeCliResult(
                contract="universe.windows-native-cli.v1",
                status="FAILED",
                return_code=1,
                duration_ms=1,
                stdout=json.dumps(
                    {"error_code": "EXECUTION_ASSIGNMENT_CURRENTNESS_UNKNOWN"}
                ),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "provider-session",
            native_runner=runner,
        )

        with self.assertRaisesRegex(
            ProjectMasterHostError,
            "PROJECT_RUNTIME_COMMAND_FAILED:EXECUTION_ASSIGNMENT_CURRENTNESS_UNKNOWN",
        ):
            coordinator._invoke(("execution-binding", "propose"), {})

    def test_approved_primary_creates_exact_descendant_task_frame(self) -> None:
        runtime_cli = self.root / ".ai/runtime/reference_runtime/cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        tools_root = self.root / "tools"
        tests_root = self.root / "tests"
        tools_root.mkdir()
        tests_root.mkdir()
        calls: list[dict[str, Any]] = []

        def runner(request):
            payload = json.loads(
                Path(request.arguments[request.arguments.index("--request") + 1]).read_text(
                    encoding="utf-8"
                )
            )
            calls.append({"arguments": request.arguments, "payload": payload})
            if "begin-work" in request.arguments:
                response = {
                    "status": "WORK_RECEIPT_ACTIVATED",
                    "binding_id": "binding-work-001",
                    "work_receipt": {"work_receipt_id": "work-receipt-001"},
                }
            else:
                response = {
                    "execution_proposal": {
                        "proposal_id": "task_frame_proposal_001",
                        "plan_digest": "a" * 64,
                    }
                }
            return NativeCliResult(
                contract="universe.windows-native-cli.v1",
                status="COMPLETED",
                return_code=0,
                duration_ms=1,
                stdout=json.dumps(response),
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
            )

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "codex:session-001",
            native_runner=runner,
        )
        runtime_posts: list[dict[str, Any]] = []

        def post(_endpoint, _token, path, payload):
            runtime_posts.append({"path": path, "payload": payload})
            if path.endswith("/create"):
                return {"status": "TASK_FRAME_HOST_ACTIVE"}
            return {
                "status": "TASK_FRAME_OPERATION_APPLIED",
                "output": {"status": "TASK_TURNS_DECLARED"},
            }

        primary = {
            "proposal_id": "task_proposal_primary_001",
            "proposal_digest": "b" * 64,
            "state": "APPROVED",
            "boundary": "P0 live room implementation",
            "task_summary": "Implement live provider room routing.",
            "request_ref": "universe://projects/GCS/proposals/primary-001",
            "source_ref": "universe://projects/GCS/requests/primary-001",
            "scope": {"roots": [str(tools_root), str(tests_root)]},
            "approval": {
                "status": "APPROVED",
                "evidence_ref": "universe://projects/GCS/decisions/primary-001",
            },
        }
        approval = {
            "status": "APPROVED",
            "proposal_id": primary["proposal_id"],
            "proposal_digest": primary["proposal_digest"],
            "commander_surface": "UNIVERSE_UI",
            "evidence_ref": primary["approval"]["evidence_ref"],
            "active_work": {
                "schema": "universe.active-work-reference.v1",
                "active_work_ref": "universe://projects/GCS/active-work/primary-001",
                "work_batch_id": "work_batch_primary_001",
                "parent_instruction_ref": primary["request_ref"],
                "proposal_id": primary["proposal_id"],
                "proposal_digest": primary["proposal_digest"],
                "approval_evidence_ref": primary["approval"]["evidence_ref"],
                "commander_surface": "UNIVERSE_UI",
                "access_surface": "CODEX_DESKTOP",
                "anchor": {
                    "session_id": "project-master-session-001",
                    "anchor_ref": "MASTER-CURRENT-001",
                    "provider": "CODEX",
                    "currentness": "CURRENT",
                },
                "recorded_at": "2026-08-12T01:00:00Z",
            },
        }
        source_work = {
            "scope_kind": "PROJECT_SOURCE_WORK",
            "write_roots": [str(tools_root), str(tests_root)],
            "write_operations": ["CREATE", "MODIFY"],
            "boundary": primary["boundary"],
            "task_summary": primary["task_summary"],
            "instruction_ref": approval["evidence_ref"],
        }
        task_frame = {
            "frame_id": "gcs-primary-001",
            "parent_actor_ref": "project-master:GCS",
            "mutation_scope": {
                "operations": ["CREATE", "MODIFY"],
                "targets": [str(tools_root / "room_router.py"), str(tests_root / "test_room_router.py")],
            },
            "turns": [
                {
                    "turn_id": "boss",
                    "role": "BOSS",
                    "worker_slot_ref": "implementation-boss",
                    "provider": "CODEX",
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "max",
                },
                {
                    "turn_id": "implementation",
                    "role": "SUB_REVIEWER",
                    "worker_slot_ref": "implementation-worker",
                    "provider": "CLAUDE",
                    "model": "sonnet",
                    "reasoning_effort": "high",
                },
                {
                    "turn_id": "review",
                    "role": "SUB_REVIEWER",
                    "worker_slot_ref": "security-reviewer",
                    "provider": "CLAUDE",
                    "model": "sonnet",
                    "reasoning_effort": "high",
                },
                {
                    "turn_id": "qa",
                    "role": "SUB_REVIEWER",
                    "worker_slot_ref": "qa-reviewer",
                    "provider": "CLAUDE",
                    "model": "sonnet",
                    "reasoning_effort": "high",
                },
            ],
            "instruction_id": "instruction:primary-001",
            "instruction_text": primary["task_summary"],
            "constraints": ["NO_COMMIT", "NO_PUSH"],
            "expected_output": {"result": "implementation and tests"},
        }

        with patch.object(
            coordinator,
            "_ensure_runtime",
            return_value={
                "endpoint": "http://127.0.0.1:41992",
                "token": "test-token",
                "session_id": "project-master-session-001",
                "frame_id": "master-current",
                "anchor_id": "MASTER-CURRENT-001",
            },
        ), patch.object(coordinator, "_post_runtime", side_effect=post):
            result = coordinator.create_approved_descendant_task_frame(
                primary_proposal=primary,
                governance_approval=approval,
                source_work=source_work,
                task_frame=task_frame,
            )

        self.assertEqual("APPROVED_DESCENDANT_TASK_FRAME_READY", result["status"])
        self.assertEqual("work-receipt-001", result["work_receipt_id"])
        self.assertEqual("task_frame_proposal_001", result["task_frame_proposal_id"])
        self.assertEqual(2, len(calls))
        self.assertEqual(approval["evidence_ref"], calls[0]["payload"]["work"]["instruction_ref"])
        plan = calls[1]["payload"]["execution_plan"]
        self.assertEqual("work-receipt-001", plan["execution_assignment_ref"])
        self.assertEqual("UNIVERSE_UI", plan["commander_surface"])
        self.assertEqual(task_frame["mutation_scope"], plan["mutation_scope"])
        self.assertEqual(
            approval["active_work"]["active_work_ref"], plan["task_summary_ref"]
        )
        self.assertEqual(2, len(runtime_posts))
        self.assertEqual(
            approval["evidence_ref"],
            runtime_posts[0]["payload"]["frame"]["parent_observation"]["evidence_ref"],
        )
        self.assertEqual(
            "project-master-session-001",
            runtime_posts[0]["payload"]["frame"]["origin_governance_session_ref"],
        )
        self.assertEqual(
            [
                {"turn_id": "boss", "role": "BOSS", "input_turn_ids": []},
                {
                    "turn_id": "implementation",
                    "role": "SUB_REVIEWER",
                    "input_turn_ids": ["boss"],
                },
                {
                    "turn_id": "review",
                    "role": "SUB_REVIEWER",
                    "input_turn_ids": ["implementation"],
                },
                {
                    "turn_id": "qa",
                    "role": "SUB_REVIEWER",
                    "input_turn_ids": ["review"],
                },
            ],
            runtime_posts[1]["payload"]["operation"]["turns"],
        )
        self.assertNotIn("token", result)
        self.assertNotIn("endpoint", result)

    def test_runtime_session_id_recovers_exact_unfinished_task_frame(self) -> None:
        runtime_cli = self.root / ".ai/runtime/reference_runtime/cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        frames_root = self.root / ".ai/runtime/task_frames"
        frames_root.mkdir(parents=True)
        database = frames_root / "recoverable.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                """
                CREATE TABLE task_frame_context (
                    singleton INTEGER PRIMARY KEY,
                    frame_id TEXT NOT NULL,
                    origin_session_id TEXT NOT NULL,
                    origin_anchor_ref TEXT NOT NULL,
                    origin_frame_id TEXT NOT NULL,
                    task_state TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO task_frame_context (
                    singleton, frame_id, origin_session_id, origin_anchor_ref,
                    origin_frame_id, task_state
                ) VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    "gcs-recover-001",
                    "project-master-original-session",
                    "MASTER-CURRENT-001",
                    "current",
                    "ACTIVE",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "codex:session-001",
            native_runner=lambda _request: self.fail("runtime must not start"),
        )

        self.assertEqual(
            "project-master-original-session",
            coordinator._runtime_session_id(
                anchor_id="MASTER-CURRENT-001",
                frame_id="current",
                recover_task_frame_id="gcs-recover-001",
            ),
        )
        self.assertEqual(
            "project-master-gcs-master",
            coordinator._runtime_session_id(
                anchor_id="MASTER-CURRENT-OTHER",
                frame_id="current",
                recover_task_frame_id="gcs-recover-001",
            ),
        )

    def test_approved_descendant_runs_boss_then_declared_child_turns(self) -> None:
        runtime_cli = self.root / ".ai/runtime/reference_runtime/cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        dispatches: list[dict[str, Any]] = []

        class Dispatcher:
            def dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
                dispatches.append(dict(request))
                if request["turn_id"] == "boss":
                    return {
                        "status": "WORKER_OUTPUT_CAPTURED",
                        "worker_id": "boss-worker-001",
                        "worker_run_ref": "boss-run-001",
                        "worker_envelope": {
                            "turn_id": "boss",
                            "worker_id": "boss-worker-001",
                            "worker_run_ref": "boss-run-001",
                            "result_receipt_ref": "result://boss",
                            "status": "COMPLETED",
                            "evidence_refs": ["result://boss"],
                            "result": {"text": "boss plan"},
                            "review_decision": "",
                        },
                        "structured_result": {
                            "summary": "Delegate the focused verification.",
                            "worker_allocations": [
                                {
                                    "turn_id": "review",
                                    "worker_slot_ref": "review-worker",
                                    "worker_path": "/root/boss/review",
                                    "task": "Review the approved work.",
                                    "expected_output": {"result": "review"},
                                    "mutation_scope": {
                                        "operations": [],
                                        "targets": [],
                                    },
                                    "skill_bindings": [],
                                }
                            ],
                        },
                    }
                return {
                    "status": "TURN_COMPLETED",
                    "worker_id": "review-worker-001",
                }

            def record_captured_result(self, request, envelope):
                self.recorded_request = dict(request)
                self.recorded_envelope = dict(envelope)
                return {"status": "TASK_COMPLETED"}

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "codex:session-001",
            worker_dispatcher=Dispatcher(),
        )
        binding = {
            "endpoint": "http://127.0.0.1:41992",
            "token": "test-token",
            "session_id": "project-master-session-001",
            "frame_id": "master-current",
            "anchor_id": "MASTER-CURRENT-001",
        }
        plan = {
            "frame_id": "gcs-primary-001",
            "parent_actor_ref": "project-master:GCS",
            "turns": [
                {
                    "turn_id": "boss",
                    "role": "BOSS",
                    "worker_slot_ref": "boss-worker",
                    "provider": "CODEX",
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "max",
                },
                {
                    "turn_id": "review",
                    "role": "SUB_REVIEWER",
                    "worker_slot_ref": "review-worker",
                    "provider": "GROK",
                    "model": "grok-4.5",
                    "reasoning_effort": "high",
                },
            ],
        }
        operations: list[dict[str, Any]] = []

        def task_operation(_binding, _frame_id, operation):
            operations.append(dict(operation))
            if operation["operation"] == "input_bundle" and operation["turn_id"] == "boss":
                return {
                    "status": "TURN_INPUTS_READY",
                    "parent_instruction_bundle": {
                        "instructions": [
                            {"instruction_digest": "a" * 64}
                        ]
                    },
                }
            if operation["operation"] == "submit_boss_allocations":
                return {"status": "BOSS_ALLOCATIONS_RECORDED"}
            return {"status": "TURN_INPUTS_READY", "inputs": []}

        with (
            patch.object(coordinator, "_ensure_runtime", return_value=binding),
            patch.object(coordinator, "_reopen_task_frame_from_runtime_store") as reopen,
            patch.object(
                coordinator,
                "_get_runtime",
                return_value={
                    "status": "TASK_FRAME_HOST_ACTIVE",
                    "execution_evidence": {
                        "execution_gate": {
                            "approval_ref": "universe://projects/GCS/decisions/primary-001",
                            "execution_plan": plan,
                        }
                    },
                },
            ),
            patch.object(coordinator, "_task_frame_operation", side_effect=task_operation),
        ):
            result = coordinator.run_approved_descendant_task_frame(
                task_frame_id="gcs-primary-001",
                primary_proposal_id="task_proposal_primary_001",
                primary_proposal_digest="b" * 64,
                approval_evidence_ref="universe://projects/GCS/decisions/primary-001",
            )

        self.assertEqual("APPROVED_DESCENDANT_TASK_FRAME_COMPLETED", result["status"])
        reopen.assert_called_once_with(
            binding=binding,
            task_frame_id="gcs-primary-001",
        )
        self.assertEqual(["boss", "review"], [call["turn_id"] for call in dispatches])
        self.assertTrue(dispatches[0]["defer_terminal_result"])
        self.assertEqual("boss-worker-001", dispatches[1]["invoker_actor_ref"])
        self.assertEqual("submit_boss_allocations", operations[1]["operation"])

    def test_approved_descendant_rejects_target_outside_primary_roots(self) -> None:
        runtime_cli = self.root / ".ai/runtime/reference_runtime/cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")
        allowed_root = self.root / "tools"
        allowed_root.mkdir()
        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "codex:session-001",
            native_runner=lambda _request: self.fail("runtime must not start"),
        )
        evidence_ref = "universe://projects/GCS/decisions/primary-002"
        primary = {
            "proposal_id": "task_proposal_primary_002",
            "proposal_digest": "c" * 64,
            "state": "APPROVED",
            "boundary": "P0 routing",
            "task_summary": "Implement routing.",
            "request_ref": "universe://projects/GCS/proposals/primary-002",
            "source_ref": "universe://projects/GCS/requests/primary-002",
            "scope": {"roots": [str(allowed_root)]},
            "approval": {"status": "APPROVED", "evidence_ref": evidence_ref},
        }
        approval = {
            "status": "APPROVED",
            "proposal_id": primary["proposal_id"],
            "proposal_digest": primary["proposal_digest"],
            "commander_surface": "UNIVERSE_UI",
            "evidence_ref": evidence_ref,
        }
        source_work = {
            "scope_kind": "PROJECT_SOURCE_WORK",
            "write_roots": [str(allowed_root)],
            "write_operations": ["CREATE"],
            "boundary": primary["boundary"],
            "task_summary": primary["task_summary"],
            "instruction_ref": evidence_ref,
        }
        task_frame = {
            "frame_id": "gcs-primary-002",
            "parent_actor_ref": "project-master:GCS",
            "mutation_scope": {
                "operations": ["CREATE"],
                "targets": [str(self.root / "outside.py")],
            },
            "turns": [
                {
                    "turn_id": "boss",
                    "role": "BOSS",
                    "worker_slot_ref": "implementation-boss",
                    "provider": "CODEX",
                    "model": "gpt-5.6-luna",
                    "reasoning_effort": "max",
                }
            ],
            "instruction_id": "instruction:primary-002",
            "instruction_text": primary["task_summary"],
            "constraints": ["NO_COMMIT"],
            "expected_output": {"result": "implementation"},
        }

        with self.assertRaisesRegex(
            ProjectMasterHostError, "DESCENDANT_TASK_FRAME_TARGET_OUT_OF_SCOPE"
        ):
            coordinator.create_approved_descendant_task_frame(
                primary_proposal=primary,
                governance_approval=approval,
                source_work=source_work,
                task_frame=task_frame,
            )

    def test_project_stop_denial_retains_process_and_refreshes_lease_version(
        self,
    ) -> None:
        runtime_cli = self.root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        runtime_cli.parent.mkdir(parents=True, exist_ok=True)
        runtime_cli.write_text("# test runtime\n", encoding="utf-8")

        class RunningProcess:
            return_code: int | None = None

            def poll(self):
                return self.return_code

            def terminate(self):
                self.return_code = 0

            def wait(self, timeout=None):
                del timeout
                return 0

            def kill(self):
                self.return_code = -9

        class DenyingSupervisor:
            @staticmethod
            def authorize_stop(*_args, **_kwargs):
                raise SessionSupervisorError(
                    "STOP_AUTHORIZATION_DENIED", "identity mismatch"
                )

            @staticmethod
            def get_session(_session_id):
                return {"process_lease": {"lease_version": 9}}

        coordinator = ProjectModeCoordinator(
            self.root,
            "GCS",
            "provider-session",
            session_supervisor=DenyingSupervisor(),
        )
        process = RunningProcess()
        coordinator._runtime_process = process
        coordinator._runtime_binding = {
            "session_id": "project-session",
            "frame_id": "master",
            "anchor_id": "anchor",
            "runtime_currentness_observation": "CURRENT",
        }
        coordinator._supervisor_session_id = "supervisor-session"
        coordinator._lease_token = "lease-token"
        coordinator._lease_version = 8
        coordinator._process_identity = {
            "pid": 4242,
            "process_created_at": "2026-08-02T12:00:00Z",
            "executable": "python.exe",
            "command": ["python.exe"],
            "endpoint": "http://127.0.0.1:51702",
            "handshake_fingerprint": "a" * 64,
        }

        with self.assertRaisesRegex(SessionSupervisorError, "identity mismatch"):
            coordinator.close()

        self.assertIs(coordinator._runtime_process, process)
        self.assertIsNotNone(coordinator._runtime_binding)
        self.assertIsNone(process.return_code)
        self.assertEqual(9, coordinator._lease_version)

    def test_resident_manager_starts_one_host_per_project(self) -> None:
        registrations: list[dict[str, Any]] = []

        def register(project_id, value):
            registrations.append({"project_id": project_id, **dict(value)})
            return {"project_id": project_id, **dict(value)}, len(registrations) == 1

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                provider_factory=lambda _root, _project_id, _store: FakeProvider(),
                coordinator_factory=lambda _root, _project_id, _session: (
                    self.surface_observer
                ),
            )
            try:
                first = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                second = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                self.assertTrue(manager.is_resident("GCS"))
            finally:
                manager.close()

        self.assertEqual("STARTED", first["status"])
        self.assertEqual("RESIDENT", second["status"])
        self.assertEqual(
            "REUSED",
            second["session_connection"]["connection_state"],
        )
        self.assertEqual(1, len(registrations))
        self.assertEqual(1, self.surface_observer.prepare_count)
        self.assertNotIn(registrations[0]["credential_env"], os.environ)

    def test_resident_manager_tracks_provider_process_lease_lifecycle(self) -> None:
        supervisor = SessionSupervisorStore(self.root / "provider-lease.sqlite3")
        provider_holder: list[PreparedFakeProvider] = []

        class LeasePreparedProvider(PreparedFakeProvider):
            def supervisor_process_identity(
                self, endpoint: str, handshake_token: str
            ) -> dict[str, Any]:
                return {
                    "pid": 4321,
                    "process_created_at": "2026-08-13T00:00:00Z",
                    "executable": "C:\\fake\\provider.exe",
                    "command": ["C:\\fake\\provider.exe", "stdio"],
                    "endpoint": endpoint,
                    "handshake_fingerprint": hashlib.sha256(
                        handshake_token.encode("utf-8")
                    ).hexdigest(),
                }

        class AnchorObserver(FakeSurfaceObserver):
            def prepare(self) -> Mapping[str, Any]:
                self.prepare_count += 1
                return {
                    "status": "SESSION_PREPARED",
                    "mode_current_anchor": {
                        "snapshot": {
                            "snapshot": {"anchor_id": "MASTER-CURRENT-LEASE"}
                        }
                    },
                }

        def register(project_id, value):
            return {"project_id": project_id, **dict(value)}, True

        def provider_factory(_root, _project_id, _store):
            provider = LeasePreparedProvider()
            provider_holder.append(provider)
            return provider

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                session_supervisor=supervisor,
                provider_factory=provider_factory,
                provider_resolver=lambda _project_id: "CLAUDE",
                coordinator_factory=lambda _root, _project_id, _session: AnchorObserver(),
            )
            try:
                result = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                live = next(
                    item
                    for item in supervisor.list_sessions(node="GCS", mode="MASTER")
                    if item["is_default"]
                )
                self.assertEqual("STARTED", result["status"])
                self.assertEqual("LIVE", live["state"])
                self.assertEqual("OWNED", live["process_lease"]["lease_state"])
                self.assertEqual(4321, live["process_lease"]["process_identity"]["pid"])
                handle = manager._handles["GCS"]
                supervisor.mark_lease_stale(
                    handle.supervisor_session_id,
                    handle.supervisor_process_identity,
                    lease_token=handle.supervisor_lease_token,
                    expected_lease_version=handle.supervisor_lease_version,
                    reason="TEST_MAINTENANCE_STALE",
                )
                reused = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                recovered = next(
                    item
                    for item in supervisor.list_sessions(node="GCS", mode="MASTER")
                    if item["is_default"]
                )
                self.assertEqual("RESIDENT", reused["status"])
                self.assertEqual("LIVE", recovered["state"])
                self.assertEqual("OWNED", recovered["process_lease"]["lease_state"])
            finally:
                manager.close()

        closed = supervisor.get_session(live["session_id"])
        self.assertEqual("DISCONNECTED", closed["state"])
        self.assertEqual("STALE", closed["process_lease"]["lease_state"])
        self.assertTrue(provider_holder[0].closed)

    def test_resident_manager_registers_prepared_agent_session_ref(self) -> None:
        registrations: list[dict[str, Any]] = []
        provider = PreparedFakeProvider()
        coordinator_sessions: list[str] = []

        def register(project_id, value):
            registrations.append({"project_id": project_id, **dict(value)})
            return {"project_id": project_id, **dict(value)}, True

        def coordinator(_root, _project_id, session_ref):
            coordinator_sessions.append(session_ref)
            return self.surface_observer

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                provider_factory=lambda _root, _project_id, _store: provider,
                coordinator_factory=coordinator,
            )
            try:
                manager.ensure({"project_id": "GCS", "project_root": str(self.root)})
            finally:
                manager.close()

        self.assertEqual(1, provider.prepare_count)
        self.assertEqual(["fake-provider:actual-session"], coordinator_sessions)
        self.assertEqual(
            "fake-provider:actual-session",
            registrations[0]["master_session_ref"],
        )
        self.assertTrue(provider.closed)

    def test_resident_manager_restarts_dead_reused_provider_process(self) -> None:
        supervisor = SessionSupervisorStore(self.root / "provider-restart.sqlite3")
        providers: list[Any] = []

        class RestartableProvider(PreparedFakeProvider):
            def __init__(self, pid: int) -> None:
                super().__init__()
                self.pid = pid
                self.alive = True

            def supervisor_process_identity(
                self, endpoint: str, handshake_token: str
            ) -> dict[str, Any]:
                if not self.alive:
                    raise ClaudeResidentError("CLAUDE_PROCESS_NOT_ALIVE")
                return {
                    "pid": self.pid,
                    "process_created_at": "2026-08-14T00:00:00Z",
                    "executable": "C:\\fake\\provider.exe",
                    "command": ["C:\\fake\\provider.exe", "stdio"],
                    "endpoint": endpoint,
                    "handshake_fingerprint": hashlib.sha256(
                        handshake_token.encode("utf-8")
                    ).hexdigest(),
                }

        def register(project_id, value):
            return {"project_id": project_id, **dict(value)}, True

        def provider_factory(_root, _project_id, _store):
            provider = RestartableProvider(4400 + len(providers))
            providers.append(provider)
            return provider

        class AnchorObserver(FakeSurfaceObserver):
            def prepare(self) -> Mapping[str, Any]:
                self.prepare_count += 1
                return {
                    "status": "SESSION_PREPARED",
                    "mode_current_anchor": {
                        "snapshot": {
                            "snapshot": {"anchor_id": "MASTER-CURRENT-RESTART"}
                        }
                    },
                }

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                session_supervisor=supervisor,
                provider_factory=provider_factory,
                provider_resolver=lambda _project_id: "CLAUDE",
                coordinator_factory=lambda _root, _project_id, _session: AnchorObserver(),
            )
            try:
                first = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                providers[0].alive = False
                second = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                live = next(
                    item
                    for item in supervisor.list_sessions(node="GCS", mode="MASTER")
                    if item["is_default"]
                )
                self.assertEqual("STARTED", first["status"])
                self.assertEqual("STARTED", second["status"])
                self.assertEqual(2, len(providers))
                self.assertEqual("LIVE", live["state"])
                self.assertEqual(4401, live["process_lease"]["process_identity"]["pid"])
            finally:
                manager.close()

        self.assertTrue(providers[0].closed)
        self.assertTrue(providers[1].closed)

    def test_resident_manager_rebinds_master_to_project_with_stable_session(self) -> None:
        source_root = self.root / "source"
        target_root = self.root / "target"
        source_root.mkdir()
        target_root.mkdir()
        supervisor = SessionSupervisorStore(self.root / "supervisor-rebind.sqlite3")
        providers: list[Any] = []

        class RebindProvider(PreparedFakeProvider):
            def __init__(self, store: ProjectMasterSessionStore, root: Path) -> None:
                super().__init__()
                self.store = store
                self.root = root

            def prepare_session(self) -> None:
                self.prepare_count += 1
                self.session_ref = self.store.session_ref_for("CLAUDE") or "claude-code:stable-session"
                self.store.observe_provider_session("CLAUDE", self.session_ref)

        def provider_factory(root, _project_id, store):
            provider = RebindProvider(store, root)
            providers.append(provider)
            return provider

        def register(project_id, value):
            return {"project_id": project_id, **dict(value)}, True

        class RebindSurfaceObserver(FakeSurfaceObserver):
            def prepare(self) -> Mapping[str, Any]:
                self.prepare_count += 1
                return {
                    "status": "SESSION_PREPARED",
                    "mode_current_anchor": {
                        "snapshot": {"snapshot": {"anchor_id": "MASTER-CURRENT-REBIND"}}
                    },
                }

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                session_supervisor=supervisor,
                provider_factory=provider_factory,
                provider_resolver=lambda _project_id: "CLAUDE",
                coordinator_factory=lambda _root, _project_id, _session: RebindSurfaceObserver(),
            )
            try:
                manager.ensure({"project_id": "SOURCE", "project_root": str(source_root)})
                before = next(
                    item for item in supervisor.list_sessions(node="SOURCE", mode="MASTER")
                    if item["is_default"]
                )
                result = manager.rebind_working_directory(
                    before["session_id"],
                    {"project_id": "TARGET", "project_root": str(target_root)},
                    expected_version=before["row_version"],
                )
                after = supervisor.get_session(before["session_id"])
                self.assertTrue(manager.is_resident("TARGET"))
                self.assertFalse(manager.is_resident("SOURCE"))
            finally:
                manager.close()
        self.assertEqual("PROVIDER_WORKING_DIRECTORY_REBOUND", result["status"])
        self.assertEqual(before["session_id"], after["session_id"])
        self.assertEqual("TARGET", after["node"])
        self.assertTrue(after["is_default"])
        self.assertEqual("claude-code:stable-session", after["provider_session_ref"])
        self.assertTrue(providers[0].closed)
        self.assertEqual(target_root.resolve(), providers[1].root.resolve())

    def test_failed_master_rebind_restores_source_and_target_defaults(self) -> None:
        source_root = self.root / "source-rollback"
        target_root = self.root / "target-rollback"
        source_root.mkdir()
        target_root.mkdir()
        supervisor = SessionSupervisorStore(self.root / "supervisor-rollback.sqlite3")

        class RollbackProvider(PreparedFakeProvider):
            def __init__(self, store: ProjectMasterSessionStore, root: Path) -> None:
                super().__init__()
                self.store = store
                self.root = root

            def prepare_session(self) -> None:
                if self.root == target_root.resolve():
                    raise ProjectMasterHostError("TARGET_PROVIDER_START_FAILED")
                self.session_ref = self.store.session_ref_for("CLAUDE") or "claude-code:rollback-session"
                self.store.observe_provider_session("CLAUDE", self.session_ref)

        target_history, _ = supervisor.register_session(
            {
                "session_id": "target-history",
                "project_id": "TARGET",
                "node": "TARGET",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-target-history",
            }
        )
        if not target_history["is_default"]:
            supervisor.set_default(
                target_history["session_id"],
                expected_pointer_version=target_history["default_pointer_version"],
            )

        def register(project_id, value):
            return {"project_id": project_id, **dict(value)}, True

        class RollbackSurfaceObserver(FakeSurfaceObserver):
            def prepare(self) -> Mapping[str, Any]:
                return {
                    "status": "SESSION_PREPARED",
                    "mode_current_anchor": {
                        "snapshot": {"snapshot": {"anchor_id": "MASTER-CURRENT-ROLLBACK"}}
                    },
                }

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                session_supervisor=supervisor,
                provider_factory=lambda root, _project_id, store: RollbackProvider(store, root),
                provider_resolver=lambda _project_id: "CLAUDE",
                coordinator_factory=lambda _root, _project_id, _session: RollbackSurfaceObserver(),
            )
            try:
                manager.ensure({"project_id": "SOURCE", "project_root": str(source_root)})
                before = next(
                    item for item in supervisor.list_sessions(node="SOURCE", mode="MASTER")
                    if item["is_default"]
                )
                with self.assertRaisesRegex(ProjectMasterHostError, "SESSION_CWD_REBIND_FAILED"):
                    manager.rebind_working_directory(
                        before["session_id"],
                        {"project_id": "TARGET", "project_root": str(target_root)},
                        expected_version=before["row_version"],
                    )
                restored = supervisor.get_session(before["session_id"])
                target_default = next(
                    item for item in supervisor.list_sessions(node="TARGET", mode="MASTER")
                    if item["is_default"]
                )
                self.assertTrue(manager.is_resident("SOURCE"))
            finally:
                manager.close()
        self.assertEqual("SOURCE", restored["node"])
        self.assertTrue(restored["is_default"])
        self.assertEqual("target-history", target_default["session_id"])

    def test_resident_manager_bootstraps_fresh_supervisor_session_before_prepare(
        self,
    ) -> None:
        supervisor = SessionSupervisorStore(self.root / "supervisor.sqlite3")
        observed: list[dict[str, Any]] = []

        class AnchorSurfaceObserver(FakeSurfaceObserver):
            def prepare(self) -> Mapping[str, Any]:
                self.prepare_count += 1
                return {
                    "status": "SESSION_PREPARED",
                    "mode_current_anchor": {
                        "snapshot": {
                            "snapshot": {"anchor_id": "MASTER-CURRENT-FRESH"}
                        }
                    },
                }

        class SupervisorAwareProvider(PreparedFakeProvider):
            def __init__(self, store: ProjectMasterSessionStore) -> None:
                super().__init__()
                self.store = store

            def prepare_session(self) -> None:
                sessions = supervisor.list_sessions(node="GCS", mode="MASTER")
                selected = next(
                    (session for session in sessions if session["is_default"]),
                    None,
                )
                if selected is None:
                    raise ProjectMasterHostError(
                        "SUPERVISOR_PROJECT_SESSION_UNAVAILABLE"
                    )
                observed.append(dict(selected))
                self.session_ref = "fake-provider:actual-session"
                self.store.observe_provider_session("CLAUDE", self.session_ref)

        def register(project_id, value):
            return {"project_id": project_id, **dict(value)}, True

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                session_supervisor=supervisor,
                provider_factory=lambda _root, _project_id, store: (
                    SupervisorAwareProvider(store)
                ),
                provider_resolver=lambda _project_id: "CLAUDE",
                coordinator_factory=lambda _root, _project_id, _session: (
                    AnchorSurfaceObserver()
                ),
            )
            try:
                result = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
            finally:
                manager.close()

        self.assertEqual("STARTED", result["status"])
        self.assertEqual(1, len(observed))
        self.assertIsNone(observed[0]["provider_session_ref"])
        self.assertEqual("UNKNOWN", observed[0]["currentness"])
        selected = next(
            session
            for session in supervisor.list_sessions(node="GCS", mode="MASTER")
            if session["is_default"]
        )
        self.assertEqual("CLAUDE", selected["provider"])
        self.assertEqual(
            "fake-provider:actual-session", selected["provider_session_ref"]
        )

    def test_resident_manager_restarts_when_provider_selection_changes(self) -> None:
        registrations: list[dict[str, Any]] = []
        selected = {"provider": "GROK"}

        def register(project_id, value):
            registrations.append({"project_id": project_id, **dict(value)})
            return {"project_id": project_id, **dict(value)}, True

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                provider_factory=lambda _root, _project_id, _store: FakeProvider(),
                provider_resolver=lambda _project_id: selected["provider"],
                coordinator_factory=lambda _root, _project_id, _session: (
                    self.surface_observer
                ),
            )
            try:
                first = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                selected["provider"] = "CODEX"
                second = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
            finally:
                manager.close()

        self.assertEqual("GROK", first["provider"])
        self.assertEqual("CODEX", second["provider"])
        self.assertEqual(2, len(registrations))

    def test_resident_manager_restarts_when_selected_session_changes(self) -> None:
        registrations: list[dict[str, Any]] = []
        providers: list[PreparedFakeProvider] = []

        def register(project_id, value):
            registrations.append({"project_id": project_id, **dict(value)})
            return {"project_id": project_id, **dict(value)}, True

        def provider_factory(_root, _project_id, store):
            provider = PreparedFakeProvider()
            provider.prepare_session = lambda: None
            provider.session_ref = store.session_ref_for("GROK") or (
                f"generated-session-{len(providers) + 1}"
            )
            providers.append(provider)
            return provider

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                provider_factory=provider_factory,
                provider_resolver=lambda _project_id: "GROK",
                coordinator_factory=lambda _root, _project_id, _session: (
                    self.surface_observer
                ),
            )
            try:
                first = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
                state = ProjectMasterSessionStore(
                    _default_state_db("GCS"),
                    "GCS",
                )
                state.observe_provider_session("GROK", "past-master-session")
                second = manager.ensure(
                    {"project_id": "GCS", "project_root": str(self.root)}
                )
            finally:
                manager.close()

        self.assertEqual("STARTED", first["status"])
        self.assertEqual("STARTED", second["status"])
        self.assertEqual(2, len(providers))
        self.assertTrue(providers[0].closed)
        self.assertEqual("past-master-session", providers[1].session_ref)
        self.assertEqual(2, len(registrations))

    def test_resident_manager_routes_native_room_events_for_all_provider_labels(
        self,
    ) -> None:
        for provider_name in ("CODEX", "CLAUDE", "GROK"):
            with self.subTest(provider=provider_name):
                provider = StreamingPreparedFakeProvider()
                observed: list[dict[str, Any]] = []

                def register(project_id, value):
                    return {
                        "bridge_id": f"bridge-{provider_name.lower()}",
                        "project_id": project_id,
                        **dict(value),
                    }, True

                with patch.dict(
                    os.environ,
                    {"LOCALAPPDATA": str(self.root)},
                    clear=False,
                ):
                    manager = ResidentProjectMasterHostManager(
                        universe_endpoint="http://127.0.0.1:52973",
                        bridge_registrar=register,
                        provider_factory=lambda _root, _project_id, _store: provider,
                        provider_resolver=lambda _project_id: provider_name,
                        coordinator_factory=lambda _root, _project_id, _session: (
                            self.surface_observer
                        ),
                        room_event_observer=lambda event: observed.append(dict(event)),
                    )
                    try:
                        manager.ensure(
                            {"project_id": "GCS", "project_root": str(self.root)}
                        )
                        accepted = manager.submit_room_event(
                            "GCS",
                            {
                                "binding_id": f"bind-{provider_name.lower()}",
                                "provider": provider_name,
                                "provider_session_ref": provider.session_ref,
                            },
                            {
                                "room_id": "room_native",
                                "room_event_id": f"event-{provider_name.lower()}",
                                "room_sequence": 1,
                                "message": {
                                    "room_event_id": f"event-{provider_name.lower()}",
                                    "author_role": "USER",
                                    "body_text": "one incremental event",
                                },
                            },
                        )
                        self.assertTrue(accepted)
                        self.assertTrue(
                            manager._handles["GCS"].worker.wait_idle()
                        )
                    finally:
                        manager.close()

                self.assertEqual("one incremental event", provider.messages[0]["body"])
                self.assertEqual("COMPLETED", observed[-1]["event"])

    def test_resident_manager_accepts_codex_transport_prefixed_session_ref(
        self,
    ) -> None:
        provider = StreamingPreparedFakeProvider()
        observed: list[dict[str, Any]] = []

        def prepare_session() -> None:
            provider.prepare_count += 1
            provider.session_ref = "codex-app-server:session-transport-001"

        def register(project_id, value):
            return {
                "bridge_id": "bridge-codex-transport",
                "project_id": project_id,
                **dict(value),
            }, True

        provider.prepare_session = prepare_session
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentProjectMasterHostManager(
                universe_endpoint="http://127.0.0.1:52973",
                bridge_registrar=register,
                provider_factory=lambda _root, _project_id, _store: provider,
                provider_resolver=lambda _project_id: "CODEX",
                coordinator_factory=lambda _root, _project_id, _session: (
                    self.surface_observer
                ),
                room_event_observer=lambda event: observed.append(dict(event)),
            )
            binding = {
                "binding_id": "bind-codex-transport",
                "provider": "CODEX",
                "provider_session_ref": "session-transport-001",
            }
            event = {
                "room_id": "room_native",
                "room_event_id": "event-codex-transport",
                "room_sequence": 1,
                "message": {
                    "room_event_id": "event-codex-transport",
                    "author_role": "USER",
                    "body_text": "Deliver to the resident Codex session",
                },
            }
            try:
                manager.ensure({"project_id": "GCS", "project_root": str(self.root)})
                self.assertTrue(manager.submit_room_event("GCS", binding, event))
                self.assertTrue(manager._handles["GCS"].worker.wait_idle())
                with self.assertRaisesRegex(
                    ProjectMasterHostError,
                    "PROJECT_MASTER_NATIVE_SESSION_MISMATCH",
                ):
                    manager.submit_room_event(
                        "GCS",
                        {**binding, "provider_session_ref": "other-session"},
                        event,
                    )
            finally:
                manager.close()

        self.assertEqual(
            "Deliver to the resident Codex session", provider.messages[0]["body"]
        )
        self.assertEqual("COMPLETED", observed[-1]["event"])

    def test_room_participant_manager_resumes_and_routes_all_provider_labels(
        self,
    ) -> None:
        for provider_name in ("CODEX", "CLAUDE", "GROK"):
            with self.subTest(provider=provider_name):
                providers: list[PreparedFakeProvider] = []
                observed: list[dict[str, Any]] = []

                def provider_factory(provider, _root, _target, store, _mode, _actor):
                    instance = StreamingPreparedFakeProvider()
                    instance.session_ref = store.session_ref_for(provider)
                    instance.prepare_session = lambda: None
                    providers.append(instance)
                    return instance

                binding = {
                    "binding_id": f"bind-{provider_name.lower()}",
                    "slot_role": "MODEL",
                    "provider": provider_name,
                    "provider_session_ref": f"{provider_name.lower()}-session-001",
                    "display_name": f"{provider_name} reviewer",
                }
                with patch.dict(
                    os.environ,
                    {"LOCALAPPDATA": str(self.root)},
                    clear=False,
                ):
                    manager = ResidentRoomParticipantHostManager(
                        room_event_observer=lambda event: observed.append(dict(event)),
                        provider_factory=provider_factory,
                    )
                    try:
                        connected = manager.ensure(
                            binding=binding,
                            repository_root=self.root,
                            node="GCS",
                            mode="MASTER",
                        )
                        self.assertTrue(
                            manager.submit(
                                binding,
                                {
                                    "room_id": "room-meeting",
                                    "room_event_id": "event-001",
                                    "room_sequence": 1,
                                    "correlation_id": "event-001",
                                    "message": {
                                        "room_event_id": "event-001",
                                        "author_role": "USER",
                                        "body_text": "Review only this increment",
                                    },
                                },
                            )
                        )
                        self.assertTrue(
                            manager._handles[binding["binding_id"]].worker.wait_idle()
                        )
                        self.assertTrue(manager.stop(binding["binding_id"]))
                    finally:
                        manager.close()

                self.assertEqual("STARTED", connected["status"])
                self.assertEqual(1, len(providers))
                self.assertEqual(
                    binding["provider_session_ref"], providers[0].session_ref
                )
                self.assertEqual(1, len(providers[0].messages))
                self.assertEqual(
                    "Review only this increment",
                    providers[0].messages[0]["body"],
                )
                self.assertEqual(
                    ["DELIVERY_ACCEPTED", "DELTA", "DELTA", "COMPLETED"],
                    [event["event"] for event in observed],
                )
                self.assertTrue(providers[0].closed)

    def test_room_participant_permission_is_scoped_and_resolved_in_place(
        self,
    ) -> None:
        provider = RoomPermissionFakeProvider()
        permission_seen = threading.Event()
        permissions: list[dict[str, Any]] = []
        observed: list[dict[str, Any]] = []

        def provider_factory(provider_name, _root, _target, store, _mode, _actor):
            provider.session_ref = store.session_ref_for(provider_name)
            provider.prepare_session = lambda: None
            return provider

        def observe_permission(binding, event, permission) -> None:
            permissions.append(
                {
                    "binding": dict(binding),
                    "event": dict(event),
                    "permission": dict(permission),
                }
            )
            permission_seen.set()

        binding = {
            "binding_id": "bind-claude-permission",
            "slot_role": "MODEL",
            "provider": "CLAUDE",
            "provider_session_ref": "claude-session-permission",
        }
        event = {
            "room_id": "room-permission",
            "room_event_id": "event-permission",
            "room_sequence": 1,
            "message": {
                "room_event_id": "event-permission",
                "author_role": "USER",
                "body_text": "Inspect the repository status",
            },
        }
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentRoomParticipantHostManager(
                room_event_observer=lambda value: observed.append(dict(value)),
                permission_observer=observe_permission,
                provider_factory=provider_factory,
            )
            try:
                manager.ensure(
                    binding=binding,
                    repository_root=self.root,
                    node="GCS",
                    mode="MASTER",
                )
                self.assertTrue(manager.submit(binding, event))
                self.assertTrue(permission_seen.wait(2))
                self.assertTrue(
                    manager.resolve_permission(
                        binding["binding_id"],
                        "permission_room_001",
                        "allow-once",
                    )
                )
                self.assertTrue(
                    manager._handles[binding["binding_id"]].worker.wait_idle()
                )
            finally:
                manager.close()

        self.assertEqual("allow-once", provider.selected_option)
        self.assertEqual(binding, permissions[0]["binding"])
        self.assertEqual(event, permissions[0]["event"])
        self.assertEqual(
            "permission_room_001",
            permissions[0]["permission"]["request_id"],
        )
        self.assertEqual("COMPLETED", observed[-1]["event"])

    def test_room_participant_disconnect_cancels_pending_permission(self) -> None:
        provider = RoomPermissionFakeProvider()
        permission_seen = threading.Event()

        def provider_factory(provider_name, _root, _target, store, _mode, _actor):
            provider.session_ref = store.session_ref_for(provider_name)
            provider.prepare_session = lambda: None
            return provider

        binding = {
            "binding_id": "bind-claude-cancel",
            "slot_role": "MODEL",
            "provider": "CLAUDE",
            "provider_session_ref": "claude-session-cancel",
        }
        event = {
            "room_id": "room-cancel",
            "room_event_id": "event-cancel",
            "room_sequence": 1,
            "message": {
                "room_event_id": "event-cancel",
                "author_role": "USER",
                "body_text": "Wait for permission",
            },
        }
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentRoomParticipantHostManager(
                room_event_observer=lambda _value: None,
                permission_observer=lambda _binding, _event, _permission: (
                    permission_seen.set()
                ),
                provider_factory=provider_factory,
            )
            manager.ensure(
                binding=binding,
                repository_root=self.root,
                node="GCS",
                mode="MASTER",
            )
            self.assertTrue(manager.submit(binding, event))
            self.assertTrue(permission_seen.wait(2))
            self.assertTrue(manager.stop(binding["binding_id"]))
            self.assertFalse(
                manager.resolve_permission(
                    binding["binding_id"],
                    "permission_room_001",
                    "allow-once",
                )
            )
            manager.close()

        self.assertIsNone(provider.selected_option)
        self.assertTrue(provider.closed)

    def test_room_participant_manager_fails_closed_on_resume_mismatch(self) -> None:
        provider = StreamingPreparedFakeProvider()

        def provider_factory(_provider, _root, _target, _store, _mode, _actor):
            return provider

        binding = {
            "binding_id": "bind-resume-mismatch",
            "slot_role": "MODEL",
            "provider": "CLAUDE",
            "provider_session_ref": "expected-session",
        }
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=False):
            manager = ResidentRoomParticipantHostManager(
                room_event_observer=lambda _event: None,
                provider_factory=provider_factory,
            )
            try:
                with self.assertRaisesRegex(
                    ProjectMasterHostError,
                    "ROOM_PARTICIPANT_SESSION_RESUME_MISMATCH",
                ):
                    manager.ensure(
                        binding=binding,
                        repository_root=self.root,
                        node="GCS",
                        mode="MASTER",
                    )
            finally:
                manager.close()

        self.assertTrue(provider.closed)

    def _worker(self) -> ProjectMasterConversationWorker:
        def post_reply(**values):
            self.replies.append(values)
            return {"status": "PROJECT_MASTER_REPLY_RECORDED"}

        def post_stream(**values):
            self.streams.append(values)
            return {"status": "PROJECT_MASTER_STREAM_EVENT_ACCEPTED"}

        return ProjectMasterConversationWorker(
            provider=self.provider,
            store=self.state,
            universe_endpoint="http://127.0.0.1:52973",
            project_id="GCS",
            bridge_token="bridge-token",
            surface_observer=self.surface_observer,
            reply_poster=post_reply,
            stream_poster=post_stream,
        )

    @classmethod
    def _envelope(cls) -> dict[str, Any]:
        return {
            "schema": MASTER_BRIDGE_ENVELOPE_SCHEMA,
            "bridge_id": "bridge_1234567890abcdef1234",
            "project_id": "GCS",
            "master_session_ref": "grok-cli:test-session",
            "message": {
                "schema": "universe.project-room-message.v1",
                "message_id": cls._message_id(),
                "project_id": "GCS",
                "idempotency_key": "room-message-live-001",
                "kind": "QUESTION",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "What should the Master review next?",
                "content_digest": "0" * 64,
                "delivery_state": "QUEUED_FOR_MASTER",
                "created_at": "2026-07-30T00:00:00Z",
            },
        }

    @staticmethod
    def _message_id() -> str:
        return "room_1234567890abcdef1234567890abcdef"


if __name__ == "__main__":
    unittest.main()
