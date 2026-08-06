# Session Currentness

Status: Candidate Core Runtime Contract
Scope: ai-career / attached project runtime
Layer: Session Lifecycle / Runtime State Template / Currentness Evidence
Parent: `.ai/core/SESSION_FRAMEWORK.md`
Created: 2026-07-05
Source validation: `konezero/GCS` PR #36, PR #38, GCS Resume Manager / Session Checkpoints

## Purpose

Session Currentness defines how a runtime records which frame is current without
confusing currentness with authority, mode, role, scope, or persistent storage.

It promotes reusable results from GCS PR #36 and PR #38:

```text
A single shared frame_id = current row is unsafe when multiple sessions write to
the same frame store.

session_id + frame_id preserves each session's current frame.

previous_session_id -> current_session_id is handoff evidence only.

session IDs are evidence coordinates only.

checkpoint_ref records checkpoint evidence separately from session identity.
```

The promoted contract is not the GCS proof implementation and does not make a
SQLite schema, file layout, storage backend, or project-specific session token
mandatory.

## Core Declaration

```text
EXECUTABLE RUNTIME CURRENTNESS KEY = SESSION_ID + FRAME_ID.

SESSION ID IS AN EVIDENCE COORDINATE ONLY.

MODE CURRENT ANCHOR KEY = MODE.

MODE IS NOT EXECUTABLE RUNTIME CURRENTNESS IDENTITY.

PREVIOUS_SESSION_ID -> CURRENT_SESSION_ID IS HANDOFF EVIDENCE ONLY.

CHECKPOINT_REF IS SEPARATE FROM SESSION IDENTITY.

FRAME STORE IS CACHE, NOT AUTHORITY.

GIT-BACKED SOURCE REMAINS AUTHORITY.

RUNTIME ANCHOR FRAME RECORDS CURRENTNESS.
RUNTIME ANCHOR FRAME DOES NOT CREATE AUTHORITY.

MODE ANCHOR STORE IS OPERATIONAL TRUTH FOR MODE CURRENT ANCHOR.
session.md / current_anchor_frame.md ARE COMPANION PROJECTIONS.
HOST_STATE_PROJECTION AND SESSION_HANDOFF ARE NOT EXECUTION MUTATION.

CURRENT ANCHOR PHYSICAL TIME MOVES FORWARD WITH HOST-OBSERVED USER INPUT.
TIME PASSAGE ALONE DOES NOT CREATE STALE.

COMMANDER SURFACE IS NOT EXECUTION SURFACE.
REPOSITORY LOCATION IS DISTINCT FROM COMMAND ORIGIN.
TRANSPORT EVIDENCE DOES NOT CREATE AUTHORITY OR CURRENTNESS.
TRANSPORT RECONNECT ALONE MUST NOT CHANGE CURRENT ANCHOR.
TRANSPORT RECONNECT ALONE MUST NOT CHANGE SESSION_ID + FRAME_ID.
```

## Currentness Key

A runtime frame is current only within a session-scoped key:

```text
session_id + frame_id
```

Recommended active frame key:

```yaml
session_id: <current-session-id>
frame_id: current
```

This is valid because `frame_id: current` is scoped by `session_id`.

Invalid shared-store key:

```yaml
frame_id: current
```

A single global `current` row is unsafe when more than one session can write or
read the same frame store.

## Session ID Format

Runtime Anchor Frame session IDs SHOULD use a compact coordinate format:

```text
<NODE>-<MODE>-<YYYYMMDD>-<SESSION_LOCATION>-<SEQ>
```

Example shape:

```text
<PROJECT>-<MODE>-20260705-<RUNTIME-WRITER-SURFACE>-001
```

The format is a currentness and restore coordinate. It is not authority.

Project-specific location tokens are implementation details. Attached projects
may use local names such as `LOCAL-CODEX`, `MOBILE-GPT`, or `WEB-GPT`, but
ai-career Core only requires that the token identify the runtime writer /
execution surface consistently enough for restore comparison.

## Runtime Frame Shape

The canonical runtime frame shape for currentness templates is:

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
  previous_session_id: <previous-or-null>
  current_session_id: <current-session-id>
  checkpoint_ref: <checkpoint-path-or-null>
  validation_ref: <validation-path-or-null>
  authority: <UNASSIGNED | assigned | unknown>
  source_ref: <git-backed-source>
