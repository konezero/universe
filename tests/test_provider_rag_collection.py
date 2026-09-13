import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from provider_session_observer import ProviderSessionObserverStore,ProviderSessionObserverError
from provider_rag_collection import backfill_source,prepare_sources,recover_missing_source
from universe_app.memory_source_window import select_source_window


class RagCollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=ProviderSessionObserverStore(self.root/"state.sqlite3")
    def write(self,path,events):
        path.write_text("".join(json.dumps(x)+"\n" for x in events),encoding="utf-8")
    def register(self,path,provider="CODEX",tail=False):
        return self.store.register_source(dict(provider=provider,provider_session_id="session",source_path=str(path),source_kind={"CODEX":"CODEX_ROLLOUT_JSONL","CLAUDE":"CLAUDE_SESSION_JSONL"}[provider],start_at_end=tail))["source_id"]
    def event(self,text,i=1):
        return {"type":"response_item","id":str(i),"payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":text}]}}
    def test_history_is_bounded_durable_and_does_not_rewind_live_cursor(self):
        path=self.root/"rollout-history.jsonl";events=[self.event("first",1),self.event("second",2)]
        self.write(path,events);sid=self.register(path,tail=True)
        before=self.store.list_sources()[0]["cursor"]
        first=backfill_source(self.store,sid,max_events=1)
        self.assertEqual("PENDING",first["state"])
        self.store=ProviderSessionObserverStore(self.root/"state.sqlite3")
        second=backfill_source(self.store,sid,max_events=1)
        self.assertEqual("COMPLETED",second["state"])
        self.assertEqual(before,self.store.list_sources()[0]["cursor"])
        self.assertEqual([],self.store.list_activities(sid))
        refs=self.store.build_batch_candidate(sid)["activity_refs"]
        self.assertEqual(2,len(refs))
        self.assertEqual(["first","second"],[x["text"] for x in self.store.build_transient_semantic_evidence(sid,refs,require_complete=True)])
        self.assertEqual(0,backfill_source(self.store,sid)["added"])
        self.write(path,events+[self.event("third",3)]);self.store.scan(sid)
        self.assertEqual(3,len(self.store.build_batch_candidate(sid)["activity_refs"]))
        self.assertEqual(1,len(self.store.list_activities(sid)))
    def test_claude_live_leaf_reaches_historical_parent(self):
        path=self.root/"claude.jsonl"
        root={"type":"user","uuid":"root","message":{"content":"old request"}}
        self.write(path,[root]);sid=self.register(path,"CLAUDE",True)
        self.write(path,[root,{"type":"assistant","uuid":"child","parentUuid":"root","message":{"content":"new answer"}}]);self.store.scan(sid)
        before=self.store.list_activities(sid)
        backfill_source(self.store,sid)
        self.assertEqual(before,self.store.list_activities(sid))
        refs=self.store.build_batch_candidate(sid)["activity_refs"]
        evidence=self.store.build_transient_semantic_evidence(sid,refs,require_complete=True)
        # The contract ordinals remain stable; file order supplies Claude lineage.
        self.assertEqual(["old request","new answer"],[x["text"] for x in evidence])
    def test_large_activity_pages_cover_entire_redacted_text_and_reject_change(self):
        path=self.root/"rollout-large.jsonl";body="".join(str(i%10) for i in range(70001))
        self.write(path,[self.event(body)]);sid=self.register(path)
        self.store.register_source(dict(provider="CODEX",provider_session_id="session",source_path=str(path),source_kind="CODEX_ROLLOUT_JSONL",origin_project_id="p",owner_project_id="p",ownership_state="ASSIGNED"))
        self.store.scan(sid)
        position={}
        store=SimpleNamespace(list_provider_session_sources=self.store.list_sources,get_memory_source_position=lambda project:position,prepare_provider_activity_batch=self.store.build_batch_candidate,provider_session_observer=self.store)
        texts=[];saved=None
        for i in range(3):
            ids,selection=select_source_window(store,"p");window=selection["activity_windows"][sid]
            page=dict(window["semantic_page"])
            evidence=self.store.build_transient_semantic_evidence(sid,window["activity_refs"],require_complete=True,semantic_page=page)
            self.assertLessEqual(sum(len(x["text"]) for x in evidence),32000)
            texts.extend(x["text"] for x in evidence)
            position={"next_source_id":sid,"selection":selection}
            if i==0:saved=dict(selection["activity_resume"]["semantic_page"])
        self.assertEqual(body,"".join(texts));self.assertIsNone(selection["activity_resume"])
        self.write(path,[self.event("x"+body[1:])])
        with self.assertRaises(ProviderSessionObserverError) as caught:
            self.store.build_transient_semantic_evidence(sid,window["activity_refs"],require_complete=True,semantic_page=saved)
        self.assertEqual("SEMANTIC_SOURCE_NOT_CURRENT",caught.exception.code)
    def test_metadata_recovery_keeps_unknown_event_fail_closed(self):
        path=self.root/"claude.jsonl"
        self.write(path,[{"type":"custom-title","customTitle":"title"},{"type":"cost-state","totalCostUSD":1},{"type":"assistant","uuid":"a","message":{"content":"answer"}}])
        sid=self.register(path,"CLAUDE");self.assertEqual("ACTIVE",self.store.scan(sid)["source"]["status"])
        self.write(path,[{"type":"unrecognized-control","unknown":"value"}])
        # A separate historical source is not invented to bypass malformed data.
        other=self.root/"other.jsonl";self.write(other,[{"type":"unrecognized-control"}]);oid=self.register(other,"CLAUDE")
        self.assertEqual("SOURCE_SCHEMA_UNSUPPORTED",self.store.scan(oid)["source"]["reason"])
    def test_relocation_requires_same_file_and_verified_session_identity(self):
        old=self.root/"rollout-move.jsonl"
        self.write(old,[{"type":"session_meta","payload":{"id":"session","cwd":str(self.root)}},self.event("answer")]);sid=self.register(old);self.store.scan(sid)
        dest=self.root/"archived_sessions";dest.mkdir();new=dest/old.name;old.rename(new)
        self.store._default_provider_home=lambda provider:self.root
        self.assertEqual("RELOCATED",recover_missing_source(self.store,sid))
        self.assertEqual("ACTIVE",self.store.scan(sid)["source"]["status"])
    def test_concurrent_history_claims_do_not_duplicate_events(self):
        from concurrent.futures import ThreadPoolExecutor
        path=self.root/"rollout-concurrent.jsonl"
        self.write(path,[self.event("one",1),self.event("two",2)])
        sid=self.register(path,tail=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:backfill_source(self.store,sid),range(2)))
        self.assertEqual(2,sum(x["added"] for x in results))
        self.assertEqual(2,len(self.store.build_batch_candidate(sid)["activity_refs"]))
    def test_maintenance_rotates_eight_sources_and_excludes_disabled(self):
        for i in range(10):
            path=self.root/("rollout-"+str(i)+".jsonl")
            self.write(path,[self.event("history")]);self.register(path,tail=True)
        one=prepare_sources(self.store);two=prepare_sources(self.store)
        self.assertEqual(8,one["processed_count"])
        self.assertEqual(two,self.store.rag_maintenance_status())
        self.assertEqual(10,len({x["source_id"] for r in [one,two] for x in r["sources"]}))
        with self.assertRaises(ProviderSessionObserverError):prepare_sources(self.store,["missing"])

    def test_rotation_and_history_error_preserve_position(self):
        path=self.root/"rollout-error.jsonl"
        path.write_text(json.dumps(self.event("first"))+"\nnot-json\n",encoding="utf-8");sid=self.register(path,tail=True)
        result=backfill_source(self.store,sid)
        self.assertEqual("BLOCKED",result["state"]);self.assertEqual("SOURCE_SCHEMA_UNSUPPORTED",result["error_code"])
        self.assertGreater(result["next_offset"],0)
        retry=backfill_source(self.store,sid);self.assertEqual(result["next_offset"],retry["next_offset"]);self.assertEqual(0,retry["added"])
        moved=self.root/"old.jsonl";path.rename(moved);self.write(path,[self.event("replacement")])
        self.assertEqual("SOURCE_ROTATED",backfill_source(self.store,sid)["error_code"])

if __name__=="__main__":unittest.main()
