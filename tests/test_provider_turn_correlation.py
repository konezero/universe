from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from provider_session_observer import ProviderSessionObserverStore
from provider_turn_correlation import codex_turn_event, exact_turn_matches
from universe_app.session_bus import SessionBus
from universe_server import UniverseHTTPServer

MID = 'msg_' + 'a' * 16
REF = 'dispatch_' + 'b' * 32


def user(turn='turn-1', mid=MID, ref=REF, role='UserMessage'):
    return {'type':'event_msg', 'timestamp':'9999-01-01T00:00:00Z', 'payload':{
        'type':'item_completed', 'turn_id':turn, 'item':{'type':role,
        'content':[{'type':'text', 'text':f'universe_dispatch_ref: {ref}\ninstruction_ref: session-bus:{mid}\nPRIVATE_BODY'}]}}}


def complete(turn='turn-1'):
    return {'type':'event_msg', 'timestamp':'9999-01-01T00:00:01Z',
            'payload':{'type':'task_complete', 'turn_id':turn}}


class ProviderTurnCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'rollout-correlation.jsonl'
        self.path.write_text('', encoding='utf-8')
        self.store = ProviderSessionObserverStore(self.root / 'observer.db')
        self.source = self.store.register_source({'provider':'CODEX',
            'provider_session_id':'codex-turn-test', 'source_path':str(self.path),
            'source_kind':'CODEX_ROLLOUT_JSONL', 'source_version':'v1', 'start_at_end':False})['source_id']

    def scan(self, *events):
        with self.path.open('a', encoding='utf-8') as f:
            for e in events:
                f.write(json.dumps(e)+'\n')
        self.store.scan(self.source)
        return sorted(self.store.list_activities(self.source, active_only=False), key=lambda x:x['ordinal'])

    def test_explicit_user_and_terminal_join_survives_restart_without_body_storage(self):
        self.scan(user())
        self.store = ProviderSessionObserverStore(self.root / 'observer.db')
        rows = self.scan(complete())
        self.assertEqual('ACTIVE', rows[0]['activity_state'])
        self.assertEqual('COMPLETED', rows[-1]['activity_state'])
        self.assertEqual(MID, rows[-1]['bus_message_id'])
        self.assertEqual(REF, rows[-1]['bus_dispatch_ref'])
        self.assertNotIn('PRIVATE_BODY', json.dumps(rows))
        self.assertNotIn(b'PRIVATE_BODY', (self.root / 'observer.db').read_bytes())

    def test_other_turn_and_assistant_echo_cannot_bind(self):
        rows = self.scan(user(role='AgentMessage'), complete(), user('turn-2'), complete('turn-3'))
        for row in rows:
            if row['event_kind']=='TURN_COMPLETED':
                self.assertIsNone(row['bus_dispatch_ref'])

    def test_conflicting_inputs_in_same_turn_fail_closed(self):
        rows = self.scan(user(), user(ref='dispatch_'+'c'*32), complete())
        self.assertIsNone(rows[-1]['bus_dispatch_ref'])

    def test_parser_requires_explicit_turn_and_input_envelope(self):
        self.assertIsNone(codex_turn_event({'type':'response_item', 'payload':user()['payload']}))
        event=user(); event['payload'].pop('turn_id')
        self.assertIsNone(codex_turn_event(event))
        event=user(); event['payload']['item']['content'][0]['text']='quoted\n'+event['payload']['item']['content'][0]['text']
        self.assertIsNone(codex_turn_event(event))

    def test_pty_whitespace_folding_preserves_exact_header_binding(self):
        event=user()
        content=event['payload']['item']['content'][0]
        content['text']=content['text'].replace('\n',' ')
        rows=self.scan(event,complete())
        self.assertEqual(MID,rows[-1]['bus_message_id'])
        self.assertEqual(REF,rows[-1]['bus_dispatch_ref'])

    def test_exact_match_rejects_old_attempt_source_and_message(self):
        activity=self.scan(user(), complete())[-1]
        lifecycle={'observer_source_id':self.source, 'bus_dispatch_ref':REF}
        self.assertTrue(exact_turn_matches(lifecycle, MID, self.source, activity))
        for candidate in ({**activity,'bus_dispatch_ref':'dispatch_'+'c'*32},
                          {**activity,'bus_message_id':'msg_'+'d'*16},
                          {**activity,'provider_turn_id':None},
                          {**activity,'event_kind':'TURN_STARTED'}):
            self.assertFalse(exact_turn_matches(lifecycle, MID, self.source, candidate))
        self.assertFalse(exact_turn_matches(lifecycle, MID, 'other-source', activity))

    def test_server_projects_exact_terminal_without_time_baseline(self):
        host=Mock(); terminal={'terminal_id':'t','session_anchor_ref':'a','state':'LIVE',
            'provider':'CODEX','mode':'MASTER','project_id':'p'}
        host.get.return_value=terminal; host.find_live.return_value=terminal
        host.list_sessions.return_value=[terminal]
        bus=SessionBus()
        message=bus.deliver_to_terminal(host, terminal=terminal, source={},
            to={'session_anchor_ref':'a'}, kind='INSTRUCTION', notify='NONE', body='work')
        mid=message['message_id']
        bus.claim_instruction(host, terminal_id='t', session_anchor_ref='a')
        bus.complete_instruction_claim(terminal_id='t', message_id=mid,
            session_anchor_ref='a', observer_source_id=self.source, bus_dispatch_ref=REF)
        server=UniverseHTTPServer.__new__(UniverseHTTPServer)
        server.session_bus=bus; server._session_anchor_terminal_host=lambda:host
        server.store=Mock()
        activity=self.scan(user(mid=mid), complete())[-1]
        wrong={**activity,'bus_dispatch_ref':'dispatch_'+'c'*32}
        rejected=server._project_observed_session_bus_terminal_result(
            session={'session_anchor_ref':'a'}, activity=wrong, source_id=self.source)
        self.assertEqual('SESSION_BUS_RESULT_CORRELATION_UNPROVEN', rejected['status'])
        self.assertEqual(1,len(bus._messages))
        accepted=server._project_observed_session_bus_terminal_result(
            session={'session_anchor_ref':'a'}, activity=activity, source_id=self.source)
        self.assertEqual('SESSION_BUS_RESULT_PROJECTED',accepted['status'])
        server.store.list_provider_session_activities.assert_not_called()
        self.assertEqual(2,len(bus._messages))

    def test_dispatcher_stamps_exact_reference_before_observer_completion(self):
        host=Mock(); terminal={'terminal_id':'t','session_anchor_ref':'a','state':'LIVE',
            'provider':'CODEX','mode':'MASTER','project_id':'p',
            'backend_owner':'RUST_RECONNECTION_HOST','launch_profile':'INTERACTIVE'}
        host.get.return_value=terminal; host.find_live.return_value=terminal
        host.list_sessions.return_value=[terminal]; host.submit_prompt.return_value='delivered'
        bus=SessionBus()
        message=bus.deliver_to_terminal(host, terminal=terminal, source={},
            to={'session_anchor_ref':'a'}, kind='INSTRUCTION', notify='NONE', body='work')
        mid=message['message_id']
        server=UniverseHTTPServer.__new__(UniverseHTTPServer)
        server.session_bus=bus; server.terminal_host=host
        server._session_anchor_terminal_host=lambda:host
        server._register_exact_provider_observer_source=lambda **kw:{'source_id':self.source}
        result=server._dispatch_pending_session_instruction(project_id='p', trigger='TURN_IDLE',
            session={'session_id':'s','session_anchor_ref':'a','provider':'CODEX',
                     'provider_session_ref':'codex-turn-test','mode':'MASTER'})
        self.assertEqual('DISPATCHED',result['status'],result)
        body=host.submit_prompt.call_args.args[1]
        ref=bus._messages[mid]['lifecycle']['bus_dispatch_ref']
        self.assertTrue(body.startswith(f'universe_dispatch_ref: {ref}\ninstruction_ref: session-bus:{mid}\n'))
        activity=self.scan(user(mid=mid,ref=ref),complete())[-1]
        result=server._project_observed_session_bus_terminal_result(
            session={'session_anchor_ref':'a'},activity=activity,source_id=self.source)
        self.assertEqual('SESSION_BUS_RESULT_PROJECTED',result['status'])


if __name__=='__main__':
    unittest.main()