```

## MUST

```text
currentness key = session_id + frame_id
session_id must remain evidence coordinate only
mode must not be used as currentness identity
previous_session_id -> current_session_id is handoff evidence only
checkpoint_ref must remain separate from session identity
frame store is cache, not authority
Git-backed source remains authority
same-Anchor user input updates observed_at only
time passage alone must not create STALE
replaced Anchors must remain Beyond footprints
selected restore candidates must pass OS_STATUS or OS_VALIDATE before continuation
transport-only events must not advance Current Anchor observed_at
transport reconnect must not change Current Anchor or session_id + frame_id
Commander Surface changes must remain separate from execution and repository coordinates
```

## Session Surface Coordinates

`session_location` and the location token inside `current_session_id` identify
the runtime writer / execution surface that owns the repository frame update.

They do not necessarily identify the user's chat device.

When the Commander speaks from one surface but repository files are written by
another, record them separately:

```yaml
session_location: <runtime-writer-surface>
commander_surface: <user-interaction-surface-or-UNKNOWN>
execution_surface: <runtime-writer-surface>
repository_location: <repo-host-or-UNKNOWN>
```

These are separate evidence coordinates. Equal observed values do not collapse
one coordinate into another or allow one coordinate to prove another.

```text
commander_surface
  -> where Commander input was observed

execution_surface
  -> where bounded execution can be attempted

repository_location
  -> where repository state is hosted or mutated

transport_state
  -> connection evidence recorded outside the Current Anchor snapshot
```

Generic example:

```yaml
session_location: <runtime-writer-surface-or-UNKNOWN>
commander_surface: <commander-interaction-surface-or-UNKNOWN>
execution_surface: <runtime-execution-surface-or-UNKNOWN>
repository_location: <repository-host-or-UNKNOWN>
```

`commander_surface` is context evidence.

It is not authority.

It must not replace `session_location` in the currentness key.

## Cross-Host Task Dispatch

The interaction carrier, execution Host, and write target are independent
planes. `SOURCE_ATTACH` describes a source provider; it does not prove that an
execution Host is absent or that a write target is unavailable. Record this
topology only when dispatching bounded work to a Task Frame or establishing an
Execution Binding. It is not part of the Mode Current Anchor and does
not create authority, scope, Assignment, or permission to mutate source.

Transport attach, detach, reconnect, route change, or UI handoff may refresh
connection evidence. Without Host-observed user input, transport evidence must
not:

```text
advance Current Anchor observed_at
change anchor_id
change session_id + frame_id
change authority or execution_assignment
```

When an actual user input is observed from a different Commander Surface, the
Current Anchor may update only `commander_surface` and `observed_at`. It must
not infer or rewrite `session_location`, `execution_surface`,
`repository_location`, session/frame/anchor identity, authority, or assignment.

A Commander Surface change does not grant authority. Existing approval or
execution-binding evidence may require renewed Pre-Execution Verification when
that evidence was bound to the previous Commander Surface.

## MUST NOT

```text
use single global frame_id=current for a shared multi-session store
treat session_id as authority
treat checkpoint_ref as authority
treat mode as currentness identity
treat role as currentness identity
treat scope as currentness identity
treat anchor state as authority
treat mode / role / scope / anchor state as authority
continue from a selected restore candidate without OS_STATUS or OS_VALIDATE
require SQLite as the only implementation
use elapsed wall-clock time alone to mark an Anchor STALE
reactivate a Beyond Anchor instead of creating a new Current Anchor
use transport presence or reconnect as authority, currentness, or assignment evidence
infer execution_surface or repository_location from commander_surface
```

## Anchor Temporal Coordinate

`ANCHOR_TEMPORAL_COORDINATE.md` defines the physical-time behavior inside the
session-scoped currentness key.

```text
Session Currentness
  -> identifies the current frame with session_id + frame_id

Anchor Temporal Coordinate
  -> advances the same Current Anchor's observed_at with Host physical time
  -> preserves replaced Anchors as Beyond footprints
  -> creates a new Current Anchor after adopted recall
