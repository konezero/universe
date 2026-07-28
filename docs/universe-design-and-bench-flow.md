# Universe Design and Bench Flow

Status: IMPLEMENTATION_DIRECTION
Scope: Universe-side design, Bench, and project handoff contract

## Purpose

Universe reduces avoidable project failure by helping a user design a complete
project before implementation, proposing appropriate routes from curated and
observed Bench data, and returning only task-relevant context and Skills to an
LLM. It is not a source executor, an authority service, or a replacement for a
Project Master.

## Product Objectives

Universe is complete only when these three objectives work together.

1. Project design: turn a user's goal into an inspectable project composition,
   including functional, implementation, document, operating, and completion
   nodes.
2. Seed-based proposal: use curated Seeds and Bench evidence to propose
   suitable stacks, routes, Skills, dependencies, and risk conditions.
3. LLM efficiency: return a bounded Context Pack and Skill Plan for the
   current node instead of repeatedly loading unrelated repository material.

## Ownership Boundaries

```text
Universe
  owns Bench aggregation, Context Pack assembly, project composition,
  selectable future routes, and cross-project comparison.

Project
  owns source, documents, tests, Task Frames, execution, and raw evidence.

Project Master
  owns implementation planning, Skill binding for Task Frames, validation,
  and completion evidence.

Career
  separately owns reusable governance adoption. Universe does not directly
  promote a Bench observation into a Career rule.
```

Universe must not mutate Project source, create execution authority, or treat
a room message, Skill Plan, or Bench result as execution permission.

## Fresh Project Flow

A Fresh Project starts with minimum user intent rather than a prescribed stack.
The user supplies enough information to bound discovery: purpose, target users,
problem, key constraints, and optional desired technologies.

```text
minimum intent
  -> Universe Bench selection
  -> bounded Context Pack
  -> LLM proposal for specification, design, stack, and route
  -> user adopts or changes meaningful choices
  -> selected composition becomes the Project Seed
  -> Project Master receives the implementation handoff
```

The proposed composition includes functional capabilities and acceptance
conditions; design and UX direction; data, API, authentication, integration,
test, release, and operating nodes; technology alternatives; document
requirements; dependencies; branches; and completion conditions. A selected
route is a design coordinate, not an execution assignment.

## Bench and Context Pack

Universe is the durable Bench owner. A Project consumes a small Context Pack;
it does not maintain a separate long-lived global Bench database. A Bench entry
records source provenance and applicability rather than an unqualified
recommendation:

```text
reference and digest
technology / Skill / model version
project domain and stack conditions
observed quality, rework, duration, token or cost signals
validation and evidence references
redaction state and collection time
```

Context Pack assembly selects only entries relevant to the current project
node, proposed action, constraints, and selected stack. It must state when no
applicable evidence exists and must not invent probability or performance
claims.

## Skill Plan

The user chooses project direction, not every individual Skill. Universe and
an LLM may propose a bounded Skill Plan from the Context Pack.

```text
purpose and project node
  -> candidate Skills with contracts and Bench rationale
  -> user adopts or changes the plan
  -> Project Master binds selected Skills to Task Frames
```

Skill Catalog records include `skill_id`, version, input/output contract,
applicable domains and stacks, operation class, evidence requirements, and
Bench history. A result is conditional on task type, model, context, and Host
conditions; it is never a universal ranking. A Task Worker runs only Skills
bound by its Project Master and may request a replacement only as a candidate.

## Project Master Handoff

After user adoption, Universe produces a Project Seed and implementation
handoff. The Project Master decomposes the selected route into Task Frames and
performs source work through the Project Runtime and its guards.

The Universe Project Room and Project Master Bridge are discussion and
delivery surfaces only. They do not create vendor chat sessions, execute source
changes, or turn a design selection into a repository mutation.

## Asynchronous Skill Observations

A Task Frame is the Project-side observation point. On completion it produces
one immutable, redacted `ai-career.skill-observation-candidate.v1` candidate
containing summary metadata, not raw repository material:

```text
candidate and observation identifiers
project and task-frame references
worker reference
skill and model version
context-pack digest
task kind
quality / validation / rework signals
duration, token, and cost observations when available
evidence references and redaction state
```

The Project publishes this observation asynchronously. It retains only the
Task Frame result and a publication receipt. Universe accepts only the exact
redacted surface below, validates it before storage, and uses
`project_id + candidate_id + observation_digest` with the candidate digest for
idempotency and conflict detection.

```text
Allowed candidate fields
  candidate_id
  schema, project_ref, task_frame_ref, source_ref, observed_at, target_ref
  redaction_state: REDACTED
  observation_digest, skill_binding_digest
  skill_id, skill_version, operation_class, context_pack_digest
  model_ref, outcome, validation_state, evidence_refs, bounded metrics

Rejected at the Universe boundary
  skill_ref, raw source paths or content, prompts, secrets, worker transcripts,
  repository documents, executable commands, and arbitrary extension fields
```

Universe stores the accepted observations in its local Bench database and
exposes only observations and aggregate counts, validation states, and metric
totals. It does not rank Skills universally or turn Bench records into source
authority.

```text
Task Worker -> Task Frame -> Project publication -> Universe ingest
Universe Bench -> Context Pack -> next Project or next Task proposal
```

Workers never write directly to the Universe database. Universe never reads
raw source, secrets, or unredacted prompts solely to build Bench history.

## Future Routes and Experience

Universe exposes three distinct proposal surfaces:

- Structural route: missing functional, implementation, document, or contract
  connections in the current Project Seed.
- Seed route: curated cold-start routes and known conditions for a Fresh
  Project.
- Bench route: context-specific suggestions supported by prior observations.

They remain user-selectable proposals, not forecasts, authority, or automatic
source changes.

After asynchronous observations exist, Universe may add an Experience Plane:
Case records, evidence-linked events, causal candidates, and similarity
matching. Canonical causal relations remain `cause -> effect`; a Why view
traverses in reverse from an outcome to possible earlier causes. Inferred
relations remain distinct from observed evidence. Career adoption is separate.

## Implementation Sequence

1. Add Universe Bench and Skill Catalog schemas with provenance and
   applicability constraints. Initial redacted asynchronous ingest,
   idempotency, and aggregate Bench query are implemented.
2. Add Fresh Project intent, specification, design, and route proposal
   contracts. Minimum intent to read-only Official Seed route candidates is
   implemented; LLM-assisted specification, design, and composition remain
   follow-ups.
3. Assemble Context Packs and propose Skill Plans from selected routes.
   Initial Project-local Context Pack assembly, Skill Plan proposal, and
   explicit adoption record are implemented. Cross-project applicability and
   Project Master handoff delivery remain follow-ups.
4. Add Project-side Task Frame SkillRunObservation publication as an
   ai-career/Core Runtime follow-up. The redacted candidate preparation
   surface is implemented; a provider append adapter remains a follow-up.
5. Connect an approved Project publication provider to the existing Universe
   ingest endpoint, then use observed Bench records for Context Pack assembly.
6. Hand selected Project Seeds and Skill Plans to Project Masters through the
   existing handoff and Bridge boundaries.
7. Add Experience and causal comparison only after observed Skill data exists.

## Explicit Deferred Boundary

This document does not change ai-career Core Runtime, Task Frame schemas, or
Execution Guard behavior. Project-side Skill binding and observation emission
must be designed and implemented in ai-career first. Universe then consumes
that published contract without taking ownership of Project execution.
