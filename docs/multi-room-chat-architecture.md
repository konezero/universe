# Multi-Room Chat Architecture (Product)

| Field | Value |
|-------|--------|
| **Status** | Draft product architecture |
| **Date** | 2026-08-05 |
| **Scope** | Universe Instance chat rooms, session attach, streaming, dashboard |
| **UI strategy** | Function-first thin chrome first; major visual redesign later |
| **Related** | `docs/room-session-attach-streaming.md`, `docs/local-universe-service.md`, `docs/local-session-supervisor.md`, `docs/universe-design-and-bench-flow.md` |

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

A separate **Meeting room** type lets the Conductor host multi-model debate,
created via Skill, with the user able to interrupt mid-meeting. Meeting output is
summary/candidate material (bench-class), not automatic execution authority.

Session continuity: **provider session ref** + optional **Resume compressed
context** restore *meaning*. Full transcript replay is not required for work, but
**timeline continuity** is improved by durable room history, post-resume bridge
lines, and session-ref inject from other harnesses without relying on Session
Observatory alone.

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

### 2.3 Meeting room (multi-model debate)

| Field | Rule |
|-------|------|
| **Cardinality** | Many concurrent |
| **Purpose** | Mix models for discussion; not the default TF execution path |
| **Host (주최자)** | **Conductor** |
| **Participants** | Multiple model slots + optional others |
| **User** | **May intervene mid-meeting** |
| **Creation** | **Skill-driven** (e.g. `meeting-room.create`) preferred over Master-only TF factory |
| **Output** | Summary / decision **candidate** artifacts (bench-class); **no** Execution Guard authority from meeting alone |
| **Handoff** | Adopted outcomes may open Project-room direction or Task Frames via normal Master path |

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
| **S1** | Project-room Master attach + stream mirror (detail: `room-session-attach-streaming.md`) — **partial/done foundation** |
| **S2** | Session-ref inject API / seed + MODE_CHANGE·SessionStart hook — **Done** |
| **S3** | Meeting room Skill create + multi-slot + stream + user interrupt — **room/slots done; live debate loop later** |
| **S4** | Boss room API + user read-only + Worker report — **API done; TF auto-wire later** |
| **S5** | Master attach into Boss + call-master — **API done; multi-Host jump later** |
| **S6** | Dashboard from structured TF/room state + evidence links |
| **S7** | Major UI redesign (after S1–S6 behave) |

**S1–S2** can proceed without full multi-room chrome. **S3** validates multi-session
push + multi-stream early. **S4–S5** complete the Master/Boss hierarchy.

---

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
