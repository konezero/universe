# Session Framework

Status: Candidate Core Architecture
Scope: ai-career
Layer: L1
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-01

## Purpose

Session Framework defines lifecycle transitions.

It does not decide authority by itself.

It uses the Active Anchor from L0 as the reference point for starting, rebuilding, recovering, switching, or ending a session.

## L1 Declaration

```text
WHEN A SESSION STARTS,
BOOT FROM ACTIVE ANCHOR.

WHEN A PROJECT RUNTIME SNAPSHOT EXISTS,
READ SNAPSHOT AS THE FAST STARTING POINT.

WHEN A SESSION DRIFTS,
REBOOT BY PULLING ACTIVE ANCHOR FORWARD.

WHEN DURABLE STATE EXISTS,
USE SNAPSHOT FIRST AND RESUME ONLY WHEN CONTINUITY IS NEEDED.

WHEN MODE CHANGES,
REBUILD ALIGNMENT WITHOUT GRANTING NEW AUTHORITY.

WHEN SESSION ENDS,
PRESERVE RECOVERABLE STATE AND WEAKEN ACTIVE FRAME.
```

Session currentness is session-scoped.

```text
EXECUTABLE RUNTIME CURRENTNESS KEY = SESSION_ID + FRAME_ID.
MODE CURRENT ANCHOR KEY = MODE.
MODE IS NOT EXECUTABLE RUNTIME CURRENTNESS IDENTITY.
```

See:

```text
.ai/core/SESSION_CURRENTNESS.md
```

## Lifecycle Commands

```text
Boot
Reboot
Resume
Mode Change
Session Out
```

## Boot

Boot starts a new session lifecycle.

```text
BOOT
  -> READ CURRENT USER INPUT
  -> ESTABLISH ACTIVE ANCHOR
  -> READ RUNTIME SNAPSHOT WHEN AVAILABLE
  -> CHECK SNAPSHOT EVIDENCE POINTERS
  -> LOAD GOVERNANCE
  -> SELECT ROLE / MODE WHEN AUTHORIZED
  -> BUILD RUNTIME
  -> REPORT READY
```

Boot readiness is not execution approval.

Snapshot is the preferred fast starting point when it exists.

```text
SNAPSHOT
  -> current state

RESUME
  -> continuity recovery when needed

ARCHIVE
  -> historical recall when needed
```

## Snapshot First

Snapshot First means current work starts from the shortest project-owned current-state index.

Candidate project path:

```text
.ai/runtime/state/session.md
```

Expected flow:

```text
Session starts or attaches to project
  -> read Runtime Snapshot
  -> resolve session_id + frame_id currentness key when frame data exists
  -> confirm Anchor / Authority / Validation pointers
  -> fetch validation or role/scope files only when needed
  -> enter Task Frame
```

Core rule:

```text
Work starts from current state.
Past context is fetched only when needed.
```

## Reboot

Reboot is not context deletion.

Reboot is anchor-based priority rebinding.

```text
REBOOT
  -> PULL ACTIVE ANCHOR FORWARD
  -> READ OR REFRESH RUNTIME SNAPSHOT WHEN AVAILABLE
  -> DISCARD STALE WORKING ASSUMPTIONS
  -> LOAD GOVERNANCE
  -> CHECK SNAPSHOT / VALIDATION / RESUME CANDIDATE AS NEEDED
  -> DECLARE ACTIVE RULES
  -> REBUILD RUNTIME
  -> REPORT READY
```

Reboot does not make old roles active automatically.

It rebuilds from the current anchor.

## Resume

Resume proposes recovery from durable state when Snapshot and current evidence are not enough.

It does not grant authority by itself.

```text
CONTINUITY NEEDED
  -> BUILD RESUME CANDIDATE
  -> REPORT SOURCE / ROLE / CHECKPOINT
  -> WAIT FOR USER APPROVAL WHEN AUTHORITY WOULD CHANGE
  -> RESTORE ONLY AFTER AUTHORIZATION
```

Resume is a recovery view, not the source of authority.

Resume is not the default boot starting point when a valid Runtime Snapshot exists.

## Archive Recall

Archive is not the default boot starting point.

Archive is used when the session needs past context, decision rationale, long logs, previous observations, or historical evidence.

```text
CURRENT STATE NEEDED
  -> Snapshot

CONTINUITY NEEDED
  -> Resume

PAST CONTEXT NEEDED
  -> Archive
```

## Mode Change

Mode Change changes role or mode alignment inside a session.

It must not confuse style continuity, prior memory, or previous role state with current authority.

```text
MODE CHANGE
  -> CHECK CURRENT ANCHOR
  -> IDENTIFY TARGET MODE
  -> KEEP SESSION CURRENTNESS KEY SEPARATE FROM MODE
  -> WEAKEN PRIOR MODE FRAME
  -> BUILD NEW MODE FRAME
  -> REPORT MODE READY
```

Mode change does not create currentness identity by itself.

```text
session_id + frame_id
  -> currentness key

mode
  -> behavior contract
```

## Session Out

Session Out ends the current lifecycle.

```text
SESSION OUT
  -> SUMMARIZE MEANINGFUL STATE WHEN NEEDED
  -> CHECK SNAPSHOT / SYNC / ARCHIVE NEED
  -> WEAKEN ACTIVE FRAME
  -> DISCARD TASK-LOCAL ASSUMPTIONS
  -> REPORT COMPLETION STATE
```

Session Out should preserve recoverable meaning without keeping the completed runtime dominant.

## Layer Boundary

L1 may trigger runtime rebuilds.

L1 does not store memory directly.

L1 does not grant authority from persistence artifacts.

```text
SESSION COMMANDS ALIGN THE LIFECYCLE.
AUTHORITY STILL COMES THROUGH ACTIVE ANCHOR.
PERSISTENCE STILL REMAINS L3.
```

## Placement Test

A concept belongs in L1 when it answers:

```text
When does the session begin?
When does it rebuild?
When does it restore?
When does it switch?
When does it end?
What is the first current-state artifact to read?
```

If the concept answers how a task executes, it belongs in L2.

If the concept answers what remains durable, it belongs in L3.
