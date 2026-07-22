# Project Primary Mode Bootstrap Template

Status: template candidate
Repository: `konezero/ai-career`
Scope: attached-project runtime bootstrap and initial project governance coordinate
Owner: attached project

## Purpose

This template defines the minimum primary-mode result required when a new
project installs ai-career through `OS_INSTALL`.

The install source provides the reusable bootstrap contract. The attached project remains the owner of the resulting project runtime, validation, authority, and later mode design.

## Core Boundary

```text
ai-career
  -> provides the reusable project-runtime assembly source
  -> provides the mandatory initial MASTER mode template
  -> does not own the attached project's runtime state
  -> does not prove or approve project-local execution

Project MASTER
  -> owns the attached project's runtime assembly
  -> owns project-local validation and evidence
  -> manages the project-local Mode Registry
  -> keeps execution authority separate from mode readiness
```

## Mandatory Install Result

A new attached project runtime installation is incomplete until the project has a source-backed primary mode entry.

The mandatory initial primary mode is `MASTER`.

```yaml
project_primary_mode:
  mode: MASTER
  role: MASTER
  mode_scope: architecture/governance
  display_mode: MASTER
  state: READY
  authority: UNASSIGNED
  execution_assignment: UNASSIGNED
```

`MASTER` is the initial project runtime governance coordinate. It is not automatic source, runtime, database, deployment, commit, push, PR, trading, order, or risk execution authority.

## Required Project Outputs

`OS_INSTALL` should assemble or reconcile the following project-local surfaces:

```text
.ai/runtime/project_instance/boot_command_entry.md
.ai/runtime/project_instance/project_anchor.md
.ai/runtime/project_instance/role_selection_gate.md
.ai/runtime/project_instance/mode_registry.json
.ai/runtime/state/session.md
.ai/runtime/state/current_anchor_frame.md
```

These six surfaces are the minimum generated **project coordinate** outputs.
They are not the complete standalone Runtime distribution and must not be used
as the sole installation manifest.

A standalone `OS_INSTALL` must additionally materialize and verify
the commit-bound distribution declared by:

```text
.ai/distribution/context_management_runtime_pack/project_runtime_distribution_manifest.json
```

That distribution includes the registered Core contract surfaces, registered
generic templates, and production Runtime / Skill / Host-adapter executables.
The installed project records per-file source and local hashes in:

```text
.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json
```

If only the six generated coordinate surfaces exist, report structure readiness
for that narrow scope and keep the standalone repository Runtime `PARTIAL`.

Even after the full standalone distribution validates, initial session state is
uninitialized. `OS_INSTALL` may declare the project primary Mode,
Role, and Scope, but only a later BOOT or attach with Host evidence may create
that Mode's Current Anchor. An executable `session_id + frame_id`
currentness coordinate is created only when an executable Runtime is started
or attached.

The project may use equivalent paths when its local architecture requires them, but it must preserve the same source-backed responsibilities.

At minimum, the installed project must provide:

```text
1. A direct user-facing MASTER mode entry phrase.
2. A `MASTER_MANAGED` central Mode Registry.
3. Node / Mode resolution before Role / Scope resolution.
4. MASTER -> Role MASTER / architecture-governance scope binding.
5. Authority and Execution Assignment as independent fields.
6. A project-owned current runtime state surface.
7. A project-owned current anchor frame or equivalent currentness surface.
```

## Primary Entry Shape

```text
마스터모드 / MASTER / MASTER mode / <PROJECT_NAME> MASTER
  -> treat as the project primary MODE_SWITCH command
  -> read project runtime state and current anchor frame
  -> resolve Node: <PROJECT_NODE>
  -> resolve Mode: MASTER
  -> resolve Role: MASTER
  -> resolve Mode Scope: architecture/governance
  -> keep Authority: UNASSIGNED unless separately source-backed
  -> keep Execution Assignment: UNASSIGNED unless a scoped task is approved
  -> report READY using project-owned source-backed facts only
```

