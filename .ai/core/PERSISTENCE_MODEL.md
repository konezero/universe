# Persistence Model

Status: Candidate Core Architecture
Scope: ai-career
Layer: L3
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-01

## Purpose

Persistence Model defines recoverable durable state.

It explains what remains after runtime execution ends.

Persistence is not active execution.

Persistence is not authority by itself.

Persistence artifacts remain passive until explicitly restored, selected, or promoted.

## L3 Declaration

```text
WHEN RUNTIME STATE SHOULD SURVIVE,
PERSIST SELECTED MEANING.

WHEN CURRENT STATE SHOULD START FAST,
CREATE OR REFRESH RUNTIME SNAPSHOT.

WHEN RECOVERY LOSS MATTERS,
CREATE SUMMARY.

WHEN RESTORE NEEDS AN ANCHOR,
CREATE CHECKPOINT.

WHEN CONTINUITY IS NEEDED,
PRESENT RESUME CANDIDATE.

WHEN MEMORY IS STORED,
KEEP IT PASSIVE UNTIL SELECTED.
```

## Persistence Objects

```text
Runtime Snapshot
Summary
Checkpoint
Resume Candidate
Memory
Archive
```

## Runtime Snapshot

Runtime Snapshot is the project-owned current-state starting point.

It is a short index, not a full history.

Candidate project path:

```text
.ai/runtime/state/session.md
```

Runtime Snapshot should answer:

```text
Where am I now?
What role/mode/authority is active?
Which session_id + frame_id identifies the current frame?
What validation evidence proves the state?
What is the current execution assignment?
What should be fetched next only if needed?
```

Core rule:

```text
SNAPSHOT STARTS CURRENT WORK.
SNAPSHOT POINTS TO EVIDENCE.
SNAPSHOT DOES NOT REPLACE EVIDENCE.
FRAME STORE IS CACHE, NOT AUTHORITY.
GIT-BACKED SOURCE REMAINS AUTHORITY.
```

See:

```text
.ai/core/SESSION_CURRENTNESS.md
```

## Summary

Summary minimizes recovery loss.

It captures current recoverable state, not the whole conversation.

```text
SUMMARY REDUCES RECOVERY LOSS.
SUMMARY DOES NOT GRANT AUTHORITY.
```

## Checkpoint

Checkpoint anchors restore.

It provides a durable reference for recovery.

```text
CHECKPOINT ANCHORS RESTORE.
CHECKPOINT DOES NOT EXECUTE WORK.
```

The installed Reference Runtime persists immutable Checkpoint records in:

```text
.ai/runtime/continuity/continuity.sqlite
```

`checkpoint prepare` builds a candidate without writing. `checkpoint save`
commits that exact candidate and returns save evidence. `checkpoint list` and
`checkpoint load` are read-only. A repeated save of the same immutable
candidate is idempotent; the same ID cannot be reused for changed content.

## Resume Candidate

Resume Candidate proposes recovery from durable state when continuity is needed beyond the current Snapshot.

It does not auto-restore role, mode, or authority.

```text
CONTINUITY NEEDED
  -> BUILD RESUME CANDIDATE
  -> REPORT SOURCE
  -> WAIT FOR APPROVAL WHEN AUTHORITY WOULD CHANGE
```

### Runtime Resume SSOT

```text
CHECKPOINT and RESUME durability
  -> .ai/runtime/continuity/continuity.sqlite only

Restore candidate filter
  -> same node + same mode

File-tree Resume Archive (.ai/resume/** and resume_archive templates)
  -> DEPRECATED for runtime resume identity
  -> keep in place; do not delete solely for deprecation
  -> historical inspection only
```

Resume is not the default starting point when a valid Runtime Snapshot exists.

When a Runtime Snapshot exposes `previous_session_id`, `current_session_id`, and
`checkpoint_ref`, the runtime may use a Resume Candidate fast path:

```text
Snapshot + Current Anchor Frame
  -> lightweight Resume Candidate Proposal
  -> Commander approval
  -> checkpoint bundle load
```

This preserves context by not loading the full checkpoint bundle before approval
unless the Commander explicitly asks for details.

The installed Reference Runtime uses the same project-local continuity store
for Resume records:

```text
resume-save prepare
  -> resume-save save
  -> LOCAL_SQLITE_COMMITTED evidence

resume-restore discover
  -> Commander selects one durable candidate
  -> resume-restore load
  -> passive rehydration candidate
```

