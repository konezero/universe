# Rust Reconnection Host

## Purpose

The Reconnection Host preserves connection continuity for a Session that already survives independently of a Supervisor. It is not a session runtime and does not make lifecycle, routing, Goal, authority, or BOOT decisions.

```text
Independent Session / cmd / ConPTY
              ^
              | Host-owned ConPTY handles
              v
      Rust Reconnection Host
              ^
              | authenticated local IPC
              v
          Supervisor
```

The current vertical slice proves that the Host process, its stable identity, and one Host-owned cmd/ConPTY child survive the exit of a real Supervisor process. A second, independently started Supervisor process discovers the Host by exact Anchor, validates it, attaches to the same Host and child process, then continues input and output from a bounded cursor.

## Current contract

The crate lives at `tools/session_host` and exposes one loopback TCP IPC endpoint. TCP is the initial transport adapter; the logical contract is transport-neutral and can move to a Windows named pipe without changing the Host actions.

Host discovery state contains:

- stable Host ID for the lifetime of the Host process;
- Anchor reference supplied by the Supervisor at creation;
- Host PID and start timestamp;
- authenticated IPC endpoint;
- current attached Supervisor ID;
- monotonically increasing attachment generation;
- live child observation (`LIVE` or `EXITED`) and the observed exit code;
- shell child PID and the Host-owned `CONPTY`, `INPUT_WRITER`, and `OUTPUT_READER` handle capabilities.

Supported actions are `status`, `attach`, `detach`, `write`, `read`, `resize`, and `shutdown`. IPC uses one newline-delimited JSON request and response per connection; every request is bounded to 16 KiB and authenticated. Terminal input is transported as base64 bytes. A replacement Supervisor may attach without replacing the Host or cmd process. Only the currently attached Supervisor may detach or perform terminal I/O.

`read` accepts an output cursor and returns base64-encoded raw PTY bytes plus the next cursor. The Host retains the latest 256 KiB; when a caller's cursor predates that window, the response marks the read as truncated. This is byte continuity, not a semantic terminal protocol or a reconstructed screen.

The Host launch contract accepts the shell executable, repeated shell arguments, child working directory, environment overlays, and initial rows/columns. These are process-launch inputs only; they do not grant Universe authority or define provider/session identity.

## Anchor-first Supervisor adapter

`tools/universe_app/reconnection_host.py` provides the first Python Supervisor adapter. The registry derives an opaque discovery filename from the SHA-256 of the exact Anchor rather than exposing Anchor text in a path. Discovery succeeds only after all of these checks pass:

1. the state schema and exact Anchor match;
2. the recorded Host PID is live;
3. the observed process creation time matches the Host start timestamp;
4. an authenticated `status` handshake returns the same Anchor, Host ID, PID, and start timestamp.

`ReconnectionPty` projects the Host endpoint through the existing PTY backend shape (`write`, bounded `read`, `resize`, `is_alive`, and `close`). Closing this adapter only detaches the Supervisor. Explicit Host shutdown remains a separate operation, so losing or replacing a Supervisor does not implicitly terminate cmd.

This adapter is an additive migration seam. The existing `TerminalHost` spawn and orphan cleanup path remains unchanged until the bridge has been exercised in production routing; this avoids silently changing current cleanup semantics before Anchor reconciliation and cleanup policy are wired together.

## Non-goals

The Host does not know or own:

- Goal, Plan, Todo, Automation, or RAG state;
- MASTER, CONDUCTOR, Worker, authority, Assignment, or BOOT semantics;
- provider session meaning or chat transcripts;
- Supervisor routing, cleanup, currentness, or policy;
- the authoritative identity of a Session merely because a PID is live.

The discovery file is evidence used to find an endpoint. It contains the bearer token needed for reattachment, so callers must place the registry in a Host-owned access-controlled Runtime directory. The adapter applies owner-only mode where supported, but Windows ACL provisioning remains a deployment responsibility. IPC responses never expose the token.

## Next slice

Wire this additive adapter into `TerminalHost` creation behind an explicit migration switch, reconcile Host registry records during Supervisor polling, and replace kill-on-restart orphan cleanup only for terminals confirmed to be Host-owned. Windows ACL provisioning and durable stale-record cleanup also remain. Goal, authority, BOOT, provider identity, and Anchor currentness stay outside the Host.
