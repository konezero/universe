# Pre-Execution Verification

Status: candidate core runtime gate
Scope: ai-career Session Runtime Governance / Runtime Execution
Layer: final pre-execution guard
Parent: `.ai/core/RUNTIME_LIFECYCLE.md`
Source observations: `konezero/gcs` PR #16, PR #31, and project-local runtime operation; ai-career issue #170
Created: 2026-07-02
Updated: 2026-07-11

## Purpose

This document defines the final verification step immediately before execution.

Authority verified at Boot does not immediately grant execution.

It also separates Host Capability, delegated Write Scope, current Execution Assignment, and final execution permission so that no single field can authorize mutation by itself.

## Core Declaration

```text
AUTHORITY AT BOOT DOES NOT GUARANTEE AUTHORITY AT EXECUTION.

HOST CAPABILITY IS NOT WRITE AUTHORITY.
WRITE SCOPE DOES NOT CREATE HOST CAPABILITY.
MODE READINESS IS NOT EXECUTION ASSIGNMENT.
EXECUTION ASSIGNMENT IS NOT FINAL EXECUTION PERMISSION.

ALWAYS VERIFY IMMEDIATELY BEFORE EXECUTION.
```

## Runtime-Owned Operational State Exception

The Execution Guard protects project-owned source, policy, configuration,
external systems, and unclassified durable targets. It does not gate a
declared Runtime's own operational-state maintenance:

```text
SNAPSHOT_SAVE / CHECKPOINT
MEMORY_SYNC
runtime-owned Inbox or Queue state transition
RESUME_SAVE
selected RESUME restore / Current Anchor realignment
```

This exception is common to ai-career and installed project Runtimes. It is
limited to the Runtime's declared state paths and persistence contracts. Such
operations must preserve their own evidence, provenance, append-only, and
selection rules; they do not create authority, write scope, or execution
permission. A write that changes project source, Core policy, templates,
configuration, or an external system leaves this exception and requires the
normal Assignment, approval, and Execution Guard route.

## Placement

```text
Heartbeat Waiting Purpose Gate
  -> No Forced Inference Proposal Gate
  -> Role / Mode Authority Gate
  -> Runtime Authority Certificate
  -> Boundary Check
  -> Pre-Execution Verification
  -> Execution
```

## Runtime Source / Project Ownership Boundary

```text
ai-career runtime source
  -> reusable contracts
  -> templates
  -> assembly guidance
  -> compatibility surfaces

attached project
  -> assembled runtime instance
  -> project-local state
  -> project-local validation and evidence
  -> project-local mode design
  -> execution assignment and approval boundaries
```

The runtime source does not become the owner of an attached project's current state merely because the project assembled from its contracts.

The attached project's MASTER or equivalent project governance coordinate owns project-local assembly and later project-mode design under the project's authority rules.

A specific initial mode requirement belongs to project installation templates. This Core gate defines ownership and execution boundaries, not the name of a project's primary mode.

## Execution Readiness Intersection

Mutation or external execution requires all of the following at the same time:

```text
Execution Readiness
  = Host Capability
  ∩ Delegated Write Scope
  ∩ Current Execution Assignment
  ∩ Pre-Execution Verification
```

### Host Capability

Host Capability describes what the current execution environment can actually perform, such as repository access, filesystem access, connector access, CLI execution, network access, or process execution.

Capability availability is Host-specific and must be resolved at runtime.

Host Capability does not grant permission.

### Delegated Write Scope

Delegated Write Scope describes what the Commander or applicable project authority has allowed for the current work.

Write Scope does not make an unavailable Host capability available.

```text
write scope present + required Host capability unavailable
  -> execution blocked

required Host capability present + write scope absent
  -> execution blocked
```

### Current Execution Assignment

Execution Assignment identifies the current delegated work. It is either an
exact task/target/action assignment or a bounded `PROJECT_SOURCE_WORK` receipt
that permits only declared `CREATE`/`MODIFY` operations inside declared source
roots. The latter never removes the concrete target check at Mutation Receipt
issuance.

Mode selection, role readiness, project attachment, runtime boot, and general discussion do not create an Execution Assignment.

An assignment must remain current and match the target and boundary at execution time.

The Instruction -> Work -> Mutation receipt split is defined in:

```text
INSTRUCTION_WORK_RECEIPT_CONTRACT.md
```

### Pre-Execution Verification

Pre-Execution Verification is the final check immediately before action.

A previously valid readiness result must not be reused after the Host, target, assignment, boundary, or Runtime Anchor Frame changes.

## Verification Rules

Immediately before execution, Runtime must verify:

