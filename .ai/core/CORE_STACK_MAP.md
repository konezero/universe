# Core Stack Map

Status: Candidate Core Architecture Map
Scope: ai-career
Parent: `.ai/core/README.md`
Created: 2026-07-01

## Purpose

This document shows how the current ai-career Core stack connects.

It is a map, not a new policy layer.

Use it to decide where a concept belongs and which document should be loaded first.

## One-Line Stack

```text
L0 INTERPRETS.
L1 ALIGNS.
ANCHOR TIME ADVANCES THE CURRENT COORDINATE.
LIFECYCLE ORDERS SESSION AUTHORITY.
COMMANDS ROUTE INSIDE LIFECYCLE.
PREFLIGHT CHECKS READINESS.
INSTRUCTIONS DEFINE THE INTERFACE.
PROJECT ANCHOR LOCALIZES.
VALIDATION RECORDS EVIDENCE.
L2 EXECUTES.
TASK FRAMES ISOLATE BOUNDED WORK.
ORCHESTRATOR CONNECTS.
CORE SERVICES ACT.
L3 PRESERVES.
```

## Stack Diagram

```text
Current Command / Trigger
  ↓
Runtime Commands
  ↓
Mode Registry
  ↓
Runtime Preflight
  ↓
Runtime Instruction Set
  ↓
Project Anchor
  ↓
OS Validation Evidence
  ↓
Runtime Orchestrator
  ↓
L0 AI Runtime Governance
  ↓
L1 Session Framework
  ↓
L2 Runtime Model
  -> optional Task Frame Orchestration
  ↓
Core Services
  ↓
L3 Persistence Model
  ↓
Result / Durable Artifact / Completion State
```

## OS_UPDATE Fast Path

When the command or mission is `OS_UPDATE`, route directly to Runtime Commands and Runtime Instruction Set after Core Stack Map, then localize through Project Anchor and record validation evidence.

```text
README.md
  -> .ai/README.md
  -> .ai/core/README.md
  -> .ai/core/CORE_STACK_MAP.md
  -> .ai/core/INTENT_FIRST_ROUTING_GATE.md
  -> .ai/core/COMMANDER_WAIT_BUFFER_RULE.md
  -> .ai/core/RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md
  -> .ai/core/RUNTIME_COMMANDS.md
  -> .ai/core/RUNTIME_INSTRUCTION_SET.md
  -> .ai/core/RUNTIME_STATE_TRUST_GATE.md
  -> .ai/core/ANCHOR_TEMPORAL_COORDINATE.md
  -> .ai/core/RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md
  -> .ai/core/PROJECT_ANCHOR.md
  -> .ai/core/OS_VALIDATION_EVIDENCE.md
  -> OS_UPDATE Flow
  -> project-local assembly / update / validate / evidence
```

`OS_UPDATE` is not Conductor Inbox review.

```text
OS_UPDATE
  -> Runtime Instruction Set
  -> Project Anchor
  -> OS Validation Evidence
  -> project-local assembly/update/validate/evidence

Conductor Resume / Inbox
  -> restore / candidate review / governance adoption
```

## External Project Instruction Model

Attached projects should not need custom ai-career templates.

They should receive common runtime instructions and assemble their own local runtime instance.

```text
One ai-career Runtime Template Model
  -> Common Runtime Instructions
  -> Project Anchor
  -> Project-local Assembly
  -> OS Validation Evidence
```

Primary project instructions:

```text
OS_INSTALL
OS_UPDATE
OS_PREFLIGHT
OS_STATUS
OS_VALIDATE
OS_ROLLBACK
OS_SYNC
```

## External Context Routing

When input comes from an attached project or external work context, Runtime Commands and Runtime Preflight first check whether the input is a command, instruction, reusable candidate, or project-local instance material.

```text
External Project Context
  ↓
Runtime Commands
  ↓
Runtime Preflight
  ↓
Instruction / Template Candidate / Project Instance?
  ├─ OS_INSTALL / OS_UPDATE -> Runtime Instruction Set -> Project Anchor -> OS Validation Evidence -> Project-local Assembly
  ├─ Template Candidate     -> Carrier / Conductor / ai-career Candidate Path
  └─ Project Instance       -> Project Master / Worker / Local Project Path
```

