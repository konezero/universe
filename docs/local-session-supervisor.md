# Local Session Supervisor

Status: implementation contract

## Purpose

The Local Session Supervisor is the resident, provider-neutral owner of durable
Mode-session and process metadata. It allows the Universe tray and Web UI to
observe and reconnect persistent sessions without treating a provider process,
chat transcript, Mode label, or PID as sufficient identity.

Room attach, multi-room chat product rules, and harness session-ref inject are
specified in `docs/multi-room-chat-architecture.md` and
`docs/room-session-attach-streaming.md`. The Supervisor stores coordinates and
bindings, not full vendor transcripts.

`POST /v1/sessions/inject` (and CLI `inject-session`) registers a Supervisor
session (stable id from node/mode/provider/ref), optionally sets the
`(node, mode)` default, then attaches the multi-room slot. Skill:
`.ai/skills/common/session-ref-inject/SKILL.md`.

The Supervisor does not decide project currentness. A resumed project session
rehydrates its own Runtime and reports its observed currentness. Universe stores
and displays that report without manufacturing it.

## Components

```text
Tray shell / Web UI
        |
        v
Universe local HTTP service
        |
        +-- Session Supervisor registry and reconciliation
        +-- Provider session adapters
        +-- Project-local continuity coordinator
        |
        v
Persistent provider processes and project Runtime stores
```

The tray is a control surface, not the process owner. The resident Universe
service owns leases and session registry mutations. Task Frame Workers remain
ephemeral and cannot enter this registry.

## Durable Model

The Supervisor uses the existing Universe SQLite database and owns five tables:

- `session_record`: provider-neutral session identity and bounded display state.
- `session_binding_history`: append-only provider session bindings.
- `process_lease`: exact process identity plus a hashed lease capability.
- `target_default_session`: one default pointer per `(node, mode)`.
- `supervisor_event`: append-only reconciliation and control evidence.

Provider transcripts remain provider-owned. The registry stores provider refs,
process metadata, aliases, and bounded summaries only.

## Identity And Ownership

Universe session identity is `(node, mode, session_id)`. Provider and provider
session refs are bindings and may change without changing the target identity.
An alias such as `GCS MASTER | CODEX` is display-only.

A process can be adopted, reconciled, or authorized for stopping only when all
of these values match exactly:

1. PID
2. process creation time
3. executable
4. exact argument array
5. endpoint
6. handshake fingerprint

The registry never stores the raw handshake token, including inside exact argv.
Sensitive argv values, including inline `--token=value` arguments, are replaced
by `sha256:<digest>` while preserving their argument position. Any partial
match produces `UNKNOWN`, records an event, and blocks destructive action.
The launch producer also fingerprints the actual handshake value wherever it
appears, including positional and previously unknown flag forms.

An operator may recover an `UNKNOWN` lease only through the authenticated
Supervisor `recover-absent` operation. The service probes the stored PID and
creation time itself. It releases the lease only when that original process is
gone or the PID belongs to a different process, records the evidence, and never
terminates the observed process.

`process_lease` is mutated only by the Supervisor service. Lease and default
pointer changes use `BEGIN IMMEDIATE` and version-based compare-and-swap.

## Session Lifecycle

```text
REGISTERED -> STARTING -> LIVE -> DISCONNECTED -> LIVE
                                 |                |
                                 +----> STOPPED <-+

Any identity conflict -> UNKNOWN
```

`STOPPED`, `RELEASED`, `STOP_AUTHORIZED`, and `STALE` process leases cannot be
reconciled into `LIVE`. A `RELEASED` or `STALE` lease may return only through
explicit lease acquisition, which rotates the capability. Alias and Provider
binding updates are metadata-only and never change the lifecycle state.

Selecting the default session is intentionally available to the local UI
without the Supervisor control token. It changes only the provider-neutral
`(node, mode)` routing pointer; it does not create currentness, authority,
process ownership, or destructive capability.

Persistent sessions include Universe CONDUCTOR, Universe MASTER, and each
project MASTER. A provider session can be replaced while its binding history is
retained. Exactly one provider-neutral default pointer exists for a target;
alternative sessions remain visible.

## Continuity Boundary

Automatic continuity saves are project-local and target each project's
`.ai/runtime/continuity/continuity.sqlite`. Triggers are bounded idle, normal
stop, provider or Mode switch, provider quota exhaustion, and completed work.
Saves are debounced and idempotent across process restarts. A quota stop keeps
the resident session and active Task Frame available for retry and does not
claim a dirty end. Crash handling records dirty-end evidence and preserves the
last good save; it never invents a summary.

This lifecycle flush is separate from the explicit `RESUME_SAVE` user command.
It reuses Runtime validation and storage without manufacturing a user command,
Git permission, or archive publication request.

Automatic saves must not invoke Git or publish a Resume Archive. Archive
publication requires an explicit command and separate approval.

The continuity coordinate copies executable Runtime currentness from the raw
Session Boot startup observation. Universe does not manufacture `CURRENT`.

## HTTP Surface

The loopback service exposes:

- `GET /v1/supervisor/sessions`
- `GET /v1/supervisor/sessions/{session_id}`
- `GET /v1/supervisor/events`
- `GET /v1/supervisor/legacy-executors`
- `GET /v1/runtime/preflight`
- `GET /v1/runtime/audit`
- `POST /v1/supervisor/sessions`
- `POST /v1/supervisor/sessions/{session_id}/bind`
- `POST /v1/supervisor/sessions/{session_id}/lease`
- `POST /v1/supervisor/sessions/{session_id}/default`
- `POST /v1/supervisor/sessions/{session_id}/reconcile`
- `POST /v1/supervisor/sessions/{session_id}/stop-authorization`
- `POST /v1/service/shutdown` (service-token required)

Registration, Provider binding, lease issuance, reconciliation, and stop
authorization require the service control token. Read-only observability and
loopback UI alias/default selection do not expose that token.

Process launch and termination are adapter-owned operations that consume exact
Supervisor leases and stop receipts. The tray never invokes a raw process-tree
kill. It requests authenticated graceful service shutdown; the service flushes
continuity and closes its owned sessions through their adapters.

## Legacy Migration

Legacy scalar provider/session settings remain readable until a neutral
`session_record`, binding history entry, and default pointer reproduce their
meaning. Migration is additive and idempotent. It must not delete legacy state
or terminate a legacy executor merely because a PID or port appears related.

Dogfooding inventories each executor independently, classifies it using the
full process identity, and leaves unmatched processes untouched.
