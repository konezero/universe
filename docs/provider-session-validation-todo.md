# Provider Session Validation Todo

Status: OPEN
Scope: provider session lifetime validation and residual bootstrap hardening
Reference commits: `7533c1e` through `503e873`

## Completed - quota continuity and local preflight

- [x] Preserve the resident Provider session and active Task Frame when a
  Provider reports quota exhaustion.
- [x] Save an automatic `PROVIDER_QUOTA` continuity record with bounded usage
  metadata and without a dirty-end claim.
- [x] Expose read-only local executable/authentication preflight suggestions in
  Runtime Settings before starting a Provider session.
- [x] Expose Provider usage/quota, pending approvals, continuity, and Worker
  Bench state in the Runtime Audit UI.

These surfaces report evidence and suggested configuration only. They do not
grant a platform permission, Runtime Assignment, or Provider entitlement.

### First-party quota observation paths

- Codex App Server reads `account/rateLimits/read` after initialization and
  consumes `account/rateLimits/updated` notifications.
- Grok ACP reads the logical `x.ai/billing` extension through its
  `_x.ai/billing` ACP wire name; if unavailable, Universe may inspect only the
  final 256 KiB of the official local Grok CLI billing log.
- Claude preserves the bounded scalar fields from resident `rate_limit_event`
  telemetry, including utilization, rate-limit type, and reset time when sent.
- All three project to `universe.provider-quota-snapshot.v1` with normalized
  `AVAILABLE / WARNING / EXHAUSTED / UNKNOWN` state and quota windows.

The observation path is read-only. It does not scrape browser cookies, poll an
undocumented Claude OAuth endpoint, infer entitlement from authentication, or
terminate/rebind a resident Provider session.

## P1 - Grok bounded-session CLI probe

- [x] Run a real Grok CLI bounded Worker session after provider quota resets.
- [x] Capture provider session and Universe coordinate state before and after the run.
- [x] Verify the bounded run does not persist a resumable session reference or replace the Node/Mode connection coordinate.
- [x] Verify close terminates the bounded provider process and leaves no resident-session binding.

Acceptance evidence must come from the actual Grok CLI process. Structural and contract tests alone do not complete this item.

Acceptance completed on 2026-09-16 with a real `grok-4.5` ACP process and
`ephemeral=True`. The bounded Worker returned the exact requested marker. Its
provider session id was not sent to the Universe session observer, no Universe
terminal or resident binding was created, and the existing Grok Project Master
kept the same live Session Anchor, Host and provider-session coordinate before
and after the run. `close()` terminated the bounded child process. The created
provider history entry was then removed through the supported
`grok sessions delete` command. Redacted evidence is stored at
`.ai/runtime/tmp/grok-persona-acceptance-20260916/grok-bounded-worker-acceptance.json`.

This acceptance is separate from the resident Grok TUI Host-turn probe. That
probe used `HOST_TURN_DELIVERY` and produced the exact in-thread Session Bus
reply; its evidence is stored beside the bounded result in
`grok-host-turn-roundtrip-evidence.json`. Grok does not expose the Codex native
queue contract, so this documentation does not label the PTY Host-turn path a
native provider queue.

## P2 - Claude MCP bootstrap file cleanup

- [x] Remove the temporary `mcp.json` after the one-time bootstrap exchange when the provider no longer needs the file.
- [x] Cover normal close, startup failure, timeout, interrupted bootstrap, and
  transient Windows file-lock retry cleanup paths.
- [x] Verify an exchanged or stale bootstrap cannot authorize a later request and is absent from durable logs and receipts.

The one-time exchange invalidates the bootstrap token. The provider receives no
session capability token, and a transient unlink failure retains the private
config path for one final cleanup attempt during close.

## P2 - long-running Provider recovery probes

- [x] Exhaust or simulate each Provider's bounded quota in a controlled account.
- [ ] Restart Universe and prove the same Node/Mode session coordinate and Task
  Frame are selected after reset.
- [x] Verify retry, explicit Provider rebinding, and user cancellation remain
  distinct audit outcomes.

On 2026-09-16 the controlled quota regression was expanded across Claude,
Codex and Grok. Each Provider records `PROVIDER_QUOTA`, retains its resident
adapter and exact provider-session reference, and does not create a dirty end.
The same suite keeps retry (`COMPLETE` after explicit re-registration), explicit
provider replacement (`REPLACED`), and user cancellation (`CANCELLED`) as
separate durable outcomes. The continuity allow-list now also accepts the
already-emitted `NEW_SESSION` and `PROVIDER_PROFILE_CHANGED` triggers, instead
of silently dropping those two explicit rebind reasons; a regression asserts
all four provider-rebinding triggers remain distinct.

A governed live Web restart (`provider-p2-recovery-restart-20260916`) completed
from PID 34640 to PID 36336 while preserving the PTY Supervisor. The Conductor,
Claude Master, Codex Master and Grok Master retained their exact live Session
Anchor, Host and provider-session coordinates. Evidence is stored at
`.ai/runtime/tmp/grok-persona-acceptance-20260916/provider-p2-restart-acceptance.json`.
No active Task Frame coordinate is exposed by the current terminal or
Supervisor projection, so that restart does not complete the remaining Task
Frame clause. One controlled restart with an actually active Task Frame remains
required; it must be observed from the Task Frame's authoritative runtime store,
not inferred from an unchanged terminal.

## Deferred boundary

`ClaudeResidentSession` remains resident-only. Bounded Claude Boss and Worker calls continue through the non-persistent `ClaudeCodeSession` path. Revisit this split only if bounded Workers move to the stream-json transport; it is not a current defect.
