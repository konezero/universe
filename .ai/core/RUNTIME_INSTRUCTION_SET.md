# Runtime Instruction Set

Status: Candidate Core Architecture  
Scope: ai-career  
Layer: Runtime Interface / Project Runtime Contract
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`  
Created: 2026-07-01  
Updated: 2026-07-03

## Purpose

Runtime Instruction Set defines the common instructions that a project can use
to install, update, validate, or operate its durable AI runtime layer.

This document separates two lifecycles that must not share one command or
readiness claim:

```text
OS_INSTALL / OS_UPDATE
  -> install or reconcile durable project-local Runtime surfaces
  -> require immutable source, durable target, proposal, approval, and write scope

BOOT / REBOOT / SESSION_ATTACH
  -> create, recreate, or connect a disposable Session Runtime
  -> do not install or update project files
```

Canonical boundary:

```text
RUNTIME_COMMANDS.md
  -> user-facing lifecycle command meanings

PROJECT_RUNTIME_INSTALL.md
  -> internal durable installation operation
```

## Core Declaration

```text
PROVIDE COMMON RUNTIME INSTRUCTIONS.

OS_INSTALL TARGET IS ONE EXPLICIT DURABLE PROJECT ROOT.
OS_INSTALL SOURCE IS ONE IMMUTABLE AI-CAREER SOURCE.

LET EACH PROJECT ASSEMBLE ITS LOCAL RUNTIME FROM THE INSTRUCTION CONTRACT ONLY AFTER OS_INSTALL OR OS_UPDATE WRITE SCOPE IS CONFIRMED.

USE OS_INSTALL TO SCAN, PROPOSE, APPROVE, AND INSTALL PROJECT-LOCAL RUNTIME STATE.
PROJECT_RUNTIME_INSTALL IS AN INTERNAL OPERATION NAME ONLY.

USE BOOT TO CREATE THE SESSION RUNTIME AND CURRENT ANCHOR.

USE PROJECT ANCHOR AS THE LOCAL POINT OF REFERENCE.

OS_UPDATE IS NOT COMPLETE UNTIL OS_VALIDATE EVIDENCE IS RECORDED.

NODE / MODE COORDINATES MUST FOLLOW NODE_MODE_COORDINATE_CONTRACT.md.

RUNTIME STATE TRUST MUST FOLLOW RUNTIME_STATE_TRUST_GATE.md.

RUNTIME IMAGE ASSEMBLY MUST FOLLOW RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md.

RUNTIME AUTHORITY EXECUTION BINDING MUST FOLLOW RUNTIME_AUTHORITY_EXECUTION_BINDING.md.

RUNTIME ROUTING MUST FOLLOW RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md.
```

## Command Entry

Runtime Instructions are entered through Runtime Commands.

```text
Command routes first.
Instruction defines the contract.
Target / Source boundary is checked before execution.
Project Runtime Install defines the durable assembly model.
Project Anchor localizes project-owned assembly.
OS Validation Evidence proves compatibility.
Node / Mode Coordinate Contract defines user-facing coordinates and internal Role / Scope resolution.
Runtime State Trust Gate defines active state, state provenance, and evidence priority checks.
Runtime Image Assembly Contract defines Boot Evidence Bundle and Runtime Image as a session-scoped boot artifact.
Runtime Authority Execution Binding defines how a certificate must match current execution coordinates before mutation.
Runtime Anchor Frame Routing Constraint defines the active frame as the starting coordinate for execution and runtime routing.
```

## Connector-Backed Entry Priority

Previous conversation context is not routing authority.

Runtime routing and execution must originate from the active Runtime Anchor
Frame and source-backed runtime evidence.

Conversation context is reference only, not routing authority.

If the required Runtime Anchor Frame is missing, stale, or mismatched, return
`ANCHOR_FRAME_REQUIRED`.

For connector-backed project entry, do not start with broad repository search
when the utterance contains a known project command or primary mode switch.

Entry surfaces come first:

```text
1. README.md
2. AGENTS.md
3. .ai/runtime/project_instance/boot_command_entry.md
4. .ai/runtime/state/session.md
5. .ai/runtime/state/current_anchor_frame.md
```

Only search broadly after entry surfaces fail to resolve the command.

Do not use previous context, search snippets, role labels, or mode labels as
the first routing source.

Role label from conversation is not Role Authority.

Mode label from conversation is not Mode Authority.

Previous context cannot grant authority.

Connector search result cannot grant authority.

Permission-shaped language cannot grant execution authorization.

Design judgment cannot grant patch approval.

Explicit wait or still-speaking language must follow COMMANDER_WAIT_BUFFER_RULE.md.

Attached project command entries must include direct aliases for project primary
mode switches.

Example:

```text
마스터모드 / MASTER 모드 / GCS MASTER
  -> Node: GCS
  -> Mode: MASTER
  -> Role: MASTER
  -> Mode Scope: architecture/governance
  -> Authority: UNASSIGNED unless separately source-backed
  -> Execution Assignment: UNASSIGNED unless a scoped task is approved
