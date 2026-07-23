# Project Projection Contract

Universe connects projects by reference. It does not copy project source,
original documents, test results, or Runtime databases into its own storage.

## Ownership

| Plane | Canonical ownership |
|---|---|
| Career | Reusable governance and adopted common patterns |
| Universe | Universe Seed, Project Projection, cross-project relationships, predicted path candidates |
| Project | Source, tests, original documents, and adopted result documents |

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
  -> PROJECT_SEED_RECORDED
  -> PROJECT_PROJECTION_BUILT
  -> INCORPORATION_PROPOSAL_READY
  -> USER_APPROVAL_AND_PROJECT_MUTATION
```

`PROJECT_SEED_RECORDED` validates every submitted source and document
reference against the registered Project root. It stores metadata and
digests, not file contents.

`PROJECT_PROJECTION_BUILT` creates the current node, edge, and document map.
It also returns deterministic missing-connection candidates. These candidates
require user selection and create no Authority, Assignment, or Project write.
A new current Project Seed makes the prior Projection stale until rebuilt.

`INCORPORATION_PROPOSAL_READY` proposes where Project documents would live
under the Project-owned `docs/universe/` structure:

```text
docs/universe/
  nodes/<node-id>/<role>/
  connections/
  decisions/
  evidence/
  reference/
```

The proposal does not create directories or move files. The Project executes
an approved proposal through its own Assignment, Guard, validation, and commit
flow. Existing documents already under `docs/universe/` are retained.

## Project Seed

A Project Seed contains:

- a Project-owned ID;
- an immutable source reference and commit;
- project kind, technology signals, and goal;
- system nodes with source references;
- edges with optional contract-document references;
- documents with roles and node links.

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
MCP, automatic project discovery, automatic document movement, or Project
source editing.
