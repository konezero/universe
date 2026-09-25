import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from todo_execution_journal import append, journal_path, records, read_reference, JournalError
from todo_journal_handoff import handoff


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = journal_path(self.root, 'todo_1')
        self.old = dict(todo_id='todo_1', owner_ref='master_old', run_id='run_old', task_frame_id='frame_old')
        self.pin = append(self.path, **self.old, event_id='launch', kind='ASSIGNED', payload={})
        self.before = self.path.read_bytes()
        common = dict(project_id='project', node_ref='node', state='WAITING', revision=10)
        self.runs = {'run_old': dict(common, session_anchor_ref='master_old'),
                     'run_new': dict(common, session_anchor_ref='master_new')}
        self.server = SimpleNamespace(
            persona_automation=SimpleNamespace(get_run=lambda key: self.runs[key]),
            store=SimpleNamespace(get_todo=lambda key: {}, get_project=lambda key: {'project_root': str(self.root)}),
            _validate_persona_automation_anchor=lambda project, owner: None,
            _persona_todo_in_run_scope=lambda run, todo: True)
        self.status = Mock(return_value={'known': True, 'alive': False, 'phase': 'EXITED'})
        self.request = dict(run_id='run_new', todo_id='todo_1', owner_ref='master_new',
                            previous_owner_ref='master_old', expected_revision=10,
                            expected_sequence=1, expected_digest=records(self.path)[-1][0]['record_digest'],
                            request_id='handoff_1')

    def call(self, **changes):
        return handoff(self.server, {**self.request, **changes}, self.root, self.status)

    def test_transfer_replay_and_new_owner_append_preserve_old_pins(self):
        result = self.call()
        self.assertEqual(result, self.call())
        self.assertEqual(2, len(records(self.path)))
        append(self.path, **{**self.old, 'owner_ref': 'master_new', 'run_id': 'run_new'},
               event_id='new-launch', kind='ASSIGNED', payload={})
        self.assertTrue(self.path.read_bytes().startswith(self.before))
        self.assertEqual('master_old', read_reference(self.pin, project_root=self.root)['owner_ref'])
        with self.assertRaises(JournalError):
            append(self.path, **self.old, event_id='stale', kind='ASSIGNED', payload={})

    def test_tail_conflicts_and_wrong_owner_do_not_write(self):
        for changes in ({'expected_sequence': 2}, {'expected_digest': '0' * 64},
                        {'previous_owner_ref': 'wrong'}, {'owner_ref': 'wrong'},
                        {'expected_revision': 9}):
            with self.subTest(changes=changes), self.assertRaises(JournalError):
                self.call(**changes)
            self.assertEqual(self.before, self.path.read_bytes())

    def test_live_unknown_and_waiting_hosts_block_transfer(self):
        for status in ({'known': True, 'alive': True, 'phase': 'RUNNING_WORKER'},
                       {'known': False}, {'known': True, 'alive': False, 'phase': 'WAITING'}):
            self.status.return_value = status
            with self.subTest(status=status), self.assertRaises(JournalError):
                self.call()
            self.assertEqual(self.before, self.path.read_bytes())

    def test_running_or_cross_scope_source_blocks_transfer(self):
        for changes in ({'state': 'RUNNING'}, {'project_id': 'other'}, {'node_ref': 'other'}):
            original = dict(self.runs['run_old'])
            self.runs['run_old'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(JournalError):
                self.call()
            self.runs['run_old'] = original
            self.assertEqual(self.before, self.path.read_bytes())

    def test_reserved_event_and_replay_conflict_rejected(self):
        with self.assertRaises(JournalError):
            append(self.path, **self.old, event_id='fake', kind='OWNER_TRANSFERRED', payload={})
        self.call()
        before = self.path.read_bytes()
        with self.assertRaises(JournalError):
            self.call(expected_digest='0' * 64)
        self.assertEqual(before, self.path.read_bytes())


if __name__ == '__main__':
    unittest.main()
