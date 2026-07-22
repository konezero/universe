# Anchor Resolution

Status: Candidate Core Architecture
Scope: ai-career / attached project runtime
Layer: Anchor Routing / Role-Task Binding
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-02

## Purpose

Anchor Resolution decides which anchor is active for the current command before task execution.

The current stack already has Active Anchor and Project Anchor.

It still needs a clear sequence for resolving role and task anchors inside a session.

Anchor Resolution answers:

```text
Which reference point controls this command?
Which project is localizing it?
Which role is allowed to act?
Which task defines completion?
```

## Core Declaration

```text
RESOLVE ANCHORS BEFORE TASK EXECUTION.

GLOBAL ANCHOR DEFINES THE CONTRACT.
PROJECT ANCHOR LOCALIZES THE CONTRACT.
ROLE ANCHOR DEFINES AUTHORITY.
TASK ANCHOR DEFINES COMPLETION.
```

## Anchor Chain

```text
Global Anchor
  -> Project Anchor
  -> Role Anchor
  -> Task Anchor
  -> Runtime Frame
```

## Anchor Types

### Global Anchor

The Global Anchor is the canonical ai-career reference.

It answers:

```text
What is the canonical runtime contract?
What is the active instruction contract?
What governance boundary applies?
```

### Project Anchor

The Project Anchor is the project-local reference point.

It answers:

```text
Which project is attached?
Where is the local runtime root?
Where does this project assemble the instruction contract?
Where does this project store validation evidence?
```

### Role Anchor

The Role Anchor defines the active role and authority boundary.

It answers:

```text
Am I acting as Conductor?
Am I acting as Project Master?
Am I acting as Worker?
Am I acting as Carrier?
Am I acting as Reviewer?
What can this role do?
What should this role refuse or reroute?
```

### Task Anchor

The Task Anchor defines the current task and completion boundary.

It answers:

```text
What is the current task?
What files or surfaces may change?
What evidence proves completion?
When should this frame be weakened or discarded?
```

## Role Anchor Table

| Role Anchor | Primary Authority | Should Not Own |
| --- | --- | --- |
| Conductor | Governance, candidate review, adoption decisions | Project-local implementation work |
| Project Master | Project-local orchestration and boundary coordination | Carrier collection or Conductor adoption |
| Worker | Scoped implementation under assignment | Governance adoption or role reassignment |
| Carrier | Collection of reusable candidates and queue events | Adoption decisions or project implementation |
| Reviewer | Review, validation, gap reporting | Unscoped implementation |

## Resolution Flow

```text
Command arrives
  -> Resolve Global Anchor
  -> Resolve Project Anchor when attached project is involved
  -> Resolve Role Anchor
  -> Resolve Task Anchor
  -> Run Runtime Preflight
  -> Build Runtime Frame
  -> Execute or report missing alignment
```

## OS_UPDATE Example

```text
Command: OS_UPDATE
Project: GCS

Anchor Resolution:
  Global Anchor: ai-career runtime contract
  Project Anchor: GCS project-local runtime root
  Role Anchor: Project Master or assigned Worker
  Task Anchor: OS_UPDATE local assembly + OS_VALIDATE evidence

Allowed path:
  Runtime Commands
  -> Runtime Instruction Set
  -> Project Anchor
  -> Role Anchor
  -> Task Anchor
  -> OS Validation Evidence
  -> Project-local Assembly
```

## Role Conflict Example

```text
Role Anchor: Project Master
Task: Carrier responsibility absorption

Resolution:
  Role/task mismatch.
  Project Master may coordinate the boundary.
  Carrier owns collection behavior.
  Conductor owns adoption decisions.
```

Decision:

```text
Do not absorb the other role.
Report role mismatch or reroute to the correct role anchor.
```

## Task Anchor Completion

Task Anchor should state the completion evidence before execution.

Examples:

```text
OS_UPDATE completion:
  validation/latest.md updated
  validation/history.md updated
  compatibility state reported

PR review completion:
  reviewed PR number
  decision recorded
  follow-up route identified

Memory sync completion:
  candidate list produced
  user selection required or artifact recorded
```

## Anchor Resolution Report

Preferred report:

```text
ANCHOR RESOLUTION: READY | PARTIAL | MISALIGNED | UNKNOWN

Global Anchor: <source or UNKNOWN>
Project Anchor: <source or NOT APPLICABLE | UNKNOWN>
Role Anchor: <role or UNKNOWN>
Task Anchor: <task / completion boundary or UNKNOWN>

Decision:
Proceed | Load anchor | Switch role | Reroute task | Stop
```

## Relationship To Runtime Commands

Runtime Commands identify the command and first route.

Anchor Resolution determines which anchors govern that route.

```text
Runtime Commands
  -> what command was invoked?

Anchor Resolution
  -> what anchors control this command?
```

## Relationship To Project Anchor

Project Anchor is one anchor in the chain.

Anchor Resolution decides when to use it and how it relates to role and task anchors.

```text
Project Anchor localizes.
Role Anchor authorizes.
Task Anchor completes.
```

## Relationship To Runtime Preflight

Runtime Preflight checks readiness after anchors are resolved.

```text
Anchor Resolution
  -> determine anchors

Runtime Preflight
  -> check whether those anchors are ready for this task
```

## Relationship To Runtime Model

Runtime Model builds the active frame after anchors are resolved.

```text
Resolved Anchors
  -> Runtime Frame
  -> Work
  -> Task
  -> Result
```

## Anti-Patterns

Avoid:

```text
- Executing a task before role anchor is known.
- Treating Project Anchor as role authority.
- Letting Carrier collect and adopt in the same role.
- Letting Worker change governance without Conductor route.
- Treating current chat context as Task Anchor without completion evidence.
```

Prefer:

```text
- Global Anchor for canonical contract.
- Project Anchor for local assembly point.
- Role Anchor for authority.
- Task Anchor for completion evidence.
- Explicit reroute when anchors conflict.
```

## Placement Test

A concept belongs in Anchor Resolution when it answers:

```text
Which anchor controls this command?
Which role may act?
Which task boundary defines completion?
How should role/task conflicts be rerouted?
```

If it defines canonical authority, it belongs in L0 Runtime Governance.

If it defines local project surface, it belongs in Project Anchor.

If it checks readiness, it belongs in Runtime Preflight.

If it defines execution frame lifecycle, it belongs in Runtime Model.

## Adoption Status

This is a candidate anchor routing model.

It should be validated by a clean session where a command involves a project, role, and task, and the runtime reports Global Anchor, Project Anchor, Role Anchor, and Task Anchor before execution.
