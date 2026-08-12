from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from claude_resident_session import (  # noqa: E402
    SESSION_QUOTA_EXHAUSTED,
    SESSION_READY,
    SESSION_STOPPED,
    ClaudeResidentError,
    ClaudeResidentSession,
)


class FakeClaudeProcess:
    """Deterministic stand-in for one resident ``claude -p`` process."""

    instances: list["FakeClaudeProcess"] = []

    def __init__(
        self,
        *,
        executable: Path,
        arguments: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        event_handler,
    ) -> None:
        self.executable = executable
        self.arguments = tuple(arguments)
        self.cwd = cwd
        self.environment = dict(environment)
        self.event_handler = event_handler
        self.sent: list[str] = []
        self.closed = False
        self.alive = True
        self.script: list[list[dict[str, Any]]] = []
        self.instances.append(self)

    def send_user_message(self, text: str) -> None:
        self.sent.append(text)
        events = self.script.pop(0) if self.script else self._default_events()
        for event in events:
            self.event_handler(event)

    def _default_events(self) -> list[dict[str, Any]]:
        index = len(self.sent)
        return [
            {"type": "system", "subtype": "init", "session_id": "claude-observed"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"answer-{index}"}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"answer-{index}",
                "session_id": "claude-observed",
            },
        ]

    def stderr_detail(self) -> str:
        return ""

    def close(self) -> None:
        self.closed = True
        self.alive = False


class ClaudeResidentSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        FakeClaudeProcess.instances.clear()
        self.observed: list[str] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _session(self, **overrides) -> ClaudeResidentSession:
        kwargs: dict[str, Any] = {
            "executable": self.root / "claude.exe",
            "cwd": self.root,
            "environment": {},
            "system_prompt": "MODE GREETING",
            "session_id": None,
            "session_observer": self.observed.append,
            "process_factory": FakeClaudeProcess,
        }
        kwargs.update(overrides)
        return ClaudeResidentSession(**kwargs)

    def test_two_messages_reuse_one_process(self) -> None:
        session = self._session()
        first = session.send_message("one", lambda _d: None)
        second = session.send_message("two", lambda _d: None)

        self.assertEqual("answer-1", first)
        self.assertEqual("answer-2", second)
        # A resident session must not re-exec the CLI per message.
        self.assertEqual(1, session.launch_count)
        self.assertEqual(1, len(FakeClaudeProcess.instances))
        self.assertEqual(2, len(FakeClaudeProcess.instances[0].sent))

    def test_stream_arguments_use_stream_json_and_no_shell(self) -> None:
        session = self._session(effort="MAX")
        session.send_message("one", lambda _d: None)
        arguments = FakeClaudeProcess.instances[0].arguments

        self.assertEqual("-p", arguments[0])
        self.assertEqual(
            "stream-json", arguments[arguments.index("--input-format") + 1]
        )
        self.assertEqual(
            "stream-json", arguments[arguments.index("--output-format") + 1]
        )
        self.assertIn("--verbose", arguments)
        self.assertEqual("max", arguments[arguments.index("--effort") + 1])

    def test_delta_order_is_preserved(self) -> None:
        session = self._session()
        process_holder: list[FakeClaudeProcess] = []

        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [
                    {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "A"}}},
                    {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "B"}}},
                    {"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "C"}}},
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "session_id": "s1",
                    },
                ]
            ]
            process_holder.append(process)
            return process

        session = self._session(process_factory=factory)
        deltas: list[str] = []
        answer = session.send_message("go", deltas.append)

        self.assertEqual(["A", "B", "C"], deltas)
        self.assertEqual("ABC", answer)

    def test_session_id_observed_and_resume_used_next_launch(self) -> None:
        session = self._session()
        session.send_message("one", lambda _d: None)

        self.assertEqual(["claude-observed"], self.observed)
        self.assertEqual("claude-code:claude-observed", session.session_ref)

        # Simulate a process failure; the next request must resume.
        FakeClaudeProcess.instances[0].alive = False
        session.send_message("two", lambda _d: None)

        second = FakeClaudeProcess.instances[1].arguments
        self.assertIn("--resume", second)
        self.assertEqual(
            "claude-observed", second[second.index("--resume") + 1]
        )
        self.assertEqual(2, session.launch_count)

    def test_new_session_uses_session_id_flag(self) -> None:
        session = self._session(session_id=None)
        session.session_id = "fresh-id"
        session.send_message("one", lambda _d: None)
        arguments = FakeClaudeProcess.instances[0].arguments

        self.assertIn("--session-id", arguments)
        self.assertNotIn("--resume", arguments)

    def test_rebind_restarts_same_session_in_target_directory(self) -> None:
        target = self.root / "target"
        target.mkdir()
        session = self._session(session_id="existing")
        session.start_or_resume()
        rebound = session.rebind_working_directory(target)
        self.assertEqual(str(target.resolve()), rebound)
        self.assertEqual(2, session.launch_count)
        self.assertTrue(FakeClaudeProcess.instances[0].closed)
        replacement = FakeClaudeProcess.instances[1]
        self.assertEqual(target.resolve(), replacement.cwd)
        self.assertIn("--resume", replacement.arguments)
        self.assertEqual(
            "existing", replacement.arguments[replacement.arguments.index("--resume") + 1]
        )

    def test_failed_rebind_restores_previous_directory_and_process(self) -> None:
        target = self.root / "target"
        target.mkdir()
        launches = 0

        def factory(**kwargs):
            nonlocal launches
            launches += 1
            if launches == 2:
                raise OSError("target launch failed")
            return FakeClaudeProcess(**kwargs)

        session = self._session(session_id="existing", process_factory=factory)
        session.start_or_resume()
        with self.assertRaisesRegex(ClaudeResidentError, "CLAUDE_CWD_REBIND_FAILED"):
            session.rebind_working_directory(target)
        self.assertEqual(self.root.resolve(), session.cwd)
        self.assertEqual(2, session.launch_count)
        self.assertEqual(self.root.resolve(), FakeClaudeProcess.instances[-1].cwd)
        self.assertEqual(SESSION_READY, session.session_status())

    def test_greeting_sent_once_on_new_session_only(self) -> None:
        session = self._session()
        session.send_message("first", lambda _d: None)
        session.send_message("second", lambda _d: None)
        sent = FakeClaudeProcess.instances[0].sent

        self.assertIn("MODE GREETING", sent[0])
        self.assertNotIn("MODE GREETING", sent[1])

    def test_resumed_session_does_not_resend_greeting(self) -> None:
        session = self._session(session_id="existing")
        session.send_message("first", lambda _d: None)

        self.assertNotIn("MODE GREETING", FakeClaudeProcess.instances[0].sent[0])

    def test_allowed_warning_telemetry_does_not_stop_the_turn(self) -> None:
        """Near-limit telemetry still completes; only a blocked status stops."""

        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [
                    {
                        "type": "rate_limit_event",
                        "rate_limit_info": {
                            "status": "allowed_warning",
                            "utilization": 0.92,
                            "surpassedThreshold": 0.9,
                        },
                    },
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "fine",
                        "session_id": "s1",
                    },
                ]
            ]
            return process

        session = self._session(process_factory=factory)
        self.assertEqual("fine", session.send_message("go", lambda _d: None))
        self.assertEqual(SESSION_READY, session.session_status())

    def test_allowed_rate_limit_telemetry_does_not_stop_the_turn(self) -> None:
        """A normal turn emits rate_limit_event with status 'allowed'."""

        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [
                    {
                        "type": "rate_limit_event",
                        "rate_limit_info": {
                            "status": "allowed",
                            "rateLimitType": "five_hour",
                        },
                    },
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "fine",
                        "session_id": "s1",
                    },
                ]
            ]
            return process

        session = self._session(process_factory=factory)
        self.assertEqual("fine", session.send_message("go", lambda _d: None))
        self.assertEqual(SESSION_READY, session.session_status())

    def test_quota_exhausted_does_not_retry(self) -> None:
        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [
                    {
                        "type": "rate_limit_event",
                        "rate_limit_info": {"status": "rejected"},
                    }
                ]
            ]
            return process

        session = self._session(process_factory=factory)
        with self.assertRaises(ClaudeResidentError) as caught:
            session.send_message("go", lambda _d: None)
        self.assertIn("CLAUDE_QUOTA_EXHAUSTED", str(caught.exception))
        self.assertEqual(SESSION_QUOTA_EXHAUSTED, session.session_status())
        observation = session.runtime_observation()
        self.assertEqual("CLAUDE", observation["provider"])
        self.assertEqual("EXHAUSTED", observation["quota_state"])

        # A second request must not launch another process.
        with self.assertRaises(ClaudeResidentError):
            session.send_message("again", lambda _d: None)
        self.assertEqual(1, session.launch_count)

    def test_stream_close_fails_pending_turn(self) -> None:
        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [[{"type": "__stream_closed__"}]]
            return process

        session = self._session(process_factory=factory)
        with self.assertRaises(ClaudeResidentError) as caught:
            session.send_message("go", lambda _d: None)
        self.assertIn("CLAUDE_STREAM_CLOSED", str(caught.exception))

    def test_close_stops_process_and_reports_stopped(self) -> None:
        session = self._session()
        session.send_message("one", lambda _d: None)
        self.assertEqual(SESSION_READY, session.session_status())

        session.close()
        self.assertTrue(FakeClaudeProcess.instances[0].closed)
        self.assertEqual(SESSION_STOPPED, session.session_status())

    def test_concurrent_turns_are_serialized(self) -> None:
        release = threading.Event()

        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            original = process.send_user_message

            def blocking(text: str) -> None:
                release.wait(timeout=5)
                original(text)

            process.send_user_message = blocking  # type: ignore[assignment]
            return process

        session = self._session(process_factory=factory)
        errors: list[Exception] = []

        def second() -> None:
            try:
                session.send_message("second", lambda _d: None)
            except Exception as error:  # noqa: BLE001 - captured for assertion
                errors.append(error)

        worker = threading.Thread(target=lambda: session.send_message("first", lambda _d: None))
        worker.start()
        # Give the first turn time to take the lock.
        threading.Event().wait(0.1)
        second()
        release.set()
        worker.join(timeout=5)

        self.assertEqual(1, len(errors))
        self.assertIn("CLAUDE_TURN_ALREADY_ACTIVE", str(errors[0]))

    def test_permission_bridge_arguments_are_wired(self) -> None:
        config = self.root / "mcp.json"
        config.write_text("{}", encoding="utf-8")
        session = self._session(permission_mcp_config=config)
        session.send_message("one", lambda _d: None)
        arguments = FakeClaudeProcess.instances[0].arguments

        self.assertEqual(
            "plan", arguments[arguments.index("--permission-mode") + 1]
        )
        self.assertEqual(
            str(config), arguments[arguments.index("--mcp-config") + 1]
        )
        self.assertEqual(
            "mcp__universe_permission__approve",
            arguments[arguments.index("--permission-prompt-tool") + 1],
        )
        self.assertIn("--strict-mcp-config", arguments)

    def test_bypass_arguments_are_refused(self) -> None:
        session = self._session(extra_arguments=("--dangerously-skip-permissions",))
        with self.assertRaises(ClaudeResidentError) as caught:
            session.send_message("one", lambda _d: None)
        self.assertIn("CLAUDE_PERMISSION_BYPASS_FORBIDDEN", str(caught.exception))
        self.assertEqual(0, session.launch_count)

    def test_turn_is_bound_to_permission_bridge(self) -> None:
        bound: list[str | None] = []

        class Bridge:
            def bind_turn(self, turn_id):
                bound.append(turn_id)

        session = self._session(permission_bridge=Bridge())
        session.send_message("one", lambda _d: None)
        session.send_message("two", lambda _d: None)

        self.assertEqual(2, len(bound))
        self.assertTrue(all(bound))
        # Each turn gets its own identity.
        self.assertNotEqual(bound[0], bound[1])

    def test_permission_token_in_provider_environment_is_refused(self) -> None:
        with self.assertRaises(ClaudeResidentError) as caught:
            self._session(
                environment={"UNIVERSE_CLAUDE_PERMISSION_TOKEN": "secret"}
            )
        self.assertIn("TOKEN_LEAKED_TO_PROVIDER", str(caught.exception))

        with self.assertRaises(ClaudeResidentError):
            self._session(
                environment={"UNIVERSE_CLAUDE_PERMISSION_ENDPOINT": "http://127.0.0.1:1"}
            )

    def test_unknown_rate_limit_status_is_neither_allowed_nor_exhausted(self) -> None:
        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [
                    {
                        "type": "rate_limit_event",
                        "rate_limit_info": {"status": "some_future_value"},
                    },
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "done",
                        "session_id": "s1",
                    },
                ]
            ]
            return process

        session = self._session(process_factory=factory)
        answer = session.send_message("go", lambda _d: None)

        # The result event decides; the unknown status neither stops the turn
        # nor is mistaken for a spent quota.
        self.assertEqual("done", answer)
        self.assertNotEqual(SESSION_QUOTA_EXHAUSTED, session.session_status())
        self.assertEqual("RATE_LIMIT_STATUS_UNKNOWN", session.last_rate_limit_status)
        self.assertEqual(["some_future_value"], session.unknown_rate_limit_statuses)

    def test_missing_rate_limit_status_is_unknown_not_exhausted(self) -> None:
        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [
                    {"type": "rate_limit_event"},
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "done",
                        "session_id": "s1",
                    },
                ]
            ]
            return process

        session = self._session(process_factory=factory)
        self.assertEqual("done", session.send_message("go", lambda _d: None))
        self.assertEqual("RATE_LIMIT_STATUS_UNKNOWN", session.last_rate_limit_status)

    def test_result_failure_is_reported_not_swallowed(self) -> None:
        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [{"type": "result", "subtype": "error_max_turns", "is_error": True}]
            ]
            return process

        session = self._session(process_factory=factory)
        with self.assertRaises(ClaudeResidentError) as caught:
            session.send_message("go", lambda _d: None)
        self.assertIn("CLAUDE_RESULT_FAILED", str(caught.exception))

    def test_missing_resumed_conversation_has_specific_failure(self) -> None:
        def factory(**kwargs):
            process = FakeClaudeProcess(**kwargs)
            process.script = [
                [
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "errors": [
                            "No conversation found with session ID: stale-session"
                        ],
                    }
                ]
            ]
            return process

        session = self._session(
            process_factory=factory,
            session_id="stale-session",
        )
        with self.assertRaisesRegex(
            ClaudeResidentError,
            "CLAUDE_SESSION_RESUME_NOT_FOUND",
        ):
            session.send_message("go", lambda _d: None)


if __name__ == "__main__":
    unittest.main()