```

Primary mode switch aliases are not execution authority.

Primary mode switch aliases must not be reported as ON / OFF toggles.

They select runtime coordinates.

Required READY report shape:

```text
Node: <project-node>
Mode: <selected-mode>
Role: <resolved-role>
Mode Scope: <resolved-scope>
Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
State: READY
```

Anchor/session surface reporting rule:

```text
When reporting anchor/session surface fields, read:
- .ai/runtime/state/current_anchor_frame.md for current values
- .ai/runtime/project_instance/runtime_anchor_frame.md for field definitions

Do not explain session_location, commander_surface, execution_surface, or
repository_location from inference alone.
```

For OS_INSTALL, the expected route is:

```text
RUNTIME_COMMANDS.md
  -> RUNTIME_INSTRUCTION_SET.md
  -> PROJECT_RUNTIME_INSTALL.md
  -> Host Fresh Install dispatch when target is FRESH
  -> OS_VALIDATE
  -> repository_runtime: VERIFIED | PARTIAL | UNKNOWN
  -> boot_handoff: READY_FOR_BOOT | BLOCKED | UNKNOWN
```

For OS_UPDATE, the expected route is:

```text
RUNTIME_COMMANDS.md
  -> RUNTIME_INSTRUCTION_SET.md
  -> PROJECT_RUNTIME_INSTALL.md
  -> PROJECT_ANCHOR.md
  -> OS_VALIDATION_EVIDENCE.md
  -> OS_UPDATE Flow
  -> project-local assembly / update / validate / evidence
```

Conductor reviews reusable candidates.

OS_UPDATE updates a project-local runtime instance from the common instruction contract through Project Runtime Install and Project Anchor.

## Why Instructions Instead Of Many Templates

A template duplicated for every project becomes project-specific maintenance.

A common instruction can be interpreted by many projects.

```text
Template copy -> project-specific drift.
Instruction contract -> shared interface, local assembly.
```

The goal is:

```text
ai-career defines OS_INSTALL / OS_UPDATE / OS_VALIDATE and BOOT separately.
Each project maps write-delegated OS lifecycle instructions to its Project Anchor, files, tools, and runtime layout.
```

## Runtime Template vs Instruction

```text
Runtime Template
  -> the reusable conceptual model
  -> boot layer, preflight, governance, runtime, services, persistence

Runtime Instruction
  -> the command contract used by projects and sessions
  -> tells a write-delegated project what to install, update, or validate
  -> tells a session to route BOOT separately after installation

Project Runtime Install
  -> internal scan / proposal / approval / install model used by OS_INSTALL
  -> defines Node, Mode, and Mode-owned Rules for durable project runtime assembly

Project Anchor
  -> the local reference point
  -> tells the project where to assemble or update

OS Validation Evidence
  -> source-backed proof of session or repository compatibility

Project Instance
  -> the local implementation inside the attached project
  -> owned by the project
```

## Target / Source Model

```text
Source
  -> repository or files used to reconstruct runtime rules and state

Target
  -> explicit durable project root where `.ai` is installed or updated

Host
  -> execution surface that materializes source and runs the lifecycle command

Session Runtime
  -> disposable process-local state created later by BOOT

Durable Project Runtime
  -> project-local `.ai` runtime surface written only after explicit write delegation
