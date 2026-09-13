"""Eligibility contract tests use an isolated Git project and attested result fixtures."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from universe_server import UniverseStore, UniverseError
from universe_app import memory_source_review as review

def attest_current_fixture(store,candidate_id,status="CURRENT"):
    """Attested model-output fixture for decision workflow tests, not semantic accuracy evidence."""
    candidate=store.get_memory_candidate(candidate_id)
    root=Path(store.get_project(candidate["project_id"])["project_root"])
    source=root/"eligibility_fixture.py";source.write_text("ENABLED = True\n",encoding="utf-8")
    for args in [("init",),("add","."),("-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","--allow-empty","-m","fixture")]:
        subprocess.run(["git","-C",str(root),*args],check=True,capture_output=True,shell=False)
    todo=None
    if status=="FUTURE":todo=store.create_todo({"scope_kind":"PROJECT","project_id":candidate["project_id"],"title":candidate["summary"][:160],"detail":"Fixture future work","state":"READY","priority":"P1","source_kind":"USER","sort_order":0})
    state=review.source_state(store,candidate["project_id"],fresh=True)
    evidence=[{"evidence_id":"S1","kind":"IMPLEMENTATION","path":source.name,"lines":[1],"file_digest":state["file_digests"][source.name],"text":"ENABLED = True"}]
    if todo:evidence=[{"evidence_id":"S1","kind":"OPEN_TODO","todo_id":todo["todo_id"],"revision":todo["revision"],"text":todo["title"]}]
    return review.record(store,candidate,state,evidence,{"status":status,"reason":"Attested model-output fixture","evidence_ids":["S1"]},"fixture://verified-result")

class SourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"project";self.root.mkdir()
        self.source=self.root/"feature.py";self.source.write_text("FEATURE_ENABLED = True\n",encoding="utf-8")
        (self.root/"REPOSITORY_MANIFEST.md").write_text("FEATURE_ENABLED = False\n",encoding="utf-8")
        for args in [("init",),("add","."),("-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-m","fixture")]:
            subprocess.run(["git","-C",str(self.root),*args],check=True,capture_output=True,shell=False)
        self.store=UniverseStore(Path(self.tmp.name)/"store.sqlite3")
        self.store.register_project({"project_id":"TEST","project_root":str(self.root)})
        self.candidate=self.store.create_memory_candidate("TEST",{"stage":"FAST_EXTRACT","summary":"FEATURE_ENABLED is enabled","ref_digests":["a"*64]})[0]
        self.candidate=self.store.get_memory_candidate(self.candidate["candidate_id"])
    def assess(self,status,ids=None):
        state=review.source_state(self.store,"TEST",fresh=True)
        evidence=review.collect_evidence(self.candidate,state)
        if ids is None:ids=[x["evidence_id"] for x in evidence if x["kind"]=="IMPLEMENTATION"]
        return review.record(self.store,self.candidate,state,evidence,{"status":status,"reason":"Fixture classification with selected evidence","evidence_ids":ids},"fixture://verified-result")
    def test_unknown_blocks_keep_and_direct_adoption(self):
        self.assertEqual(["IGNORE"],self.candidate["decision_contract"]["allowed_actions"])
        with self.assertRaises(UniverseError) as caught:self.store.review_memory_candidate(self.candidate["candidate_id"],{"decision":"KEEP"})
        self.assertEqual("MEMORY_SOURCE_REVIEW_REQUIRED",caught.exception.code)
        with self.assertRaises(UniverseError):review.require_eligible(self.store,self.candidate,"ADOPT")
    def test_current_allows_keep_and_adoption_but_changed_source_blocks(self):
        candidate=self.assess("CURRENT")
        self.assertIn("KEEP",candidate["decision_contract"]["allowed_actions"])
        self.assertNotIn("START_PRODUCT_DESIGN",candidate["decision_contract"]["allowed_actions"])
        review.require_eligible(self.store,candidate,"ADOPT")
        self.source.write_text("FEATURE_ENABLED = False\n",encoding="utf-8")
        with self.assertRaises(UniverseError):review.require_eligible(self.store,candidate,"ADOPT")
        self.assertEqual("UNVERIFIED",review.projection(self.store,candidate,fresh=True)["status"])
    def test_direct_adoption_rechecks_source_and_replay_preserves_existing_memory(self):
        candidate=self.assess("CURRENT")
        self.store.review_memory_candidate(candidate["candidate_id"],{"decision":"KEEP"})
        original=self.source.read_text(encoding="utf-8")
        self.source.write_text("FEATURE_ENABLED = False\n",encoding="utf-8")
        with self.assertRaises(UniverseError) as caught:self.store.adopt_memory_candidate(candidate["candidate_id"],candidate["candidate_digest"])
        self.assertEqual("MEMORY_SOURCE_REVIEW_REQUIRED",caught.exception.code)
        self.assertEqual([],self.store.list_project_memories("TEST"))
        self.source.write_text(original,encoding="utf-8")
        memory,created=self.store.adopt_memory_candidate(candidate["candidate_id"],candidate["candidate_digest"])
        self.assertTrue(created)
        self.source.write_text("FEATURE_ENABLED = False\n",encoding="utf-8")
        replay,created=self.store.adopt_memory_candidate(candidate["candidate_id"],candidate["candidate_digest"])
        self.assertFalse(created);self.assertEqual(memory["memory_id"],replay["memory_id"])

    def test_outdated_is_preserved_separately_and_cannot_be_kept(self):
        candidate=self.assess("OUTDATED")
        self.assertEqual("archive",candidate["source_review"]["bucket"])
        self.assertEqual("REVIEW_REQUIRED",candidate["state"])
        self.assertEqual(self.candidate["summary"],candidate["summary"])
        self.assertEqual(["IGNORE"],candidate["decision_contract"]["allowed_actions"])
    def test_missing_or_document_only_evidence_never_establishes_truth(self):
        state=review.source_state(self.store,"TEST",fresh=True);evidence=review.collect_evidence(self.candidate,state)
        for ids in [[],["S999"],[x["evidence_id"] for x in evidence if x["kind"]=="REFERENCE"]]:
            with self.assertRaises(UniverseError):review.record(self.store,self.candidate,state,evidence,{"status":"CURRENT","reason":"fixture","evidence_ids":ids},"receipt")
    def test_future_requires_open_todo_and_never_allows_fact_adoption(self):
        with self.assertRaises(UniverseError):self.assess("FUTURE")
        self.store.create_todo({"scope_kind":"PROJECT","project_id":"TEST","title":"FEATURE_ENABLED is enabled plan","detail":"future work","state":"READY","priority":"P1","source_kind":"USER","sort_order":0})
        state=review.source_state(self.store,"TEST",fresh=True);evidence=review.collect_evidence(self.candidate,state)
        candidate=self.assess("FUTURE",[x["evidence_id"] for x in evidence if x["kind"]=="OPEN_TODO"])
        self.assertIn("START_PRODUCT_DESIGN",candidate["decision_contract"]["allowed_actions"])
        self.assertNotIn("KEEP",candidate["decision_contract"]["allowed_actions"])
        with self.assertRaises(UniverseError):review.require_eligible(self.store,candidate,"ADOPT")
    def test_source_change_during_invocation_rejects_record(self):
        state=review.source_state(self.store,"TEST",fresh=True);evidence=review.collect_evidence(self.candidate,state)
        self.source.write_text("FEATURE_ENABLED = False\n",encoding="utf-8")
        with self.assertRaises(UniverseError):review.record(self.store,self.candidate,state,evidence,{"status":"CURRENT","reason":"fixture","evidence_ids":[x["evidence_id"] for x in evidence if x["kind"]=="IMPLEMENTATION"]},"receipt")
    def test_snippets_not_persisted_and_idempotent_result(self):
        first=self.assess("CURRENT");self.assess("CURRENT")
        with self.store._connection() as c:
            rows=c.execute("SELECT result_json FROM memory_source_assessment").fetchall()
        self.assertEqual(1,len(rows));self.assertNotIn('FEATURE_ENABLED = True',rows[0][0])
        self.assertTrue(first["source_review"]["evidence"][0]["file_digest"])

if __name__=="__main__":unittest.main()
