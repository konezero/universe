# Multi-Room Chat Architecture (Product)

| Field | Value |
|-------|--------|
| **Status** | Draft product architecture; live routing contract fixed |
| **Date** | 2026-08-09 |
| **Scope** | Universe Instance chat rooms, session attach, streaming, dashboard |
| **UI strategy** | Function-first thin chrome first; major visual redesign later |
| **Related** | `docs/live-session-room-routing.md`, `docs/room-session-attach-streaming.md`, `docs/local-universe-service.md`, `docs/local-session-supervisor.md`, `docs/universe-design-and-bench-flow.md` |

---

## 1. Overview

Universe needs more than one Project Master dock. Operators run **several concurrent
chat rooms** with **live streaming** and **status refresh**, while roles and write
rights differ by room type.

Work actually happens in **Boss rooms** (Task Frame / node-scoped). The Project
Master is parent of many Task Frames and **moves between Boss rooms** (attach) to
instruct and coordinate. Users speak instructions primarily to the Master; in Boss
rooms they **observe only**. Boss rooms produce durable turns and boss-distilled
artifacts (bench/files) that become **decision evidence**.

A separate **Meeting Room** is a user-visible, persistent collaboration space.
The Conductor can convene relevant agents, while Masters, Bosses, Workers, and
research-oriented models can request information, coordinate feature boundaries,
escalate scope or authority questions, and co-author documents. The user may
observe, intervene, redirect, or adopt an outcome. Automated multi-model debate is
only one optional facilitation policy; it is not the room's defining behavior.
Meeting output remains proposal material and never creates execution authority by
itself.

The normative live transport and observation rules are fixed in
`docs/live-session-room-routing.md`. Provider sessions retain their own context;
Universe does not replay room transcripts or automatically inject Context Packs.
Vendor Session Rooms mirror one provider session, while Universe-owned rooms
fan out only unseen events to participant CLI sessions using independent
delivery and observation cursors.

---

## 2. Room types

### 2.1 Project room (Universe project chat)

| Field | Rule |
|-------|------|
| **Cardinality** | One per project |
| **Purpose** | Design, large-unit direction, user ↔ Master conversation |
| **Host / primary agent** | Project Master (LLM session) |
| **User** | Full participant — **direct instructions go here** (and to Master) |
| **Conductor** | May participate as user-facing coordination surface where product maps Conductor↔user |
| **Session push** | Master session attach (see `room-session-attach-streaming.md` as first slice) |

### 2.2 Boss room (node / Task Frame chat)

| Field | Rule |
|-------|------|
| **Cardinality** | One per work unit; **lifetime aligned to Task Frame** (preferred) |
| **Purpose** | Real implementation coordination; primary work chat |
| **Host (방장)** | **Boss** — fixed for the room |
| **Workers** | Report problems and progress **to the Boss** |
| **Project Master** | **Observer + attach for instruction/coordination** (parent of Boss); jumps across many Boss rooms |
| **User** | **Observe only** — no direct Boss/Worker instructions in this room |
| **Evidence** | Durable turns + boss-distilled bench/files with `evidence_refs` |

Hierarchy:

```text
Project Master (parent)
  ├── Task Frame 1 → Boss room (host=Boss) → Workers
  ├── Task Frame 2 → Boss room (host=Boss) → Workers
  └── Task Frame N → …
```

Escalation:

```text
Worker → Boss (problem report)
Boss → may call Master
Master → attach Boss room → re-instruct / re-scope
User instructions → Master (Project room), not Boss room write path
```

### 2.3 Meeting Room (collaboration and specification)

