# Node / Mode Coordinate Contract

Status: Candidate Core Runtime Contract
Scope: ai-career / attached project runtime
Layer: Runtime Instruction / Project Runtime Assembly / Status Coordinate
Parent: `.ai/core/RUNTIME_INSTRUCTION_SET.md`
Created: 2026-07-05
Source validation: GCS fresh mobile GPT boot

## Purpose

This document defines the source-backed coordinate contract that OS_UPDATE,
OS_STATUS, OS_VALIDATE, and attached project boot flows must use when reporting
or assembling Node, Mode, Role, and Scope.

It closes the drift found during GCS fresh mobile GPT boot validation:

```text
role_selection_gate.md still used Role / Mode / Node selection structure.
current_anchor_frame.md recorded node: architecture/governance.
fresh GPT returned Node: UNKNOWN or confused Role authority wording.
```

The failure was not only a project-local OS_UPDATE miss.

The missing reusable contract is:

```text
Node > Mode
Mode = Role + Scope
```

## Core Declaration

```text
USER-FACING COORDINATE IS HOST / NODE / MODE.

NODE IS THE PROJECT OR RUNTIME NODE.

MODE IS THE USER-FACING OPERATING MODE FOR THAT NODE.

ROLE AND SCOPE ARE INTERNAL RUNTIME RESOLUTIONS OF MODE.

ROLE IS NOT A FIRST-CLASS USER COORDINATE.

SCOPE IS NOT NODE.

MODE SCOPE IS NOT AUTHORITY.

RUNTIME ANCHOR FRAME RECORDS CURRENTNESS.
RUNTIME ANCHOR FRAME DOES NOT CREATE AUTHORITY.
```

## Coordinate Model

User-facing coordinate:

```text
Host
  -> Node
      -> Mode
```

Meaning:

```text
Host = execution surface
Node = project / runtime node
Mode = user-facing operating mode for that node
```

Mode resolves internally to:

```text
Role
Scope
Rule Set
```

Runtime owns that mapping.

The mapping is loaded from the central source-backed Registry:

```text
.ai/runtime/project_instance/mode_registry.json
```

A syntactically safe token is not a valid Mode unless the active Registry
contains it. Caller-provided Role, Scope, or Mode Profile cannot replace the
registered definition. See `.ai/core/MODE_REGISTRY.md`.

The user-facing coordinate should not require Role or Scope selection when Node
and Mode are sufficient.

## Node

Node is the project or runtime node.

Examples:

```text
node: ai-career
node: GCS
node: WebApp
```

Node is not:

```text
architecture/governance
source-audit
implementation
review
```

Those are Mode scopes or task scopes, not Nodes.

## Mode

Mode is the user-facing operating mode for a Node.

Examples:

```text
mode: CONDUCTOR
mode: CARRIER
mode: MASTER
mode: REVIEWER
mode: IMPLEMENTATION
mode: DEBUG
```

Mode is not merely a label.

Mode resolves to a source-backed behavior contract:

```yaml
mode: MASTER
role: MASTER
mode_scope: architecture/governance
rule_set: <source-backed rule surface>
```

The Tutorial Guide is a read-only interaction profile, not an ai-career Mode:

```yaml
node: ai-career
interaction_profile: TUTORIAL_GUIDE
role: READER_GUIDE
mode_scope: tutorial/guided-walkthrough
rule_set: .ai/core/TUTORIAL_GUIDE_MODE.md
authority: UNASSIGNED
execution_assignment: UNASSIGNED
repository_write_scope: NONE
```

The `TUTORIAL` interaction command is not OS_INSTALL and is not runtime
execution authority.

Mode is the Current Anchor identity. It is not executable Runtime currentness identity.

Executable Runtime currentness belongs to `SESSION_CURRENTNESS.md` and uses:

```text
session_id + frame_id
```

Use Mode to select the Mode Current Anchor. Do not use Mode to decide which
executable Runtime frame is current.

## Role

Role is an internal resolved runtime field.

Candidate rule:

```text
Role is resolved from Mode by source-backed mapping.
Role is not direct authority.
Role is not a substitute for Mode authority.
Role should not be required as a first-class user coordinate when Node / Mode is enough.
```

## Scope

Scope is an internal resolved runtime field.

Candidate rule:

```text
Scope belongs under Mode.
Scope must not be stored as Node.
Mode Scope is not authority.
```

Example:

```yaml
node: GCS
mode: MASTER
role: MASTER
mode_scope: architecture/governance
```

