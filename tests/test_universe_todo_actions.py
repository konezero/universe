from __future__ import annotations
import concurrent.futures
import http.client
import json
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import test_memory_candidates_and_delegations as fixtures
from universe_todo_actions import TodoActions


class TodoActionHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MemoryCandidateApiTests()
        cls.fixture.setUp()
        cls.server = cls.fixture.server
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def todo(self, project_id='TEST'):
        type(self).count += 1
        return self.server.store.create_todo({'scope_kind': 'UNIVERSE' if project_id is None else 'PROJECT',
            'project_id': project_id, 'title': f'Todo command test {self.count}', 'detail': '',
            'priority': 'P1', 'state': 'BACKLOG', 'source_kind': 'USER', 'sort_order': self.count})

    def request(self, todo, state='IN_PROGRESS', **changes):
        return {'todo_id': todo['todo_id'], 'project_id': todo['project_id'],
                'expected_revision': todo['revision'], 'request_id': 'command-' + todo['todo_id'],
                'state': state, **changes}

    def action(self, action_id, request, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address[:2], timeout=8)
        try:
            conn.request('POST', '/v1/actions', json.dumps({'action_id': action_id, 'request': request}),
                         {'Content-Type': 'application/json', **(headers or {})})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_native_cli_discovers_and_executes_without_session_lookup(self):
        todo = self.todo()
        state_file = Path(self.fixture.temp.name) / 'cli-state.json'
        state_file.write_text(json.dumps({'endpoint': self.fixture.endpoint, 'token': 'fixture-token'}), encoding='utf-8')
        request_file = state_file.with_name('cli-request.json')
        command = [sys.executable, str(ROOT / 'tools' / 'universe_server.py'), 'action', '--state-file', str(state_file)]
        def run(args):
            process = subprocess.run(command + args, cwd=ROOT, shell=False, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertFalse(process.stderr, process.stderr)
            return process.returncode, json.loads(process.stdout)
        code, catalog = run(['--catalog'])
        self.assertEqual(0, code, catalog)
        self.assertTrue(any(c['action_id'] == 'todo.state' for c in catalog['registry']['contracts']))
        request = self.request(todo)
        request_file.write_text(json.dumps({'action_id': 'todo.state', 'request': request}), encoding='utf-8-sig')
        code, result = run(['--request-file', str(request_file)])
        self.assertEqual(0, code, result)
        self.assertEqual('IN_PROGRESS', result['todo']['state'])
        request['request_id'] += '-stale'
        request_file.write_text(json.dumps({'action_id': 'todo.state', 'request': request}), encoding='utf-8')
        code, result = run(['--request-file', str(request_file)])
        self.assertNotEqual(0, code)
        self.assertEqual('TODO_REVISION_CONFLICT', result['error_code'])

    def test_catalog_has_executable_input_schema_and_no_private_session_fields(self):
        report = self.server.action_registry.coverage_report()
        for action_id in ('todo.read', 'todo.list', 'todo.state'):
            contract = next(c for c in report['contracts'] if c['action_id'] == action_id)
            schema = contract['metadata']['request_schema']
            self.assertFalse(schema['additionalProperties'])
            self.assertNotIn('provider_session_ref', schema['properties'])
        self.assertIn('validation', schema['properties'])

    def test_no_session_discovery_for_read_list_and_state(self):
        todo = self.todo()
        with mock.patch.object(self.server.session_supervisor, 'list_sessions', side_effect=AssertionError('private session lookup')):
            status, read = self.action('todo.read', {'todo_id': todo['todo_id']})
            self.assertEqual(200, status, read)
            self.assertEqual(todo['revision'], read['todo']['revision'])
            status, result = self.action('todo.state', self.request(todo))
            self.assertEqual(200, status, result)
            self.assertEqual('IN_PROGRESS', result['todo']['state'])
        status, listing = self.action('todo.list', {'project_id': 'TEST', 'limit': 1})
        self.assertEqual(200, status, listing)
        self.assertEqual(1, len(listing['todos']))
        self.assertTrue(all(r['project_id'] == 'TEST' for r in listing['todos']))
        self.assertEqual([], self.action('todo.list', {'project_id': 'TEST', 'offset': 10000})[1]['todos'])

    def test_completion_evidence_and_caller_fields_are_validated_before_writes(self):
        todo = self.todo()
        for changes, code in [({}, 'TODO_COMPLETION_VALIDATION_REQUIRED'),
            ({'provider_session_ref': 'claimed'}, 'TODO_ACTION_REQUEST_INVALID'),
            ({'actor': {'kind': 'USER'}}, 'ACTION_CALLER_CONTEXT_FORBIDDEN'),
            ({'expected_revision': True}, 'TODO_ACTION_REQUEST_INVALID')]:
            status, result = self.action('todo.state', self.request(todo, 'DONE', **changes))
            self.assertGreaterEqual(status, 400, result)
            self.assertEqual(code, result['error_code'])
        self.assertEqual(todo, self.server.store.get_todo(todo['todo_id']))
        valid = self.request(todo, 'DONE', validation={'status': 'PASSED', 'evidence_ref': 'test://verified-result'})
        status, result = self.action('todo.state', valid)
        self.assertEqual(200, status, result)
        self.assertEqual('DONE', result['todo']['state'])
        self.assertEqual('RECORDED', result['result_propagation']['status'])
        self.assertFalse(result['task_frame_created'])

    def test_replay_conflict_revision_and_restart_preserve_one_transition(self):
        todo = self.todo(); request = self.request(todo)
        status, result = self.action('todo.state', request)
        self.assertEqual(200, status, result)
        self.server.todo_actions = TodoActions(self.server.store)
        status, replay = self.action('todo.state', request)
        self.assertEqual(200, status, replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(result['todo'], replay['todo'])
        status, changed = self.action('todo.state', {**request, 'state': 'READY'})
        self.assertEqual((409, 'TODO_STATE_REQUEST_CONFLICT'), (status, changed['error_code']))
        status, stale = self.action('todo.state', {**request, 'request_id': request['request_id'] + '-new'})
        self.assertEqual((409, 'TODO_REVISION_CONFLICT'), (status, stale['error_code']))
        self.assertEqual(todo['revision'] + 1, self.server.store.get_todo(todo['todo_id'])['revision'])

    def test_scope_wrong_missing_and_schema_errors(self):
        todo = self.todo()
        status, result = self.action('todo.state', self.request(todo, project_id='ANOTHER'))
        self.assertEqual((409, 'TODO_SCOPE_CONFLICT'), (status, result['error_code']))
        self.assertEqual(404, self.action('todo.read', {'todo_id': 'missing-todo'})[0])
        for request in ({}, {'project_id': 'TEST', 'limit': True}, {'project_id': 'TEST', 'include_done': 'true'}):
            self.assertEqual(400, self.action('todo.list', request)[0])

    def test_concurrent_equal_and_different_commands(self):
        todo = self.todo(); request = self.request(todo)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.action('todo.state', request), range(2)))
        self.assertEqual([200, 200], [r[0] for r in results])
        self.assertEqual([False, True], sorted(r[1]['replayed'] for r in results))
        todo = self.todo(); request = self.request(todo)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda n: self.action('todo.state', {**request, 'request_id': request['request_id'] + str(n)}), range(2)))
        self.assertEqual([200, 409], sorted(r[0] for r in results))

    def test_ledger_failure_rolls_back_state_and_project_event(self):
        todo = self.todo()
        with self.server.store._connection() as connection:
            connection.execute("CREATE TRIGGER reject_todo_state BEFORE INSERT ON todo_state_action BEGIN SELECT RAISE(ABORT, 'fixture ledger failure'); END")
        try:
            self.assertEqual(500, self.action('todo.state', self.request(todo))[0])
            self.assertEqual(todo, self.server.store.get_todo(todo['todo_id']))
        finally:
            with self.server.store._connection() as connection:
                connection.execute('DROP TRIGGER reject_todo_state')

    def test_fanout_failure_preserves_applied_state_and_replay_recovers(self):
        todo = self.todo(); request = self.request(todo, 'DONE', validation={'status': 'PASSED', 'evidence_ref': 'test://result'})
        with mock.patch.object(self.server.store, 'record_todo_result_fanout', side_effect=sqlite3.OperationalError('fixture offline')):
            status, result = self.action('todo.state', request)
        self.assertEqual(200, status, result)
        self.assertEqual('PENDING', result['result_propagation']['status'])
        status, replay = self.action('todo.state', request)
        self.assertEqual(200, status, replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual('RECORDED', replay['result_propagation']['status'])
        self.assertEqual(result['todo']['revision'], replay['todo']['revision'])

    def test_reopen_backlog_and_universe_scoped_todos(self):
        todo = self.todo(None)
        for n, state in enumerate(('DONE', 'READY', 'BACKLOG')):
            request = self.request(todo, state, request_id='universe-state-command-' + str(n))
            if state == 'DONE':request['validation'] = {'status': 'PASSED', 'evidence_ref': 'test://manual-review'}
            status, result = self.action('todo.state', request)
            self.assertEqual(200, status, result)
            self.assertEqual(state, result['todo']['state'])
            todo = result['todo']
        self.assertTrue(any(r['todo_id'] == todo['todo_id'] for r in self.action('todo.list', {'project_id': None})[1]['todos']))

    def test_provider_bridge_is_not_relabelled_as_operator_and_plain_patch_cannot_change_state(self):
        todo = self.todo()
        for action_id in ('todo.state', ' todo.state', 'todo.state ', ' todo.state '):
            status, result = self.action(action_id, self.request(todo), {'X-Universe-Provider-Session': 'claimed'})
            self.assertEqual((403, 'TODO_OPERATOR_REQUIRED'), (status, result['error_code']))
        body = {k:todo[k] for k in ('scope_kind','project_id','title','detail','priority','state','source_kind','sort_order','revision')}
        body['state'] = 'DONE'
        status, result = self.fixture.request('PATCH', '/v1/todos/' + todo['todo_id'], body)
        self.assertEqual((400, 'ACTION_TODO_LIFECYCLE_VIA_RECEIPT'), (status, result['error_code']))
        self.assertEqual(todo, self.server.store.get_todo(todo['todo_id']))


if __name__ == '__main__':
    unittest.main()
