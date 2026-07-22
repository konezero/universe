# Runtime Preflight

Status: Candidate Core Architecture
Scope: ai-career
Layer: Pre-Orchestrator Guard
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-01

## Purpose

Runtime Preflight checks whether the current runtime is ready to execute the requested work.

It runs after a trigger is parsed and before Runtime Orchestrator performs execution planning.

Runtime Preflight does not execute the task.

It answers:

```text
Is this runtime prepared to do this work now?
```

For attached project input, it also answers:

```text
Is this an OS instruction, a reusable candidate, or a project-local instance change?
```

## Preflight Declaration

```text
BEFORE EXECUTION,
VERIFY RUNTIME READINESS.

IF RUNTIME IS NOT READY,
REPORT THE MISSING ALIGNMENT.

DO NOT EXECUTE UNTIL THE REQUIRED ALIGNMENT IS ESTABLISHED.
```

## Position In The Architecture

```text
Current Command / Trigger
  -> Parse Current Command
  -> Runtime Preflight
  -> Runtime Instruction Set when instruction is present
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
TRIGGER
  -> PREFLIGHT CHECKS READINESS
  -> INSTRUCTIONS DEFINE THE INTERFACE
  -> ORCHESTRATOR CONNECTS
  -> CORE SERVICES ACT
```

## Why This Layer Exists

A runtime can understand a command but still be unprepared to execute it.

Examples:

```text
- The repository is not loaded.
- The role is not aligned with the requested work.
- The boot depth is only Partial but the work requires Full Core Boot.
- The current authority allows review but not write.
- The task belongs to another role.
- The requested layer is not loaded.
- The required service is unavailable.
- Attached project requests OS_UPDATE but has no local runtime surface to update.
- Attached project requests OS_INSTALL but lacks enough repository structure to assemble locally.
- External context is project-local but is being applied as Core policy.
- External context is reusable runtime material but is being trapped inside one project instance.
```

Runtime Preflight prevents the runtime from silently executing work under the wrong role, wrong layer, wrong scope, wrong boot depth, wrong instruction state, or wrong project boundary.

## Preflight Checklist

Runtime Preflight may check:

```text
Repository Ready?
Role Ready?
Boot Depth Ready?
Authority Ready?
Scope Ready?
Layer Ready?
Task Ready?
Instruction Ready?
Instruction Type?
Project Assembly Ready?
Candidate Or Local?
Source Ready?
Service Ready?
Persistence Ready?
```

The result is one of:

```text
READY
NOT READY
PARTIAL READY
UNKNOWN
```

## Readiness Is Not Authority

Preflight checks readiness.

It does not grant authority.

```text
A READY preflight means the runtime appears prepared.
It does not override user approval, governance boundaries, or role contracts.
```

## Role Alignment

Role alignment checks whether the current role is the right role for the requested work.

Example:

```text
Role: Project Master
Task: Carrier Agent Absorption

Preflight:
Role Ready? PARTIAL / NOT READY

Reason:
Project Master may coordinate project execution.
Carrier responsibilities belong to Carrier.
Conductor decides reusable governance adoption.
```

Recommended response:

```text
Coordinate the boundary.
Do not absorb Carrier responsibilities into Project Master.
Escalate reusable policy questions to Conductor.
```

## Boot Depth Alignment

Boot depth alignment checks whether the runtime has loaded enough Core context.

Example:

```text
Boot Status: PARTIAL
Task: Runtime Governance Patch

Preflight:
Boot Depth Ready? NOT READY

Reason:
Runtime Governance Patch requires Full Core Boot.
```

Recommended response:

```text
Load Full Core Boot before patching governance documents.
```

## Authority Alignment

Authority alignment checks whether the current user command permits the requested action.

Example:

```text
Mode: READ_ONLY
Task: GitHub File Write

Preflight:
Authority Ready? NOT READY

Reason:
Write action requires explicit user scope.
```

Recommended response:

```text
Report required authority before writing.
```

## Instruction Alignment

Instruction alignment checks whether attached project input is a common Runtime Instruction.

