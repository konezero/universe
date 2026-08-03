# Provider Session Validation Todo

Status: OPEN
Scope: provider session lifetime validation and residual bootstrap hardening
Reference commits: `7533c1e` through `503e873`

## P1 - Grok bounded-session CLI probe

- [ ] Run a real Grok CLI bounded Worker session after provider quota resets.
- [ ] Capture provider session and Universe coordinate state before and after the run.
- [ ] Verify the bounded run does not persist a resumable session reference or replace the Node/Mode connection coordinate.
- [ ] Verify close terminates the bounded provider process and leaves no resident-session binding.

Acceptance evidence must come from the actual Grok CLI process. Structural and contract tests alone do not complete this item.

## P2 - Claude MCP bootstrap file cleanup

- [ ] Remove the temporary `mcp.json` after the one-time bootstrap exchange when the provider no longer needs the file.
- [ ] Cover normal close, startup failure, timeout, and interrupted bootstrap cleanup paths.
- [ ] Verify an exchanged or stale bootstrap cannot authorize a later request and is absent from durable logs and receipts.

The current one-time exchange already invalidates the bootstrap token. This item reduces residual file exposure and operational clutter.

## Deferred boundary

`ClaudeResidentSession` remains resident-only. Bounded Claude Boss and Worker calls continue through the non-persistent `ClaudeCodeSession` path. Revisit this split only if bounded Workers move to the stream-json transport; it is not a current defect.