| Field | Rule |
|-------|------|
| **Cardinality** | Many concurrent; may be attached to a Feature Node, Task Frame, or ad-hoc inquiry |
| **Purpose** | Shared requests, research, coordination, escalation, decision support, and document co-authoring |
| **Host / facilitator** | **Conductor** by default; facilitation does not imply execution authority |
| **Participants** | User plus invited Conductor, Master, Boss, Worker, and model sessions |
| **User** | Full visible participant; may observe, intervene, redirect, request revision, or adopt a candidate outcome |
| **Interaction** | Free participant messaging and targeted requests first; round-robin or other automated discussion is optional |
| **Retrieval** | Invited research models may retrieve RAG evidence and return source-linked findings to the room |
| **Coordination** | Participants may expose cross-feature dependencies and request scope or authority escalation through the proper owner |
| **Documents** | Participants may co-author versioned proposal, specification, comparison, and decision-candidate artifacts |
| **Creation** | User, Conductor, or a governed workflow may open or attach a room without requiring a Task Frame |
| **Output** | Evidence-linked candidate artifacts; **no** execution authority from meeting participation or completion alone |
| **Handoff** | User-adopted outcomes may become Feature Node Expected Paths or enter the normal Master/Task Frame route |

Meeting Rooms provide the collaboration layer for Universe's core future-path
loop:

```text
User describes a capability
  -> Feature Node records the intent
       -> Meeting Room gathers sessions, evidence, and documents
            -> multiple detailed implementation specifications
                 -> Expected Path candidates on the feature graph
                      -> explicit user adoption
                           -> Goals and Todos for the adopted path
```

The detailed specifications are the predictions. They are alternative future
implementation paths, not automatically selected plans. Todos belong to the
adopted path and must not be used as a substitute for the Feature Node or the
pre-adoption candidate set.

### 2.4 Work progress dashboard (observation UI)

Not a chat room. Aggregates:

- Task / Boss-room state (waiting, in progress, blocked, done)
- Decision and blocker records
- Links into room messages / bench artifacts (`evidence_refs`)

Multiple rooms may be open while the dashboard stays a **status truth** surface
(prefer structured events over mining raw chat).

---

## 3. Common runtime requirements

1. **Many rooms concurrent** — each with independent membership and stream.
2. **Realtime streaming** — partial assistant text as transient UI; **final turns durable**.
3. **Status refresh** — room list + dashboard driven by structured state, not only chat parse.
4. **Session push/attach** — rooms without bound sessions are empty shells; create room + create/resume sessions + bind slots + wire stream together.
5. **Function-first UI** — minimal chrome (list + log + input + status); major SPA redesign later.

---

## 4. Session model

### 4.1 Supervisor remains the registry

- Identity: provider-neutral `(node, mode, session_id)` plus `provider_session_ref`.
- Observatory is one UX entry; it is **not** the only inject path.
- **External harness boot** may push `provider` + `session_ref` (+ project/room target) into Universe via loopback API or seed file so resume works without finding Observatory.

### 4.2 Attach semantics (generalized)

| Room | Who is room host | Who attaches / jumps |
|------|------------------|----------------------|
| Project | Master (conversation axis) | User always present; Master session bound |
| Boss | **Boss fixed** | Master attaches to instruct; User subscribe-only |
| Meeting | **Conductor host** | Models as slots; User interrupt channel |

Attach **never** creates Current Anchor, authority, Execution Assignment, or write
scope. It only binds **conversation/stream routing**.

### 4.3 Meaning vs timeline continuity

| Mechanism | Restores |
|-----------|----------|
| Provider **session ref** resume | Conversation **meaning** on provider side (when supported) |
| **RESUME_SAVE / load** | Bounded `compressed_context` (+ optional summary); **not** full transcript |
| **Durable room turns** | Scrollback timeline in Universe UI |
| **Post-resume bridge line** | Explicit “where we left off” in room chrome |

Product acceptance: connection + meaning is enough to continue work; **timeline
continuity** is improved by durable history + bridge, not by storing raw provider
transcripts in Resume.

---

## 5. Artifacts and evidence

Workers may accumulate in process/memory/journal. Boss **distills** and drops
**files / Bench-class artifacts**. Meeting rooms similarly drop summary candidates.

```text
Worker activity (memory / stream)
  → Boss room durable turns (dialogue evidence)
  → Boss distillation → file / Bench artifact
       + evidence_refs (room_id, message_ids, worker result ids)
  → Master / dashboard / user review
```

