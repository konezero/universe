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

## Current implementation gap

Fleet already provides typed Worker/Reviewer session creation and durable bindings.
Persona automation still dispatches a Master message directly. The next implementation
slice adds the durable execution/result/review records and routes `WORKER_REVIEW`
through Fleet instead of treating a Master message completion as a result.
