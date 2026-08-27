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

The current vertical slice proves that the Host process, its stable identity, and one Host-owned cmd/ConPTY child survive the disappearance of one Supervisor connection. A replacement Supervisor attaches to the same Host and child process, then continues input and output from a bounded cursor.

## Current contract

The crate lives at `tools/session_host` and exposes one loopback TCP IPC endpoint. TCP is the initial transport adapter; the logical contract is transport-neutral and can move to a Windows named pipe without changing the Host actions.

Host discovery state contains:

- stable Host ID for the lifetime of the Host process;
- Anchor reference supplied by the Supervisor at creation;
- Host PID and start timestamp;
- authenticated IPC endpoint;
- current attached Supervisor ID;
- monotonically increasing attachment generation;
- explicit `LIVE` runtime observation.
- shell child PID and the Host-owned `CONPTY`, `INPUT_WRITER`, and `OUTPUT_READER` handle capabilities.

Supported actions are `status`, `attach`, `detach`, `write`, `read`, and `shutdown`. IPC uses one newline-delimited JSON request and response per connection; every request is bounded to 16 KiB and authenticated. A replacement Supervisor may attach without replacing the Host or cmd process. Only the currently attached Supervisor may detach or perform terminal I/O.

`read` accepts an output cursor and returns base64-encoded raw PTY bytes plus the next cursor. The Host retains the latest 256 KiB; when a caller's cursor predates that window, the response marks the read as truncated. This is byte continuity, not a semantic terminal protocol or a reconstructed screen.

## Non-goals

The Host does not know or own:

- Goal, Plan, Todo, Automation, or RAG state;
- MASTER, CONDUCTOR, Worker, authority, Assignment, or BOOT semantics;
- provider session meaning or chat transcripts;
- Supervisor routing, cleanup, currentness, or policy;
- the authoritative identity of a Session merely because a PID is live.

The discovery file is evidence used to find an endpoint. It contains the bearer token needed for reattachment, so the future Supervisor adapter must place it in a Host-owned access-controlled Runtime directory. The adapter must also validate Host PID plus start time and complete an authenticated handshake before reporting the Host as live. IPC responses never expose the token.

## Next slice

Integrate Supervisor discovery with the Host registry, validate PID plus start time before attachment, provision Windows ACLs for the discovery file, and replace the simulated Supervisor IDs in the integration test with real Supervisor A/B processes. Process exit detection, resize, cleanup policy, and durable registry reconciliation also remain outside this slice.
