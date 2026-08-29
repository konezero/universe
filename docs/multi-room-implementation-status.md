# Multi-Room Implementation Status (S1–S5)

| Field | Value |
|-------|--------|
| **Status** | Function-first foundation plus bounded meeting coordinator landed |
| **Date** | 2026-08-10 |
| **Product architecture** | `docs/multi-room-chat-architecture.md` |
| **S1 design detail** | `docs/room-session-attach-streaming.md` |
| **Purpose of this doc** | Inventory of **what is implemented now** for a later major UI redesign |

> UI is intentionally thin (Settings → “Multi-room”). Redesign should **consume these APIs and rules**, not reinvent room types or write matrices.

---

## 1. Code map

| Path | Role |
|------|------|
| `tools/universe_multi_room.py` | Room store, write matrix, attach/inject, meeting/boss helpers, SSE hub |
| `tools/universe_server.py` | HTTP routes; `prepare_session_ref_inject`; CLI `inject-session` |
| `tools/universe_session_inject_hook.py` | Best-effort MODE_CHANGE / SessionStart → inject (offline-safe) |
| `tools/universe_ui/index.html` + `app.js` | Settings multi-room panel (list/open/post/inject/call-master) |
| `.claude/settings.json` | Claude `SessionStart` → inject hook |
| `.ai/skills/common/meeting-room-create/SKILL.md` | Skill entry for meeting create |
| `.ai/skills/common/session-ref-inject/SKILL.md` | Inject + hook contract |
| `.ai/skills/common/mode-change/SKILL.md` | Post-MODE_CHANGE inject step |
| `tests/test_multi_room.py` | Unit tests for store rules and meeting coordination |
| `tests/test_session_inject_hook.py` | Hook resolve / dry-run / offline / inject |

Database: **same Universe SQLite** (`universe.sqlite3` / service DB). Tables:

- `chat_room`
- `chat_room_session` (slot bindings)
- `chat_room_message` (durable turns)
- `chat_room_control_event` (`CALL_MASTER`, `WORKER_REPORT`, …)

---

## 2. Slice checklist

### S1 — Project room Master attach + stream + bridge

| Item | Status |
|------|--------|
| PROJECT room ensure on prepare | **Done** (`ensure_project_room` + prepare hooks) |
| Attach MASTER slot with provider/session_ref | **Done** (`POST .../attach`, prepare auto-attach when connection known) |
| `bridge_line` on snapshot / attach / prepare | **Done** |
| Durable multi-room messages | **Done** |
| Legacy Project Room (`project_room_message`) + MASTER_STREAM | **Still primary** for existing Project Master dock; multi-room runs **alongside** |
| Full Host session_ref invalidate (silent REUSE fix) | **Not fully ported** from design PR2 — attach is stored; deep Host switch still design follow-up |
| Mid-turn `active_master_stream` rehydrate | **Not in multi-room hub yet** (still S1 design PR4 for classic project room) |

### S2 — Session-ref inject + continuity bridge

| Item | Status |
|------|--------|
| `POST /v1/sessions/inject` | **Done** — Supervisor register + optional default + room attach |
| Stable `session_id` hash (node, mode, provider, ref) | **Done** (`supervisor_session_id_for`) |
| CLI `inject-session` (+ optional `--seed-file`) | **Done** |
| Auto-create PROJECT room if only `project_id` given | **Done** |
| bridge_line explains meaning vs timeline | **Done** |
| Thin UI inject form (Settings multi-room panel) | **Done** |
| Skill `session-ref-inject` | **Done** |
| Hook `tools/universe_session_inject_hook.py` | **Done** — best-effort MODE_CHANGE / SessionStart |
| Claude `.claude/settings.json` SessionStart | **Done** (project hook → inject hook) |
| External harness auto-post | **Done (best-effort)** — needs provider+ref resolve; Universe offline → `OFFLINE` no block |

### S3 — Meeting room

