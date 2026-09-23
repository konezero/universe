import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_agent_session_gateway import FakeJsonRpcTransport
from agent_session_gateway import AgentSessionError, CodexAppServerSession
from task_frame_file_editor import TOOL
from universe_runtime_worker_dispatch import RuntimeWorkerDispatcher


class HostDynamicToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.handler = Mock(return_value={'status': 'HOST_EDIT_READ', 'repository_write': False})
        self.options = dict(executable=Path(self.temp.name)/'codex.exe', cwd=Path(self.temp.name),
                            environment={}, system_prompt='system', session_id=None,
                            permission_requester=lambda _: None, session_observer=lambda _: None,
                            ephemeral=True, dynamic_tools=[TOOL], dynamic_tool_handler=self.handler)

    def test_registers_ephemeral_tool_and_dispatches_structured_response(self):
        with patch('agent_session_gateway.JsonRpcStdioProcess', FakeJsonRpcTransport):
            session = CodexAppServerSession(**self.options)
            self.addCleanup(session.close)
            session.prompt('test', lambda _: None)
            start = next(p for m,p in session._transport.requests if m == 'thread/start')
            self.assertEqual([TOOL],start['dynamicTools'])
            self.assertTrue(start['ephemeral'])
            session._active_delta = lambda _: None
            response = session._handle_request('item/tool/call',dict(threadId=session.session_id,
                turnId='turn',callId='call',tool='universe_edit',arguments={'operation':'read'}))
            self.assertTrue(response['success'])
            self.assertEqual('HOST_EDIT_READ',json.loads(response['contentItems'][0]['text'])['status'])
            self.handler.assert_called_once()

    def test_rejects_unbound_calls_and_does_not_leak_handler_errors(self):
        with patch('agent_session_gateway.JsonRpcStdioProcess', FakeJsonRpcTransport):
            session = CodexAppServerSession(**self.options)
            self.addCleanup(session.close)
            session.prompt('test', lambda _: None)
            call = dict(threadId=session.session_id,turnId='turn',callId='call',tool='universe_edit',arguments={})
            self.assertFalse(session._handle_request('item/tool/call',call)['success'])
            session._active_delta = lambda _: None
            for change in ({'threadId':'other'},{'turnId':''},{'callId':''},{'tool':'unknown'},
                           {'namespace':'other'},{'arguments':'not-an-object'}):
                with self.subTest(change=change):
                    self.assertFalse(session._handle_request('item/tool/call',{**call,**change})['success'])
            self.handler.assert_not_called()
            self.handler.side_effect = RuntimeError('sensitive content')
            result = session._handle_request('item/tool/call',call)
            self.assertFalse(result['success'])
            self.assertNotIn('sensitive',str(result))

    def test_requires_ephemeral_and_callable_handler(self):
        for changes in ({'ephemeral':False},{'dynamic_tool_handler':None}):
            with self.subTest(changes=changes), self.assertRaises(AgentSessionError):
                CodexAppServerSession(**{**self.options,**changes})

    def test_native_write_cannot_bypass_active_host_editor_or_escalate(self):
        dispatcher = RuntimeWorkerDispatcher.__new__(RuntimeWorkerDispatcher)
        dispatcher._host_editors = {'run':object()}
        dispatcher.permission_escalator = Mock(return_value='APPROVE')
        request = {'worker_run_ref':'run','repository_write_scope':'BOUNDED'}
        permission = {'tool_call':{'title':'item/fileChange/requestApproval'},
                      'options':[{'kind':'allow_once','optionId':'yes'},{'kind':'reject_once','optionId':'no'}]}
        with patch.object(dispatcher,'describe_write_permission',return_value={'kind':'WRITE'}):
            self.assertEqual('no',dispatcher._task_frame_permission(request,permission))
        dispatcher.permission_escalator.assert_not_called()