```

An observed user input is a Host-time observation, not semantic adoption. When
the same Anchor remains current, the input changes `observed_at` only.

```text
elapsed time alone                  -> no STALE transition
source/session/frame mismatch       -> RECHECK_REQUIRED
source-backed replacement           -> old Anchor STALE as execution coordinate
missing or conflicting evidence     -> UNKNOWN
```

Anchor temporal fields do not create authority, execution assignment, write
permission, checkpoint adoption, or Resume activation.

## Mode Boundary

Mode is a behavior contract, not a currentness identity.

Same-mode continuity can be valid when a session reads its own frame.

Same mode across sessions is not enough to prove currentness continuity.

## Handoff Evidence

`previous_session_id -> current_session_id` records continuity handoff.

It does not grant authority.

It does not prove that the current session may execute.

Recording handoff evidence and projecting Mode Anchor companions
(`HOST_STATE_PROJECTION`) are Runtime-owned operational state. They do not
require Execution Guard, Task Assignment, or a Mutation Receipt. See
`.ai/skills/common/host-state-projection/SKILL.md`.

Before **project-owned** mutation or command execution, the runtime must still
validate:

```text
source_ref
anchor
authority
scope
pre-execution verification
```

## Checkpoint Reference Boundary

`checkpoint_ref` records where checkpoint evidence can be found.

It is separate from session identity:

```text
session_id      -> currentness / restore coordinate
checkpoint_ref  -> checkpoint evidence pointer
```

A checkpoint reference may help locate restore evidence. It does not authorize
mutation, command routing, source edits, execution assignment, checkpoint
loading, or resume by itself.

## Checkpoint Storage Compatibility

Core does not require one checkpoint storage backend.

A compatible project may expose:

```text
checkpoint_ref -> snapshot.yaml
```

or another durable reference form, as long as the runtime can resolve it during
OS_STATUS / OS_VALIDATE and can distinguish snapshot evidence from full resume
material.

GCS validates one compatible model:

```text
Snapshot-first operation:
  current_anchor_frame.md
  session.md
  checkpoint_ref -> snapshot.yaml

Resume-on-demand operation:
  snapshot.yaml
  summary.md
  decisions.md
  open_issues.md
  next_actions.md
  touched_files.md
  archive when needed
```

The promoted project Runtime model separates Git-tracked checkpoint evidence
from mutable current session coordinates:

```text
Git-tracked checkpoint evidence: .ai/runtime/project_instance/checkpoints/<SESSION_ID>/
Mutable current coordinates: .ai/runtime/state/
```

This validates the common boundary:

```text
checkpoint_ref points to restore evidence.
checkpoint_ref does not restore automatically.
Resume Bundle loads only after approval or explicit request.
Execution authority remains separate.
```

## Runtime Resume SSOT

```text
CHECKPOINT and RESUME durability
  -> .ai/runtime/continuity/continuity.sqlite

