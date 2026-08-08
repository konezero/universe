# Universe Shared Runtime and Provider Session Observer

Status: Proposed architecture

Related:

- `docs/universe-install-mode.md`
- `docs/core-release-db.md`
- `docs/local-session-supervisor.md`
- `docs/universe-runtime-host.md`
- `docs/universe-design-and-bench-flow.md`
- `docs/universe-network-architecture.md`

## Purpose

Universe must let a Project remain independently installable while making the
normal multi-project path simple: attach the Project, use shared Runtime
services, and keep the Project's own identity and execution boundary intact.

The same host must observe Provider session activity without taking ownership
of Provider transcripts. This creates one architecture for three related
concerns:

1. Project attachment and shared Runtime hosting.
2. Release DB governance selection with minimal context injection.
3. Provider session observation, activity projection, and bounded Memory
   candidate extraction.

This document is an evolution contract. It does not replace the existing
`install_mode` behavior until the listed implementation slices land.

## Ownership Rules

```text
Universe Shared Runtime
  - Career Release cache and governance selector
  - shared common Skills
  - Session Supervisor and Provider adapters
  - Runtime lease, Scheduler, work exchange, and Execution Guard transport
  - current binding registry

Project Workspace
  - repository and worktree
  - Project ID and Current Anchor
  - Project release pin and Project profile
  - Project-local Skills, evidence, and artifacts
```

`Universe` may host Runtime services. It never acquires the Project's source,
identity, Current Anchor, release decision, or execution authority merely by
attaching the Project.

## Project Attachment Model

The following axes are independent and must not be collapsed into one
`install_mode` field.

| Field | Values | Meaning |
| --- | --- | --- |
| `install_origin` | `PROJECT_STANDALONE`, `UNIVERSE_CREATED` | Immutable historical origin. |
| `universe_membership` | `DETACHED`, `DISCOVERED`, `LINKED`, `MANAGED` | Current relationship to a Universe instance. |
| `runtime_host` | `PROJECT_LOCAL`, `UNIVERSE_SHARED` | Current Runtime hosting responsibility. |

`runtime_host` is deliberately not named `runtime_owner`: Project ownership
does not transfer when Universe hosts shared Runtime services.

### Invariants

```text
Project ID remains Project-owned.
Current Anchor semantics remain Project-owned.
Attach, detach, and runtime-host changes never rewrite either value.
Project Execution Guard remains the final source-mutation boundary.
Universe membership never creates Project authority or Assignment.
```

### Lifecycle

```text
DETACHED
  -> DISCOVERED  Universe has observed a verifiable Project only
  -> LINKED      explicit attachment and identity verification completed
  -> MANAGED     Universe holds an active shared-Runtime lease

MANAGED
  -> LINKED      shared lease is released or unavailable; Project remains attached
  -> DETACHED    explicit detach; Project identity and local data remain intact
```

An existing standalone Project normally follows:

```text
PROJECT_STANDALONE + DETACHED + PROJECT_LOCAL
  -> DISCOVERED
  -> LINKED + PROJECT_LOCAL
  -> MANAGED + UNIVERSE_SHARED
```

`LINKED` is intentionally useful on its own. It enables discovery, projection,
documentation, and session relationships before any shared Runtime migration.

### Runtime Lease and Fallback

`MANAGED` requires a renewable, scoped Runtime lease that binds the Universe
instance, Project ID, selected release, Runtime Host Profile, and expiry. The
lease is not a source-write capability.

If a shared Runtime becomes unavailable, the Project moves to `LINKED` and
uses its declared standalone fallback only when that Project has one. The
fallback must be visible to the operator; it must not silently create a second
live authority while the Universe host is healthy.

The current `install_mode` record remains the compatibility input:

```text
UNIVERSE_ATTACHED  -> prefer LINKED, then MANAGED after lease activation
PROJECT_STANDALONE -> allow PROJECT_LOCAL fallback; prefer host when configured
```

## Release DB Governance Selector

The Release DB is an executable governance catalog, not a document bundle that
an LLM rereads in full on every turn.

```text
Current coordinates
  Role + Mode + Operation + Scope + Risk + Capability
  -> Runtime selector
  -> required governance units and dependency closure
  -> bounded instruction Context Pack
  -> Master or Worker
```

### Data Model

```text
governance_unit
  governance_id, kind, full_content, compact_instruction,
  source_ref, source_digest, release_id

governance_index
  role, mode, operation, scope_kind, risk_class, capability,
  governance_id, required, priority

governance_dependency
  governance_id, requires_governance_id

governance_override
  base_governance_id, overriding_governance_id, applies_when
```

Every query result includes release ID, unit IDs, source digests, selector
digest, and dependency closure. Runtime may cache verified immutable releases,
but it must invalidate cache entries when the selected release changes.

### Selector Contract

Skills describe selection plans; they do not build arbitrary SQL. Runtime owns
named queries or a validated selector AST.

```yaml
selector:
  role: PROJECT_MASTER
  mode: MASTER
  operation: DELEGATE_TO_BOSS
  scope: FEATURE
  risk: GUARDED
  capability: TASK_FRAME
```

The immutable `governance-bootstrap` Skill is the root of trust. It resolves
the current coordinates, selects the appropriate Role loader, and delegates
only then to operation-specific selection Skills. Core invariants always load;
selected rules are additive.

```text
Always-loaded Core
  + Role rules
  + Operation rules
  + Scope and risk rules
  + required Skills
```

The model receives the rendered minimum instruction set and provenance. It does
not decide which governance files or database records apply.

## Provider Session Observer

Universe observes Provider-owned conversation sources to build a live activity
view. It does not import or duplicate raw transcripts as canonical Universe
data.