| Item | Status |
|------|--------|
| `POST /v1/rooms` with `room_type=MEETING` | **Done** |
| Conductor host + MODEL slots | **Done** |
| User write allowed | **Done** (write matrix) |
| Skill `meeting-room-create` | **Done** |
| Bounded round-robin coordination core | **Done** (`MultiRoomMeetingCoordinator`) |
| Delta-only provider input | **Done**; full transcript forwarding is forbidden |
| Turn-boundary cancellation and per-room single flight | **Done** |
| Durable meeting summary | **Done** (`MEETING_SUMMARY` control event) |
| Live provider completion adapter / HTTP Feature run endpoint | **Done** — verified opaque provider sessions feed the bounded coordinator; completed per-model outputs become revision-pinned SPECIFICATION / Expected Path candidates |
| Explicit adopted-path Goal materialization | **Done** — USER action creates one idempotent DESIGNING Goal with revision/digest provenance; no Todo, milestone, Task Frame, authority, or assignment |
| Goal Work Plan alternatives | **Done** — linked Meeting Room + at least two verified provider sessions produce bounded structured candidates with message/binding/run provenance |
| Explicit Work Plan adoption and apply | **Done** — USER adoption is separate from atomic idempotent application; application creates only PLANNED Milestones and BACKLOG Todos, never Task Frame, authority, assignment, READY, or IN_PROGRESS state |
| Thin Meeting Room planning UI + semantic graph | **Done** — generate/adopt/apply controls and redacted `GOAL_WORK_PLAN` projection are visible without embedding full plan bodies |
| Independent fresh meeting reviewers | **Done** — the Session Broker creates provider sessions with `session_action=NEW`; bindings record `lifecycle_owner=MEETING` and bypass the reusable Provider Chat catalog only for that exact broker-owned identity |
| Meeting close/archive lifecycle | **Done** — closing archives only meeting-owned fresh sessions, detaches active room bindings, keeps durable room history, and never terminates reused Master/Conductor sessions |
| Catalog-lag attachment recovery | **Done** — an `INDEPENDENT` catalog projection may resolve only when one exact Supervisor provider/session record supplies a non-empty Session Anchor; ambiguous or unanchored matches still fail closed |

### S4 — Boss room

| Item | Status |
|------|--------|
| `create_boss_room(project_id, task_frame_id)` | **Done** (idempotent per OPEN task_frame) |
| Host role BOSS | **Done** |
| USER write forbidden | **Done** (`ROOM_WRITE_FORBIDDEN`) |
| Worker report API | **Done** (`POST .../worker-report`) |
| Auto-create on real Task Frame lifecycle | **Not wired** into TF engine yet — call API when TF opens |
| Boss provider process auto-start | **Not done** |

### S5 — Master multi-boss attach + call master

| Item | Status |
|------|--------|
| Master slot attach into BOSS room | **Done** (`attach` + `call_master` auto_attach) |
| `POST .../call-master` control event | **Done** |
| Multi-room concurrent OPEN rooms | **Done** (list/filter) |
| Master process jump / multi-Host routing | **Not done** — binding only |
| Dashboard | **Not in S1–S5** (S6) |

---

## 3. HTTP API (function-first)

