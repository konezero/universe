# Todo tracking policy (project plant)

Schema note: `universe.todo-tracking-policy.v1`  
**Authoritative template:** ai-career  
`ai-career/.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md`  
Universe product note: `docs/universe-todo-tracking-policy.md`  
This file is a **mirror** for the Universe repo only — promote via Career.

## Scope (important)

**Universe Todo is for Universe** — the host’s operational / execution queue
for this attached project. It is **not** a replacement for the project’s own
work board, issue tracker, or product backlog.

| Surface | Role |
|---------|------|
| **Universe `project_todo` (DB)** | Host-side queue: ops, attach, Master/dispatch handoffs, multi-project work Universe will drive or observe |
| **Project-local board** (issues, next_actions, product backlog, …) | **Remains valid** for product engineering work owned inside the repo |
| **docs / contracts** | Specs and design — reference from either side; not a live status board for host execution |

## Rule for Universe Todo

### Do (Universe / host operators and agents working *through* Universe)

- Put **host-facing** open work in Todo (`POST/PATCH /v1/todos` or UI board)
- Scope to the attached `project_id` when the work is about that project
- Link design docs in `todo.detail` (paths only; no status tables in markdown)
- Prefer READY → execute path (dispatch / Master / TF) when wiring exists; until then Todo is still the **host queue**, not the product backlog

### Do not

- Force every product task into Universe Todo
- Delete or forbid the project’s own work board “because Todo exists”
- Treat project `next_actions.md` / GitHub Issues as wrong — they serve **product** work
- Commit markdown only to bump host-queue status (use Todo DB for that)

### When host is READY

Universe-driven work for this project → **Todo board**.  
Product-only work → **project-local board** (as the project already does).

### When host is down (standalone)

No host queue. Use project-local practices only until Universe attach.

## Plant target

```text
.ai/universe/TODO_TRACKING_POLICY.md
```
