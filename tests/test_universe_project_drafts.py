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
            context = normalize_conductor_ui_context({'project_draft_id': 'draft_shared'})
            resolved = fixture.server._project_draft_context(context)
            self.assertEqual('User-defined goal', resolved['project_draft']['fields']['goal'])
            self.assertEqual(1, resolved['project_draft']['revision'])
            self.assertEqual('project.draft.save', resolved['project_draft_actions']['save'])
            with self.assertRaises(UniverseError):
                normalize_conductor_ui_context({'project_draft_id': 'draft_shared', 'project_draft': {'revision': 999}})
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