Reuse direction:

```text
ai-career Runtime Instruction
  -> Project Anchor
  -> attached project
  -> project-local assembly
  -> OS validation evidence
```

Promotion direction:

```text
Project observation
  -> Carrier collects
  -> Conductor reviews
  -> Template Candidate
  -> ai-career instruction/model update if adopted
```

## Anchor Flow

```text
ACTIVE ANCHOR IS THE POINT OF REFERENCE.

WHEN INTERPRETATION IS UNCERTAIN,
PULL ACTIVE ANCHOR FORWARD.

WHEN USER INPUT IS OBSERVED,
ADVANCE THE SAME CURRENT ANCHOR'S observed_at ONLY.

WHEN AN ANCHOR IS REPLACED,
PRESERVE IT AS A BEYOND FOOTPRINT.
```

Project anchor rule:

```text
ACTIVE PROJECT ANCHOR IS THE LOCAL POINT OF REFERENCE.

WHEN PROJECT INTERPRETATION IS UNCERTAIN,
PULL THE PROJECT ANCHOR FORWARD.
```

Evidence rule:

```text
OS_UPDATE IS NOT READY UNTIL OS_VALIDATE EVIDENCE IS RECORDED.
```

Meaning:

```text
Strong context / competing triggers / stale assumptions
  -> pull Active Anchor forward
  -> resolve runtime command when present
  -> check runtime readiness
  -> resolve runtime instruction when attached project asks for OS install/update
  -> pull Project Anchor forward for local assembly
  -> record OS_VALIDATE evidence for local compatibility
  -> route external input as instruction, template candidate, or project instance when needed
  -> rebuild session alignment when needed
  -> rebuild runtime frame
  -> invoke services under active frame
  -> persist selected meaning only
```

## Layer Responsibilities

| Layer | Document | Verb | Responsibility |
| --- | --- | --- | --- |
| L0 | `AI_RUNTIME_GOVERNANCE.md` | interprets | Defines Active Anchor, authority interpretation, contract, safety, and boundary reference |
| L1 | `SESSION_FRAMEWORK.md` | aligns | Defines Boot, Reboot, Resume Candidate, Mode Change, and Session Out lifecycle transitions |
| Lifecycle | `RUNTIME_LIFECYCLE.md` | orders | Defines Session Start, Runtime Bootstrap, gates, authority certificate, boundary check, pre-execution verification, execution, and Session End |
| Intent Gate | `INTENT_FIRST_ROUTING_GATE.md` | classifies | Ensures utterance intent is classified before known command, mode, role, or anchor tokens can route |
| Wait Buffer | `COMMANDER_WAIT_BUFFER_RULE.md` | defers | Blocks mutation while the Commander is still speaking and releases buffered fragments only through normal gates |
| Anchor Frame Constraint | `RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md` | constrains | Requires runtime routing and execution to originate from the active Runtime Anchor Frame and source-backed runtime evidence |
| Entry | `RUNTIME_COMMANDS.md` | routes | Defines runtime command entry points and first-route decisions such as OS_UPDATE Fast Path inside the lifecycle |
| Mode Registry | `MODE_REGISTRY.md` | resolves | Defines the source-backed Mode allow-list, immutable ai-career Modes, and MASTER-managed project Mode boundaries |
| Bootstrap | `SESSION_RUNTIME_BOOTSTRAP.md` | reconstructs | Derives a Current Interpretation Basis and fresh Session Boot Anchor from current source-backed evidence without reactivating historical Anchors |
| State Trust | `RUNTIME_STATE_TRUST_GATE.md` | verifies | Protects active `*_ING` state, state provenance, narrative time, and evidence priority before continuation |
| Anchor Time | `ANCHOR_TEMPORAL_COORDINATE.md` | advances | Defines Host-time input observation, forward-only Current Anchor time, Beyond footprints, and Current-Basis-gated recall adoption |
| Runtime Image | `RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md` | assembles | Defines Boot Evidence Bundle and session-scoped Runtime Image assembly while excluding observed_at-only touches from invalidation |
| Authority Credential | `RUNTIME_AUTHORITY_CERTIFICATE.md` | carries | Defines the session-scoped Authority Credential generated during Runtime Boot and destroyed at Session End |
| Authority Binding | `RUNTIME_AUTHORITY_EXECUTION_BINDING.md` | binds | Verifies certificate binding to current writer, execution surface, assignment, approval, source, anchor, and boundary before mutation |
| Final Guard | `PRE_EXECUTION_VERIFICATION.md` | verifies | Rechecks certificate, anchor, runtime state, boundary, target, and permission immediately before execution |
| Guard | `RUNTIME_PREFLIGHT.md` | checks | Verifies repository, role, boot depth, authority, scope, layer, task, service readiness, and instruction/template/instance routing before execution |
| Interface | `RUNTIME_INSTRUCTION_SET.md` | defines | Defines common OS_INSTALL, OS_UPDATE, OS_PREFLIGHT, OS_STATUS, OS_VALIDATE, OS_ROLLBACK, and OS_SYNC instructions for project-local assembly |
| Local Anchor | `PROJECT_ANCHOR.md` | localizes | Defines the project-local point of reference for assembling or updating local runtime surfaces from the instruction contract |
| Evidence | `OS_VALIDATION_EVIDENCE.md` | records | Defines where OS_VALIDATE, VERSION_MANIFEST checks, canonical contract freshness, and compatibility evidence are recorded |
| L2 | `RUNTIME_MODEL.md` | executes | Defines Base, Anchor, Runtime, Frame, Work, Task, Result, Weak, and Discard |
| L2 Task Frame | `TASK_FRAME_ORCHESTRATION.md` | isolates | Defines optional subordinate Task Frames, non-authoritative Boss/Workers, Result Packet rejoin, and Parent-only adoption |
| Connector | `RUNTIME_ORCHESTRATOR.md` | connects | Moves commands through L0/L1/L2, invokes Core Services, and routes selected results to L3 |
| Services | `CORE_SERVICES.md` | acts | Performs scoped tool/capability actions under active frame |
| L3 | `PERSISTENCE_MODEL.md` | preserves | Defines Summary, Checkpoint, Resume Candidate, Memory, Archive, and storage boundaries |

