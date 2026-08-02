from __future__ import annotations

import hashlib
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
from project_master_bridge import (  # noqa: E402
    MASTER_BRIDGE_ENVELOPE_SCHEMA,
    ProjectMasterBridgeError,
)
from project_seed_apply import build_project_seed_asset_approval  # noqa: E402
from project_seed_assets import build_project_seed_asset_proposal  # noqa: E402
from project_skill_plan_apply import build_project_skill_plan_approval  # noqa: E402
from project_master_host import (  # noqa: E402
    ClaudeProjectMasterRuntime,
    CodexProjectMasterRuntime,
    GrokProjectMasterRuntime,
    LiveProjectMasterBridgeHost,
    ProjectMasterHostError,
    ProjectMasterConversationWorker,
    ProjectModeCoordinator,
    ProjectMasterSessionStore,
    ResidentModeSessionHost,
    ResidentProjectMasterHostManager,
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

    @property
    def session_ref(self) -> str:
        return "fake-acp:session"

    def reply_stream(self, prompt: str, on_delta) -> str:
        self.prompts.append(prompt)
        on_delta(self.answer)
        return self.answer

    def close(self) -> None:
        self.closed = True


class FakeSurfaceObserver:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, Any]] = []
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

    def apply_file(
        self,
        *,
        target: Path,
        content: bytes,
        operation: str,
        boundary: str,
        approval_evidence_ref: str,
        request_ref: str,
    ) -> Mapping[str, Any]:
        self.mutations.append(
            {
                "target": target,
                "operation": operation,
                "boundary": boundary,
                "approval_evidence_ref": approval_evidence_ref,
                "request_ref": request_ref,
            }
        )
        target.write_bytes(content)
        return {
            "status": "FILE_MUTATION_APPLIED",
            "receipt_id": f"permit-{len(self.mutations)}",
        }


class ProjectMasterHostTests(unittest.TestCase):
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
        self.assertEqual(
            "REPLACED", state.observe_provider_session("CODEX", "codex-1")
        )
        sessions = supervisor.list_sessions(node="GCS", mode="MASTER")
        self.assertEqual(2, len(sessions))
        self.assertEqual(
            ["CODEX"], [item["provider"] for item in sessions if item["is_default"]]
        )
        self.assertEqual("codex-1", state.session_ref_for("CODEX"))
        self.assertIsNone(state.session_ref_for("GROK"))

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
                    "mode_current_anchor": {"status": "MODE_CURRENT_ANCHOR_OBSERVED"},
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
            source_commit_resolver=lambda _root: "a" * 40,
        )
        with patch(
            "project_master_host._required_host_executable",
            return_value=Path(sys.executable),
        ):
            coordinator.prepare()
            coordinator.observe(self._envelope()["message"])

        self.assertEqual("MASTER", requests[0]["mode"])
        self.assertEqual("grok-cli:session-001", requests[0]["host_session_ref"])
        self.assertEqual("UNIVERSE_UI", requests[1]["commander_surface"])
        self.assertEqual(
            f"universe://project-room/messages/{self._message_id()}",
            requests[1]["evidence_ref"],
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
            source_commit_resolver=lambda _root: "a" * 40,
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
                "delivery_state": "DELIVERED_TO_MASTER",
                "created_at": "2026-07-30T00:00:00Z",
            },
        }

    @staticmethod
    def _message_id() -> str:
        return "room_1234567890abcdef1234567890abcdef"


if __name__ == "__main__":
    unittest.main()
