"""Exact-bound delivery must not elect one provider or one latest Master."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from universe_server import UniverseHTTPServer

class LiveBindingDispatchTests(unittest.TestCase):
    def dispatch(self, provider="CLAUDE", currentness="UNKNOWN", change=None):
        terminal = dict(terminal_id="term-a", project_id="career", mode="MASTER",
                        provider=provider, state="LIVE", supervisor_session_id="session-a",
                        session_anchor_ref="anchor-a")
        session = dict(session_id="session-a", node="career", mode="MASTER",
                       provider=provider, session_anchor_ref="anchor-a", currentness=currentness)
        if change:
            change(terminal, session)
        host = Mock()
        host.get.return_value = terminal
        host.list_sessions.return_value = [terminal]
        dispatch = Mock(return_value={"status": "DISPATCHED", "message_id": "msg-a"})
        server = SimpleNamespace(_session_anchor_terminal_host=lambda: host,
            session_supervisor=SimpleNamespace(get_session=lambda _: session),
            session_bus=Mock(), _dispatch_pending_session_instruction=dispatch)
        message = dict(message_id="msg-a", kind="INSTRUCTION", delivery_state="PENDING",
            recipient_anchor_ref="anchor-a", to=dict(terminal_id="term-a",
                session_anchor_ref="anchor-a", project_id="career", mode="MASTER"))
        result = UniverseHTTPServer._dispatch_live_posted_session_instructions(server, {"messages": [message]})
        return result[0], dispatch

    def test_all_providers_and_activity_rankings_can_receive(self):
        for provider in ("CODEX", "CLAUDE", "GROK"):
            for currentness in ("CURRENT", "STALE", "UNKNOWN"):
                with self.subTest(provider=provider, currentness=currentness):
                    result, dispatch = self.dispatch(provider, currentness)
                    self.assertEqual("DISPATCHED", result["status"])
                    dispatch.assert_called_once()

    def test_wrong_binding_never_receives(self):
        for field, value in (("provider", "GROK"), ("mode", "CONDUCTOR"),
                             ("node", "other-project"), ("session_anchor_ref", "anchor-other")):
            with self.subTest(field=field):
                result, dispatch = self.dispatch(change=lambda t, s: s.update({field: value}))
                self.assertEqual("SESSION_IDENTITY_MISMATCH", result["status"])
                dispatch.assert_not_called()

    def test_dead_terminal_never_receives(self):
        result, dispatch = self.dispatch(change=lambda t, s: t.update(state="EXITED"))
        self.assertEqual("BOUND_TERMINAL_UNAVAILABLE", result["status"])
        dispatch.assert_not_called()

class QueueRecoveryLoopTests(unittest.TestCase):
    def test_recovery_is_independent_and_survives_one_failure(self):
        stop = Mock()
        stop.wait.side_effect = [False, False, True]
        recover = Mock(side_effect=[RuntimeError("temporary unavailable"), {"status": "READY"}])
        server = SimpleNamespace(_supervisor_maintenance_stop=stop, run_session_bus_recovery_once=recover)
        UniverseHTTPServer._session_bus_recovery_loop(server)
        self.assertEqual(2, recover.call_count)
        self.assertEqual("RuntimeError", server._session_bus_recovery_last_run["error_code"])

    def test_idle_pty_uses_one_tcp_request_per_read_interval(self):
        from unittest.mock import patch
        from universe_app.reconnection_host import ReconnectionPty
        pty = ReconnectionPty.__new__(ReconnectionPty)
        pty._closed = False
        pty._cursor = 0
        pty.supervisor_id = "s"
        pty.client = Mock()
        pty.client.request.return_value = {"output": {"next_cursor": 0, "data_base64": ""}}
        with patch("universe_app.reconnection_host.time.monotonic", return_value=100), \
             patch("universe_app.reconnection_host.time.sleep") as sleep:
            self.assertEqual(b"", pty.read(0.2))
        pty.client.request.assert_called_once()
        sleep.assert_called_once_with(0.2)

if __name__ == "__main__":
    unittest.main()
