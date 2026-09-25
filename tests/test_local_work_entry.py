"""Exercise installed local receipt entry without a service or live state writes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.ai/runtime'))
from reference_runtime.mode_registry_runtime import load_mode_registry
from reference_runtime.project_runtime_store import ProjectRuntimeStore


class LocalWorkEntryTests(unittest.TestCase):
    def test_local_cli_activates_receipt_without_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tools').mkdir()
            registry = load_mode_registry(ROOT)  # Release seed for isolated fixture only.
            payload = registry.as_dict()
            hashed = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            store = ProjectRuntimeStore(root)
            try:
                store.prepare_mode_current_anchor(
                    project_id='fixture', registry_payload=payload,
                    registry_revision=registry.revision, registry_digest=hashed(payload),
                    registry_source_ref='fixture://registry', mode='MASTER',
                    mode_definition_digest=hashed(registry.resolve('MASTER').as_dict()),
                    source_ref='fixture://instruction', host_session_ref='fixture-session',
                    observed_at='2026-09-25T00:00:00Z')
            finally:
                store.close()
            request = {'session_id': 'fixture-session', 'mode': 'MASTER', 'source_commit': 'a' * 40,
                       'work': {'scope_kind': 'LOCAL_INSTRUCTION_WORK',
                                'instruction_id': 'fixture-instruction',
                                'write_roots': [str(root / 'tools')],
                                'write_operations': ['CREATE', 'MODIFY'],
                                'boundary': 'isolated fixture tools',
                                'task_summary': 'verify local receipt route',
                                'instruction_ref': 'fixture://instruction'}}
            result = subprocess.run(
                [sys.executable, str(ROOT / '.ai/runtime/reference_runtime/cli.py'),
                 'execution-binding', 'begin-local-work', '--repo-root', str(root), '--request', '-'],
                input=json.dumps(request), encoding='utf-8', capture_output=True,
                shell=False, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual('WORK_RECEIPT_ACTIVATED', json.loads(result.stdout)['status'])
            self.assertTrue((root / '.ai/runtime/state/anchor_work/fixture-session.json').exists())
