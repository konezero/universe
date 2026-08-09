---
name: universe-project-seed
description: Prepare and assemble a Project-owned Universe Seed asset set from an approved Master handoff or bounded static discovery.
---

# Universe Project Seed Assembly

Invocation class: `PROJECT_GOVERNANCE_MUTATION`

This Skill is available only to an installed Project's registered `MASTER`
Mode. It turns an approved Universe Composition handoff or a bounded
Project-local discovery request into a proposal for the Project-owned
`.ai/universe/` Seed assets.

The canonical asset contract is installed at:

```text
.ai/templates/universe_project_seed/README.md
.ai/templates/universe_project_seed/TODO_TRACKING_POLICY.md
```

`TODO_TRACKING_POLICY.md` is a **policy plant** (not one of the five Seed digest
payloads). On OS_INSTALL / project-runtime distribution it ships with the
`universe_project_seed` template and should land at:

```text
.ai/universe/TODO_TRACKING_POLICY.md
```

Rule: Universe `project_todo` is the **host** operational/execution queue for
this attach — not a replacement for the project’s own product work board.
Docs are references only; see `TODO_TRACKING_POLICY.md`.

## Inputs

Accept only one of the following source-backed inputs:

```text
1. approved `universe.project-master-handoff.v1` delivery; or
2. approved Project-local discovery Task Assignment.
```

An arbitrary chat instruction, a Universe route suggestion, or an existing
directory is not an assembly approval.

## Read-Only Discovery

Before creating a proposal:

1. Read the active Project Mode Registry and confirm `MASTER`.
2. Read the selected handoff or Task Assignment and preserve its source and
   selection references.
3. Inspect only the approved project source and document scope.
4. Keep functional nodes, implementation nodes, and their evidence bindings
   distinct.
5. List only project documents that actually exist and can be linked to a
   functional node or declared project-wide role.
6. Produce a five-file Project Seed proposal with expected file digests.

When a connected Universe returns
`universe.project-seed-asset-proposal.v1`, it is a prepared exact-byte
proposal only. Verify the proposal's `seed_digest`, `proposal_digest`, target
paths, and per-asset SHA-256 values before presenting it for approval. Do not
let a Universe response bypass Project Master discovery, approval, or the
receipt-aware write path.

Discovery is static. Do not import, execute, test, install, or start project
code during this Skill.

## Proposal Shape

The proposal must identify:

```text
target_root: .ai/universe/
assets:
  - manifest.json
  - functional-graph.json
  - implementation-graph.json
  - bindings.json
  - documents.json
source:
  handoff_ref or task_assignment_ref
selection_ref:
  approved selection or assignment reference
effects:
  project_source_write: NONE
  project_runtime_state_write: PROPOSED
  universe_publish: NONE
  career_promotion: NONE
  task_frame: NONE
```

The proposal is not a write receipt. It must include unresolved or absent
document and implementation links rather than inventing them.

## Apply

After exact User approval:

1. Bind the five target files as one Project Runtime state write scope.
2. Run the normal Execution Guard and use the receipt-aware write path.
3. Write the complete asset set under `.ai/universe/`.
4. Validate that `manifest.json` binds all four payload digests and that each
   referenced project document exists in the approved discovery scope.
5. Report the published Seed manifest reference and source/selection evidence.

Use one receipt-aware write operation per exact asset unless the Host exposes a
verified atomic Project Runtime state batch gateway. A prepared proposal does
not itself create a receipt or grant a batch-write capability.

The Skill does not create a Universe connection, send an HTTP request, append
an archive, modify application source, commit, push, or update Career.

## Failure States

```text
MODE_NOT_REGISTERED
MASTER_MODE_REQUIRED
UNIVERSE_SEED_INPUT_UNAPPROVED
UNIVERSE_SEED_DISCOVERY_SCOPE_UNAVAILABLE
UNIVERSE_SEED_PROPOSAL_REQUIRED
UNIVERSE_SEED_WRITE_SCOPE_REQUIRED
UNIVERSE_SEED_VALIDATION_FAILED
```
