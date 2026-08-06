# Todo tracking policy (cross-project, fixed)

Status: **FIXED** product rule for every Universe-attached project  
Plant path on each project: `.ai/universe/TODO_TRACKING_POLICY.md`  
Related: `docs/universe-install-mode.md`, `docs/universe-worklist-merged.md`

## Distribution (important)

**Do not** hand-copy this rule into every repo from the Universe app tree.

| Layer | Role |
|-------|------|
| **ai-career (source)** | Template + boot guidance. Canonical plant: `.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md`. START_HERE template points agents at Todo DB, not `docs/TODO.md`. |
| **Promotion / install** | OS_INSTALL, project seed materialize, or attach package **from Career** writes `.ai/universe/TODO_TRACKING_POLICY.md` on the project |
| **Universe host** | Runs Todo API/UI; does not own the policy law file as multi-repo SSOT |
| **Project** | Carries the **installed** plant only |

Ad-hoc plants under GCS/career/rendezvous from a Universe session are
**interim** until the next Career-sourced install/update overwrites or
confirms them.

---

## Problem this fixes

Markdown backlogs / worklists / “next actions” files become **tracking
surfaces**. Status changes with **no real work** still produce git commits.
Those files then look like progress and pollute history.

## Rule (all attached projects)

| Surface | Role | Git |
|---------|------|-----|
| **Universe `project_todo` (DB)** | **Only** open-work queue (state, priority, scope) | Not in git |
| **docs / design / contracts** | Explanation, specs, acceptance — **reference only** | Commit when content changes |
| **Markdown backlog / worklist / next_actions** | **Not** a live tracker. Archive or index at most | Do not bump status-only |

### Operators and agents must

1. Create / update / close work in **Todo** (`POST/PATCH /v1/todos` or UI board).
2. Put **document paths** in `todo.detail` (until a formal `document_ref` exists).  
   Example: `docs/universe-install-mode.md`, `docs/foo.md`.
3. **Not** maintain parallel live status tables in markdown “for the agent.”
4. **Not** commit a doc solely to flip BACKLOG → READY style rows.

### Allowed markdown

- Design that actually changed
- ADRs, contracts, install mode text
- One-line index: “open work = Universe Todo for project_id X”

### Forbidden as live trackers

- `BACKLOG.md` / `TODO.md` status boards as source of truth  
- `docs/*-worklist*.md` status grade tables as daily queue  
- `.ai/projects/*/next_actions.md` as the agent’s task list (use Todo; link doc)

## Scope

Applies when:

- `install_mode` is `UNIVERSE_ATTACHED` (default), or  
- project is registered on a Universe host, or  
- agent is working under Universe attach (PWD = project_root)

Standalone (`PROJECT_STANDALONE`) without a host may keep a **local** queue only
if no Todo API is reachable; prefer host Todo when host is READY
(`prefer_boot: HOST`).

## Planting

Every project that Universe attaches should carry:

```text
.ai/universe/TODO_TRACKING_POLICY.md
```

**Source template (Career):**

```text
ai-career/.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md
```

Install / attach / seed path **must** plant from Career (or a Release that
embeds that template), not from a one-off Universe workspace copy.

Universe repo keeps `templates/project-todo-tracking-policy.md` only as a
**mirror for docs/tests**; Career remains authoritative for promotion.

## Agent load hint

Not part of core boot order by default. When the task is “what’s open work?”
or planning delivery, agents **should** read:

1. Universe Todos for this `project_id` (API / board)  
2. This policy file  
3. Linked docs from todo details only as needed  

They must **not** invent work from stale worklist markdown.

## Acceptance

- [ ] Policy file present on each attached project under `.ai/universe/`
- [ ] Open product work for that project exists as `project_todo` rows
- [ ] Design docs referenced from `detail`, not duplicated as status boards
- [ ] No routine commits that only rewrite backlog status tables
