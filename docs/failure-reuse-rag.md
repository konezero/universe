# Failure reuse RAG

Instruction: `p1-failure-reuse-rag-20260910`.
Work Receipt: `work_af5f1776bad0aeffe3babd18`;
binding: `binding_3e0be326f0b1a268dc947e9f`.

## Bounded outcome

The first implementation/verification record below describes commit `6c147af`.
The 2026-09-11 follow-up at the end extends that slice; its deployment state is
reported separately from source and fixture results.

Recall project-local failure evidence with its reported cause, proposed remedy,
applicability and verification limits; record the observed result of trying it.
No automatic patch, provider invocation, web research, canonical adoption,
pattern promotion, or change to the pending P0 Goal.

## Source evidence and design

`UniverseStore.create_experience_case` requires real Skill observations and
`match_experience_case` compares Skill/outcome/validation overlap. These are
observations, not a cause/remedy record. The Goal completion defect was observed
in HTTP/storage tests, not a Task Frame Worker run; inventing a Skill observation
to admit it would misrepresent its origin.

Memory Candidates already have redaction, immutable content digests, review,
and persistent JSON storage. Extend their MEMORY kind with optional structured
failure knowledge. Keep one canonical candidate store. A separate bounded reuse
ledger references the exact candidate digest; it is feedback, not another RAG.

Retrieval requires component + operation + error code. A supplied cause key and
applicability context must not contradict the candidate. Missing cause/context
must stay explicitly unconfirmed. Similarity never proves common causation.
Rejected, conflicted, or superseded candidates must not be recommended. Repeated
case registration and reuse references are idempotent; changed content conflicts.

Expose recall and reuse through project-local APIs, and allow the existing LLM
retrieval-context boundary to include an explicitly requested failure recall in
a separate CANDIDATE_ONLY section. Do not mix it with adopted Memory or its
recent-note fallback. Preserve the recorded validation and deployment limits
even after someone reports successful reuse.

## Acceptance and affected planes

- Source/storage: candidate schema, deterministic lookup, immutable feedback,
  persistence/reopen and concurrency/replay tests.
- API/protocol: real HTTP record -> recall -> feedback -> repeat; cross-project,
  mismatched cause/context/digest and malformed input rejection.
- Runtime context: the same recall projection appears at the existing explicit
  retrieval boundary; no provider is invoked by recall.
- Initial evidence: the Goal completion defect, labelled RETROSPECTIVE, with
  contract tests PASS and resident deployment/live provider verification NOT_RUN.
- UI, provider, lifecycle, distribution: record actual exercised scope and any
  NOT_RUN state; no resident restart or publication implied by source tests.

## API use

All paths below are project-local, under `/v1/projects/{project_id}`. Use the
existing service authentication and local-operator context for writes.

| Method and suffix | Contract |
| --- | --- |
| `POST /memory-candidates` | Register the example candidate; returns its stable `candidate_id` and content `candidate_digest`. New failures enter `REVIEW_REQUIRED`. |
| `POST /failure-reuse/query` | Read exact structured matches, proposed remedies, recorded verification and limitations. No writes or provider calls. |
| `POST /failure-reuse/observations` | Record one attempt against the exact candidate digest; local operator required. |
| `GET /failure-reuse/observations?candidate_id=...` | Read the latest 20 attempt records with their own context, validation and evidence; reports truncation. |
| `POST /retrieval-context` | Existing Mode/Current Anchor contract, with optional `failure` query; adds `retrieval.failure_reuse`, separate from adopted Memory hits. |

The registration body is [examples/goal-completion-failure.json](examples/goal-completion-failure.json).
It describes the actual earlier defect retrospectively. `TASK_FRAME_READY` is
an observed automation-state tag, not a newly introduced API error code.

Recall request:

```json
{
  "component": "goal_automation",
  "operation": "apply_task_frame_todo_result",
  "error_code": "TASK_FRAME_READY",
  "cause_key": "work-plan-terminal-application-missing",
  "context": {
    "completion_route": "WORK_PLAN_TODOS",
    "execution_plane": "HTTP_STORAGE"
  },
  "limit": 5
}
```

Identifiers/context values normalize case. The three signature fields are
required. `cause_key` and `context` may be omitted for discovery, but missing
confirmation remains explicit in each match. A supplied contradictory cause
or applicability dimension excludes the candidate. `CONFIRMED` is a reported
evidence state, not an independent root-cause conclusion made by this service.

After an actual attempt, submit `reuse_ref`, `candidate_id`, `candidate_digest`,
the attempted `query`, `outcome`, `validation: {plane, status}`, `evidence_refs`
and `limitations`. Read the IDs/digest from registration or recall; do not
substitute a Mode, session, or Task Frame ID. Use one stable `reuse_ref` per
attempt, not a new random value for each retry.

- `RESOLVED` requires `PASS`, evidence references, matching reported confirmed
  cause, and all required applicability context.
- `NOT_RESOLVED` requires `FAIL` and evidence references.
- `UNKNOWN` can preserve `NOT_RUN` without inventing evidence.
- Exact registration/feedback replay returns 200 after the initial 201. The
  same stable identity with different content returns 409, without overwriting.
