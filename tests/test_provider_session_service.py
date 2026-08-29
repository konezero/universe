from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
import unittest
from typing import Any, Callable, Mapping
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.provider_session_service import (  # noqa: E402
    ProviderSessionError,
    ProviderSessionService,
)


CHAT_KEY = "provider_chat_0123456789abcdef01234567"


class FakeCoordinateStore:
    def __init__(self) -> None:
        self.observed: list[tuple[str, str]] = []

    def observe_provider_session(self, provider: str, session_ref: str) -> None:
        self.observed.append((provider, session_ref))


class FakeProviderHost:
    def __init__(
        self,
        requester: Callable[[Mapping[str, Any]], str | None],
    ) -> None:
        self.requester = requester
        self.store = FakeCoordinateStore()
        self.closed = False
        self.prepared: list[str] = []
        self.permission_waiting = False
        self.status_calls = 0
        self.block_started = threading.Event()
        self.block_release = threading.Event()
        self.work_statuses: list[dict[str, Any]] = []

    def set_permission_requester(
        self, requester: Callable[[Mapping[str, Any]], str | None]
    ) -> None:
        self.requester = requester

    def prepare(self, provider: str) -> Mapping[str, Any]:
        self.prepared.append(provider)
        return self.status()

    def status(self) -> Mapping[str, Any]:
        self.status_calls += 1
        if self.permission_waiting:
            raise AssertionError("snapshot must not call host.status during permission")
        return {
            "connection_state": "REUSED" if self.prepared else "STORED",
            "resident": bool(self.prepared),
            "requested_mode": "MASTER",
            "last_provider": "CODEX",
            "last_session_ref": "provider-secret-session-ref",
            "target_kind": "LOCAL_PATH",
            "target_id": "provider-secret-session-ref",
            "model_ref": "luna",
            "runtime_observation": {
                "schema": "universe.provider-runtime-observation.v1",
                "provider": "CODEX",
                "session_ref": "provider-secret-session-ref",
                "state": "READY",
                "quota_state": "AVAILABLE",
                "rate_limit_status": "allowed",
                "usage": {"input_tokens": 10, "private": "drop-me"},
                "quota": {
                    "schema": "universe.provider-quota-snapshot.v1",
                    "provider": "CODEX",
                    "source": "account/rateLimits/read",
                    "state": "AVAILABLE",
                    "secret": "drop-me",
                    "windows": [
                        {
                            "name": "PRIMARY",
                            "used_percent": 25,
                            "window_minutes": 300,
                            "resets_at": 1788220800,
                            "secret": "drop-me",
                        }
                    ],
                },
            },
        }

    def reply_stream(
        self,
        provider: str,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> Mapping[str, Any]:
        if message["body"] == "block":
            on_delta("before cancel ")
            self.block_started.set()
            self.block_release.wait(2)
            on_delta("after cancel")
            return {"text": "after cancel result", "session_ref": "provider-secret-session-ref"}
        if str(message["body"]).startswith("permission"):
            self.permission_waiting = True
            try:
                request_id = (
                    "permission-001"
                    if message["body"] == "permission"
                    else str(message["body"])
                )
                selected = self.requester(
                    {
                        "request_id": request_id,
                        "provider": provider,
                        "session_id": "provider-secret-session-ref",
                        "tool_call": {"title": "Read project"},
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
            finally:
                self.permission_waiting = False
            text = f"selected:{selected or 'none'}"
            on_delta(text)
            return {"text": text, "session_ref": "provider-secret-session-ref"}
        if message["body"] == "git milestones":
            self.work_statuses = [
                {
                    "operation": "COMMIT",
                    "state": "COMPLETED",
                    "exit_code": 0,
                    "source": "GIT_TRACE2",
                    "commit_sha": "d" * 40,
                    "short_sha": "ddddddd",
                    "commit_message": "Describe Git action",
                    "branch": "codex/action-history",
                    "changed_files": 3,
                },
                {
                    "operation": "PUSH",
                    "state": "FAILED",
                    "exit_code": 1,
                    "source": "GIT_TRACE2",
                },
            ]
        on_delta("hello ")
        on_delta("world")
        return {"text": "hello world", "session_ref": "provider-secret-session-ref"}

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        statuses, self.work_statuses = self.work_statuses, []
        return statuses

    def close(self) -> None:
        self.closed = True


class FakeRepositoryGitObserver:
    def __init__(self) -> None:
        self.milestones: list[dict[str, Any]] = []
        self.closed = False

    def drain_work_statuses(self) -> list[dict[str, Any]]:
        milestones, self.milestones = self.milestones, []
        return milestones

    def close(self) -> None:
        self.closed = True


class FakeActionStore:
    def __init__(self) -> None:
        self.todo_actions: list[tuple[str, dict[str, Any]]] = []
        self.actions: list[dict[str, Any]] = []

    def apply_todo_action(
        self, todo_id: str, value: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.todo_actions.append((todo_id, dict(value)))
        return {
            "status": "TODO_ACTION_APPLIED",
            "todo": {"todo_id": todo_id, "state": "IN_PROGRESS"},
        }

    def record_provider_session_action(
        self, _chat_key: str, action: Mapping[str, Any], *, retain: int
    ) -> Mapping[str, Any]:
        self.actions.append(dict(action))
        self.actions = self.actions[-retain:]
        return dict(action)

    def list_provider_session_actions(
        self, _chat_key: str, *, limit: int
    ) -> list[dict[str, Any]]:
        return self.actions[-limit:]

    def delete_provider_session_action(
        self, _chat_key: str, _action_id: str
    ) -> Mapping[str, Any] | None:
        return None


class ProviderSessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hosts: list[FakeProviderHost] = []
        self.descriptor_overrides: dict[str, Any] = {}

        def resolver(chat_key: str) -> Mapping[str, Any]:
            return {
                "chat_key": chat_key,
                "provider": "CODEX",
                "provider_session_ref": "provider-secret-session-ref",
                "project_id": "universe",
                "node": "universe",
                "mode": "MASTER",
                "repository_root": r"C:\workspace\universe",
                "current_anchor_ref": "MASTER-CURRENT-TEST",
                "alias": "Universe Master",
                "model_ref": "luna",
                "session_kind": "CHAT",
                "identity_state": "VERIFIED",
                **self.descriptor_overrides,
            }

        def host_factory(
            _descriptor: Mapping[str, Any],
            requester: Callable[[Mapping[str, Any]], str | None],
        ) -> FakeProviderHost:
            host = FakeProviderHost(requester)
            self.hosts.append(host)
            return host

        self.resolver = resolver
        self.host_factory = host_factory
        self.service = self._new_service()

    def _new_service(
        self,
        *,
        retained_idempotency: int | None = None,
        retained_permissions: int | None = None,
        repository_git_observer_factory: Callable[[Path], Any] | None = None,
        action_store: Any = None,
        action_observer: Callable[[str, Mapping[str, Any]], Mapping[str, Any] | None] | None = None,
    ) -> ProviderSessionService:
        return ProviderSessionService(
            resolver=self.resolver,
            host_factory=self.host_factory,
            permission_timeout_seconds=2,
            retained_idempotency=retained_idempotency,
            retained_permissions=retained_permissions,
            action_store=action_store,
            action_observer=action_observer,
            repository_git_observer_factory=repository_git_observer_factory,
            repository_git_poll_seconds=0.01,
        )

    def tearDown(self) -> None:
        self.service.close()

    def test_common_message_channel_envelope_routes_codex_and_grok(self) -> None:
        for index, provider in enumerate(("CODEX", "GROK"), start=1):
            with self.subTest(provider=provider):
                self.descriptor_overrides["provider"] = provider
                message_id = f"msg-channel-{index}"
                accepted = self.service.submit_channel(
                    CHAT_KEY,
                    {
                        "schema": "universe.host-message-channel.v1",
                        "message_id": message_id,
                        "session_anchor_ref": "anchor-channel-test",
                        "content": f"hello {provider.lower()}",
                        "meta": {"provider": provider, "kind": "INSTRUCTION"},
                    },
                )
                self.assertEqual(
                    "PROVIDER_SESSION_INPUT_ACCEPTED", accepted["status"]
                )
                self.assertEqual(
                    {
                        "schema": "universe.host-message-channel.v1",
                        "message_id": message_id,
                        "session_anchor_ref": "anchor-channel-test",
                        "adapter": "PROVIDER_NATIVE",
                    },
                    accepted["message_channel"],
                )
                self.assertTrue(self.service.wait_idle(CHAT_KEY))
                self.assertEqual(
                    f"hello {provider.lower()}",
                    self.service.snapshot(CHAT_KEY)["messages"][-2]["body"],
                )
                self.service.close()
                self.service = self._new_service()

    def test_common_message_channel_rejects_unknown_schema(self) -> None:
        with self.assertRaises(ProviderSessionError) as raised:
            self.service.submit_channel(
                CHAT_KEY,
                {
                    "schema": "unknown.channel.v1",
                    "message_id": "msg-invalid-schema",
                    "session_anchor_ref": "anchor-channel-test",
                    "content": "must not dispatch",
                    "meta": {},
                },
            )
        self.assertEqual(
            "PROVIDER_CHANNEL_SCHEMA_UNSUPPORTED", raised.exception.code
        )
        self.assertEqual([], self.hosts)

    def test_direct_turn_streams_without_room_queue_or_secret_projection(self) -> None:
        accepted = self.service.submit(
            CHAT_KEY,
            {"body": "hello", "idempotency_key": "turn-001"},
        )
        self.assertEqual("PROVIDER_SESSION_INPUT_ACCEPTED", accepted["status"])
        self.assertFalse(accepted["room_queue_used"])
        self.assertTrue(self.service.wait_idle(CHAT_KEY))

        snapshot = self.service.snapshot(CHAT_KEY)
        self.assertEqual(["USER", "ASSISTANT"], [m["role"] for m in snapshot["messages"]])
        self.assertEqual("hello world", snapshot["messages"][-1]["body"])
        self.assertEqual("COMPLETED", snapshot["messages"][-1]["state"])
        self.assertEqual("COMPLETED", snapshot["work_status"]["state"])
        self.assertEqual("PROVIDER_TURN", snapshot["work_status"]["operation"])
        event_states = [
            event["payload"]["work_status"]["state"]
            for event in self.service.events.wait(
                CHAT_KEY, after_event_id=0, timeout_seconds=0.1
            )
            if event["payload"].get("type") == "PROVIDER_SESSION_WORK_STATUS"
        ]
        self.assertEqual(["STARTED", "COMPLETED"], event_states)
        self.assertEqual(
            [("CODEX", "provider-secret-session-ref")],
            self.hosts[0].store.observed,
        )
        public = json.dumps(snapshot)
        self.assertNotIn("provider-secret-session-ref", public)
        self.assertNotIn("repository_root", public)
        self.assertNotIn("source_path", public)
        self.assertNotIn("session_ref", public)
        self.assertNotIn("drop-me", public)
        self.assertEqual(
            {"input_tokens": 10},
            snapshot["connection"]["runtime_observation"]["usage"],
        )
        self.assertEqual(
            {
                "schema": "universe.provider-quota-snapshot.v1",
                "provider": "CODEX",
                "source": "account/rateLimits/read",
                "state": "AVAILABLE",
                "windows": [
                    {
                        "name": "PRIMARY",
                        "used_percent": 25,
                        "window_minutes": 300,
                        "resets_at": 1788220800,
                    }
                ],
            },
            snapshot["connection"]["runtime_observation"]["quota"],
        )
        self.assertFalse(snapshot["room_queue_used"])

    def test_supervisor_attested_target_is_accepted_as_persistent_session(self) -> None:
        self.descriptor_overrides.update(
            {
                "identity_state": "VERIFIED",
                "identity_source": "SESSION_SUPERVISOR",
            }
        )
        snapshot = self.service.snapshot(CHAT_KEY)
        self.assertEqual("PROVIDER_SESSION_SNAPSHOT_COLLECTED", snapshot["status"])

    def test_git_trace2_milestones_publish_without_command_arguments(self) -> None:
        self.service.submit(
            CHAT_KEY,
            {"body": "git milestones", "idempotency_key": "turn-git-001"},
        )
        self.assertTrue(self.service.wait_idle(CHAT_KEY))

        events = self.service.events.wait(
            CHAT_KEY, after_event_id=0, timeout_seconds=0.1
        )
        statuses = [
            event["payload"]["work_status"]
            for event in events
            if event["payload"].get("type") == "PROVIDER_SESSION_WORK_STATUS"
        ]
        self.assertEqual(
            ["PROVIDER_TURN", "PROVIDER_TURN", "COMMIT", "PUSH"],
            [status["operation"] for status in statuses],
        )
        self.assertEqual("GIT_EXIT_1", statuses[-1]["error_code"])
        self.assertEqual(
            {"source": "GIT_TRACE2", "exit_code": 1}, statuses[-1]["details"]
        )
        snapshot = self.service.snapshot(CHAT_KEY)
        self.assertEqual(["COMMIT", "PUSH"], [item["operation"] for item in snapshot["actions"]])
        commit_action = snapshot["actions"][0]
        self.assertEqual("INFORMATIONAL", commit_action["kind"])
        self.assertEqual("ddddddd · Describe Git action · 3 files", commit_action["summary"])
        deleted = self.service.delete_action(CHAT_KEY, commit_action["action_id"])
        self.assertEqual("PROVIDER_SESSION_ACTION_DELETED", deleted["status"])
        self.assertEqual(["PUSH"], [item["operation"] for item in self.service.snapshot(CHAT_KEY)["actions"]])
        public = json.dumps(statuses)
        self.assertNotIn("argv", public)
        self.assertNotIn("repository_root", public)

    def test_terminal_git_action_records_event_projection(self) -> None:
        observed: list[tuple[str, Mapping[str, Any]]] = []

        def observe(chat_key: str, action: Mapping[str, Any]) -> Mapping[str, Any]:
            observed.append((chat_key, dict(action)))
            return {"status": "EVENT_PROJECTED", "message_id": "msg_result_001"}

        self.service.close()
        self.service = self._new_service(action_observer=observe)
        self.service._publish_work_status(
            CHAT_KEY,
            "message-git-event-001",
            "COMPLETED",
            operation="COMMIT",
            details={"source": "GIT_TRACE2", "commit_sha": "a" * 40},
        )

        self.assertEqual(CHAT_KEY, observed[0][0])
        self.assertEqual("COMMIT", observed[0][1]["operation"])
        self.assertEqual(
            {"status": "EVENT_PROJECTED", "message_id": "msg_result_001"},
            self.service.snapshot(CHAT_KEY)["actions"][0]["event_projection"],
        )

    def test_explicit_git_todo_marker_starts_but_does_not_complete_todo(
        self,
    ) -> None:
        action_store = FakeActionStore()
        self.service.close()
        self.service = self._new_service(action_store=action_store)

        commit_sha = "f" * 40
        marked = self.service._publish_work_status(
            CHAT_KEY,
            "message-git-hook-001",
            "COMPLETED",
            operation="COMMIT",
            details={
                "source": "GIT_TRACE2",
                "exit_code": 0,
                "commit_sha": commit_sha,
                "short_sha": "fffffff",
                "commit_message": "feat: hook [Universe-Todo: todo_hook_001]",
                "branch": "main",
                "changed_files": 2,
            },
        )
        self.assertEqual("todo_hook_001", marked["details"]["todo_id"])
        self.assertEqual(
            "IN_PROGRESS", action_store.actions[0]["todo_transition"]["state"]
        )
        self.assertEqual(
            [
                (
                    "todo_hook_001",
                    {
                        "action_id": "git-commit-" + commit_sha,
                        "outcome": "STARTED",
                        "source": "GIT_TRACE2",
                        "evidence_ref": "git://commit/" + commit_sha,
                    },
                )
            ],
            action_store.todo_actions,
        )

        self.service._publish_work_status(
            CHAT_KEY,
            "message-git-hook-002",
            "COMPLETED",
            operation="COMMIT",
            details={
                "source": "GIT_TRACE2",
                "exit_code": 0,
                "commit_sha": "e" * 40,
                "short_sha": "eeeeeee",
                "commit_message": "feat: unrelated",
            },
        )
        self.service._publish_work_status(
            CHAT_KEY,
            "message-git-hook-003",
            "COMPLETED",
            operation="PUSH",
            details={
                "source": "GIT_TRACE2",
                "exit_code": 0,
                "commit_sha": commit_sha,
                "short_sha": "fffffff",
                "commit_message": "feat: hook [Universe-Todo: todo_hook_001]",
                "branch": "main",
                "remote": "origin",
            },
        )
        self.assertEqual(1, len(action_store.todo_actions))

    def test_repository_git_milestone_reaches_registered_session_without_turn(self) -> None:
        observer = FakeRepositoryGitObserver()
        self.service.close()
        self.service = self._new_service(
            repository_git_observer_factory=lambda _root: observer
        )
        self.service.snapshot(CHAT_KEY)
        observer.milestones = [
            {
                "operation": "PUSH",
                "state": "COMPLETED",
                "exit_code": 0,
                "source": "GIT_TRACE2",
                "commit_sha": "e" * 40,
                "short_sha": "eeeeeee",
                "commit_message": "External push",
                "branch": "codex/external",
                "remote": "origin",
                "changed_files": 1,
            }
        ]
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            actions = self.service.snapshot(CHAT_KEY)["actions"]
            if actions:
                break
            time.sleep(0.01)

        self.assertEqual(["PUSH"], [item["operation"] for item in actions])
        self.assertEqual("codex/external -> origin · eeeeeee", actions[0]["summary"])

    def test_internal_callbacks_bind_reply_before_terminal_event(self) -> None:
        observed: list[tuple[str, str]] = []
        accepted = self.service.submit(
            CHAT_KEY,
            {"body": "hello", "idempotency_key": "turn-callback-001"},
            on_accepted=lambda reply: observed.append(
                ("accepted", str(reply["message_id"]))
            ),
            on_terminal=lambda reply: observed.append(
                (str(reply["state"]).lower(), str(reply["message_id"]))
            ),
        )
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        self.assertEqual(
            [
                ("accepted", accepted["reply"]["message_id"]),
                ("completed", accepted["reply"]["message_id"]),
            ],
            observed,
        )

    def test_permission_round_trip_is_scoped_to_opaque_chat_key(self) -> None:
        self.service.submit(
            CHAT_KEY,
            {"body": "permission", "idempotency_key": "turn-permission"},
        )
        deadline = time.monotonic() + 2
        pending: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            permissions = self.service.snapshot(CHAT_KEY)["permissions"]
            if permissions:
                pending = permissions[0]
                break
            time.sleep(0.01)
        self.assertIsNotNone(pending)
        assert pending is not None
        self.assertEqual("PROVIDER_SESSION", pending["scope_kind"])
        self.assertNotIn("session_id", pending)
        self.assertEqual(1, self.hosts[0].status_calls)

        resolved, changed = self.service.resolve_permission(
            CHAT_KEY,
            pending["request_id"],
            "allow-once",
        )
        self.assertTrue(changed)
        self.assertEqual("RESOLVED", resolved["state"])
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        self.assertEqual(
            "selected:allow-once",
            self.service.snapshot(CHAT_KEY)["messages"][-1]["body"],
        )

    def test_unverified_and_worker_targets_fail_closed(self) -> None:
        base = self.service.resolver

        def worker(chat_key: str) -> Mapping[str, Any]:
            return {**base(chat_key), "session_kind": "WORKER"}

        self.service.resolver = worker
        with self.assertRaisesRegex(ProviderSessionError, "workers"):
            self.service.snapshot(CHAT_KEY)

        def unverified(chat_key: str) -> Mapping[str, Any]:
            return {**base(chat_key), "identity_state": "UNKNOWN"}

        self.service.resolver = unverified
        with self.assertRaisesRegex(ProviderSessionError, "not verified"):
            self.service.snapshot(CHAT_KEY)

    def test_duplicate_idempotency_returns_same_turn(self) -> None:
        first = self.service.submit(
            CHAT_KEY,
            {"body": "hello", "idempotency_key": "same-turn"},
        )
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        second = self.service.submit(
            CHAT_KEY,
            {"body": "different", "idempotency_key": "same-turn"},
        )
        self.assertEqual(first["message"]["message_id"], second["message"]["message_id"])
        self.assertEqual(2, len(self.service.snapshot(CHAT_KEY)["messages"]))

    def test_concurrent_submit_is_busy_before_worker_thread_starts(self) -> None:
        original_start = threading.Thread.start
        provider_start_entered = threading.Event()
        release_provider_start = threading.Event()
        delayed = False

        def delayed_start(thread: threading.Thread) -> None:
            nonlocal delayed
            if thread.name.startswith("provider-session-") and not delayed:
                delayed = True
                provider_start_entered.set()
                release_provider_start.wait(2)
            original_start(thread)

        first_errors: list[BaseException] = []

        def first_submit() -> None:
            try:
                self.service.submit(
                    CHAT_KEY,
                    {"body": "first", "idempotency_key": "concurrent-first"},
                )
            except BaseException as error:  # noqa: BLE001 - test captures thread error
                first_errors.append(error)

        with patch.object(threading.Thread, "start", delayed_start):
            caller = threading.Thread(target=first_submit, name="first-submit-caller")
            caller.start()
            self.assertTrue(provider_start_entered.wait(1))
            try:
                with self.assertRaises(ProviderSessionError) as raised:
                    self.service.submit(
                        CHAT_KEY,
                        {"body": "second", "idempotency_key": "concurrent-second"},
                    )
                self.assertEqual("PROVIDER_SESSION_BUSY", raised.exception.code)
            finally:
                release_provider_start.set()
            caller.join(timeout=2)

        self.assertFalse(caller.is_alive())
        self.assertEqual([], first_errors)
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        self.assertEqual(2, len(self.service.snapshot(CHAT_KEY)["messages"]))

    def test_cancel_suppresses_late_provider_result_without_claiming_process_stop(self) -> None:
        self.service.submit(CHAT_KEY, {"body": "block", "idempotency_key": "cancel-turn"})
        host = self.hosts[0]
        self.assertTrue(host.block_started.wait(1))

        cancelled = self.service.cancel(CHAT_KEY)
        self.assertEqual("PROVIDER_SESSION_CANCELLATION_REQUESTED", cancelled["status"])
        self.assertEqual("CANCELLATION_REQUESTED", cancelled["message"]["state"])
        repeated = self.service.cancel(CHAT_KEY)
        self.assertEqual("PROVIDER_SESSION_CANCELLATION_ALREADY_REQUESTED", repeated["status"])

        host.block_release.set()
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        reply = self.service.snapshot(CHAT_KEY)["messages"][-1]
        self.assertEqual("CANCELLED", reply["state"])
        self.assertEqual("before cancel ", reply["body"])
        self.assertNotIn("after cancel", reply["body"])

    def test_cancel_after_completed_reply_is_not_reapplied(self) -> None:
        self.service.submit(CHAT_KEY, {"body": "hello", "idempotency_key": "complete-then-cancel"})
        self.assertTrue(self.service.wait_idle(CHAT_KEY))

        cancelled = self.service.cancel(CHAT_KEY)

        self.assertEqual("PROVIDER_SESSION_CANCEL_NOT_REQUIRED", cancelled["status"])
        self.assertNotIn("message", cancelled)
        self.assertEqual("COMPLETED", self.service.snapshot(CHAT_KEY)["messages"][-1]["state"])

    def test_cancel_does_not_reapply_to_terminal_reply_during_worker_cleanup(self) -> None:
        self.service.submit(CHAT_KEY, {"body": "block", "idempotency_key": "terminal-race"})
        host = self.hosts[0]
        self.assertTrue(host.block_started.wait(1))
        with self.service._lock:
            message_id = self.service._active_message_ids[CHAT_KEY]
            reply = next(message for message in self.service._messages[CHAT_KEY] if message["message_id"] == message_id)
            reply["state"] = "COMPLETED"

        cancelled = self.service.cancel(CHAT_KEY)

        self.assertEqual("PROVIDER_SESSION_CANCEL_NOT_REQUIRED", cancelled["status"])
        self.assertEqual("COMPLETED", cancelled["message"]["state"])
        host.block_release.set()
        self.assertTrue(self.service.wait_idle(CHAT_KEY))

    def test_model_change_replaces_idle_handle_and_closes_previous_host(self) -> None:
        self.service.submit(
            CHAT_KEY,
            {"body": "first", "idempotency_key": "model-first"},
        )
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        first_host = self.hosts[0]

        self.descriptor_overrides["model_ref"] = "terra"
        self.service.submit(
            CHAT_KEY,
            {"body": "second", "idempotency_key": "model-second"},
        )
        self.assertTrue(self.service.wait_idle(CHAT_KEY))

        self.assertTrue(first_host.closed)
        self.assertEqual(2, len(self.hosts))
        self.assertEqual("terra", self.service.snapshot(CHAT_KEY)["target"]["model_ref"])

    def test_close_cancels_pending_permission_without_hanging(self) -> None:
        self.service.submit(
            CHAT_KEY,
            {"body": "permission-close", "idempotency_key": "permission-close"},
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.service.snapshot(CHAT_KEY)["permissions"]:
                break
            time.sleep(0.01)
        else:
            self.fail("permission request did not become pending")

        closer = threading.Thread(target=self.service.close, name="service-close-test")
        closer.start()
        closer.join(timeout=2)
        self.assertFalse(closer.is_alive())
        self.assertTrue(self.hosts[0].closed)
        self.assertTrue(self.service.wait_idle(CHAT_KEY))

    def test_restart_reselects_exact_provider_coordinate_without_public_leak(self) -> None:
        self.service.submit(
            CHAT_KEY,
            {"body": "before", "idempotency_key": "restart-before"},
        )
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        self.service.close()

        self.service = self._new_service()
        self.service.submit(
            CHAT_KEY,
            {"body": "after", "idempotency_key": "restart-after"},
        )
        self.assertTrue(self.service.wait_idle(CHAT_KEY))
        restarted = self.hosts[-1]
        self.assertEqual(
            [("CODEX", "provider-secret-session-ref")],
            restarted.store.observed,
        )
        self.assertNotIn(
            "provider-secret-session-ref",
            json.dumps(self.service.snapshot(CHAT_KEY)),
        )

    def test_process_local_idempotency_and_permission_history_are_bounded(self) -> None:
        self.service.close()
        self.service = self._new_service(
            retained_idempotency=2,
            retained_permissions=2,
        )
        for index in range(3):
            self.service.submit(
                CHAT_KEY,
                {
                    "body": f"turn-{index}",
                    "idempotency_key": f"bounded-turn-{index}",
                },
            )
            self.assertTrue(self.service.wait_idle(CHAT_KEY))
        self.assertLessEqual(len(self.service._idempotency), 2)

        for index in range(3):
            request_id = f"permission-{index}"
            self.service.submit(
                CHAT_KEY,
                {"body": request_id, "idempotency_key": f"bounded-{request_id}"},
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                pending = [
                    item
                    for item in self.service.snapshot(CHAT_KEY)["permissions"]
                    if item["request_id"] == request_id and item["state"] == "PENDING"
                ]
                if pending:
                    break
                time.sleep(0.01)
            else:
                self.fail(f"{request_id} did not become pending")
            self.service.resolve_permission(CHAT_KEY, request_id, "reject-once")
            self.assertTrue(self.service.wait_idle(CHAT_KEY))
        self.assertLessEqual(len(self.service._permissions), 2)

    def test_observer_excerpts_fill_empty_snapshot_without_room_queue(self) -> None:
        created = self.service.observe_excerpts(
            CHAT_KEY,
            [
                {"excerpt_id": "semantic_user", "role": "USER", "text": "from vendor"},
                {"excerpt_id": "semantic_assistant", "role": "ASSISTANT", "text": "reply"},
            ],
            replace_observer=True,
        )
        snapshot = self.service.snapshot(CHAT_KEY)
        self.assertEqual(2, len(created))
        self.assertEqual(
            ["from vendor", "reply"],
            [item["body"] for item in snapshot["messages"]],
        )
        self.assertTrue(all(item["origin"] == "PROVIDER_OBSERVER" for item in created))
        self.assertFalse(snapshot["room_queue_used"])


if __name__ == "__main__":
    unittest.main()
