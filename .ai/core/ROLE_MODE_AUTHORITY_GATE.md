# Role / Mode Authority Gate

Status: candidate core runtime gate
Scope: ai-career Session Runtime Governance / Role and Mode Transition
Layer: post-command proposal / pre-role-mode activation guard
Parent: `.ai/core/SESSION_RUNTIME_GOVERNANCE.md`, `.ai/core/RUNTIME_COMMANDS.md`
Created: 2026-07-02

## Purpose

This document defines a runtime gate that prevents the assistant from simulating a role or mode when source-backed authority is unavailable.

It promotes the GCS emergency proposal from:

```text
konezero/gcs PR #29
.ai/memory/inbox/2026-07-02-role-mode-authority-source-gate.md
```

The failure mode is:

```text
User gives a role/mode label
  -> model recognizes the label
  -> authority sources are unavailable or unchecked
  -> model imitates the role/mode anyway
```

## Core Declaration

```text
ROLE / MODE LABEL IS NOT ROLE / MODE AUTHORITY.

ROLE / MODE TRANSITION REQUIRES SOURCE-BACKED AUTHORITY.

IF AUTHORITY SOURCE IS UNAVAILABLE,
AUTHORITY IS UNKNOWN.

DO NOT SIMULATE UNVERIFIED ROLES OR MODES.
```

## Authority Source

A role or mode transition may proceed only when the runtime can verify the applicable authority source.

Authority sources may include:

```text
session runtime governance files;
project-local role / mode gate;
source-backed active session state;
explicit user-approved transition plus applicable runtime policy;
repository or connector source fetched in the active session.
```

Conversation memory, model familiarity, role label recognition, or recent context is not enough.

## Request vs Activation

Role and mode status must distinguish request, selection, authority, and activation.

```text
role/mode label detected
  -> REQUESTED

Commander selects a mode
  -> SELECTED

source-backed Mode policy/profile and Host-proven governance coordinates verify context
  -> mode_context_active

source-backed authority verifies the transition
  -> AUTHORIZED

raw executable Runtime evidence verifies a runtime frame
  -> executor_active
```

`REQUESTED` or `SELECTED` is not `mode_context_active`.

`SESSION_BOOT_IMAGE_CREATED` is not role or mode activation.

`mode_context_active` is not execution authority or executor liveness.

Role authority scope is separate from active delegation. For Conductor on the
`konezero/ai-career` source repository:

```text
role_authority_scope: AI_CAREER_REPOSITORY_ROOT
governance_read: ALLOWED
repository_write: USER_APPROVAL_REQUIRED
active_delegation: UNASSIGNED until a concrete approved operation exists
```

This is a repository-local governance scope, not authority over attached
projects and not a bypass of an approved source mutation's execution guard.

`mode_context_active` means the current governance session may use the selected
mode as its response and coordination context after source-backed policy/profile
and available Host governance-coordinate checks. It does not require an
Executable Runtime frame.

It does not grant:

```text
repository write
runtime mutation
external tool execution
project execution assignment
authority escalation
```

Bare `CONDUCTOR_MODE_ACTIVE`, `MASTER_MODE_ACTIVE`, or equivalent mode-active
claims are forbidden because they conflate governance context and executable
Runtime state. When executable state is needed, report `executor_active`
separately and require:

```text
mode_authority_source
source-backed validation of that source
raw executable Runtime frame or session attach target
applicable Commander approval
```

If any required coordinate is missing, stale, unchecked, or unavailable:

```text
mode_authority: UNKNOWN
mode_context_active: false
executor_active: false
```

The runtime may still report the requested mode and ask for the missing source,
approval, or validation.

If execution authority is missing but source-backed Mode policy/profile and
available Host governance coordinates are sufficient, the runtime should report:

```text
mode_context_active: true
mode_authority: UNASSIGNED | UNKNOWN
execution_assignment: UNASSIGNED
repository_write: NONE
```

Mode context may remain active for read-only coordination, review, routing
proposals, and OS_STATUS / OS_VALIDATE.

Project-owned source, policy, configuration, external, and unclassified durable
mutation remains blocked until authority and execution assignment are verified.
Declared Runtime-owned operational state follows the separate exception in
`PRE_EXECUTION_VERIFICATION.md`.

## Mode Intent Without Active Runtime

If a role or mode shorthand is received before an ai-career Executable Runtime
is active, the gate must distinguish governance Mode context from executable
Runtime state. It must not activate the role or Mode from the label alone.

```text
role/mode shorthand
  -> natural-language Mode intent
  -> internal MODE_CHANGE and PREPARING_SESSION alignment
  -> resolve source-backed Role / Scope policy
  -> mode_context_active only when that policy/profile is loaded
  -> keep executable Runtime fields UNKNOWN until raw Host Runtime evidence
  -> propose OS_INSTALL only for an explicit durable-install request
```

This applies even when the requested mode is recognized.

Recognized mode is not active mode.

Selected mode is not active mode.

No external tool execution may be routed from a mode shorthand while authority
or execution assignment is `UNASSIGNED`.

## Source-Only Anchor Snapshot Boundary

This rule applies to every Mode, not only Conductor.

```text
source-only BOOT / REBOOT + resolved Mode
  -> source-backed mode_context_active may be true
  -> readable Anchor Snapshot: OBSERVED_REFERENCE rehydration input
  -> governance context may be rehydrated from that snapshot
  -> conversation Resume / Archive: recall material only
  -> session_preparation_state: UNKNOWN until Mode selection
  -> session_preparation_state: PREPARED for a new Mode Current Anchor
  -> session_preparation_state: REHYDRATED for an existing Mode Current Anchor
  -> executable runtime session / frame / executable_runtime_currentness: UNKNOWN without raw execution evidence
```