## Core Identity

`AI_CORE.md` defines the reusable operating doctrine for roles, boot, memory, checkpoint, merge, and project attachment.

It is part of Core, but it is no longer the top-level runtime governance layer.

```text
AI_RUNTIME_GOVERNANCE.md
  -> top-level runtime reference model

AI_CORE.md
  -> reusable operating doctrine and role/memory/checkpoint model
```

## Primary Read Order

For full Core boot or policy work:

```text
1. .ai/core/README.md
2. .ai/core/CORE_STACK_MAP.md
3. .ai/core/MODE_REGISTRY.md
4. .ai/core/RUNTIME_LIFECYCLE.md
5. .ai/core/INTENT_FIRST_ROUTING_GATE.md
6. .ai/core/COMMANDER_WAIT_BUFFER_RULE.md
7. .ai/core/RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md
8. .ai/core/RUNTIME_COMMANDS.md
9. .ai/core/SESSION_RUNTIME_BOOTSTRAP.md
10. .ai/core/RUNTIME_STATE_TRUST_GATE.md
11. .ai/core/ANCHOR_TEMPORAL_COORDINATE.md
12. .ai/core/RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md
13. .ai/core/RUNTIME_AUTHORITY_CERTIFICATE.md
14. .ai/core/RUNTIME_AUTHORITY_EXECUTION_BINDING.md
15. .ai/core/PRE_EXECUTION_VERIFICATION.md
16. .ai/core/AI_RUNTIME_GOVERNANCE.md
17. .ai/core/SESSION_FRAMEWORK.md
18. .ai/core/RUNTIME_PREFLIGHT.md
19. .ai/core/RUNTIME_INSTRUCTION_SET.md
20. .ai/core/PROJECT_ANCHOR.md
21. .ai/core/OS_VALIDATION_EVIDENCE.md
22. .ai/core/RUNTIME_MODEL.md
23. .ai/core/TASK_FRAME_ORCHESTRATION.md
24. .ai/core/RUNTIME_ORCHESTRATOR.md
25. .ai/core/CORE_SERVICES.md
26. .ai/core/PERSISTENCE_MODEL.md
27. .ai/core/AI_CORE.md
```

