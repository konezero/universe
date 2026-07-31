# Runtime State Session Snapshot Template

Status: Candidate Template
Template Family: `runtime_state`
Target Implementation Path: `.ai/runtime/state/session.md`
Scope: project-local runtime state snapshot
Owner: attached project
Consumer: ai-career Runtime commands, OS_STATUS, OS_UPDATE, boot fast path

## Purpose

This template defines the canonical shape for a short project-local runtime state snapshot.

The snapshot is a fast-path index for the current project session runtime state.

It lets a session read one small file before deciding whether to fetch heavier boot, role, scope, validation, or project files.

## Core Rule

```text
ai-career defines the snapshot contract.
The project implements the snapshot.
```

The snapshot is not the source of truth for every field.

It is an index that points to source-backed evidence.

```text
Snapshot is fast path.
Evidence is proof.
```

## Required Fields

```yaml
project:
  id: <project-id>
  repository: <owner/repo-or-local-id>
  node: <node-or-topic>

runtime:
  session_runtime:
    state: READY | PARTIAL | NOT_READY | UNKNOWN
    evidence: <path-or-note>
  repository_runtime:
    state: VERIFIED | PARTIAL | NOT_VERIFIED | NOT_ATTACHED | UNKNOWN
    runtime_root: <path-or-null>
    validation_evidence: <path-or-null>

session:
  session_id: <current-session-id>
  previous_session_id: <previous-session-id-or-null>
  current_session_id: <current-session-id>
  checkpoint_ref: <checkpoint-path-or-null>
  session_location: <runtime-writer-surface>
  commander_surface: <user-interaction-surface-or-UNKNOWN>
  execution_surface: <runtime-writer-surface>
  repository_location: <repo-host-or-UNKNOWN>
  role: <role>
  mode: <mode>
  display_mode: <user-facing-mode>
  authority: ASSIGNED | UNASSIGNED | APPROVED | BLOCKED | UNKNOWN
  execution_assignment: <task-id-or-UNASSIGNED>

transport:
  state: ATTACHED | DETACHED | RECONNECTED | ROUTE_CHANGED | UNKNOWN
  observed_at: <host-observation-time-or-UNKNOWN>
  evidence_ref: <host-evidence-ref-or-UNKNOWN>
  authority: false

provider_session_connection:
  target_ref: <connection-target-or-UNKNOWN>
  last_provider: <provider-or-UNKNOWN>
  last_session_ref: <provider-session-ref-or-UNKNOWN>
  requested_mode: <mode-intent-or-UNKNOWN>
  state: NEW | REPLACED | REUSED | UNKNOWN
  greeting_required: true | false | UNKNOWN
  persistence: LAST_COORDINATE | EPHEMERAL | UNKNOWN
  authority: false

session_preparation:
  state: PREPARED | REHYDRATED | UNKNOWN
  mode: <selected-mode-or-UNKNOWN>

mode_current_anchor:
  mode: <selected-mode-or-UNKNOWN>
  anchor_id: <mode-current-anchor-or-UNKNOWN>
  state: CURRENT | UNKNOWN
  observed_at: <last-accepted-input-time-or-UNKNOWN>
  mode_registry_revision: <positive-integer-or-UNKNOWN>
  mode_registry_digest: <sha256-or-UNKNOWN>
  mode_definition_digest: <sha256-or-UNKNOWN>
  snapshot_ref: <mode-anchor-store-reference-or-UNKNOWN>
  authority: false

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
  session_location: <runtime-writer-surface>
  commander_surface: <user-interaction-surface-or-UNKNOWN>
  execution_surface: <runtime-writer-surface>
  repository_location: <repo-host-or-UNKNOWN>
  authority: <UNASSIGNED | assigned | unknown>
  source_ref: <git-backed-source>
  source_commit: <git-commit-or-UNKNOWN>

runtime_image:
  status: assembled | absent | stale | unknown
  activation_status: requested | assembled | active | stale | unknown
  assembly_profile: memory | markdown | yaml | json | sqlite_memory | sqlite_file | file_cache | other | unknown
  evidence_ref: <path-or-null>
  session_artifact_ref: <path-or-in-memory-coordinate-or-null>
  source_ref: <git-backed-source-or-UNKNOWN>
  source_commit: <git-commit-or-UNKNOWN>
  source_digest: <digest-or-UNKNOWN>
  host_action: SESSION_ATTACH | LOCAL_INSTALL_OR_ATTACH | SOURCE_ATTACH | UNKNOWN
  authority: false
  verification_status: SOURCE_READY | VERIFIED | PARTIAL | FAIL | UNKNOWN
  validation_ref: <path-or-null>
  checked_at: <timestamp-or-null>

authority_certificate:
  status: current | stale | missing | unknown
  certificate_ref: <path-or-null>
  session_id: <session-id-or-UNKNOWN>
  session_location: <runtime-writer-surface-or-UNKNOWN>
  execution_surface: <runtime-writer-surface-or-UNKNOWN>
  repository_location: <repo-host-or-UNKNOWN>
  anchor_id: <anchor-or-UNKNOWN>
  runtime_frame_id: <frame-id-or-UNKNOWN>
  source_commit: <git-commit-or-UNKNOWN>
  execution_assignment: <task-id-or-UNASSIGNED>

anchor:
  project_anchor: READY | PARTIAL | NOT_READY | UNKNOWN
  anchor_source: <path-or-null>

scope:
  load_scope: <path-or-null>
  write_scope: <path-or-null>
  current_rules: <path-or-null>

validation:
  latest: <path-or-null>
  result: PASS | PARTIAL | FAIL | UNKNOWN
  contract_sha: <sha-or-null>
  checked_at: <timestamp-or-null>

working_tree:
  state: clean | dirty | unknown | unavailable
  checked_at: <timestamp-or-null>
  note: <text-or-null>

pending:
  memory_sync: none | pending | flushed | unknown
  update: none | pending | reboot_required | unknown
  proposal: none | pending | unknown

updated_at: <timestamp>
updated_by: <role-or-agent>
```

