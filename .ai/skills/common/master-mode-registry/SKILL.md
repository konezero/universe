---
name: master-mode-registry
description: Manage an installed project's source-backed Mode Registry from MASTER.
---

# MASTER Mode Registry Management

Invocation class: `PROJECT_GOVERNANCE_MUTATION`

This Skill applies only when the active repository Registry is
`MASTER_MANAGED`. It supports:

```text
ADD_MODE
MODIFY_MODE
DELETE_MODE
LIST_MODES
SHOW_MODE
```

The canonical project Registry is:

```text
.ai/runtime/project_instance/mode_registry.json
```

## Read Operations

`LIST_MODES` and `SHOW_MODE` are read-only. Use the Reference Runtime:

```text
mode-registry list --repo-root <project>
mode-registry show --repo-root <project> --mode <MODE>
```

## Mutation Operations

Mode mutation is available only from `MASTER`.

```text
1. Read the current Registry.
2. Create an exact plan with `mode-registry plan`.
3. Display operation, target Mode, definition, and revision change.
4. Obtain explicit User approval.
5. Bind an exact assignment and Registry-file write scope.
6. Run Execution Guard.
7. Apply the exact postimage through the receipt-aware file gateway.
8. Validate the Registry owner, canonical identifiers, and new revision.
```

Planner input:

```json
{
  "operation": "ADD | MODIFY | DELETE",
  "actor_mode": "MASTER",
  "mode": "MODE_ID",
  "active_mode": "MASTER",
  "definition": {
    "role": "ROLE_ID",
    "scope": "project-scope",
    "mode_profile": "GOVERNANCE_ONLY"
  }
}
```

`DELETE` omits `definition`.

The planner does not write. Use:

```text
mode-registry plan --repo-root <project> --request <path|->
```

## Invariants

```text
MASTER may add, modify, and delete project Modes.
MASTER cannot delete itself.
MASTER may change its own Scope or Mode Profile but must retain the MASTER role.
The active Mode cannot be deleted.
DELETE requires a registered, source-backed active Mode; `UNKNOWN` and
unregistered labels are not accepted.
Deleted Mode history remains in Anchor and Task Frame stores.
Changes apply on the next MODE_CHANGE or session preparation. The changed
Registry revision/definition binding creates a new Mode Current Anchor and
retires the previous Anchor to Beyond evidence.
```

An ai-career `IMMUTABLE` Registry always rejects mutation. `CONDUCTOR` and
`CARRIER` are fixed ai-career Modes.
