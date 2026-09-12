import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from universe_batch_runtime import MemoryBatchRuntimePool
from universe_app.connection import UniverseError

class BatchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        self.serial=0
        self.alive=True
        def factory(project):
            self.serial+=1
            sid="batch-runtime-"+str(self.serial)
            binding=dict(session_id=sid,origin_anchor_ref="anchor-"+sid,endpoint="http://127.0.0.1:1",
                token="private-"+sid,runtime_currentness_observation="CURRENT")
            return SimpleNamespace(start=lambda:(self.events.append("start") or binding),
                stop=lambda:self.events.append("stop"),
                reconcile=lambda:"LIVE" if self.alive else "EXITED")
        self.pool=MemoryBatchRuntimePool(factory)
    def request(self,public):
        return {**public,"frame_id":"frame","task_frame_ref":"frame","turn_id":"turn","invoker_actor_ref":"actor"}
    def test_no_supervisor_and_credentials_expire_after_cleanup(self):
        with self.pool.execution("p","FAST_EXTRACT") as (binding,public):
            request=self.request(public);self.pool.bind_frame("p",request)
            self.assertEqual(binding["token"],self.pool.resolve("p",request)["token"])
            with self.assertRaises(UniverseError):self.pool.resolve("other",request)
            with self.assertRaises(UniverseError):self.pool.resolve("p",{**request,"turn_id":"other"})
            with self.assertRaises(UniverseError):self.pool.resolve("p",{**request,"credential_ref":"wrong"})
            self.alive=False
            with self.assertRaises(UniverseError):self.pool.resolve("p",request)
        with self.assertRaises(UniverseError):self.pool.resolve("p",request)
        self.assertEqual(["start","stop"],self.events)
    def test_duplicate_stage_does_not_start_second_runtime(self):
        with self.pool.execution("p","FAST_EXTRACT"):
            with self.assertRaises(UniverseError) as caught:
                with self.pool.execution("p","FAST_EXTRACT"):self.fail()
            self.assertEqual("MEMORY_BATCH_RUNTIME_BUSY",caught.exception.code)
        self.assertEqual(["start","stop"],self.events)
    def test_failure_cleans_runtime_and_next_batch_is_fresh(self):
        with self.assertRaisesRegex(ValueError,"failed"):
            with self.pool.execution("p","FAST_EXTRACT") as (_,first):
                raise ValueError("failed")
        with self.pool.execution("p","FAST_EXTRACT") as (_,second):
            self.assertNotEqual(first["session_id"],second["session_id"])
        self.assertEqual(["start","stop","start","stop"],self.events)
    def test_shutdown_stops_owned_runtime_once_and_rejects_new_jobs(self):
        with self.pool.execution("p","FAST_EXTRACT"):
            self.pool.close()
        self.assertEqual(["start","stop"],self.events)
        with self.assertRaises(UniverseError):
            with self.pool.execution("p","FAST_EXTRACT"):self.fail()
    def test_start_failure_is_cleaned(self):
        def failed():raise ValueError("startup failed")
        pool=MemoryBatchRuntimePool(lambda project:SimpleNamespace(start=failed,stop=lambda:self.events.append("stop")))
        with self.assertRaises(ValueError):
            with pool.execution("p","FAST_EXTRACT"):self.fail()
        self.assertEqual(["stop"],self.events)
