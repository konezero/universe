# Runtime Orchestrator

Status: Candidate Core Architecture
Scope: ai-career
Layer: Core Connector
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-01

## Purpose

Runtime Orchestrator connects the AI Runtime Governance layers to executable core services.

It does not replace L0, L1, L2, Runtime Preflight, or L3.

It is the thin coordination layer that receives a preflight-ready trigger, pulls the Active Anchor forward when needed, aligns the session, builds the runtime frame, invokes core services, and sends selected results to persistence.

Runtime Preflight checks readiness before the orchestrator executes the flow.

## Orchestrator Declaration

```text
WHEN A TRIGGER ARRIVES,
CHECK RUNTIME PREFLIGHT BEFORE EXECUTION.

WHEN PREFLIGHT IS READY,
ORCHESTRATE FROM ACTIVE ANCHOR.

WHEN CORE SERVICES ARE NEEDED,
INVOKE THEM FROM ACTIVE FRAME.

WHEN RESULTS SHOULD SURVIVE,
ROUTE THEM TO PERSISTENCE.
```

## Position In The Architecture

```text
Current Command / Trigger
  -> Runtime Preflight
  -> Runtime Orchestrator
  -> L0 AI Runtime Governance
  -> L1 Session Framework
  -> L2 Runtime Model
  -> Core Services
  -> L3 Persistence Model
  -> Result Report
```

Short form:

```text
L0 INTERPRETS.
L1 ALIGNS.
PREFLIGHT CHECKS READINESS.
L2 EXECUTES.
CORE SERVICES ACT.
L3 PRESERVES.
ORCHESTRATOR CONNECTS.
```

## Why This Layer Exists

The L0-L3 documents define responsibilities, but they do not describe the coordinator that moves a command through the layers.

Without an orchestrator, future implementations may accidentally let:

```text
GitHub tool calls decide authority
Memory decide current task
Archive become active frame
Resume auto-restore role
Prior context dominate the current command
Preflight become execution instead of readiness checking
```

The orchestrator prevents that by enforcing the flow:

```text
Anchor first.
Session alignment second.
Preflight readiness before execution.
Runtime frame third.
Core service invocation fourth.
Persistence last.
```

## Core Flow

```text
TRIGGER
  -> PARSE CURRENT COMMAND
  -> RUN RUNTIME PREFLIGHT
  -> IF PREFLIGHT NOT READY, REPORT MISSING ALIGNMENT
  -> PULL ACTIVE ANCHOR FORWARD WHEN NEEDED
  -> ALIGN SESSION
  -> BUILD RUNTIME FRAME
  -> SELECT WORK CONTEXT
  -> INVOKE CORE SERVICES
  -> PRODUCE RESULT
  -> WEAKEN COMPLETED FRAME
  -> DISCARD TASK-LOCAL ASSUMPTIONS
  -> PERSIST SELECTED MEANING WHEN USEFUL
  -> REPORT COMPLETION STATE
```

## Step 1: Parse Current Command

The orchestrator begins with the current user command.

It must not continue blindly from the previous goal.

```text
WHEN A NEW TRIGGER APPEARS,
PARSE CURRENT COMMAND FIRST.
```

Examples of triggers:

```text
@GitHub
@Carrier
@Conductor
메모싱크
감사모드
리부트
상태표시
고고
```

## Step 2: Run Runtime Preflight

Before execution, the orchestrator checks whether the runtime is prepared for the requested task.

```text
BEFORE EXECUTION,
VERIFY RUNTIME READINESS.
```

Runtime Preflight may check:

```text
Repository
Role
Boot Depth
Authority
Scope
Layer
Task
Service
Persistence
```

Preflight outcomes:

```text
READY
NOT READY
PARTIAL READY
UNKNOWN
```

If Preflight is not ready, the orchestrator reports the missing alignment instead of executing the task.

```text
IF PREFLIGHT IS NOT READY,
REPORT WHAT MUST BE ALIGNED BEFORE EXECUTION.
```

Preflight does not grant authority and does not execute work.

## Step 3: Pull Active Anchor Forward

If the current command, strong context, or trigger competition creates uncertainty, the orchestrator pulls the Active Anchor forward.

```text
WHEN INTERPRETATION IS UNCERTAIN,
PULL ACTIVE ANCHOR FORWARD.
```

This is not context deletion.

This reorders interpretation so the current Anchor leads the next runtime.

## Step 4: Align Session

The orchestrator uses L1 Session Framework to determine whether the command requires:

```text
Boot
Reboot
Resume Candidate
Mode Change
Session Out
No lifecycle change
```

```text
WHEN SESSION STATE IS MISALIGNED,
ALIGN SESSION BEFORE EXECUTION.
```

Session alignment must not grant authority from persistence artifacts.

## Step 5: Build Runtime Frame

The orchestrator uses L2 Runtime Model to build the active execution frame.

```text
Base
  -> Anchor
  -> Runtime
  -> Frame
  -> Work
  -> Task
```

```text
BEFORE CORE SERVICES ACT,
ESTABLISH ACTIVE FRAME.
```

Only the active frame controls execution.

## Step 6: Select Work Context

The orchestrator selects the minimum required work context.

Examples:

```text
Repository file
Pull request
Inbox queue
Memory note
Archive record
Uploaded artifact
Search result
```

```text
WHEN WORK CONTEXT IS SELECTED,
KEEP UNRELATED PRIOR FRAMES WEAK.
```

## Step 7: Invoke Core Services

Core Services are tools or capabilities that act under the active frame.

Candidate services:

```text
GitHub
Storage
Memory
Archive
Search
Dispatch
Carrier
Status
Validation
```

Core Services do not decide authority.

