---
name: persistence
description: Route snapshot, checkpoint, resume, archive, and memory requests to installed persistence surfaces.
---

# Persistence Invocation

Invocation class: `REFERENCE_RUNTIME_OPERATIONAL_STATE`

Host storage action: `HOST_DEPENDENT`

Capability classification: `checkpoint_resume_archive = HOST_DEPENDENT`

This Skill routes `SNAPSHOT_SAVE`, `CHECKPOINT`, `RESUME_SAVE`, `ARCHIVE_SAVE`,
`MEMORY_SYNC`, and prepared `SKILL_OBSERVATION` exports. Checkpoint and Resume records use the installed
project-local continuity store. Archive and Memory routes remain capability
dependent. This Skill does not select durable content, activate restored state,
or create authority.

## Targets

```text
.ai/core/RUNTIME_COMMANDS.md
  -> .ai/core/PERSISTENCE_MODEL.md
  -> .ai/runtime/continuity/continuity.sqlite for Checkpoint and Resume records
  -> installed archive/memory surface selected by the runtime
```

For Checkpoint and Resume, run `prepare` first and pass its unchanged candidate
to `save`. Return `SAVED` only when the SQLite transaction reports
`LOCAL_SQLITE_COMMITTED`. A prepared artifact is not durable. No checkpoint,
resume, archive, memory, or cache entry becomes active or authoritative through
this Skill.

Without a bound Execution Host and project filesystem access, local Checkpoint
and Resume operations are unavailable. A mobile or web Connector with an
approved Provider writer may continue only through `HANDOFF_APPEND`.

`SKILL_OBSERVATION` begins with a Task Frame Result Packet, not arbitrary
conversation content. `skill-observation prepare` validates and returns a
redacted `PREPARED` candidate only. It does not create a local database record
or publish to Universe. A project may later append that candidate to an
approved Runtime-owned archive path through `HANDOFF_APPEND`; provider-native
evidence is required before reporting that export as durable.

Checkpoint, snapshot, memory sync, runtime-owned Inbox/Queue updates,
`RESUME_SAVE`, and selected `RESUME` restore use the declared Runtime-owned
operational-state route rather than Execution Guard. They remain bounded by
their persistence, selection, provenance, and Current Anchor contracts.

Any persistence action that writes source, a project-owned artifact, Core,
configuration, templates, or an external system must instead execute
`.ai/skills/common/execution-guard/SKILL.md` for that exact target. Read-only
resume discovery does not require a mutation receipt. A prepared payload or
user request is not evidence of durable completion.