## Minimal Markdown Implementation

Projects may implement the snapshot as Markdown if YAML tooling is not available.

Example:

```text
# Session Runtime State

Project: GCS
Repository: konezero/gcs
Node: GCS

Session Runtime: READY
Repository Runtime: VERIFIED
Runtime Root: .ai/runtime/project_instance/
Validation Evidence: .ai/runtime/project_instance/validation/latest.md

Role: MASTER
Mode: MASTER
Mode Scope: architecture/governance
Display Mode: MASTER
Session ID: <current-session-id>
Previous Session ID: <previous-or-null>
Current Session ID: <current-session-id>
Checkpoint Ref: <checkpoint-path-or-null>
Session Location: <runtime-writer-surface-or-UNKNOWN>
Commander Surface: <commander-interaction-surface-or-UNKNOWN>
Execution Surface: <runtime-execution-surface-or-UNKNOWN>
Repository Location: <repository-host-or-UNKNOWN>
Authority: UNASSIGNED
Execution Assignment: UNASSIGNED

Transport State: <ATTACHED | DETACHED | RECONNECTED | ROUTE_CHANGED | UNKNOWN>
Transport Observed At: <host-observation-time-or-UNKNOWN>
Transport Evidence: <host-evidence-ref-or-UNKNOWN>
Transport Authority: false

Provider Session Target: <connection-target-or-UNKNOWN>
Last Provider: <provider-or-UNKNOWN>
Last Provider Session Ref: <provider-session-ref-or-UNKNOWN>
Requested Mode: <mode-intent-or-UNKNOWN>
Provider Session Connection State: <NEW | REPLACED | REUSED | UNKNOWN>
Provider Session Greeting Required: <true | false | UNKNOWN>
Provider Session Persistence: <LAST_COORDINATE | EPHEMERAL | UNKNOWN>
Provider Session Authority: false

Runtime Frame:
  session_id: <current-session-id>
  frame_id: current
  node: GCS
  mode: MASTER
  role: MASTER
  mode_scope: architecture/governance
  anchor_id: <anchor>
  state: READY
  entered_at: <anchor-creation-time>
  observed_at: <last-current-anchor-observation-time>
  state_updated_at: <last-semantic-state-transition-time>
  validated_at: <last-source-backed-validation-time-or-empty>
  state_origin: current_session
  state_freshness: current
  previous_session_id: <previous-or-null>
  current_session_id: <current-session-id>
  checkpoint_ref: <checkpoint-path-or-null>
  validation_ref: .ai/runtime/project_instance/validation/latest.md
  session_location: <runtime-writer-surface-or-UNKNOWN>
  commander_surface: <commander-interaction-surface-or-UNKNOWN>
  execution_surface: <runtime-execution-surface-or-UNKNOWN>
  repository_location: <repository-host-or-UNKNOWN>
  authority: UNASSIGNED
  source_ref: <git-backed-source>
  source_commit: <git-commit-or-UNKNOWN>

Runtime Image:
  status: assembled | absent | stale | unknown
  activation_status: requested | assembled | active | stale | unknown
  assembly_profile: memory | markdown | yaml | json | sqlite_memory | sqlite_file | file_cache | other | unknown
  evidence_ref: <path-or-null>
  session_artifact_ref: <path-or-in-memory-coordinate-or-null>
  source_ref: <git-backed-source-or-UNKNOWN>
  source_commit: <git-commit-or-UNKNOWN>
  source_digest: <digest-or-UNKNOWN>
  host_action: SESSION_ATTACH | LOCAL_INSTALL_OR_ATTACH | SOURCE_ATTACH | UNKNOWN
  authority: false
  verification_status: SOURCE_READY | VERIFIED | PARTIAL | FAIL | UNKNOWN
  validation_ref: <path-or-null>
  checked_at: <timestamp-or-null>

Authority Certificate:
  status: current | stale | missing | unknown
  certificate_ref: <path-or-null>
  session_id: <session-id-or-UNKNOWN>
  session_location: <runtime-writer-surface-or-UNKNOWN>
  execution_surface: <runtime-writer-surface-or-UNKNOWN>
  repository_location: <repo-host-or-UNKNOWN>
  anchor_id: <anchor-or-UNKNOWN>
  runtime_frame_id: <frame-id-or-UNKNOWN>
  source_commit: <git-commit-or-UNKNOWN>
  execution_assignment: <task-id-or-UNASSIGNED>

Project Anchor: READY
Anchor Source: .ai/runtime/project_instance/project_anchor.md

Scope Policy: .ai/runtime/project_instance/scope_policy.md
Current Rules: source-backed Core plus this current session state

Latest Validation: PASS
Contract SHA: <sha>
Checked At: <timestamp>

Working Tree: clean
Working Tree Checked At: <timestamp>

Pending Memory Sync: none
Pending Update: none
Pending Proposal: none

Updated At: <timestamp>
Updated By: <role-or-agent>
```

