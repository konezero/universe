# Universe Memory RAG (product slice)

Status: implemented and live-probed (governed FAST_EXTRACT adapter plus deterministic Memory RAG)
Scope: project-local memory notes, configurable redacted batch stages,
governed Codex extraction, candidate review, SkillRunObservation/Bench evidence,
node link/unlink, search, and propose-links
Not: automatic Candidate adoption, Seed mutation, automatic Bench/Future promotion,
Career promotion, or raw transcript storage

Product-model clarification (2026-09-23): [Galaxy proposals, Memory sources,
and Fleet Goal nodes](galaxy-memory-fleet-goal-lifecycle.md) separates project
Memory ideas/predictions from operational RAG references. The target
collection and LLM noise-removal pipeline recurs under Ops configuration
independently of Goal/Todo completion; the second LLM pass is not live yet.
Memory and Galaxy share each proposal's decision state; accepting
from either creates a separate, linked Fleet Goal. RAG stores source-checked
Bench/development/operational references without a per-item forecast-adoption
decision. The candidate `KEEP` and `rag.adopt` contract below is current
implementation, not this target Goal acceptance or RAG ingestion contract.

## Target project pipeline and current stage mapping (2026-09-23)

```text
Ops: assign LLM/model, recurrence, quota and failure policy per project
  -> scheduled LLM collection
  -> LLM noise removal and classification
  -> Memory proposal source (idea, prediction, possible Goal/product)
       -> same proposal and acceptance state in Memory and Galaxy
       -> user accepts from either surface
       -> distinct Fleet Goal work node -> Todos -> outcome -> terminal DONE
  -> RAG operational reference (Bench, development docs, cautions)
       -> source/currentness/applicability validation -> retrieval
```

Collection is periodic and does not wait for Goal completion, Todo reopening,
or a human KEEP decision for each batch. Goal automation is finite and stops
when that Goal is complete. RAG evidence is not a forecast to accept as a Goal.
The Memory source and Galaxy proposal are project-scoped; a Goal has its own
identity and explicit origin link. A later follow-up Goal is new work, not a
restart of a completed Goal. See the
[Goal-node lifecycle contract](galaxy-memory-fleet-goal-lifecycle.md).

Implementation status: FAST_EXTRACT uses a governed LLM. CONSOLIDATE
supports a governed LLM noise decision pass with Codex gpt-5.6-luna MAX
and `fallback: NONE`. With `dry_run: true`, the decisions are recorded without
candidate writes. With `dry_run: false`, source-pinned KEEP decisions produce
review-only CONSOLIDATE candidates atomically with the run; NOISE decisions
produce none. The model's route is retained in run evidence, not yet a durable
candidate-to-proposal/RAG routing edge. SYNTHESIZE is deterministic and
INDEPENDENT_CHECK is structural. Automatic proposal generation and
source-checked operational RAG ingestion remain unimplemented.
Current `KEEP`/`rag.adopt` still perform legacy Memory/RAG review; the new
`goal.accept-proposal` Action is a separate user decision for an eligible
idea or prediction and does not remove or migrate that legacy route. Fleet Goal
projection is now distinct in the UI, and project-local proposal candidates are
projected as separate nodes in Galaxy/Knowledge views. Full proposal lineage
graph edges, historic Feature/Goal migration, and live scheduled end-to-end
acceptance remain pending. Galaxy proposals now have a paged project-scoped
read model; the Memory menu shows that proposal read model and Ops retains
a separately capped legacy operational RAG review. Ops now labels deterministic
stages as model-not-invoked even when a provider/model is configured. Downstream
stages read a lean batch-input projection, without UI review/RAG lookups, and
reject an input set above 500 candidates with `MEMORY_BATCH_INPUT_WINDOW_REQUIRED`
before writing a partial run. A durable paged stage cursor is still required for
those larger projects; this guard is not the second LLM pass. Do not present the
target pipeline as production-complete.

## Source-grounded candidate eligibility (2026-09-13)

Current implementation evidence is now required before KEEP/adoption. Open TODO-backed future work is kept separate from current facts; outdated or contradicted records are retained in a separate archive. Unverified or stale assessments block adoption. See [source eligibility contract and live verification](rag-source-eligibility-20260913.md). Semantic comparison currently runs explicitly in bounded batches; scheduled INDEPENDENT_CHECK remains a structural check.

## LLM retrieval context

