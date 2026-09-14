"""rag.memory-relate: project-scoped, typed memory knowledge/relation storage.

Replaces the UI-hardcoded topic/relation table (app.js RAG_MEMORY_TOPIC_OVERRIDES
/ RAG_MEMORY_RELATION_NOTES, removed 2026-09-14) with a governed server-owned
path. Runs against a real in-process server/DB via the existing
MemoryCandidateApiTests fixture, exercising the action over real HTTP exactly
as the UI would call it.

2026-09-14: the first version of this action bound idempotency to the relation
edge alone (memory_id, target_memory_id, relation) and applied a `knowledge`
write unconditionally on every call. A Conductor probe (relate-replay-probe.py)
reproduced silent data loss: sending the same relation payload twice but with
a DIFFERENT knowledge.topic each time returned 200/200, the second reported
MEMORY_RELATION_REPLAYED (implying nothing changed), yet the stored topic had
actually changed underneath. The fix binds the whole request (relation AND
knowledge together) to a caller-supplied request_id, mirroring
TodoActions.change_state's request_id+request_json dedupe — see
test_probe_scenario_relation_replay_no_longer_hides_a_knowledge_change below,
which is the exact probe scenario, now with the required request_id.
"""
from __future__ import annotations

import itertools
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import test_memory_candidates_and_delegations as fixtures

_REQUEST_ID_SEQ = itertools.count()


def next_request_id():
    return f"relate-test-{next(_REQUEST_ID_SEQ):08d}"


class MemoryRelateActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.request = cls.fixture.request

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def make_memory(self, title, body):
        return self.server.store.create_project_memory(
            "TEST", {"title": title, "body": body, "state": "DECISION_NOTE"}
        )

    def act(self, request, request_id=None):
        body = {**request, "request_id": request_id or next_request_id()}
        return self.request("POST", "/v1/actions", {"action_id": "rag.memory-relate", "request": body})

    def relate_request(self, source, target, note, relation="COMPLEMENTS", expected_relation_revision=0):
        return {
            "project_id": "TEST",
            "memory_id": source["memory_id"],
            "expected_memory_digest": source["memory_digest"],
            "relation": relation,
            "target_memory_id": target["memory_id"],
            "expected_target_memory_digest": target["memory_digest"],
            "expected_relation_revision": expected_relation_revision,
            "note": note,
        }

    # --- the exact Conductor-reported defect ----------------------------

    def test_probe_scenario_relation_replay_no_longer_hides_a_knowledge_change(self):
        child = self.make_memory("probe-child", "body child")
        target = self.make_memory("probe-target", "body target")
        base = self.relate_request(child, target, "same request note")

        first = self.act({**base, "knowledge": {"kind": "USER_DECISION", "topic": "Topic A", "applicability": "scope"}})
        self.assertEqual(first[0], 200)

        # A genuinely NEW request_id with a different knowledge half is a
        # real, distinct, intentional change — it must actually apply and be
        # reported as a knowledge change, not silently ignored.
        base_revised = {**base, "expected_relation_revision": 1}
        second = self.act({**base_revised, "knowledge": {"kind": "USER_DECISION", "topic": "Topic B", "applicability": "scope"}})
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["knowledge_changed"], True)
        self.assertEqual(second[1]["relation_changed"], False)  # same relation identity+note: no relation-side change
        stored = self.server.store.get_project_memory("TEST", child["memory_id"])
        self.assertEqual(stored["knowledge"]["topic"], "Topic B", "the second, distinct request must actually apply")
        # Reusing one SAME request_id for two different knowledge values (the
        # actual probe shape) is covered precisely by the next test.

    def test_same_request_id_different_payload_rejected_atomically(self):
        child = self.make_memory("probe-child2", "body")
        target = self.make_memory("probe-target2", "body")
        base = self.relate_request(child, target, "note")
        rid = next_request_id()
        first = self.act({**base, "knowledge": {"kind": "USER_DECISION", "topic": "Topic A", "applicability": "s"}}, request_id=rid)
        self.assertEqual(first[0], 200)
        # Same request_id, semantically different payload (topic changed) ->
        # 409, and nothing must have been written for this second call: the
        # stored topic must still read "Topic A".
        second = self.act({**base, "knowledge": {"kind": "USER_DECISION", "topic": "Topic Z", "applicability": "s"}}, request_id=rid)
        self.assertEqual(second[0], 409)
        self.assertEqual(second[1]["error_code"], "MEMORY_RELATE_REQUEST_CONFLICT")
        stored = self.server.store.get_project_memory("TEST", child["memory_id"])
        self.assertEqual(stored["knowledge"]["topic"], "Topic A", "a rejected conflicting replay must not partially apply")

    def test_same_request_id_same_payload_is_a_pure_replay(self):
        child = self.make_memory("probe-child3", "body")
        target = self.make_memory("probe-target3", "body")
        base = self.relate_request(child, target, "note")
        req = {**base, "knowledge": {"kind": "USER_DECISION", "topic": "Topic A", "applicability": "s"}}
        rid = next_request_id()
        first = self.act(req, request_id=rid)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]["replayed"], False)
        second = self.act(req, request_id=rid)
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["replayed"], True)
        self.assertEqual(second[1]["relation"]["relation_id"], first[1]["relation"]["relation_id"])

    # --- project-scope / target existence -----------------------------

    def test_target_must_exist_in_same_project(self):
        rep = self.make_memory("Rep", "representative body")
        status, result = self.act({
            "project_id": "TEST",
            "memory_id": rep["memory_id"],
            "expected_memory_digest": rep["memory_digest"],
            "relation": "COMPLEMENTS",
            "target_memory_id": "memory_does_not_exist",
            "expected_target_memory_digest": "0" * 64,
            "note": "n",
        })
        self.assertEqual(status, 404)
        self.assertEqual(result["error_code"], "MEMORY_RELATION_TARGET_NOT_FOUND")

    def test_target_in_a_different_project_is_rejected(self):
        other_root = Path(self.fixture.temp.name) / "OTHER"
        other_root.mkdir(exist_ok=True)
        (other_root / "REPOSITORY_MANIFEST.md").write_text("# OTHER\n", encoding="utf-8")
        self.server.store.register_project({"project_id": "OTHER", "project_root": str(other_root)})
        other = self.server.store.create_project_memory("OTHER", {"title": "Other", "body": "b", "state": "DECISION_NOTE"})
        rep = self.make_memory("Rep2", "representative body 2")
        status, result = self.act(self.relate_request(rep, other, "n"))
        self.assertEqual(status, 404)
        self.assertEqual(result["error_code"], "MEMORY_RELATION_TARGET_NOT_FOUND")

    def test_self_relation_rejected(self):
        rep = self.make_memory("Rep3", "body")
        status, result = self.act(self.relate_request(rep, rep, "n"))
        self.assertEqual(status, 400)
        self.assertEqual(result["error_code"], "MEMORY_RELATION_SELF_REFERENCE")

    # --- digest / concurrency guard (both source AND target) ------------

    def test_stale_source_digest_conflicts(self):
        rep = self.make_memory("Rep4", "body")
        child = self.make_memory("Child4", "body")
        req = self.relate_request(child, rep, "n")
        req["expected_memory_digest"] = "0" * 64
        status, result = self.act(req)
        self.assertEqual(status, 409)
        self.assertEqual(result["error_code"], "MEMORY_DIGEST_CONFLICT")

    def test_stale_target_digest_conflicts(self):
        rep = self.make_memory("Rep4b", "body")
        child = self.make_memory("Child4b", "body")
        req = self.relate_request(child, rep, "n")
        req["expected_target_memory_digest"] = "0" * 64
        status, result = self.act(req)
        self.assertEqual(status, 409)
        self.assertEqual(result["error_code"], "MEMORY_RELATION_TARGET_DIGEST_CONFLICT")

    def test_target_digest_required_with_target_memory_id(self):
        rep = self.make_memory("Rep4c", "body")
        child = self.make_memory("Child4c", "body")
        req = self.relate_request(child, rep, "n")
        del req["expected_target_memory_digest"]
        status, result = self.act(req)
        self.assertEqual(status, 400)
        self.assertEqual(result["error_code"], "MEMORY_RELATION_INVALID")

    # --- idempotency / conflicting replay at the request_id layer -------

    def test_identical_request_id_replay_is_idempotent(self):
        rep = self.make_memory("Rep5", "body")
        child = self.make_memory("Child5", "body")
        req = self.relate_request(child, rep, "same note")
        rid = next_request_id()
        status1, result1 = self.act(req, request_id=rid)
        self.assertEqual(status1, 200)
        self.assertEqual(result1["status"], "MEMORY_RELATION_RECORDED")
        status2, result2 = self.act(req, request_id=rid)
        self.assertEqual(status2, 200)
        self.assertEqual(result2["replayed"], True)
        self.assertEqual(result2["relation"]["relation_id"], result1["relation"]["relation_id"])

    # --- intentional revision, CAS-guarded, history preserved -----------

    def test_intentional_relation_revision_updates_note_and_keeps_history(self):
        rep = self.make_memory("Rep6", "body")
        child = self.make_memory("Child6", "body")
        first = self.act(self.relate_request(child, rep, "first note"))
        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]["relation"]["revision"], 1)
        second = self.act(self.relate_request(child, rep, "revised note", expected_relation_revision=1))
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["relation_changed"], True)
        self.assertEqual(second[1]["relation"]["revision"], 2)
        self.assertEqual(second[1]["relation"]["note"], "revised note")
        # History preserved in project_event (no dedicated listing method
        # exists yet, so query the append-only table directly). Scoped by
        # this test's own relation_id since project_event is a shared,
        # project-wide log across the whole test class run.
        relation_id = second[1]["relation"]["relation_id"]
        with self.server.store._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM project_event WHERE project_id='TEST' AND event_type='MEMORY_RELATION_REVISED' "
                "AND payload_json LIKE ?",
                (f'%{relation_id}%',),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("first note", rows[0]["payload_json"])

    def test_stale_relation_revision_rejected(self):
        rep = self.make_memory("Rep6b", "body")
        child = self.make_memory("Child6b", "body")
        first = self.act(self.relate_request(child, rep, "first note"))
        self.assertEqual(first[0], 200)
        # Wrong expected_relation_revision (0, as if it were new) while a
        # relation already exists at revision 1 -> reject, do not silently update.
        status, result = self.act(self.relate_request(child, rep, "sneaky overwrite", expected_relation_revision=0))
        self.assertEqual(status, 409)
        self.assertEqual(result["error_code"], "MEMORY_RELATION_REVISION_CONFLICT")
        stored = self.server.store.get_project_memory("TEST", child["memory_id"])
        relations = [r for r in stored["relations"] if r["memory_id"] == child["memory_id"]]
        self.assertEqual(relations[0]["note"], "first note")

    # --- knowledge/topic write, reusable by any memory ------------------

    def test_knowledge_topic_write_and_readback(self):
        mem = self.make_memory("Topic test", "body content")
        status, result = self.act({
            "project_id": "TEST",
            "memory_id": mem["memory_id"],
            "expected_memory_digest": mem["memory_digest"],
            "note": "classify",
            "knowledge": {"kind": "USER_DECISION", "topic": "임의 주제", "applicability": "테스트"},
        })
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "MEMORY_KNOWLEDGE_RECORDED")
        self.assertEqual(result["memory"]["knowledge"]["topic"], "임의 주제")
        status2, listing = self.request("GET", "/v1/projects/TEST/memories")
        self.assertEqual(status2, 200)
        found = next(m for m in listing["memories"] if m["memory_id"] == mem["memory_id"])
        self.assertEqual(found["knowledge"]["topic"], "임의 주제")

    def test_identical_knowledge_resubmission_is_a_true_no_op(self):
        mem = self.make_memory("Topic test2", "body content")
        req = {
            "project_id": "TEST", "memory_id": mem["memory_id"],
            "expected_memory_digest": mem["memory_digest"], "note": "classify",
            "knowledge": {"kind": "USER_DECISION", "topic": "동일 주제", "applicability": "테스트"},
        }
        first = self.act(req)
        self.assertEqual(first[1]["knowledge_changed"], True)
        second = self.act(req)  # different request_id, identical content
        self.assertEqual(second[0], 200)
        self.assertEqual(second[1]["status"], "MEMORY_RELATE_NO_CHANGE")
        self.assertEqual(second[1]["knowledge_changed"], False)

    # --- relations attached on read paths, both directions ---------------

    def test_relations_readable_from_both_sides_via_list(self):
        rep = self.make_memory("Rep7", "body")
        child = self.make_memory("Child7", "body")
        status, _ = self.act(self.relate_request(child, rep, "n"))
        self.assertEqual(status, 200)
        status2, listing = self.request("GET", "/v1/projects/TEST/memories")
        self.assertEqual(status2, 200)
        by_id = {m["memory_id"]: m for m in listing["memories"]}
        self.assertEqual(len(by_id[child["memory_id"]]["relations"]), 1)
        self.assertEqual(len(by_id[rep["memory_id"]]["relations"]), 1)
        self.assertEqual(by_id[child["memory_id"]]["relations"][0]["relation"], "COMPLEMENTS")

    # --- ignore/restore: both source AND target sides ---------------------

    def test_ignored_source_cannot_be_related_until_restored(self):
        rep = self.make_memory("Rep8", "body")
        child = self.make_memory("Child8", "body")
        status_ignore, ignore_result = self.request("POST", "/v1/actions", {
            "action_id": "rag.memory-retention",
            "request": {
                "project_id": "TEST", "memory_id": child["memory_id"],
                "expected_memory_digest": child["memory_digest"], "expected_revision": 0,
                "decision": "IGNORE", "note": "test ignore",
            },
        })
        self.assertEqual(status_ignore, 200)
        req = self.relate_request(child, rep, "n")
        req["expected_memory_digest"] = ignore_result["memory"]["memory_digest"]
        status, result = self.act(req)
        self.assertEqual(status, 409)
        self.assertEqual(result["error_code"], "MEMORY_IGNORED")

    def test_ignored_target_cannot_receive_a_new_relation(self):
        rep = self.make_memory("Rep8b", "body")
        child = self.make_memory("Child8b", "body")
        status_ignore, ignore_result = self.request("POST", "/v1/actions", {
            "action_id": "rag.memory-retention",
            "request": {
                "project_id": "TEST", "memory_id": rep["memory_id"],
                "expected_memory_digest": rep["memory_digest"], "expected_revision": 0,
                "decision": "IGNORE", "note": "test ignore target",
            },
        })
        self.assertEqual(status_ignore, 200)
        req = self.relate_request(child, rep, "n")
        req["expected_target_memory_digest"] = ignore_result["memory"]["memory_digest"]
        status, result = self.act(req)
        self.assertEqual(status, 409)
        self.assertEqual(result["error_code"], "MEMORY_RELATION_TARGET_IGNORED")

    # --- concurrent writers: no lost update ------------------------------

    def test_concurrent_relation_revision_updates_do_not_lose_a_writer(self):
        rep = self.make_memory("Rep9", "body")
        child = self.make_memory("Child9", "body")
        first = self.act(self.relate_request(child, rep, "base note"))
        self.assertEqual(first[0], 200)

        results = []

        def attempt(note):
            results.append(self.act(self.relate_request(child, rep, note, expected_relation_revision=1)))

        threads = [threading.Thread(target=attempt, args=(f"writer-{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = sorted(r[0] for r in results)
        # Exactly one of the two concurrent revision-1 updates must win; the
        # other must see a revision conflict, never a silent lost update.
        self.assertEqual(statuses, [200, 409])


if __name__ == "__main__":
    unittest.main()