```text
Provider transcript or session source
  -> Provider Session Observer
  -> normalized activity projection
  -> Universe UI and Activity DB
  -> bounded batch candidate extraction
  -> optional Memory review flow
```

### Observation Is Not Control

| Plane | Responsibility |
| --- | --- |
| Observation | discover, identify, tail, reduce, project activity events |
| Control | open, resume, send, stop through provider CLI, ACP, or app-server |
| Current truth | Project Runtime Current Anchor |
| Transcript ownership | Provider |
| Relationship and projection | Universe |

Observation never makes a session current, grants provider permission, or
replaces the resident Session Supervisor.

### Adapter Contract

```text
discover_sessions()
identify_session()
subscribe(cursor)
reduce(event)
project()
```

Every adapter persists only source identity and cursor state:

```text
session_source
  provider, provider_session_id, source_path, source_kind,
  file_identity, cursor, source_version, last_seen_at

session_binding
  provider_session_id, project_id, node, mode, current_anchor_id
```

Provider-specific source rules:

| Provider | Discovery and event source | Reducer requirement |
| --- | --- | --- |
| Codex | metadata/index plus `rollout-*.jsonl` | byte offset or rollout ordinal |
| Claude | project `<session-id>.jsonl` | branch reduction via `parentUuid` |
| Grok | session `updates.jsonl` | event-position reducer; never tail `chat_history.jsonl` |

File rotation, truncation, incompatible source schemas, missing paths, and
ambiguous session identity are reported as `UNKNOWN`; they are never repaired
by inference.

### Activity and Memory Boundary

```text
Provider transcript = canonical raw conversation
Universe activity   = live operational projection
Memory              = selected durable knowledge
Current Anchor      = current operational truth
```

Universe records concise activity such as turn start/end, tool phase, approval
wait, quota stop, error class, and result references. It does not write every
event to Memory or invoke an LLM per event.

Candidate extraction is batched at a turn boundary, task completion, or idle
window. It considers only useful decisions, corrections, durable constraints,
failure-resolution pairs, adoption outcomes, and repeatable discoveries. A
candidate stores a source session reference and cursor so the Provider-owned
source can be revisited without copying its transcript.

Raw prompts, source text, provider messages, credentials, tool command text,
and full transcripts are excluded from Universe activity, Bench, and Memory
records unless a separate explicit, redacted artifact contract permits them.

### Local Observer Delivery Boundary

The local observer surface registers an explicitly selected Provider source,
tails it with a durable file identity and cursor, and stores only reduced
activity. Codex rollout JSONL, Claude session JSONL branch leaves, and Grok
`updates.jsonl` share the same `UNKNOWN` fail-closed behavior for rotation,
truncation, missing files, and unsupported schema.

At a turn boundary, Universe can prepare a redacted activity batch candidate.
The candidate contains only activity references, reducer metadata, and the
source cursor. It is `REVIEW_REQUIRED`: it does not publish Memory, create a
Skill Bench observation, or alter Future paths. Those three routes retain
their own evidence and adoption contracts.

## Security and Privacy

1. Observation requires local user enablement and visible source registration.
2. Provider session files remain local and Provider-owned.
3. Universe stores no raw transcript copy and does not synchronize it to Career
   or a remote Gateway.
4. Cursors, paths, and bindings are minimized, access-controlled metadata.
5. An observer can display activity but cannot issue Provider commands.
6. A provider source parser must declare its supported format version.
7. Memory promotion remains a separate review and adoption process.

## Implementation Sequence

### P0: Attachment and shared Runtime foundation

1. Add a versioned attachment record with `install_origin`,
   `universe_membership`, `runtime_host`, and compatibility mapping from
   `install_mode`.
2. Add shared Runtime registry and renewable lease records.
3. Implement `DISCOVERED -> LINKED -> MANAGED` transitions, release selection,
   health checks, explicit detach, and standalone fallback.
4. Keep Project ID, Current Anchor, Project Runtime, and Execution Guard
   contracts unchanged through every transition.

### P0: Governance selector foundation

1. Extend the Career Release builder with governance unit, index, dependency,
   override, and selector-plan artifacts.
2. Implement the immutable `governance-bootstrap` loader and validated named
   selector operations in Runtime.
3. Return a digest-bound minimal Context Pack and prove that no whole-release
   prompt injection occurs.

### P1: Codex observer vertical slice

1. Discover Codex session metadata and locate the active rollout source.
2. Tail incrementally with durable cursor and file-identity checks.
3. Project normalized activity to Session Supervisor and Web UI.
4. Verify restart, rotation, duplicate-event, and unsupported-format behavior.

### P1: Activity-to-Memory batching

1. Store bounded operational activity without raw transcript payloads.
2. Detect turn, task, and idle batch boundaries.
3. Produce reviewable Memory candidates linked to session and cursor only.
4. Keep Memory Sync, Bench observations, and Career promotion as distinct
   follow-up routes.

### P2: Additional Provider adapters and product integration

1. Add the Claude branch reducer and Grok `updates.jsonl` reducer.
2. Expose source state, observer health, and privacy controls in the Session
   Observatory and project view.
3. Integrate the remote Web UI as an observation surface only; pairing never
   exposes local Provider tokens or transcript stores.

## Acceptance Criteria

- A standalone Project can be discovered, linked, managed, detached, and
  reattached without identity or Current Anchor mutation.
- A failed shared Runtime lease falls back explicitly and never silently creates
  dual authority.
- Runtime selects governance through a verified selector contract and renders
  only the required context.
- Codex activity is visible from an incremental source cursor while the raw
  transcript remains Provider-owned.
- Activity batching yields no automatic Memory publication and no per-event LLM
  call.
- Claude and Grok sources fail closed to `UNKNOWN` when parsing cannot be
  verified.
