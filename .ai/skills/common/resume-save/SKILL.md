---
name: resume-save
description: Prepare and durably save an explicit RESUME_SAVE candidate in the Runtime-owned continuity store.
---

# Resume Save

Invocation class: `REFERENCE_RUNTIME_OPERATIONAL_STATE`

Capability classification: `checkpoint_resume_archive = HOST_DEPENDENT`

This is the discoverable `RESUME_SAVE` entrypoint over
`.ai/skills/common/persistence/SKILL.md`. It prepares bounded restoration
material and saves the exact immutable candidate in the project-local
continuity store. Saving does not make a Resume record current or authoritative.

## Route

```text
current Parent selects bounded conversation restoration content
  -> resume-save prepare validates and hashes the candidate
  -> resume-save save verifies candidate_id + candidate
  -> .ai/runtime/continuity/continuity.sqlite transaction commits
  -> LOCAL_SQLITE_COMMITTED evidence confirms durability
```

Report `SAVED` only after the local SQLite commit. Idempotent replay returns the
original save evidence. Resume discovery and loading are separate operations
and must not automatically activate an old Anchor, Role, Authority, or
Execution Assignment. Source or project-owned writes outside the Runtime-owned
continuity path still require Execution Guard.

A source-only mobile or web Connector must not claim `SAVED` for the local
continuity store. Its durable boundary is an explicitly approved Provider
`HANDOFF_APPEND`; local Resume save requires a bound Execution Host with project
filesystem access.
