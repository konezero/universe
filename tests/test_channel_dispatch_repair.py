from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import claude_channel_mcp as mcp
from universe_server import UniverseHTTPServer
from universe_app.session_bus import SessionBus, SessionBusError
from claude_channel_broker import ClaudeChannelBroker, session_lookup_path


class ChannelDispatchRepairTests(unittest.TestCase):
    def setUp(self):
        self.terminal = {'terminal_id': 't', 'session_anchor_ref': 'a',
                         'supervisor_session_id': 's', 'state': 'LIVE',
                         'provider': 'CLAUDE', 'mode': 'MASTER', 'project_id': 'p'}
        self.host = Mock()
        self.host.get.return_value = self.terminal
        self.host.list_sessions.return_value = [self.terminal]
        self.host.find_live.return_value = self.terminal
        self.bus = SessionBus()
        self.message = self.bus.deliver_to_terminal(self.host, terminal=self.terminal,
            source={}, to={'session_anchor_ref': 'a'}, kind='INSTRUCTION', notify='NONE', body='work')
        self.mid = self.message['message_id']

    def test_ack_does_not_create_result_or_complete_work(self):
        self.bus.claim_instruction(self.host, terminal_id='t', session_anchor_ref='a')
        self.bus.complete_instruction_claim(terminal_id='t', session_anchor_ref='a', message_id=self.mid)
        for phase in ('RECEIVED', 'STARTED'):
            value = self.bus.acknowledge_instruction(self.mid, terminal_id='t', session_anchor_ref='a', phase=phase)
            self.assertEqual('STARTED', value['lifecycle_state'])
        self.assertIn('provider_started_at', value['lifecycle'])
        self.assertEqual(1, len(self.bus._messages))
        with self.assertRaises(SessionBusError):
            self.bus.acknowledge_instruction(self.mid, terminal_id='other', session_anchor_ref='a', phase='RECEIVED')

    def test_dispatch_phase_is_distinct_from_provider_start(self):
        self.bus.claim_instruction(self.host, terminal_id='t', session_anchor_ref='a')
        dispatched = self.bus.complete_instruction_claim(
            terminal_id='t', session_anchor_ref='a', message_id=self.mid)
        # Adapter delivered the instruction; the provider has not acknowledged it.
        self.assertEqual('STARTED', dispatched['lifecycle_state'])
        self.assertEqual('DISPATCHED', dispatched['lifecycle']['execution_phase'])
        self.assertIn('dispatched_at', dispatched['lifecycle'])
        # An explicit provider STARTED ACK promotes the phase and stamps the
        # real start time (used as the terminal-result correlation floor).
        running = self.bus.acknowledge_instruction(
            self.mid, terminal_id='t', session_anchor_ref='a', phase='STARTED')
        self.assertEqual('RUNNING', running['lifecycle']['execution_phase'])
        self.assertIn('provider_started_at', running['lifecycle'])

    def test_recovery_never_steals_an_in_process_claim(self):
        self.bus.claim_instruction(self.host, terminal_id='t', session_anchor_ref='a')
        self.assertEqual([], self.bus.recover_pending_deliveries()['messages'])
        self.bus.release_instruction_claim(terminal_id='t', session_anchor_ref='a', message_id=self.mid)
        self.assertEqual(1, len(self.bus.recover_pending_deliveries()['messages']))

    def test_recovery_backoff_preserves_same_message(self):
        with patch('universe_app.session_bus.time.time', return_value=100):
            self.bus.record_dispatch_attempt(self.mid, {'status':'CHANNEL_PENDING', 'error_code':'PENDING'})
            self.assertEqual([], self.bus.recover_pending_deliveries()['messages'])
        with patch('universe_app.session_bus.time.time', return_value=200):
            values = self.bus.recover_pending_deliveries()['messages']
        self.assertEqual([self.mid], [m['message_id'] for m in values])
        self.assertEqual('PENDING', values[0]['lifecycle']['dispatch_attempt']['error_code'])

    def test_mismatched_supervisor_provider_is_reported_without_dispatch(self):
        server = UniverseHTTPServer.__new__(UniverseHTTPServer)
        server.session_bus = self.bus
        server._session_anchor_terminal_host = lambda: self.host
        server.session_supervisor = SimpleNamespace(get_session=lambda sid: {
            'session_id':'s', 'session_anchor_ref':'a', 'currentness':'CURRENT', 'provider':'GROK', 'mode':'MASTER'})
        server._dispatch_pending_session_instruction = Mock()
        result = server._dispatch_live_posted_session_instructions({'messages':[self.message]})
        self.assertEqual('SESSION_IDENTITY_MISMATCH', result[0]['status'])
        server._dispatch_pending_session_instruction.assert_not_called()
        self.assertEqual('PENDING', self.bus._messages[self.mid]['delivery_state'])

    def test_http_channel_error_is_not_collapsed_to_unavailable(self):
        error = urllib.error.HTTPError('http://localhost/result', 409, 'conflict', {},
            io.BytesIO(json.dumps({'error_code':'CHANNEL_RESULT_CONFLICT', 'detail':'different result'}).encode()))
        with patch.object(mcp, '_ENDPOINT', 'http://127.0.0.1:1'), patch.object(mcp.urllib.request, 'urlopen', side_effect=error):
            result = mcp._post(mcp.CHANNEL_RESULT_PATH, {}, 'token')
        self.assertEqual('CHANNEL_RESULT_CONFLICT', result['error_code'])
        self.assertEqual(409, result['http_status'])

    def test_old_host_cannot_silently_record_ack_as_final(self):
        with patch.object(mcp, '_ACK_SUPPORTED', False), patch.object(mcp, '_post') as post:
            result = mcp.handle_message({'jsonrpc':'2.0', 'id':1, 'method':'tools/call',
                'params':{'name':'universe_channel_ack', 'arguments':{'message_id':self.mid, 'phase':'RECEIVED'}}})
        self.assertEqual('CHANNEL_ACK_UNSUPPORTED', result['result']['structuredContent']['error_code'])
        post.assert_not_called()

    def test_real_http_channel_ack_and_final_reach_bus_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'UNIVERSE_DATA_DIR':tmp}):
            broker = ClaudeChannelBroker(terminal_id='t', project_id='p', mode='MASTER', provider='CLAUDE', supervisor_session_id='s').start()
            try:
                bootstrap = json.loads(session_lookup_path('t').read_text(encoding='utf-8'))
                registration = broker.exchange_bootstrap(bootstrap['bootstrap_token'])
                server = UniverseHTTPServer.__new__(UniverseHTTPServer)
                server.session_bus = self.bus
                server._session_anchor_terminal_host = lambda: self.host
                server.terminal_host = self.host
                self.host.channel_state.return_value = 'READY'
                self.host.push_channel.side_effect = lambda tid, payload, on_result: broker.push(payload, on_result=on_result)
                dispatch = server._dispatch_pending_session_instruction(project_id='p',
                    session={'session_id':'s', 'session_anchor_ref':'a', 'provider':'CLAUDE', 'mode':'MASTER'}, trigger='TURN_IDLE')
                self.assertEqual('DISPATCHED', dispatch['status'])
                with patch.object(mcp, '_ENDPOINT', broker.endpoint), patch.object(mcp, '_SESSION_TOKEN', registration['session_token']), patch.object(mcp, '_ACK_SUPPORTED', True):
                    for tool, args in [('universe_channel_ack', {'message_id':self.mid, 'phase':'STARTED'}),
                                       ('universe_channel_reply', {'message_id':self.mid, 'body_text':'actual final result'})]:
                        reply = mcp.handle_message({'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params':{'name':tool, 'arguments':args}})
                        self.assertFalse(reply['result']['isError'], reply)
                        if tool.endswith('_ack'):
                            self.assertEqual(1, len(self.bus._messages))
                results = [m for m in self.bus._messages.values() if m['kind'] == 'RESULT']
                self.assertEqual(1, len(results))
                self.assertEqual('actual final result', results[0]['body_text'])
                self.host.write.assert_not_called()
            finally:
                broker.close()

    def test_rust_channel_error_is_not_collapsed_to_unavailable(self):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.recv.return_value = b'{"status":"ERROR","error_code":"CHANNEL_RESULT_CONFLICT","detail":"conflict","channel":null}\n'
        with patch.object(mcp, '_ENDPOINT', 'tcp://127.0.0.1:1'), patch.object(mcp.socket, 'create_connection', return_value=client):
            result = mcp._post(mcp.CHANNEL_RESULT_PATH, {}, 'token')
        self.assertEqual('CHANNEL_RESULT_CONFLICT', result['error_code'])


if __name__ == '__main__':
    unittest.main()