## Fast Path Usage

A runtime session may use this file as the first read after project attach or reboot.

Expected flow:

```text
Read .ai/runtime/state/session.md
  -> resolve the selected Mode's Current Anchor
  -> resolve session_id + frame_id only for executable Runtime currentness
  -> confirm project / role / authority / validation pointers
  -> detect previous_session_id -> current_session_id handoff evidence
  -> detect checkpoint_ref when present
  -> offer lightweight Resume Candidate before reading checkpoint bundle
  -> fetch validation/latest only when needed
  -> fetch checkpoint bundle only after Commander approval or explicit detail request
  -> fetch role/scope docs only when needed
  -> enter task frame
```

Resume candidate fast-path report:

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

## Safety Rules

```text
Do not treat snapshot fields as proof without evidence paths when verification matters.
Do not report repository runtime as VERIFIED unless validation evidence exists and passes.
Do not treat role readiness as execution authority.
Do not use mode as executable Runtime currentness identity.
Do not use session_id as Mode Current Anchor identity.
Do not use a single global frame_id=current for a shared multi-session frame store.
Do not treat previous_session_id -> current_session_id as authority.
Do not treat a frame store as authority.
Do not require SQLite as the only frame-store implementation.
Do not treat a durable Current/Beyond Anchor store as an executable Runtime or authority.
Do not load the full checkpoint bundle before Resume Candidate approval unless details are explicitly requested.
Do not start source mutation when execution_assignment is UNASSIGNED.
Do not silently ignore dirty working tree state.
Do not treat Runtime Image as authority.
Do not require SQLite as the only Runtime Image implementation.
Do not treat Runtime Authority Certificate presence as execution authority.
Do not mutate from stale Runtime Image or stale Authority Certificate.
Do not report SESSION_BOOT_IMAGE_CREATED without an assembled artifact or profile coordinate.
Do not report repository runtime surface as VERIFIED without validation evidence.
Do not report <MODE>_ACTIVE without source-backed mode authority and applicable approval.
Do not treat mode request or mode selection as mode activation.
Do not mark a Current Anchor STALE from elapsed time alone.
Do not reactivate a Beyond Anchor; create a new Current Anchor after adoption.
Do not treat transport presence or reconnect as authority or currentness evidence.
Do not infer Execution Surface or Repository Location from Commander Surface.
```

