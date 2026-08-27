# Rust Reconnection Host

## Purpose

The Reconnection Host preserves connection continuity for a Session that already survives independently of a Supervisor. It is not a session runtime and does not make lifecycle, routing, Goal, authority, or BOOT decisions.

```text
Independent Session / cmd / ConPTY
              ^
              | reconnectable handle (next slice)
              v
      Rust Reconnection Host
              ^
              | authenticated local IPC
              v
          Supervisor
```

The first vertical slice proves that the Host process and its stable identity survive the disappearance of one Supervisor connection and accept a replacement Supervisor attachment. cmd/ConPTY handle ownership and handle transfer are deliberately the next slice.

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

Supported actions are `status`, `attach`, `detach`, and `shutdown`. Every request is bounded to 16 KiB and authenticated. A replacement Supervisor may attach without replacing the Host. Only the currently attached Supervisor may detach.

## Non-goals

The Host does not know or own:

- Goal, Plan, Todo, Automation, or RAG state;
- MASTER, CONDUCTOR, Worker, authority, Assignment, or BOOT semantics;
- provider session meaning or chat transcripts;
- Supervisor routing, cleanup, currentness, or policy;
- the authoritative identity of a Session merely because a PID is live.

The discovery file is evidence used to find an endpoint. A future Supervisor adapter must validate Host PID plus start time and complete an authenticated handshake before reporting the Host as live.

## Next slice

Create cmd and ConPTY from inside the Host, retain their process and pseudo-console handles, stream bounded terminal output through the IPC contract, and prove that Supervisor B reattaches to the same cmd process after Supervisor A exits.
