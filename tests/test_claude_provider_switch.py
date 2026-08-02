"""Provider switch behaviour on a real ResidentModeSessionHost.

These are behavioural tests, not source inspection: a real host drives fake
provider processes, so a switch has to actually terminate the old process,
cancel its pending approval, revoke its token, and ignore its late output.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from claude_permission_bridge import ClaudePermissionBridge  # noqa: E402
from claude_permission_broker import ClaudePermissionBroker  # noqa: E402
from project_master_host import ResidentModeSessionHost  # noqa: E402


class FakeProcess:
    """Stands in for one provider OS process."""

    _next_pid = 9000

    def __init__(self) -> None:
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.alive = True
        self.terminated = False

    def close(self) -> None:
        self.alive = False
        self.terminated = True


class FakeProvider:
    """A resident provider that owns a process, a broker, and a bridge."""

    instances: list["FakeProvider"] = []

    def __init__(self, provider: str, target_id: str) -> None:
        self.provider = provider
        self.target_id = target_id
        self.process = FakeProcess()
        self.session_id = f"{provider.lower()}-session-{self.process.pid}"
        self.session_ref = f"{provider.lower()}:{self.session_id}"
        self.connection_state = "NEW"
        self.closed = False
        self.replies: list[str] = []
        self.permission_requester = None
        self.bridge = ClaudePermissionBridge(
            session_ref=self.session_ref,
            permission_requester=lambda request: self._ask(request),
        )
        self.broker = ClaudePermissionBroker(
            bridge=self.bridge, target=f"{target_id}/{provider}"
        )
        # Generation identity: output from an older generation must be ignored.
        self.generation = self.process.pid
        FakeProvider.instances.append(self)

    def _ask(self, request: Mapping[str, Any]) -> str | None:
        if self.permission_requester is None:
            return None
        return self.permission_requester(request)

    def set_permission_requester(self, requester) -> None:
        self.permission_requester = requester

    def prepare_session(self) -> None:
        return None

    def reply(self, message: Mapping[str, Any]) -> str:
        if not self.process.alive:
            raise AssertionError("a closed provider must not receive a message")
        text = str(message.get("text") or "")
        self.replies.append(text)
        return f"{self.provider}:{text}"

    def close(self) -> None:
        self.closed = True
        self.process.close()
        self.broker.close()


class ProviderSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        FakeProvider.instances.clear()
        self.approvals: list[Mapping[str, Any]] = []

    def tearDown(self) -> None:
        for provider in FakeProvider.instances:
            provider.broker.close()
        self.temp.cleanup()

    def _host(self, **overrides) -> ResidentModeSessionHost:
        def factory(provider, _root, target_id, _store, _mode, _actor):
            return FakeProvider(provider, target_id)

        kwargs: dict[str, Any] = {
            "actor_label": "Universe Conductor",
            "provider_factory": factory,
        }
        kwargs.update(overrides)
        return ResidentModeSessionHost(
            self.root,
            "CONDUCTOR",
            "CONDUCTOR",
            self.root / f"switch-{len(FakeProvider.instances)}.sqlite",
            **kwargs,
        )

    def _message(self, text: str) -> dict[str, Any]:
        return {"text": text}

    # -- test 6 ---------------------------------------------------------

    def test_switch_terminates_the_previous_process(self) -> None:
        host = self._host()
        try:
            host.reply("CLAUDE", self._message("one"))
            first = FakeProvider.instances[0]
            self.assertTrue(first.process.alive)

            host.reply("CODEX", self._message("two"))
        finally:
            host.close()

        second = FakeProvider.instances[1]
        self.assertEqual("CLAUDE", first.provider)
        self.assertEqual("CODEX", second.provider)
        self.assertTrue(first.process.terminated, "old process must be terminated")
        self.assertFalse(first.process.alive)
        self.assertTrue(first.closed)
        self.assertNotEqual(first.process.pid, second.process.pid)

    def test_switch_revokes_the_previous_capability_token(self) -> None:
        host = self._host()
        try:
            host.reply("CLAUDE", self._message("one"))
            first = FakeProvider.instances[0]
            stale_token = first.broker.token.value
            self.assertFalse(first.broker.token.revoked)

            host.reply("GROK", self._message("two"))
        finally:
            host.close()

        self.assertTrue(first.broker.token.revoked)
        denied = first.broker.handle_payload(
            {"tool_name": "Write", "input": {}, "tool_use_id": "late"},
            presented_token=stale_token,
        )
        self.assertEqual("deny", denied["behavior"])

    def test_switch_cancels_a_pending_approval(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        results: list[Mapping[str, Any]] = []

        def slow_requester(_request: Mapping[str, Any]) -> str | None:
            entered.set()
            release.wait(timeout=5)
            return "allow-once"

        host = self._host(permission_requester=slow_requester)
        try:
            host.reply("CLAUDE", self._message("one"))
            first = FakeProvider.instances[0]

            def pending() -> None:
                results.append(
                    first.broker.handle_payload(
                        {
                            "tool_name": "Write",
                            "input": {"file_path": "x"},
                            "tool_use_id": "pending-1",
                        },
                        presented_token=first.broker.token.value,
                    )
                )

            worker = threading.Thread(target=pending)
            worker.start()
            self.assertTrue(entered.wait(timeout=5))

            # Switch provider while the approval is still on screen.
            host.reply("CODEX", self._message("two"))
            release.set()
            worker.join(timeout=5)
        finally:
            host.close()

        self.assertEqual(1, len(results))
        self.assertEqual("deny", results[0]["behavior"])

    def test_switch_while_waiting_for_approval_still_terminates(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow_requester(_request: Mapping[str, Any]) -> str | None:
            entered.set()
            release.wait(timeout=5)
            return "allow-once"

        host = self._host(permission_requester=slow_requester)
        try:
            host.reply("CLAUDE", self._message("one"))
            first = FakeProvider.instances[0]
            worker = threading.Thread(
                target=lambda: first.broker.handle_payload(
                    {"tool_name": "Bash", "input": {}, "tool_use_id": "busy-1"},
                    presented_token=first.broker.token.value,
                )
            )
            worker.start()
            self.assertTrue(entered.wait(timeout=5))

            host.reply("GROK", self._message("two"))
            release.set()
            worker.join(timeout=5)
        finally:
            host.close()

        self.assertTrue(first.process.terminated)

    # -- test 7 ---------------------------------------------------------

    def test_only_the_new_provider_receives_later_messages(self) -> None:
        host = self._host()
        try:
            host.reply("CLAUDE", self._message("one"))
            host.reply("CODEX", self._message("two"))
            host.reply("CODEX", self._message("three"))
        finally:
            host.close()

        first, second = FakeProvider.instances[0], FakeProvider.instances[1]
        self.assertEqual(["one"], first.replies)
        self.assertEqual(["two", "three"], second.replies)
        # Exactly two providers were built: no third, no revival of the first.
        self.assertEqual(2, len(FakeProvider.instances))

    def test_only_one_active_session_pointer_is_kept(self) -> None:
        host = self._host()
        try:
            first_reply = host.reply("CLAUDE", self._message("one"))
            after_claude = host.status()
            second_reply = host.reply("CODEX", self._message("two"))
            after_codex = host.status()

            # The reply reports exactly one active provider at a time.
            self.assertEqual("CLAUDE", first_reply["provider"])
            self.assertEqual("CODEX", second_reply["provider"])
            self.assertNotEqual(
                first_reply["session_ref"], second_reply["session_ref"]
            )
            # The connection contract keeps a single coordinate, not a map.
            self.assertTrue(after_claude["resident"])
            self.assertTrue(after_codex["resident"])
            self.assertEqual(
                "LAST_COORDINATE", after_codex["session_persistence"]
            )
        finally:
            host.close()

        stopped = host.status()
        self.assertFalse(stopped["resident"], "no resident pointer after close")
        alive = [p for p in FakeProvider.instances if p.process.alive]
        self.assertEqual([], alive, "no provider process may outlive the host")

    def test_late_output_from_the_previous_generation_is_rejected(self) -> None:
        host = self._host()
        try:
            host.reply("CLAUDE", self._message("one"))
            first = FakeProvider.instances[0]
            host.reply("CODEX", self._message("two"))
            second = FakeProvider.instances[1]

            # A late message aimed at the retired generation must not run.
            with self.assertRaises(AssertionError):
                first.reply(self._message("late"))

            self.assertNotEqual(first.generation, second.generation)
            self.assertTrue(second.process.alive)
        finally:
            host.close()

    def test_same_provider_twice_reuses_one_process(self) -> None:
        host = self._host()
        try:
            host.reply("CLAUDE", self._message("one"))
            host.reply("CLAUDE", self._message("two"))
        finally:
            host.close()

        self.assertEqual(1, len(FakeProvider.instances))
        self.assertEqual(["one", "two"], FakeProvider.instances[0].replies)


if __name__ == "__main__":
    unittest.main()
