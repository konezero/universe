---
name: resume-restore
description: Discover and load selected Resume candidates without activating an old Anchor automatically.
---

# Resume Restore

Invocation class: `REFERENCE_RUNTIME_READ_ONLY`

## Command intent (mandatory)

```text
ONE COMMAND INTENT -> ONE COMMAND EXECUTION -> STOP

Run this Skill only on explicit Commander restore intent:
  리쥼 복원 / RESUME / resume-restore / clear restore language

Do NOT run after RESUME_SAVE solely because save succeeded.
Do NOT treat CHECKPOINT, BOOT, Mode switch, or OS_* as restore intent.
Do NOT chain BOOT, RESUME_SAVE, or other commands after load.
```

Save remains `.ai/skills/common/resume-save/SKILL.md`. This Skill never writes a
new Resume candidate. After discover (awaiting selection) or load, stop and wait.

Use `resume-restore discover` to query the project-local continuity store. The
runtime filters durable Resume records to the same `node + mode` coordinate and
returns ranked candidates only. Caller-supplied candidate evidence remains a
supported bounded input for provider-backed handoff cases.

## SSOT and deprecation

```text
SSOT: .ai/runtime/continuity/continuity.sqlite
Canonical store URI: sqlite://.ai/runtime/continuity/continuity.sqlite
Discover filter: same node + same mode
File-tree .ai/resume/** : DEPRECATED; not a discover source for runtime restore.
Do not delete existing archive trees solely for deprecation.
Do not use database://continuity as target_ref or store identity.
```

Cross-mode and cross-node records MUST NOT appear as restore candidates for the
active coordinate. Role labels on historical archives are not discover keys.

```text
Snapshot insufficient
  -> discover Resume candidates from continuity.sqlite only
  -> filter same node + same mode
  -> exclude records that fail Resume coordinate or compressed-context validation
  -> report source and checkpoint reference
  -> Commander selection
  -> resume-restore load selected record
  -> reject the selected record if the same validation no longer passes
  -> return passive rehydration candidate
       (snapshot.compressed_context and optional summary)
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
