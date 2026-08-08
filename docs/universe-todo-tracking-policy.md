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
| **Universe** | Project-integration template SSOT: Todo policy, Project binding, and attach guidance |
| **Career** | Runtime Release DB payload and lifecycle contract producer; not the project-integration template owner |
| **Promotion / install** | Universe install flow plants local `.ai/universe/TODO_TRACKING_POLICY.md` from the selected release and project template |
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

Canonical Universe source:

```text
templates/project-integration/TODO_TRACKING_POLICY.md
```

`GET /v1/project-templates` exposes the digest-bound catalog. For a registered
Project, `GET /v1/projects/<project_id>/integration-template-proposal` exposes
the exact local plant before an install starts.

Career may retain `.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md`
as a compatibility installation mirror until the Universe Project Lifecycle
Host materializes this catalog directly. Career does not independently own or
author the policy.

## Agent load hint

When working **through Universe** on host-facing work:

1. Universe Todos for this `project_id`
2. This policy plant
3. Linked docs from `detail`

When doing **product** work inside the repo without host execution intent:
use the **project’s own** board; do not invent a second full backlog in Todo.

## Acceptance

- [ ] Policy plant proposed by the Universe integration catalog before install
- [ ] Policy plant materialized by the Universe Project Lifecycle Host
- [ ] Wording: Todo = Universe host queue, not product board replacement
- [ ] Host-facing open work can live in `project_todo`
- [ ] Project-local boards remain allowed for product work