Currentness rules:

```text
Mode Current Anchor key = mode
executable Runtime currentness key = session_id + frame_id
same-Anchor user input updates observed_at only
Host physical observation time must not move backward
time passage alone does not create STALE
replaced Anchors remain Beyond footprints
adopted recall creates a new Current Anchor
the installed Reference Runtime persists Current/Beyond Anchor coordinates in a project-local file store; that store contains raw coordinates and candidates only
transport-only events leave the Current Anchor snapshot and observed_at unchanged
Commander input from a new surface changes only commander_surface and observed_at
Commander Surface and Execution Surface remain distinct coordinates
previous_session_id -> current_session_id is handoff evidence only
frame store is cache, not authority
Runtime Image is boot artifact, not authority
Authority Certificate carries context, not execution authority
Git-backed source remains authority
```

## Refresh Triggers

A project should refresh this snapshot when:

```text
OS_INSTALL completes durable Project Runtime installation or verification
Mode alignment may create or replace that Mode's Current Anchor
EXECUTABLE_RUNTIME_START creates or recreates the executable Session Runtime
OS_UPDATE completes or detects no patch needed
OS_VALIDATE records new evidence
Mode / Role / Display Mode changes
Execution Assignment changes
Working tree state is checked
Memory Sync flush state changes
REBOOT activates a new Base / Runtime frame
Host observes a new user input for the same Current Anchor
Host observes transport attach, detach, reconnect, route change, or UI handoff
topic transition or explicit realignment replaces the Current Anchor
Parent / User adopts a Beyond recall candidate
```

## Source-Only OS_STATUS

This project snapshot may be read through a GitHub, mobile, web, or other
source-only attachment. In that case it is an observed reference, not current
Host Runtime evidence.

```yaml
status: SOURCE_READY
checkpoint:
  status: OBSERVED_REFERENCE | NOT_OBSERVED
resume_restore:
  status: NOT_PERFORMED
validation:
  status: NOT_RUN
mode_current_anchor: UNKNOWN
session_runtime: UNKNOWN
repository_runtime: UNKNOWN
executable_runtime_currentness: UNKNOWN
authority: UNASSIGNED
execution_assignment: UNASSIGNED
```

Do not promote values stored in this snapshot, a checkpoint, a Resume Archive,
a validation file, or a Runtime Image document into current state without the
separate Host evidence for that operation.

## Relationship To ai-career Core

This template relates to:

```text
SESSION_RUNTIME_GOVERNANCE.md
INTENT_FIRST_ROUTING_GATE.md
SESSION_CURRENTNESS.md
ANCHOR_TEMPORAL_COORDINATE.md
RUNTIME_STATE_TRUST_GATE.md
RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md
RUNTIME_AUTHORITY_EXECUTION_BINDING.md
NODE_MODE_COORDINATE_CONTRACT.md
RUNTIME_STATUS_SOURCE_RULE.md
RUNTIME_COMMANDS.md
RUNTIME_INSTRUCTION_SET.md
PROJECT_RUNTIME_INSTALL.md
OS_VALIDATION_EVIDENCE.md
```

## Status

```text
Template status: candidate
Implementation owner: project
Canonical project path: .ai/runtime/state/session.md
```
