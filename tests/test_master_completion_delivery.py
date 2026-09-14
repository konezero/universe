import sys, tempfile, unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from universe_server import UniverseStore, UniverseError, UniverseHTTPServer
from universe_app.session_bus import SessionBus, SessionBusError, message_handling

class MasterCompletionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "REPOSITORY_MANIFEST.md").write_text("# probe", encoding="utf8")
        self.store = UniverseStore(self.root / "store.db")
        self.store.register_project({"project_id": "probe", "project_root": str(self.root)})
        self.bus_path = self.root / "bus.db"
        self.bus = SessionBus(database_path=self.bus_path)
        self.master, _ = self.store.create_master_message("probe", {
            "title": "test", "instruction": "read only", "idempotency_key": "one",
            "metadata": {"reply_anchor_ref": "anchor-c", "reply_terminal_id": "old-terminal"}})
        self.mid = self.master["message_id"]
        self.store.claim_master_message("probe", provider="CLAUDE", session_anchor_ref="anchor-m")
    def complete(self, body="actual result"):
        return self.store.complete_master_message(self.mid, provider="CLAUDE", body_text=body)
    def test_completion_replay_conflict_and_owner(self):
        with self.assertRaises(UniverseError):
            self.store.complete_master_message(self.mid, provider="CODEX")
        first = self.complete()
        self.assertEqual(first, self.complete())
        with self.assertRaises(UniverseError): self.complete("changed")
        self.assertEqual("actual result", self.store.get_master_message(self.mid)["completion_request"]["body_text"])
    def test_concurrent_completions_publish_one_result(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.complete(), range(2)))
        self.assertEqual(results[0]["completion_result"], results[1]["completion_result"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            messages = list(pool.map(self.bus.publish_master_completion, results))
        self.assertEqual(messages[0]["message_id"], messages[1]["message_id"])
        self.assertEqual(1, len(self.bus._messages))
    def test_restart_recovers_commit_before_bus_publish(self):
        done = self.complete()
        server = Mock(store=self.store, session_bus=self.bus)
        with patch.object(self.bus, "_persist_message", side_effect=__import__("sqlite3").OperationalError("disk error")):
            result = UniverseHTTPServer._publish_master_completion_results(server)
        self.assertEqual(1, len(result["errors"]))
        self.assertEqual(0, len(self.bus._messages))
        server.store = UniverseStore(self.root / "store.db")
        server.session_bus = SessionBus(database_path=self.bus_path)
        recovered = UniverseHTTPServer._publish_master_completion_results(server)
        self.assertFalse(recovered["errors"])
        again = SessionBus(database_path=self.bus_path)
        again.publish_master_completion(done)
        self.assertEqual(1, len(again._messages))

    def test_master_completion_closes_only_matching_claude_handoff(self):
        self.complete()
        terminal = {
            "terminal_id": "claude-terminal",
            "session_anchor_ref": "anchor-m",
            "project_id": "probe",
            "mode": "MASTER",
            "provider": "CLAUDE",
            "state": "LIVE",
        }
        handoff = self.bus.deliver_to_terminal(
            Mock(),
            terminal=terminal,
            source={"project_id": "probe", "mode": "CONDUCTOR", "provider": "CODEX"},
            to={"project_id": "probe", "mode": "MASTER", "provider": "CLAUDE"},
            kind="COORDINATION",
            notify="NONE",
            body="handoff through the Master queue",
            thread_id=self.mid,
        )
        self.bus.transition(
            handoff["message_id"], state="ACCEPTED",
            terminal_id=terminal["terminal_id"], session_anchor_ref="anchor-m",
        )
        self.bus.complete_instruction_claim(
            terminal_id=terminal["terminal_id"], message_id=handoff["message_id"],
            session_anchor_ref="anchor-m", delivery_channel="CLAUDE_CODE_CHANNEL",
        )
        unrelated = self.bus.deliver_to_terminal(
            Mock(),
            terminal=terminal,
            source={"project_id": "probe", "mode": "CONDUCTOR", "provider": "CODEX"},
            to={"project_id": "probe", "mode": "MASTER", "provider": "CLAUDE"},
            kind="COORDINATION",
            notify="NONE",
            body="unrelated handoff must remain pending",
            thread_id="different-master-message",
        )
        self.bus.transition(
            unrelated["message_id"], state="ACCEPTED",
            terminal_id=terminal["terminal_id"], session_anchor_ref="anchor-m",
        )
        self.bus.complete_instruction_claim(
            terminal_id=terminal["terminal_id"], message_id=unrelated["message_id"],
            session_anchor_ref="anchor-m", delivery_channel="CLAUDE_CODE_CHANNEL",
        )
        server = Mock(store=self.store, session_bus=self.bus)
        published = UniverseHTTPServer._publish_master_completion_results(server)
        self.assertIn(handoff["message_id"], published["handoff_message_ids"])
        current = self.bus._messages[handoff["message_id"]]
        self.assertEqual("COMPLETED", current["lifecycle_state"])
        self.assertEqual(
            f"universe://projects/probe/master-messages/{self.mid}#completed",
            current["lifecycle"]["result_ref"],
        )
        self.assertEqual("STARTED", self.bus._messages[unrelated["message_id"]]["lifecycle_state"])
        self.assertEqual([], UniverseHTTPServer._publish_master_completion_results(server)["handoff_message_ids"])
    def test_offline_anchor_rebind_forward_and_consume_without_reply_loop(self):
        result = self.bus.publish_master_completion(self.complete())
        terminal = {"terminal_id": "new-terminal", "session_anchor_ref": "anchor-c", "state": "LIVE",
                    "project_id": "probe", "mode": "CONDUCTOR", "provider": "CODEX"}
        host = Mock(); host.get.return_value = terminal; host.list_sessions.return_value = [terminal]
        inbox = self.bus.inbox(host, session_anchor_ref="anchor-c", projection="RESULTS")
        self.assertEqual([result["message_id"]], [m["message_id"] for m in inbox["messages"]])
        forwarded = self.bus.forward_result_as_instruction(host, result_message_id=result["message_id"],
            terminal_id="new-terminal", session_anchor_ref="anchor-c")
        self.assertEqual("PROCESS_REPLY", message_handling(forwarded)["action"])
        self.assertEqual(forwarded["message_id"], self.bus.forward_result_as_instruction(host,
            result_message_id=result["message_id"], terminal_id="new-terminal", session_anchor_ref="anchor-c")["message_id"])
        for state in ["ACCEPTED", "STARTED"]:
            self.bus.transition(forwarded["message_id"], state=state, terminal_id="new-terminal", session_anchor_ref="anchor-c")
        count = len(self.bus._messages)
        self.bus.reply(forwarded["message_id"], terminal_id="new-terminal", session_anchor_ref="anchor-c",
                       body_text="received", outcome="COMPLETED", host=host)
        self.assertEqual(count, len(self.bus._messages))
        self.bus.publish_master_completion(self.store.get_master_message(self.mid))
        self.assertEqual("READ", self.bus._messages[result["message_id"]]["delivery_state"])
    def test_changed_import_and_wrong_anchor_rejected(self):
        done = self.complete(); self.bus.publish_master_completion(done)
        done["completion_result"]["recipient_anchor_ref"] = "wrong"
        with self.assertRaises(SessionBusError): self.bus.publish_master_completion(done)
    def test_no_summary_is_explicit_and_oversize_is_not_completed(self):
        with self.assertRaises(UniverseError): self.complete("x" * 25000)
        self.assertEqual("PROCESSING", self.store.get_master_message(self.mid)["delivery_state"])
        self.assertIn("not supplied", self.complete("")["completion_result"]["body_text"])

if __name__ == "__main__": unittest.main()
