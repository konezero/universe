from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from universe_project_drafts import ProjectDrafts, DraftError, FIELDS


class ProjectDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'drafts.sqlite3'
        @contextmanager
        def connection():
            c = sqlite3.connect(self.database)
            try:
                with c:
                    yield c
            finally:
                c.close()
        self.drafts = ProjectDrafts(connection)
        self.request = {'draft_id': 'draft_test', 'project_id': None, 'expected_revision': 0, 'request_id': 'save_1', 'fields': dict.fromkeys(FIELDS, '')}
        self.request['fields'].update(title='Clinical pathway', domain='healthcare', goal='Reduce missed follow-ups')

    def test_read_save_reopen_and_exact_replay(self):
        self.assertEqual(0, self.drafts.read('draft_test')['revision'])
        one = self.drafts.save(self.request, {'kind': 'USER'})
        self.assertEqual(one, self.drafts.save(self.request, {'kind': 'USER'}))
        self.assertEqual(one, self.drafts.read('draft_test'))
        self.assertEqual([one], self.drafts.list())
        with self.drafts.connection() as c:
            self.assertEqual(1, c.execute('SELECT COUNT(*) FROM project_authoring_revision').fetchone()[0])

    def test_concurrent_edit_and_replay_conflicts(self):
        self.drafts.save(self.request, {})
        changed = {**self.request, 'fields': {**self.request['fields'], 'goal': 'different'}}
        with self.assertRaisesRegex(DraftError, 'different content'):
            self.drafts.save(changed, {})
        with self.assertRaisesRegex(DraftError, 'another editor'):
            self.drafts.save({**changed, 'request_id': 'save_2'}, {})
        second = self.drafts.save({**changed, 'request_id': 'save_2', 'expected_revision': 1}, {})
        self.assertEqual(2, second['revision'])
        self.assertEqual('different', second['fields']['goal'])
        self.assertEqual(1, self.drafts.save(self.request, {})['revision'])
        self.assertEqual(2, self.drafts.read('draft_test')['revision'])

    def test_scope_is_immutable_and_history_is_retained(self):
        self.drafts.save({**self.request, 'project_id': 'TEST'}, {})
        with self.assertRaisesRegex(DraftError, 'another project'):
            self.drafts.save({**self.request, 'expected_revision': 1, 'request_id': 'save_2'}, {})
        self.assertEqual(1, len(self.drafts.list('TEST')))
        self.assertEqual([], self.drafts.list('OTHER'))

    def test_invalid_fields_and_revision_do_not_create_a_record(self):
        for request in ({**self.request, 'expected_revision': True}, {**self.request, 'fields': {'goal': 'incomplete'}}, {**self.request, 'fields': {**self.request['fields'], 'goal': 1}}, {**self.request, 'draft_id': '../escape'}):
            with self.assertRaises(DraftError):
                self.drafts.save(request, {})
        self.assertEqual([], self.drafts.list())


    def _accepted_request(self, **overrides):
        value = {
            'draft_id': 'draft_test',
            'revision': 1,
            'request_id': 'register_1',
            'idempotency_key': 'register_key_1',
            'project_id': 'TEST',
            'project_root': 'C:/projects/TEST',
            'accepted_fields': ['title', 'goal'],
        }
        value.update(overrides)
        return value

    def test_register_replays_by_idempotency_request_and_revision(self):
        self.drafts.save(self.request, {'kind': 'USER'})
        calls = []
        def materialize(draft, request, actor):
            calls.append((draft['draft_id'], draft['revision']))
            return {'status': 'PROJECT_DRAFT_REGISTERED', 'project': {'project_id': request['project_id']}}

        first = self.drafts.accept(self._accepted_request(), {'kind': 'USER'}, materialize)
        self.assertEqual(first, self.drafts.accept(self._accepted_request(), {'kind': 'USER'}, materialize))
        self.assertEqual(first, self.drafts.accept(self._accepted_request(request_id='register_2', idempotency_key='register_key_2'), {'kind': 'USER'}, materialize))
        self.assertEqual([('draft_test', 1)], calls)
        with self.assertRaisesRegex(DraftError, 'different content'):
            self.drafts.accept(self._accepted_request(project_root='C:/other'), {'kind': 'USER'}, materialize)

    def test_register_retry_recovers_after_materialization_failure(self):
        self.drafts.save(self.request, {'kind': 'USER'})
        calls = []
        def materialize(draft, request, actor):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('simulated partial failure')
            return {'status': 'PROJECT_DRAFT_REGISTERED', 'project': {'project_id': request['project_id']}}

        with self.assertRaisesRegex(DraftError, 'simulated partial failure'):
            self.drafts.accept(self._accepted_request(), {'kind': 'USER'}, materialize)
        result = self.drafts.accept(self._accepted_request(), {'kind': 'USER'}, materialize)
        self.assertEqual('PROJECT_DRAFT_REGISTERED', result['status'])
        self.assertEqual([1, 1], calls)
        with self.drafts.connection() as connection:
            self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM project_draft_acceptance').fetchone()[0])


