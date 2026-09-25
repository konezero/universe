"""Typed Worker session boundary; provider identity is not a Host or Task Frame ID.

Provider factories translate configuration. Adapters own identity observation,
permission binding and gateway lifetime. No credentials are stored in results.
"""
from dataclasses import dataclass, replace
from typing import Callable, Protocol


@dataclass(frozen=True)
class ProviderSession:
    provider: str
    task_frame_id: str
    task_frame_turn_id: str
    provider_session_ref: str = ''
    host_session_ref: str = ''
    session_anchor_ref: str = ''


@dataclass(frozen=True)
class ProviderSessionResult:
    session: ProviderSession
    text: str


class SessionAdapter(Protocol):
    def run(self, prompt: str) -> ProviderSessionResult: ...


class ProviderSessionIdentityError(ValueError):
    pass


class GatewaySessionAdapter:
    provider: str
    prefix: str

    def __init__(self, *, session: ProviderSession, factory: Callable,
                 gateway_factory: Callable):
        if session.provider != self.provider:
            raise ProviderSessionIdentityError('PROVIDER_SESSION_PROVIDER_MISMATCH')
        self.session = session
        self.factory = factory
        self.gateway_factory = gateway_factory

    def observe(self, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ProviderSessionIdentityError('PROVIDER_SESSION_ID_REQUIRED')
        ref = self.prefix + session_id
        if self.session.provider_session_ref not in ('', ref):
            raise ProviderSessionIdentityError('PROVIDER_SESSION_ID_CHANGED')
        self.bind_permission(ref)
        self.session = replace(self.session, provider_session_ref=ref)

    def bind_permission(self, ref: str) -> None:
        pass  # Grok/Codex deliver approvals through their native session transports.

    def prepare(self, native) -> None:
        pass

    def run(self, prompt: str) -> ProviderSessionResult:
        native = self.factory(self.observe)
        gateway = None
        try:
            self.prepare(native)
            gateway = self.gateway_factory(native)
            text = gateway.reply_stream(prompt, lambda _delta: None)
            ref = gateway.session_ref
            if not ref.startswith(self.prefix):
                raise ProviderSessionIdentityError('PROVIDER_SESSION_REF_INVALID')
            self.observe(ref[len(self.prefix):])
            self.session = replace(self.session,
                                   host_session_ref=gateway.host_session_ref or '')
            return ProviderSessionResult(self.session, text)
        finally:
            if gateway is not None:
                gateway.close()
            else:
                native.close()


class GrokSessionAdapter(GatewaySessionAdapter):
    provider = 'GROK'
    prefix = 'grok-acp:'


class CodexSessionAdapter(GatewaySessionAdapter):
    provider = 'CODEX'
    prefix = 'codex-app-server:'


class ClaudeSessionAdapter(GatewaySessionAdapter):
    provider = 'CLAUDE'
    prefix = 'claude-code:'

    def __init__(self, *, broker=None, **kwargs):
        super().__init__(**kwargs)
        self.broker = broker

    def bind_permission(self, ref: str) -> None:
        if self.broker is not None:
            # One owner updates BOTH broker token and bridge. Never rebind only
            # the bridge, and never relax cross-session rejection.
            self.broker.bind_session_ref(ref)

    def prepare(self, native) -> None:
        if self.broker is not None:
            # Resident Claude supplies --session-id before spawning its process.
            # Bind before MCP registration and the first permission request.
            self.observe(native.session_id)
