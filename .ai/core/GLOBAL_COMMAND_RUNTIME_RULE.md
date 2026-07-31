# Global Command Runtime Rule

Status: core runtime candidate
Repository: `konezero/ai-career`
Scope: runtime implementation rule for governance-level global commands

## Governance Boundary

Global command meanings are defined by Governance, not by Runtime.

```text
Governance
  -> defines global command vocabulary and invariant command contracts

Runtime
  -> implements global command resolution and lifecycle behavior

Project
  -> consumes the commands through project-local runtime instances
```

Governance policy source:

```text
.ai/governance/COMMAND_GOVERNANCE_POLICY.md
```

## Purpose

Some commands must be interpreted before the active Role, Mode, or Task context.

These commands stabilize the session and prevent the assistant from resolving them through contaminated recent context.

## Core Rule

```text
Global commands are context stabilizers.
They must be resolved before Role, Mode, or Task resolution.
```

## One Command Rule

```text
ONE COMMAND INTENT -> ONE COMMAND EXECUTION -> STOP

Do not chain a second global or runtime command by inference.
Examples forbidden without a new Commander utterance:
  리쥼 저장 then 리쥼 복원
  부트 then 리쥼 저장
  상태 then OS_UPDATE
```

Multi-step work is allowed only when it is the internal contract of the single
named command (for example `resume-save prepare` then `save` for one candidate).

## Command Entry Flow

```text
User Input
  -> Global Command Resolver
  -> if matched: Global Command Handler
  -> if not matched: Role / Mode / Task Resolver
```

The active role may provide trigger thresholds or presentation style, but it must not redefine the global command contract.

## Required Global Commands

Runtime must implement the governance command contracts for:

```text
부트
리부트
모드체인지
메모싱크
@GitHub 메모싱크
상태
취소 / 멈춤
```

The detailed command meanings live in:

```text
.ai/governance/COMMAND_GOVERNANCE_POLICY.md
```

## Implementation Notes

### 부트

```text
부트
  -> Session Open
  -> Minimal Core
  -> Role Gate
  -> Role Boot Task
```

`부트` does not imply Conductor boot.

### 리부트

```text
리부트
  -> Release current Role
  -> Clear active Task Queue
  -> Release connector/executor locks
  -> Minimal Core
  -> Role Gate
  -> Role Boot Task
```

### 모드체인지

```text
모드체인지
  -> Reboot
  -> Role Gate
  -> Role Boot Task
```

Mode change must not mutate role/mode state inside a contaminated context unless a future runtime explicitly validates safe live switching.

### 메모싱크

Memory is global.
Roles may define different trigger conditions, but the memory sync command contract is global.

```text
메모싱크
  -> extract meaningful memory candidates
  -> show Candidate List
  -> require User Selection
  -> write only selected memory
```

`메모싱크` must not write directly without Candidate List and User Selection.

### @GitHub 메모싱크

```text
@GitHub 메모싱크
  -> extract meaningful memory candidates
  -> show Candidate List
  -> require User Selection
  -> write selected candidates to Git-backed memory / checkpoint surface
```

GitHub sync is the persistence step, not a replacement for candidate extraction.

### 상태

```text
상태
  -> source-backed status report
  -> no source means UNKNOWN
```

### 취소 / 멈춤

```text
취소 / 멈춤
  -> mark active task as INTERRUPTED
  -> pop: false
  -> require recovery check before retry if side effects may have occurred
```

## Role-Specific Trigger Conditions

Memory is global, but trigger conditions may differ by role.

Examples:

```text
Conductor
  -> trigger on durable policy, checkpoint, architecture, or governance candidates

Project Master
  -> trigger on project decisions, implementation direction, or handoff candidates

Worker
  -> trigger on implementation discoveries, bug causes, test results

Audit / Reviewer
  -> trigger on evidence, violations, risks, validation results
```

## Failure Mode Prevented

Without this layer, recent context may cause global commands to resolve incorrectly.

Example failure:

```text
@GitHub 메모싱크
  -> incorrectly resolved as direct checkpoint write
  -> skipped Candidate List
  -> skipped User Selection
```

Correct behavior:

```text
@GitHub 메모싱크
  -> Governance command contract
  -> Global Command Resolver
  -> Candidate List
  -> User Selection
  -> Git-backed persistence
```

## Status

Core runtime candidate.

This rule implements:

```text
.ai/governance/COMMAND_GOVERNANCE_POLICY.md
```

This rule complements:

```text
CONTEXT_MANAGEMENT_RUNTIME_FREEZE.md
TASK_QUEUE_RUNTIME_V1.md
RUNTIME_STATUS_SOURCE_RULE.md
APP_RUNTIME_BOUNDARY_RULE.md
USER_INTERRUPT_RUNTIME_RULE.md
PROJECT_INSTANCE_RUNTIME_RULE.md
```
