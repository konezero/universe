# P0 Live Session Control Plane

Status: Approved implementation plan

## Purpose

Complete the live-session slice without turning Universe into a second
transcript store or a second provider chat runtime. The browser must display
the current state of a provider-owned one-to-one session and the distinct
state of Universe-owned collaboration rooms. Live input stays on the native
provider channel; durable work remains on the Task Frame and delegation path.

## Approved Boundary

Only these product roots may change:

- `tools/`
- `tools/universe_ui/`
- `tests/`
- `docs/`

Do not change `.ai`, global provider defaults, provider credentials, external
deployment, or unrelated UI composition. Do not persist raw transcript deltas,
full provider transcripts, hidden reasoning, tool arguments, or secrets.

## User-Facing Result

### Provider Session Room

- A selected supported provider session is a direct one-to-one room.
- The provider remains authoritative for conversation context, busy-input
  behavior, permission prompts, resume, and response streaming.
- Universe tails only newly observed, bounded and redacted display deltas.
- Unattached sessions remain observation-only. Mobile attach is explicit and
  exposes whether native control is available.

### Universe-Owned Room

- Conductor, Task Frame, Boss, meeting, and project coordination rooms remain
  Universe-owned event streams.
- Room delivery is incremental through the participant's native live channel.
- A room cursor never causes complete transcript replay or substitutes for a
  provider's own session context.

### UI Surfaces

- Chat remains a conversation surface only.
- Activity is an independent operational feed.
- Approval is an independent inbox with an explicit decision state.
- The user can cancel queued work, request cancellation for active work, or
  stop terminal-pending-review work. The UI displays the resulting state rather
  than pretending every cancellation is immediate.

## Data and Trust Boundary

```text
Provider transcript
  -> transient parser result / browser SSE delta
  -> UI state only

Provider cursor and reduced activity evidence
  -> Universe SQLite

Universe-owned room event
  -> ordered Room database event
  -> participant delivery cursor and UI SSE
```

The transient delta contract contains only a stable opaque source reference,
provider, event identity, role, redacted text, digest, and state transition.
It has no database write path. The server returns `UNSUPPORTED` for a provider
whose local source format cannot safely produce a delta.

No observer event, provider output, or Room message can approve a proposal.
The existing direct Commander/browser approval route remains the only decision
authority.

## Delivery Sequence

1. **Make the Task Frame execution path usable for this bounded source work.**
   Keep Worker sessions ephemeral and provider credentials process-local. Add a
   Master-owned mutation gateway path that accepts only a declared Task Frame
   target, the active Work Receipt lineage, a one-time Guard receipt, and an
   exact content digest. A Worker returns a bounded patch candidate; the Master
   alone applies a validated file mutation.
2. **Add provider delta projection.** Extend the provider observer with an
   explicit transient read API over already-attested cursor events. Start with
   Codex where semantic events exist; expose safe unsupported/unknown states for
   formats that cannot yet be parsed. Preserve existing activity scans and
   memory extraction behavior.
3. **Expose live-session endpoints.** Add compatibility-preserving routes for
   a bounded provider room snapshot/delta feed and explicit native attach
   status. Keep direct vendor rooms separate from `UniverseMultiRoom` records.
4. **Separate operational inboxes.** Add server projections for activity,
   approval, and delegation cancellation. Cancellation transitions are:

   ```text
   QUEUED -> CANCELLED
   RUNNING -> CANCELLATION_REQUESTED -> CANCELLED | COMPLETED | FAILED
   TERMINAL_PENDING_REVIEW -> CANCELLED
   COMPLETED | FAILED | CANCELLED -> unchanged
   ```

   Each transition is idempotent against its delegation identity and records a
   redacted outcome.
   The current CLI bridge has no verified provider-stop capability: an active
   cancellation suppresses result adoption and waits for the observed terminal
   state. It must not claim that the external provider process was terminated.
5. **Wire the UI.** Keep the persistent chat dock. Add compact Activity and
   Approval entry points, a selected-session live delta viewport, unread state
   that does not change scroll position, and cancellation controls keyed to
   server state. Provider session rooms must never be rendered as Universe
   multi-participant rooms.
6. **Verify linked behavior.** Cover cursor incrementality, redaction,
   unsupported providers, attach/control distinction, room separation,
   cancellation transitions/idempotency, API compatibility, and UI scroll /
   unread behavior. Run focused unit tests before the full Universe suite.

## Task Frame Topology

```text
Project Master (Parent)
  -> /root/boss (Codex Luna, bounded synthesis)
  -> /root/boss/sub1-implementer (declared source patch candidate)
  -> /root/boss/sub2-reviewer (independent boundary and security review)
  -> /root/boss/sub3-qa (focused regression verification)
  -> /root/boss final result
```

The Boss may allocate and invoke declared Sub turns only in dependency order.
No Sub rereads repository governance documents or performs startup. The Parent
observes bounded result packets and remains available to the user.

The owning Project Master resolves the Node/Mode Runtime before creating a
Task Frame. A Host capability check proves only that a Provider CLI is
available. It must not replace or require equality with the Provider/model
declared for the approved Task Frame turn; that declaration is execution
metadata from the owning Node/Mode policy.

## Completion Conditions

- Provider display deltas are transient, redacted, cursor-bound, and never
  appear in the Universe database.
- Vendor session rooms and Universe-owned rooms have distinct API/UI models.
- Activity, approval, and chat have separate state and navigation.
- Cancellation accurately reflects queued, active, and review-pending work.
- Worker patch application is constrained by Task Frame lineage and Guard
  receipt rather than raw provider CLI access.
- Existing public API routes and focused regression tests pass.
