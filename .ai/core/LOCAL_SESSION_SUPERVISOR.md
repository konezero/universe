# Local Session Supervisor

Status: Active Core Runtime Contract
Scope: persistent local Mode Session ownership and observation

## Purpose

The Local Session Supervisor is a vendor-neutral contract between the installed
ai-career Runtime and a Host implementation such as Universe. It replaces
fire-and-forget Session Boot processes with explicitly owned, observable, and
reconcilable local sessions.

The Supervisor does not become the source of Provider, Mode, Anchor, or
executable Runtime currentness. A resumed project session evaluates its own
currentness. The Supervisor records and presents the resulting observation.

## Identity Separation

These identities are never interchangeable:

| Identity | Owner | Purpose |
| --- | --- | --- |
| Supervisor session record | Host Supervisor store | Durable user-visible session history |
| Mode binding | ai-career/project Runtime | Node and Mode operating coordinate |
| Provider session reference | Provider adapter | Resume the Provider-owned conversation |
| Runtime process | Local Supervisor Service | Exact process ownership and lifecycle |

An editable alias is presentation metadata only. Provider selection and
Provider Session Refs are binding attributes, not Supervisor session identity.

## Persistent And Ephemeral Boundary

```text
CONDUCTOR or MASTER Mode Session
  -> PERSISTENT_MODE_SESSION
  -> eligible for Supervisor registration and provider resume

TASK_FRAME_BOSS or TASK_FRAME_WORKER
  -> EPHEMERAL
  -> never registered as a persistent session
  -> never replaces a persistent Provider Session Ref
```

Subordinate agents remain governed by `TASK_FRAME_ORCHESTRATION.md`.

## Vendor-Neutral Verbs

```text
LAUNCH
ADOPT
RESUME
RECONNECT
STOP
DESCRIBE
```

Every persistent process exposes a descriptor containing:

```text
PID
process creation time
executable
exact argv
endpoint
handshake token fingerprint
```

The Supervisor may adopt or stop a process only when every field matches its
active lease and an independent Host process probe confirms the leased PID and
process creation time still identify a live process. Partial or unavailable
evidence resolves to `UNKNOWN`; it never authorizes inference, cleanup, or bulk
termination.

Exact argv identity is stored as an ordered fingerprint vector that preserves
argument boundaries without persisting any raw argument. Every argument is
stored as `sha256:<digest>` in both the descriptor and lease, including values
whose secret semantics are unknown to the registry. The separate handshake
fingerprint remains required.
The endpoint is a secret-free loopback HTTP origin. User info, query strings,
fragments, and non-loopback hosts are forbidden in the process descriptor.

## Registry And Default Selection

The Host stores all observed persistent sessions. It may select exactly one
provider-neutral default for each `(node, mode)` target while retaining the
other Provider bindings as alternatives and history.

```text
default selection != currentness
default selection != authority
default selection != destructive capability
```

A changed Provider or Provider Session Ref appends binding history. It must not
erase an earlier binding or rewrite the Supervisor session identity.
Alias and Provider-binding updates are metadata operations and may annotate a
terminal session, but they never change session state, lease state, currentness,
or process ownership. Reactivation requires explicit lease acquisition.

## Session State

```text
REGISTERED -> STARTING -> LIVE -> DISCONNECTED -> LIVE
                                 |                |
                                 +----> STOPPED <-+
any ambiguous identity -> UNKNOWN
```

`UNKNOWN` may return to an observed state only after full identity
reverification. It cannot transition directly to a destructive operation.
`STOPPED`, `RELEASED`, `STOP_AUTHORIZED`, and `STALE` cannot be reconciled back
to a live state. A `RELEASED` or `STALE` process must enter through explicit
lease acquisition with a rotated capability.

## Process Lease

Only the Local Supervisor Service owns process leases. A lease binds the exact
process descriptor, a hashed capability, and a monotonically checked version.
Replacement is allowed only after the prior lease is `RELEASED` or `STALE`.

