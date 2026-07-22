# Mode Registry Contract

Status: active core runtime contract
Scope: ai-career and installed project Mode resolution

## Purpose

This contract defines the source-backed allowlist used before `MODE_CHANGE`,
session preparation, or Mode Current Anchor access.

```text
Safe Mode token
  !=
Registered Mode
```

A Mode label is accepted only when it is present in the active repository's
central Mode Registry. The registry resolves the Mode to its Role, Scope, and
Mode Profile.

An unresolved generic `BOOT` with `Mode: UNKNOWN` does not perform Mode
resolution and returns `MODE_SELECTION_REQUIRED`. Once a Mode is selected,
missing Registry evidence is a terminal `MODE_REGISTRY_UNAVAILABLE` result.
Source-only Hosts must resolve the same provider-observed Registry before
activating Mode context; absence of a local Anchor store does not permit a
caller-supplied Mode definition.

## Canonical Surface

```text
.ai/runtime/project_instance/mode_registry.json
```

The registry schema is:

```text
ai-career.mode-registry.v1
```

## Registry Classes

### ai-career

The ai-career source repository has an immutable registry containing exactly:

```text
CONDUCTOR
CARRIER
```

No runtime command or Skill may add, modify, or delete these entries.

### Installed Project

A fresh installed project has a `MASTER_MANAGED` registry whose required root
Mode is `MASTER`.

```text
MASTER
  -> may add project-local Modes
  -> may modify project-local Modes
  -> may delete non-active project-local Modes
  -> may not delete MASTER
  -> must retain the MASTER role when modifying itself
```

The MASTER management surface is:

```text
.ai/skills/common/master-mode-registry/SKILL.md
```

Registry mutation is project configuration mutation. It requires explicit
approval, bounded write scope, Execution Guard permission, and a receipt-aware
write gateway. The Registry planner does not write files.

Registry Mode and Role identifiers use canonical uppercase ASCII. Duplicate
JSON keys are invalid. The ai-career Registry owner is exactly `ai-career`;
an installed project Registry owner must match the installed project identity.

## Resolution Order

```text
Natural-language Mode intent
  -> internal MODE_CHANGE
  -> load central Mode Registry
  -> reject an unregistered Mode
  -> resolve Role / Scope / Mode Profile from the registry
  -> prepare the session
  -> resolve that Mode's Current Anchor
```

Caller-provided Role, Scope, or Mode Profile must match the registered entry.
The caller cannot widen or replace the registered definition.

Mode Current Anchor evidence binds the Registry revision, complete Registry
digest, and selected Mode definition digest used during alignment. A Registry
revision or selected definition change retires the previous Mode Current
Anchor to Beyond evidence and creates a new Current Anchor on the next
successful Mode preparation.

`MODE_NOT_REGISTERED` is terminal for that Mode request. Stop before Role,
Scope, or Mode Profile resolution, Host coordinate checks, session preparation,
Current Anchor access, or executable Runtime decisions. Host evidence cannot
register a missing Mode and must not be presented as a retry condition.

## Mutation Invariants

```text
IMMUTABLE registry mutation
  -> MODE_REGISTRY_IMMUTABLE

unregistered Mode selection
  -> MODE_NOT_REGISTERED

registered definition mismatch
  -> MODE_REGISTRY_PROFILE_MISMATCH

non-MASTER project registry mutation
  -> MASTER_MODE_REQUIRED

DELETE_MODE MASTER
  -> ROOT_MODE_DELETION_FORBIDDEN

MODIFY_MODE MASTER with role != MASTER
  -> ROOT_MODE_ROLE_CHANGE_FORBIDDEN

DELETE_MODE <active-mode>
  -> ACTIVE_MODE_DELETION_FORBIDDEN

DELETE_MODE with active Mode UNKNOWN
  -> ACTIVE_MODE_REQUIRED

DELETE_MODE with unregistered active Mode
  -> MODE_NOT_REGISTERED
```

Deleting a registry entry does not delete its historical Current Anchor,
Beyond Anchor footprints, Task Frames, or archive evidence.

Registry changes take effect on the next `MODE_CHANGE` or session preparation.
They do not retroactively rewrite an already active Mode context.

## Authority Boundary

Mode registration proves that a Mode definition is source-backed.

It does not create:

```text
execution authority
write scope
execution assignment
executable Runtime currentness
repository verification
```
