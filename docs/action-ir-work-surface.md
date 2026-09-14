# Action IR work surface

Status: source contract updated 2026-09-14. Discover the running server's actual
coverage with `GET /v1/actions` or `python tools/universe_server.py action --catalog`.
A source declaration alone does not prove that an older running server supports it.

## Shared human and LLM commands

[Shared authoring](universe-design-and-bench-flow.md#shared-human-and-llm-authoring)
requires human and LLM clients to use the same objects, validation and revision
rules. The Todo work-map UI now uses `todo.create`, `todo.update`, and `todo.state`.
The native CLI sends the same Action envelope through the existing HTTP transport.
Project/plan drafts and complete form synchronization still have separate coverage;
this Todo slice does not mark the entire UI requirement complete.

| Action | Input and behavior |
| --- | --- |
| `feature.create` | Create a feature node; server assigns the USER actor. |
| `todo.create` | Create a Todo using the existing Store contract. |
| `todo.update` | Full metadata replacement with revision checking. An unchanged `state` may be supplied; changing state is rejected. |
| `todo.read` | `{ "todo_id": "..." }`; return the current Todo including its revision. |
| `todo.list` | Required `project_id` (null selects Universe scope); optional `node_ref`, `include_done` (false), `offset` (0), and `limit` (100, maximum 200). Return matching rows, total and pagination. |
| `todo.state` | Apply a revision-checked operator command with durable request replay and completion evidence. |

`todo.read/list/state` expose their exact JSON input schemas in each catalog
contract's `metadata.request_schema`. Unknown fields are rejected. Errors retain
stable `error_code`, detail and HTTP status. Common caller-owned actor, context,
session and credential fields remain forbidden by the Action registry.

## Todo lifecycle command

The native entry accepts a UTF-8 JSON file, including a BOM:

```text
python tools/universe_server.py action --catalog
python tools/universe_server.py action --request-file C:/Temp/todo-state.json
```

The endpoint and service credential come from the existing local server-state
file. Use `--state-file` for a different host profile; never put a secret in the
request file or command arguments. Read with `todo.read` before constructing a
new state command:

```json
{
  "action_id": "todo.state",
  "request": {
    "todo_id": "<observed Todo id>",
    "project_id": "universe",
    "expected_revision": 3,
    "request_id": "<unique stable request id>",
    "state": "DONE",
    "validation": {
      "status": "PASSED",
      "evidence_ref": "<actual validation record or result reference>"
    }
  }
}
```

- Valid states are BACKLOG, READY, IN_PROGRESS, BLOCKED and DONE. DONE requires
  PASSED validation and a nonempty evidence reference. The caller verifies that
  evidence; the server validates and records the supplied attestation.
- `project_id` must exactly match the current Todo scope; use null for a Universe
  Todo. `expected_revision` is an integer of at least 1.
- Request IDs contain 8–100 ASCII letters, digits, underscores or hyphens. Retry
  an uncertain request with its original ID **and complete original input**.
- A single SQLite transaction checks request identity, scope and revision, then
  writes the state, immutable result and project history event. A failed ledger
  or event write rolls back the state as well. Replaying identical input returns
  its original result with `replayed: true`, including after server restart.
- Reusing an ID for different input returns `TODO_STATE_REQUEST_CONFLICT` (409).
  A new command with an old revision returns `TODO_REVISION_CONFLICT` (409).
  Wrong project scope returns `TODO_SCOPE_CONFLICT` (409). Missing completion
  validation returns `TODO_COMPLETION_VALIDATION_REQUIRED` (409). No new write
  occurs on those failures.
- Read again after replay to display the latest object: the immutable command
  result can precede a later command. A command that keeps the same state records
  a result without increasing the Todo revision.
- Existing DONE/BLOCKED result propagation remains idempotent. A temporary
  propagation storage failure returns applied state plus
  `result_propagation.status=PENDING`; replay retries propagation without
  reapplying state. This creates review candidates, not automatic RAG adoption.
- The UI requires completion evidence and confirmation, retains uncertain input
  in session storage, and shows **이전 요청 확인** until that original request is
  resolved. State and metadata have separate save buttons.

## Operator and execution-session ownership

This is a Todo **operator command** on the existing loopback/direct-commander
transport. It does not choose an execution session, dispatch work, grant an
assignment or create a Task Frame. Provider/Worker bridge headers cannot be
relabeled as operator input (`TODO_OPERATOR_REQUIRED`). The server resolves the
actor; clients never supply an actor or reconstruct a Session Anchor.

The existing supervised execution-report flow retains its Anchor-aware domain
receipt gateway (`/v1/todo-action-mutation-receipts` then `/consume`, or
`/v1/todos/{id}/actions`). A Worker completion report remains that execution
contract. An authorized human or LLM operator updating the work map uses
`todo.state`. These domain contracts are distinct from Execution Guard evidence
recording. Generic `todo.update` and legacy `PATCH /v1/todos/{id}` both reject
lifecycle changes with `ACTION_TODO_LIFECYCLE_VIA_RECEIPT` (the retained legacy
error code); the error directs operator clients to `todo.state`.

Public `/v1/sessions` and `/v1/supervisor/sessions` responses are display
projections. They do not expose raw `provider_session_ref` or `session_id`
fields. Absence of those fields is **not evidence of an unbound session**.
Todo commands require neither those projections nor private SQL lookup. If a
session-dependent action lacks an executable resolver/contract, report that
specific capability as unavailable instead of inferring binding from an
unrelated list response.

## Remaining coverage

`todo.priority`, `todo.reorder`, `todo.bind_node`, `todo.bind_goal`, and
`todo.move_project` remain unregistered dedicated Actions. Read the current Todo
and use revision-checked `todo.update` for supported metadata changes.
`todo.archive`, `todo.restore`, and `todo.delete` are also pending dedicated
contracts. Legacy delete remains available in the work-map UI. Registration and
handler-backed coverage must be distinguished from the full vocabulary.

## Verification

`tests/test_universe_todo_actions.py` exercises the HTTP contract, native CLI,
missing completion evidence, private/caller fields, project scope, stale writes,
concurrent requests, durable replay, atomic rollback and propagation recovery.
`tests/test_todo_actions_ui.js` covers shared Actions and uncertain request replay.
Existing supervised lifecycle tests remain applicable to their separate gateway.

## Web service lifecycle Actions — 2026-09-14

Handler-backed `service.status` and `service.restart` are now implemented in source. They are registered only on a server running this version. They do not restart the PTY Supervisor.

- `service.status`: `{}` returns service status, pid and endpoint; `{ "operation_id": "<request-id>" }` also retrieves durable restart progress.
- `service.restart`: `{ "request_id": "<8–100 ASCII letters/digits/_/->", "expected_pid": <observed positive pid> }`. The HTTP gateway requires the existing local service-control Bearer token and rejects remote operators. Caller-supplied role, credentials and context remain forbidden in the Action body. Human Settings and LLM clients use this same contract.
- The server persists acceptance before launching a fixed external helper. Reusing a request id replays the record; changing its target conflicts. A different active request conflicts. The helper verifies the process again before shutdown, preserves the database, port and control token, and records completion only after a different healthy process appears on the original endpoint.
- Control tokens are passed to the replacement server only through its environment, never operation records or command arguments. Regular CLI restart semantics are unchanged.
- Settings → Service exposes restart/result lookup. An uncertain transport response retains the request id in session storage for reconnection; a definite validation/authorization failure clears it. The operation remains queryable after page reload. An interrupted helper is reported as unconfirmed rather than successful.

The Action does not create authorization. An agent Host reuses its existing user instruction or implemented automation authorization and calls `tools/universe_service_execution.py execute`. The Host records attempt/validation/dispatch evidence without a separate bind, permit or consume step; the service still authenticates the operator and validates its target. See [service-restart-execution.md](service-restart-execution.md) for the callable contract and completion evidence. The common recorder is not a generic COMMAND executor. Supervised Todo lifecycle receipts are a separate domain transition contract and are not Execution Guard permits.
