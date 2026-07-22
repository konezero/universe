# Runtime Frame Template

Status: Candidate Template
Template Family: `runtime_frame`
Target Implementation Path: project-local runtime frame store
Scope: executable-runtime currentness
Owner: attached project
Consumer: ai-career Runtime commands, OS_STATUS, OS_UPDATE, OS_VALIDATE, boot fast path

## Purpose

This template defines the canonical executable-runtime frame shape. The Mode
Current Anchor belongs to the selected Mode and may exist on a
mobile or web Host without this frame or an executor.

It implements the contract from:

```text
.ai/core/SESSION_CURRENTNESS.md
.ai/core/ANCHOR_TEMPORAL_COORDINATE.md
.ai/core/NODE_MODE_COORDINATE_CONTRACT.md
```

## Required Shape

```yaml
runtime_frame:
  session_id: <current-session-id>
  frame_id: current
  node: <node>
  mode: <mode>
  role: <resolved-role>
  mode_scope: <resolved-scope>
  anchor_id: <anchor>
  state: <state>
  entered_at: <anchor-creation-time>
  observed_at: <last-current-anchor-observation-time>
  state_updated_at: <last-semantic-state-transition-time>
  validated_at: <last-source-backed-validation-time-or-empty>
  state_origin: current_session | previous_session | checkpoint | archive | memory | conversation | unknown
  state_freshness: current | restored | stale | forwarded | unknown
  previous_session_id: <previous-or-null>
  current_session_id: <current-session-id>
  checkpoint_ref: <checkpoint-path-or-null>
  validation_ref: <validation-path-or-null>
  authority: <UNASSIGNED | assigned | unknown>
  source_ref: <git-backed-source>
  source_commit: <git-commit-or-UNKNOWN>
  runtime_image_status: assembled | absent | stale | unknown
  runtime_image_ref: <path-or-null>
  runtime_image_profile: memory | markdown | yaml | json | sqlite_memory | sqlite_file | file_cache | other | unknown
  authority_certificate_status: current | stale | missing | unknown
  authority_certificate_ref: <path-or-null>
  session_location: <runtime-writer-surface-or-UNKNOWN>
  commander_surface: <user-interaction-surface-or-UNKNOWN>
  execution_surface: <runtime-writer-surface-or-UNKNOWN>
  repository_location: <repo-host-or-UNKNOWN>
  execution_assignment: <task-id-or-UNASSIGNED>

session_preparation_state: PREPARED | REHYDRATED | UNKNOWN

mode_anchor_reference:
  mode: <mode-or-UNKNOWN>
  anchor_id: <mode-current-anchor-or-UNKNOWN>
  state: CURRENT | UNKNOWN
  snapshot_ref: <anchor-snapshot-or-UNKNOWN>
  authority: false

transport_evidence:
  state: ATTACHED | DETACHED | RECONNECTED | ROUTE_CHANGED | UNKNOWN
  observed_at: <host-observation-time-or-UNKNOWN>
  evidence_ref: <host-evidence-ref-or-UNKNOWN>
  authority: false
```

`transport_evidence` is stored beside the Runtime Frame, not inside the
Current Anchor identity. Updating it alone must leave the complete
`runtime_frame` object unchanged.

`mode_anchor_reference` is provenance only. It does not make the
executable Runtime frame current, ready, authorized, or assigned.

## Session ID Coordinate

Runtime frame session IDs SHOULD use this compact coordinate shape:

```text
<NODE>-<MODE>-<YYYYMMDD>-<SESSION_LOCATION>-<SEQ>
```

Example shape:

```text
<PROJECT>-<MODE>-20260705-<RUNTIME-WRITER-SURFACE>-001
```

Project-specific tokens are allowed, but they must not be treated as Core
schema requirements. The location token identifies the runtime writer /
execution surface for currentness comparison; it does not identify authority.

## Checkpoint Boundary

`checkpoint_ref` is separate from session identity.