The tray, UI, Provider adapter, and project session cannot infer ownership from
a PID, endpoint, command substring, or successful health response.

The local UI may select the provider-neutral default session without the
Supervisor control token. That metadata update changes only the `(node, mode)`
routing pointer and never creates currentness, authority, process ownership, or
destructive capability.

An invalid lease capability is rejected without changing the session, lease
state, or lease version. When the verified lease owner presents mismatching or
unverifiable process evidence, the child remains running, the lease and session
resolve to `UNKNOWN`, and the owner refreshes the current lease version. The
process handle is retained for observation; denial never falls back to raw
termination.

An `UNKNOWN` lease may be released only by an explicit Supervisor-control
operation after the Supervisor itself proves that the original PID plus process
creation time is absent. PID reuse counts as absence of the original process.
The recovery writes an event and changes only lease/session state; it never
terminates a process or adopts the observed replacement.

Legacy process inventory is observe-only. It must not expose a raw legacy
command line because positional credentials cannot be classified reliably.
Read surfaces may expose only a bounded command profile and a one-way command
fingerprint.

## Host Boundary

ai-career owns the vocabulary, descriptor validation, and lifecycle
invariants. A Host such as Universe owns the durable registry, process probes,
leases, reconciliation, Provider adapters, and UI. The Host Runtime Lifecycle,
not Session Boot or the tray UI, supervises the Supervisor Service itself.

`OS_UPDATE` remains a Host Runtime Lifecycle operation and never starts a
Session Boot session.

## Automatic Continuity Boundary

The common mode contract is carried into attached projects by
`.ai/templates/runtime_continuity/README.md` and
`.ai/skills/common/resume-save/SKILL.md`. Every persistent Conductor, Master,
and project Mode Session must attach the shared lifecycle adapter and route
`TASK_COMPLETED`, `NORMAL_STOP`, `PROVIDER_SWITCH`, `MODE_SWITCH`, and debounced
`IDLE` through it. A Provider or UI surface may not opt out.

Task Frame Boss and Worker sessions remain ephemeral. They do not replace a
persistent Provider Session Ref or create a persistent Mode Resume record; the
Parent flushes bounded task results through its own persistent session.

Automatic continuity writes only to the attached project's local
`.ai/runtime/continuity/continuity.sqlite`. It is a Supervisor lifecycle flush,
not the user command named `RESUME_SAVE`. The same Runtime validators and
durable store are used, but an automatic trigger cannot infer a user request,
publish an archive, or create Git authority.

Unknown coordinates fail closed as `AUTO_CONTINUITY_SKIPPED`. A crash preserves
the last good record and dirty-end evidence; it never fabricates a summary.

Git publication and Resume Archive publication remain separate, explicit user
commands with separate approval. Automatic continuity has no code path that
invokes either publication mechanism.

## Service And Tray Boundary

The Local Supervisor Service owns persistent session records and exact process
leases. The tray is a user-scope shell that may start the service, display
status, open the UI, or request authenticated graceful shutdown. It does not
own or terminate child session processes.

Service shutdown first flushes local continuity and then closes each exact
leased child through the Supervisor. There is no broad process-kill fallback.

## Legacy Executors

Legacy Session Boot executors are inventoried read-only:

```text
exact six-field identity plus active lease -> MANAGED_EXACT
partial overlap                          -> UNKNOWN
no overlap                               -> UNMANAGED
```

Observation never authorizes bulk cleanup, PID-only adoption, or a raw process
kill. Migration may occur only after the complete identity and active lease are
established through the normal Supervisor route.

## Non-Authority Rule

```text
Supervisor registry != Provider currentness
Supervisor registry != Mode Current Anchor
Supervisor registry != authority
Supervisor registry != Execution Assignment
Supervisor registry != execution permission
```

The Supervisor is a durable routing, ownership, continuity, and observability
surface. It does not replace the active project's Runtime decisions.