File-tree Resume Archive (.ai/resume/**)
  -> DEPRECATED for runtime restore identity
  -> keep in place; do not delete solely for deprecation
```

See `.ai/templates/runtime_continuity/README.md` and the resume-save /
resume-restore Skills.

## Restore Candidate Selection

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
continuing work.

If node/mode is missing, stale, conflicted, or inferred only from conversation
context, report `ANCHOR_FRAME_REQUIRED` or `UNKNOWN` rather than continuing.

## Resume Candidate Fast Path

When a project snapshot exposes both handoff evidence and `checkpoint_ref`, the
runtime should offer a lightweight Resume Candidate before loading the full
checkpoint bundle.

Fast-path inputs:

```text
.ai/runtime/state/session.md
.ai/runtime/state/current_anchor_frame.md
checkpoint_ref
previous_session_id
current_session_id
git status
validation/latest when needed
```

Expected flow:

```text
Read runtime state snapshot
  -> detect previous_session_id -> current_session_id handoff evidence
  -> detect checkpoint_ref
  -> confirm Node / Mode / Anchor / State / Authority / Execution Assignment
  -> show Resume Candidate Proposal
  -> wait for Commander approval
  -> only after approval, load checkpoint bundle
```

Required proposal shape:

```text
Resume Candidate Found

Previous Session: <previous_session_id>
Current Session: <current_session_id>
Checkpoint: <checkpoint_ref>
Anchor: <anchor_id>
State: <state>
Validation: <PASS | PARTIAL | FAIL | UNKNOWN>
Authority: <UNASSIGNED | ...>
Execution Assignment: <UNASSIGNED | ...>

Restore candidate?
```

The proposal is a context-saving index.

It must not load the full checkpoint bundle unless the Commander approves or
the user explicitly asks for details.

Resume Candidate Proposal does not create authority, execution assignment, or
file modification approval.

## Frame Store Boundary

A frame store may be implemented as:

```text
Markdown
YAML
JSON
SQLite
runtime memory
other project-local storage
```

SQLite may be useful for proof or implementation, but it is not required by the
contract.

Frame store rule:

```text
FRAME STORE IS CACHE.
GIT-BACKED SOURCE IS AUTHORITY.
```

If frame store and Git-backed source disagree, Git-backed source wins.

If the disagreement cannot be safely resolved, report `UNKNOWN`.

## Template Requirement

Attached project runtime state templates should implement session-scoped
currentness when they expose runtime frame data.

Required template surfaces:

```text
.ai/templates/runtime_state/session.md
.ai/templates/runtime_frame/README.md
```

Project-local implementation may vary, but OS_UPDATE / OS_VALIDATE must be able
to report whether the project implements, defers, or cannot determine:

```text
session_id + frame_id currentness key
entered_at / observed_at / state_updated_at / validated_at temporal meanings
same-Anchor input advances observed_at only
time passage alone does not create STALE
Beyond recall creates a candidate and adopted recall creates a new Current Anchor
transport evidence remains outside the Current Anchor snapshot
Commander input from a new surface changes only commander_surface and observed_at
session ID format as an evidence coordinate
previous_session_id -> current_session_id handoff evidence
checkpoint_ref separate from session identity
checkpoint_ref resolvable as snapshot or durable checkpoint evidence
same node + same mode restore candidate selection
selected candidate OS_STATUS / OS_VALIDATE before continuation
mode is not currentness identity
frame store is cache, not authority
Git-backed source remains authority
```

## OS_UPDATE Requirement

OS_UPDATE must compare attached project runtime state or frame surfaces against
this contract when the project records current runtime frame state.

Required OS_UPDATE checks:

```text
1. Is SESSION_CURRENTNESS.md visible in the active core surface registry?
2. Is the runtime state template visible as a registered contract template?
3. Does the project use session_id + frame_id as the currentness key?
4. Does the project avoid single global frame_id=current in shared stores?
5. Does the project avoid using mode as currentness identity?
6. Does the project record handoff as evidence only?
7. Does the project separate checkpoint_ref from session identity?
8. Does the project expose checkpoint_ref as resolvable snapshot or durable checkpoint evidence?
9. Does the project select restore candidates only within same node + same mode?
10. Does selected restore continuation require OS_STATUS or OS_VALIDATE?
11. Does the project treat frame store as cache, not authority?
12. Does the project keep Git-backed source as authority?
13. Does the project preserve Anchor temporal field meanings?
14. Does same-Anchor input advance observed_at only?
15. Does elapsed time avoid creating STALE by itself?
16. Does adopted Beyond recall create a new Current Anchor?
17. Do transport-only events preserve Current Anchor identity and observed_at?
18. Does a Commander Surface change avoid rewriting execution and repository coordinates?
19. Does OS_VALIDATE record evidence for this comparison?
```

If the project has frame-currentness surfaces but they were not compared,
OS_VALIDATE should report `PARTIAL` or `UNKNOWN`, not `PASS`.

## Relationship To Node / Mode Coordinate Contract

`NODE_MODE_COORDINATE_CONTRACT.md` defines coordinate meaning.

This document defines currentness identity.

```text
Node / Mode Coordinate Contract
  -> Host / Node / Mode / Role / Scope meaning

Session Currentness
  -> session_id + frame_id currentness key
```

Mode must not be used as currentness identity.

## Relationship To Session Framework

Session Framework starts, rebuilds, switches, and ends session lifecycle.

Session Currentness provides the session-scoped frame key used by runtime state
templates during boot, reboot, mode change, handoff, and status reporting.

`ANCHOR_TEMPORAL_COORDINATE.md` defines how the Anchor under that key advances
with physical Runtime observation time.

## Relationship To Persistence Model

Persistence may preserve frame data.

Persistence does not make the frame authoritative.

The frame store is a cache and currentness index.

## Validation Questions

OS_VALIDATE should answer:

```text
Is SESSION_CURRENTNESS.md registered as an active core surface?
Is .ai/templates/runtime_state/session.md registered as a contract template?
Is .ai/templates/runtime_frame/README.md registered as a contract template?
Does the project use session_id + frame_id for currentness?
Does the project avoid a single global frame_id=current in shared stores?
Does the project avoid mode-as-currentness identity?
Is session_id evidence coordinate only?
Is previous_session_id -> current_session_id evidence only?
Is checkpoint_ref separate from session identity?
Is checkpoint_ref resolvable as snapshot or durable checkpoint evidence?
Are restore candidates selected only inside same node + same mode?
Does selected restore continuation require OS_STATUS or OS_VALIDATE?
Is frame store treated as cache?
Is Git-backed source still authority?
Does the Current Anchor expose the required temporal field meanings?
Does same-Anchor input change observed_at only?
Does time passage alone avoid creating STALE?
Does Beyond recall remain candidate-only until a new Current Anchor is adopted?
Do transport-only events preserve Current Anchor identity and observed_at?
Does Commander Surface remain separate from Execution Surface and Repository Location?
```

## Adoption Status

This is a candidate core runtime contract.

It promotes GCS PR #36 as reusable runtime currentness guidance without
promoting GCS-specific SQLite schema as mandatory implementation.

It adopts GCS PR #38 as common Runtime Anchor Frame session coordinate guidance
without promoting GCS-specific session location tokens or project-local runtime
state values as mandatory implementation.

It records GCS Resume Manager / Session Checkpoints as compatible checkpoint
storage evidence without requiring GCS's file-backed bundle layout as the only
valid checkpoint storage backend.