```text
CORE SERVICES ACT UNDER ACTIVE FRAME.
CORE SERVICES DO NOT ESTABLISH AUTHORITY.
```

Examples:

```text
GitHub reads or writes repository files.
Storage writes artifacts to a provider.
Memory prepares or reads memory candidates.
Archive records durable history.
Search retrieves source context.
Dispatch delivers approved work.
Carrier collects candidate events.
Status reports source-backed runtime state.
Validation checks completion evidence.
```

## Step 8: Produce Result

The orchestrator reports task output and completion state.

```text
TASK RESULT
  -> Complete
  -> Ready
  -> Waiting
  -> Failed
  -> Unknown
```

```text
WHEN TASK OUTPUT IS READY,
REPORT RESULT WITH COMPLETION STATE.
```

## Step 9: Weaken And Discard

After the result, the orchestrator separates the completed runtime from future commands.

```text
AFTER TASK COMPLETION,
WEAKEN COMPLETED FRAME.

AFTER TASK COMPLETION,
DISCARD TASK-LOCAL ASSUMPTIONS.
```

Weak keeps the frame recoverable but passive.

Discard removes task-local assumptions from active interpretation.

## Step 10: Route To Persistence

The orchestrator routes selected durable meaning to L3 Persistence.

```text
WHEN RESULT SHOULD SURVIVE,
ROUTE SELECTED MEANING TO L3.
```

Possible L3 targets:

```text
Summary
Checkpoint
Resume Candidate
Memory
Archive
```

Persistence remains passive until explicitly restored, selected, or promoted.

## Orchestrator Boundaries

The orchestrator must not:

```text
- Treat tool availability as authority.
- Treat memory as current source of truth.
- Treat archive as active frame automatically.
- Treat resume candidate as role approval.
- Treat GitHub write success as policy adoption.
- Treat generated files as Sync Complete unless saved to approved durable storage.
- Treat Preflight READY as user approval.
```

The orchestrator may:

```text
- Parse triggers.
- Run Runtime Preflight.
- Pull Active Anchor forward.
- Align session lifecycle.
- Build active runtime frame.
- Invoke core services under active frame.
- Route selected meaning to persistence.
- Report completion state.
```

## Example: GitHub Memory Sync

```text
@GitHub 메모싱크
  -> TRIGGER DETECTED
  -> PARSE CURRENT COMMAND
  -> PREFLIGHT: repository / role / authority / service readiness
  -> PULL ACTIVE ANCHOR FORWARD WHEN NEEDED
  -> ALIGN SESSION: GitHub + Conductor context
  -> BUILD FRAME: Memory Sync task
  -> SELECT WORK: current conversation summary + target repo path
  -> INVOKE CORE SERVICES: GitHub create_file
  -> RESULT: memory file created
  -> WEAKEN FRAME
  -> DISCARD task-local assumptions
  -> L3: Memory artifact persisted
  -> REPORT Complete
```

## Example: Reboot

```text
리부트
  -> TRIGGER DETECTED
  -> PREFLIGHT: boot/session state readiness
  -> PULL ACTIVE ANCHOR FORWARD
  -> DISCARD stale working assumptions
  -> LOAD GOVERNANCE
  -> CHECK resume/checkpoint candidate
  -> ALIGN SESSION
  -> REBUILD RUNTIME
  -> REPORT READY
```

## Example: Status Command

```text
상태표시
  -> TRIGGER DETECTED
  -> PARSE CURRENT COMMAND
  -> PREFLIGHT: source availability / role / scope
  -> ALIGN SESSION WITHOUT ROLE ESCALATION
  -> SELECT SOURCE-BACKED STATUS CONTEXT
  -> INVOKE STATUS SERVICE
  -> REPORT VERIFIED OR UNVERIFIED STATE
```

Status must be source-backed or explicitly marked unverified.

## Example: Role Mismatch

```text
Role: Project Master
Task: Carrier responsibility absorption
  -> TRIGGER DETECTED
  -> PARSE CURRENT COMMAND
  -> PREFLIGHT: role/task alignment
  -> PREFLIGHT STATUS: NOT READY or PARTIAL READY
  -> REPORT: Project Master may coordinate boundary, but must not absorb Carrier responsibilities
  -> STOP EXECUTION OR REQUEST ROLE/MODE ALIGNMENT
```

## Relationship To Runtime Preflight

Runtime Preflight checks readiness before execution.

Runtime Orchestrator connects the ready task through the architecture.

```text
PREFLIGHT CHECKS READINESS.
ORCHESTRATOR DECIDES FLOW.
CORE SERVICES PERFORM ACTIONS.
L3 PRESERVES SELECTED RESULTS.
```

## Relationship To Core Services

Core Services should be small and tool-like.

They should not own the governance model.

```text
ORCHESTRATOR DECIDES FLOW.
CORE SERVICES PERFORM ACTIONS.
L3 PRESERVES SELECTED RESULTS.
```

## Placement Test

A concept belongs in Runtime Orchestrator when it answers:

```text
How does a command move through L0, L1, L2, Core Services, and L3?
Who coordinates tool/service invocation?
Where is completion state reported?
Where are Weak and Discard applied after result?
```

A concept belongs in Runtime Preflight when it answers:

```text
Is the current runtime ready for this task?
Which alignment is missing before execution?
```

If a concept defines reference/authority, it belongs in L0.

If it defines lifecycle transitions, it belongs in L1.

If it defines active frame execution, it belongs in L2.

If it defines durable artifacts, it belongs in L3.

If it performs a specific action, it belongs in Core Services.

## Adoption Status

This is a candidate core architecture document.

It should be reviewed after the L0-L3 layer model is accepted.
