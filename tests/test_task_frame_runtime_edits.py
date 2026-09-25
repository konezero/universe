"""Exercise the actual installed Runtime and file gateway; only the LLM is fake."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from tests import test_universe_task_frame_skill_observation as fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from reference_runtime.anchor_session_memory_adapter import AnchorSessionMemoryHostAdapter
from reference_runtime.task_frame_runtime import build_task_frame_instruction_proposal
from universe_runtime_host import UniverseRuntimeHost, INSTRUCTION_PROFILE
from universe_runtime_worker_dispatch import RuntimeWorkerDispatcher


class TaskFrameRuntimeEditTests(unittest.TestCase):
    def setUp(self):
        fixture.UniverseTaskFrameSkillObservationTests.setUp(self)
        manifest_path = self.target / '.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        for relative in (str(INSTRUCTION_PROFILE), '.ai/core/INSTRUCTION_WORK_RECEIPT_CONTRACT.md',
                         '.ai/core/PRE_EXECUTION_VERIFICATION.md'):
            destination = self.target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
            manifest['managed_paths'].append({
                'class': 'runtime', 'target_path': Path(relative).as_posix(),
                'local_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            })
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        shutil.copyfile(ROOT / '.ai/runtime/project_instance/UNIVERSE_RELEASE_INSTALL.json',
                        manifest_path.parent / 'UNIVERSE_RELEASE_INSTALL.json')
        registry_path = manifest_path.parent / 'mode_registry.json'
        shutil.copyfile(ROOT / '.ai/runtime/project_instance/mode_registry.json', registry_path)
        self.path = self.target / 'example.txt'
        self.path.write_text('before\n', encoding='utf-8')
        self.adapter = AnchorSessionMemoryHostAdapter(repository_root=self.target)
        self.addCleanup(self.adapter.close)
        activated = self.adapter.activate({
            'session_id': 'session', 'anchor_mode': 'MASTER', 'source_ref': 'test://parent',
            'snapshot': {'session_id': 'session', 'frame_id': 'current', 'anchor_id': 'anchor',
                         'state': 'READY', 'source_commit': 'a' * 40,
                         'observed_at': '2026-09-23T00:00:00Z',
                         'executable_runtime_currentness': {'status': 'CURRENT'},
                         'coordinates': {key: 'test-host' for key in (
                             'session_location', 'commander_surface', 'execution_surface', 'repository_location')},
                         'evidence_refs': {'validation': 'test://validation'}},
        })
        self.assertEqual('HOST_SESSION_MEMORY_ACTIVATED', activated.get('status'), activated)
        self.calls = []
        self.dispatcher = RuntimeWorkerDispatcher(self.target, post=self.post)
        self.host = UniverseRuntimeHost(self.target, worker_dispatcher=self.dispatcher)
        self.host._post_runtime = self.post
        self.host._build_task_frame_proposal = lambda plan, **kw: build_task_frame_instruction_proposal(plan)
        self.capability = {'status': 'AVAILABLE', 'provider': 'CODEX', 'model': 'test-model',
                           'capability_evidence_ref': 'test://provider', 'cli_auto_approve': 'ON'}

    def post(self, endpoint, token, route, payload):
        self.calls.append((route, payload))
        method = {
            '/v1/execution-binding/begin-local-work': self.adapter.begin_local_instruction_work,
            '/v1/task-frame/create': self.adapter.create_task_frame,
            '/v1/task-frame/operation': self.adapter.apply_task_frame_operation,
            '/v1/task-frame/worker-result': self.adapter.accept_task_frame_worker_result,
            '/v1/task-frame/close': self.adapter.close_task_frame,
            '/v1/mutation-gateway/apply-file': self.adapter.apply_file_mutation,
        }[route]
        result = method(payload)
        self.assertNotEqual('UNKNOWN', result.get('status'), result)
        return result

    def run_frame(self, *, replace_binding=False):
        def provider(name, request):
            editor = self.dispatcher._host_editors[request['worker_run_ref']]
            if replace_binding:
                self.adapter.begin_local_instruction_work({'session_id': 'session', 'work': {
                    'scope_kind': 'LOCAL_INSTRUCTION_WORK', 'instruction_id': 'different',
                    'instruction_ref': 'universe://goals/other', 'write_roots': [str(self.target)],
                    'write_operations': ['MODIFY'], 'boundary': 'task-frame:other', 'task_summary': 'other',
                }})
            self.edit_result = editor({
                'operation': 'replace', 'path': str(self.path),
                'expected_sha256': hashlib.sha256(self.path.read_bytes()).hexdigest(),
                'old_text': 'before', 'new_text': 'after',
            }, 'edit-1')
            return {'status': 'COMPLETED', 'worker_run_ref': request['worker_run_ref'],
                    'worker_id': 'provider-worker', 'result_receipt_ref': 'test://provider-result',
                    'result': {'text': json.dumps({'outcome': 'DONE'})},
                    'session_persistence': 'EPHEMERAL', 'persistent_session_ref': 'UNKNOWN',
                    'universe_coordinate_persisted': False}
        with patch.object(self.dispatcher, 'provider_capability', return_value=self.capability), \
             patch.object(self.dispatcher, '_invoke_provider', side_effect=provider):
            return self.host.invoke_structured_task(
                runtime_binding={'endpoint': 'http://127.0.0.1:1', 'token': 'test-only',
                                 'session_id': 'session', 'origin_anchor_ref': 'anchor',
                                 'origin_frame_id': 'current', 'parent_actor_ref': 'parent',
                                 'parent_evidence_ref': 'test://parent'},
                provider='CODEX', invocation_id='goal-task', frame_id='frame', turn_id='worker',
                source_ref='universe://goals/design', instruction='Update the assigned example',
                context_pack={'todo_id': 'todo-design'}, output_contract={'required': ['outcome']},
                repository_write_scope='BOUNDED',
                mutation_scope={'operations': ['MODIFY'], 'targets': [str(self.path)]},
            )

    def test_goal_instruction_reaches_live_gateway_without_local_anchor_receipt(self):
        result = self.run_frame()
        self.assertTrue(result['repository_write'])
        self.assertTrue(self.edit_result['repository_write'], self.edit_result)
        self.assertEqual('after\n', self.path.read_text())
        frame = next(p['frame'] for route, p in self.calls if route == '/v1/task-frame/create')
        self.assertIsNone(frame['task_frame_execution_approval'])
        self.assertNotEqual('UNASSIGNED', frame['execution_assignment_ref'])
        self.assertEqual('universe://goals/design', frame['parent_instruction']['instruction_ref'])
        self.assertFalse((self.target / '.ai/runtime/state/anchor_work').exists())
        self.assertFalse(self.dispatcher.frame_work)

    def test_replaced_runtime_work_binding_rejects_old_worker_edit(self):
        self.run_frame(replace_binding=True)
        self.assertFalse(self.edit_result['repository_write'], self.edit_result)
        self.assertIn('WORK_RECEIPT_BOUNDARY_MISMATCH', self.edit_result['reasons'])
        self.assertEqual('before\n', self.path.read_text())


if __name__ == '__main__':
    unittest.main()
