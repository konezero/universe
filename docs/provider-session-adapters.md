# Worker provider session boundary

Worker execution uses `SessionAdapter.run(prompt) -> ProviderSessionResult`
in `tools/provider_session_adapter.py`. Grok ACP, Codex App Server and both
Claude CLI paths use this boundary through the Worker dispatcher.

`ProviderSession` keeps provider identity, Task Frame/turn, provider session
reference, Host session reference and session Anchor in separate fields.
It is immutable; observing a provider ID produces an updated value. It contains
no credential or authority grant. Missing Host coordinates remain unknown at
the external result boundary; a provider session is not a Host session.

The dispatcher retains native configuration and tool provisioning. Each
adapter owns observed identity, pre-execution binding, stream execution and
session cleanup. Existing external Worker result schemas remain unchanged.
This does not claim identical native tool APIs across providers: Codex dynamic
tools and Claude MCP/shell transport remain distinct implementations.

Claude's adapter binds its generated session ID through
`ClaudePermissionBroker.bind_session_ref` before gateway startup, updating
both the broker token and permission bridge. Binding only the bridge leaves
the token at `claude-code:pending:*` and causes permission session mismatch.
Repeated observation of the same ID is allowed; a different ID or provider is
rejected. Existing cross-session permission checks are not weakened.

Contract tests cover all three providers, cleanup on success/failure, identity
isolation and the actual Claude broker/bridge pre-execution binding. These are
local tests, not evidence of a live Worker completing its Todo. Live acceptance
must use the existing supported Task Frame route.