Every resident Project Master message receives a bounded, project-local
`universe.project-llm-retrieval-context.v1` projection. Retrieval includes
only `LINKED` Memory, ranking Node and token matches first and using a bounded
recent fallback when no explicit match exists. It also ranks matching
project-local Bench observations first, then falls back to bounded successful
project-local Skills. Bench recommendations remain `CANDIDATE_ONLY` with
`TASK_FRAME_SELECTION_REQUIRED`; they create no skill binding, authority, or
execution assignment. Explicit Context Packs carry the same retrieval
projection for downstream planning. Unlinked or merely proposed Memory is not
injected.

## Invariant

```text
Node Memory = reference context
MEMORY_SYNC != Candidate
MEMORY_SYNC != Seed write
MEMORY_SYNC != Task Frame / authority
Provider activity batch -> operator-selected project Memory -> node review/link
Provider activity refs -> FAST_EXTRACT -> CONSOLIDATE -> SYNTHESIZE -> Candidate Review
Codex rollout cursor -> redacted Activity -> governed FAST_EXTRACT -> Candidate + Bench evidence
Conductor chat -> bounded delegation state -> Project Master / Task Frame result
```

## Memory batch stages

Each Project can persist one configuration per stage:
`FAST_EXTRACT`, `CONSOLIDATE`, `SYNTHESIZE`, and optional
`INDEPENDENT_CHECK`. Configuration includes provider, model, effort, schedule,
quota or budget, fallback, enabled, and dry-run values. Missing provider,
model, and effort values inherit the existing `ROUTINE` Worker Binding shape
for `MEMORY_<STAGE>`.

The service resolves the normalized configuration against the current provider
model catalog. An invalid provider or model fails closed. An unavailable
provider is reported as unavailable; only an explicitly configured
`DETERMINISTIC` fallback can run without an available provider. The catalog
snapshot and resolution status are returned with the configuration.

The Runtime persists one scheduler state per enabled stage and executes due
stages from a wall-clock worker. Schedule claims use a durable due-slot key,
lease owner and generation so restart recovery cannot duplicate a successful
slot. Failed attempts use deterministic exponential backoff with a bounded
attempt count; expired claims return to retry state exactly once. Quota windows
are UTC epoch-aligned and their consumption is persisted with the claim.
Quota counts started attempts, including retries, so a failing provider cannot
bypass the window limit by repeatedly retrying. Exhausted slots advance to the
next cadence while retaining `last_outcome: FAILED_EXHAUSTED` and their attempt
history for operator review.
Shutdown stops new claims before waiting for the scheduler worker. Current
state, next due time, attempt count, last outcome, and lease state are exposed
through the project configuration and Work Loop projections. Operators may
still trigger a stage manually through the API/UI. `FAST_EXTRACT` is the
governed exception: it may
execute only through a claimed Task Frame, with Provider `CODEX`, model
`gpt-5.6-luna`, effort `MAX`, and `fallback: NONE`. CONSOLIDATE may use the
same read-only Task Frame path only as a dry-run decision preview: it validates
one KEEP/NOISE and proposal/RAG route decision per pinned extraction candidate,
records the terminal receipt and run, and creates no candidates or adoption.
The preview accepts 1..32 candidates; larger inputs require a durable model
window. It does not satisfy the completed CONSOLIDATE prerequisite for
SYNTHESIZE. Other stages and an explicitly configured `DETERMINISTIC` fallback
retain the deterministic route and report `provider_invocation: NOT_RUN`. This slice enforces total
`max_runs` and `{ "max_runs": N, "window_hours": H }` quota windows. Token
and cost budgets fail closed until Provider usage telemetry is connected.

```text
GET  /v1/settings/memory-batch/catalog
GET  /v1/projects/{project_id}/memory-batch-config
POST /v1/projects/{project_id}/memory-batch-config
GET  /v1/projects/{project_id}/memory-batches/runs
POST /v1/projects/{project_id}/memory-batches/run
```

## Redacted candidates

`FAST_EXTRACT` consumes reduced activity references and creates typed `MEMORY`
candidates. `CONSOLIDATE` deterministically deduplicates and records
`DUPLICATE_OF`, `MERGED_FROM`, `CONFLICTS_WITH`, and supersede relations.
`SYNTHESIZE` creates review-only `IDEA`, `HYPOTHESIS`, and `PRODUCT`
candidates with `DERIVED_FROM` relations from consolidated candidates marked
`KEEP`. It does not fall back to raw `FAST_EXTRACT` candidates when that stage
has no candidates. A completed (not dry-run) `CONSOLIDATE` run is required
before `SYNTHESIZE`; otherwise the stage reports `MEMORY_BATCH_UPSTREAM_REQUIRED`
instead of recording an empty success. This only checks stage completion, not
that the upstream run is current to the latest extraction.
`INDEPENDENT_CHECK` reports bounded integrity failures without changing candidate
state.

