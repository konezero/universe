# Persona Worker/Reviewer automation contract

## Recover an already-closed independent Host

POST `/v1/actions` with `action_id: persona.automation.recover-frame-review`.
The request contains `run_id`, `owner_ref`, `task_frame_id`, `request_id`,
`expected_revision`, plus either `worker_attempt` and `reviewer_attempt` (positive
integers) or all four exact `worker_result_ref`, `worker_result_digest`,
`reviewer_result_ref`, and `reviewer_result_digest` fields. Never mix selectors
and evidence. The server resolves canonical evidence; it accepts no verdict.

Recovery requires the latest launched Host to be EXITED/MASTER_DONE, unchanged
Todo project/node scope, the exact owning session, and both results already
collected. Legacy Hosts without a launch journal still require exactly the
original Worker and read-only Reviewer. Journal-backed Hosts support rework:
select the latest assigned attempt of each role, verify contiguous attempts,
unchanged Todo content/write scope, exact subsequent collections, and a closed
Master journal. The Reviewer assignment must pin the selected collected Worker
ref/digest. Missing, stale, orphaned or differently reviewed attempts are rejected.
The server reads the project-owned Task Frame SQLite stores in read-only mode,
verifies source/owner/role/sequence/independence, compares immutable result refs
and SHA-256 digests, and cross-checks execution envelopes.

The Action atomically records the actual persisted verdict and binds a
TASK_FRAME_RECOVERY assignment and review. It never creates a dispatch or runs a
provider. Same-request retries replay; conflicting evidence, stale revisions,
and replacement of an existing assignment/review are rejected. A PASS with an
incomplete or unverified Worker result is rejected (the explicit completed Worker
validation values are PASS, PASSED and VERIFIED). PASS additionally requires
retained original Host input or validated Master journal lineage proving unchanged
Todo title/detail. Missing transient input alone never authorizes PASS; without
either content proof, immutable NEEDS_REVISION/BLOCKED may be bound but cannot
complete. Malformed or conflicting input is rejected.
NEEDS_REVISION and BLOCKED
remain non-passing. Recovery leaves the run WAITING with the actual next action;
it does not close a Todo or mark a run complete. A valid PASS can subsequently use
the existing complete and Todo validation routes.

## Canonical role collection

For a journal-backed Host, `persona.automation.collect-frame` accepts
`result_role: WORKER|REVIEWER` and a positive integer `result_attempt` alongside
`run_id`, `owner_ref`, `task_frame_id`, `status: COMPLETED`, and `request_id`.
Do not mix these selectors with `result_ref`/`result_digest`. The server reads the
exact owning role store, computes canonical evidence and applies the same strict
owner, Todo, terminal-state and execution-envelope checks as explicit evidence.
The response returns `result_ref` and `result_digest`; deterministic collection
replay remains unchanged. The selector's COMPLETED status means result collection,
not Worker acceptance: PARTIAL_VERIFICATION and NEEDS_REVISION remain non-passing.

## Purpose

A node Master orchestrates work. It may complete a bounded operational item directly,
but source mutation, RAG execution, external effects, or evidence-bearing validation
must use the Worker/Reviewer route.

## Durable sequence

1. The node Master selects an exact Todo and records `execution_mode`:
   `MASTER_DIRECT` or `WORKER_REVIEW`.
2. For `WORKER_REVIEW`, the server creates one Worker session assignment pinned to
   project, feature node, Todo, optional Task Frame, Master Anchor, instruction and
   completion conditions. A launch receipt alone is never a work result.
3. The Worker submits one result bound to that assignment: outcome, evidence refs,
   validation state, and a concise result reference.
4. The server creates a Reviewer assignment for the same Todo. Its Anchor must differ
   from the Worker Anchor. The Reviewer receives only the pinned Worker result and
   records `PASS`, `NEEDS_REVISION`, or `BLOCKED` with evidence.
5. Only a Reviewer `PASS` allows the Master to close a `WORKER_REVIEW` automation
   assignment. Non-PASS creates a deterministic follow-up Todo and keeps automation
   progressing by priority.

## Invariants

- Every write is scoped to the exact project/node/Todo/Task Frame coordinates.
- Worker and Reviewer results use CAS revisions and idempotency keys.
- A Worker launch, queue receipt, or CLI liveness is not execution success.
- The Reviewer cannot equal the Worker or accept a missing Worker result.
- Master completion is rejected while the required reviewer verdict is absent.
- Direct Master work remains available only when explicitly selected; the server does
  not infer safety from Todo wording.

### Source execution uses the owning Runtime

A Fleet Goal's bounded instruction is carried through its Todo and Task Frame.
For source work, the common Host binds that instruction with the existing
`execution-binding/begin-local-work` API, creates the existing
`task-frame-instruction-v2` frame with the returned assignment, and submits edits
to the same Runtime's `mutation-gateway/apply-file` API. This is execution of the
Goal instruction, not another user approval. The Worker receives neither tokens
nor the Host's work snapshot. Its editor checks the live turn claim, exact files,
operations and preimage; each gateway request pins the frame's work boundary.
Replacing the active binding therefore invalidates an older editor.

The standalone `.ai/runtime/state/anchor_work` file is not the receipt store for
an attached Task Frame. Mixing its local lookup with a server-owned Runtime
caused `ANCHOR_WORK_RECEIPT_REQUIRED` despite a valid Goal/Todo assignment.
Integration coverage must execute the installed Runtime and file gateway with
no local Anchor receipt file; injecting a fake receipt cannot verify this path.

### Conductor rework requests

For a journal-backed Task Frame, a Conductor uses
`persona.automation.request-rework` with `run_id`, `task_frame_id`, its
registered `conductor_anchor_ref`, stable `request_id`, `feedback`, and the
`based_on_result_ref`/`based_on_result_digest` of the latest collected Worker
result. The server checks the frame and Conductor's project, appends a
`CONDUCTOR_REWORK_REQUESTED` entry to that Todo's journal, then sends the
Master a Session Bus instruction containing the pinned journal reference.
The Bus body is a notification; the journal is the request history. A replay
reuses the same entry and Bus idempotency key. The Bus queues the message for
the Master's Anchor when its terminal is offline.

The Master reads the pinned entry and, if rework is still needed, sends
`persona.automation.host-directive` with `directive: REWORK`, `target_role:
WORKER`, matching `feedback`, and `conductor_request_ref` set to that journal
reference. The Host directive adapter rejects the request if a newer Worker
result has since been collected. Directives still enter the Todo journal before
the Host's Boss room receives them.

## Implementation status

`WORKER_REVIEW` now routes through the typed Fleet Worker session Action. The
automation projection durably records the exact Worker instruction/result and
the distinct Reviewer assignment/verdict, while `MASTER_DIRECT` retains the
existing Master queue route. A node-bound `MASTER_DIRECT` result immediately
creates a distinct Reviewer Worker through the same typed Fleet route; the
node Master is therefore the orchestrator and does not wait for an unrelated
human review. A Reviewer `PASS` queues the next node-Master control turn,
which completes the exact automation assignment and Todo through typed
Actions. Todo completion is rejected until the required independent Reviewer
`PASS`; non-PASS verdicts use the existing deterministic follow-up Todo route.
Provider execution and live acceptance remain separate evidence fields and are
never inferred from a session or queue receipt.
