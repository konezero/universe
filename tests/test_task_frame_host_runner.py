"""Only the Master writes journal context; roles get a pinned read reference."""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from task_frame_host_runner import RuntimeHostRoleRunner, _failure_evidence_database
from universe_runtime_host import RuntimeHostError
from todo_execution_journal import append, append_directive_assignment, journal_path, read_reference

GOOD = {'structured_result': {'outcome': 'DONE', 'result_text': 'changed a.py', 'evidence_refs': [], 'validation_state': 'UNVERIFIED'}, 'result_receipt_ref': 'receipt-1'}

class FakeRuntimeHost:
    def __init__(self, result=GOOD, error=None):
        self.result, self.error, self.calls = result, error, []
    def invoke_structured_task(self, **kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        return self.result

class RoleRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = journal_path(self.root, 'todo_1')
        self.identity = dict(todo_id='todo_1', owner_ref='master_1', run_id='run_1', task_frame_id='tf1')
        scope = {'repository_write_scope': 'BOUNDED', 'mutation_scope': {'operations': ['MODIFY'], 'targets': [str(self.root / 'a.py')]}}
        self.ref = append(self.path, **self.identity, event_id='launch', kind='ASSIGNED', payload={
            'role': 'WORKER', 'attempt': 1, 'todo': {'title': 'Fix it', 'detail': 'the detail'},
            'persona': 'careful', 'worker_write_scope': scope, 'feedback': None, 'collected_results': {}})
        self.spec = {'repository_root': str(ROOT), 'project_root': str(self.root),
                     'task_frame_id': 'tf1', 'run_id': 'run_1', 'todo_id': 'todo_1', 'provider': 'CODEX',
                     'runtime_binding': {'endpoint': 'http://127.0.0.1:1'},
                     'worker_write_scope': scope, 'todo_journal': self.ref}

    def test_worker_gets_reference_not_bulk_context_and_cannot_write_journal(self):
        host = FakeRuntimeHost(); before = self.path.read_bytes()
        result = RuntimeHostRoleRunner(self.spec, runtime_host=host).run('WORKER', attempt=1, feedback=None)
        call = host.calls[0]; context = call['context_pack']
        self.assertEqual(self.ref, context['journal_assignment'])
        import base64, json
        self.assertIn('--reference-base64', context['journal_read_argv'])
        self.assertEqual(self.ref, json.loads(base64.b64decode(context['journal_read_argv'][-1])))
        self.assertEqual(sys.executable, context['journal_read_argv'][0])
        self.assertTrue(Path(context['journal_read_argv'][0]).is_file())
        self.assertNotIn('.venv_win', context['journal_read_argv'][0])
        for key in ('todo', 'persona', 'master_feedback', 'worker_result'): self.assertNotIn(key, context)
        self.assertEqual('BOUNDED', call['repository_write_scope'])
        self.assertEqual(self.spec['worker_write_scope']['mutation_scope'], call['mutation_scope'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual('COMPLETED', result.status)
        self.assertEqual('task-frame-result://tf1_worker_1/worker-turn/receipt-1', result.result_ref)
        self.assertEqual(64, len(result.result_digest))

    def test_reviewer_reads_master_collected_worker_result_after_runner_restart(self):
        append(self.path, **self.identity, event_id='collect', kind='RESULT_COLLECTED',
               payload={'role': 'WORKER', 'attempt': 1, 'status': 'COMPLETED',
                        'result_ref': 'worker-result-1', 'result_digest': 'worker-digest-1',
                        'result': GOOD['structured_result']})
        ref = append_directive_assignment(self.path, frame_id='tf1', request_id='review', directive='RUN_ROLE', role='REVIEWER', feedback=None)
        host = FakeRuntimeHost()
        result = RuntimeHostRoleRunner(self.spec, runtime_host=host).run('REVIEWER', attempt=1, feedback=None)
        self.assertEqual('COMPLETED', result.status)
        self.assertEqual('NONE', host.calls[0]['repository_write_scope'])
        self.assertEqual(ref, host.calls[0]['context_pack']['journal_assignment'])
        self.assertEqual('changed a.py', read_reference(ref, project_root=self.root)['payload']['collected_results']['WORKER']['result']['result_text'])

    def test_rework_has_its_own_attempt_and_pinned_feedback(self):
        append_directive_assignment(self.path, frame_id='tf1', request_id='rework', directive='REWORK', role='WORKER', feedback='handle empty input')
        host = FakeRuntimeHost(); runner = RuntimeHostRoleRunner(self.spec, runtime_host=host)
        self.assertEqual('COMPLETED', runner.run('WORKER', attempt=2, feedback='handle empty input').status)
        self.assertEqual('tf1_worker_2', host.calls[0]['frame_id'])
        self.assertEqual('FAILED', runner.run('WORKER', attempt=2, feedback='wrong').status)
        self.assertEqual(1, len(host.calls))

    def test_binding_failure_never_falls_back_to_launch_credentials(self):
        host = FakeRuntimeHost(); fresh = {'endpoint': 'http://127.0.0.1:2', 'token': 'new'}
        runner = RuntimeHostRoleRunner(self.spec, runtime_host=host, binding_provider=lambda: fresh)
        runner.run('WORKER', attempt=1, feedback=None)
        self.assertEqual(fresh, host.calls[0]['runtime_binding'])
        def down(): raise ConnectionError('server restarting')
        for provider in (down, lambda: {'endpoint': 'incomplete'}):
            host = FakeRuntimeHost()
            result = RuntimeHostRoleRunner(self.spec, runtime_host=host, binding_provider=provider).run('WORKER', attempt=1, feedback=None)
            self.assertEqual('TASK_FRAME_BINDING_UNAVAILABLE', result.error_code)
            self.assertEqual([], host.calls)

    def test_missing_or_wrong_journal_blocks_before_provider(self):
        for spec in ({**self.spec, 'todo_journal': None}, {**self.spec, 'run_id': 'wrong'}):
            host = FakeRuntimeHost()
            result = RuntimeHostRoleRunner(spec, runtime_host=host).run('WORKER', attempt=1, feedback=None)
            self.assertEqual('FAILED', result.status); self.assertEqual([], host.calls)

    def test_provider_error_and_bad_result_stay_failed(self):
        for host, code in ((FakeRuntimeHost(error=RuntimeHostError('WORKER_TRANSPORT_FAILED', 'down')), 'WORKER_TRANSPORT_FAILED'),
                           (FakeRuntimeHost(result={}), 'PROVIDER_RESULT_INVALID')):
            result = RuntimeHostRoleRunner(self.spec, runtime_host=host).run('WORKER', attempt=1, feedback=None)
            self.assertEqual(code, result.error_code)

    def test_failure_evidence_store_is_durable_and_explicit(self):
        configured = _failure_evidence_database({'failure_evidence_database': str(self.root / 'failure.sqlite')})
        self.assertEqual(self.root / 'failure.sqlite', configured)

if __name__ == '__main__': unittest.main()
