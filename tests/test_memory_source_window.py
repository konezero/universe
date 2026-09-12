from contextlib import contextmanager
import json
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from universe_app.memory_source_window import select_source_window
from universe_app.connection import UniverseError
from semantic_evidence import semantic_text_digest

class SourceWindowTests(unittest.TestCase):
    def store(self,count=80,chars=1,start=None):
        text="?"*chars
        evidence=[dict(excerpt_id="x",activity_digest="a"*64,ordinal=1,role="USER",
                       text=text,text_digest=semantic_text_digest(text))]
        return SimpleNamespace(
            list_provider_session_sources=lambda:[{"source_id":f"s{i:03}","enabled":True,"status":"ACTIVE"} for i in range(count)],
            get_memory_source_position=lambda project:{"next_source_id":start},
            prepare_provider_activity_batch=lambda sid:{"source":{"provider":"CODEX"},"activity_refs":[{"id":sid}]},
            provider_session_observer=SimpleNamespace(build_transient_semantic_evidence=lambda *args, **kwargs:evidence))
    def test_batch_count_limit_and_cursor_rotate_without_starvation(self):
        store=self.store()
        selected,report=select_source_window(store,"p")
        self.assertEqual(64,len(selected));self.assertEqual(16,report["deferred_count"])
        store.get_memory_source_position=lambda project:{"next_source_id":report["next_source_id"]}
        next_selected,_=select_source_window(store,"p")
        self.assertEqual("s064",next_selected[0])
        self.assertEqual(80,len(set(selected+next_selected)))
    def test_total_text_budget_is_bounded(self):
        selected,report=select_source_window(self.store(chars=2000),"p")
        self.assertEqual(16,len(selected));self.assertEqual("s016",report["next_source_id"])
    def test_stale_source_is_reported_without_poisoning_healthy_sources(self):
        store=self.store(count=3)
        original=store.provider_session_observer.build_transient_semantic_evidence
        def read(sid,refs, **kwargs):
            if sid=="s000":raise UniverseError("SEMANTIC_SOURCE_NOT_CURRENT","old location",409)
            return original(sid,refs)
        store.provider_session_observer.build_transient_semantic_evidence=read
        ids,report=select_source_window(store,"p")
        self.assertEqual(["s001","s002"],ids);self.assertEqual(1,report["skipped_count"])
        self.assertEqual("SEMANTIC_SOURCE_NOT_CURRENT",report["skipped"][0]["error_code"])
    def test_other_provider_sources_are_reported_and_never_read_for_extraction(self):
        store=self.store(count=3)
        store.prepare_provider_activity_batch=lambda sid:{"source":{"provider":"UNSUPPORTED" if sid=="s000" else "CODEX"},"activity_refs":[{"id":sid}]}
        original=store.provider_session_observer.build_transient_semantic_evidence
        read=[]
        def evidence(sid,refs, **kwargs):
            read.append(sid)
            return original(sid,refs)
        store.provider_session_observer.build_transient_semantic_evidence=evidence
        ids,report=select_source_window(store,"p")
        self.assertEqual(["s001","s002"],ids)
        self.assertEqual(ids,read)
        self.assertEqual(0,report["deferred_count"])
        self.assertEqual([{"source_id":"s000","error_code":"FAST_EXTRACT_PROVIDER_INVALID"}],report["skipped"])
    def test_all_supported_source_providers_are_selected(self):
        store=self.store(count=3)
        providers=dict(zip(["s000","s001","s002"],["CODEX","CLAUDE","GROK"]))
        store.prepare_provider_activity_batch=lambda sid:{"source":{"provider":providers[sid]},"activity_refs":[{"id":sid}]}
        ids,report=select_source_window(store,"p")
        self.assertEqual(list(providers),ids)
        self.assertEqual(0,report["skipped_count"])
        self.assertEqual(set(providers.values()),{v["source"]["provider"] for v in report["activity_windows"].values()})
    def test_only_unsupported_sources_do_not_advance(self):
        store=self.store(count=2)
        store.prepare_provider_activity_batch=lambda sid:{"source":{"provider":"UNSUPPORTED"},"activity_refs":[{"id":sid}]}
        with self.assertRaises(UniverseError) as caught:select_source_window(store,"p")
        self.assertEqual("MEMORY_BATCH_SOURCES_UNAVAILABLE",caught.exception.code)
        self.assertEqual(2,json.loads(caught.exception.detail)["skipped_count"])
    def test_oversized_source_resumes_after_exact_activity(self):
        store=self.store(count=1)
        refs=[{"activity_id":str(i),"ordinal":i,"activity_digest":str(i)} for i in range(1,601)]
        store.prepare_provider_activity_batch=lambda sid:{"source":{"provider":"CODEX","source_id":sid},"activity_refs":list(reversed(refs))}
        ids,first=select_source_window(store,"p")
        self.assertEqual(512,len(first["activity_windows"]["s000"]["activity_refs"]))
        self.assertEqual(512,first["activity_resume"]["after"]["ordinal"])
        store.get_memory_source_position=lambda project:{"next_source_id":"s000","selection":first}
        ids,second=select_source_window(store,"p")
        self.assertEqual(88,len(second["activity_windows"]["s000"]["activity_refs"]))
        self.assertIsNone(second["activity_resume"])
        del refs[511]
        with self.assertRaises(UniverseError) as caught:select_source_window(store,"p")
        self.assertEqual("MEMORY_ACTIVITY_CURSOR_STALE",caught.exception.code)
    def test_window_revalidation_rejects_changed_activity(self):
        from universe_app.memory_source_window import apply_activity_window
        batch={"source":{"provider":"CODEX","source_id":"s"},"activity_refs":[{"activity_id":"a","activity_digest":"one"}]}
        window={"source":batch["source"],"activity_refs":[dict(batch["activity_refs"][0])]}
        self.assertEqual(batch,apply_activity_window(batch,window))
        batch["activity_refs"][0]["activity_digest"]="changed"
        with self.assertRaises(UniverseError):apply_activity_window(batch,window)
    def test_empty_prefix_does_not_hide_later_semantic_activities(self):
        from provider_session_observer import ProviderSessionObserverError
        store=self.store(count=1)
        refs=[{"activity_id":str(i),"ordinal":i} for i in range(1,601)]
        store.prepare_provider_activity_batch=lambda sid:{"source":{"provider":"CODEX","source_id":sid},"activity_refs":refs}
        original=store.provider_session_observer.build_transient_semantic_evidence
        def evidence(sid,selected,**kwargs):
            if selected[-1]["ordinal"] <= 512:raise ProviderSessionObserverError("SEMANTIC_EVIDENCE_EMPTY","no messages")
            return original(sid,selected,**kwargs)
        store.provider_session_observer.build_transient_semantic_evidence=evidence
        ids,report=select_source_window(store,"p")
        self.assertEqual(513,report["activity_windows"]["s000"]["activity_refs"][0]["ordinal"])
        self.assertIsNone(report["activity_resume"])
    def test_observer_validation_error_preserves_typed_http_boundary(self):
        from provider_session_observer import ProviderSessionObserverError
        store=self.store(count=1)
        def failed(*args, **kwargs):raise ProviderSessionObserverError("SEMANTIC_EVIDENCE_INVALID","invalid selected reference")
        store.provider_session_observer.build_transient_semantic_evidence=failed
        with self.assertRaises(UniverseError) as caught:select_source_window(store,"p")
        self.assertEqual("SEMANTIC_EVIDENCE_INVALID",caught.exception.code)
        self.assertEqual("invalid selected reference",caught.exception.detail)
    def test_empty_sources_do_not_invoke_or_advance(self):
        store=self.store(count=3);store.prepare_provider_activity_batch=lambda sid:{"activity_refs":[]}
        with self.assertRaises(UniverseError) as caught:select_source_window(store,"p")
        self.assertEqual("MEMORY_BATCH_SOURCES_UNAVAILABLE",caught.exception.code)
        self.assertEqual(3,json.loads(caught.exception.detail)["skipped_count"])
    def test_unexpected_errors_are_not_silently_skipped(self):
        store=self.store()
        def failed(sid):raise UniverseError("DATABASE_UNAVAILABLE","failure",500)
        store.prepare_provider_activity_batch=failed
        with self.assertRaises(UniverseError) as caught:select_source_window(store,"p")
        self.assertEqual("DATABASE_UNAVAILABLE",caught.exception.code)
    def test_cursor_is_durable_and_requires_completed_run(self):
        from universe_server import UniverseStore
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"cursor.sqlite3"
            @contextmanager
            def connection():
                c=sqlite3.connect(path);c.row_factory=sqlite3.Row
                try:
                    with c:
                        yield c
                finally:
                    c.close()
            with connection() as c:
                c.executescript("CREATE TABLE memory_batch_source_position(project_id TEXT PRIMARY KEY,next_source_id TEXT,last_run_id TEXT,selection_json TEXT,updated_at TEXT); CREATE TABLE memory_batch_run(project_id TEXT,stage TEXT,run_id TEXT,status TEXT);")
                c.executemany("INSERT INTO memory_batch_run VALUES ('p','FAST_EXTRACT',?,?)",[("ok","COMPLETED"),("bad","FAILED")])
            store=SimpleNamespace(_connection=connection,get_project=lambda project:{"project_id":project})
            store.get_memory_source_position=lambda project:UniverseStore.get_memory_source_position(store,project)
            report={"next_source_id":"s064","selected_count":64}
            UniverseStore.advance_memory_source_position(store,"p","ok",report)
            self.assertEqual("s064",store.get_memory_source_position("p")["next_source_id"])
            with self.assertRaises(UniverseError) as caught:
                UniverseStore.advance_memory_source_position(store,"p","ok",{"next_source_id":"s999","previous_run_id":"stale"})
            self.assertEqual("MEMORY_ACTIVITY_CURSOR_CONFLICT",caught.exception.code)
            with self.assertRaises(UniverseError):
                UniverseStore.advance_memory_source_position(store,"p","bad",{"next_source_id":"s000"})
            self.assertEqual("s064",store.get_memory_source_position("p")["next_source_id"])
