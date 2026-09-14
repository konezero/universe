# Service restart execution evidence

Status: implemented and live-verified 2026-09-14.

The user changed Execution Guard from permission issuance to evidence recording.
`tools/universe_service_execution.py` now dispatches an already authorized
`service.restart` directly and appends execution evidence. There is no prepare,
bind, check, permit expiry or consume sequence. An instruction reference records
existing authorization; it cannot create authorization or act as a credential.

## Host sequence

Use structured native argv with `shell=False`:

```text
python tools/universe_service_execution.py execute
  --repo-root <repository>
  --request <json-file>
  --instruction-ref <existing-user-instruction-or-authorized-automation-run>
```

The request is exactly `{ "request_id": "<stable-id>", "expected_pid": <observed-pid> }`.
`--session-id` is optional and defaults to CODEX_THREAD_ID; an authorized automation
does not need an invented interactive session or Anchor. Observe service.status
before choosing the expected PID. The Host target comes only from its
UNIVERSE_STATE_FILE or normal local Universe state path.

1. Append ATTEMPTED under the request id.
2. Validate the fixed local endpoint, control-token availability and observed PID;
   append VALIDATED. Scope and authorization belong to the invoking Host, and the
   service still authenticates its local operator and checks the PID at execution.
3. Send only the fixed service.restart Action. No command, executable, caller
   endpoint/token, approval object or old receipt is accepted. Redirects are disabled.
4. Append DISPATCHED after acknowledgement. Query service.status with the same
   operation_id until COMPLETED, FAILED or INTERRUPTED. Acceptance is not completion.
   If transport is uncertain, query that id; do not issue a replacement restart.

## Evidence and failure behavior

Runtime bookkeeping is append-only in
`.ai/runtime/state/service_execution.sqlite3`, table `execution_event`.
Each event records an operation id, phase, timestamp, session when available,
existing instruction/run reference, fixed Action/target, expected PID, state
hash and structured error code. Tokens, raw source and prompts are not recorded.
The service's durable operation ledger owns completion and correlates by the same
id. The Host ledger's DISPATCHED is never rewritten as an invented success.

If the initial record fails, no Action is dispatched: EXECUTION_AUDIT_UNAVAILABLE
is a storage failure, not a request for another approval. A logging failure after
acceptance preserves the actual response and reports EXECUTION_AUDIT_FAILED
separately. A lost response is UNCONFIRMED and is never automatically retried.
Historical binding and permit tables remain historical data and are not consumed.
Operational audit records are not automatic RAG collection candidates.

## Shared Runtime migration

Canonical common Runtime changes are in `C:/workspace/career/runtime-source/.ai`:
ExecutionGuardRuntime records evidence; the file gateway revalidates at the write;
project-bound Host and repo-root CLI routes persist evidence. Old consume calls
return EXECUTION_RECEIPTS_RETIRED. Generic COMMAND execution is still not supplied
by the common recorder. Source Work Receipts continue to describe bounded user
instructions; they are not per-execution permission tokens.

Universe's installed common Runtime is still the older linked release. Its
content-addressed files and release provenance were not overwritten. The local
project policy explicitly adopts the user's new decision, and this product-owned
service adapter is active independently of the shared release upgrade. Upgrading
all installed common file/Host routes requires building and installing that new
release through the normal release pipeline.

## Live verification

Operation `restart-evidence-fa366ea68a2248e9b4bec967bb1fdaad` completed without a
binding or permit. Web PID 38244 became 20992 at http://127.0.0.1:61265. PTY
Supervisor PID 18468 remained unchanged; remote gateway and connector returned
READY. The Host ledger contains ATTEMPTED, VALIDATED and DISPATCHED for that same
id, and service.status reports its authoritative COMPLETED result.

Tests cover execution without an interactive session, exact request/target checks,
PID changes, token exclusion, uncertain dispatch, audit-storage failures before
and after dispatch, and the underlying service replay/control regressions.
