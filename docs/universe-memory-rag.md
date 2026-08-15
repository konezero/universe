# Universe Memory RAG (product slice)

Status: implemented and live-probed (governed FAST_EXTRACT adapter plus deterministic Memory RAG)
Scope: project-local memory notes, configurable redacted batch stages,
governed Codex extraction, candidate review, SkillRunObservation/Bench evidence,
node link/unlink, search, and propose-links
Not: Candidate auto-adoption, Seed mutation, automatic Bench/Future promotion,
Career promotion, or raw transcript storage

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
`gpt-5.6-luna`, effort `MAX`, and `fallback: NONE`. The remaining stages and
an explicitly configured `DETERMINISTIC` fallback retain the deterministic
route and report `provider_invocation: NOT_RUN`. This slice enforces total
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
candidates with `DERIVED_FROM` relations. `INDEPENDENT_CHECK` reports bounded
integrity failures without changing candidate state.

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

## Governed FAST_EXTRACT

`POST /v1/projects/{project_id}/memory-batches/run` accepts a `FAST_EXTRACT`
request only when the stored configuration resolves to the exact Codex
`gpt-5.6-luna`/`MAX` ceiling. The request supplies registered `source_ids` and
a loopback Runtime binding to one READY Task Frame turn. It cannot assert a
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
  "state": "BRAINSTORM|OBSERVED|QUESTION|DECISION_NOTE",
  "node_ref": "optional-node-id",
  "graph": "functional|implementation"
}
```

If `node_ref` is omitted, `link_state` is `UNLINKED`.

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