Required READY report shape:

```text
Node: <PROJECT_NODE>
Mode: MASTER
Role: MASTER
Mode Scope: architecture/governance
Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
State: READY
```

## Additional Mode Creation

Additional project modes are created from the project side after entering MASTER mode.

```text
OS_INSTALL
  -> assemble minimum project runtime surfaces
  -> create mandatory MASTER primary mode
  -> return READY_FOR_BOOT
BOOT
  -> enter project MASTER coordinate after session evidence is created
  -> inspect project needs
  -> use the MASTER Mode Registry Skill
  -> propose additions, modifications, or deletions
  -> obtain applicable project/user approval
  -> update the project-local Registry through Execution Guard
```

ai-career may provide mode templates and assembly guidance. It does not decide which additional modes a project must operate.

`MASTER` may add, modify, and delete project Modes, but it cannot delete
itself. An active Mode must be changed before deletion. Registry deletion does
not delete historical Anchor or Task Frame evidence.

## Host Independence

The MASTER contract is host-neutral.

Host-specific fields such as the following are resolved by the attached project at runtime:

```text
session_location
commander_surface
execution_surface
repository_location
available connectors
local filesystem capability
CLI capability
repository write capability
```

Changing Host does not by itself change:

```text
Node
Mode
Role
Mode Scope
Project ownership
```

Host resolution must not be persisted as universal project authority.

## Local Host Entry

For an installed Project Runtime on local PC / CLI / Codex / VSCode, the user
lifecycle action is `BOOT`. The executor may record
`LOCAL_INSTALL_OR_ATTACH` only as an internal Host action.

```text
BOOT
  -> internal Host action: LOCAL_INSTALL_OR_ATTACH
  -> read project boot entry
  -> read project-owned runtime state and current anchor frame
  -> resolve the source-backed primary mode
  -> route to MASTER session attach or mode entry
  -> apply Role / Mode Authority Gate
  -> keep Authority and Execution Assignment separate
```

Local availability means BOOT can enter the installed Project Runtime path. It
does not mean that MASTER execution authority, repository write scope, or a
task assignment is automatic.

## Ownership and Validation

```text
ai-career template
  = assembly source and reusable contract

project-local MASTER runtime
  = assembled project instance

project-local validation
  = project-owned verification result
```

ai-career does not centrally own the proof that a project's current runtime is valid. It provides the source contract from which the project can assemble and validate its own runtime.

## Installation Completion Gate

The template-level primary-coordinate check is:

```yaml
master_primary_mode:
  entry_defined: true
  mode_registry_initialized: true
  root_mode_deletion_forbidden: true
  runtime_state_initialized: true
  anchor_frame_initialized: true
  authority_separated: true
  execution_assignment_separated: true
```

This check proves only the MASTER primary coordinate. It does not prove the
standalone distribution, executable Runtime, source-commit binding, or required
file hash closure.

If the mandatory MASTER entry or project-owned state surfaces are missing, report:

```text
OS_INSTALL: PARTIAL
MASTER_MODE: NOT_READY
```

Do not report the attached project runtime as ready merely because template files were copied.
Do not report `OS_INSTALL: COMPLETE` from this template check alone.

## Non-Goals

This template does not:

- grant MASTER execution authority;
- define project business behavior;
- modify production source, database, trading, order, or risk behavior;
- require a particular Host or filesystem path;
- require SQLite or a durable runtime database;
- make ai-career the owner of project-local runtime proof;
- create additional modes without project-side review.

## Relationship to Existing Templates

This template complements:

```text
.ai/templates/project_boot_command_entry/README.md
.ai/templates/runtime_state/session.md
```

The boot command entry defines how primary mode phrases route.
The runtime-state template defines how the project records the resolved state.
This template defines the mandatory initial MASTER bootstrap result connecting those two surfaces.