Candidate records retain only a bounded summary, source-session digest, source
range, reference digests, relations, and repetition relevance. Raw prompts,
transcripts, source text, commands, and tool arguments are rejected recursively
at the API boundary. Repetition changes relevance only; it never creates
factual authority. Candidate writes do not mutate Current Anchor, Project
facts, Seed, authority, Assignment, or source.

```text
GET  /v1/projects/{project_id}/memory-candidates?stage=&kind=&state=
POST /v1/projects/{project_id}/memory-candidates
POST /v1/memory-candidates/{candidate_id}/review
```

Only `REVIEW_REQUIRED` candidates accept `IGNORE`, `KEEP`, `EXPLORE`, or
`START_PRODUCT_DESIGN`. A second identical decision is idempotent; all other
transitions fail closed with a conflict.

`KEEP` is a review decision only. Canonical RAG adoption is a separate explicit
Action IR operation. `rag.adopt` accepts only a `MEMORY` candidate with a
recorded `KEEP` review and requires the caller to pin the current
`candidate_digest`. It creates one `OBSERVED`, `UNLINKED` project Memory whose
origin ref contains the candidate digest. Repeating the same request returns
the existing Memory as an idempotent replay. Adoption does not link a Node,
write Seed, create authority, create an Assignment, or start a Task Frame.

```text
POST /v1/actions
```

```json
{
  "action_id": "rag.adopt",
  "request": {
    "candidate_id": "memory_candidate_...",
    "expected_candidate_digest": "64-character-sha256-hex"
  }
}
```

## Collection priority — 2026-09-13

The operator's collection order supersedes the older broad reusable-lesson prompt:

1. User brainstorming notes, equivalent substantive conversational ideas and
   requirements, and explicit operational decisions with their reasons. Preserve
   proposed versus confirmed intent; assistant suggestions are not user decisions.
2. Transferable procedures, judgments and LLM experience supported by observed
   decisions/outcomes, with explicit applicability. Extract the useful procedure
   and rationale, not its surrounding file changes, commits, test counts or repair
   history. A solved incident with no continuing applicability has no active value.

Policy `reusable-knowledge-topics.v2` requires new provider output to identify
`knowledge.kind`, a concise stable `knowledge.topic`, and `knowledge.applicability`.
Kinds distinguish user ideas/requirements/decisions from reusable procedures and
experience. Metadata is bounded, retained through normalization/consolidation and
included in the candidate digest. Legacy records retain their original digests and
are not silently backfilled. Model-suggested metadata is not a value-review receipt.

FAST_EXTRACT receives transcripts, not attested current-source state. It can extract
an evidenced procedural lesson but cannot certify current implementation or remaining
recurrence paths. Generic advice and model success self-reports are insufficient.
Unimplemented user intent remains valuable. Source-currentness and knowledge value
are separate questions; a newer collection timestamp establishes neither.

The Memory view groups stored records and active candidates by topic, folds identical
claims across review states, and preserves individual provenance/history. Legacy
keyword categories are explicitly navigation suggestions, not semantic equivalence.
Current-source records remain distinguishable from recently collected unverified
records. Conflicts prevent a canonical-latest implication; there is no automatic
semantic rewrite or supersession based on date. Excluded candidates are a separate
history list and show their exclusion status before their stale source-review badge.

Session readiness, approval waiting, routine progress/success, test-response
instructions, generic agent procedure, placeholders, and repeated summaries are
excluded. Empty extraction is a valid outcome. The policy version participates in
run identity so old-policy results cannot masquerade as a fresh evaluation.
Deterministic synthesis consumes only KEEP-reviewed inputs; it must not amplify
unreviewed extraction into three new kinds. Existing candidates and memories remain
unchanged by this source update; historical cleanup is a separate review operation.

## Existing-record cleanup and collection recovery — 2026-09-13