Bench already “drops” reviewable packages; Boss distillation is the same family of
pipeline, with Boss-room dialogue as the evidence inlet.

---

## 6. Streaming architecture (technical preference)

| Choice | Preference |
|--------|------------|
| Room unit for Boss | **Task Frame lifetime** (not orphan permanent node rooms) |
| Transport | **Per-room SSE hub** (generalize `ProjectRoomEventHub`) |
| Partial text | Transient stream events |
| Final turn | Durable room message |
| Mid-turn reconnect | Process-local buffer rehydrate into SNAPSHOT (must-have for polish slice) |
| Global single bus | Avoid for v1 (permission and filter complexity) |

---

## 7. Write permission matrix (product)

| Surface | User write | Master write | Boss write | Worker write |
|---------|------------|--------------|------------|--------------|
| Project room | Yes (to Master path) | Yes | N/A | N/A |
| Boss room | **No** (observe/subscribe) | Yes when **attached** | Yes (host) | Yes (to Boss) |
| Meeting room | **Yes** (interrupt) | Slot if invited | N/A | N/A |
| Dashboard | No (observe) | N/A | N/A | N/A |

User → Boss direct instruction API: **forbidden** in v1 product rules.

---

## 8. Implementation slices (function-first)

Order is incremental; each slice ships with **thin UI**.

| Slice | Deliverable |
|-------|-------------|
| **S0** | This architecture + write matrix locked in docs — **done** |
| **S1–S5 foundation** | Function-first implementation — see **`docs/multi-room-implementation-status.md`** |
| **S1** | Project-room Master attach + native incremental input/output mirror (detail: `room-session-attach-streaming.md`) — **Done for resident Codex/Claude/Grok Project Master** |
| **S2** | Session-ref inject API / seed + MODE_CHANGE·SessionStart hook — **Done** |
| **S3** | Meeting room Skill create + multi-slot + stream + user interrupt; imported Codex/Claude/Grok sessions use explicit process-local native controls and Room-scoped permission decisions; automated debate loop remains |
| **S4** | Boss room API + user read-only + Worker report — **API done; TF auto-wire later** |
| **S5** | Master attach into Boss + call-master — **API done; multi-Host jump later** |
| **S6** | Dashboard from structured TF/room state + evidence links |
| **S7** | Major UI redesign (after S1–S6 behave) |

**S1–S2** can proceed without full multi-room chrome. **S3** validates multi-session
push + multi-stream early. **S4–S5** complete the Master/Boss hierarchy.

---

## Feature Node and Expected Path first vertical slice

The durable planning order is now represented directly by the local runtime:

1. `POST /v1/projects/{project_id}/feature-nodes` records the Feature Node before any Goal or Todo.
2. A Feature Node may bind one existing `MEETING` room in the same project.
3. `POST /v1/feature-nodes/{feature_id}/expected-paths` pins a room `SPECIFICATION` artifact to its exact revision and content digest.
4. At least two candidate paths must exist before `POST /v1/feature-nodes/{feature_id}/adoptions` accepts an explicit USER selection.
5. Adoption marks one path `ADOPTED` and the remaining candidates `NOT_SELECTED`; it does not create a Goal, Todo, Task Frame, authority, or execution assignment.
6. A separate explicit USER `POST /v1/feature-nodes/{feature_id}/goals` action materializes exactly one idempotent `DESIGNING` project Goal. Its provenance pins the adoption, Expected Path, specification revision, and digest; its owner remains `UNASSIGNED`, and it creates no Todo, milestone, Task Frame, authority, or execution assignment.
7. `POST /v1/goals/{goal_id}/work-plan-runs` reuses the linked Meeting Room and at least two verified provider sessions to collect strict, bounded JSON Work Plan alternatives. Invalid model output is recorded as a candidate failure; durable store/provenance failures are not hidden as model failures.
8. Candidate generation creates no Milestone or Todo. `POST /v1/goals/{goal_id}/work-plan-adoptions` requires an explicit USER rationale and at least two alternatives, then marks exactly one plan `ADOPTED`.
9. A separate explicit USER `POST /v1/goals/{goal_id}/work-plan-applications` atomically creates deterministic `PLANNED` Milestones and `BACKLOG` Todos. Replay is idempotent and creates no duplicates.
10. Planning never creates a Task Frame, authority, execution assignment, or `READY`/`IN_PROGRESS` work. Those remain later governed transitions.
11. An applied Work Plan may enter the existing Project Master handoff route as `GOAL_WORK_PLAN`. The durable handoff pins the Goal, USER adoption, USER application, applied plan, and exact generated Todo coordinates before delivery. Delivery still creates no Task Frame, authority, assignment, or Todo state transition; those are the next governed execution-loop boundary.
12. A delivered Goal handoff exposes one immutable `instruction_ref`. `POST /v1/projects/{project_id}/master-handoffs/{handoff_id}/instruction-task-frame` accepts only a Project Master proposal whose `request_ref`, proposal digest, Project, and handoff digest match that receipt. On a matching instruction-authorized Host receipt, Universe records a durable one-to-one handoff/proposal/Task Frame binding. Replay is idempotent; mismatched lineage fails closed. The transition creates but does not run the Task Frame and does not change Todo state.
13. `GET /v1/goals/{goal_id}/automation` projects the deterministic Conductor stop state from those durable records. `POST /v1/goals/{goal_id}/automation/advance` requires an explicit `ADVANCE` instruction, checks the Goal revision, idempotently creates/delivers the Master handoff, waits without guessing when no exact proposal exists, and binds a caller-supplied Task Frame only after exact lineage matching. It never runs the frame or changes Todo state. Repeated calls are the initial bounded Conductor loop; a background scheduler may call the same route later without acquiring broader powers.
14. A bound frame still cannot mutate every Work Plan Todo as one undifferentiated result. `POST /v1/goals/{goal_id}/automation/todo-selection` requires the current supervised session and an explicit `SELECT_TODOS` action, then seals one immutable Goal/application/handoff/Task Frame/Todo selection before receipt-backed `STARTED` transitions. `POST /v1/goals/{goal_id}/automation/todo-results` accepts only an already attached result from that exact frame, reads its `todo_actions` payload, rejects duplicate or unselected Todo coordinates, and applies each action through the existing one-time Todo action receipt. `COMPLETED` still requires `validation.status=PASSED`; a partial result changes only the Todos explicitly proven in that result. The automation surface exposes eligible Todos, the selection, result summaries, and consumed action receipts without exposing full Task Frame result bodies.

Read routes are `GET /v1/projects/{project_id}/feature-nodes`, `GET /v1/feature-nodes/{feature_id}`, and `GET /v1/goals/{goal_id}/work-plans`. The semantic project graph projects `FEATURE_NODE`, `EXPECTED_PATH`, `GOAL_WORK_PLAN`, adoption and provenance edges without embedding specification or full Work Plan bodies.

## 9. Non-goals (program-level)

- Replacing Career governance or inventing authority from chat/attach.
- Full vendor UI mirror or hidden reasoning exposure.
- Treating meeting/Boss chat completion as write permission.
- Automatic Host start on project graph select alone (unless product later changes).
- Making Session Observatory the only session entry (ref inject is first-class).

---

## 10. Open product notes (non-blocking)

1. Conductor vs “user session” labeling in Project room chrome (same human operator, two product names).
2. Whether Meeting host is always Universe Conductor Mode session or any operator-designated facilitator slot.
3. Soft-delete vs archive policy for closed Boss rooms after Task Frame completion.

---

## 11. References

- `docs/room-session-attach-streaming.md` — Project room Master attach + SSE rehydrate (implementation design).
- `docs/local-session-supervisor.md` — session registry, not transcript store.
- `docs/local-universe-service.md` — Project Room, Bridge, SSE contracts.
- `docs/universe-design-and-bench-flow.md` — Bench drop / design handoff family.
- `.ai/skills/common/resume-save/SKILL.md`, `resume-restore/SKILL.md` — compressed context, not full chat.