For lightweight work, read only the smallest slice required.

For `OS_UPDATE`, use the fast path:

```text
CORE_STACK_MAP.md
  -> RUNTIME_COMMANDS.md
  -> RUNTIME_INSTRUCTION_SET.md
  -> PROJECT_ANCHOR.md
  -> OS_VALIDATION_EVIDENCE.md
  -> PROJECT_INSTANCE_RUNTIME_RULE.md
  -> RUNTIME_STATUS_SOURCE_RULE.md
```

## Classification Questions

When a new concept appears, ask:

```text
1. Does it define the reference point or authority boundary?
   -> L0

2. Does it start, rebuild, restore, switch, or end a session?
   -> L1

3. Does it define a runtime command or first route?
   -> Runtime Commands

4. Does it define the order from Session Start to Execution to Session End?
   -> Runtime Lifecycle

5. Does it reconstruct authority at Runtime Boot?
   -> Session Runtime Bootstrap

6. Does it define the current-session authority credential?
   -> Runtime Authority Certificate

7. Does it define a session-scoped boot artifact assembled from source-backed evidence?
   -> Runtime Image Assembly Contract

8. Does it bind the authority credential to current writer, surface, assignment, approval, target, and boundary?
   -> Runtime Authority Execution Binding

9. Does it verify final authority immediately before execution?
   -> Pre-Execution Verification

10. Does it check whether the current runtime is ready for this task?
   -> Runtime Preflight

11. Does it define a common instruction for attached projects?
   -> Runtime Instruction Set

12. Does it define the project-local reference point for assembly?
   -> Project Anchor

13. Does it define OS_VALIDATE evidence, VERSION_MANIFEST check, or contract freshness proof?
   -> OS Validation Evidence

14. Does it classify external input as instruction, template candidate, or project instance?
   -> Runtime Commands / Runtime Preflight / Runtime Instruction Set / Project Anchor / Project Instance boundary

15. Does it define the active command frame or task lifecycle?
   -> L2

16. Does it isolate bounded Boss/Worker subwork and return candidate results to a current Parent?
   -> Task Frame Orchestration

17. Does it coordinate multiple layers or service calls?
   -> Runtime Orchestrator

18. Does it perform an action through a tool or capability?
   -> Core Services

19. Does it remain durable or recoverable after execution?
   -> L3

20. Does it define reusable role/core doctrine?
   -> AI Core

21. Does it define how Host physical time advances Current Anchor observations,
    preserves Beyond footprints, or realigns an adopted recall?
   -> Anchor Temporal Coordinate
```

## Common Placements

```text
Active Anchor        -> L0
Anchor physical time -> Anchor Temporal Coordinate
Beyond footprint     -> Anchor Temporal Coordinate / L3 evidence boundary
Beyond recall        -> Anchor Temporal Coordinate / Parent adoption boundary
Project Anchor       -> Project Anchor
Validation Evidence  -> OS Validation Evidence
VERSION_MANIFEST     -> OS Validation Evidence / status source
Contract Freshness   -> OS Validation Evidence
Contract             -> L0
Authority            -> L0
Safety Boundary      -> L0
Boot                 -> L1
Reboot               -> L1
Resume Candidate     -> L1 / L3 boundary
Mode Change          -> L1
Runtime Command      -> Runtime Commands
Commander Wait       -> Commander Wait Buffer Rule
Wait Buffer          -> Commander Wait Buffer Rule
Runtime Anchor Frame Routing -> Runtime Anchor Frame Routing Constraint
Runtime Lifecycle    -> Runtime Lifecycle
Runtime Boot         -> Session Runtime Bootstrap
Authority Bootstrap  -> Session Runtime Bootstrap
Runtime Image        -> Runtime Image Assembly Contract
Boot Evidence Bundle -> Runtime Image Assembly Contract
Authority Credential -> Runtime Authority Certificate
Certificate Binding  -> Runtime Authority Execution Binding
Certificate Destroy  -> Runtime Authority Certificate
Pre-Execution Check  -> Pre-Execution Verification
OS_INSTALL Command   -> Runtime Commands
OS_UPDATE Command    -> Runtime Commands
Preflight            -> Runtime Preflight
Readiness Check      -> Runtime Preflight
Role Alignment       -> Runtime Preflight
Boot Depth Check     -> Runtime Preflight
Scope Check          -> Runtime Preflight
Runtime Instruction  -> Runtime Instruction Set
OS_INSTALL Contract  -> Runtime Instruction Set
OS_UPDATE Contract   -> Runtime Instruction Set
OS_VALIDATE          -> Runtime Instruction Set / OS Validation Evidence
Project-local root   -> Project Anchor
Project-local entry  -> Project Anchor
Template Candidate   -> Runtime Preflight / L3 candidate path
Project Instance     -> Runtime Preflight / PROJECT_INSTANCE_RUNTIME_RULE.md
External Context     -> Runtime Commands / Runtime Preflight
Base                 -> L2
Runtime              -> L2
Frame                -> L2
Task                 -> L2
Task Frame           -> Task Frame Orchestration
Boss / decision_role -> Task Frame Orchestration
Result Packet Rejoin -> Task Frame Orchestration
Parent Adoption Gate -> Task Frame Orchestration
Weak                 -> L2
Discard              -> L2
GitHub action        -> Core Services
Storage provider     -> Core Services / L3 boundary
Memory Sync Writer   -> Core Services / L3 boundary
Status report        -> Core Services + source rule
Summary              -> L3
Checkpoint           -> L3
Memory               -> L3
Archive              -> L3
```