Read-only inspection of the live store found 139 Universe candidates. Sixteen
records are selected for noise review: nine contentless count/digest synthesis
records, six concatenated cross-topic synthesis records, and one test-response
instruction. Twelve remain REVIEW_REQUIRED; four are already EXPLORE and require
an explicit reopen transition before archival. This is a selected review packet,
not a claim that live records have been archived. Other user intent and historical
implementation claims require separate evidence review. Career's 29,075 identical
candidate summaries were already IGNORE and are not rewritten.

`POST /v1/actions` with `action_id: rag.archive-candidate` accepts exactly
`project_id`, `candidate_id`, `expected_candidate_digest`, and `note`. It requires
a server-resolved USER, checks project/content identity, and uses the existing
review transition and audit history to record IGNORE. It does not delete the
candidate, auto-reopen an existing decision, or alter canonical Memory. Identical
replay returns the same revision; changed content, project, or replay reason
fails. Saved Memory cleanup remains separate from candidate archival.

Two independently observed collection failures have source fixes:

- The pending source was ACTIVE but its ownership had become UNASSIGNED. The
  project selector treated its old cursor as a fatal inactive-source error. It
  now retains that cursor in `suspended_activity_resumes`, continues eligible
  sources, and restores the exact cursor if ownership/eligibility returns.
  Changed activity evidence within an eligible source still fails closed.
- Codex producer digests include turn/bus correlation, but semantic reads omitted
  those fields. Reads now reconstruct the producer's recorded correlation after
  checking the current event's turn and explicit bus metadata. Existing activity
  records and hashes are not rewritten; actual event changes still fail.

Validation: 39 observer, source-window and archival API tests passed. A read-only
probe through the repaired consumer processed 128 live activity references,
including the exact previously failing activity, and returned semantic evidence.
This probe did not write the live database.

Live follow-up after operator restart (PID 54144): the governed archive Action
recorded IGNORE for all 12 selected REVIEW_REQUIRED candidates; API readback
confirmed their state. Four selected EXPLORE records remain unchanged pending
an explicit reopen workflow. No canonical Memory was deleted or relinked.

Default collection, without explicit source IDs, completed as
`memory_batch_run_117b89c5686ac12177860573` in approximately 127 seconds and stored
five REVIEW_REQUIRED candidates. Their cited excerpts were read back and all
were user-authored requirements or operational directions. Review is still
required before treating their wording and scope as canonical decisions.
The batch processed one bounded source window; six sources were deferred.
API readback confirmed the new cursor and retained suspended ownership cursor.
This proves one default batch, not exhaustive recollection or scheduled firing.

### Topic-view validation — 2026-09-14

Observed owner: `groupRagReviewCandidates` previously partitioned exact summaries
by kind/source bucket, while `renderMemory` separated stored memories from active
candidates. The topic projection now groups those records for navigation and folds
identical claims across review states. It does not establish semantic equivalence
or a canonical replacement. Adoption topic display follows only an exact
project/digest/candidate origin match; it does not rewrite canonical memory rows.

Validation: 26 Python tests passed (FAST_EXTRACT and candidate/delegation suites),
three JavaScript UI suites passed, syntax and Git whitespace checks passed. Test
shutdown emitted resource warnings and two remote-gateway state-file permission
warnings; successful assertions do not establish clean test-process teardown.
Live Playwright on the existing service observed 144 candidates and 71 saved
memories in six suggested topics; search, empty results and history expansion
passed with no page errors. Existing project selection completed slowly, so a
second check isolated the Memory view; both passed.

The running service PID remained 54144. Static UI changes were served live, but
new Python collection policy v2 has not been loaded or exercised by a live provider
batch in this process. The page also displayed a recent scheduled FAST_EXTRACT
failure `MEMORY_ACTIVITY_CURSOR_STALE`; its present root cause is UNKNOWN and is
not declared resolved by this topic-view change. Existing records still need
semantic/value review before consolidating different statements or selecting a
current authoritative decision. No chronological auto-supersession was performed.

## Excluding previously saved knowledge — 2026-09-14

Confirmed implementation gap: canonical `project_memory` had no retention state,
`list_project_memories` returned every saved row to retrieval and maintenance, and
the Memory detail view offered only node linking. Candidate IGNORE was restricted
to REVIEW_REQUIRED, leaving previously reviewed noise stranded.

