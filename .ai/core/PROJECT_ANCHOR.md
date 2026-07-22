# Project Anchor

Status: Candidate Core Architecture
Scope: ai-career / attached project runtime
Layer: Project-local Reference Point
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-01

## Purpose

Project Anchor defines the local reference point that an attached project uses when assembling or updating its project-local runtime from the ai-career instruction contract.

ai-career has the canonical Active Anchor.

An attached project needs a Project Anchor.

Without a Project Anchor, OS_INSTALL and OS_UPDATE can know the instruction contract but still lack a local point of reference for assembly.

Without validation evidence, Project Anchor remains candidate or partial.

## Project Anchor Declaration

```text
ACTIVE PROJECT ANCHOR IS THE LOCAL POINT OF REFERENCE.

WHEN PROJECT INTERPRETATION IS UNCERTAIN,
PULL THE PROJECT ANCHOR FORWARD.

ASSEMBLE THE PROJECT-LOCAL RUNTIME
FROM THE ACTIVE INSTRUCTION CONTRACT.
```

## Anchor Relationship

```text
ai-career Active Anchor
  -> canonical runtime reference
  -> instruction contract
  -> global boundary

Project Anchor
  -> local project reference
  -> local assembly boundary
  -> project runtime surface
```

Short form:

```text
AI-CAREER ANCHOR DEFINES.
PROJECT ANCHOR IMPLEMENTS.
```

## Project Anchor Boundary

Project Anchor implements locally.

It does not redefine the canonical runtime contract.

```text
PROJECT ANCHOR IMPLEMENTS.
IT DOES NOT REDEFINE THE CANONICAL RUNTIME CONTRACT.
```

A project may adapt file layout, framework, tooling, and local runtime surface.

A project should preserve compatibility with the active ai-career instruction contract.

## Position In OS_UPDATE

```text
OS_UPDATE
  -> Runtime Commands
  -> Runtime Instruction Set
  -> Project Anchor
  -> Project-local Assembly
  -> OS_VALIDATE
  -> Project-local Evidence / Checkpoint
```

## Position In OS_INSTALL

```text
OS_INSTALL
  -> Runtime Commands
  -> Runtime Instruction Set
  -> Project Anchor
  -> Project-local Boot Layer
  -> Project-local Runtime Surface
  -> OS_VALIDATE
```

## What Project Anchor Provides

Project Anchor provides the local answer to:

```text
Where does this project start reading?
Where is the project-local runtime root?
Where are project-local runtime files assembled?
Which local role gate or boot command entry applies?
Which local checkpoint/memory/scope policy owns project state?
Which local validation surface proves compatibility?
```

## Candidate Project Anchor Surface

A project may express its Project Anchor through files such as:

```text
AGENTS.md
.ai/START_HERE.md
.ai/runtime/project_instance/boot_command_entry.md
.ai/runtime/project_instance/os_update.md
.ai/runtime/project_instance/project_anchor.md
.ai/runtime/project_instance/status.md
.ai/runtime/project_instance/validation/
.ai/runtime/project_instance/validation/latest.md
.ai/runtime/project_instance/validation/history.md
.ai/runtime/project_instance/checkpoints/*
.ai/runtime/project_instance/memory/*
.ai/runtime/project_instance/scope_policy.md
```

The exact files may vary by project.

Compatibility is measured by whether the project can report its local anchor, boot path, update path, validation path, and evidence.

## Project Anchor Preflight

Runtime Preflight should check Project Anchor readiness for OS_INSTALL and OS_UPDATE.

```text
Project Anchor Ready?
  -> READY
  -> NOT READY
  -> PARTIAL
  -> UNKNOWN
```

OS_UPDATE should not proceed as local assembly until the project can identify a local Project Anchor or declare that one must be created.

## Project Anchor Readiness

Project Anchor readiness requires more than an anchor file.