```

Default GitHub connector interpretation:

```text
@GitHub ai-career OS_INSTALL
  -> source: konezero/ai-career
  -> target: UNKNOWN until explicitly confirmed
  -> result: INSTALL_PROPOSAL_REQUIRED
  -> repository_write: false until exact approval
```

Repository write interpretation requires one of:

```text
OS_INSTALL with an exact approved proposal
OS_UPDATE with an exact approved proposal
```

## Project Attachment Model

```text
Attached Project
  -> receives common Runtime Instruction
  -> runs Project Runtime Install model only when durable project assembly is intended
  -> identifies Project Anchor
  -> interprets instruction against project-local structure
  -> assembles or updates local runtime instance only after write scope is confirmed
  -> validates local result
  -> records validation evidence
  -> reports evidence back when needed
```

ai-career owns the instruction contract.

The project owns the Project Anchor, local assembly, and validation evidence.

Repository attachment alone does not grant repository write authority.

## Primary Instructions

### OS_INSTALL

Purpose:

```text
Install the complete durable project-local `.ai` Runtime into one explicit target.
```

Default assembly model:

```text
Resolve one immutable ai-career source.
Resolve and confirm one absolute durable project target.
Classify the target without mutation.
Present the exact managed-path install and validation proposal.
Wait for Commander approval and bounded write scope.
Dispatch the FRESH branch through the Host Fresh Install adapter.
Invoke the deterministic internal Project Runtime installer.
Run OS_VALIDATE and record raw evidence.
Return READY_FOR_BOOT without creating Session Runtime state.
```

Default target states:

```text
READY_FOR_BOOT
ALREADY_INSTALLED
PARTIAL
UNKNOWN
```

Session Runtime activation is not an OS_INSTALL result.

If repository runtime files already exist because of an earlier target confusion, classify them as:

```text
PROOF_ARTIFACT
CANDIDATE_RUNTIME_SURFACE
PARTIAL_REPOSITORY_EVIDENCE
```

until explicit OS_INSTALL or OS_UPDATE reconciliation promotes, updates, or removes them.

### Internal PROJECT_RUNTIME_INSTALL Operation

Purpose:

```text
Implement the approved durable OS_INSTALL write and validation plan.
```

Required assembly model:

```text
Verify the approved OS_INSTALL request, immutable source, and durable target.
Verify target classification is FRESH and approval coordinates match exactly.
Consume the approved Node / initial MASTER Mode / managed-path plan.
Resolve the project-runtime distribution manifest at one immutable source commit.
Materialize registered Core, Template, and required Runtime executable surfaces.
Record source path, target path, source commit, and SHA-256 for every managed surface.
Install project-local .ai runtime surface and generated project instance.
Identify or create Project Anchor.
Assemble the mandatory initial MASTER primary-mode contract from
`.ai/templates/project_primary_mode/README.md`.
Initialize project-owned runtime state and current anchor frame surfaces.
Create validation evidence surface.
Run OS_VALIDATE.
Record OS_VALIDATE evidence.
```

Immutable source transport is Host-dependent but contract-equivalent:

```text
local Git CLI available
  -> use the distribution installer with --source-root

GitHub connector file/blob reads plus writable sandbox available
  -> Host reads the project Runtime source index
  -> Host fetches every indexed path at one full commit
  -> Host materializes a content-addressed source bundle
  -> use the distribution installer with --source-bundle

authenticated GitHub CLI plus writable sandbox available
  -> Host resolves one full commit through gh api
  -> Host fetches every indexed path and blob SHA at that commit
  -> Host records source provider github-cli without relabeling
  -> use the distribution installer with --source-bundle