`rag.memory-retention` is the shared human/LLM Action for saved memory exclusion
and restoration. Required request fields: project_id, memory_id,
expected_memory_digest, expected_revision (0 before a first decision), decision
(IGNORE or ACTIVE), and nonempty note (maximum 500 characters). Actor/context are
server-resolved. An append-only `project_memory_retention` table stores reason,
actor, content identity and revision. Exact retries replay; stale revisions and
changed replay reasons conflict. Original bodies, provenance and node links remain.
The detail view keeps exclusion controls available when optional node projection
loading fails; the existing structured API error is shown without replacing the
memory content or retention actions. A projection-less test project returned
PROJECT_PROJECTION_NOT_FOUND (404), exposing this previously coupled failure path.
Default memory lists, searches, planning contexts, graph projections and link
maintenance omit IGNORE before pagination. `include_ignored=true` is an explicit
review/history list; it does not change normal retrieval. The UI offers **무시 —
RAG에서 제외**, then **메모 복원** in the excluded-history section. Restore activates
the saved memory without recreating or approving its old candidate review.

`rag.archive-candidate` now accepts expected_candidate_revision for an already
reviewed candidate. The candidate review and exclusion of its exactly matched
adopted memory share one transaction. Excluding an adopted saved memory likewise
withdraws its source candidate in the same transaction, preserving review history.
Explicit candidate creation and batch insertion cannot revive the exact same
excluded summary under a new source identity. This is exact-content suppression,
not semantic matching of paraphrases or a guarantee about all manual imports.

Cleanup review packet (Universe only): 50 candidates and 37 saved memories were
selected. The initial live pass changed 43 REVIEW_REQUIRED candidates to IGNORE;
after the operator restarted the server, all seven already-reviewed candidates
and 37 saved memories were also ignored through the named Actions. Selection reasons: empty/test responses, one-time session or
approval status, completed source/release/test reports, unsupported old source
findings, and metadata-only location/status claims. Product decisions, brainstorming,
applicable procedures and mixed passages needing extraction were retained. This
is not a declaration that every remaining record has passed value review.

Validation: 39 Python tests and three JavaScript suites passed, including actual
HTTP actions, replay/conflict behavior, active-list exclusion, restoration,
adopted-source withdrawal and exact recollection suppression. A real browser against
the test HTTP server also completed exclude -> absent from retrieval -> reopen from
excluded history -> restore, including the projection-less project failure path;
zero page errors were observed. Screenshot: `.artifacts/ui/rag-ignore-dialog-20260914.png`.
Existing test-process resource warnings and a test-server request-handler shutdown
timeout remain; clean teardown is not inferred from successful assertions.

After restart, PID 17944 exposed rag.memory-retention. The remaining 44 Actions
completed. Readback verified all 50 selected candidates are IGNORE, all 37 selected
saved memories are IGNORE and absent from ordinary retrieval, and 34 of 71 saved
memories remain active. The reviewed ID/digest/reason packet remains in the Host
operation directory as `ignore-review-packet.json`; applied results and readback
are `ignore-restart-applied.json` and `ignore-restart-verification.json`.

Live browser verification passed: 34 active memories, 37 excluded records, and the
excluded detail opens with a Restore button, with zero page errors. Playwright's
initial string-evaluation wait was blocked by CSP; the successful check used CSP
bypass only in the ephemeral test browser, without altering product CSP. Screenshot:
`.artifacts/ui/rag-ignore-live-20260914.png`.

Completion evidence for this slice: outcome SUCCEEDED; source/storage/API PASS;
UI PASS on test and resident HTTP services; resident new-Action activation PASS;
live selected-record cleanup PASS (50 candidates and 37 saved memories).
Changed paths: universe_memory.py (retention events), universe_server.py (storage,
retrieval and handlers), universe_action_registry.py, universe_ui/app.js, affected
memory/UI tests and this document. No source commit or deployment is asserted.

## Direct user decision recording

An already-confirmed product or architecture decision uses the separate
`rag.record-decision` Action. The Action resolves the actor and execution
context on the server, requires a stable `decision_ref`, and requires a Node
reference. It always records one `DECISION_NOTE` with `LINKED` state, so the
decision is immediately available to bounded retrieval for that Node.

The decision reference is the logical idempotency boundary. Repeating the same
reference with the same content returns the existing Memory. Reusing it with
different content fails with a conflict; the previous decision is never
overwritten. The origin ref is deterministic and includes the project and
decision reference.