Base: Universe local service (`http://127.0.0.1:<port>`).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/v1/rooms?project_id=&room_type=` | List OPEN rooms |
| POST | `/v1/rooms` | Create PROJECT / BOSS / MEETING |
| GET | `/v1/rooms/{room_id}` | Snapshot: room, bindings, messages, bridge_line, write_roles |
| GET | `/v1/rooms/{room_id}/messages` | Durable messages |
| GET | `/v1/rooms/{room_id}/stream` | SSE (`snapshot`, `room`, `ping`) |
| POST | `/v1/rooms/{room_id}/messages` | Post message (`author_role`, `body_text`) |
| POST | `/v1/rooms/{room_id}/attach` | Push session into slot |
| POST | `/v1/rooms/{room_id}/provider-sessions` | Attach one reusable, Anchor-verified Provider Chat session as a meeting model |
| POST | `/v1/rooms/{room_id}/fresh-provider-sessions` | Create independent meeting-owned reviewer sessions through the Session Broker |
| POST | `/v1/rooms/{room_id}/call-master` | Boss→Master call event |
| POST | `/v1/rooms/{room_id}/worker-report` | Worker→Boss report |
| POST | `/v1/rooms/{room_id}/close` | Archive meeting-owned fresh sessions, detach bindings, and close the room |
| POST | `/v1/sessions/inject` | Harness session-ref inject |
| POST | `/v1/projects/ensure-room` | Ensure PROJECT multi-room |
| POST | `/v1/projects/{id}/master-session/prepare` | Also ensures PROJECT multi-room + bridge_line |

### Create meeting body

```json
{
  "room_type": "MEETING",
  "title": "API debate",
  "topic": "...",
  "project_id": "optional",
  "models": [
    {"provider": "GROK", "display_name": "Grok", "session_ref": "..."}
  ]
}
```

### Create boss body

```json
{
  "room_type": "BOSS",
  "project_id": "proj_x",
  "task_frame_id": "tf_y",
  "title": "optional",
  "boss_session": {
    "provider": "CLAUDE",
    "provider_session_ref": "..."
  }
}
```

### Inject body

```json
{
  "project_id": "proj_x",
  "room_type": "PROJECT",
  "slot_role": "MASTER",
  "provider": "CLAUDE",
  "session_ref": "session-or-thread-id",
  "make_default": true
}
```

Server path:

1. Register Supervisor session (`session_` + sha256 of node/mode/provider/ref)
2. `set_default` when `make_default` (default true for PROJECT+MASTER)
3. Multi-room slot attach with `supervisor_session_id`

CLI:

```powershell
python tools/universe_server.py inject-session `
  --project-id proj_x --provider CODEX --provider-session-ref <thread>
```

---

## 4. Write matrix (enforced in store)

| Room | USER | CONDUCTOR | MASTER | BOSS | WORKER | MODEL |
|------|------|-----------|--------|------|--------|-------|
| PROJECT | yes | yes | yes | — | — | — |
| BOSS | **no** | — | yes | yes | yes | — |
| MEETING | yes | yes | yes | — | — | yes |

User direct instruction to Boss room → `403 ROOM_WRITE_FORBIDDEN`.

---

## 5. Product rules encoded

1. Boss room host = BOSS; Master attaches for coordination.
2. User observes Boss rooms; instructs via Project Master path.
3. Worker reports go to Boss room (`worker-report`).
4. Boss may `call-master` (control event + optional Master slot).
5. Meeting: Conductor host; user interrupt allowed; no authority from room alone.
6. Attach responses include `authority: UNASSIGNED`, `execution_assignment: UNASSIGNED`.
7. Timeline continuity: durable `chat_room_message` + `bridge_line` (not full provider transcript).

---

## 6. UI redesign notes (do not lose)

When redesigning SPA:

1. **Do not** bury rooms only in Settings long-term — rooms are primary surfaces (architecture §2).
2. Keep **three room types** and write matrix; chrome can change, contracts should not silently.
3. Session Observatory is optional entry; **inject + attach APIs** are required for harness boot.
4. Per-room SSE already exists at `/v1/rooms/{id}/stream` — multi-dock should open multiple EventSources.
5. Classic Project Master dock (`/v1/projects/.../room/*`) remains until fully migrated; multi-room is parallel foundation.
6. Feature gaps for redesign backlog: Host session_ref hard-switch, live native
   completion adapter for the bounded meeting coordinator, TF auto boss-room,
   and dashboard (S6).

---

## 7. Manual smoke

```powershell
# With Universe serve running and token from server.json:
# POST /v1/rooms meeting, GET /v1/rooms, POST attach/inject, POST messages,
# POST boss room + worker-report + call-master
```

```powershell
python -m unittest tests.test_multi_room tests.test_session_inject_hook -v

# Hook dry-run (no Universe required):
python tools/universe_session_inject_hook.py --repo-root . --provider CODEX --session-ref demo --dry-run --trigger mode_change
```

---

## 8. Related

- Product: `docs/multi-room-chat-architecture.md`
- S1 deep design: `docs/room-session-attach-streaming.md`
- Skill: `.ai/skills/common/meeting-room-create/SKILL.md`
- Skill: `.ai/skills/common/session-ref-inject/SKILL.md`
