# Cooperative Contention Resolution — Proposal

Status: DRAFT / for operator review
Date: 2026-09-04
Authors: Conductor (Claude) synthesis of an operator brainstorm + an
adversarial design review by the Codex Project Master (session-bus thread
`msg_3f2097d11359b560`).

## Problem

Multiple live sessions (Claude / Codex / Grok, each a Fleet-axis executor)
share one physical working tree. Today nothing stops two of them from
editing the same file in service of different tasks; the collision is only
discovered later, as a Git merge conflict — *after* both sets of edits
already exist.

## Philosophy

Orca isolates: each agent gets its own worktree so they never meet.
Universe takes the opposite stance — **let them meet, and make them talk**.
When Universe sees that two sessions want the same resource it does not
lock and does not decide; it surfaces the overlap and lets the two owning
sessions negotiate directly over the session bus.

```
Resource Presence
      ↓
overlap detected  (Universe, server-side)
      ↓
Peer Negotiation  (the two sessions, over the bus)
      ↓
┌──────────────────────────────────────────────┐
│ DELEGATE   "I'll fold your change into mine"  │
│ WAIT       "do it after I release"            │
│ PARALLEL   "both proceed, expect a conflict"  │
│ HANDOFF    "you take this from here"          │
└──────────────────────────────────────────────┘
      ↓
coordination / session state update
```

Order matters: Git resolves conflicts *after* the edits exist. This
resolves **intent conflicts before implementation**.

This is a different layer from Meeting. Meeting answers "what do we build".
This answers "how do we not step on each other in the next few minutes" —
short, operational, seconds-scale.

## What v1 IS and IS NOT  (load-bearing)

- v1 is **cooperative coordination plus an audit trail**. It is NOT a lock,
  NOT a safety guarantee, NOT worktree isolation.
- `EXECUTION_GUARD_PERMITTED_WITH_CONTENTION` still *permits* the mutation.
  The contention record is advisory. A session may keep working on
  non-overlapping parts regardless.
- `PARALLEL` records **consented risk**. It does not prevent
  last-writer-wins, does not protect a merge base, and must never be
  presented as "safe because a message was delivered".
- Coordination is **not an authority source**. `DELEGATE` and `HANDOFF`
  never broaden a session's authority; they must stay inside the receiving
  Work Receipt's assignment and write scope and route through the existing
  governed assignment machinery.

## Presence source

Two complementary signals, both derived from machinery that already runs:

### 1. Work Receipt scope + `resource_intent` (authoritative, forward-looking)

The active execution-guard Work Receipt is the authoritative statement of
*who is allowed to touch what* and is already heartbeat-bound and scoped
to one server-resolved workspace. It is **not** an exact statement of what
the session intends to edit — write roots are often broad.

So add a small `resource_intent` declaration *inside* receipt `prepare`
(no separate user-facing step):

```
resource_intent {
  operation: CREATE | MODIFY
  paths:     canonical globs or exact repo-relative paths
  duration:  optional expected minutes
}
```

The intent must be a subset of the receipt scope. If absent, fall back to
comparing the broader scope and return `uncertain = true`. Re-declare on
scope expansion or receipt renewal.

### 2. `resource_touch` history (observed, backward-looking, audit substrate)

Every guarded mutation already flows through a receipt bound to
`session_anchor_ref` + `instruction_ref` and knows its target path. On
`prepare` and on `consume`, write one coalesced row:

```
resource_touch {
  resource_path, node_ref,
  session_anchor_ref, task_frame_ref, instruction_ref, receipt_id,
  operation, state (PENDING | DONE), touched_at
}
```

Coalesce per `(session_anchor_ref, resource_path, task_frame_ref)` within a
window — one row saying "anchor X is working on path Y as of Z", not one
per edit.

This single table yields three things:
1. **Decaying presence** — recent `PENDING`/`DONE` touches by still-LIVE
   anchors. Fresh (`< ~5 min`) = active presence; older = "worked here
   recently, ask before large changes". Presence is a gradient, not binary.
2. **"in service of what"** — the `instruction_ref` / `task_frame_ref` on
   the touch: *"Codex touched session_bus.py while doing todo_X"*.