```

The connector or GitHub CLI is called by the Host/Skill, not by the Python
installer. A partial remote fetch, mixed ref, missing blob evidence, provider
relabeling, or synthetic commit must stop before project mutation.

Required assembly targets:

```text
Project Runtime Install
Project Anchor
Primary Mode Bootstrap (MASTER)
Boot Layer
Runtime Preflight
Runtime Governance Reference
Runtime Model / Active Frame
Runtime Orchestrator
Core Service Boundary
Persistence / Checkpoint Boundary
Status / Validation Surface
OS Validation Evidence Surface
```

Expected result:

```text
Project can report its Project Anchor, installed Runtime surface, local BOOT path,
validation evidence path, and READY_FOR_BOOT handoff. Session state remains
UNKNOWN until BOOT.
```

### OS_UPDATE

Purpose:

```text
Update an existing project-local runtime layer to match the current ai-career instruction contract.
```

Required update targets:

```text
Identify Project Runtime Install state.
Identify Project Anchor.
Check VERSION_MANIFEST.md existence and status.
Record canonical contract source/freshness evidence.
Compare current project runtime surface.
Compare ai-career instruction contract.
Patch project-local assembly.
Validate local boot/readiness/status behavior.
Record OS_VALIDATE evidence.
Record project-local checkpoint or update evidence.
```

If the target predates the managed distribution manifest, OS_UPDATE must not
fall back to `--force` or treat familiar files as owned. It must route through
the explicit legacy migration gate declared by Project Runtime Install:

```text
detect unmanaged collision
  -> select source-backed migration profile
  -> verify complete legacy footprint before writing
  -> archive original bytes and hashes
  -> install current managed distribution
  -> reinitialize active session/currentness as UNKNOWN
  -> OS_VALIDATE
```

Legacy `READY`, `VERIFIED`, Anchor, or Currentness prose is recovery input only.
It cannot be promoted into the new Runtime State without separate current
Host/checkpoint evidence.

Expected result:

```text
Project runtime remains locally assembled but compatible with the current instruction contract, with validation evidence recorded.
```

Route boundary:

```text
OS_UPDATE
  -> Runtime Commands
  -> Runtime Instruction Set
  -> Project Runtime Install
  -> Project Anchor
  -> OS Validation Evidence
  -> Project-local assembly/update/validate

Reusable observation
  -> candidate review
  -> adoption decision
