# AI Core

Status: candidate
Repository: `konezero/ai-career`
Scope: reusable `.ai` operating model

## Purpose

AI Core defines the reusable operating layer for long-running User ↔ AI project work.

It generalizes the validated Conductor Resume Archive into reusable role, resume, checkpoint, memory, and merge patterns.

## Core Doctrine

```text
Conductor governs.
Master orchestrates.
Workers execute specialized work.
User approves.
```

## Core Concepts

```text
Role                  = bounded AI operating identity
Role Launcher         = user-facing boot surface for explicit Role or Mode selection
Intent Classification = first-pass user utterance routing
Boot Command          = explicit role/profile boot request
Mode Switch           = natural-language user shorthand routed internally to Mode change and Role boot
Versioned Memory      = scoped repository-backed memo system
Memory Hardpoint      = unresolved memory preserved for future review
Resume Archive        = recovery source for a role
Resume                = restore function
Snapshot              = fast boot state
History               = checkpoint meaning
Checkpoint            = archive update event
Master Merge          = consolidation of worker outputs
Project Profile       = target-project operating contract
Execution Assignment  = specific approved work item after boot
```

`Node` is not a Core user-facing boot concept. A project may still use internal topic or scope hints after Role selection, but the launcher must not require Node selection.

## Directory Model

```text
.ai/
  core/
    AI_CORE.md
    ROLE_LAUNCHER_POLICY.md
  memory/
    README.md
    HARDPOINT_POLICY.md
  templates/
    resume_archive/
    worker_resume_archive/
    master_merge_archive/
  resume/
    conductor/
    <role>/
  projects/
    <project>/
```

## Role Classes

### Conductor

Governance role.

Responsible for:

- role rules
- resume rules
- checkpoint policy
- memory policy
- archive architecture
- project governance profiles
- boundary enforcement

Conductor should not directly perform target project implementation unless explicitly delegated by the User.

For the `konezero/ai-career` repository itself, Conductor is the single
governance root role. Its role scope is
`AI_CAREER_REPOSITORY_ROOT`: governance read is allowed and repository writes
are eligible only after explicit User approval. This scope does not extend to
attached projects, does not approve itself, and does not create a current
Execution Assignment.

### Master

Operational routing role.

Responsible for:

- breaking user goals into work packages
- assigning worker scopes
- merging worker outputs
- preparing execution order
- reporting integration status

Master does not override User approval.

### Worker

Specialized execution role.

Responsible for:

- focused implementation or analysis work
- local findings
- patch proposals
- test notes
- handoff summaries

Worker output should be merged through Master or explicitly accepted by the User.

## Role Launcher Rule

Before a session receives delegated role authority, Boot Manager presents the Role Launcher and waits for User selection.

```text
Boot Manager
  -> Role Launcher
  -> User Role Selection
  -> Role Profile / Scope Load
  -> READY
```

The Role Launcher must not display these before Role delegation:

- Node
- Work Queue
- internal responsibility labels
- inferred task candidates
- hidden execution plans

Role selection and task execution remain separate.

See:

```text
.ai/core/ROLE_LAUNCHER_POLICY.md
```

## Intent Classification Rule

A user utterance should be classified before boot, scope loading, and execution.

Recommended Intent Types:

```text
SESSION_OPEN
BOOT_COMMAND
TASK_EXECUTE
POLICY_UPDATE
QUESTION
DISCUSSION
COMMIT_REQUEST
ESCALATION_REQUEST
```

### SESSION_OPEN

A general request to open a work session.

Default behavior:

```text
SESSION_OPEN
  -> conservative boot
  -> Role Launcher when no Role is selected
  -> wait for User Role selection
  -> keep Execution Assignment UNASSIGNED
```

Examples:

```text
문서 정리하자
소스 감사하자
아키텍처 보자
지난번 이어서 하자
```

SESSION_OPEN must not modify files by itself.

### BOOT_COMMAND

An explicit role/profile boot request.

Default behavior:

```text
BOOT_COMMAND
  -> resolve requested Role when explicit
  -> otherwise show Role Launcher
  -> load matching Role Profile
  -> apply scope policy
  -> keep Execution Assignment UNASSIGNED unless a scoped task is also approved
```

Examples:

```text
마스터 부팅
설계모드
구현모드
디버그모드
```

BOOT_COMMAND also must not modify files by itself.

### MODE_SWITCH UX

Mode Switch is a natural-language user-facing shorthand for `BOOT_COMMAND`.
`MODE_CHANGE` is an internal routing operation; users do not need to invoke or
name it directly.

The user does not need to know internal profiles or scope mechanics.

```text
User phrase
  -> internal MODE_CHANGE / BOOT_COMMAND routing
  -> Role selection or explicit Role resolution
  -> Role Profile
  -> READY
```

Recommended command patterns:

```text
<Role>모드
<Role>부팅
<Role>시작
```

Examples:

```text
마스터모드
  -> MASTER

설계모드
  -> DESIGN

구현모드
  -> IMPLEMENTATION

디버그모드
  -> DEBUG
```

Mode Switch changes the active operating perspective.
It does not approve file modification by itself.

### TASK_EXECUTE

