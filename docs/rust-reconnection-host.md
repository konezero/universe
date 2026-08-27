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

## Production migration switch

The standalone PTY Supervisor can opt into the Host-backed production path with:

```text
UNIVERSE_RECONNECTION_HOST_ENABLED=1
UNIVERSE_RECONNECTION_HOST_BINARY=<absolute universe-session-host.exe>  # optional when the local release/debug binary exists
UNIVERSE_RECONNECTION_HOST_REGISTRY=<Host-owned registry directory>     # optional
```

The switch is off by default. When disabled, `TerminalHost` keeps the existing Python-owned ConPTY behavior. When enabled, terminal creation launches or reuses the exact Anchor's Rust Host, attaches a `ReconnectionPty`, and writes the provider command into the Host-owned persistent cmd. The Rust Host inherits a sanitized environment so a new provider cannot accidentally inherit Codex, Claude, or Grok session markers from its Supervisor.

`TERMINAL_CREATED` audit evidence now records the backend owner and the bounded metadata needed to rebuild the process-local `TerminalSession`. On Supervisor start and every lifecycle poll, reconciliation runs before legacy orphan cleanup:

```text
audit Terminal metadata + exact Anchor
  -> registry state
  -> PID/start-time validation
  -> authenticated Host status
  -> attach replacement Supervisor
  -> rebuild TerminalSession and output pump
  -> legacy orphan cleanup
```

Only an authenticated, live `RUST_RECONNECTION_HOST` record suppresses legacy cmd termination. A missing, stale, mismatched, or unreachable Host falls back to the existing exact PID/start-time cleanup path. Managed-shell identity files retain verified SessionStart attach evidence so reconstruction does not invent provider attachment from PID liveness.

## Non-goals

The Host does not know or own:

- Goal, Plan, Todo, Automation, or RAG state;
- MASTER, CONDUCTOR, Worker, authority, Assignment, or BOOT semantics;
- provider session meaning or chat transcripts;
- Supervisor routing, cleanup, currentness, or policy;
- the authoritative identity of a Session merely because a PID is live.

The discovery file is evidence used to find an endpoint. It contains the bearer token needed for reattachment, so callers must place the registry in a Host-owned access-controlled Runtime directory. The adapter applies owner-only mode where supported, but Windows ACL provisioning remains a deployment responsibility. IPC responses never expose the token.

## Next slice

Package the Rust binary as a release artifact, provision Windows ACLs for the registry, and add durable stale-record cleanup after operational dogfooding. Claude channel rebinding is best-effort during PTY reconstruction and must remain separately observable from PTY continuity. Goal, authority, BOOT, provider identity, and Anchor currentness stay outside the Host.
