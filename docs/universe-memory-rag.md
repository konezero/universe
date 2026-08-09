# Universe Memory RAG (product slice)

Status: implemented (deterministic 1st slice)  
Scope: project-local memory notes, operator-selected provider activity batches,
node link/unlink, search, propose-links
Not: Candidate creation, Seed mutation, automatic Bench/Future promotion,
nightly LLM batch, Career promotion

## Invariant

```text
Node Memory = reference context
MEMORY_SYNC != Candidate
MEMORY_SYNC != Seed write
MEMORY_SYNC != Task Frame / authority
Provider activity batch -> operator-selected project Memory -> node review/link
```

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

It never auto-`LINKED`, never writes Seed, and never creates Candidates. A later
nightly LLM batch may replace scoring while keeping the same apply boundary.

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
