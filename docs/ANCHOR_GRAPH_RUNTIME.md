# Anchor Graph Runtime

Status: product contract

Universe models project continuity as an anchor graph. An anchor identifies a
durable lineage coordinate; UI selection identifies only which coordinate the
operator is currently viewing.

## Graph

```text
Project
  +-- Project Room
  +-- Mode Anchor (one per Mode)
  |     +-- Session Anchor A
  |     |     +-- exact vendor session / chat key
  |     |     +-- Task Frame lineage
  |     +-- Session Anchor B
  |           +-- exact vendor session / chat key
  |           +-- Task Frame lineage
  +-- Meeting Rooms (zero or more, explicit membership)
```

## Invariants

1. A project has exactly one Mode Anchor for each registered Mode.
2. A Mode Anchor appends Session Anchor references. It is not a pointer that
   replaces earlier sessions and it does not infer one globally active session.
3. Every persistent session owns one Session Anchor. The Session Anchor binds
   `project_id`, `mode`, the Universe session identity, the exact vendor session
   identity or chat key, and its Task Frame descendants.
4. The left panel lists all Session Anchors belonging to the selected Mode. The
   selected card is per-Mode navigation state; selecting it attaches the chat to
   that exact Session Anchor and vendor chat coordinate.
5. `ACTIVE`, process liveness, currentness, and selected chat are separate
   states. None may silently rewrite another.
6. A direct user chat message is delivered only to the selected session chat,
   Project Room, or Meeting Room chosen by the user. It never enters the shared
   work queue.
7. The shared work queue is an internal cross-session delegation transport. A
   queue item must name both `origin_session_anchor_ref` and
   `target_session_anchor_ref`; its result rejoins the exact origin anchor.
8. A Task Frame is a child of its originating Session Anchor. Worker execution
   may be ephemeral, but Task Frame status and results retain
   `origin_session_anchor_ref` and rejoin only that session.
9. Project Room is the fixed project-wide conversation. A Meeting Room is a
   separate room identity with explicit invited sessions. Neither is a session
   chat or a work queue.

## Local work authorization

A Task Proposal records a reviewable plan and may be cited as reference. It is
not approval evidence. One direct user instruction authorizes the bounded local
work described by that instruction. The execution guard verifies the current
instruction coordinate, roots, operations, concrete target, payload digest, and
Task Frame lineage without creating or requiring a separate approval event.

Local edits, tests, Task Frames, staging, commits, and pushes do not require
another approval prompt. The selected Session Anchor receives bounded work-status
notifications (`STARTED`, `COMPLETED`, `FAILED`, or `CANCELLED`). Commit and push
are notification-only milestones such as `COMMIT_COMPLETED`, `COMMIT_FAILED`,
`PUSH_COMPLETED`, and `PUSH_FAILED`; they never create approval evidence.
Destructive effects remain outside an instruction unless explicitly named.

Provider processes inherit a session-scoped Git Trace2 event target. Universe
parses only terminal `commit` and `push` command names plus exit codes after a
turn, emits notification-only milestones, and removes the trace file when the
provider session closes. Trace argv and repository paths are never projected
into Provider Session events.

## Selection and delivery

```text
select Mode
  -> list Mode Anchor's Session Anchor refs
select Session card
  -> persist selected_session_anchor_ref for that Mode
  -> resolve exact vendor chat coordinate from that Session Anchor
send user message
  -> deliver directly to that selected conversation

delegate work to another session
  -> append internal queue item with origin and target Session Anchor refs
  -> target executes
  -> result attaches to origin_session_anchor_ref
```

The graph is durable truth. Selection is recoverable UI state. Delivery must
always name the exact destination coordinate.
