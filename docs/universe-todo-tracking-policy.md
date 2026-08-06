# Todo tracking policy (Universe host queue — fixed)

Status: **FIXED** (wording v2)  
Plant path on each project: `.ai/universe/TODO_TRACKING_POLICY.md`  
Related: `docs/universe-install-mode.md`, `docs/universe-worklist-merged.md`

## What Todo is

**Universe Todo (`project_todo`) is for Universe** — the host’s
**operational / execution queue** about an attached project.

It is **not**:

- a replacement for the project’s own work board / issues / product backlog  
- the only place “any” engineering work may live  

## Two queues (do not merge ownership)

| Surface | Owner | Role |
|---------|--------|------|
| **Universe Todo DB** | Universe host | Host-facing work: ops, multi-project, Master/dispatch/TF handoffs, install_mode, E2E, attach-scoped execution the host will drive |
| **Project-local board** | Project repo / team | Product engineering work (features, bugs, refactors) as the project already tracks them |
| **docs / contracts** | Git | Specs — **reference** from either queue; not a live status tracker for host execution |

Link across queues is optional (`todo.detail` ↔ issue id). Ownership stays split.

## Distribution

**Do not** hand-copy from the Universe app tree into every repo.

| Layer | Role |
|-------|------|
| **ai-career** | Template SSOT: `.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md` |
| **Promotion / install** | OS_INSTALL / distribution pack plants `.ai/universe/TODO_TRACKING_POLICY.md` |
| **Universe host** | Todo API/UI; execution wiring (TODO → dispatch/TF) may grow later |
| **Project** | Installed plant + **keeps its own product board** |

## Rule (host / Universe operators)

### Do

1. Put **Universe-driven** open work in Todo (`/v1/todos` or UI).
2. Scope with `project_id` when the work is about that attach.
3. Put **document paths** in `todo.detail` (reference only).
4. Treat READY items as the host queue head (execution handoff when implemented).

### Do not

1. Require every product task to appear as a Universe Todo.
2. Ban project `next_actions`, GitHub Issues, or internal boards.
3. Commit markdown only to flip host-queue status (use Todo DB).
4. Confuse product backlog with host execution queue.

## Standalone

Without a READY host, there is no Universe queue — project-local practices only
until attach (`prefer_boot: HOST` when host returns).

## Planting

```text
.ai/universe/TODO_TRACKING_POLICY.md
```

Career source:

```text
.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md
```

Registered in Career `CORE_SURFACE_REGISTRY` and
`project_runtime_source_index` for distribution packs.

Universe repo `templates/project-todo-tracking-policy.md` is a **mirror** only.

## Agent load hint

When working **through Universe** on host-facing work:

1. Universe Todos for this `project_id`
2. This policy plant
3. Linked docs from `detail`

When doing **product** work inside the repo without host execution intent:
use the **project’s own** board; do not invent a second full backlog in Todo.

## Acceptance

- [ ] Policy plant present via Career install/update
- [ ] Wording: Todo = Universe host queue, not product board replacement
- [ ] Host-facing open work can live in `project_todo`
- [ ] Project-local boards remain allowed for product work
