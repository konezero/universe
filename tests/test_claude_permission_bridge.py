from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from claude_permission_bridge import (  # noqa: E402
    CLAUDE_PERMISSION_OPTIONS,
    PERMISSION_REQUEST_SCHEMA,
    ClaudePermissionBridge,
)


class ClaudePermissionBridgeTests(unittest.TestCase):
    def _bridge(self, decision, **overrides) -> ClaudePermissionBridge:
        self.seen: list[Mapping[str, Any]] = []

        def requester(request: Mapping[str, Any]) -> str | None:
            self.seen.append(request)
            return decision(request) if callable(decision) else decision

        kwargs: dict[str, Any] = {
            "session_ref": "claude-code:session-1",
            "permission_requester": requester,
        }
        kwargs.update(overrides)
        return ClaudePermissionBridge(**kwargs)

    def _request(self, **overrides) -> dict[str, Any]:
        request: dict[str, Any] = {
            "tool_name": "Write",
            "input": {"file_path": "C:/tmp/a.txt", "content": "hello"},
            "tool_use_id": "toolu_1",
        }
        request.update(overrides)
        return request

    def test_allow_once_returns_allow_with_updated_input(self) -> None:
        bridge = self._bridge("allow-once")
        result = bridge.handle(self._request())

        self.assertEqual("allow", result["behavior"])
        self.assertEqual(
            {"file_path": "C:/tmp/a.txt", "content": "hello"}, result["updatedInput"]
        )
        # allow_once must not persist anything.
        self.assertNotIn("updatedPermissions", result)

    def test_allow_always_persists_only_claude_suggestions(self) -> None:
        bridge = self._bridge("allow-always")
        result = bridge.handle(
            self._request(
                suggestions=[
                    {"destination": "localSettings", "rule": "Write"},
                    {"destination": "session", "rule": "ignored"},
                ]
            )
        )

        self.assertEqual("allow", result["behavior"])
        self.assertEqual(
            [{"destination": "localSettings", "rule": "Write"}],
            result["updatedPermissions"],
        )

    def test_allow_always_without_suggestions_does_not_invent_a_rule(self) -> None:
        bridge = self._bridge("allow-always")
        result = bridge.handle(self._request())

        self.assertEqual("allow", result["behavior"])
        self.assertNotIn("updatedPermissions", result)

    def test_reject_returns_deny_with_message(self) -> None:
        bridge = self._bridge("reject-once")
        result = bridge.handle(self._request())

        self.assertEqual("deny", result["behavior"])
        self.assertIn("rejected", result["message"])

    def test_no_decision_fails_closed(self) -> None:
        bridge = self._bridge(None)
        result = bridge.handle(self._request())

        self.assertEqual("deny", result["behavior"])
        self.assertIn("CLAUDE_PERMISSION_NO_DECISION", result["message"])

    def test_unknown_option_fails_closed(self) -> None:
        bridge = self._bridge("grant-everything")
        result = bridge.handle(self._request())

        self.assertEqual("deny", result["behavior"])
        self.assertIn("CLAUDE_PERMISSION_OPTION_UNKNOWN", result["message"])

    def test_requester_exception_fails_closed(self) -> None:
        def boom(_request):
            raise RuntimeError("ui gone")

        bridge = ClaudePermissionBridge(
            session_ref="claude-code:session-1", permission_requester=boom
        )
        result = bridge.handle(self._request())

        self.assertEqual("deny", result["behavior"])
        self.assertIn("CLAUDE_PERMISSION_REQUESTER_FAILED", result["message"])

    def test_closed_bridge_fails_closed(self) -> None:
        bridge = self._bridge("allow-once")
        bridge.close()
        result = bridge.handle(self._request())

        self.assertEqual("deny", result["behavior"])
        self.assertIn("stopped", result["message"])

    def test_session_mismatch_fails_closed(self) -> None:
        bridge = self._bridge("allow-once")
        result = bridge.handle(self._request(session_ref="claude-code:other"))

        self.assertEqual("deny", result["behavior"])
        self.assertIn("SESSION_MISMATCH", result["message"])

    def test_turn_mismatch_fails_closed(self) -> None:
        bridge = self._bridge("allow-once")
        bridge.bind_turn("turn-1")
        result = bridge.handle(self._request(turn_id="turn-2"))

        self.assertEqual("deny", result["behavior"])
        self.assertIn("TURN_MISMATCH", result["message"])

    def test_bound_turn_is_accepted(self) -> None:
        bridge = self._bridge("allow-once")
        bridge.bind_turn("turn-1")
        result = bridge.handle(self._request(turn_id="turn-1"))

        self.assertEqual("allow", result["behavior"])

    def test_invalid_payload_fails_closed(self) -> None:
        bridge = self._bridge("allow-once")

        self.assertEqual("deny", bridge.handle({"input": {}})["behavior"])
        self.assertEqual(
            "deny", bridge.handle({"tool_name": "Write", "input": "x"})["behavior"]
        )

    def test_close_during_decision_is_never_adopted(self) -> None:
        """close() landing right after the check must not leak an approval."""
        holder: dict[str, Any] = {}

        def decide(_request):
            # The operator answers, but the service stops in the same breath.
            holder["bridge"].close()
            return "allow-once"

        bridge = self._bridge(decide)
        holder["bridge"] = bridge
        result = bridge.handle(self._request())

        self.assertEqual("deny", result["behavior"])
        self.assertIn("CANCELLED_BY_SHUTDOWN", result["message"])

    def test_turn_change_during_decision_supersedes_the_answer(self) -> None:
        holder: dict[str, Any] = {}

        def decide(_request):
            # A new turn starts while the operator is still deciding.
            holder["bridge"].bind_turn("turn-2")
            return "allow-once"

        bridge = self._bridge(decide)
        holder["bridge"] = bridge
        bridge.bind_turn("turn-1")
        result = bridge.handle(self._request(turn_id="turn-1"))

        self.assertEqual("deny", result["behavior"])
        self.assertIn("SUPERSEDED", result["message"])

    def test_concurrent_close_never_yields_allow_after_shutdown(self) -> None:
        """Stress the check/adopt boundary from another thread."""
        import threading

        for _ in range(60):
            holder: dict[str, Any] = {}
            entered = threading.Event()

            def decide(_request):
                entered.set()
                return "allow-once"

            bridge = self._bridge(decide)
            holder["bridge"] = bridge
            results: list[dict[str, Any]] = []

            worker = threading.Thread(
                target=lambda: results.append(bridge.handle(self._request()))
            )
            worker.start()
            entered.wait(timeout=2)
            bridge.close()
            worker.join(timeout=2)

            self.assertEqual(1, len(results))
            if results[0]["behavior"] == "allow":
                # Only legal if adoption completed before close took the lock.
                self.assertNotIn("updatedPermissions", results[0])

    def test_request_uses_universe_contract_and_offers_three_options(self) -> None:
        bridge = self._bridge("allow-once")
        bridge.bind_turn("turn-9")
        bridge.handle(self._request(turn_id="turn-9"))
        request = self.seen[0]

        self.assertEqual(PERMISSION_REQUEST_SCHEMA, request["schema"])
        self.assertEqual("CLAUDE", request["provider"])
        self.assertEqual("claude-code:session-1", request["session_id"])
        self.assertEqual("Write", request["tool_call"]["toolName"])
        self.assertEqual("turn-9", request["tool_call"]["turnId"])
        self.assertEqual(
            ["allow_once", "allow_always", "reject_once"],
            [option["kind"] for option in request["options"]],
        )
        self.assertEqual(len(CLAUDE_PERMISSION_OPTIONS), len(request["options"]))


if __name__ == "__main__":
    unittest.main()
