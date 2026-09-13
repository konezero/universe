from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from universe_app.session_bus import SessionBus, SessionBusError
from universe_app.session_bus_hooks import handle_hook
from universe_session_bus_hook import hook_request, render_hook, run_hook

class Host:
    def __init__(self):
        self.rows = {tid: {"terminal_id": tid, "session_anchor_ref": "anchor_" + tid,
                          "state": "LIVE", "provider": "CODEX", "project_id": "universe",
                          "mode": "CONDUCTOR" if tid == "a" else "MASTER", "supervisor_session_id": "sup-" + tid}
                     for tid in ("a", "b")}
    def get(self, tid): return self.rows[tid]
    def list_sessions(self): return list(self.rows.values())

class StopHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "bus.sqlite3"
        self.host, self.bus = Host(), SessionBus(database_path=self.db)
        self.payload = {"terminal_id": "a", "session_anchor_ref": "anchor_a", "provider": "CODEX",
                        "provider_session_ref": "thread-a", "hook_event_name": "Stop"}
    def post(self, kind="COORDINATION", target="a"):
        return self.bus.post(self.host, {"to": {"terminal_id": target}, "from": {"terminal_id": "b"},
                                        "kind": kind, "body_text": "Master report"})["message_id"]
    def hook(self, **kwargs): return handle_hook(self.bus, self.host, {**self.payload, **kwargs})

    def test_empty_stop_finishes_without_continuation(self):
        self.assertEqual({}, render_hook(self.hook(), self.payload))
    def test_stop_only_reminds_without_claiming_reading_or_replying(self):
        mid = self.post(); before = json.dumps(self.bus._messages, sort_keys=True)
        result = self.hook(); output = render_hook(result, self.payload)
        self.assertEqual("block", output["decision"])
        self.assertNotIn("Master report", output["reason"])
        self.assertNotIn("body_text", result["messages"][0])
        self.assertEqual(1, result["pending_count"])
        self.assertEqual(before, json.dumps(self.bus._messages, sort_keys=True))
        # Only the agent's deliberate processing uses the existing state API.
        for state in ["ACCEPTED", "STARTED"]:
            self.bus.transition(mid, state=state, terminal_id="a", session_anchor_ref="anchor_a")
        self.bus.reply(mid, terminal_id="a", session_anchor_ref="anchor_a", body_text="Actually processed", host=self.host)
        self.assertFalse(self.hook()["messages"])
    def test_continuation_stop_does_not_loop_or_auto_complete(self):
        mid = self.post(); self.hook()
        self.assertEqual({}, render_hook(self.hook(stop_hook_active=True), self.payload))
        self.assertEqual("QUEUED", self.bus._messages[mid]["lifecycle_state"])
    def test_lost_hook_response_leaves_queue_unchanged_after_restart(self):
        self.post(); first = self.hook()
        self.bus = SessionBus(database_path=self.db)
        self.assertEqual(first["messages"], self.hook()["messages"])
        self.assertEqual(1, len(self.bus._messages))
    def test_all_pending_states_remain_available_for_contextual_review(self):
        mids = [self.post() for _ in range(3)]
        self.bus.transition(mids[1], state="ACCEPTED", terminal_id="a", session_anchor_ref="anchor_a")
        self.bus.transition(mids[2], state="ACCEPTED", terminal_id="a", session_anchor_ref="anchor_a")
        self.bus.transition(mids[2], state="STARTED", terminal_id="a", session_anchor_ref="anchor_a")
        result = self.hook()
        self.assertEqual(3, result["pending_count"])
        self.assertEqual(set(mids), {m["message_id"] for m in result["messages"]})
    def test_other_project_session_and_new_work_are_not_read(self):
        self.post(target="b"); self.post(kind="INSTRUCTION")
        self.assertFalse(self.hook()["messages"])
    def test_non_stop_event_rejected(self):
        for event in ["UserPromptSubmit", "PostToolUse"]:
            with self.assertRaises(SessionBusError): self.hook(hook_event_name=event)
    def test_client_ignores_subagents_and_repeated_stop(self):
        env = {"UNIVERSE_TERMINAL_ID": "a", "UNIVERSE_SESSION_ANCHOR_REF": "anchor_a",
               "UNIVERSE_SUPERVISOR_SESSION_ID": "sup-a", "UNIVERSE_PROVIDER": "CODEX"}
        payload = {"session_id": "thread-a", "hook_event_name": "Stop"}
        self.assertIsNotNone(hook_request(payload, env, "CODEX"))
        for extra in [{"agent_id": "child"}, {"stop_hook_active": True}, {"hook_event_name": "PostToolUse"}]:
            self.assertIsNone(hook_request({**payload, **extra}, env, "CODEX"))
        self.assertIsNone(hook_request(payload, env, "CLAUDE"))
    def test_provider_payloads_and_feedback_contracts(self):
        for provider in ["CODEX", "CLAUDE", "GROK"]:
            with self.subTest(provider=provider):
                env = {"UNIVERSE_TERMINAL_ID": "a", "UNIVERSE_SESSION_ANCHOR_REF": "anchor_a",
                       "UNIVERSE_SUPERVISOR_SESSION_ID": "sup-a", "UNIVERSE_PROVIDER": provider}
                payload = ({"hookEventName": "stop", "hook_event_name": "Stop", "sessionId": "thread-a",
                            "stopHookActive": False, "reason": "end_turn"} if provider == "GROK" else
                           {"hook_event_name": "Stop", "session_id": "thread-a", "stop_hook_active": False})
                request = hook_request(payload, env, provider)
                self.assertEqual("thread-a", request["provider_session_ref"])
                self.assertEqual(provider, request["provider"])
                self.post(); output = render_hook(self.hook(), request)
                if provider == "CODEX": self.assertEqual("block", output["decision"])
                else:
                    self.assertNotIn("decision", output)
                    self.assertEqual("Stop", output["hookSpecificOutput"]["hookEventName"])
                    self.assertIn("/v1/session-bus/inbox", output["hookSpecificOutput"]["additionalContext"])
                self.assertEqual({}, render_hook({"pending_count": 0}, request))
    def test_grok_skips_session_end_children_repeated_and_foreign_handlers(self):
        env = {"UNIVERSE_TERMINAL_ID": "a", "UNIVERSE_SESSION_ANCHOR_REF": "anchor_a",
               "UNIVERSE_SUPERVISOR_SESSION_ID": "sup-a", "UNIVERSE_PROVIDER": "GROK"}
        payload = {"hookEventName": "stop", "hook_event_name": "Stop", "sessionId": "thread-a", "reason": "end_turn"}
        for extra in [{"reason": "shutdown"}, {"reason": "channel_closed"}, {"reason": None},
                      {"stopHookActive": True}, {"stop_hook_active": True}, {"subagentType": "Explore"},
                      {"agentId": "child"}, {"session_id": "different"}, {"hookEventName": "post_tool_use"}]:
            with self.subTest(extra=extra):
                with patch("universe_session_bus_hook.load_server_connection") as connection:
                    self.assertEqual("SKIPPED", run_hook({**payload, **extra}, provider="GROK", environment=env)["status"])
                    connection.assert_not_called()
        with patch("universe_session_bus_hook.load_server_connection") as connection:
            self.assertEqual("SKIPPED", run_hook(payload, provider="CLAUDE", environment=env)["status"])
            connection.assert_not_called()
    def test_stop_install_preserves_other_hooks_and_is_idempotent(self):
        from universe_session_inject_hook import _bus_stop_hook_payload, merge_bus_stop_hook, _write_bus_stop_hook
        existing = {"permissions": {"allow": ["Read"]}, "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "existing-start"}]}],
            "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "other-stop"},
                     {"type": "command", "command": "python universe_session_bus_hook.py --provider OLD"}]}]}}
        before = json.dumps(existing, sort_keys=True)
        for provider in ["CLAUDE", "GROK"]:
            desired = _bus_stop_hook_payload("C:/Python Path/python.exe", "C:/Project Path/tools/universe_session_inject_hook.py", provider)
            merged = merge_bus_stop_hook(existing, desired)
            self.assertEqual(before, json.dumps(existing, sort_keys=True))
            self.assertEqual(existing["permissions"], merged["permissions"])
            self.assertEqual(existing["hooks"]["SessionStart"], merged["hooks"]["SessionStart"])
            self.assertEqual("other-stop", merged["hooks"]["Stop"][0]["hooks"][0]["command"])
            self.assertEqual(merged, merge_bus_stop_hook(merged, desired))
            self.assertEqual({"SessionStart", "Stop"}, set(merged["hooks"]))
        target = Path(self.tmp.name) / "settings.json"; target.write_text("{broken", encoding="utf-8")
        self.assertEqual("ERROR", _write_bus_stop_hook(target, "python", "inject.py", "CLAUDE")["status"])
        self.assertEqual("{broken", target.read_text(encoding="utf-8"))

    def test_server_rejects_wrong_provider_session(self):
        from universe_server import UniverseHTTPServer, UniverseError
        session = {"session_id": "sup-a", "session_anchor_ref": "anchor_a", "provider": "CODEX", "provider_session_ref": "thread-a"}
        server = SimpleNamespace(_session_anchor_terminal_host=lambda: self.host, session_bus=self.bus,
                                 session_supervisor=SimpleNamespace(get_session=lambda _: session))
        request = {**self.payload, "schema": "universe.session-bus-hook.v1", "supervisor_session_id": "sup-a"}
        self.assertEqual("NO_PENDING_MESSAGE", UniverseHTTPServer.handle_session_bus_hook(server, request)["status"])
        for extra in [{"provider_session_ref": "other"}, {"session_anchor_ref": "anchor_b"}, {"provider": "GROK"}]:
            with self.assertRaises(UniverseError) as error:
                UniverseHTTPServer.handle_session_bus_hook(server, {**request, **extra})
            self.assertEqual("BUS_HOOK_SESSION_MISMATCH", error.exception.code)
        self.host.rows["a"]["session_anchor_ref"] = "different-live-anchor"
        with self.assertRaises(UniverseError) as error:
            UniverseHTTPServer.handle_session_bus_hook(server, request)
        self.assertEqual("BUS_HOOK_SESSION_MISMATCH", error.exception.code)

if __name__ == "__main__": unittest.main()
