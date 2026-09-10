import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from universe_runtime_session_bootstrap import bootstrap_managed_session
from universe_session_inject_hook import build_parser, run_hook


def seed(root):
    p = root / '.ai/runtime/project_instance/mode_registry.json'
    p.parent.mkdir(parents=True)
    p.write_bytes((ROOT / '.ai/runtime/project_instance/mode_registry.json').read_bytes())
    (p.parent / 'DISTRIBUTION_MANIFEST.json').write_bytes(
        (ROOT / '.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json').read_bytes())


class ManagedRuntimeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        seed(self.root)

    def session(self, provider='GROK', sid='session_test', mode='CONDUCTOR', ref='vendor_test'):
        return dict(session_id=sid, session_anchor_ref='anchor_'+sid,
                    provider=provider, provider_session_ref=ref, mode=mode)

    def read(self, sid):
        p = self.root / '.ai/runtime/session_store' / ('session_'+sid)
        p = p.with_name('session-'+hashlib.sha256(sid.encode()).hexdigest()[:24]+'.sqlite3')
        with closing(sqlite3.connect(p)) as c:
            return json.loads(c.execute('SELECT snapshot_json FROM anchor_snapshot').fetchone()[0])

    def links(self, mode):
        with closing(sqlite3.connect(self.root / '.ai/runtime/state/project_runtime.sqlite3')) as c:
            s=json.loads(c.execute('SELECT snapshot_json FROM mode_current_anchor WHERE mode=?',(mode,)).fetchone()[0])
            return s['snapshot']['session_anchor_refs']

    def test_all_providers_replay_preserves_other_sessions(self):
        for provider in ['GROK','CLAUDE','CODEX']:
            s=self.session(provider, 'session_'+provider)
            for _ in range(2):
                r=bootstrap_managed_session(self.root,s)
                self.assertEqual('MANAGED_SESSION_BOOTSTRAP_COMPLETE',r['status'],r)
            self.assertEqual(provider,self.read(s['session_id'])['provider'])
        self.assertEqual(3,len(self.links('CONDUCTOR')))

    def test_late_vendor_identity_uses_same_anchor_and_preserves_context(self):
        s=self.session(ref='')
        r=bootstrap_managed_session(self.root,s)
        self.assertEqual('MANAGED_SESSION_BOOTSTRAP_COMPLETE',r['status'],r)
        from reference_runtime.anchor_session_memory_runtime import AnchorSessionMemoryRuntime
        rt=AnchorSessionMemoryRuntime(database_path=Path(r['session_sql_path']))
        stored=rt.stored_snapshot(); snap=dict(stored['snapshot']);snap['task_frame_refs']=[{'frame_ref':'keep'}]
        rt.record_snapshot(snapshot=snap,source_ref=stored['source_ref']);rt.close()
        s['provider_session_ref']='arrived_later'
        self.assertEqual('MANAGED_SESSION_BOOTSTRAP_COMPLETE',bootstrap_managed_session(self.root,s)['status'])
        self.assertEqual([{'frame_ref':'keep'}],self.read(s['session_id'])['task_frame_refs'])
        self.assertEqual('grok-cli:arrived_later',self.read(s['session_id'])['observer_session_ref'])
        self.assertEqual(1,len(self.links('CONDUCTOR')))

    def test_identity_conflict_is_not_rebound(self):
        s=self.session();bootstrap_managed_session(self.root,s)
        s['session_anchor_ref']='wrong'
        self.assertEqual('MANAGED_SESSION_IDENTITY_CONFLICT',bootstrap_managed_session(self.root,s)['status'])
        self.assertEqual('anchor_session_test',self.read(s['session_id'])['anchor_id'])

    def test_existing_executable_attachment_is_preserved(self):
        s=self.session()
        r=bootstrap_managed_session(self.root,s)
        from reference_runtime.anchor_session_memory_runtime import AnchorSessionMemoryRuntime
        with closing(AnchorSessionMemoryRuntime(database_path=Path(r['session_sql_path']))) as rt:
            stored=rt.stored_snapshot()
            snap=dict(stored['snapshot'])
            snap['observed_at']='2099-01-01T00:00:00.123456Z'
            snap['executable_runtime_currentness']={'status':'CURRENT'}
            snap['authority']='UNASSIGNED'
            rt.record_snapshot(snapshot=snap,source_ref=stored['source_ref'])
        r=bootstrap_managed_session(self.root,s)
        self.assertEqual('MANAGED_SESSION_BOOTSTRAP_COMPLETE',r['status'],r)
        snap=self.read(s['session_id'])
        self.assertEqual({'status':'CURRENT'},snap['executable_runtime_currentness'])
        self.assertEqual('UNASSIGNED',snap['authority'])
        self.assertEqual('2099-01-01T00:00:00Z',snap['observed_at'])

    def test_unknown_mode_does_not_create_session_db(self):
        r=bootstrap_managed_session(self.root,self.session(mode='NOT_REGISTERED'))
        self.assertEqual('MANAGED_SESSION_BOOTSTRAP_FAILED',r['status'])
        self.assertFalse((self.root/'.ai/runtime/session_store').exists())

    def test_mode_change_can_open_managed_session_and_keeps_old_link(self):
        s=self.session();bootstrap_managed_session(self.root,s)
        spec=importlib.util.spec_from_file_location('mode_change_test',ROOT/'.ai/skills/common/mode-change/scripts/mode_change_session.py')
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        r=mod.apply(self.root,{'session_id':s['session_id'],'mode':'CONDUCTOR'})
        self.assertEqual('MODE_CHANGE_SESSION_ANCHOR_UPDATED',r['status'],r)
        self.assertEqual(1,len(self.links('CONDUCTOR')))

    def test_real_hook_success_and_missing_server_identity_are_distinct(self):
        args=build_parser().parse_args(['--repo-root',str(self.root),'--project-id','universe','--provider','GROK','--session-ref','vendor_test','--trigger','session_start'])
        env={'UNIVERSE_SUPERVISOR_SESSION_ID':'session_test','UNIVERSE_MODE':'CONDUCTOR'}
        response={'status':'SESSION_REF_INJECTED','supervisor_session':self.session()}
        with mock.patch('universe_session_inject_hook.load_server_connection',return_value=('http://local','test',None)), mock.patch('universe_session_inject_hook.endpoint_reachable',return_value=True), mock.patch('universe_session_inject_hook.post_inject',return_value=(200,response,None)):
            r=run_hook(args,environment=env)
            self.assertEqual('INJECTED',r['status'],r)
            self.assertEqual(1,len(self.links('CONDUCTOR')))
            response.pop('supervisor_session')
            r=run_hook(args,environment=env)
            self.assertEqual('RUNTIME_SESSION_BOOTSTRAP_FAILED',r['status'])


if __name__ == '__main__': unittest.main()
