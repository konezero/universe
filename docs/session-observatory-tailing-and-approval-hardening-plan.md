# Session Observatory, Vendor Tailing, and Approval Hardening Plan

Status: Approved implementation plan
Priority: P0
Owner: Universe
Scope: local Universe service, Session Observatory, provider adapters, approval UX, and regression evidence

## Outcome

Universe must present one stable Universe session even when the provider,
project, node, mode, working directory, or provider session reference changes.
The Session Observatory must discover and tail Codex, Claude, and Grok session
sources without importing full transcripts, and the approval surface must treat
Commander text and an approval button as two inputs to the same server-owned
decision path.

The first dogfood case is the active Codex desktop session. Today it appears
under `CODEX -> gcs` because the rail groups provider observations by initial
working directory. The intended result is a bound Universe session displayed
at its current Universe project/node/mode location, with immutable origin and
append-only movement history available as secondary details.

## Non-goals

- Do not copy full provider transcripts into Universe SQLite.
- Do not persist hidden reasoning, raw tool payloads, secrets, provider tokens,
  or unredacted command text.
- Do not make observation equivalent to provider control, Runtime authority,
  Assignment, Current Anchor mutation, or Execution Guard permission.
- Do not let a provider, Boss, Worker, or relayed model message create Commander
  approval.
- Do not implement remote relay or cloud synchronization in this slice.
- Do not modify installer-managed `.ai` Runtime files as product work.

## Canonical Session Model

### Stable identity

`universe_session_id` is the canonical durable key. It does not change when a
provider is replaced, a provider session is resumed, or the session moves to a
different project/node/mode.

### Immutable origin

Store once at first observation or registration:

```text
origin_provider
origin_provider_session_ref_hash
origin_workspace_key
origin_observed_at
```

Raw provider session references and source paths remain adapter-private. Public
API and UI receive opaque IDs, labels, and redacted source kinds only.

### Mutable current location

```text
current_project_id
current_node
current_mode
current_anchor_id
current_provider
current_provider_binding_id
current_activity_state
currentness
last_seen_at
```

Current location is a projection. Provider/PWD origin is not silently rewritten
when the session moves.

### Append-only history

Add two histories:

```text
provider_binding_history
  universe_session_id, provider, opaque_provider_binding_id,
  started_at, ended_at, transition_reason

session_location_history
  universe_session_id, project_id, node, mode, anchor_id,
  entered_at, left_at, source_ref
```

Only one provider binding and one location may be current. Transitions use a
transaction and compare-and-swap version so duplicate discovery cannot create
two current rows.

## Provider Observation and Tailing

Use a provider-neutral adapter contract:

```text
discover_sessions()
identify_session()
open_cursor()
read_bounded(cursor, limits)
reduce(event)
project_activity()
```

Required adapters:

| Provider | Source | Cursor rule |
| --- | --- | --- |
| Codex desktop/CLI | session metadata plus `rollout-*.jsonl` | byte offset plus file identity or monotonic rollout ordinal |
| Claude | project session JSONL | source identity plus branch-aware event cursor |
| Grok | session `updates.jsonl` | event position plus file identity; never tail `chat_history.jsonl` |

Each adapter must fail closed to `UNKNOWN` on missing source, rotation,
truncation, unsupported schema, ambiguous identity, or cursor regression. It
must not repair those conditions by guessing.

### Bounded live states

The normalized projection supports:

```text
LIVE
WORKING
WAITING_APPROVAL
FAILED
DISCONNECTED
UNKNOWN
```

`LIVE` requires recent provider-source evidence or a current resident process
lease. A stale Supervisor binding alone cannot produce `LIVE`. `WORKING` and
`WAITING_APPROVAL` require provider event evidence, not message text inference.

Tail payloads exposed to the browser are bounded redacted activity messages.
They may include assistant-visible text chunks and state transitions, but not
reasoning, tool arguments, credentials, source bodies, or complete transcript
replay. Universe stores cursor/evidence metadata and concise activity records,
not the raw stream.

