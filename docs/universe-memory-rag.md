# Universe Memory RAG (product slice)

Status: implemented foundation (deterministic Memory RAG plus review-only batch candidates)
Scope: project-local memory notes, configurable redacted batch stages,
candidate review, node link/unlink, search, and propose-links
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

The current product slice persists and validates schedule policy, but does not
yet run these four stages from a wall-clock scheduler. Stage execution is
manual through the API/UI. The configured Provider, model, and effort gate
whether a stage may run and are recorded in the run contract; candidate
generation itself remains deterministic until the Task Frame Provider adapter
is connected. The service must not claim that a Provider model generated a
candidate in this state. A run therefore requires `fallback: DETERMINISTIC`
and reports `provider_invocation: NOT_RUN`. This slice enforces `max_runs`;
token, cost, and window budgets fail closed until Provider usage telemetry is
connected.

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
