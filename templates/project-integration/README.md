# Universe Project Integration Templates

Status: canonical project-integration template catalog

These templates define how a product repository joins Universe. They are not a
Career Runtime package and do not grant Project execution authority.

## Catalog

| Template | Installed or tracked target | Purpose |
|---|---|---|
| `project-binding.example.json` | `.universe/project.json` | Small tracked identity and installation-mode binding |
| `TODO_TRACKING_POLICY.md` | `.ai/universe/TODO_TRACKING_POLICY.md` | Universe host queue boundary |
| `universe-connection.md` | local Universe connection metadata | Redacted Project-to-Universe publication boundary |
| `node-memory.md` | `.ai/memory/universe_nodes/` | Node-attached local memory contract |

## Ownership

Universe owns this catalog and versions it with the project-attachment flow.
Career produces the Runtime Release DB used to materialize a local `.ai/`
instance. Rendezvous owns remote route discovery and approval. See
`docs/universe-product-suite.md`.

## Installation invariant

```text
tracked: .universe/project.json
local:   .ai/
```

An attached Project must not track its installed `.ai/` workspace. A migration
removes `.ai/` from the Git index without deleting local files.

## Compatibility transition

Until the Universe installer consumes this catalog directly, the equivalent
Career Release payload remains an installation compatibility mirror. New
project-integration policy changes start here and are mirrored deliberately;
they are not independently authored in Career.

## Proposal API

The local Universe service exposes the catalog at `GET /v1/project-templates`.
For a registered Project, `GET /v1/projects/<project_id>/integration-template-proposal`
returns exact, digest-bound target assets before any write starts. The response
separates the tracked `.universe/` asset from local `.ai/` assets and remains
`NOT_STARTED` until the Project Lifecycle Host receives the applicable approval.
