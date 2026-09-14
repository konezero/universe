import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from universe_app.terminal_host import TerminalHost,TerminalHostError
from universe_app.reconnection_host import ReconnectionPty
class NativeQueueHostBoundaryTests(unittest.TestCase):
    def setup_host(self,available):
        backend=Mock(spec=ReconnectionPty)
        backend.client=Mock()
        backend.client.status.return_value={"turn_delivery":{"native_queue_available":available}}
        session=SimpleNamespace(provider="CODEX",session_anchor_ref="a",backend=backend)
        return SimpleNamespace(get=lambda tid:session),backend
    def test_old_host_cannot_fall_back_to_pty(self):
        host,backend=self.setup_host(False)
        with self.assertRaises(TerminalHostError) as caught:
            TerminalHost.offer_turn(host,"t",{"session_anchor_ref":"a"})
        self.assertEqual("HOST_NATIVE_QUEUE_UPGRADE_REQUIRED",caught.exception.code)
        backend.offer_turn.assert_not_called()
    def test_new_host_receives_exact_payload(self):
        host,backend=self.setup_host(True)
        payload={"session_anchor_ref":"a","message_id":"m","text":"한글\noriginal"}
        TerminalHost.offer_turn(host,"t",payload)
        backend.offer_turn.assert_called_once_with(payload)
    def test_wrong_anchor_never_reaches_native_queue(self):
        host,backend=self.setup_host(True)
        with self.assertRaises(TerminalHostError): TerminalHost.offer_turn(host,"t",{"session_anchor_ref":"foreign"})
        backend.offer_turn.assert_not_called()