Repository reading must not be described as restoring an active archive, inbox,
or executable runtime session/frame. Anchor Snapshot rehydration aligns a
governance coordinate; it does not make a historical snapshot executable
Runtime state. Snapshot observation never creates `repository_runtime:
VERIFIED`, `authority: ASSIGNED`, `execution_assignment: ASSIGNED`, or an
active executor.

## Registry Gate

Before Role or Scope authority is evaluated, the requested Mode must resolve
through:

```text
.ai/runtime/project_instance/mode_registry.json
```

An unregistered label returns `MODE_NOT_REGISTERED` and must not be simulated.
Caller-provided Role, Scope, or Mode Profile must exactly match the registered
entry. Registry membership still does not create execution authority.

## Runtime Behavior

### Bare Role / Mode Label

```text
Commander: 컨덕터
Runtime: 네.
```

No role claim occurs.

### Ambiguous Role / Mode Phrase

```text
Commander: 컨덕트 모드
Runtime:
  -> role/mode intent detected
  -> authority source required
  -> if source unavailable: UNKNOWN
  -> no simulation
```

Recommended response:

```text
Conductor mode intent detected.
Source-backed governance Mode resolution is required.
Executable Runtime state is independent and remains UNKNOWN until a capable
Host returns raw Runtime evidence.
```

If the session runtime is attached but no certificate has been issued:

```text
Conductor mode selected as governance context.
Runtime Authority Certificate is missing.
Authority remains UNASSIGNED.
Execution Assignment remains UNASSIGNED.
Executable Runtime start, if needed, is a separate Host/profile decision.
```

If a context certificate exists but execution authority is unassigned:

```text
Conductor mode context is active.
Authority: UNASSIGNED.
Execution Assignment: UNASSIGNED.
Read-only coordination and proposal work may continue.
Mutation requires explicit assignment and pre-execution verification.
```

### Complete Transition Request With Source Available

```text
Commander: @GitHub ai-career Conductor mode로 전환해
Runtime:
  -> fetch/read authority source
  -> verify transition contract
  -> boundary / authority check
  -> transition only if allowed
```

### Complete Transition Request With Source Unavailable

```text
Commander: @GitHub ai-career Conductor mode로 전환해
Runtime:
  -> source unavailable
  -> authority UNKNOWN
  -> do not simulate
  -> wait
```

## Gate Integration Diagram

```text
User utterance
  -> Heartbeat Waiting Purpose Gate
       bare call / no purpose: heartbeat only
  -> No Forced Inference Proposal Gate
       partial command / action-only / incomplete scope: proposal or missing-anchor question
  -> Role / Mode Authority Gate
       role or mode label / transition request:
         verify source-backed authority
         UNKNOWN if source unavailable
         no role/mode simulation
  -> Runtime Commands
       complete command with authority and boundary
  -> Runtime Preflight
  -> Action
```

## Relationship to Heartbeat Waiting Purpose Gate

Heartbeat handles no-purpose inputs.

```text
컨덕터
  -> heartbeat only
  -> no role claim
```

Role / Mode Authority Gate applies when a role or mode transition is being considered.

## Relationship to No Forced Inference Proposal Gate

No Forced Inference prevents invented target, scope, or purpose.

Role / Mode Authority Gate prevents invented authority.

```text
No Forced Inference
  -> Do not invent what the user meant.

Role / Mode Authority
  -> Do not invent the authority to act as a role or mode.
```

## Relationship to Runtime Commands

Runtime Commands may identify a role/mode transition route only after the transition is complete enough to evaluate.

Role/mode labels do not bypass authority verification.

```text
Role/mode transition request
  -> Role / Mode Authority Gate
  -> authority verified?
       YES -> Runtime Commands / Preflight
       NO  -> UNKNOWN / no simulation
```

## Relationship to Session Runtime Governance

Session Runtime Governance owns the session boundary.

This gate adds a specific validation rule:

```text
Role/mode authority must be source-backed before the assistant claims the role or mode.
```

## Validation Cases

### Bare Label

```text
컨덕터
```

Expected:

```text
네.
```

No Conductor role claim.

### Ambiguous Mode Phrase

```text
컨덕트 모드
```

Expected:

```text
role/mode intent detected
authority source required
UNKNOWN if unavailable
no simulation
```

### Complete Transition, Source Available

```text
@GitHub ai-career Conductor mode로 전환해
```

Expected:

```text
fetch/read authority source
verify transition contract
boundary / authority check
transition only if allowed
```

### Complete Transition, Source Unavailable

```text
@GitHub ai-career Conductor mode로 전환해
```

Network/source unavailable.

Expected:

```text
Authority: UNKNOWN
Cannot enter or simulate Conductor mode without source-backed authority.
```

### Project-local Role / Mode

```text
@GitHub gcs MASTER mode로 전환해
```

Expected:

```text
verify GCS role/mode source
use project-local role gate
do not infer from label alone
```

## Non-goals

This gate is not:

- a ban on role or mode transition;
- a ban on role labels;
- a replacement for Runtime Commands;
- a replacement for No Forced Inference;
- permission to ignore complete commands;
- permission to simulate unavailable roles;
- a project-specific role registry.

## Candidate Adoption Note

Validate this gate against:

- fresh sessions;
- disconnected or degraded network conditions;
- connector-backed sessions before repository fetch;
- bare role labels;
- ambiguous role/mode phrases;
- complete transition requests with available sources;
- complete transition requests with unavailable sources;
- attached project role/mode gates.

Recent merged origin case:

- `.ai/queue/merged/20260703-0005-first-greeting-boot-effect.yaml`
- `0005` is the canonical evidence that mid-session role/mode labels can be recognized before authority proof is available.
