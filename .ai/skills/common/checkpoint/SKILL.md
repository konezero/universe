---
name: checkpoint
description: Prepare and durably save a snapshot-first CHECKPOINT through the Runtime-owned continuity store.
---

# Checkpoint

Invocation class: `REFERENCE_RUNTIME_OPERATIONAL_STATE`

Durable capability: `checkpoint_resume_archive = HOST_DEPENDENT`

Bare `CHECKPOINT` means `SNAPSHOT_SAVE` unless the Commander explicitly asks
for a Resume Point or Archive. Invoke the installed continuity command profile
to prepare the caller-selected snapshot, then save that exact immutable
candidate to the project-local continuity store.

```text
CHECKPOINT
  -> checkpoint prepare
  -> PREPARED candidate
  -> checkpoint save
  -> .ai/runtime/continuity/continuity.sqlite transaction
  -> LOCAL_SQLITE_COMMITTED evidence
```

`checkpoint save` must receive the unchanged `candidate_id + candidate` pair
returned by `checkpoint prepare`. Report `SAVED` only after the SQLite
transaction commits. Idempotent replay of the same immutable candidate returns
the original save evidence; reuse of the same ID with different content fails.

Checkpoint evidence remains passive and grants no authority, execution
assignment, Mode Current Anchor activation, or executable Runtime currentness.
`checkpoint list` and `checkpoint load` are read-only. A persistence action that
changes project source or any non-Runtime-owned target leaves this route and
requires Execution Guard.

A mobile or web Connector without a bound Execution Host cannot save, list, or
load this local SQLite store. It may only route a selected handoff artifact to
`HANDOFF_APPEND` when an approved Provider writer is available.
