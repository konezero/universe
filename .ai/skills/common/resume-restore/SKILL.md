---
name: resume-restore
description: Discover and load selected Resume candidates without activating an old Anchor automatically.
---

# Resume Restore

Invocation class: `REFERENCE_RUNTIME_READ_ONLY`

Use `resume-restore discover` to query the project-local continuity store. The
runtime filters durable Resume records to the same `node + mode` coordinate and
returns ranked candidates only. Caller-supplied candidate evidence remains a
supported bounded input for provider-backed handoff cases.

```text
Snapshot insufficient
  -> discover Resume candidates
  -> report source and checkpoint reference
  -> Commander selection
  -> resume-restore load selected record
  -> return passive rehydration candidate
  -> OS_STATUS or OS_VALIDATE
  -> Current Anchor realignment decision
```

Discovery and load do not restore Role, Authority, Execution Assignment, Mode
Current Anchor, or executable Runtime currentness. `load` proves only that the
selected durable record was read and verified by the continuity store. Missing
or conflicting evidence remains `UNKNOWN` or `RESUME_CANDIDATE_NOT_FOUND`.

Local discovery and load require a bound Execution Host with access to the
project continuity database. A mobile or web Connector may observe a separately
published handoff artifact, but it must not claim that it queried the local
SQLite store.

After explicit Commander selection, later Current Anchor realignment is a
separate Runtime-owned operation. It does not use Execution Guard unless the
selected route also changes source, project-owned files, configuration, or an
external system.
