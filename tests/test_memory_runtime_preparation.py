import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT/".ai/runtime"))
from universe_app.memory_runtime_preparation import prepared_memory_frame
from universe_app.connection import UniverseError
from reference_runtime.task_frame_runtime import build_task_frame_instruction_proposal

class MemoryPreparationTests(unittest.TestCase):
    def config(self):
        return dict(persisted=True,enabled=True,config_id="cfg",revision=1,project_id="p",
                    provider="CODEX",model_ref="gpt-5.6-luna",effort="MAX")
    def host(self, fail=False):
        self.calls=[]
        def propose(plan, **kwargs):
            self.plan=plan
            return build_task_frame_instruction_proposal(plan)
        def post(endpoint,token,path,body):
            self.calls.append((path,body))
            if path.endswith("create"):return {"status":"TASK_FRAME_HOST_ACTIVE"}
            if path.endswith("close"):return {"status":"TASK_FRAME_HOST_CLOSED"}
            return {"status":"TASK_FRAME_OPERATION_APPLIED","output":{"status":"FAILED" if fail else "TASK_TURNS_DECLARED"}}
        return SimpleNamespace(_build_task_frame_proposal=propose,_post_runtime=post,provider_capability=lambda provider: {"status":"AVAILABLE","model":"gpt-5.6-luna"})
    def binding(self):
        return dict(session_id="batch-runtime-s",origin_anchor_ref="a",origin_frame_id="current",
                    runtime_currentness_observation="CURRENT",parent_actor_ref="parent",endpoint="http://127.0.0.1:1",token="fixture")
    def test_fresh_frame_exact_session_and_no_approval(self):
        host=self.host();frames=[]
        for _ in range(2):
            with prepared_memory_frame(host,self.binding(),{"session_id":"batch-runtime-s"},self.config()) as frame:
                frames.append(frame["frame_id"])
                self.assertEqual(frame["frame_id"],frame["task_frame_ref"])
                self.assertEqual("batch-runtime-s",frame["session_id"])
        self.assertNotEqual(*frames)
        creates=[b for p,b in self.calls if p.endswith("create")]
        self.assertIsNone(creates[0]["frame"]["task_frame_execution_approval"])
        self.assertEqual("NONE",creates[0]["frame"]["parent_instruction"]["repository_write_scope"])
        self.assertEqual(2,len([p for p,b in self.calls if p.endswith("close")]))
    def test_declaration_failure_closes_frame(self):
        with self.assertRaises(UniverseError):
            with prepared_memory_frame(self.host(True),self.binding(),{},self.config()):self.fail("must not invoke")
        self.assertTrue(self.calls[-1][0].endswith("close"))
    def test_execution_failure_closes_frame(self):
        with self.assertRaisesRegex(ValueError,"provider failed"):
            with prepared_memory_frame(self.host(),self.binding(),{},self.config()):
                raise ValueError("provider failed")
        self.assertTrue(self.calls[-1][0].endswith("close"))
    def test_installed_runtime_accepts_frame_and_turn(self):
        from reference_runtime.task_frame_runtime import load_profile, TaskFrameRuntime, ParentObservation, TaskTurn
        from universe_app.memory_runtime_preparation import PROFILE
        host=self.host()
        original=host._post_runtime
        frames=[]
        def post(endpoint,token,path,body):
            if path.endswith("create"):
                payload=dict(body["frame"])
                observation=payload.pop("parent_observation")
                runtime=TaskFrameRuntime(profile=load_profile(ROOT,PROFILE),
                    parent_observation=ParentObservation(**observation), **payload)
                frames.append(runtime)
            if path.endswith("operation"):
                operation=body["operation"]
                output=frames[-1].declare_turns(turns=[TaskTurn(**t) for t in operation["turns"]],observed_at=operation["observed_at"])
                self.assertEqual("TASK_TURNS_DECLARED",output["status"])
            if path.endswith("close"):
                frames[-1].conn.close()
            return original(endpoint,token,path,body)
        host._post_runtime=post
        with prepared_memory_frame(host,self.binding(),{"session_id":"batch-runtime-s"},self.config()) as prepared:
            self.assertTrue(prepared["turn_id"])

    def test_scheduler_enters_common_preparation_and_returns_real_run(self):
        from universe_server import UniverseHTTPServer
        config={**self.config(),"stage":"FAST_EXTRACT","fallback":"NONE","resolution":{"status":"AVAILABLE"}}
        captured=[]
        from universe_batch_runtime import MemoryBatchRuntimePool
        fake_runtime=SimpleNamespace(start=lambda:self.binding(),stop=lambda:None,reconcile=lambda:"LIVE")
        pool=MemoryBatchRuntimePool(lambda project:fake_runtime)
        server=SimpleNamespace(
            _memory_batch_runtimes=pool,
            store=SimpleNamespace(get_memory_batch_config=lambda *args:config,
                list_provider_session_sources=lambda:[{"source_id":"registered-source","enabled":True,"status":"ACTIVE"}],
                get_memory_source_position=lambda project:{},
                advance_memory_source_position=lambda project,run,selection:selection,
                prepare_provider_activity_batch=lambda sid:{"activity_refs":[{"id":"one"}]},
                provider_session_observer=SimpleNamespace(build_transient_semantic_evidence=lambda *args:[{
                    "excerpt_id":"e","activity_digest":"a"*64,"ordinal":1,"role":"USER","text":"data",
                    "text_digest":__import__("semantic_evidence").semantic_text_digest("data")}])),
            runtime_host=self.host(),
            _resolve_memory_batch_config=lambda *args:(config,{}))
        def run(project,request):
            if "runtime_binding" not in request:
                return UniverseHTTPServer.run_memory_batch(server,project,request)
            captured.append(request)
            return {"run":{"run_id":"actual-run","status":"COMPLETED"}}
        server.run_memory_batch=run
        result=UniverseHTTPServer._run_scheduled_memory_batch(server,"p","FAST_EXTRACT")
        self.assertEqual("actual-run",result["run_id"])
        self.assertEqual(["registered-source"],captured[0]["source_ids"])
        self.assertEqual("SCHEDULED",captured[0]["trigger"])
        self.assertTrue(self.calls[-1][0].endswith("close"))

    def test_unpersisted_configuration_does_not_create_authority_or_frame(self):
        host=self.host()
        config=self.config();config["persisted"]=False
        with self.assertRaises(UniverseError):
            with prepared_memory_frame(host,self.binding(),{},config):self.fail("unexpected execution")
        self.assertEqual([],self.calls)
    def test_cleanup_failure_preserves_primary_provider_error(self):
        host=self.host();original=host._post_runtime
        def post(endpoint,token,path,body):
            if path.endswith("close"):raise UniverseError("CLOSE_FAILURE","cleanup failed",409)
            return original(endpoint,token,path,body)
        host._post_runtime=post
        with self.assertRaisesRegex(ValueError,"provider failed") as caught:
            with prepared_memory_frame(host,self.binding(),{},self.config()):
                raise ValueError("provider failed")
        self.assertIn("CLOSE_FAILURE",str(caught.exception.__notes__))