Loading a Resume record does not activate it. Role, Authority, Execution
Assignment, Mode Current Anchor, and executable Runtime currentness remain
separate decisions and evidence surfaces.

## Memory

Memory preserves selected knowledge.

Memory is advisory until selected by the active runtime.

Memory must not override current user input, repository truth, Runtime Snapshot, or Active Anchor.

```text
MEMORY STORES SELECTED KNOWLEDGE.
MEMORY REMAINS PASSIVE UNTIL USED.
MEMORY DOES NOT OVERRIDE ACTIVE ANCHOR.
```

## Archive

Archive is durable history and a source for recalled context.

Archive can preserve events, decisions, carrier observations, dispatch records, journals, and policy candidates.

Archive is used when the current task needs past context, decision rationale, long logs, previous observations, or historical evidence.

```text
ARCHIVE STORES DURABLE HISTORY.
ARCHIVE SUPPORTS RECALL.
ARCHIVE DOES NOT BECOME ACTIVE FRAME AUTOMATICALLY.
```

Archive-first derivation may still be useful for generating derived state, but current work should prefer Snapshot-first start when a valid snapshot exists.

## Role Separation

```text
Runtime Snapshot
  -> current state / starting point

Resume Candidate
  -> continuity recovery when current state is insufficient

Archive
  -> historical recall when past context is needed
```

## Storage Provider Boundary

Persistence may use different storage providers.

Examples:

```text
GitHub
Local workspace
Files / iCloud Drive
Google Drive
NAS
Other providers
```

The storage provider does not decide authority.

```text
STORAGE PROVIDER STORES ARTIFACTS.
ACTIVE ANCHOR INTERPRETS ARTIFACTS.
```

Checkpoint and Resume use the installed project-local SQLite provider by
default. Archive, Memory, cross-Host handoff, and external synchronization may
use other approved providers. Provider choice never promotes passive records
into active state.

## Host Capability Boundary

```text
Execution Host bound to project filesystem
  -> local continuity prepare / save / list / load

Mobile or web Connector with approved Provider writer
  -> HANDOFF_APPEND only
  -> no local SQLite claim

Read-only source Connector
  -> no durable persistence claim
```

Source observation does not prove that the continuity database exists or is
reachable. A Connector may append selected Runtime-owned handoff artifacts only
when its Provider write capability, exact append path, approval or selection,
and Provider result evidence are all available.

## Sync Ready vs Sync Complete

A prepared artifact is not necessarily durable.

```text
Sync Ready
  -> artifact is prepared but not yet saved to durable storage

Sync Complete
  -> artifact is saved to approved durable storage
```

Chat-only output or downloadable sandbox files are at most Sync Ready until the user saves or syncs them to a durable target.

## Memory Sync

Memory Sync extracts reviewed knowledge candidates.

It should not store every conversation by default.

```text
Conversation
  -> Candidate extraction
  -> User selection
  -> Memory artifact
  -> Storage provider
```

Candidate principle:

```text
MEMORY SYNC PRODUCES REVIEWED KNOWLEDGE ARTIFACTS.
STORAGE PROVIDER SHOULD BE PLUGGABLE.
GITHUB SHOULD BE OPTIONAL, NOT ASSUMED.
```

## Passive Until Restored

Persistence artifacts must remain recoverable but passive.

```text
PERSISTENCE DOES NOT GRANT AUTHORITY.
PERSISTENCE DOES NOT CONTROL EXECUTION.
PERSISTENCE BECOMES ACTIVE ONLY THROUGH ANCHORED RESTORE OR SELECTION.
```

Runtime Snapshot is read early, but it still does not grant authority by itself.

Authority remains interpreted through Active Anchor and current user intent.

Frame stores, including SQLite-backed stores, are caches and currentness indexes.

They are not canonical authority sources.

```text
Frame DB = cache.
Git-backed source = authority.
```

## Layer Boundary

L3 stores and recovers.

L3 does not execute current tasks.

L3 does not interpret authority.

L3 can provide evidence to L0/L1/L2.

```text
L3 PRESERVES.
L2 EXECUTES.
L1 ALIGNS.
L0 INTERPRETS.
```

## Placement Test

A concept belongs in L3 when it answers:

```text
What should survive?
What can be restored?
What is durable?
What is resumable?
What remains passive until explicitly selected?
What current-state index should be persisted for fast restart?
```

If the concept controls the current task, it belongs in L2.

If the concept starts or rebuilds the session lifecycle, it belongs in L1.

If the concept defines the reference point or authority boundary, it belongs in L0.
