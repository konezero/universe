import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from provider_session_adapter import (
    ProviderSession, ProviderSessionIdentityError, GrokSessionAdapter,
    CodexSessionAdapter, ClaudeSessionAdapter,
)
from claude_permission_bridge import ClaudePermissionBridge
from claude_permission_broker import ClaudePermissionBroker


class ProviderSessionAdapterTests(unittest.TestCase):
    def make_adapter(self, kind, *, broker=None, fail=False):
        self.closed = []
        self.observer = None
        test = self

        def factory(observe):
            test.observer = observe
            return SimpleNamespace(session_id='real-id', close=lambda: test.closed.append('native'))

        class Gateway:
            session_ref = kind.prefix + 'real-id'
            host_session_ref = 'host-id'

            def __init__(self, native):
                if broker:
                    # Assert both identities before the first provider request.
                    test.assertEqual(self.session_ref, broker.token.current_session_ref())
                    test.assertEqual(self.session_ref, broker.bridge.session_ref)

            def reply_stream(self, prompt, callback):
                test.observer('real-id')
                if fail:
                    raise RuntimeError('provider failed')
                return 'result'

            def close(self):
                test.closed.append('gateway')

        kwargs = {'broker': broker} if kind is ClaudeSessionAdapter else {}
        return kind(session=ProviderSession(kind.provider, 'frame', 'turn',
                                           session_anchor_ref='anchor'),
                    factory=factory, gateway_factory=Gateway, **kwargs)

    def test_all_providers_share_typed_execution_and_cleanup(self):
        for kind in (GrokSessionAdapter, CodexSessionAdapter, ClaudeSessionAdapter):
            with self.subTest(provider=kind.provider):
                result = self.make_adapter(kind).run('prompt')
                self.assertEqual('result', result.text)
                self.assertEqual(kind.prefix + 'real-id', result.session.provider_session_ref)
                self.assertEqual('frame', result.session.task_frame_id)
                self.assertEqual('turn', result.session.task_frame_turn_id)
                self.assertEqual('anchor', result.session.session_anchor_ref)
                self.assertEqual('host-id', result.session.host_session_ref)
                self.assertEqual(['gateway'], self.closed)

    def test_provider_failure_closes_each_adapter(self):
        for kind in (GrokSessionAdapter, CodexSessionAdapter, ClaudeSessionAdapter):
            with self.subTest(provider=kind.provider):
                adapter = self.make_adapter(kind, fail=True)
                with self.assertRaisesRegex(RuntimeError, 'provider failed'):
                    adapter.run('prompt')
                self.assertEqual(['gateway'], self.closed)

    def test_claude_binds_real_broker_and_bridge_before_gateway(self):
        bridge = ClaudePermissionBridge(session_ref='claude-code:pending:test',
                                        permission_requester=lambda request: None)
        broker = ClaudePermissionBroker(bridge=bridge)
        try:
            result = self.make_adapter(ClaudeSessionAdapter, broker=broker).run('prompt')
            self.assertEqual(result.session.provider_session_ref, broker.token.current_session_ref())
            self.assertEqual(result.session.provider_session_ref, bridge.session_ref)
        finally:
            broker.close()

    def test_identity_cannot_change_after_binding(self):
        adapter = self.make_adapter(ClaudeSessionAdapter)
        adapter.observe('first')
        adapter.observe('first')
        with self.assertRaisesRegex(ProviderSessionIdentityError, 'ID_CHANGED'):
            adapter.observe('other')

    def test_cross_provider_session_rejected(self):
        with self.assertRaisesRegex(ProviderSessionIdentityError, 'PROVIDER_MISMATCH'):
            ClaudeSessionAdapter(session=ProviderSession('CODEX', 'frame', 'turn'),
                                 factory=None, gateway_factory=None)

    def test_gateway_cannot_return_another_provider_identity(self):
        adapter = self.make_adapter(CodexSessionAdapter)
        original = adapter.gateway_factory

        def wrong_gateway(native):
            gateway = original(native)
            gateway.session_ref = 'claude-code:real-id'
            return gateway

        adapter.gateway_factory = wrong_gateway
        with self.assertRaisesRegex(ProviderSessionIdentityError, 'REF_INVALID'):
            adapter.run('prompt')
        self.assertEqual(['gateway'], self.closed)

    def test_empty_provider_identity_rejected(self):
        adapter = self.make_adapter(GrokSessionAdapter)
        with self.assertRaisesRegex(ProviderSessionIdentityError, 'ID_REQUIRED'):
            adapter.observe('')

    def test_prepare_failure_closes_native(self):
        broker = SimpleNamespace(bind_session_ref=lambda ref: (_ for _ in ()).throw(ValueError('mismatch')))
        adapter = self.make_adapter(ClaudeSessionAdapter, broker=broker)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            adapter.run('prompt')
        self.assertEqual(['native'], self.closed)


if __name__ == '__main__':
    unittest.main()
