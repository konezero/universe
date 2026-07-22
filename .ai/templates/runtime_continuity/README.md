# Runtime Continuity Store Template

Status: project contract template
Scope: installed project Runtime operational state

## Purpose

Installed projects persist passive Checkpoint and Resume records at:

```text
.ai/runtime/continuity/continuity.sqlite
```

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