## Session Observatory Information Architecture

### Primary grouping

1. Bound sessions: group by current Universe project, then current node/mode.
2. Unbound sessions: group under Vendor, then immutable origin workspace.
3. Hidden sessions: excluded from the normal rail and available through a
   separate hidden view.

The rail label uses a user alias when present, otherwise a stable anchor-derived
label. Raw Universe or provider session IDs are not primary UI text.

### Session row

Show:

```text
alias or anchor label
provider and model
current project / node / mode
activity state and last-seen time
bound / unbound / hidden state
pending approval indicator
```

The detail view may show immutable origin and movement/provider history. Moving
or rebinding a session updates current location and appends history; it does not
create a second canonical session.

### Commands

- Open/resume the selected provider session.
- Bind or move to a Universe project/node/mode.
- Switch provider while retaining the Universe session.
- Hide/unhide a session.
- Filter likely bounded Task Frame workers without deleting provider history.

## Approval Unification

### One decision service

Both UI button approval and Commander text approval call the same backend
operation:

```text
decide_pending_proposal(proposal_id, digest, decision, commander_context)
```

The server, not the browser or model, derives:

```text
commander_surface
commander_input_ref / evidence_ref
active Universe session
proposal id and digest match
idempotency key
```

The user never assembles a session reference or approval evidence packet.

### Natural-language rule

- Exactly one current pending proposal plus direct Commander input such as
  `approve` or `승인`: approve that exact proposal.
- More than one pending proposal: require explicit proposal selection in the
  inbox; never guess.
- No pending proposal: report that there is nothing to approve.
- Duplicate approval: return the existing decision idempotently.
- Stale digest or proposal: fail closed and refresh the inbox.

Platform tool permission remains a separate approval class and continues to use
the provider/platform prompt surface.

### Trust boundary

Only direct Commander-origin input or an authenticated browser action may
decide a governance proposal. Provider output, Project Master output, relayed
room messages, Boss/Worker results, and observer events cannot be normalized
into Commander approval.

## API and Storage Changes

Expected storage additions or migrations:

```text
universe_session
provider_binding_history
session_location_history
provider_source_cursor
session_visibility
```

Existing Supervisor session rows are migrated or projected into the canonical
model. Backfill rules:

1. Reuse an existing stable Universe session key when one is already present.
2. Otherwise create one canonical session per unambiguous current Supervisor
   record.
3. Preserve first provider/PWD as origin.
4. Use existing node/mode/anchor binding as current location only when evidence
   is present; otherwise leave the session unbound.
5. Do not infer a provider session reference from a process, alias, or path.

Expected API evolution:

```text
GET  /v1/sessions
GET  /v1/sessions/{opaque_id}
POST /v1/sessions/{opaque_id}/bind
POST /v1/sessions/{opaque_id}/provider
POST /v1/sessions/{opaque_id}/visibility
GET  /v1/sessions/{opaque_id}/activity/stream
GET  /v1/governance/proposals/pending
POST /v1/governance/proposals/{proposal_id}/decision
```

Existing endpoints may remain as compatibility routes but must delegate to the
same services.

## Architecture-Drift Observation

Record the current implementation drift as the first regression observation:

```text
expected: bound current session grouped by Universe location
actual: provider/PWD grouping (`CODEX -> gcs`)
cause class: implementation drift after context compression / incomplete plan carryover
evidence: UI/API projection and current code grouping rule
correction: canonical session location model and rail regrouping
```

The observation pipeline stores only structured, redacted data:

```text
expected_contract_ref
observed_behavior_code
drift_class
source_commit
validation_ref
correction_commit
regression_test_ref
```

This is separate from Skill/model Bench data. Later batches may correlate drift
with provider/model/task context, but a single incident is not a model score.

