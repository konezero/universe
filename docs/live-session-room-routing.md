# Live Session Room and Incremental Room Routing Contract

| Field | Value |
|---|---|
| Status | Fixed implementation contract |
| Date | 2026-08-09 |
| Scope | Vendor session rooms, Universe-owned rooms, live provider input, incremental observation and delivery |
| Replaces | Full-room transcript injection and chat-queue delivery assumptions |

## 1. Core model

Universe exposes provider sessions and Universe-owned collaboration rooms through
one UI, but they have different ownership.

### 1.1 Vendor Session Room

A Vendor Session Room is a one-to-one view and control surface for one real
Codex, Claude, or Grok session.

```text
Universe Session Room <-> Provider Session
```

The provider session owns conversation context, in-flight input behavior,
permission prompts, resume semantics, and response streaming. Universe attaches
to the provider-native control channel and tails the provider activity stream.

### 1.2 Universe-Owned Room

Task Frame, Boss, Meeting, Conductor-to-Master, and other multi-participant rooms
are created and owned by Universe.

```text
Universe Room
  +-- Participant A -> live provider session
  +-- Participant B -> live provider session
  +-- Observer UI
```

Universe owns the room event order and membership. Each participant remains a
real provider session. New room messages are delivered incrementally to each
participant's native live input channel.

## 2. Room observation boundary

The Room database is the boundary at which Universe observes room activity. It
is not a replacement provider transcript and is not a transport queue.

It records room identity, membership, ordered room events, provider/session
bindings, observation cursors, delivery cursors, activity state, and bounded
display material. Reading a room never updates provider currentness.

For a Vendor Session Room, the provider transcript is authoritative and the
Room database is an observed projection. For a Universe-Owned Room, the ordered
Universe room event stream is authoritative for room coordination; provider
session transcripts remain authoritative for each participant's own session.

## 3. Native live input

User or room input is sent through the provider-native live session surface.

```text
Universe UI or Universe Room event
  -> provider adapter send_input
  -> Codex app-server / Claude resident stream-json / Grok ACP
  -> provider-native accepted, deferred, interrupted, rejected, or disconnected state
```

Universe does not implement a second busy-input policy. If a provider accepts
input while work is active, the provider decides whether to steer, defer,
interrupt, or reject it. Universe only projects that observed result.

An externally discovered session is observation-only until an adapter obtains a
valid attach, resume, or native control channel.

## 4. No transcript or automatic context injection

The following are forbidden on ordinary live-room turns:

- replaying the full provider transcript;
- injecting the last N room messages on every turn;
- serializing room history into `runtime_context`;
- automatically sending a fixed Context Pack when a provider changes;
- using a chat queue to rebuild provider conversation state.

A new or replaced provider session attaches to the same Project Current Anchor.
It receives only its current coordinate, role, room binding, current input, and
available retrieval capabilities. Todo, Proposal, document, activity, Room RAG,
and Memory RAG content is resolved lazily when needed.

Task Frame Worker input bundles remain a separate bounded execution contract.
They do not justify replaying a Room transcript.

## 5. Independent cursors

The implementation must not collapse these cursors:

```text
participant_delivery_cursor
  Last Universe Room sequence accepted by one participant's provider session.

provider_observation_cursor
  Last provider-native activity position observed for one provider session.

viewer_cursor
  Last room event viewed by one UI client.
```

Only participant and provider activity changes session activity/currentness.
Opening, scrolling, or reconnecting an observer UI does not.

## 6. Incremental fan-out

Every Universe-Owned Room event has a monotonic `room_sequence` and immutable
`room_event_id`. For each active participant:

1. Read events where `room_sequence > participant_delivery_cursor`.
2. Exclude events whose `origin_participant_id` is that participant.
3. Send each unseen event once through the participant's native live input.
4. Advance the delivery cursor only after provider acceptance is observed.
5. Tail provider output incrementally and append newly observed participant
   output to the room event stream.
6. Use provider event identity plus room correlation identity to suppress echo
   and duplicate observation.

Reconnection resumes from cursors. It never resends the complete room history.
An uncertain provider acceptance is surfaced as uncertain; it is not silently
retried as a new input.

## 7. Queue boundary

Queues remain valid for bounded asynchronous execution and durable handoff:

- Master to Task Frame Boss/Worker dispatch;
- batch execution and dependency ordering;
- result packets and external handoff;
- offline observation ingestion where the producer owns an append contract.

Queues are not used for live user-to-provider chat or incremental Room fan-out.
Provider-native deferral is an observed provider state, not a Universe queue
entry.

## 8. Required participant states

```text
OBSERVED      provider activity can be tailed
ATTACHED      provider session is bound to a Room and Anchor
CONTROLLED    a native input channel is available
LIVE          input and output are currently available
DISCONNECTED  no live control channel is available
```

Observation capability must never be inflated into control capability.

## 9. Implementation boundary

The first implementation slice must:

1. Stop Conductor resident-session turns from embedding room history.
2. Add provider-native incremental input and output streaming to Conductor as
   already used by Project Master where possible.
3. Add participant delivery and provider observation cursors for
   Universe-Owned Rooms.
4. Route only unseen Room events to each participant CLI session.
5. Keep Vendor Session Room tailing one-to-one and observation-first.
6. Keep Task Frame dispatch queues separate from Room transport.
7. Add reconnect, duplicate, echo, disconnect, and cursor-regression tests.
8. Expose room/participant activity for observer UI without making viewing an
   activity event.

The current foundation implements the ordered event plane, independent cursors,
duplicate/echo suppression, non-replaying attach semantics, Conductor native
streaming, and the observer UI. The resident Project Master path now registers
a process-local native control for its selected Codex, Claude, or Grok session.
The Room coordinator records `QUEUED` without advancing the cursor, advances it
only after provider acceptance, streams deltas as transient observations, and
stores only the final provider message as a durable Room event. Native control
callbacks and credentials are never stored in the Room database.

Provider-specific controls for imported external sessions and arbitrary
Meeting participants must still register with the delivery coordinator before
a binding may move from `OBSERVED`/`ATTACHED` to `CONTROLLED`/`LIVE`. Until
then, discovery is observation-only and no delivery cursor is advanced.

## 10. Acceptance criteria

- A live provider receives only the current input, not prior transcript text.
- A participant joining or reconnecting to a Room receives only events after
  its accepted delivery cursor.
- A participant never receives its own tailed output as new input.
- Provider output appears incrementally in the observing Room UI.
- Viewing the Room does not change provider currentness.
- Provider replacement attaches under the same Project Current Anchor without
  transcript injection.
- A tail-only external session remains read-only until native control attach.
- Task Frame dispatch queues continue to work without sharing chat delivery
  state or tables.