3. **Intent-level blame** — richer than `git blame`: the task, not the
   commit.

`node_ref` on the row buys typed-node-level presence for free when the
unified node graph lands.

## Data model

### Contention group (not pairwise)

Pairwise negotiation misses three-way and transitive overlap. Model a
**contention group**:

```
contention {
  contention_id            (stable; transactional + idempotent create)
  workspace_id             (server-resolved repository/workspace identity)
  resource_summary         (canonical digest + short display; bounded, no secrets)
  scope_version
  participants: [ { session_anchor_ref, receipt_id, provider, intent, uncertain } ]
  decision_state: OPEN
  created_at, expires_at
}
```

Simultaneous `prepare` from A and B must converge on **one**
`contention_id` (both see the same record) — this closes the TOCTOU race
where each sees "no peer". Creating the record must not reserve the
resource.

### Coordination negotiation state machine

Bus delivery state is not decision state. Duplicate, late, and
out-of-order replies are expected.

```
coordination {
  coordination_id
  contention_id
  proposal_version
  idempotency_key
  state: OPEN → PROPOSED → ACCEPTED | REJECTED → RESOLVED
         (EXPIRED, CANCELLED are terminal)
  proposed_outcome, proposed_by, accepted_by
  target_receipt / target_resource_subset, expected_version
  deadline
}
```

A single participant's proposal is **not** a resolved decision unless the
outcome is one-sided by contract. Transfer/delegation needs explicit peer
acceptance; PARALLEL needs mutual consent.

### Scope matching / canonicalization

- repo-relative paths only; normalize Windows case and slashes
- reject `..`; explicit symlink / junction policy
- directory-vs-file rules
- `uncertain` bit for broad globs
- a client-supplied peer identity is never trusted — the server resolves
  workspace and participant identity

### Transport

No new system. Session bus already: durable, carries
`session_anchor_ref` / `node_ref` / `task_frame_ref`, has a
`QUEUED → ACCEPTED → STARTED` lifecycle. Add:

```
kind: COORDINATION
subtype: RESOURCE_OVERLAP
```

one durable message addressed to **both** participants, carrying
`contention_id`, `coordination_id`, receipt IDs, anchors, resource digest,
scope version, initiator, expiry.

## Outcome semantics (refined)

| Outcome | Contract |
|---|---|
| **DELEGATE** | B proposes A owns the exact canonical resource subset. A accepts; B releases that subset. Not complete until the delegated change is acknowledged **and validated**. Does not auto-mutate A's receipt — routes through governed assignment. |
| **WAIT** | B enters `WAITING_FOR_PEER` **only for the overlapping subset**, keeps non-overlapping work. A's release event carries the resource version and a fresh prepare/recheck path for B. Needs an explicit expiry + wake-up contract. |
| **PARALLEL** | Both explicitly consent to the exact scope, workspace, risk, and merge responsibility. Records *expected conflict*. Grants no authority, provides no isolation. Same-worktree same-file PARALLEL is opt-in and rendered as visibly risky. |
| **HANDOFF** | A releases and B accepts an **atomic transfer** of the resource (or task). The old receipt must stop claiming that subset before the new binding is active. Needs a transfer generation, not a chat message. |

Operationally: `DELEGATE` / `HANDOFF` are the most over-specified (they
imply receipt/assignment mutation — keep that on the existing governed
path). `PARALLEL` is the most technically under-specified (shared-tree
writes can be unrecoverable). `WAIT` needs the expiry/wake-up contract
spelled out.