## Implementation Slices

### Slice 1 - Canonical session and migration

Files:

- `tools/session_supervisor.py`
- `tools/universe_server.py`
- `tests/test_session_supervisor.py`
- `tests/test_universe_server.py`

Deliver the stable session record, origin/current-location separation, history,
visibility, migration, idempotent transitions, and compatibility projections.

### Slice 2 - Provider adapters and bounded tail

Files:

- `tools/provider_session_observer.py`
- provider gateway/session modules only when required
- `tests/test_provider_session_observer.py`
- focused provider integration tests

Deliver Codex, Claude, and Grok discovery plus bounded incremental reducers,
cursor persistence, rotation/truncation handling, and normalized activity.

### Slice 3 - Observatory and approval UX

Files:

- `tools/universe_ui/app.js`
- `tools/universe_ui/index.html` only if semantic structure must change
- `tools/universe_ui/styles.css`
- `tools/universe_server.py`
- `tests/test_universe_server.py`
- static/browser UI tests

Deliver current-location grouping, unbound Vendor/PWD fallback, alias/anchor
labels, filters, true activity tailing, pending proposal inbox, and unified
button/text approval.

### Slice 4 - Security review, QA, and dogfood

Deliver:

- migration and compatibility tests
- cursor replay/rotation/truncation tests
- duplicate/stale approval tests
- provider/worker approval rejection tests
- transcript/reasoning/secret redaction tests
- browser visual and interaction QA at desktop and mobile widths
- dogfood proof that the active Codex desktop session appears once at its
  current Universe location and updates activity without raw transcript storage
- architecture-drift observation linked to its regression test

## Task Frame Topology

The implementation Task Frame is created only after this document is committed.
Its immutable input includes the plan commit SHA and this file's SHA-256.

```text
Parent: current Codex MASTER
Boss: Claude Opus, architecture/decomposition/synthesis
Worker 1: Codex Luna Max, canonical data model/migration/API
Worker 2: Codex Luna Max, provider adapters/tailing/UI integration
Worker 3: Codex Luna Max, independent security review and QA
Parent final: inspect adopted changes, run the full regression/browser suite,
              fix follow-up defects, and produce final commit evidence
```

Workers receive bounded Context Packs and declared file allocations. They do
not re-enter Boot or repository governance. Product mutation remains receipt
checked through the active Task Frame lineage.

## Acceptance Criteria

- The active Codex desktop session appears exactly once under its current
  Universe project/node/mode, not under `CODEX -> gcs` once bound.
- Initial provider/PWD remains visible as immutable origin in details/history.
- Switching provider or moving node/mode retains the same Universe session and
  appends history.
- Unbound sessions remain discoverable under Vendor/PWD.
- Codex, Claude, and Grok adapters resume from monotonic cursors and fail closed
  on unsupported or regressed sources.
- `LIVE`, `WORKING`, and `WAITING_APPROVAL` require concrete provider/process
  evidence; a stale binding cannot create them.
- Text `승인` and the approval button produce the same idempotent decision for a
  single exact pending proposal.
- Multiple pending proposals require selection, and no model/worker/provider
  message can approve.
- Public APIs and UI expose no raw provider ref, local source path, token,
  hidden reasoning, or full transcript copy.
- Desktop and mobile browser QA passes without rail/chat overlap.
- Existing Session Supervisor, Project Master, provider, and governance tests
  remain green.
- The drift incident is stored as structured evidence and closed only by a
  linked regression test and correction commit.

## Completion Evidence

Completion requires:

```text
plan_commit_sha
plan_file_sha256
Task Frame proposal/approval/result refs
implementation commit(s)
security review result
automated test result
browser QA evidence
dogfood observation
architecture-drift closure ref
```

The implementation is incomplete if only the rail layout changes. Canonical
identity, real provider tailing, approval unification, security boundaries, and
dogfood evidence are one vertical slice.
