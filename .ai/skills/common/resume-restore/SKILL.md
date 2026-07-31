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

## SSOT and deprecation

```text
SSOT: .ai/runtime/continuity/continuity.sqlite
Discover filter: same node + same mode
File-tree .ai/resume/** : DEPRECATED; not a discover source for runtime restore.
Do not delete existing archive trees solely for deprecation.
```

Cross-mode and cross-node records MUST NOT appear as restore candidates for the
active coordinate. Role labels on historical archives are not discover keys.

```text
Snapshot insufficient
  -> discover Resume candidates from continuity.sqlite only
  -> filter same node + same mode
  -> report source and checkpoint reference
  -> Commander selection
  -> resume-restore load selected record
  -> return passive rehydration candidate
       (summary / snapshot.compressed_context when present)
  -> OS_STATUS or OS_VALIDATE
  -> Current Anchor realignment decision (separate; not automatic)
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