```json
{
  "action_id": "rag.record-decision",
  "request": {
    "project_id": "universe",
    "decision_ref": "universe-governance-loop",
    "title": "Universe governance loop",
    "body": "Conductor coordinates the user-approved project loop.",
    "node_ref": "universe",
    "graph": "functional"
  }
}
```

This Action writes only canonical project Memory. It does not write Seed,
authority, Assignment, Task Frame, repository source, or push state. The
generic `/memories` surface remains for brainstorms, questions, and observed
notes; `DECISION_NOTE` requests on that legacy route are rejected so confirmed
decisions cannot bypass the Action boundary.

## Governed FAST_EXTRACT

`POST /v1/projects/{project_id}/memory-batches/run` accepts a `FAST_EXTRACT`
request only when the stored configuration resolves to the exact Codex
`gpt-5.6-luna`/`MAX` ceiling. The request supplies registered `source_ids` and
a secret-free reference to an existing Host-owned session Runtime attachment
and one READY Task Frame turn. As of the 2026-09-11 follow-up, `runtime_binding`
contains `session_id`, `session_anchor_ref`, `credential_ref`, `task_frame_ref`,
`frame_id`, `turn_id`, and `invoker_actor_ref`; `task_frame_ref` must equal
`frame_id`. Read `credential_ref` from the session-start attachment result.
The server checks the current live session, project, anchor and exact attachment,
then resolves its transient endpoint/token internally. No global Conductor
binding fallback or new attachment is created by a batch request. Old callers
that send `runtime_binding.token` must migrate; that wire shape remains rejected
by the Action credential gate. The request cannot assert a
claim, Worker identity, Worker run, or result receipt. The Host dispatcher owns
capability planning, claim, ephemeral Worker creation, and terminal result
recording. The session observer owns a durable per-source byte offset and event
ordinal. The durable provider boundary projects the selected source into
`universe.provider-activity-batch-redacted.v1`, retaining only source/session
identity, cursor, activity identifiers, event kinds, states, timestamps, and
SHA-256 digests. Transcript text, prompts, source text, commands, tool
arguments, secrets, and hidden reasoning are never persisted.

For extraction only, the observer reopens the exact registered Codex JSONL
events at their attested byte offsets, verifies their Activity digests, and
builds a bounded transient user/assistant excerpt set. Secret-like values are
redacted before dispatch. The excerpt text is available only in the ephemeral
Worker context; the run identity and durable records retain its digest, not its
content. Provider summaries that contain secret-like values or copy a long
verbatim transcript span are rejected.

The provider receives one structured request with `repository_write_scope:
NONE`, an empty mutation scope, and a redacted Activity context pack. Its
structured result contract fixes every `FAST_EXTRACT` candidate kind to
`MEMORY`; only later synthesis stages may propose `IDEA`, `HYPOTHESIS`, or
`PRODUCT`. The result is normalized into a `REVIEW_REQUIRED` Memory Candidate;
there is no automatic Memory, Seed, Anchor, authority, Assignment, or source
mutation. A completed run also records one redacted
`ai-career.skill-observation-candidate.v1` with a result-receipt evidence
reference. The existing `skill_run_observation` table and
`GET /v1/bench/skills` expose that bounded observation to Bench comparisons.

The run identity is derived from project, stage, resolved configuration,
redacted Activity digests, and transient semantic-input digests. A duplicate
completed identity is returned without invoking the Provider again; a
concurrent `RUNNING` identity fails closed; a `FAILED` identity may be retried
and increments its attempt counter. Candidate persistence, the redacted
SkillRunObservation, and run completion share one SQLite transaction. Provider
request credentials and semantic excerpts remain transient, and persisted
execution records contain only bounded model, Worker, Task Frame, receipt,
digest, attempt, and status references.

Failed governed attempts now trigger review-only failure recall using
`component=memory_batch`, `operation=fast_extract`, the reported error code and
`execution_plane=governed_task_frame`. Before an explicitly requested retry,
recall is refreshed against current candidate review states and added to the
Worker context as reference material, not extraction source or authority.
The append-only attempt evidence and terminal run transition share one SQLite
transaction, preserving the original failure after a retry succeeds.
`GET /v1/projects/{project_id}/failure-reuse/batch-attempts?run_id=...` returns
the latest 20 immutable attempt records and a truncation flag. Existing historical
runs are not retroactively fabricated into attempt records.
Successful retry means the batch operation completed; remedy application and
causal resolution remain `UNKNOWN` until separately evidenced. This flow neither
auto-retries nor auto-patches. See [failure-reuse-rag.md](failure-reuse-rag.md).

