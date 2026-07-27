# Project Projection Contract

Universe connects projects by reference. It does not copy project source,
legacy documents, test results, or Runtime databases into its own storage.

## Ownership

| Plane | Canonical ownership |
|---|---|
| Career | Reusable governance and adopted common patterns |
| Universe | Universe Seed, Project Projection, cross-project relationships, predicted path candidates |
| Project | Source, tests, and canonical `.ai/universe/` Seed assets |

References bind a Project ID, immutable source commit, repository-relative
path, optional symbol, and SHA-256 digest. Raw file contents remain in the
Project.

The initial local service records the source commit as
`PROJECT_SUBMITTED`; it does not execute Git commands inside the attached
Project to attest that commit. File digests are verified directly against the
registered local root.

## State Flow

```text
PROJECT_REGISTERED
  -> PROJECT_DISCOVERY_DISPATCH_QUEUED
  -> Project Master publishes `.ai/universe/` Seed assets
  -> PROJECT_SEED_ASSETS_SYNCED
  -> PROJECT_PROJECTION_BUILT
  -> INCORPORATION_PROPOSAL_READY
  -> USER_APPROVAL_AND_PROJECT_MUTATION
```

`PROJECT_SEED_ASSETS_SYNCED` verifies the Project Master-published
`.ai/universe/manifest.json` and its graph assets against the registered
Project root. Universe stores an indexed copy and digests, not raw source
content.

`PROJECT_PROJECTION_BUILT` creates the current node, edge, and document map.
It also returns deterministic missing-connection candidates. These candidates
require user selection and create no Authority, Assignment, or Project write.
A new current Project Seed makes the prior Projection stale until rebuilt.

`INCORPORATION_PROPOSAL_READY` proposes where a Project Master would derive
canonical Universe-facing documents under the Project-owned `.ai/universe/`
structure:

```text
.ai/universe/
  documents/nodes/<node-id>/<role>/
  documents/connections/
  documents/decisions/
  documents/evidence/
  documents/reference/
```

The proposal does not create directories or move files. The Project executes
an approved proposal through its own Assignment, Guard, validation, and commit
flow. Existing canonical documents under `.ai/universe/` are retained.

## Project Seed

A Project Seed contains:

- a Project-owned ID;
- an immutable source reference and commit;
- project kind, technology signals, goal, optional summary, and optional
  working-reference rules;
- functional nodes with source references;
- functional edges with optional contract-document references;
- implementation nodes for packages, modules, classes, services, adapters, and
  endpoints;
- many-to-many functional-to-implementation bindings;
- documents with a title, role, and functional-node links.

`working_rules` are Project Seed reference context for planning and LLM work.
They do not grant authority, replace Project policy, or allow source mutation.
Functional nodes and implementation nodes are separate planes. A binding is
evidence that an implementation realizes, supports, adapts, or exposes a
functional node; it is not a claim that either plane owns the other.

Document roles include `ARCHITECTURE`, `DESIGN`, `SPECIFICATION`, `CONTRACT`,
`POLICY`, `DECISION`, `CHANGELOG`, `EVIDENCE`, and `REFERENCE`. A document with
`project_wide: true` belongs to the main Project node and cannot also name a
component. An empty `node_ids` array without that marker remains an unmapped
document candidate. The Project node can therefore retain project-wide design,
specification, policy, decision, and change references without assigning them
to an unrelated component.

Only repository-relative regular-file references are accepted. Parent
traversal, root escape, missing files, symlinks, and digest mismatches are
rejected.

## Projection Boundary

Projection is a current Universe view derived from one exact Project Seed. It
may identify:

- disconnected nodes;
- edges without contract references;
- documents not mapped to a node.

The initial predictor turns those findings into user-selectable structural
path candidates. It does not claim probabilities, learned outcomes, or source
mutation.

## Non-Goals

This contract does not implement WebGL, remote synchronization, OAuth, P2P,
MCP, automatic document movement, or Project source editing. Project discovery
is Master-owned: Universe queues the read-only request and reads a published
`.ai/universe/` bundle after the Project Master completes it.
