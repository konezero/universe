# Service restart execution

Universe owns the web-service lifecycle. `tools/universe_service_execution.py`
is the local Host adapter for agent-issued restart. It dispatches the same
`service.restart` Action used by Settings after a separate exact lifecycle
binding and a consumed one-time execution permit. A source Work Receipt,
Mode, local control token, or an Action id alone is not this permission.

## Host sequence

Use structured native argv with `shell=False`. Common CLI arguments are
`--repo-root <repository>` and `--session-id <current Host session>`.
The session defaults to `CODEX_THREAD_ID`. The service target comes only from
Host `UNIVERSE_STATE_FILE` or the normal local Universe state location.

1. `python tools/universe_service_execution.py prepare ...` returns the exact
   proposal: current Session Anchor, service state preimage, PID, endpoint,
   Action request id, and content-derived proposal id. No token is returned.
2. The Host attaches existing explicit user authorization to this proposal:
   `bind --request <json>` with `proposal` and `approval`. Approval fields are
   `status: APPROVED`, `proposal_id`, `action_id: service.restart`, `target`,
   `expected_pid`, and `instruction_ref`. All target fields must match.
   This is Host-attested evidence, not a remote client assertion. Do not invent
   user approval. A user instruction already authorizing this restart needs no
   second prompt. Source-edit authorization alone does not authorize restart.
3. `check --request <json>` accepts `{ "binding_id": "<proposal_id>" }` and
   returns `EXECUTION_GUARD_PERMITTED` with a 30-second one-time `receipt_id`.
4. Immediately call `execute --request <json>` with that `receipt_id`. The
   receipt-aware hook rechecks the current Anchor and exact Host state under a
   serialized ledger transaction, consumes the permit, then sends only the
   bound `service.restart` request. It accepts no executable, shell command,
   caller endpoint, caller token, or replacement request. HTTP redirects are
   disabled. The service also validates its current PID before acceptance and
   again before stopping it.
5. Poll `service.status` with the returned `operation_id` until `COMPLETED`,
   `FAILED`, or `INTERRUPTED`. Acceptance is not completion. If transport is
   uncertain, query that same id; do not issue a new restart to compensate.

Bindings and permit consumption are Runtime-owned operational state in
`.ai/runtime/state/service_execution.sqlite3`. Service completion is separately
recorded in the existing service operation ledger. Neither stores a token.
The adapter is a local Host capability, not a new remotely callable approval
endpoint. Human Settings continues to use its authenticated operator flow.

The installed shared Runtime still lacks generic COMMAND handling. Its files
are content-addressed release links and were not modified. In particular, do
not interpret its `DIRECT_INSTRUCTION_SCOPE_READY / NOT_REQUIRED` file-route
response as lifecycle permission. This product-owned adapter provides the
strict lifecycle route; generic command execution remains unsupported here.

## Verified 2026-09-14

The adapter issued and consumed a real permit and dispatched operation
`restart-888e7f33ccf14e638059b08953b23024`. Its durable status reached COMPLETED:
web PID 53816 became 38244 at `http://127.0.0.1:61265`; PTY Supervisor PID 18468
remained unchanged. Saved remote access resumed, with gateway and connector
both READY. The new shared project-draft Actions were visible after restart.

Tests cover separate exact approval, missing binding, one-time consumption,
expiry, changed Anchor/target/PID, modified proposal, nonlocal endpoint
rejection, fixed HTTP dispatch and token exclusion. Related service Action and
service-control regressions also passed (33 tests total).