```

### OS_PREFLIGHT

Purpose:

```text
Check whether the current session or project-local runtime is ready for the requested instruction.
```

Checks:

```text
Repository source
Execution host target
Repository write scope
Node
Mode
Mode Rule Set
Boot Depth
Authority
Scope
Layer
Task
Instruction
Project Runtime Install
Project Anchor
VERSION_MANIFEST
Canonical Contract Source
Boot Evidence Bundle / Runtime Image status when used
Runtime Authority Certificate execution binding status when mutation is in scope
OS Validation Evidence Surface
Service
Persistence
```

Expected result:

```text
READY / NOT READY / PARTIAL READY / UNKNOWN
```

### OS_STATUS

Purpose:

```text
Report the current session and/or project-local runtime state using source-backed evidence.
```

Expected result:

```text
Repository source
Durable Project Runtime target
Current Session Runtime Host
Repository write scope
Session Runtime state
Repository Runtime Surface state
Project Runtime Install state
Current Node
Current Mode
Mode Rule Set status
Project Anchor
Runtime install state
Boot depth
Instruction compatibility
VERSION_MANIFEST status
Canonical contract source/freshness status
Latest OS_VALIDATE evidence
Last checkpoint/update evidence
Unknown fields explicitly marked UNKNOWN
```

Recommended status split:

```text
Session Runtime: READY / PARTIAL / UNKNOWN
Repository Runtime Surface: PROOF_ONLY / PARTIAL / VERIFIED / UNKNOWN
Repository Write Scope: NONE / EXPLICIT / UNKNOWN
```

### OS_VALIDATE

Purpose:

```text
Validate whether a session runtime or project-local runtime assembly satisfies the instruction contract and record evidence.
```

Expected result:

```text
Pass / Fail / Partial / Unknown
Session Runtime status included
Repository Runtime Surface status included when repository surfaces exist
Repository write scope included
Project Runtime Install status included when relevant
Project Anchor status included when relevant
VERSION_MANIFEST status included when relevant
Canonical contract source included
Missing surfaces listed explicitly
Evidence path recorded when durable evidence is available
```

Source-backed comparison rule:

```text
OS_VALIDATE MAY read a session-scoped Runtime Image.
OS_VALIDATE MUST validate against Git-backed source.
PASS is allowed only when Runtime Image and Git-backed source agree.
Runtime Image disagreement MUST NOT be treated as authority.
If Runtime Image and Git source disagree, Git-backed source wins.
If the disagreement cannot be safely resolved, report UNKNOWN.
If Runtime Image is stale while Git validates, report STALE.
If required source-backed surfaces are missing, report PARTIAL or FAIL.
```

Runtime Image authority boundary:

```text
Runtime Image is a boot artifact.
Runtime Image is disposable.
Runtime Image is not authority.
Validation may read Runtime Image.
Authority reads Git-backed source.
```

Runtime Image assembly boundary:

```text
Boot Evidence Bundle is source-backed input.
Runtime Image assembly is environment-specific.
Core does not require SQLite.
Core does not require durable runtime.db.
Runtime Image stale/current status must be source-backed when reported.
```

Runtime Authority Certificate execution boundary:

```text
Certificate Presence != Execution Authority.
Certificate must bind to current writer, execution surface, repository location, execution assignment, approval evidence, target, boundary, anchor, frame, and source coordinates before mutation.
Stale or mismatched certificate blocks mutation.
Read-only review may continue only when stale status is surfaced.
```

Comparison result mapping:

| Runtime Image | Git Source | Final | Meaning |
|---|---|---|---|
| PASS | PASS | PASS | Both agree on verified source-backed evidence |
| PASS | PARTIAL / FAIL | UNKNOWN | Runtime Image overclaims compared to Git |
| PARTIAL / FAIL | PASS | STALE | Runtime Image is behind or inconsistent |
| PARTIAL | PARTIAL | PARTIAL | Both agree required surfaces are missing |
| FAIL | FAIL | FAIL | Both fail |
| UNKNOWN | any | UNKNOWN | Runtime Image cannot be verified |
| any | UNKNOWN | UNKNOWN | Git source cannot be verified |

Runtime Image comparison is optional when no Runtime Image exists.

Git-backed source validation remains required for PASS.

### OS_ROLLBACK

Purpose:

```text
Return a project-local runtime assembly to a previous known checkpoint when an update fails.
```

Expected result:

```text
Project-local runtime restored to known checkpoint or failure reason reported.
```

### OS_SYNC

Purpose:

```text
Synchronize selected project-local runtime observations back toward ai-career when they are reusable candidates.
```

Expected result:

```text
Reusable observation routed as candidate.
Project-local details remain local.
```

## Instruction Flow

```text
Instruction arrives
  -> Runtime Commands resolves first route
  -> Target / Source boundary check
  -> Runtime Preflight checks readiness
  -> Resolve instruction type
  -> Run the internal Project Runtime Install model for OS_INSTALL
  -> Identify Project Anchor when project assembly/update is in scope
  -> Check VERSION_MANIFEST when project assembly/update is in scope
  -> Record canonical contract source/freshness
  -> Resolve durable project target and Host execution surface separately
  -> Install / update / validate Project Runtime, or route BOOT separately
  -> Record OS_VALIDATE evidence when durable evidence is available
  -> Report evidence
  -> Persist local checkpoint if needed and authorized
  -> Route reusable observations back as candidates when appropriate
```

## OS_INSTALL Flow

```text
OS_INSTALL
  -> OS_PREFLIGHT
  -> resolve one immutable ai-career source
  -> confirm one absolute durable project target
  -> inspect and classify the target without mutation
  -> present the exact managed-path install and validation proposal
  -> wait for Commander approval and bounded write scope
  -> FRESH: invoke Host Fresh Install adapter
  -> ALREADY_INSTALLED: no mutation; report READY_FOR_BOOT
  -> UPDATE_PROPOSAL: stop and route to a separate OS_UPDATE proposal
  -> MIGRATION_REVIEW_REQUIRED: stop for reviewed migration
  -> UNKNOWN_OR_BLOCKED: stop without mutation
  -> run OS_VALIDATE after an approved install
  -> report repository-runtime evidence and READY_FOR_BOOT
