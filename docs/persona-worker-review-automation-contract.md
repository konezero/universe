# Persona Worker/Reviewer automation contract

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

## Implementation status

`WORKER_REVIEW` now routes through the typed Fleet Worker session Action. The
automation projection durably records the exact Worker instruction/result and
the distinct Reviewer assignment/verdict, while `MASTER_DIRECT` retains the
existing Master queue route. Todo completion is rejected until the current
Worker result has an independent Reviewer `PASS`; non-PASS verdicts use the
existing deterministic follow-up Todo route. Provider execution and live
acceptance remain separate evidence fields and are never inferred from a
session or queue receipt.
