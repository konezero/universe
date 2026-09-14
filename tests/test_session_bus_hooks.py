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
from universe_host_turn_hook import normalize_event, run_hook

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

    def test_legacy_stop_never_reads_or_continues_bus(self):
        self.post(); before=json.dumps(self.bus._messages,sort_keys=True)
        result=self.hook()
        self.assertEqual("HOST_LIFECYCLE_REQUIRED",result["status"])
        self.assertEqual(0,result["pending_count"])
        self.assertEqual(before,json.dumps(self.bus._messages,sort_keys=True))

    def test_provider_lifecycle_is_not_idle_at_stop(self):
        for provider in ("CODEX","CLAUDE","GROK"):
            env={"UNIVERSE_PROVIDER":provider}
            payload={"session_id":"thread-a","hook_event_name":"Stop", "stop_hook_active":True}
            self.assertEqual("STOPPING",normalize_event(payload,provider,env)["event"])
            payload.update(hook_event_name="UserPromptSubmit",prompt="instruction_ref: session-bus:msg_abc hello")
            self.assertEqual("msg_abc",normalize_event(payload,provider,env)["message_id"])
            self.assertIsNone(normalize_event({**payload,"agent_id":"child"},provider,env))
        native=normalize_event({"sessionId":"s","hookEventName":"user_prompt_submit"},"GROK",{})
        self.assertEqual("PROMPT_SUBMITTED",native["event"])
        self.assertEqual("IDLE",normalize_event({"thread-id":"s","turn-id":"t","type":"agent-turn-complete"},"CODEX",{})["event"])
        self.assertIsNone(normalize_event({"session_id":"s","hook_event_name":"Notification","notification_type":"agent_completed"},"CLAUDE",{}))
        self.assertEqual("STOPPING",normalize_event({"session_id":"s","hook_event_name":"AfterAgent"},"GEMINI",{})["event"])

    def test_hook_talks_only_to_exact_host_and_outputs_no_context(self):
        payload={"session_id":"s","hook_event_name":"Stop"}
        with patch("universe_host_turn_hook.ReconnectionHostRegistry") as registry:
            client=registry.return_value.discover_by_host_id.return_value
            client.request.return_value={"host":{"turn_delivery":{"state":"STOPPING"}}}
            result=run_hook(payload,provider="CODEX",environment={"UNIVERSE_SESSION_HOST_ID":"host-one"})
            registry.return_value.discover_by_host_id.assert_called_once_with("host-one")
            self.assertEqual("turn_observe",client.request.call_args.args[0])
            self.assertEqual({},result["hook_stdout"])
            self.assertEqual("HOST_TURN_OBSERVED",result["status"])

    def test_codex_config_migration_preserves_unrelated_tables(self):
        import tomllib
        from universe_session_inject_hook import merge_codex_turn_hooks
        source='model = "x"\n[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "python universe_session_bus_hook.py"\n[other]\nvalue = 1\n'
        merged=merge_codex_turn_hooks(source,"C:/Python Path/python.exe","C:/repo/tools/universe_session_inject_hook.py")
        value=tomllib.loads(merged)
        self.assertEqual({"value":1},value["other"])
        self.assertEqual(1,len(value["hooks"]["Stop"]))
        self.assertEqual(merged,merge_codex_turn_hooks(merged,"C:/Python Path/python.exe","C:/repo/tools/universe_session_inject_hook.py"))

    def test_codex_mixed_hook_group_preserves_other_command(self):
        import tomllib
        from universe_session_inject_hook import merge_codex_turn_hooks
        source='[[hooks.Stop]]\nmatcher = "*"\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "python universe_session_bus_hook.py"\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "other-stop"\n'
        merged=merge_codex_turn_hooks(source,"python","C:/repo/tools/universe_session_inject_hook.py")
        groups=tomllib.loads(merged)["hooks"]["Stop"]
        self.assertEqual("other-stop",groups[0]["hooks"][0]["command"])
        self.assertEqual(merged,merge_codex_turn_hooks(merged,"python","C:/repo/tools/universe_session_inject_hook.py"))

    def test_grok_native_prompt_id_and_cancel_are_preserved(self):
        event=normalize_event({"sessionId":"s","hookEventName":"stop_cancelled","promptId":"old-turn"},"GROK",{})
        self.assertEqual("old-turn",event["turn_id"])
        self.assertEqual("INTERRUPTED",event["event"])

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
            self.assertTrue({"SessionStart", "Stop", "UserPromptSubmit", "Notification", "SessionEnd"} <= set(merged["hooks"]))
        target = Path(self.tmp.name) / "settings.json"; target.write_text("{broken", encoding="utf-8")
        self.assertEqual("ERROR", _write_bus_stop_hook(target, "python", "inject.py", "CLAUDE")["status"])
        self.assertEqual("{broken", target.read_text(encoding="utf-8"))

    def test_server_rejects_wrong_provider_session(self):
        from universe_server import UniverseHTTPServer, UniverseError
        session = {"session_id": "sup-a", "session_anchor_ref": "anchor_a", "provider": "CODEX", "provider_session_ref": "thread-a"}
        server = SimpleNamespace(_session_anchor_terminal_host=lambda: self.host, session_bus=self.bus,
                                 session_supervisor=SimpleNamespace(get_session=lambda _: session))
        request = {**self.payload, "schema": "universe.session-bus-hook.v1", "supervisor_session_id": "sup-a"}
        self.assertEqual("HOST_LIFECYCLE_REQUIRED", UniverseHTTPServer.handle_session_bus_hook(server, request)["status"])
        for extra in [{"provider_session_ref": "other"}, {"session_anchor_ref": "anchor_b"}, {"provider": "GROK"}]:
            with self.assertRaises(UniverseError) as error:
                UniverseHTTPServer.handle_session_bus_hook(server, {**request, **extra})
            self.assertEqual("BUS_HOOK_SESSION_MISMATCH", error.exception.code)
        self.host.rows["a"]["session_anchor_ref"] = "different-live-anchor"
        with self.assertRaises(UniverseError) as error:
            UniverseHTTPServer.handle_session_bus_hook(server, request)
        self.assertEqual("BUS_HOOK_SESSION_MISMATCH", error.exception.code)

if __name__ == "__main__": unittest.main()