```

OS_INSTALL does not assemble a Session Runtime Image. A later `BOOT` consumes
the installed Runtime, creates a fresh Session Bootstrap and Current Anchor, and
produces separate session-scoped evidence.

## Internal PROJECT_RUNTIME_INSTALL Flow

```text
OS_INSTALL approved FRESH branch
  -> internal PROJECT_RUNTIME_INSTALL operation
  -> OS_PREFLIGHT
  -> verify immutable source, durable target, and exact approval binding
  -> verify target classification is FRESH
  -> consume approved Node / initial MASTER Mode / managed-path plan
  -> resolve the immutable distribution manifest and source commit
  -> materialize registered Core / Template / Runtime executable surfaces
  -> write the project-local installed manifest with per-file hashes
  -> identify or create Project Anchor
  -> assemble mandatory MASTER primary-mode entry and Role / Mode Scope binding
  -> initialize project-owned state and anchor surfaces as UNINITIALIZED
  -> assemble Boot Layer
  -> assemble Runtime Preflight
  -> assemble Governance reference
  -> assemble Runtime Model / Frame rules
  -> assemble Orchestrator path
  -> assemble Core Service boundary
  -> assemble Persistence boundary
  -> create validation evidence surface
  -> run OS_VALIDATE
  -> record OS_VALIDATE evidence
  -> report installed runtime surface
  -> return READY_FOR_BOOT
```

Creating only the MASTER entry, project state, and validation files is a
project-instance structure result. It must not be promoted to standalone
repository-runtime `VERIFIED` unless the installed distribution manifest and
all required managed-surface hashes also validate.

For a local PC / CLI / Codex / VSCode Host, a verified Project Runtime may route
the requested primary Mode to `BOOT`. BOOT reads the installed project boot
entry and creates a fresh process-local Session Runtime. Local Host capability
does not create Mode Authority, Write Scope, or Execution Assignment.

## OS_UPDATE Flow

```text
OS_UPDATE
  -> OS_PREFLIGHT
  -> identify Project Runtime Install state
  -> identify Project Anchor
  -> check VERSION_MANIFEST
  -> record canonical contract source/freshness
  -> read current project-local runtime surface
  -> read current ai-career instruction contract
  -> diff instruction contract against local assembly
  -> patch project-local assembly
  -> run OS_VALIDATE
  -> record OS_VALIDATE evidence
  -> record update evidence
  -> report compatibility state
```

## Instruction Is Not Implementation

Instructions define required behavior and surfaces.

They do not prescribe a single file layout, framework, language, or tool implementation.

```text
GCS may assemble the runtime one way.
PK21 may assemble the runtime another way.
Another project may assemble it differently.
```

Compatibility is measured by behavior and evidence, not by identical file copies.

## Relationship To Runtime Commands

Runtime Commands choose the first route.

Runtime Instruction Set defines the instruction contract.

```text
RUNTIME_COMMANDS.md
  -> command entry / first route

RUNTIME_INSTRUCTION_SET.md
  -> durable OS lifecycle contract and BOOT handoff requirements
```

## Relationship To Project Runtime Install

Project Runtime Install defines how a durable project runtime is assembled.

```text
RUNTIME_INSTRUCTION_SET.md
  -> instruction interface

PROJECT_RUNTIME_INSTALL.md
  -> scan / proposal / approval / install / validation model
```

`PROJECT_RUNTIME_INSTALL.md` defines the internal durable install operation used
by OS_INSTALL. It is not a second user-facing lifecycle command.

## Relationship To Project Anchor

Project Anchor localizes the instruction contract.

```text
RUNTIME_INSTRUCTION_SET.md
  -> what must be assembled or updated

PROJECT_ANCHOR.md
  -> where the attached project assembles or updates locally
```

## Relationship To OS Validation Evidence

OS Validation Evidence defines how a session or project proves compatibility.

```text
RUNTIME_INSTRUCTION_SET.md
  -> required instruction behavior

OS_VALIDATION_EVIDENCE.md
  -> required evidence surface and completion proof
```

## Relationship To Runtime Preflight

Runtime Preflight checks whether a session or project is ready to execute an instruction.

```text
Instruction
  -> Preflight
  -> Ready?
  -> Execute session attach, local assembly, or update only when ready and authorized