```text
Project Anchor Ready =
  Project Anchor exists
  + project-local runtime root is known
  + OS_UPDATE entry is known
  + validation/latest.md exists or missing state is recorded
  + validation/history.md exists or missing state is recorded
  + VERSION_MANIFEST status is recorded inside validation/latest.md
  + canonical contract source/freshness evidence is recorded inside validation/latest.md
```

If validation evidence paths are absent, Project Anchor status should be `PARTIAL` or `UNKNOWN`.

## Project Anchor Report

Preferred report:

```text
PROJECT ANCHOR STATUS: READY | NOT READY | PARTIAL | UNKNOWN

Project: <project-name>
Project Root: <path or UNKNOWN>
Project Runtime Root: <path or UNKNOWN>
Boot Command Entry: <path or UNKNOWN>
OS_UPDATE Entry: <path or UNKNOWN>
Validation Surface: <path or UNKNOWN>
Latest Validation Evidence: <path or UNKNOWN>
Validation History: <path or UNKNOWN>
Canonical Contract Source: <ref or UNKNOWN>
Checkpoint Surface: <path or UNKNOWN>
Scope Policy: <path or UNKNOWN>

Decision:
Proceed | Create Project Anchor | Update Project Anchor | Create Validation Evidence Surface | Stop
```

## Relationship To Runtime Instruction Set

Runtime Instruction Set defines what must be assembled.

Project Anchor defines where and how the project assembles it locally.

```text
Instruction Contract
  -> required behavior and surfaces

Project Anchor
  -> local reference point and assembly location
```

## Relationship To OS Validation Evidence

OS Validation Evidence defines where the project records proof of compatibility.

```text
Project Anchor
  -> local reference point

OS Validation Evidence
  -> source-backed proof that local assembly matches the active instruction contract
```

A Project Anchor is not operationally ready until validation evidence location and contract freshness evidence are known.

## Relationship To Project Instance Boundary

Project Anchor belongs to the project instance boundary.

```text
ai-career owns:
- canonical runtime contract
- runtime commands
- runtime instructions
- reusable model

project owns:
- project anchor
- project-local assembly
- project-local runtime state
- project-local validation evidence
```

## Relationship To Active Anchor

The Active Anchor in ai-career remains the canonical reference.

The Project Anchor is a local reference derived from the active instruction contract.

```text
Active Anchor answers:
What is the canonical contract?

Project Anchor answers:
Where does this project implement the contract?
```

## Drift Handling

If project-local interpretation conflicts with the active ai-career instruction contract:

```text
Pull Active Anchor forward.
Pull Project Anchor forward.
Compare canonical contract against local assembly.
Report compatibility gap.
Patch locally or raise reusable candidate when appropriate.
Record OS_VALIDATE evidence.
```

## Anti-Patterns

Avoid:

```text
- Treating project-local assembly as canonical ai-career contract.
- Updating a project without identifying its local reference point.
- Reporting Project Anchor READY without validation evidence location.
- Reporting OS_UPDATE complete from file edits only.
- Assuming every project has the same file layout.
- Treating Project Anchor as Conductor authority.
```

Prefer:

```text
- Canonical contract from ai-career.
- Local reference from Project Anchor.
- Project-local assembly.
- Source-backed validation.
- Compact validation evidence surface.
- Reusable discoveries routed as candidates.
```

## Placement Test

A concept belongs in Project Anchor when it answers:

```text
Where is the project-local reference point?
Where should OS_INSTALL or OS_UPDATE assemble files locally?
Which local runtime entry, validation surface, and checkpoint surface apply?
```

If it defines the canonical contract, it belongs in ai-career Core.

If it defines command entry, it belongs in `RUNTIME_COMMANDS.md`.

If it defines instruction requirements, it belongs in `RUNTIME_INSTRUCTION_SET.md`.

If it defines validation evidence, it belongs in `OS_VALIDATION_EVIDENCE.md`.

If it defines project-specific files, it belongs in the attached project instance.

## Adoption Status

This is a candidate project-local reference model.

It should be validated by applying OS_UPDATE to an attached project and confirming that the project reports a local Project Anchor before assembly and records OS_VALIDATE evidence after assembly.