- Revised knowledge uses a new `failure_ref` and an explicit `SUPERSEDES`
  relation through the existing candidate mechanism. It does not mutate the
  original incident or observations.

`reuse_outcomes` counts all recorded contexts/validation planes for the exact
candidate digest; `reuse_outcomes_scope` says so explicitly. It is not a success
probability or proof that the current environment is fixed. Read the individual
observations before using their evidence. Original provider/deployment
`NOT_RUN` entries are never upgraded by a later HTTP/storage success.

Evidence URIs are validated references, not automatically fetched or verified
documents. Secret-like values and raw/unsupported failure fields are rejected;
callers must still submit redacted summaries rather than transcripts.

## Verification record

Validated on 2026-09-11, Windows, Python 3.14.5. Tests use temporary project
roots and SQLite stores with an actual local HTTP server. No provider was
invoked and no user-running service database was used.

| Test scope | Result |
| --- | --- |
| `test_failure_reuse.py` | 16 PASS; schema, registration/recall/feedback/reopen, different causes/context/projects, stale digests, review/relation exclusion, incomplete evidence, negative/unknown outcomes and concurrent replay. |
| `test_memory_candidates_and_delegations.py` | 12 PASS. |
| `test_universe_memory.py` | 7 PASS. |
| `test_memory_execution_service.py` | 3 PASS. |
| `test_universe_server.py -k retrieval` | 1 PASS; existing retrieval regression. |
| `test_universe_server.py -k goal` | 20 PASS; retains the prior P0 changes. |
| `test_memory_fast_extract.py` | 2 PASS, 2 FAIL with `ACTION_CREDENTIAL_REF_ONLY`. The same two failures reproduce using Git HEAD versions of the three touched modules. |
| Targeted Ruff `E9,F63,F7,F82`, Git diff whitespace | PASS. |

The FAST_EXTRACT failures are
`test_codex_activity_to_fast_extract_candidate_and_bench_is_idempotent` and
`test_failed_fast_extract_run_can_retry_same_input_once`: the existing payload
contains `request.runtime_binding.token`, which the current credential-ref-only
gate rejects. Comparison loaded HEAD versions of `universe_memory.py`,
`memory_fast_extract.py` and `universe_server.py` into the unchanged test runner;
this was not a full clean-environment run. This slice did not relax that gate.

Host-observed request/result artifacts are under `.ai/runtime/tmp/`:
`p1-rag-result-0.json` through `p1-rag-result-4.json`,
`p1-rag-retrieval-tests-result.json`, `p1-rag-goal-regression-result.json`,
and `p1-rag-baseline-test-result.json`. They are local execution evidence,
not checked-in product fixtures.

```yaml
outcome: SUCCEEDED
boundary: bounded failure-candidate recall and observed-reuse source/API slice
affected_planes: [source, storage, api_protocol, retrieval_projection]
validation:
  - plane: source_storage_api_protocol
    state: PASS
    evidence_refs: [tests/test_failure_reuse.py, .ai/runtime/tmp/p1-rag-result-0.json]
  - plane: retrieval_projection
    state: PASS
    evidence_refs: [tests/test_failure_reuse.py, .ai/runtime/tmp/p1-rag-retrieval-tests-result.json]
  - plane: adjacent_fast_extract_regression
    state: FAIL
    evidence_refs: [.ai/runtime/tmp/p1-rag-result-3.json, .ai/runtime/tmp/p1-rag-baseline-test-result.json]
  - plane: live_provider_and_resident_deployment
    state: NOT_RUN
    evidence_refs: []
  - plane: ui
    state: NOT_APPLICABLE
    evidence_refs: []
  - plane: distribution
    state: NOT_RUN
    evidence_refs: []
residual_risks:
  - No autonomous failure capture, semantic/embedding search, research or repair loop.
  - No scale/performance validation of large same-signature candidate collections.
  - No new HTTP retrieval-context test against a live Mode Anchor; store projection tested.
  - Example registration and feedback were temporary HTTP/storage fixtures, not live RAG adoption or proof of a real second repair.
  - Existing FAST_EXTRACT credential migration failures remain.
changed_paths:
  - tools/universe_app/failure_reuse.py
  - tools/universe_memory.py
  - tools/universe_server.py
  - tools/knowledge_redaction.py
  - tools/memory_fast_extract.py
  - tests/test_failure_reuse.py
  - docs/examples/goal-completion-failure.json
  - docs/failure-reuse-rag.md
```

This is not completion of the broader P1 automation backlog or the blocked P0
Goal. The running Universe service is unchanged; no restart, commit, release,
push, live candidate registration, or canonical RAG adoption was performed.

## 2026-09-11: governed batch failure/retry integration

Instruction: `p1-failure-reuse-live-flow-20260911`.
Work Receipt: `work_019a1f8415a981729b6f7253`;
binding: `binding_7810eaf5f1a1d0d2a7601107`.

Confirmed owners and changes:

1. FAST_EXTRACT's old HTTP payload carried `runtime_binding.token`, while the
   common Action Registry correctly rejects inline credentials. Baseline:
   two failing tests, both `ACTION_CREDENTIAL_REF_ONLY`, before Worker dispatch.
   The session-start hook already creates a Host-owned session attachment.
   Its public projection now supplies an opaque, attachment-specific
   `credential_ref`; the shared resolver checks exact project/session/anchor,
   live/current state, and reference before returning transport credentials to
   internal code only. Missing/stale references fail closed, with no new session
   or global-Conductor fallback. Task Frame still owns turn authorization.
2. `retry_memory_batch_run` removed the prior failure and successful completion
   replaced its result JSON. New `memory_batch_attempt_evidence` rows preserve
   each terminal attempt in the same transaction as its run transition. This is
   run evidence, not another knowledge store or automatic candidate promotion.
3. `fail_memory_batch_run` automatically recalls existing project-local failure
   candidates. Retry refreshes the original query so newly ignored/superseded
   evidence is excluded, then prepares a separately labelled Worker context.
   Immutable snapshots retain the exact evidence seen at failure and retry.
4. The successful second attempt links its previous attempt reference and
   candidate IDs/digests. Operation PASS does not imply causal remediation:
   `remedy_application` and `cause_resolution` remain `UNKNOWN`;
   `CONTEXT_PREPARED_NOT_PROVEN_APPLIED` is not proof the provider used a remedy.
   Explicit evaluated reuse still uses the original observation API.

API: `GET /v1/projects/{project_id}/failure-reuse/batch-attempts?run_id=...`.
It returns the latest 20 records, newest first, with a truncation flag.
Current run responses also expose `run.attempt_evidence`. The credential-only
request migration is documented in [universe-memory-rag.md](universe-memory-rag.md).
No old token-body compatibility bypass was added. No new credential database,
raw-token registration endpoint, autonomous retry, or repair authority was added.

The integration test registers a clearly fixture-labelled failure candidate,
executes the actual HTTP/Host/dispatcher path with an intentionally failing
provider adapter, checks automatic recall, retries under the same run ID,
asserts the retry context, and verifies history/replay after SQLite reopen.
The provider adapter and Runtime transport are fixtures, not live providers.
Separate coverage verifies concurrent exactly-once failed-attempt capture,
transaction rollback, cross-project rejection and stale/rotated attachments.

Live preflight: the user service at `http://127.0.0.1:60443` reports READY;
Supervisor reports two LIVE records (one CURRENT, one STALE). The current
Universe batch listing has 23 runs and no FAILED entries. This does not prove
the new code is loaded or that historical failures never occurred. Service
restart approval was requested because active sessions can be interrupted.
No resident restart, live provider invocation or live candidate/fixture import
has been performed during this follow-up.

Final follow-up regression: **78 PASS** across failure recall (17),
FAST_EXTRACT/HTTP retry (6), session credentials (3), Memory (7), Memory
candidates/delegation (12), Memory execution (3), Action Registry (9), session
attachment (1), and Goal automation (20). The two prior FAST_EXTRACT failures
are fixed by migrating the request contract; inline-token rejection remains
covered. The retry-context test also rejects a candidate ignored between the
first failure and the second attempt. Targeted Ruff and whitespace checks PASS.
Host-observed results are `.ai/runtime/tmp/p1-flow-*-result.json`; the baseline
failure record is `p1-flow-fast-baseline-result.json`.

```yaml
outcome: PARTIAL
affected_planes: [source, storage, api_protocol, runtime_context, session_attachment]
validation:
  - plane: source_storage_api_protocol
    state: PASS
    evidence_refs: [tests/test_failure_reuse.py, tests/test_memory_fast_extract.py]
  - plane: session_attachment
    state: PASS
    evidence_refs: [tests/test_session_runtime_credentials.py, .ai/runtime/tmp/p1-flow-attachment-tests-result.json]
  - plane: runtime_context
    state: PASS
    evidence_refs: [.ai/runtime/tmp/p1-flow-fast-final-result.json]
  - plane: live_provider_and_resident_application
    state: NOT_RUN
    evidence_refs: []
  - plane: ui
    state: NOT_APPLICABLE
    evidence_refs: []
  - plane: distribution
    state: NOT_RUN
    evidence_refs: []
residual_risks:
  - Resident restart permission is pending; live application and provider probe remain unverified.
  - Automatic capture is limited to reserved governed Memory batch attempts, not every product failure or pre-dispatch Action rejection.
  - Existing legacy callers must adopt the credential reference contract; the basic UI Run stage button does not choose a Task Frame turn.
  - Source-only Runtime attachments still need genuine Host currentness and Task Frame authorization before execution.
  - Research-gap filling, semantic search, automatic retry/patch and verified causal-remedy adoption remain outside this slice.
changed_paths:
  - tools/universe_app/session_runtime_credentials.py
  - tools/universe_app/failure_reuse.py
  - tools/universe_server.py
  - tools/memory_fast_extract.py
  - tests/test_session_runtime_credentials.py
  - tests/test_failure_reuse.py
  - tests/test_memory_fast_extract.py
  - docs/universe-memory-rag.md
  - docs/failure-reuse-rag.md
```
