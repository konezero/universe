from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from universe_service_actions import ServiceActions, ServiceActionError
from universe_action_registry import build_default_action_registry


class ServiceActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'server.json'
        self.previous = {'pid': os.getpid(), 'endpoint': 'http://127.0.0.1:56789', 'database': str(Path(self.temp.name) / 'universe.sqlite3')}
        self.path.write_text(json.dumps(self.previous), encoding='utf-8')
        self.service = ServiceActions(self.path)
        self.request = {'request_id': 'restart-test-1234', 'expected_pid': os.getpid()}

    def accept(self):
        with mock.patch('universe_service_actions.subprocess.Popen') as spawn:
            spawn.return_value.pid = 1234567
            result = self.service.restart(self.request, {'kind': 'USER'})
            args = spawn.call_args.args[0]
            self.assertEqual(str(self.path), args[-2])
            self.assertNotIn('universe_server.py', args)
        return result

    def test_durable_acceptance_replay_and_conflict(self):
        result = self.accept()
        self.assertEqual('ACCEPTED', result['status'])
        with mock.patch('universe_service_actions.subprocess.Popen') as spawn:
            replay = self.service.restart(self.request, {'kind': 'USER'})
            self.assertEqual(result, replay)
            spawn.assert_not_called()
            with self.assertRaisesRegex(ServiceActionError, 'another process'):
                self.service.restart({**self.request, 'expected_pid': 1}, {})
        with mock.patch('universe_service_actions.service_status', return_value=self.previous):
            self.assertEqual(result, ServiceActions(self.path).status({'operation_id': result['operation_id']})['operation'])

    def test_concurrent_different_request_is_rejected(self):
        self.accept()
        with self.assertRaisesRegex(ServiceActionError, 'active'):
            self.service.restart({**self.request, 'request_id': 'different-request'}, {})

    def test_fixed_target_and_stale_instance(self):
        for request in ({**self.request, 'command': 'anything'}, {**self.request, 'expected_pid': True}, {**self.request, 'request_id': '../escape'}):
            with self.assertRaises(ServiceActionError):
                self.service.restart(request, {})
        with self.assertRaisesRegex(ServiceActionError, 'refresh'):
            self.service.restart({**self.request, 'expected_pid': 1}, {})

    def test_spawn_failure_is_queryable(self):
        with mock.patch('universe_service_actions.subprocess.Popen', side_effect=OSError('launch failed')):
            result = self.service.restart(self.request, {})
        self.assertEqual('FAILED', result['status'])
        self.assertEqual('SERVICE_HELPER_START_FAILED', result['error']['code'])

    def test_external_owner_preserves_database_and_confirms_replacement(self):
        self.accept()
        with mock.patch('universe_service_actions.time.sleep'), mock.patch('universe_service_actions.restart_service', return_value={'status': 'READY'}) as restart, mock.patch('universe_service_actions.service_status', return_value={**self.previous, 'status': 'READY', 'pid': 7654321}):
            self.service.run(self.request['request_id'])
            record = self.service.status({'operation_id': self.request['request_id']})['operation']
        self.assertEqual('COMPLETED', record['status'])
        self.assertEqual(Path(self.previous['database']), restart.call_args.kwargs['database_path'])
        self.assertTrue(record['preserves_pty_supervisor'])

    def test_owner_wont_stop_a_changed_process(self):
        self.accept()
        self.path.write_text(json.dumps({**self.previous, 'pid': 42}), encoding='utf-8')
        with mock.patch('universe_service_actions.time.sleep'), mock.patch('universe_service_actions.restart_service') as restart, mock.patch('universe_service_actions.service_status', return_value={}):
            self.service.run(self.request['request_id'])
            record = self.service.status({'operation_id': self.request['request_id']})['operation']
        restart.assert_not_called()
        self.assertEqual('SERVICE_INSTANCE_CHANGED', record['error']['code'])

    def test_interrupted_helper_reports_uncertainty_without_claiming_success(self):
        record = self.accept()
        with self.service._connect() as connection:
            record['updated_at'] = 1
            self.service._save(connection, record)
        with mock.patch('universe_service_actions.service_status', return_value={'status': 'READY'}), mock.patch('universe_service_actions.pid_is_running', return_value=False):
            operation = self.service.status({'operation_id': record['operation_id']})['operation']
        self.assertEqual('INTERRUPTED', operation['status'])
        self.assertEqual('ACCEPTED', operation['recorded_status'])
        self.assertEqual('HELPER_INTERRUPTED', operation['error']['code'])

    def test_catalog_only_exposes_bound_actions_and_schemas_match(self):
        registry = build_default_action_registry(work_surface_handlers={'service.status': lambda r,c: self.service.status(r), 'service.restart': lambda r,c: {}})
        self.assertEqual('universe.service-status.v1', registry.lookup('service.status').result_schema_ref)
        self.assertEqual('SERVICE_LIFECYCLE_MUTATION', registry.lookup('service.restart').side_effect_class)


class ServiceActionHttpTests(unittest.TestCase):
    def test_token_gate_after_body_read_and_keep_alive_drain(self):
        from test_memory_candidates_and_delegations import MemoryCandidateApiTests
        import http.client
        fixture = MemoryCandidateApiTests()
        fixture.setUp()
        try:
            host, port = fixture.server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=5)
            try:
                payload = json.dumps({'action_id': 'service.restart', 'request': {'request_id': 'http-restart-test', 'expected_pid': os.getpid()}})
                # The Action envelope is parsed before authorization. Re-reading
                # the same body here would block instead of returning 401.
                with mock.patch('universe_service_actions.ServiceActions.restart', return_value={'schema': 'universe.service-restart.v1', 'status': 'ACCEPTED'}) as restart:
                    connection.request('POST', '/v1/actions', payload, {'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    self.assertEqual(401, response.status)
                    self.assertEqual('SERVICE_CONTROL_TOKEN_REQUIRED', json.loads(response.read())['error_code'])
                    restart.assert_not_called()
                    connection.request('POST', '/v1/actions', payload, {'Content-Type': 'application/json', 'Authorization': 'Bearer candidate-test-token'})
                    response = connection.getresponse()
                    self.assertEqual(200, response.status)
                    self.assertEqual('ACCEPTED', json.loads(response.read())['status'])
                    self.assertEqual('USER', restart.call_args.args[1]['kind'])
                    connection.request('POST', '/v1/service/shutdown', '{}', {'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    self.assertEqual(401, response.status)
                    response.read()
            finally:
                connection.close()
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
