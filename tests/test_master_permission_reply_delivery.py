"""Isolated mailbox + real server observer boundary; no processes/providers."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from universe_app.session_bus import SessionBus, message_handling
from universe_server import UniverseHTTPServer


class MasterPermissionReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "bus.sqlite3"
        self.master = dict(terminal_id="term_master", project_id="universe", mode="MASTER",
                           provider="CLAUDE", state="LIVE", session_anchor_ref="anchor_master")
        self.conductor = dict(terminal_id="term_conductor", project_id="universe", mode="CONDUCTOR",
                              provider="CODEX", state="LIVE", session_anchor_ref="anchor_conductor")
        self.rows = [self.master, self.conductor]
        self.host = SimpleNamespace(list_sessions=lambda: self.rows,
                                    get=lambda tid: next(r for r in self.rows if r["terminal_id"] == tid))
        self.bus = SessionBus(self.path)

    def reply(self, body="APPROVE perm_test", key="host-permission-escalation:perm_test"):
        posted = self.bus.post(self.host, {
            "from": {"mode": "MASTER", "provider": "UNIVERSE", "project_id": "universe",
                     "session_anchor_ref": "anchor_master"},
            "to": self.conductor, "kind": "INSTRUCTION",
            "thread_id": "persona-run_test-perm-perm_test", "idempotency_key": key,
            "body_text": "Decide exact Worker permission request",
        })
        for state in ("ACCEPTED", "STARTED"):
            self.bus.transition(posted["message_id"], state=state, terminal_id="term_conductor",
                                session_anchor_ref="anchor_conductor")
        return self.bus.reply(posted["message_id"], terminal_id="term_conductor",
                              session_anchor_ref="anchor_conductor", body_text=body, host=self.host)

    def server(self):
        server = SimpleNamespace(session_bus=self.bus,
            _lookup_master_permission_decision=Mock(return_value=None),
            _session_anchor_terminal_host=lambda: self.host,
            _observe_persona_automation_result=Mock(),
            _dispatch_live_posted_session_instructions=Mock(return_value=[]))
        server._deliver_master_permission_results = lambda mid="": UniverseHTTPServer._deliver_master_permission_results(server, mid)
        return server

    def test_immediate_observer_wakes_original_master_without_deciding_permission(self):
        server = self.server()
        self.bus.result_observer = lambda packet: UniverseHTTPServer._observe_session_bus_result(server, packet)
        packet = self.reply()
        posted = server._dispatch_live_posted_session_instructions.call_args.args[0]
        forwarded = posted["messages"][0]
        self.assertEqual(packet["result"]["message_id"], forwarded["in_reply_to"])
        self.assertEqual("anchor_master", forwarded["recipient_anchor_ref"])
        self.assertEqual("CLAUDE", forwarded["to"]["provider"])
        self.assertEqual("APPROVE perm_test", forwarded["body_text"])
        self.assertEqual("PROCESS_REPLY", message_handling(forwarded)["action"])
        self.assertFalse(message_handling(forwarded)["authority_granted"])

    def test_restart_recovers_denial_once_and_ack_does_not_loop(self):
        packet = self.reply("DENY perm_test")
        self.bus = SessionBus(self.path)
        forwarded = self.bus.forward_master_permission_results(self.host)["messages"]
        self.assertEqual(1, len(forwarded))
        self.assertEqual("DENY perm_test", forwarded[0]["body_text"])
        self.bus = SessionBus(self.path)
        self.assertEqual([], self.bus.forward_master_permission_results(self.host)["messages"])
        for state in ("ACCEPTED", "STARTED"):
            self.bus.transition(forwarded[0]["message_id"], state=state, terminal_id="term_master",
                                session_anchor_ref="anchor_master")
        ack = self.bus.reply(forwarded[0]["message_id"], terminal_id="term_master",
                             session_anchor_ref="anchor_master", body_text="Received", host=self.host)
        self.assertEqual("REPLY_CONSUMED", ack["status"])
        self.assertEqual([], self.bus.forward_master_permission_results(self.host)["messages"])

    def test_offline_owner_not_replaced_by_other_master_then_recovered(self):
        packet = self.reply()
        self.master["state"] = "EXITED"
        self.rows.append({**self.master, "state": "LIVE", "terminal_id": "term_other",
                          "session_anchor_ref": "other_anchor"})
        result = self.bus.forward_master_permission_results(self.host)
        self.assertEqual([], result["messages"])
        self.assertEqual("BUS_MASTER_RECIPIENT_UNAVAILABLE", result["errors"][0]["error_code"])
        self.assertEqual("UNREAD", self.bus._messages[packet["result"]["message_id"]]["delivery_state"])
        self.master["state"] = "LIVE"
        self.assertEqual(1, len(self.bus.forward_master_permission_results(self.host)["messages"]))

    def test_unrelated_result_does_not_wake_master(self):
        self.reply(key="ordinary-work")
        self.assertEqual([], self.bus.forward_master_permission_results(self.host)["messages"])

    def test_recorded_decision_releases_only_exact_reply_and_survives_restart(self):
        self.reply()
        mid = self.bus.forward_master_permission_results(self.host)["messages"][0]["message_id"]
        for state in ("ACCEPTED", "STARTED"):
            self.bus.transition(mid, state=state, terminal_id="term_master", session_anchor_ref="anchor_master")
        proof = dict(owner_ref="anchor_master", project_id="universe", permission_request_id="perm_test",
                     decision="APPROVE", message_id="room_decision")
        for field, value in (("owner_ref", "other"), ("project_id", "career"),
                             ("permission_request_id", "other"), ("decision", "ESCALATE"), ("message_id", "")):
            self.assertEqual([], self.bus.reconcile_permission_reply_handoffs(lambda _: {**proof, field: value})["message_ids"])
        with patch.object(self.bus, "_persist_message", side_effect=OSError("test storage error")):
            self.assertTrue(self.bus.reconcile_permission_reply_handoffs(lambda _: proof)["errors"])
        self.assertEqual("STARTED", self.bus._messages[mid]["lifecycle_state"])
        self.bus = SessionBus(self.path)
        self.assertEqual([mid], self.bus.reconcile_permission_reply_handoffs(lambda _: proof)["message_ids"])
        self.assertNotIn("final_result_id", self.bus._messages[mid]["lifecycle"])
        self.assertEqual([], self.bus.reconcile_permission_reply_handoffs(lambda _: proof)["message_ids"])
        self.bus = SessionBus(self.path)
        self.assertEqual("COMPLETED", self.bus._messages[mid]["lifecycle_state"])

    def test_lookup_uses_master_decision_in_exact_launched_frame(self):
        import json
        original = {"body_text": "run_id: run_test\ntask_frame_id: host_test\npermission_request_id: perm_test",
                    "thread_id": "persona-run_test-perm-perm_test",
                    "from": {"session_anchor_ref": "anchor_master", "project_id": "universe"}}
        event = {"message": {"author_role": "MASTER", "message_id": "decision_1", "body_text": json.dumps({
            "schema": "universe.task-frame-host-permission-decision.v1", "request_id": "perm_test", "decision": "DENY"})}}
        server = SimpleNamespace(persona_automation=Mock(), multi_rooms=Mock(), _persona_task_frame_state_root=lambda: self.tmp.name)
        server.persona_automation.get_run.return_value = {"session_anchor_ref": "anchor_master", "project_id": "universe"}
        server.persona_automation.host_frame_launched.return_value = {"task_frame_id": "host_test"}
        server.multi_rooms.list_room_events.return_value = [event]
        with patch("universe_server.task_frame_host_status", return_value={"room_id": "room_test"}):
            lookup = lambda: UniverseHTTPServer._lookup_master_permission_decision(server, original)
            self.assertEqual("DENY", lookup()["decision"])
            event["message"]["author_role"] = "WORKER"
            self.assertIsNone(lookup())
            event["message"]["author_role"] = "MASTER"
            server.persona_automation.host_frame_launched.return_value = None
            self.assertIsNone(lookup())

    def test_periodic_server_sweep_recovers_missed_observer(self):
        self.reply()
        server = self.server()
        server.persona_automation = Mock()
        server._publish_master_completion_results = Mock(return_value={})
        server.store = SimpleNamespace(reclaim_expired_master_messages=lambda: [])
        observed = UniverseHTTPServer.run_conductor_operating_loop_once(server)
        self.assertEqual(1, len(observed["master_permission_replies"]["messages"]))
        self.assertEqual("anchor_master", observed["master_permission_replies"]["messages"][0]["recipient_anchor_ref"])
        self.assertTrue(server._dispatch_live_posted_session_instructions.called)

    def test_mismatched_result_identity_or_thread_never_forwarded(self):
        packet = self.reply()
        mid = packet["result"]["message_id"]
        original = copy.deepcopy(self.bus._messages[mid])
        for field, value in (("recipient_anchor_ref", "wrong"), ("thread_id", "wrong"),
                             ("in_reply_to", "wrong"), ("from", {"mode": "MASTER"}),
                             ("to", {"mode": "MASTER", "project_id": "career"})):
            with self.subTest(field=field):
                self.bus._messages[mid] = {**copy.deepcopy(original), field: value}
                self.assertEqual([], self.bus.forward_master_permission_results(self.host)["messages"])
        self.bus._messages[mid] = original


if __name__ == "__main__":
    unittest.main()