Common instructions are defined in `RUNTIME_INSTRUCTION_SET.md`.

Examples:

```text
OS_INSTALL
OS_UPDATE
OS_PREFLIGHT
OS_STATUS
OS_VALIDATE
OS_ROLLBACK
OS_SYNC
```

Preflight result:

```text
Instruction Type? OS_INSTALL | OS_UPDATE | OS_PREFLIGHT | OS_STATUS | OS_VALIDATE | OS_ROLLBACK | OS_SYNC | NONE | UNKNOWN
```

If an instruction is present, Preflight should route to the Runtime Instruction Set before orchestration.

```text
Attached Project
  -> OS_UPDATE
  -> Runtime Preflight
  -> Runtime Instruction Set
  -> Project-local Assembly / Update
```

## Candidate Or Local Alignment

Candidate or Local alignment checks whether external input should become a reusable candidate or remain project-local.

This is different from Runtime Instruction alignment.

Instructions are common interfaces.

Candidates are reusable discoveries that may update ai-career later.

Project-local changes remain inside the attached project.

```text
External Context
  -> Runtime Preflight
  -> Instruction?
       -> YES: route to Runtime Instruction Set
       -> NO: Candidate or Local?
            -> Candidate: Carrier / Conductor candidate path
            -> Local: Project Master / Worker project path
```

### Runtime Instruction

External input is an instruction when it asks the attached project to install, update, validate, report, roll back, or sync its local runtime instance.

Preflight result:

```text
Instruction Type? OS_UPDATE
Decision: Route to Runtime Instruction Set
```

### Reusable Candidate

External input may be a reusable candidate when it describes:

```text
Reusable runtime rule
Reusable role boundary
Reusable boot behavior
Reusable preflight check
Reusable memory/checkpoint/archive policy
Reusable service boundary
Reusable project attachment pattern
```

Preflight result:

```text
Candidate Or Local? REUSABLE CANDIDATE
Decision: Route to Carrier / Conductor / Candidate Patch path
```

### Project Instance Change

External input should remain project-local when it describes:

```text
Project bug fix
Project source implementation
Project-specific configuration
Project-specific data
Project-specific runtime state
One-off operational detail
```

Preflight result:

```text
Candidate Or Local? PROJECT INSTANCE
Decision: Route to Project Master / Worker path
```

## Project Assembly Direction

Projects can attach to `ai-career`, receive common runtime instructions, and assemble their own local runtime instance.

```text
ai-career Runtime Instruction
  -> attached project
  -> project-local assembly
```

Projects can also send reusable discoveries back upward.

```text
Project observation
  -> Carrier collects
  -> Conductor reviews
  -> Candidate
  -> ai-career instruction/model update if adopted
```

Preflight should protect both directions:

```text
Do not apply project-local details as Core rules.
Do not require custom templates per project.
Do not trap reusable runtime patterns inside one project instance.
```

## Layer Alignment

Layer alignment checks whether the task belongs to the claimed layer.

Example:

```text
Task: Define authority rule
Claimed Layer: Core Services

Preflight:
Layer Ready? NOT READY

Reason:
Authority rules belong to L0 Runtime Governance.
Core Services act under active frame and do not establish authority.
```

Recommended response:

```text
Move the rule to L0 or report a placement mismatch.
```

## Service Alignment

Service alignment checks whether a needed capability is available and appropriate.

Example:

```text
Task: Search GitHub repository
Service: GitHub

Preflight:
Service Ready? READY if GitHub access is available
```

Service readiness does not decide whether the action should be performed.

It only reports whether the service can support the active task.

## Preflight Report Format

Preferred report:

