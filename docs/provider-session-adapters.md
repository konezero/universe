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

## PowerShell command routing

Provider adapters translate native command requests into the immutable
`CommandInvocation` contract (provider, native tool, exact command text, cwd,
request ID). The common Worker permission path consumes this typed contract;
it does not branch on CLI/tool names. Native names belong only to each adapter.
Unrecognized tools cannot forge the Host's typed classification, and unsupported
command formats are not stringified into executable commands.

Claude `PowerShell` is a command alongside `Bash`. Previously, even with a
matching broker/bridge identity, PowerShell was rejected without reaching the
Master escalation path. Classification does not grant permission: read-only
Workers still need an explicit Master decision for shell execution, and
destructive-command checks remain in effect.

Claude broker decisions emit bounded Host-owned room diagnostics: recognized
tool name, allow/deny, sanitized code, and identity-match booleans. Commands,
tokens, arbitrary exception text and provider reasoning are not recorded.
Diagnostic logging cannot approve a rejected command. Lack of a diagnostic
is not evidence that the bridge succeeded or that session identity failed.

## Collected result notifications

A Master can collect a Worker result and request rework in the same turn.
Leaving that result notification STARTED until a separate chat reply would
block delivery of the next Worker's permission request. The server maintenance
sweep therefore acknowledges result delivery only when the canonical collection
matches the exact run, launched journal-backed Host, current Master owner,
project, Todo, immutable result reference and digest. Historical attempts are
looked up by exact result identity, not replaced by the latest attempt.

The message records `RESULT_COLLECTION_CONFIRMED` in `lifecycle.collection_ack`.
This does not fabricate a provider reply, review PASS or Todo completion, and
does not acknowledge permission requests or uncollected results. Persistence
failure leaves the message unchanged and is reported by the maintenance sweep.

## Live verification (2026-09-25)

Fresh Claude Host `host_85df5238f9a3619dbc8d` (PID 46284) loaded the typed
adapter implementation with repository write scope NONE. In room
`room_06c0ad796c2c4801ee19db7b`, sequences 3 and 8 record PowerShell ALLOWED
with matching broker/bridge identity. Attempt 2 read pinned assignment sequence
20 (SHA-256 `61e5bdd1be59db943d10b2c194437bbb01217bb654871caa9b145de0bf692768`)
and reported journal-read exit 0 and exact PowerShell output
`DIAG-TYPED-20260925-7E82`, exit 0.

Canonical collection event `persona_event_b42d9144a81f48c2a880d11f` preserves
Worker result digest
`8c2c1062f9268e03dbfececb04ecc5d1f6b8706ecfdd89d3b32f2352b0a264c1`.
Host status then confirmed `alive=false`, `EXITED`, `MASTER_DONE` at
2026-09-25T05:39:10Z. This is execution-path diagnostic acceptance, not a
Reviewer PASS or completion of the original implementation Todo. Grok and Codex
were contract-tested locally, not newly exercised live in this verification.

The service restart operation
`provider-command-collection-recovery-20260925-1` completed (PID 44124 to 44660).
The server subsequently acknowledged previously collected notice
`msg_21563c5e58268ece` with `RESULT_COLLECTION_CONFIRMED`, freeing the next
permission notification. Its old, long-waiting Worker later returned
`CLAUDE_PERMISSION_CANCELLED_BY_SHUTDOWN`; that failed attempt was not counted
as success and its Host was closed before the fresh successful test.