```text
session_id      -> currentness / restore coordinate
checkpoint_ref  -> checkpoint evidence pointer
```

Neither value grants execution authority by itself.

## Restore Candidate Ordering

Restore candidates are selected only inside the same `node + mode` coordinate.

Candidate ordering:

```text
1. same node
2. same mode
3. latest observed_at, or updated_at for a legacy surface
4. latest date in session_id
5. latest seq in session_id
6. matching session_location when available
7. valid checkpoint_ref
```

The selected candidate must still pass `OS_STATUS` or `OS_VALIDATE` before
continuation.

## Required Rules

MUST:

```text
executable Runtime currentness key = session_id + frame_id
session_id is executable Runtime evidence coordinate only
mode must not be used as executable Runtime currentness identity
Mode Current Anchor key = mode
previous_session_id -> current_session_id is handoff evidence only
checkpoint_ref is separate from session identity
restore candidate selection stays inside same node + same mode
selected restore candidate passes OS_STATUS or OS_VALIDATE before continuation
restored or stale *_ING must route to OS_STATUS / OS_VALIDATE before continuation
state origin and freshness must be known or UNKNOWN
same-Anchor user input updates observed_at only
Host observation time must not move backward
elapsed time alone must not create STALE
replaced Anchors remain Beyond footprints
adopted recall creates a new Current Anchor at current physical time
transport-only events leave runtime_frame and observed_at unchanged
Commander input from a new surface changes only commander_surface and observed_at
Commander Surface and Execution Surface remain distinct coordinates
frame store is cache, not authority
Runtime Image is boot artifact, not authority
Authority Certificate presence is not execution authority
Git-backed source remains authority
```

MUST NOT:

```text
use single global frame_id=current for shared multi-session store
treat session_id as authority
treat checkpoint_ref as authority
treat mode/role/scope/anchor state as authority
continue restored or stale active state automatically
continue from selected restore candidate without OS_STATUS or OS_VALIDATE
require SQLite as the only implementation
mutate from stale Runtime Image or stale Authority Certificate
reactivate a Beyond Anchor directly
use an LLM-generated timestamp when Host physical time is available
use transport attachment or reconnect as authority or currentness evidence
infer execution_surface or repository_location from commander_surface
```

## Implementation Freedom

Projects may implement the frame store with:

```text
Markdown
YAML
JSON
SQLite
runtime memory
file cache
other project-local storage
```

The implementation must preserve the contract. It does not need to copy a
specific GCS schema, file layout, session location token, or checkpoint path.

## OS_UPDATE / OS_VALIDATE

Attached projects should report one of:

```text
implemented
proposal_required
deferred
not_applicable
unknown
```

OS_VALIDATE should report `PARTIAL` or `UNKNOWN` when a project has
frame-currentness surfaces but cannot prove whether it:

```text
- uses session_id + frame_id as the executable Runtime currentness key,
- uses Mode as the Current Anchor key,
- treats session_id as evidence coordinate only,
- separates checkpoint_ref from session identity,
- selects restore candidates only inside same node + same mode,
- requires OS_STATUS or OS_VALIDATE before restore continuation.
- preserves Anchor temporal field meanings,
- updates observed_at only for same-Anchor user input,
- avoids creating STALE from elapsed time alone,
- creates a new Current Anchor after adopted Beyond recall,
- keeps transport-only observations outside the Current Anchor snapshot,
- keeps Commander, execution, and repository coordinates separate.
```

## Status

```text
Template status: candidate
Implementation owner: project
Canonical contract: .ai/core/SESSION_CURRENTNESS.md
Temporal contract: .ai/core/ANCHOR_TEMPORAL_COORDINATE.md
State trust contract: .ai/core/RUNTIME_STATE_TRUST_GATE.md
Runtime image contract: .ai/core/RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md
Authority binding contract: .ai/core/RUNTIME_AUTHORITY_EXECUTION_BINDING.md
Source validation: konezero/GCS PR #36, PR #38
```
