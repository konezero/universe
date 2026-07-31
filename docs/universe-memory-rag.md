# Universe Memory RAG (product slice)

Status: implemented (deterministic 1st slice)  
Scope: project-local memory notes, node link/unlink, search, propose-links  
Not: Candidate creation, Seed mutation, nightly LLM batch, Career promotion

## Invariant

```text
Node Memory = reference context
MEMORY_SYNC != Candidate
MEMORY_SYNC != Seed write
MEMORY_SYNC != Task Frame / authority
```

## API

```text
POST /v1/projects/{project_id}/memories
GET  /v1/projects/{project_id}/memories?link_state=&node_ref=&q=
POST /v1/projects/{project_id}/memories/link
GET  /v1/projects/{project_id}/memories/propose-links
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

## Propose-links

`GET .../memories/propose-links` runs a **deterministic token-overlap** scorer
against the current Project Projection nodes. It never writes Seed or links
automatically. UI may apply a proposal as `PROPOSED` or `LINKED` after user
action.

Nightly LLM maintenance remains a later batch; this slice only ships the
non-LLM proposal helper.

## UI

Inspector **Memory** tab:

- add note (auto-links when a graph node is selected)
- unlinked list + Link to selected node
- refresh / apply deterministic proposals
- node-scoped linked memory list

Inspector **Future** tab aggregates Seed structure, Bench/Experience counts,
Memory, and Master handoffs for a single planning surface.