Invalid:

```yaml
node: architecture/governance
mode: MASTER
```

## Runtime Anchor Frame Shape

Corrected GCS example:

```yaml
host: mobile-gpt
node: GCS
mode: MASTER
role: MASTER
mode_scope: architecture/governance
anchor_id: GCS_WORK_RESUME
state: READY
authority: UNASSIGNED
```

Interpretation:

```text
Node identifies the project/runtime node.
Mode identifies the user-facing operating mode.
Role is resolved from Mode.
Scope is resolved under Mode.
Authority remains separate.
```

## Authority Boundary

None of these create authority:

```text
Host
Node
Mode
Role
Scope
Mode Scope
Anchor state
Runtime Anchor Frame
```

Authority still requires:

```text
User approval
+ source-backed policy
+ scoped execution assignment
+ pre-execution verification
```

## Policy Requirement

Runtime policy surfaces that mention role, mode, node, scope, authority, status,
or anchor frames must preserve this coordinate split.

Required policy behavior:

```text
Role / Mode Authority Gate verifies source-backed authority.
Node / Mode Coordinate Contract defines user-facing coordinates.
Runtime Instruction Set defines OS_UPDATE propagation requirements.
Project Runtime Install resolves project-local Node / Mode / Rule Set mappings.
OS Validation Evidence proves the attached project used the active contract.
```

Policy surfaces must not report Role, Scope, or Mode Scope as authority.

## Template Requirement

Templates or project implementation contracts that expose session or runtime
state should use these fields when the information is available:

```yaml
host: <execution-surface-or-UNKNOWN>
node: <project-runtime-node-or-UNKNOWN>
mode: <user-facing-mode-or-UNKNOWN>
role: <resolved-role-or-UNKNOWN>
mode_scope: <resolved-mode-scope-or-UNKNOWN>
authority: <assigned-authority-or-UNASSIGNED-or-UNKNOWN>
```

Templates must not store a Mode Scope as `node`.

Templates may omit internal fields only when the attached project can still
report whether the fields are implemented, deferred, or unknown during
OS_VALIDATE.

## Project-Local Propagation Requirement

OS_UPDATE must propagate this contract to attached projects when the project has
any local surface that records or resolves Node, Mode, Role, Scope, status, or
runtime anchor frame data.

Required OS_UPDATE checks:

```text
1. Is NODE_MODE_COORDINATE_CONTRACT.md visible in the active core surface registry?
2. Does the project identify Node as project/runtime node?
3. Does the project identify Mode as user-facing operating mode?
4. Does the project resolve Role from Mode through source-backed mapping?
5. Does the project resolve Scope under Mode instead of storing it as Node?
6. Does the project keep Mode Scope separate from authority?
7. Does the project record OS_VALIDATE evidence for this comparison?
```

If the project has no local coordinate or anchor-frame surface, OS_UPDATE should
record:

```text
coordinate_contract: not_applicable | deferred | unknown
```

If the project has such a surface but cannot compare it, OS_VALIDATE should
report `PARTIAL` or `UNKNOWN`, not `PASS`.

## OS_UPDATE Implication

After this contract is visible through the Core Surface Registry and Runtime
Instruction Set, attached projects should correct local drift such as:

```text
role_selection_gate.md
current_anchor_frame.md
fresh boot status wording
project-local runtime status templates
project-local validation evidence records
```

Expected GCS correction:

```text
Node: GCS
Mode: MASTER
Role: MASTER
Mode Scope: architecture/governance
Authority: UNASSIGNED unless explicitly assigned
```

## COMMANDER WAIT

This contract preserves the current GCS validation rule:

```text
COMMANDER WAIT MEANS STOP.
```

WAIT is not:

```text
heartbeat
background continuation
implicit approval
```

## Validation Questions

OS_VALIDATE should answer:

```text
Is NODE_MODE_COORDINATE_CONTRACT.md registered as an active core surface?
Did OS_UPDATE compare the attached project's coordinate surfaces against it?
Does the project report Node / Mode as the user-facing coordinate?
Are Role and Scope resolved internally from Mode?
Are architecture/governance and similar scopes stored as mode_scope rather than node?
Is authority still separate from Host / Node / Mode / Role / Scope / Anchor Frame?
Was the result recorded in project-local validation evidence?
```

## Adoption Status

This is a candidate core runtime contract.

Core promotion beyond candidate status requires explicit approval.