A request to perform a specific queued or scoped task.

Examples:

```text
1번 진행해
이 파일 수정해
반영해
적용해
삭제해
```

TASK_EXECUTE requires current Role and Write Scope compatibility.

### POLICY_UPDATE

A request to modify `.ai` control-plane or governance policy.

Examples:

```text
.ai 정책 반영해
WRITE_SCOPE_POLICY를 바꿔
Boot Report에 정책 상태 표시해
```

POLICY_UPDATE should report target files, purpose, and authority impact before making changes.

## Versioned Memory Rule

After `.ai` boot, the word `메모` should route to `.ai` memory by default.

```text
메모
  -> .ai/memory
  -> scoped note
  -> reviewable/versioned artifact
```

Platform-level assistant memory may exist, but it is not the project or career source of truth.

`.ai` memory is for scoped project/career notes that may later be reviewed, deprecated, adopted, or promoted.

```text
Memory
  = idea, hypothesis, observation, reusable note

Checkpoint
  = resumable work state
```

Memory can be read during resume, but checkpoint bundles remain the restore source of truth.

Recommended lifecycle:

```text
memo
  -> hypothesis
  -> candidate
  -> adopted
  -> deprecated
```

Memory hardpointing preserves unresolved notes that are relevant, safe, not yet implemented, and worth revisiting.

See:

```text
.ai/memory/HARDPOINT_POLICY.md
```

Versioned memory must not store raw transcript, hidden reasoning, secrets, tokens, account/order data, or sensitive project details.

## Boot Rule

A role should boot from repository-backed files when available.

Minimum role-archive boot order:

```text
manifest.json
index.md
snapshots/current_state.md
snapshots/decisions.md
snapshots/next_actions.md
capabilities/<role>.md
profiles/communication.md
```

Optional continuity sources:

```text
checkpoints.md
history/
conversation/
memory/
```

Load optional sources only when the fast boot state is insufficient.

## Boot State and Execution Assignment Rule

Boot completion and work execution are separate states.

```text
Boot State = READY
Execution Assignment = UNASSIGNED
```

Meaning:

```text
Boot READY
  = role/profile/context is loaded

mode_context_active
  = the selected Role/Mode policy and scope are applied to the current
    governance session; it is not executable Runtime liveness

Execution Assignment UNASSIGNED
  = no specific task has been approved for execution yet
```

Therefore:

```text
Boot READY
≠
Patch/Execution approval
```

A project may store the active assignment pointer in a file such as:

```text
.ai/runtime/state/session.md
```

If that file remains `UNASSIGNED`, the assistant should treat the session as booted but not yet assigned to a specific executable task.

## Recommended Boot Pipeline

```text
User utterance
  -> Intent Type classification
  -> Resume check when relevant
  -> Role Launcher or explicit Role resolution
  -> User Role Selection when needed
  -> Role Profile
  -> Load Scope
  -> Write Scope
  -> Session Dashboard Policy
  -> Role Memory
  -> Versioned Memory, if relevant
  -> Career Dispatch Boundary, if used
  -> Boot State READY
  -> Execution Assignment remains UNASSIGNED until a scoped task is approved
```

## Checkpoint Rule

Create a checkpoint when one of these changes materially:

- role boundary
- current state
- decisions
- next actions
- archive structure
- project governance profile
- worker/master handoff model
- intent classification policy
- boot profile policy
- role launcher policy
- mode switch policy
- memory policy
- hardpoint policy

A checkpoint should record why the update matters, not duplicate full snapshots without need.

## Merge Rule

Worker outputs should not automatically become canonical state.

Canonical adoption path:

```text
Worker output
  -> Master merge candidate
  -> Conductor governance check when needed
  -> User approval
  -> Repository update
  -> Checkpoint if meaningful
```

## Project Attachment Rule

Target projects such as GCS attach through project governance profiles.

A project profile should define:

- project identity
- role mapping
- implementation boundaries
- patch approval rules
- test/report expectations
- rollback rules
- checkpoint triggers
- intent/boot policy overrides, if any
- mode switch phrases, if any
- memory scope and storage policy, if used
- hardpoint policy, if used

## Storage Policy Rule

Project `.ai` storage is owner policy, not Core mandate.

Supported modes:

```text
Shared Mode
  .ai tracked by Git

Local Mode
  .ai ignored by Git

Hybrid Mode
  shared governance/profile files tracked by Git
  runtime/resume/session/cache files kept local
```

Recommended default for personal projects:

```text
Hybrid Mode
```

Core Pack defines operating rules.
The project owner defines storage policy.

## Safety Boundaries

AI Core must not be used to claim:

- same-instance identity
- hidden-reasoning continuity
- unlimited authority
- approval bypass
- secret access

Restored files provide operating context, not autonomous permission.

## Current Validated Baseline

The first validated role archive is:

```text
.ai/resume/conductor/
```

Its active checkpoint is:

```text
conductor-2026-06-28-archive-install-001
```

GCS later validated `.ai Core Pack` project attachment and exposed the need to distinguish boot readiness from execution assignment.

GCS also validated that Role Launcher should present only user-facing Roles before delegation, while preserving `Execution Assignment = UNASSIGNED` guards.
