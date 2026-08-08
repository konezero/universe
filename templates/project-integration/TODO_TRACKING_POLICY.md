# Todo Tracking Policy

Schema: `universe.todo-tracking-policy.v1`
Owner: Universe project-integration template catalog

## Boundary

Universe Todo is the host queue for attach, lifecycle, multi-project, and
Universe-driven operational work. It does not replace a Project's issue
tracker, product backlog, or engineering board.

| Surface | Owner | Role |
|---|---|---|
| Universe `project_todo` | Universe | Host-facing operational work |
| Project board | Project | Product engineering work |
| Linked docs | Git | Reference and specification, not live Todo state |

## Rules

- A follow-up, Memory Sync record, or conversation may create a reviewable
  `TODO_DRAFT`; it must not create a durable Todo without user confirmation.
- Scope host work to the relevant `project_id` or `node_ref`.
- Keep document references in `todo.detail` rather than duplicating status in
  Markdown.
- A standalone Project uses its own board while no Universe host is attached.

## Plant target

```text
.ai/universe/TODO_TRACKING_POLICY.md
```

The plant is local Runtime state. The template source is Universe; the
installed Runtime payload is supplied through the selected Career Release DB.
