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

## P1 - Grok bounded-session CLI probe

- [ ] Run a real Grok CLI bounded Worker session after provider quota resets.
- [ ] Capture provider session and Universe coordinate state before and after the run.
- [ ] Verify the bounded run does not persist a resumable session reference or replace the Node/Mode connection coordinate.
- [ ] Verify close terminates the bounded provider process and leaves no resident-session binding.

Acceptance evidence must come from the actual Grok CLI process. Structural and contract tests alone do not complete this item.

## P2 - Claude MCP bootstrap file cleanup

- [x] Remove the temporary `mcp.json` after the one-time bootstrap exchange when the provider no longer needs the file.
- [x] Cover normal close, startup failure, timeout, interrupted bootstrap, and
  transient Windows file-lock retry cleanup paths.
- [x] Verify an exchanged or stale bootstrap cannot authorize a later request and is absent from durable logs and receipts.

The one-time exchange invalidates the bootstrap token. The provider receives no
session capability token, and a transient unlink failure retains the private
config path for one final cleanup attempt during close.

## P2 - long-running Provider recovery probes

- [ ] Exhaust or simulate each Provider's bounded quota in a controlled account.
- [ ] Restart Universe and prove the same Node/Mode session coordinate and Task
  Frame are selected after reset.
- [ ] Verify retry, explicit Provider rebinding, and user cancellation remain
  distinct audit outcomes.

## Deferred boundary

`ClaudeResidentSession` remains resident-only. Bounded Claude Boss and Worker calls continue through the non-persistent `ClaudeCodeSession` path. Revisit this split only if bounded Workers move to the stream-json transport; it is not a current defect.
