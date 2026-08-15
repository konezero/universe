# Goal-centered Work Loop

Universe exposes one project-scoped work-loop view that connects Goal/Plan evidence, Todo outcomes, Experience, Memory/RAG, Bench observations, curated Seeds, prediction proposals, and document-automation counts.

## Operator flow

1. Select a project and open its Details inspector.
2. In **Work Loop**, choose **Predict** to build a deterministic proposal from currently recorded evidence.
3. Inspect every suggestion's kind, confidence, and provenance. Unsupported and low-confidence candidates remain visible as rejected inputs.
4. Choose **Keep** or **Reject** for the proposal. Keeping is curation only: it does not create a Goal, Plan, Milestone, Todo, authority, or execution assignment.
5. Choose **Recover** after an interrupted run to return only recovery-eligible `IN_PROGRESS` Todos to `READY`.

Predictions never auto-adopt Goals or Todos. Document proposals also remain review-only and are never auto-applied.

## API

- `GET /v1/projects/{project_id}/work-loop` returns prediction proposals, deterministic Result fan-outs, and document-automation proposal counts.
- `POST /v1/projects/{project_id}/work-loop/predictions` builds or reuses the content-addressed current proposal.
- `POST /v1/projects/{project_id}/work-loop/predictions/review` accepts `{ "proposal_id": "...", "decision": "KEEP|REJECT" }`.
- `POST /v1/projects/{project_id}/work-loop/recover` performs bounded Todo restart recovery.

Prediction review states are `PROPOSAL_ONLY`, `KEPT`, and `REJECTED`. A reviewed proposal cannot be switched to the opposite decision; the API fails closed with a conflict.

## Prediction evidence

The engine combines curated Seed material, structured success/failure Experience, completed or blocked Todo outcomes, reviewed Memory/RAG material, and Bench observations. A normal Goal/Plan/Milestone suggestion requires at least two evidence kinds and confidence at or above the configured threshold. A prior failed Experience with overlapping tokens produces a `RISK` suggestion and recurrence-prevention references instead of silently repeating the plan.

Each accepted suggestion contains:

- `kind`: `GOAL`, `PLAN`, `MILESTONE`, or `RISK`;
- `confidence`: deterministic score in the range `0..0.95`;
- `provenance`: source kind and stable source reference;
- `rationale`: why the evidence supports review;
- `recurrence_prevention`: matching failed Experience when applicable;
- `adoption_state: PROPOSAL_ONLY`.

Rejected candidates retain `reason` (`UNSUPPORTED` or `LOW_CONFIDENCE`), confidence, and provenance so operators can see why Universe declined to recommend them.

## Result fan-out and recovery

A terminal Todo transition records one idempotent Result fan-out descriptor for Goal/Plan observation, Experience candidacy, and Memory review. The descriptor does not itself create those entities; downstream incorporation remains independently reviewable.

Recovery only changes an `IN_PROGRESS` Todo when a linked room message has explicit `FAILED` delivery evidence, then emits a recovery event. An unlinked Todo remains untouched because absence is not failure evidence. Recovery creates no Task Frame or execution assignment. Operators should inspect the returned `recovered` list before resuming work.