## Hard parts and mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | TOCTOU on simultaneous `prepare` — both see no peer | transactional, idempotent contention create; converge on one `contention_id`; no resource reservation |
| 2 | Shared physical worktree → same-path PARALLEL is last-writer-wins | server-resolved `workspace_id` as a match precondition; PARALLEL opt-in + visibly risky; never imply Git merge auto-recovers |
| 3 | Pairwise misses 3-way / transitive overlap | contention **group** with participants + resource version (first UI may still render one peer) |
| 4 | Scope-match ambiguity | canonicalization rules above; `uncertain` bit; never trust client peer identity |
| 5 | Bus delivery state ≠ decision state; dup/late/out-of-order replies | `coordination_id` + `proposal_version` + idempotency key + the state machine |
| 6 | Receipt close ≠ resource available (uncommitted edits remain) | `RESOURCE_AVAILABLE` tied to a **released receipt generation**, scoped/versioned; late events do not reopen an old negotiation |
| 7 | A peer broadening another session's authority | `DELEGATE` / `HANDOFF` require both-party acceptance and stay within the receiving receipt's assignment + write scope; coordination is not approval |
| 8 | Stale presence after a session dies | heartbeat TTL with an epoch + grace period; mark presence `SUSPECT` before expiry; reject messages from an old host/session generation |
| 9 | Negotiation latency — sessions read the bus only at `TURN_IDLE` | bounded expiry; on timeout → **`EXPIRED`, re-ask** — do **not** silently pick `WAIT` or `PARALLEL` |
| 10 | Deadlock — A waits on B, B waits on A | cycle detection on the contention group → escalate to Conductor |

## Minimal first slice

Scope: active `LOCAL_INSTRUCTION_WORK` receipts in the **same physical
workspace**, **path-glob overlap only**, `CREATE` / `MODIFY` only.

1. **On receipt `prepare`**: server-resolve the workspace + current host
   heartbeat, canonicalize the declared `resource_intent`, find other
   active compatible receipts, create/reuse a `contention` record. Return
   `EXECUTION_GUARD_PERMITTED_WITH_CONTENTION` with `contention_id`, peer
   summaries, canonical resource summary, `uncertain`, `expires_at`,
   `decision_state = OPEN`. Do **not** block the receipt; do **not** treat
   this response as consent.
2. **Emit one durable `COORDINATION` / `RESOURCE_OVERLAP`** message to both
   participants (fields above; bounded paths; no secret-bearing values).
3. **Accept four typed replies**, each idempotent and tied to the current
   `proposal_version`. A reply records proposer, accepter, exact target
   receipt/resource subset, expected version, optional deadline.
4. **Update only coordination / session state**: `WAITING_FOR_PEER` for the
   waiting participant; `RESOLVED` + audit evidence for accepted decisions;
   `RESOURCE_AVAILABLE` on release; `EXPIRED` on timeout. Keep actual
   assignment transfer and receipt-scope mutation on the existing governed
   assignment path until that path has a tested **atomic transfer
   primitive**.
5. **UI**: a non-modal dock chip `Contention · 2` with a state color. Its
   popover shows peer provider/session, short resource summary, age / TTL,
   the `uncertain` flag, and the four actions, and renders `WAITING`,
   `PARALLEL CONSENTED`, `HANDOFF`, or `EXPIRED`. It must never claim a
   write is safe merely because a message was delivered. The negotiation
   thread itself shows in the existing session-bus inbox dialog.

## Explicit v1 cuts

1. **No node-graph / capability overlap, no semantic conflict analysis.**
   Path globs in one server-resolved workspace are enough to prove the
   transport and the lifecycle. ("auth refactor" vs "login retry fix" —
   Universe flags the coordinate overlap; the sessions judge whether it is
   a real conflict.)
2. **No automatic receipt / assignment mutation, no automatic timeout
   choice.** Record proposals and consent first; require the existing
   assignment machinery for `DELEGATE` / `HANDOFF`; a timeout surfaces
   `EXPIRED` rather than silently selecting `WAIT` or `PARALLEL`. `PARALLEL`
   stays an explicit, visibly risky audit outcome — not a default, not an
   isolation mechanism.

## Open questions for the operator

- Is the dock chip the right first surface, or should contention also show
  on the Todo / file row directly (`session_bus.py  ● Claude editing
  ● Codex needs this  Coordinating…`)?
- `WAIT` wake-up: push a bus message on release, or have the waiting
  session re-poll on its next `TURN_IDLE`?
- Should `resource_touch` rows be pruned, or kept as permanent
  intent-level history (they double as an activity log)?
- Where does the atomic transfer primitive for `DELEGATE` / `HANDOFF` live
  — extend the todo-action receipt, or a new assignment-transfer action?