```text
PREFLIGHT STATUS: READY | NOT READY | PARTIAL READY | UNKNOWN

Repository: READY | NOT READY | UNKNOWN
Role: READY | NOT READY | PARTIAL | UNKNOWN
Boot Depth: READY | NOT READY | PARTIAL | UNKNOWN
Authority: READY | NOT READY | UNKNOWN
Scope: READY | NOT READY | UNKNOWN
Layer: READY | NOT READY | UNKNOWN
Task: READY | NOT READY | UNKNOWN
Instruction Type: OS_INSTALL | OS_UPDATE | OS_PREFLIGHT | OS_STATUS | OS_VALIDATE | OS_ROLLBACK | OS_SYNC | NONE | UNKNOWN
Project Assembly: READY | NOT READY | PARTIAL | UNKNOWN
Candidate Or Local: REUSABLE CANDIDATE | PROJECT INSTANCE | NOT APPLICABLE | UNKNOWN
Source: READY | NOT READY | UNKNOWN
Service: READY | NOT READY | UNKNOWN

Decision:
Proceed | Request alignment | Load required context | Switch role/mode | Route to Runtime Instruction Set | Route to candidate path | Route to project instance | Stop
```

## Relationship To Boot Status

Boot Status reports what is loaded.

Runtime Preflight decides whether what is loaded is enough for the requested work.

```text
Boot Status = current loaded state.
Preflight = readiness for this task.
```

A `PARTIAL` boot can be ready for a lightweight task and not ready for a governance patch.

## Relationship To Runtime Instruction Set

Runtime Preflight detects whether an attached project is invoking a common runtime instruction.

Runtime Instruction Set defines what that instruction means.

```text
Preflight detects OS_UPDATE.
Runtime Instruction Set defines OS_UPDATE.
Project assembles or updates locally.
```

## Relationship To Project Instance Boundary

Runtime Preflight uses `PROJECT_INSTANCE_RUNTIME_RULE.md` when the task crosses a project boundary.

```text
ai-career owns reusable instructions and contracts.
Projects own local runtime assembly and mutable state.
```

Preflight decides whether the current input should become an instruction, candidate, or local project path.

It does not adopt candidates by itself.

## Relationship To Runtime Orchestrator

Runtime Preflight runs before orchestration.

```text
Preflight checks readiness.
Instruction Set defines interface.
Orchestrator decides flow.
Core Services perform actions.
L3 preserves selected results.
```

The orchestrator may call Preflight again if the task changes, the role changes, or new constraints appear.

## Relationship To L0/L1/L2/L3

```text
L0 provides the reference point and authority interpretation.
L1 provides boot/session lifecycle state.
L2 provides active frame/task state.
Core Services provide capability state.
L3 provides durable state and persistence readiness.
Runtime Preflight checks whether those states are aligned for the current task.
```

## Anti-Pattern

Runtime Preflight must not become a second orchestrator.

It should not:

```text
- Execute the task.
- Rewrite architecture.
- Grant authority.
- Promote memory.
- Persist results by itself.
- Replace user approval.
- Adopt candidates by itself.
- Create custom templates for every project.
```

It should:

```text
- Check readiness.
- Report mismatches.
- Recommend alignment steps.
- Stop unsafe or misaligned execution.
- Route attached project input toward instruction, candidate, or local project handling.
```

## Observed Pattern

This layer was identified after observing a runtime that detected a role/task mismatch before execution.

The observed behavior was not simple refusal.

It was a readiness check:

```text
Current role can coordinate this boundary.
Current role should not absorb another role's responsibility.
```

The same pattern applies to attached project input:

```text
Current repository provides common runtime instructions.
Attached projects assemble local runtime instances.
Reusable improvements return as candidates.
```

## Placement Test

A concept belongs in Runtime Preflight when it answers:

```text
Is the current runtime ready to execute this task?
Which alignment is missing?
What must be loaded, switched, approved, or scoped before execution?
Is this external input an instruction, reusable candidate, or project-local instance change?
```

If a concept defines authority, it belongs in L0.

If it defines lifecycle transition, it belongs in L1.

If it defines common OS instructions, it belongs in Runtime Instruction Set.

If it defines active execution structure, it belongs in L2.

If it coordinates execution, it belongs in Runtime Orchestrator.

If it performs a capability action, it belongs in Core Services.

If it preserves durable state, it belongs in L3.

## Adoption Status

This is a candidate pre-orchestrator guard.

It should be validated through repeated clean-session tests before becoming canonical policy.