class ProjectDraftApiTests(unittest.TestCase):
    def test_resident_provider_prompts_receive_the_shared_snapshot(self):
        from project_master_host import CodexProjectMasterRuntime, GrokProjectMasterRuntime
        message = {'message_id': 'm1', 'kind': 'QUESTION', 'sender': 'USER', 'body': 'Review the plan', 'ui_context': {'project_draft': {'draft_id': 'draft_test', 'revision': 4, 'fields': {'goal': 'Shared goal'}}, 'project_draft_actions': {'save': 'project.draft.save'}}}
        for prompt in (CodexProjectMasterRuntime._prompt(message), GrokProjectMasterRuntime._prompt(None, message)):
            self.assertIn('Shared goal', prompt)
            self.assertIn('project.draft.save', prompt)
            self.assertIn('not execution authority', prompt)

    def test_actions_and_server_resolved_conversation_snapshot(self):
        from test_memory_candidates_and_delegations import MemoryCandidateApiTests
        from universe_server import normalize_conductor_ui_context, UniverseError
        fixture = MemoryCandidateApiTests()
        fixture.setUp()
        try:
            for attribute in ("service_state_path", "remote_gateway_state_path", "remote_connector_state_path", "remote_connector_config_path"):
                getattr(fixture.server, attribute).relative_to(Path(fixture.temp.name))
            def action(name, request):
                return fixture.request('POST', '/v1/actions', {'action_id': 'project.draft.' + name, 'request': request})
            request = {'draft_id': 'draft_shared', 'project_id': 'TEST', 'expected_revision': 0, 'request_id': 'create_1', 'fields': dict.fromkeys(FIELDS, '')}
            request['fields']['goal'] = 'User-defined goal'
            status, result = action('save', request)
            self.assertEqual(200, status, result)
            self.assertEqual('USER', result['draft']['actor']['kind'])
            self.assertEqual(409, action('save', {**request, 'request_id': 'stale'})[0])
            self.assertEqual(403, action('save', {**request, 'actor': {'kind': 'USER'}})[0])
            self.assertEqual(404, action('save', {**request, 'project_id': 'MISSING', 'request_id': 'wrong'})[0])
            # Save an accepted provider edit through the shared Action.
            edited = {**request, 'expected_revision': 1, 'request_id': 'provider_edit_1', 'fields': {**request['fields'], 'goal': 'Provider-edited goal'}}
            status, result = action('save', edited)
            self.assertEqual(200, status, result)
            self.assertEqual(2, result['draft']['revision'])
            context = normalize_conductor_ui_context({'project_draft_id': 'draft_shared'})
            resolved = fixture.server._project_draft_context(context)
            self.assertEqual('Provider-edited goal', resolved['project_draft']['fields']['goal'])
            self.assertEqual(2, resolved['project_draft']['revision'])
            self.assertEqual('project.draft.save', resolved['project_draft_actions']['save'])
            with self.assertRaises(UniverseError):
                normalize_conductor_ui_context({'project_draft_id': 'draft_shared', 'project_draft': {'revision': 999}})
        finally:
            fixture.tearDown()


    def test_register_action_replays_and_recovers_after_materialization_partial_failure(self):
        from test_memory_candidates_and_delegations import MemoryCandidateApiTests
        fixture = MemoryCandidateApiTests()
        fixture.setUp()
        try:
            def action(name, request):
                return fixture.request('POST', '/v1/actions', {'action_id': 'project.draft.' + name, 'request': request})

            def save_draft(draft_id, request_id, title):
                fields = dict.fromkeys(FIELDS, '')
                fields.update(title=title, goal='Create a project from the accepted draft')
                status, result = action('save', {'draft_id': draft_id, 'project_id': None, 'expected_revision': 0, 'request_id': request_id, 'fields': fields})
                self.assertEqual(200, status, result)
                return fields

            root = Path(fixture.temp.name)
            registered_root = root / 'draft-registered'
            registered_root.mkdir()
            (registered_root / 'REPOSITORY_MANIFEST.md').write_text('# DRAFT_REGISTER_ACTION\n', encoding='utf-8')
            save_draft('draft_register_action', 'save_register_action', 'Registered draft')
            registration = {'draft_id': 'draft_register_action', 'revision': 1, 'request_id': 'register_action_1', 'idempotency_key': 'register_action_1', 'project_id': 'DRAFT_REGISTER_ACTION', 'project_root': str(registered_root), 'accepted_fields': list(FIELDS)}
            status, result = action('register', registration)
            self.assertEqual(200, status, result)
            self.assertEqual('PROJECT_DRAFT_REGISTERED', result['status'])
            self.assertTrue(result['created'])
            self.assertEqual('draft_register_action', result['project']['metadata']['accepted_draft_id'])
            status, replay = action('register', registration)
            self.assertEqual(200, status, replay)
            self.assertEqual(result, replay)

            partial_root = root / 'draft-partial'
            partial_root.mkdir()
            (partial_root / 'REPOSITORY_MANIFEST.md').write_text('# DRAFT_PARTIAL_ACTION\n', encoding='utf-8')
            save_draft('draft_partial_action', 'save_partial_action', 'Partially materialized draft')
            partial = {'draft_id': 'draft_partial_action', 'revision': 1, 'request_id': 'register_partial_1', 'idempotency_key': 'register_partial_1', 'project_id': 'DRAFT_PARTIAL_ACTION', 'project_root': str(partial_root), 'accepted_fields': list(FIELDS)}
            original_register = fixture.server.store.register_project
            materialization_attempts = 0

            def fail_once_after_materialization(value):
                nonlocal materialization_attempts
                project, created = original_register(value)
                materialization_attempts += 1
                if materialization_attempts == 1:
                    raise RuntimeError('simulated response loss after project materialization')
                return project, created

            fixture.server.store.register_project = fail_once_after_materialization
            status, failure = action('register', partial)
            self.assertEqual(409, status, failure)
            self.assertEqual('PROJECT_DRAFT_MATERIALIZATION_FAILED', failure.get('error_code'))
            status, recovered = action('register', partial)
            self.assertEqual(200, status, recovered)
            self.assertEqual('PROJECT_DRAFT_REPLAYED', recovered['status'])
            self.assertFalse(recovered['created'])
            status, replay = action('register', partial)
            self.assertEqual(200, status, replay)
            self.assertEqual(recovered, replay)
            self.assertEqual(2, materialization_attempts)
        finally:
            fixture.tearDown()

    def test_registration_ui_payload_matches_project_draft_register_contract(self):
        source = (Path(__file__).resolve().parents[1] / 'tools' / 'universe_ui' / 'app.js').read_text(encoding='utf-8')
        function_start = source.index('async function registerAcceptedProjectDraft()')
        request_start = source.index('const request = {', function_start)
        request_end = source.index('\n    };', request_start) + len('\n    };')
        request = source[request_start:request_end]
        self.assertIn('draft_id: current.draft_id', request)
        self.assertIn('revision: Number(current.revision)', request)
        self.assertIn('request_id: requestId', request)
        self.assertIn('idempotency_key: requestId', request)
        self.assertIn('project_id: projectId', request)
        self.assertIn('project_root: projectRoot', request)
        self.assertIn('accepted_fields: Object.keys(current.fields)', request)
        self.assertNotIn('expected_revision', request)
        self.assertNotIn('release_id', request)


if __name__ == '__main__':
    unittest.main()