## Runtime Command Path

Example path for `@GitHub 메모싱크`:

```text
@GitHub 메모싱크
  -> Runtime Commands identifies MEMORY_SYNC
  -> Runtime Preflight checks repository / role / authority / service readiness
  -> Runtime Orchestrator parses trigger
  -> L0 pulls Active Anchor forward when needed
  -> L1 aligns session as GitHub/Conductor context
  -> L2 builds Memory Sync task frame
  -> Core Services invoke GitHub / Memory / Storage
  -> L3 persists selected memory artifact
  -> Orchestrator reports completion state
```

Example path for `리부트`:

```text
리부트
  -> Runtime Commands identifies REBOOT
  -> Runtime Preflight checks whether current boot/session state is sufficient to handle reboot
  -> Runtime Orchestrator parses trigger
  -> L0 pulls Active Anchor forward
  -> L1 rebuilds session alignment
  -> L2 discards stale working assumptions
  -> L2 rebuilds runtime frame
  -> Orchestrator reports READY
```

Example path for role mismatch:

```text
Role: Project Master
Task: Carrier responsibility absorption
  -> Runtime Commands identifies command if present
  -> Runtime Preflight checks role/task alignment
  -> Preflight reports NOT READY or PARTIAL READY
  -> Recommended action: coordinate boundary, do not absorb Carrier responsibilities
  -> Orchestrator does not execute absorption task
```

Example path for external project OS update:

```text
Source: Attached Project
Command: OS_UPDATE
  -> Runtime Commands resolves OS_UPDATE Fast Path
  -> Runtime Instruction Set resolves OS_UPDATE contract
  -> Project Anchor identifies local reference point
  -> OS Validation Evidence defines evidence surface
  -> Runtime Preflight checks local readiness
  -> Project assembles or updates local runtime instance
  -> OS_VALIDATE records evidence
  -> reusable observations return as candidates only when appropriate
```

## Anti-Drift Rule

```text
IF A CONCEPT STARTS TO OWN TOO MUCH,
CLASSIFY IT BY LAYER AGAIN.
```

Examples:

```text
Memory stores knowledge, but does not decide authority.
GitHub performs actions, but does not decide policy.
Archive preserves history, but does not become active frame.
Resume proposes recovery, but does not auto-grant role authority.
Commands route first, but do not define instruction contracts.
Preflight checks readiness, but does not execute the task.
Instructions define common interface, but projects assemble local instances.
Project Anchor localizes assembly, but does not redefine the canonical contract.
OS Validation Evidence records proof, but does not define the instruction contract.
Template candidates are reviewed, not auto-adopted.
Project instances consume instructions, but do not own Core contracts.
Core defines doctrine, but Runtime Governance defines the reference model.
```

## Adoption Status

This is a candidate map for the current Core stack.

It should be updated whenever the Core stack gains a new canonical layer or when a candidate layer is deprecated.
