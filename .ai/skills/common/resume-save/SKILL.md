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

## SSOT and deprecation

```text
SSOT: .ai/runtime/continuity/continuity.sqlite
Identity fields: node + mode (+ session_id + frame_id + anchor_id + checkpoint_ref)
File-tree .ai/resume/** : DEPRECATED for runtime resume; do not write new runtime
  continuity there; do not delete existing trees solely for deprecation.
```

Compressed conversation or handoff meaning belongs in the candidate payload
(for example `summary` and/or `snapshot.compressed_context`), not in a new
Resume Archive tree and not as a raw full transcript dump.

## Compressed context (candidate payload)

Optional but preferred for session handoff:

```text
snapshot.compressed_context
  -> bounded, redacted recovery meaning for the next session
  -> not authority, not assignment, not Mode Current Anchor proof

summary
  -> short passive recovery line; may accompany compressed_context
```

Hosts SHOULD prefer compressed meaning over replaying chat. Secrets, tokens,
and raw provider transcripts MUST NOT be placed in either field.

## Fail-closed coordinates

```text
node / mode missing or UNKNOWN
session_id / frame_id / anchor_id / checkpoint_ref missing or UNKNOWN
  -> do not claim SAVED
  -> require Session Preparation / Mode resolution first
```

Mode must be the registered Mode coordinate. Role is resolved from Mode and is
not an independent resume identity.

## Route

```text
current Parent selects bounded conversation restoration content
  -> require Mode-resolved node/mode and session coordinates (fail closed if UNKNOWN)
  -> resume-save prepare validates and hashes the candidate
  -> resume-save save verifies candidate_id + candidate
  -> .ai/runtime/continuity/continuity.sqlite transaction commits
  -> LOCAL_SQLITE_COMMITTED evidence confirms durability
```

Report `SAVED` only after the local SQLite commit. Idempotent replay returns the
original save evidence. Resume discovery and loading are separate operations
and must not automatically activate an old Anchor, Mode Current Anchor, Role,
Authority, or Execution Assignment. Source or project-owned writes outside the
Runtime-owned continuity path still require Execution Guard.

A source-only mobile or web Connector must not claim `SAVED` for the local
continuity store. Its durable boundary is an explicitly approved Provider
`HANDOFF_APPEND`; local Resume save requires a bound Execution Host with project
filesystem access.
