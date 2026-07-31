# Runtime Continuity Store Template

Status: active project contract template (runtime resume SSOT)
Scope: installed project Runtime operational state

## Purpose

Installed projects persist passive Checkpoint and Resume records at:

```text
.ai/runtime/continuity/continuity.sqlite
```

This SQLite store is the **only runtime SSOT** for `CHECKPOINT` and
`RESUME_SAVE` / `RESUME_RESTORE`. File-tree Resume Archives under
`.ai/resume/` are **DEPRECATED** for runtime restore identity; they remain
historical and must not be deleted solely for deprecation.

Resume candidate selection uses **same node + same mode**. Role is resolved
from Mode and is not the restore key. Session evidence uses
`session_id + frame_id` with session_id shape
`<NODE>-<MODE>-YYYYMMDD-<LOCATION>-SEQ`.

Installation creates only the managed
`.ai/runtime/continuity/.gitignore` boundary. The Reference Runtime creates
the database on the first successful `checkpoint save` or `resume-save save`.
Installation must not create an empty database or claim saved continuity
evidence.

## Boundary

```text
prepare
  -> immutable candidate + candidate_id
save
  -> append-only SQLite transaction
  -> LOCAL_SQLITE_COMMITTED evidence
list/load
  -> read-only passive record access
```

The store does not create or activate Role, Authority, Execution Assignment,
Mode Current Anchor, or executable Runtime currentness. Resume loading returns
a rehydration candidate for an explicit later adoption decision.

The database, WAL, and shared-memory files are Runtime state and remain outside
source commits. The installed directory-local `.gitignore` excludes every
Runtime-created file while keeping the ignore boundary itself managed.

Mobile and web Connectors do not access this store merely by attaching source.
Without a bound Execution Host, their durable boundary is an approved Provider
`HANDOFF_APPEND` to a declared Runtime-owned append path.