The integration suite exercises the real Universe Runtime Host and dispatcher
with a fake provider process. A billable live Codex/Luna extraction over a
registered real transcript also completed on 2026-08-10. The first attempt was
correctly rejected because the provider returned descriptive values outside the
candidate kind contract. After constraining the structured schema to `MEMORY`,
the retry completed under the same run identity as attempt 2, created two
`REVIEW_REQUIRED` candidates, persisted no raw transcript fields, and produced
one `universe.memory.fast-extract` SkillRunObservation visible in Bench.

The observer permits one complete JSONL event to exceed the per-scan byte
budget, up to the fixed 4 MiB single-event ceiling, so a large provider event
cannot stall the cursor indefinitely. Events above that hard ceiling fail
closed as `SOURCE_EVENT_TOO_LARGE`.

## Non-blocking Conductor delegation

Delegation stores a bounded summary, project, Worker role, optional Task Frame
reference, provider/model request, progress summary, and result summary. It
does not store a chat transcript or replay one on recovery. The delegation
worker is separate from the ordinary Conductor room worker, so an active
Project Master or Task Frame delegation does not occupy the Conductor chat
queue.

```text
POST /v1/conductor/delegations
GET  /v1/conductor/delegations?project_id=&state=
GET  /v1/conductor/delegations/{delegation_id}
POST /v1/conductor/delegations/{delegation_id}/progress
POST /v1/conductor/delegations/{delegation_id}/result
POST /v1/conductor/delegations/{delegation_id}/fail
```

`RUNNING` delegations are recovered as `QUEUED` on service restart and resume
from the bounded state record. Conductor remains the coordinator. The default
`PROJECT_MASTER` route sends only the bounded summary to the resident Project
Master and completes from the resulting Project Room reference, without
copying the reply into the delegation record. Boss/Worker roles require an
approved Task Frame executor and fail closed when one is not installed.

## API

```text
POST /v1/projects/{project_id}/memories
GET  /v1/projects/{project_id}/memories?link_state=&node_ref=&q=
POST /v1/projects/{project_id}/memories/link
GET  /v1/projects/{project_id}/memories/propose-links
POST /v1/projects/{project_id}/memories/maintain
POST /v1/session-observer/sources/{source_id}/record-memory
```

Create body:

```json
{
  "title": "optional",
  "body": "note text",
  "state": "BRAINSTORM|OBSERVED|QUESTION",
  "node_ref": "optional-node-id",
  "graph": "functional|implementation"
}
```

If `node_ref` is omitted, `link_state` is `UNLINKED`.
Use `POST /v1/actions` with `rag.record-decision` for a confirmed
`DECISION_NOTE`.

## Provider activity memory

The Activity panel can record one reviewed Provider batch into the currently
selected Project as an `OBSERVED`, `UNLINKED` memory. The server stores only
the provider/session identity, count of reduced activity references, and a
batch origin reference. It excludes transcript, prompts, responses, and tool
commands. The operator then uses the normal node-link flow.

`record-memory` is local-operator only. It is idempotent per
project/source/batch and never creates a Skill observation, Bench row,
Experience Case, Future projection, Candidate, or Career promotion.

## Propose-links

`GET .../memories/propose-links` runs a **deterministic token-overlap** scorer
against the current Project Projection nodes. It never writes Seed or links
automatically. UI may apply a proposal as `PROPOSED` or `LINKED` after user
action.

Nightly LLM scoring remains a later provider adapter. This slice ships the
non-LLM proposal helper, deterministic maintain batch, and a service-callable
redacted nightly batch contract whose sink receives only proposal records and
digests.

## Maintain batch (deterministic nightly stub)

`POST .../memories/maintain` runs the same token-overlap scorer as propose-links
and may optionally apply the top proposal per memory as `PROPOSED` only.

```json
{
  "apply_proposals": false,
  "limit": 20,
  "per_memory": 1,
  "min_score": 1
}
```

Response always reports:

```text
batch_kind: DETERMINISTIC_TOKEN_OVERLAP
llm_batch: NOT_RUN
effects.seed_write: NONE
effects.auto_linked: false
```

It never auto-`LINKED`, never writes Seed, and never creates a Candidate from
the legacy maintain route. The configurable Memory batch route is the separate
redacted candidate pipeline described above.

