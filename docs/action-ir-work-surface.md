# Action IR work-surface — scope decision

Status: DECIDED 2026-09-07 (Codex MASTER review `msg_108348ba950aa385`,
master-messages `master_msg_639d0cff` slices 1-2, `master_msg_742041f6` slice 3).

## What is on `POST /v1/actions`

The typed Action front door carries **create / full-replace** work-surface
mutations only:

| Action          | wraps                              | notes |
|-----------------|------------------------------------|-------|
| `feature.create`| `UniverseStore.create_feature_node`| server injects `created_by_role=USER` |
| `todo.create`   | `UniverseStore.create_todo`        | |
| `todo.update`   | `UniverseStore.update_todo`        | plain-PATCH fields, revision-guarded (explicit `409`); **`state` is not accepted** |

`ActionContract` still enforces `actor_context_resolution=SERVER_SIDE`, the
forbidden caller-context fields, and `credential_handling=CREDENTIAL_REF_ONLY`
(inline secrets rejected, only an opaque `credential_ref` passes). The typed
Action is a front door — it does not replace Execution Guard, Task Proposal,
Commander approval, or the guarded/receipt-bound Store mutations underneath.

Legacy `POST /v1/todos`, `PATCH /v1/todos/{id}`, and
`POST /v1/projects/{id}/feature-nodes` are unchanged and remain first-class.

## What is NOT on `/v1/actions`

### Lifecycle state transitions
`todo.state` (and any DONE/BLOCKED/… move) route **only** through the
anchor-aware receipt gateway: `POST /v1/todo-action-mutation-receipts`
(`normalize_todo_action_mutation_request`) → `.../consume`, or the
`POST /v1/todos/{id}/actions` surface. Those require a Session-Anchor-bound
caller identity (`provider` / `provider_session_ref` / `session_id` /
`session_anchor_ref` / `instruction_ref`), which `ActionContract` deliberately
forbids callers from supplying. `todo.update` on `/v1/actions` rejects a `state`
that differs from the current Todo (`ACTION_TODO_LIFECYCLE_VIA_RECEIPT`) and
pins `state` to current otherwise.

**Conductor adapter / runtime**: use the receipt gateway for lifecycle
transitions. Do not fall back to generic `/v1/actions`.

### Fine-grained partial Actions
`todo.priority`, `todo.reorder`, `todo.bind_node`, `todo.bind_goal`,
`todo.move_project` are **not implemented**. `todo.update` (full-replace +
revision guard) covers them for any caller that can `GET` the current Todo,
so separate Actions are not yet justified. `TODO_ACTION_IDS` keeps the full
vocabulary; only `IMPLEMENTED_WORK_SURFACE_ACTION_IDS` are registered as
discoverable contracts — the pending ones report `UNCOVERED`, never available.

## Future work (not implemented)

- **(d) Action-driven lifecycle**: if needed, do not add `session_ref` to the
  Action request. The server should resolve `session_id` / anchor / provider
  from the authenticated Action transport credential/connection and inject
  them into the existing prepare/consume flow. Without that binding, callers
  use the legacy receipt route.
- **`todo.archive` / `todo.restore` / `todo.delete`**: no dedicated Store
  methods exist. Settle the data model, restore semantics, revision, and audit
  trail first, then a separate slice.
- **Action-only adapter**: an adapter that can only `invoke_action` (no Todo
  read/get Action, no `GET`) cannot obtain the `current + revision` that
  full-replace `todo.update` needs. That is when a partial + atomic
  `todo.update` variant becomes justified.