```text
Required Host Capability available?
Delegated Write Scope current and target-matched?
Execution Assignment assigned, current, and target-matched?
Authority Certificate still valid?
Authority Certificate binding still current?
Project Anchor unchanged?
Runtime Anchor Frame still current?
Runtime State still current?
Boundary unchanged?
Target unchanged?
Execution still permitted?
```

## Host Independence

Host coordinates are runtime evidence, not universal project authority.

The following may differ by Host:

```text
session_location
commander_surface
execution_surface
repository_location
available connectors
filesystem capability
CLI capability
repository write capability
```

Changing Host does not automatically change the project Node, selected Mode, resolved Role, project ownership, or approved task.

Changing Host requires capability resolution and renewed Pre-Execution Verification. Re-bootstrap may be required when current authority binding or runtime currentness cannot be preserved.

## Coordinate and Evidence Non-Authority Rule

The following may support routing, currentness, recovery, or validation, but do not independently create execution authority:

```text
session identity
checkpoint identity
Runtime Anchor Frame
Task Frame
Runtime Image
Authority Certificate presence
Mode readiness
Role label
Host Capability
```

Session identity records currentness coordinates.

Checkpoint identity records recovery evidence.

A Task Frame may assist task sequencing but is subordinate to the active Runtime Anchor Frame. If they disagree, the active source-backed Runtime Anchor Frame wins.

When a claimed Task Frame Sub performs a pre-authorized mutation, verification
must also bind the current Parent Execution Assignment, Runtime-recorded Boss
allocation, claimed Sub turn and Worker, exact Worker path, operation, target,
and payload hash. The Boss allocation may narrow the Parent Write Scope but
must never expand it. Every concrete mutation requires a distinct one-time
receipt even when one Parent approval covers the whole bounded Frame.

Task Frame orchestration and Parent adoption boundaries are defined in:

```text
TASK_FRAME_ORCHESTRATION.md
```

## Verification Failure

If any required item is absent, stale, mismatched, unavailable, or cannot be verified:

```text
Execution Cancel
  -> Authority UNKNOWN when authority binding cannot be verified
  -> keep Execution Assignment UNASSIGNED or BLOCKED when assignment is missing
  -> report missing Host Capability or Write Scope truthfully
  -> re-bootstrap if necessary
  -> Commander confirmation when renewed approval is required
```

Specific blocking rules:

```text
Host Capability: unavailable or UNKNOWN
  -> BLOCK

Delegated Write Scope: absent, stale, or mismatched
  -> BLOCK

Execution Assignment: UNASSIGNED, stale, or target-mismatched
  -> BLOCK

Pre-Execution Verification: PARTIAL, FAIL, or UNKNOWN
  -> CANCEL EXECUTION
```

Runtime must not continue execution from stale Boot authority or a stale readiness result.

Read-only inspection may continue only when the degraded or unknown state is explicitly reported and the inspection does not require the missing capability or authority.

## Relationship To Runtime Preflight

Runtime Preflight checks readiness before orchestration and task setup.

Pre-Execution Verification checks final validity immediately before execution.

```text
Runtime Preflight
  -> can prepare or reject a task frame

Pre-Execution Verification
  -> resolves capability + scope + assignment + current authority binding
  -> can still cancel immediately before action
```

## Relationship To Runtime Authority Certificate

The Runtime Authority Certificate is usable only if it is still valid at execution time.

```text
valid certificate at Boot
  -> still must pass final verification
```

Execution binding is checked through:

```text
RUNTIME_AUTHORITY_EXECUTION_BINDING.md
```

Certificate presence alone must not authorize mutation.

## Project Implementation Guidance

Attached projects should expose enough source-backed state to resolve:

```text
current Host capability profile
current Write Scope
current Execution Assignment
active Runtime Anchor Frame
Pre-Execution Verification result
project-local validation evidence
```

The storage format is project-owned.

This gate does not require SQLite, a durable runtime database, a specific filesystem path, or a specific vendor implementation.

## Validation Questions

```text
Did Runtime resolve the required Host Capability immediately before execution?
Did Runtime distinguish Host Capability from delegated Write Scope?
Did Runtime verify a current, target-specific Execution Assignment?
Did Runtime keep Mode and Role readiness separate from execution authority?
Did Runtime verify the certificate immediately before execution?
Did Runtime verify certificate binding to current writer, execution surface, assignment, approval, source, anchor, and boundary?
Did Runtime verify the project anchor, Runtime Anchor Frame, and runtime state were still current?
Did Runtime verify boundary, target, and permission had not changed?
Did a Host, target, assignment, frame, or boundary change trigger renewed verification?
Did any absent, stale, mismatched, unavailable, or UNKNOWN required term cancel execution?
Did Runtime treat project-local runtime state and validation as project-owned?
Did failure trigger re-bootstrap or Commander confirmation instead of action?
Did the implementation avoid requiring a specific Host, vendor, filesystem path, or database?
```