`run_nightly_memory_rag_batch()` adds a credential-free service boundary for
scheduled runs. It hashes the Memory/Node sets and source reference, omits raw
prompts, source, commands, and sink details from returned evidence, and emits
proposal-only records. The current scorer is deterministic or heuristic; no
provider call is implied.

## Bench compare

`GET /v1/bench/compare?group_by=skill|model|provider|project&limit=50` returns
aggregate success rates, outcome counts, and average duration for redacted Skill
observations. It is review-only and does not promote Career patterns.


## UI

Inspector **Memory** tab:

- add note (auto-links when a graph node is selected)
- unlinked list + Link to selected node
- refresh / apply deterministic proposals
- node-scoped linked memory list
- configure and run the four Memory batch stages
- filter candidates by stage, kind, and review state
- review candidate provenance summaries and record bounded decisions
- next-work bundles on the existing Memory and Work Loop surfaces group review items by duplicate/stale/conflict/related Feature, show API page limits, and point at an existing Todo or Proposed Nodes without auto-adopting RAG or starting a Goal
- `START_PRODUCT_DESIGN` / `EXPLORE` review generates Proposed Nodes as `USER_REVIEW_ONLY` and returns the same review-inbox projection

Inspector **Future** tab aggregates Seed structure, Bench/Experience counts,
Memory, and Master handoffs for a single planning surface.

## In-process maintain worker

The local `serve` process owns a background worker:

```text
GET  /v1/settings/service
POST /v1/settings/service
  body: { "memory_maintain": { "interval_hours": 0 } }
```

- `interval_hours = 0` (default): worker idle (rechecks ~30s)
- `interval_hours > 0`: runs HEURISTIC maintain for each connected project on that period, applying PROPOSED only
- UI: Settings → Memory maintain interval (hours)

## Collector coverage and related work (2026-09-11 reconciliation)

`UniverseStore.record_semantic_collection_observation` stores a project/source
cursor with the latest event id/type, source digest and observation time. Its
upsert establishes a latest-source coordinate; it is not, by itself, evidence
of exhaustive collection, ordered event replay, crash recovery or extraction
coverage across sessions, rooms, Task Frames, files, tests, commits and research.
The global Collector Todo (`todo_89efd4a14c884386855ae58a391908f4`) retains those
acceptance checks. Inventory each source -> cursor -> Memory/Bench projection
before claiming global automation is complete.

| Existing Todo | Owning reference and remaining boundary |
| --- | --- |
| `todo_89efd4a14c884386855ae58a391908f4` | This document: collection coverage, lineage and recovery |
| `todo_c4836d989cab441da55c1309f5545e40` | [Work Loop](work-loop-prediction.md): review inbox -> next work; live UI remains unverified |
| `todo_memo_document_attach_to_existing_node_v1` | [Memory-to-Feature](memory-to-feature-automation.md): existing ATTACH, historical notes and next Planning Context |
| `todo_node_planning_context_meeting_auto_v1` | [Memory-to-Feature](memory-to-feature-automation.md): actual meeting input and session/quota/retry checks |
| `todo_prediction_paths_bound_to_feature_node_v1` | [Memory-to-Feature](memory-to-feature-automation.md): node-bound prediction provenance |
| `todo_prediction_versus_outcome_calibration_v1` | [Work Loop](work-loop-prediction.md): implemented calibration, live result attribution still to verify |
| `todo_c272adb801ac45348d0315e36c2d07c6` | [Failure reuse](failure-reuse-rag.md): governed batch capture/recall/retry evidence and separately recorded deployment limits |

The status at the top describes the original product slice, not proof that every
later addition has been applied to the running service. In particular, consult
the dated failure-reuse follow-up for its resident/provider NOT_RUN boundary.
No additional Todo is needed for the gaps above: they already have owners.

## 2026-09-12 자동화 상태 정정

예약 배치와 지금 실행은 Host가 배치 전용 Runtime/Task Frame을 준비하고 종료하는 공통 경로를 사용한다. 사용자가 열린 프로젝트 세션을 지정할 필요가 없다. 기본 입력도 분할 수집하며 성공 후 다음 소스 위치를 저장한다. 실제 Provider 호출과 한글 후보 3개 생성을 검증했다. 예약 시각 자동 발화 및 사용자 검토·채택·연결 전체 live 검증은 별도로 남아 있다. 현재 구현·증거·한계는 [RAG 자동화 점검](rag-automation-status-20260912.md)을 참조한다.
