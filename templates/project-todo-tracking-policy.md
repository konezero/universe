# Todo tracking policy (project plant)

Schema note: `universe.todo-tracking-policy.v1`  
**Authoritative template:** ai-career  
`ai-career/.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md`  
Universe product note: `docs/universe-todo-tracking-policy.md`  
This file is a **mirror** for the Universe repo only — promote via Career.

## Rule

| Surface | Role |
|---------|------|
| **Universe Todo DB** (`project_todo`) | **Only** live work queue — state, priority, scope |
| **docs / contracts** | Reference only — link from `todo.detail` |
| **Markdown backlog / worklist / next_actions** | **Not** live tracking. Do not status-bump for the agent |

### Do

- Open / progress / close work via Universe UI Todo board or ` /v1/todos`
- Put doc paths in todo `detail` (e.g. `docs/….md`)
- Commit docs only when the **document content** changes

### Do not

- Treat worklist/backlog markdown as the task list
- Commit “status only” updates to tracking markdown
- Duplicate the same queue in git and DB

### When host is READY

Always prefer Universe Todos for this `project_id` over any local markdown list.

### When host is down (standalone only)

If `install_mode=PROJECT_STANDALONE` and no host, temporary local notes are
allowed until host attach; then migrate open items into Todo and stop
using markdown as the queue.