```

Preflight should distinguish:

```text
Instruction contract from ai-career
Repository source
Current session target
Repository write scope
Project Runtime Install state
Project Anchor inside attached project
Project-local assembly inside attached project
OS_VALIDATE evidence inside attached project
Reusable observation returning to ai-career
```

## Relationship To Project Instance Boundary

Runtime Instruction Set relies on the Project Instance boundary:

```text
ai-career owns instruction contracts and reusable runtime model.
Projects own Project Anchor, local runtime assembly, validation evidence, and mutable state.
```

A project may propose reusable improvements back to ai-career through candidate flow.

## Relationship To Templates

There is one reusable runtime template model.

There should not be one template per project.

```text
One Runtime Template.
Many project-local assemblies.
Common instructions connect them through Project Runtime Install, Project Anchor, and Validation Evidence.
```

## Routing Guidance

Prefer:

```text
- Common instructions.
- Command route first.
- Target / Source boundary check.
- Exact OS_INSTALL proposal and approval before durable repository write.
- Internal Project Runtime Install operation during approved local assembly.
- Project Anchor before local assembly.
- Project-local assembly only after write scope.
- Source-backed validation.
- OS_VALIDATE evidence record.
- Candidate flow for reusable improvements.
- Compatibility by behavior and evidence.
```

## Placement Test

A concept belongs in Runtime Instruction Set when it answers:

```text
What common OS lifecycle instruction should projects understand?
What durable Runtime surface must a project install or update?
What evidence proves the instruction was applied?
How does a project update or validate its local runtime against ai-career?
```

If a concept defines the OS_INSTALL target/source boundary, it belongs in
`RUNTIME_COMMANDS.md` and the internal installation details belong in
`PROJECT_RUNTIME_INSTALL.md`.

If a concept defines durable project-runtime assembly, it belongs in `PROJECT_RUNTIME_INSTALL.md`.

If a concept defines validation evidence, it belongs in `OS_VALIDATION_EVIDENCE.md`.

If a concept defines the local project reference point, it belongs in `PROJECT_ANCHOR.md`.

If a concept defines the first route for a command, it belongs in `RUNTIME_COMMANDS.md`.

If a concept defines authority, it belongs in L0 Runtime Governance.

If it checks readiness before instruction execution, it belongs in Runtime Preflight.

If it coordinates command flow, it belongs in Runtime Orchestrator.

If it implements project-specific files, it belongs in the project instance.

## Adoption Status

This is a candidate runtime interface document.

It should be tested by feeding OS_INSTALL / OS_UPDATE instructions to at least one attached project and checking whether the runtime can:

```text
- require an immutable source and explicit durable target for OS_INSTALL,
- stop before mutation until the exact install proposal is approved,
- keep OS_INSTALL repository-runtime evidence separate from BOOT session evidence,
- expose PROJECT_RUNTIME_INSTALL only as an internal operation,
- validate existing repository surfaces as PROOF_ONLY / PARTIAL / VERIFIED / UNKNOWN,
- record OS_VALIDATE evidence when durable validation is available.
```

## Node / Mode Coordinate Contract Requirement

OS_UPDATE must include `.ai/core/NODE_MODE_COORDINATE_CONTRACT.md` when comparing
the active ai-career instruction contract against an attached project that
records or resolves Node, Mode, Role, Scope, status, or Runtime Anchor Frame
data.

Required interpretation:

```text
User-facing coordinate = Host / Node / Mode
Node = project / runtime node
Mode = user-facing operating mode
Mode resolves internally to Role / Scope / Rule Set
Scope is not Node
Mode Scope is not authority
Runtime Anchor Frame records currentness, not authority
```

Required project-local propagation:

```text
OS_UPDATE
  -> compare project coordinate surfaces against NODE_MODE_COORDINATE_CONTRACT.md
  -> patch or propose local correction when write scope allows
  -> record coordinate comparison in OS_VALIDATE evidence
```

If the project has coordinate-bearing surfaces but OS_UPDATE cannot compare
them, report `PARTIAL` or `UNKNOWN`.

If the project has no coordinate-bearing surfaces, record the contract as
`not_applicable`, `deferred`, or `unknown` rather than silently omitting it.
