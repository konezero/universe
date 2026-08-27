# Memory-to-Feature Proposal and Goal Automation

Status: approved Goal direction; first review-only vertical slice implemented and locally validated
Owner: Universe Conductor for proposal coordination; Project Master for implementation
Scope: project-local conversation, Memory, semantic graph, Feature Node planning, and governed Goal automation

## Product outcome

Universe turns ordinary Conductor conversation and accumulated project Memory into
reviewable product direction without skipping the user's decision boundary:

```text
Conductor conversation
  -> Working Memory and evidence
  -> semantic clustering and deduplication
  -> existing-node link or Proposed Feature Node
  -> user explores the proposal
  -> Feature Node plus Meeting Room
  -> multiple Expected Path candidates
  -> user selects a path and starts the Goal
  -> Conductor plans and replans within the approved Goal
  -> Project Master decomposes and executes through Task Frames
  -> Activity, evidence, result, and Memory candidates feed the next loop
```

The durable order remains Feature Node before Goal and Todo. A proposal does not
become a Feature Node merely because a model produced it, and a Feature Node does
not create execution authority.

## Existing foundations

Universe already provides:

- project-local Memory and Memory Candidate ingestion, review, retrieval, and graph projection;
- review-only Work Loop predictions;
- durable Meeting Rooms with multiple verified provider sessions;
- Feature Nodes, revision-pinned Expected Paths, and explicit path adoption;
- structured Goal Work Plan alternatives, adoption, and deterministic application;
- Project Master handoff lineage and Task Frame binding;
- bounded Goal automation projection and a receipt-backed scheduler.

The missing product boundary is the orchestration between those foundations.
Memory Candidate `START_PRODUCT_DESIGN` and Work Loop `KEEP` currently terminate as
review states; neither produces a reviewable Feature Node proposal. Feature Node
creation begins from a manual Meeting Room form, and the Goal scheduler deliberately
stops before Task Frame execution, Todo selection, and result application.

## Canonical entities

### Semantic Cluster

A project-local, rebuildable grouping of related intent and evidence.

Required fields:

```text
cluster_id
project_id
title
summary
member_refs
evidence_kinds
conflict_refs
superseded_refs
cluster_digest
created_at / updated_at
```

A cluster is evidence organization, not a fact, Memory adoption, or product decision.

### Feature Node Proposal

A durable review object that either proposes a new Feature Node or an attachment to
an existing functional node.

```text
proposal_id
project_id
proposal_kind: NEW_FEATURE | LINK_EXISTING
title
intent_text
target_node_ref
cluster_refs
evidence_refs
constraints
confidence
proposal_digest
state: PROPOSAL_ONLY | EXPLORE | ADOPTED | REJECTED | SUPERSEDED
reviewed_by / reviewed_at / rationale
```

The proposal digest binds the exact title, intent, target, evidence, and constraints.
Review is per proposal, never one decision over an unrelated batch.

### Node Planning Context

Before a Meeting run, Universe builds one bounded context from:

- linked and explicitly selected Memory;
- proposal evidence, conflicts, and supersession state;
- neighboring functional and implementation graph nodes;
- current project constraints and selected technology signals;
- applicable Bench observations and failure cases;
- relevant document, commit, and result references.

The context stores references and a digest. Raw transcripts and full provider results
remain outside the durable context.

### Expected Path v2

Expected Paths keep their revision-pinned `SPECIFICATION` artifact and add a redacted,
structured route projection:

```text
steps
dependencies
branches
architecture decisions
implementation phases
risks and mitigations
acceptance conditions
effort / cost / quota estimates
evidence refs
```

These route elements become projection-only graph nodes and edges. They allow the
Galaxy to draw predicted routes and compare them with the path actually taken.

### Goal Start Receipt

Selecting an Expected Path and starting its Goal is the main implementation approval.
The receipt pins:

```text
feature_id and feature revision
expected_path_id and specification digest
approved project and node scope
constraints and exclusions
local mutation boundary
validation requirements
local commit policy
push policy: USER_APPROVAL_REQUIRED
```

Within that boundary the Conductor may create and revise plans, allocate sessions,
request Project Master work, create governed Task Frames, run tests, recover failures,
and produce local commits. Scope expansion stops for user direction.

## Human decision boundaries

1. `feature.explore`
   - Materializes an approved proposal as a Feature Node and may open its Meeting Room.
   - Creates no Goal, Todo, Task Frame, authority, or execution assignment.

2. `goal.start`
   - Selects one Expected Path and authorizes bounded local implementation.
   - Replaces repeated user gates for Work Plan adoption/application when those choices
     stay within the approved Goal scope; the Conductor records those decisions.

3. `rag.adopt`
   - Promotes selected candidate knowledge to canonical reusable RAG.
   - Product exploration does not imply canonical knowledge adoption.

4. `repository.push`
   - Publishes exact local commits to a remote repository.

## Automation state machine

```text
DISCOVERING
  -> AWAITING_FEATURE_REVIEW
  -> EXPLORING_PATHS
  -> AWAITING_GOAL_START
  -> PLANNING
  -> AWAITING_MASTER_FRAME
  -> EXECUTING
  -> VALIDATING
  -> COMMITTING_LOCAL
  -> COMPLETED
```

Stop states include insufficient evidence, conflicting proposal, quota, missing exact
session, missing Task Frame lineage, scope expansion, validation failure, and user
intervention. Every stop exposes a stable code and next permitted action.

## First vertical slice

The first implementation slice is deliberately review-only:

1. Build deterministic Feature Node proposals from selected project Memory and Work
   Loop evidence.
2. Store each proposal with exact evidence and digest provenance.
3. Expose list, generate, and per-proposal review APIs.
4. Project proposals as `FEATURE_NODE_PROPOSAL` ghost nodes in the semantic graph.
5. Add a thin Proposed Nodes Inbox in the current Project Inspector.
6. On `EXPLORE`, create no Feature Node yet; the next slice will add the explicit
   receipt-backed materialization action and Meeting Room creation.

This slice never creates Goal, Todo, authority, execution assignment, Task Frame, or
canonical RAG knowledge.

## Implementation sequence

1. P0 Proposal Compiler and Proposed Nodes Inbox.
2. P0 Node Planning Context and Meeting Room auto-preparation.
3. P0 Expected Path v2 route graph.
4. P0 combined path selection and Goal Start receipt.
5. P0 Project Master execution driver over existing guarded transitions.
6. P1 prediction-versus-outcome calibration and Fleet/Activity projection.
7. P1 Fresh Project Wizard convergence on the same proposal and Goal contracts.

## Acceptance

- Repeated generation over unchanged evidence returns the same proposal IDs.
- A proposal is attributable to exact project-local evidence and never embeds raw transcript text.
- Existing-node link and new-node proposals are distinguishable.
- User review is per proposal and idempotent.
- No proposal review creates a Feature Node, Goal, Todo, Task Frame, authority, Assignment, or RAG adoption.
- The semantic graph and UI expose proposal state and evidence count.
- Stale, conflicted, or superseded evidence is visible and does not silently become authority.
- Focused storage, API, graph, and UI contract tests pass.
