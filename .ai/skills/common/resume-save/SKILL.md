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

## Automatic lifecycle route

Persistent Conductor, Master, and project Mode Sessions are automatically
flushed through the shared automatic continuity coordinator at stable lifecycle
events: `TASK_COMPLETED`, `NORMAL_STOP`, `PROVIDER_SWITCH`, `MODE_SWITCH`, and
debounced `IDLE`. This Host lifecycle route is common to all persistent modes;
it is not a per-provider or per-mode opt-in.

Task Frame Boss and Worker sessions are ephemeral and do not create persistent
Mode Resume records. Their Parent persists bounded task state when appropriate.

Automatic flushing is separate from the explicit `RESUME_SAVE` command below.
It uses the same validators and local SQLite store, but does not imply user
intent, restore, authority, or Git/archive publication.

## Command intent (mandatory)

```text
ONE COMMAND INTENT -> ONE COMMAND EXECUTION -> STOP

Commander says 리쥼 저장 / RESUME_SAVE / resume-save
  -> run resume-save prepare and save only
  -> STOP after SAVED or a blocked/failed save report

Do NOT chain any second command after save.
Do NOT run resume-restore discover or load after save.
Do NOT infer restore, adoption, BOOT, OS_UPDATE, or Mode Anchor activation.
```

Internal prepare→save for the **same** candidate is one `RESUME_SAVE` contract.
Anything else needs a new Commander utterance.

Restore is a separate command. Invoke `.ai/skills/common/resume-restore/SKILL.md`
only when the Commander explicitly requests restore (for example `리쥼 복원`,
`RESUME`, or `resume-restore`).

## SSOT and deprecation

```text
SSOT: .ai/runtime/continuity/continuity.sqlite
Identity fields: node + mode (+ session_id + frame_id + anchor_id + checkpoint_ref)
File-tree .ai/resume/** : DEPRECATED for runtime resume; do not write new runtime
  continuity there; do not delete existing trees solely for deprecation.
```

## target_ref (required store URI)

Canonical value only (omit field to default):

```text
sqlite://.ai/runtime/continuity/continuity.sqlite
```

Do **not** invent informal URIs. The following are non-canonical; prepare rewrites
recognized aliases to the canonical value, and unknown values fail closed:

```text
WRONG: database://continuity
WRONG: database://continuity.sqlite
WRONG: drive://continuity/checkpoint
RIGHT: sqlite://.ai/runtime/continuity/continuity.sqlite
```

Compressed conversation or handoff meaning belongs in
`snapshot.compressed_context`. `summary` may accompany it but cannot replace
it. Do not create a new Resume Archive tree or store a raw full transcript.

## Compressed context (candidate payload)

Required for session handoff:

```text
snapshot.compressed_context
  -> bounded, redacted recovery meaning for the next session
  -> not authority, not assignment, not Mode Current Anchor proof

summary
  -> optional short passive recovery line; cannot replace compressed_context
```

`snapshot.compressed_context` MUST be a non-empty string containing bounded
recovery meaning. Secrets, tokens, and raw provider transcripts MUST NOT be
placed in either field.

## Fail-closed coordinates

```text
node / mode missing or UNKNOWN
session_id / frame_id / anchor_id / checkpoint_ref missing or UNKNOWN
snapshot.compressed_context missing, non-string, or empty
  -> do not claim SAVED
  -> require Session Preparation / Mode resolution first
```

Mode must be the registered Mode coordinate. Role is resolved from Mode and is
not an independent resume identity.

## Route

```text
current Parent selects bounded conversation restoration content
  -> require Mode-resolved node/mode and session coordinates (fail closed if UNKNOWN)
  -> require non-empty snapshot.compressed_context
  -> resume-save prepare validates and hashes the candidate
  -> resume-save save verifies candidate_id + candidate
  -> .ai/runtime/continuity/continuity.sqlite transaction commits
  -> LOCAL_SQLITE_COMMITTED evidence confirms durability
```

Report `SAVED` only after the local SQLite commit. Idempotent replay returns the
original save evidence. After `SAVED`, stop: do not chain discover/load.
Resume discovery and loading are separate operations under explicit restore
intent and must not automatically activate an old Anchor, Mode Current Anchor,
Role, Authority, or Execution Assignment. Source or project-owned writes outside
the Runtime-owned continuity path still require Execution Guard.

A source-only mobile or web Connector must not claim `SAVED` for the local
continuity store. Its durable boundary is an explicitly approved Provider
`HANDOFF_APPEND`; local Resume save requires a bound Execution Host with project
filesystem access.
