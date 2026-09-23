from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from todo_execution_journal import (JournalError, append, append_directive_assignment,
    assignment_reference, journal_path, read_reference, records)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = journal_path(self.root, 'todo_1')
        self.identity = dict(todo_id='todo_1', owner_ref='master_1', run_id='run_1', task_frame_id='frame_1')
        self.payload = dict(role='WORKER', attempt=1, todo={'title': 'Task', 'detail': 'Read only'},
                            persona='Careful', worker_write_scope={'repository_write_scope': 'NONE'},
                            feedback=None, collected_results={})

    def start(self):
        return append(self.path, **self.identity, event_id='launch', kind='ASSIGNED', payload=self.payload)

    def test_append_replay_and_pinned_read(self):
        ref = self.start()
        before = self.path.read_bytes()
        self.assertEqual(ref, self.start())
        self.assertEqual(before, self.path.read_bytes())
        append(self.path, **self.identity, event_id='result', kind='RESULT_COLLECTED', payload={'role': 'WORKER', 'result': {'outcome': 'PARTIAL'}})
        self.assertTrue(self.path.read_bytes().startswith(before))
        self.assertEqual(self.payload, read_reference(ref, project_root=self.root)['payload'])
        self.assertEqual(ref, assignment_reference(self.path, 'frame_1', 'WORKER', 1))

    def test_host_pinned_tool_reads_exact_record_and_refuses_substitutions(self):
        from todo_execution_journal import read_assignment_tool
        ref = self.start()
        before = self.path.read_bytes()
        read = lambda args, reference=ref: read_assignment_tool(args,project_root=self.root,reference=reference)
        result = read({})
        self.assertEqual('HOST_JOURNAL_READ',result['status'])
        self.assertEqual(self.payload,result['record']['payload'])
        self.assertFalse(result['repository_write'])
        self.assertEqual('TODO_JOURNAL_ARGUMENTS_DENIED',read({'path':'other'})['error_code'])
        self.assertEqual('TODO_JOURNAL_PATH_INVALID',read({}, {**ref,'path':str(self.root/'other')})['error_code'])
        self.assertEqual('TODO_JOURNAL_REFERENCE_CHANGED',read({}, {**ref,'sha256':'0'*64})['error_code'])
        self.assertEqual(before,self.path.read_bytes())

    def test_conflicting_replay_and_owner_are_rejected_without_write(self):
        self.start(); before = self.path.read_bytes()
        with self.assertRaises(JournalError):
            append(self.path, **self.identity, event_id='launch', kind='ASSIGNED', payload={})
        with self.assertRaises(JournalError):
            append(self.path, **{**self.identity, 'owner_ref': 'other'}, event_id='other', kind='ASSIGNED', payload={})
        self.assertEqual(before, self.path.read_bytes())

    def test_review_requires_collection_then_gets_only_latest_role_results(self):
        self.start()
        args = dict(frame_id='frame_1', request_id='review', directive='RUN_ROLE', role='REVIEWER', feedback=None)
        with self.assertRaises(JournalError) as caught:
            append_directive_assignment(self.path, **args)
        self.assertEqual('TODO_JOURNAL_WORKER_COLLECTION_REQUIRED', caught.exception.code)
        append(self.path, **self.identity, event_id='result', kind='RESULT_COLLECTED',
               payload={'role': 'WORKER', 'result': {'outcome': 'PARTIAL'}})
        ref = append_directive_assignment(self.path, **args)
        self.assertEqual(ref, append_directive_assignment(self.path, **args))
        self.assertEqual(1, read_reference(ref, project_root=self.root)['payload']['attempt'])
        self.assertEqual('PARTIAL', read_reference(ref, project_root=self.root)['payload']['collected_results']['WORKER']['result']['outcome'])
        rework = append_directive_assignment(self.path, frame_id='frame_1', request_id='rework', directive='REWORK', role='WORKER', feedback='fix')
        self.assertEqual(2, read_reference(rework, project_root=self.root)['payload']['attempt'])

    def test_partial_tail_is_preserved_and_blocks_new_assignments(self):
        ref = self.start()
        with self.path.open('ab') as f: f.write(b'{"torn":')
        before = self.path.read_bytes()
        self.assertEqual(self.payload, read_reference(ref, project_root=self.root)['payload'])
        with self.assertRaises(JournalError) as caught: self.start()
        self.assertEqual('TODO_JOURNAL_TAIL_INCOMPLETE', caught.exception.code)
        self.assertEqual(before, self.path.read_bytes())

    def test_path_escape_and_reference_tamper_fail_closed(self):
        with self.assertRaises(JournalError): journal_path(self.root, '../escape')
        ref = self.start()
        for changed in ({'path': str(self.root / 'other')}, {'sha256': '0'*64}, {'length': 0}, {'run_id': 'other'}):
            with self.subTest(changed=changed), self.assertRaises(JournalError):
                read_reference({**ref, **changed}, project_root=self.root)

    def test_resume_context_retains_latest_collected_role_evidence(self):
        from todo_execution_journal import collected_context
        self.start()
        for index in range(3):
            append(self.path, **self.identity, event_id='worker-' + str(index),
                   kind='RESULT_COLLECTED', payload={'role': 'WORKER', 'attempt': index + 1, 'result': {'outcome': 'PARTIAL'}})
        latest = collected_context(self.path)
        self.assertEqual(1, len(latest))
        self.assertEqual(3, latest['WORKER']['attempt'])

    def test_base64_reader_cli_preserves_reference_without_json_shell_quotes(self):
        import base64, subprocess
        ref = self.start()
        encoded = base64.b64encode(json.dumps(ref).encode()).decode('ascii')
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/todo_execution_journal.py'),
             '--root', str(self.root), '--reference-base64', encoded],
            shell=False, capture_output=True, encoding='utf-8', timeout=15, check=True)
        self.assertEqual(self.payload, json.loads(result.stdout)['payload'])

    def test_complete_record_corruption_is_not_skipped(self):
        self.start()
        with self.path.open('ab') as f: f.write(b'{"invalid":1}\n')
        with self.assertRaises(JournalError): records(self.path)


if __name__ == '__main__': unittest.main()
