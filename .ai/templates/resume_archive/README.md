# Resume Archive Template

Status: **DEPRECATED (runtime resume path)**  
Deprecation date: 2026-07-31  
Branch / track: ai-career PR #269 (`codex/source-only-conductor-boundary`)

## Runtime Resume SSOT

```text
Runtime Resume / Checkpoint durability
  -> .ai/runtime/continuity/continuity.sqlite

Commands
  -> RESUME_SAVE / RESUME_RESTORE (resume-save / resume-restore Skills)
  -> CHECKPOINT prepare/save

Restore filter
  -> same node + same mode

Session evidence key
  -> session_id + frame_id
  -> session_id shape: <NODE>-<MODE>-YYYYMMDD-<LOCATION>-SEQ

Do not use this file-tree archive as RESUME_SAVE or RESUME_RESTORE identity.
Do not use repository + role as runtime resume identity.
Do not delete this template solely for deprecation; keep it historical.
```

See `.ai/templates/runtime_continuity/README.md`,
`.ai/skills/common/resume-save/SKILL.md`, and
`.ai/skills/common/resume-restore/SKILL.md`.

## Historical purpose (non-runtime)

This template previously described a repository-backed Resume Archive under
`.ai/resume/<role>/`. Existing trees remain readable historical material only.
They are not the default continuity path and must not auto-restore Mode,
Authority, or Execution Assignment.

## Target Path (historical)

```text
.ai/resume/<role>/
```

## Required Files (historical layout)

```text
.ai/resume/<role>/
  manifest.json
  index.md
  checkpoints.md
  capabilities/<role>.md
  profiles/communication.md
  snapshots/current_state.md
  snapshots/decisions.md
  snapshots/next_actions.md
  history/README.md
  conversation/
```

## Identity Rule (deprecated)

```text
DEPRECATED: Repository + Role = Resume Identity

Runtime resume identity is Node + Mode (continuity store).
Role remains a Mode-resolved internal value, not the restore key.
```

Do not use old chat session id as the restore identity.

## Resume Archive vs Runtime Continuity vs Runtime Archive

```text
Resume Archive (this template)  DEPRECATED file tree; historical only
Runtime Continuity Store        SSOT for CHECKPOINT + RESUME records (SQLite)
Runtime Archive                 scheduled/automation execution record
```

Use `.ai/templates/runtime_archive/README.md` for Carrier, Dispatch, Night Audit,
or other scheduled Runtime execution records.

## Minimal Manifest Shape (historical)

If an existing archive is annotated, set:

```json
{
  "archive_schema": "1.0",
  "archive_type": "resume-archive",
  "status": "deprecated",
  "runtime_resume_ssot": ".ai/runtime/continuity/continuity.sqlite",
  "resume_identity": "deprecated:repository+role",
  "runtime_resume_identity": "node+mode"
}
```

Legacy fields such as `role` and `checkpoint_id` may remain for history only.

## Minimal Read Order (historical inspection only)

```text
1. manifest.json
2. index.md
3. snapshots/current_state.md
4. snapshots/decisions.md
5. snapshots/next_actions.md
6. capabilities/<role>.md
7. profiles/communication.md
```

Optional:

```text
8. checkpoints.md
9. history/
10. conversation/
```

This order is for human/historical review. Runtime restore uses continuity
discover/load only.

## Snapshot Rules

Snapshots are fast boot state for historical archives.

They should answer:

- What is the role?
- What is active now?
- What decisions are already settled?
- What should happen next?

Snapshots should not become full conversation dumps.
Compressed conversation for runtime resume belongs in the continuity
RESUME candidate payload, not as an archive dump.

## History Rules

History entries explain why a checkpoint matters.

Do not copy full snapshots into history unless there is a specific reason.

Git stores exact diffs. History stores checkpoint intent.

## Safety Rule

Historical Resume Archive material restores no authority.

Runtime Resume load is passive rehydration only. User approval remains final.
Mode Current Anchor, Authority, and Execution Assignment are never granted by
archive presence or continuity load alone.
