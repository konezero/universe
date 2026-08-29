# Intent-to-Skill Routing and Skill Bench

Status: proposed Universe product contract
Scope: conversation intent routing, installed Skill resolution, bounded fallback, and evidence-backed Skill promotion
Parent: `docs/universe-design-and-bench-flow.md`

## Purpose

Universe should respond to a user request even when no dedicated Skill exists,
while allowing repeated successful fallback patterns to become candidates for
reusable Skills. The system therefore separates real-time intent routing from
the slower Skill Bench lifecycle.

The intended flow is:

```text
verified conversation context
-> Intent Gate
-> required Capability
-> Skill Resolver
   -> installed Skill, or
   -> bounded generic fallback
-> result and redacted observation
-> Skill Bench
-> Skill Candidate
-> validation and Release adoption
```

An unavailable Skill is not normally a terminal state. It becomes terminal
only when neither an installed Skill nor a safe fallback can satisfy the
required Capability within the classified effect boundary.

## Ownership Boundaries

Career/Core owns the general intent, authority, Assignment, receipt, and
mutation contracts. Universe owns the product-side Skill Catalog, resolver,
fallback observation, Bench aggregation, candidate presentation, and Release
selection experience.

The active Parent or trusted Host classifies language. Deterministic Runtime
code validates provenance, coordinates, decision shape, registry state, and
effect boundaries. Semantic confidence never creates authority.

The following remain separate:

```text
intent classification
skill selection
skill execution
mutation authority
Skill Candidate generation
Skill installation / Release adoption
```

## Goals

1. Map varied natural-language requests to stable Capability identifiers.
2. Automatically select the best installed Skill for the current Project and
   task context.
3. Continue through a bounded fallback when no Skill matches.
4. Record redacted evidence that a reusable Skill gap exists.
5. Promote repeated, stable, useful fallback behavior into a Skill Candidate.
6. Preserve current Session, Anchor, authority, Assignment, and mutation gates.
7. Allow future Universe-specific Skills without rewriting the Intent Gate.

## Non-goals

- Creating or installing a Skill from one unmatched request.
- Treating model confidence, Mode, Role, BOOT, or a Skill match as authority.
- Persisting raw prompts, source content, secrets, provider transcripts, or
  hidden reasoning as Bench evidence.
- Allowing a fallback to bypass a missing Todo, document, filesystem, network,
  or other effect adapter.
- Ranking one Skill universally without Project, task, model, and Host context.

## Runtime Flow

### 1. Context Evidence

The Host supplies a bounded evidence envelope containing:

- the current verified user message ID and digest;
- a bounded ordered list of prior USER and ASSISTANT message IDs and digests;
- the active Session, Frame, and Anchor coordinates;
- an exact pending proposal reference when the message confirms a proposal;
- the current Project and Node coordinates when available.

The envelope contains references and digests. Bench persistence never requires
raw conversation text.

### 2. Intent Decision

The Intent Gate returns one structured decision:

```json
{
  "schema": "universe.intent-decision.v1",
  "decision_id": "intent_...",
  "session_id": "...",
  "frame_id": "...",
  "anchor_id": "...",
  "utterance_ref": "session-bus:msg_...",
  "context_digest": "...",
  "intent_class": "PLAN_REQUEST",
  "imperative_state": "EXPLICIT",
  "target_state": "EXACT",
  "required_capability": "PLAN_CREATE",
  "effect_class": "NONE",
  "route": "RESOLVE_SKILL",
  "confirmation_of": null
}
```

Canonical routes are:

```text
RESOLVE_SKILL
READ_ONLY_RESPONSE
PROPOSE
ASK
WAIT
STRICT_GATE
```

A mentioned command token is evidence, not routing authority. Questions,
reviews, examples, status reports, and design discussion do not become mutation
requests merely because they mention a Skill or command. An action-only message
may confirm only one exact pending proposal through `confirmation_of`; it cannot
select an arbitrary candidate from conversation history.

### 3. Capability Normalization

Intent is normalized to a durable Capability rather than directly to a Skill
name. Examples:

| User request | Intent | Capability | Effect |
| --- | --- | --- | --- |
| `계획 좀 짜볼래?` | `PLAN_REQUEST` | `PLAN_CREATE` | `NONE` |
| `이거 어떻게 생각해?` | `QUESTION` | `REVIEW_OR_EXPLAIN` | `NONE` |
| `그 내용을 TODO로 등록해` | `TASK_EXECUTE` | `TODO_WRITE` | `RUNTIME_STATE_WRITE` |
| `보고서 파일 만들어` | `TASK_EXECUTE` | `DOCUMENT_CREATE` | `USER_ARTIFACT_WRITE` |
| `공통 스킬 추가해` | `TASK_EXECUTE` | `SKILL_AUTHOR` | `BOUNDED_LOCAL_WORK` |
| `이 파일 삭제해` | `TASK_EXECUTE` | `FILE_DELETE` | `STRICT_MUTATION` |

Synonyms, abbreviations, Korean particles, and common typos may map to the same
Capability. Capability identifiers remain stable English values in APIs and
storage.

### 4. Skill Resolution

The resolver queries one immutable Skill Registry snapshot. A Skill declaration
includes at least:

```yaml
skill_id: universe-planning
version: 1
scope: [UNIVERSE]
intents: [PLAN_REQUEST]
capabilities: [PLAN_CREATE]
effects: [NONE]
output_contract: universe.structured-plan.v1
priority: 100
```

Resolution precedence is deterministic:

1. an explicitly user-selected installed Skill;
2. a current Project- or Node-scoped installed Skill;
3. a Universe-scoped installed Skill;
4. an installed common Skill;
5. a registered generic fallback handler.

Every selected Skill must match the required Capability and effect class.
Project scope may specialize a common Skill but cannot weaken effect,
provenance, authority, or execution gates.

The resolver records:

```json
{
  "schema": "universe.skill-resolution.v1",
  "resolution_id": "resolution_...",
  "intent_decision_id": "intent_...",
  "required_capability": "PLAN_CREATE",
  "registry_digest": "...",
  "selected_handler_kind": "SKILL",
  "selected_skill_id": "universe-planning",
  "selected_skill_version": "1",
  "selection_scope": "UNIVERSE",
  "fallback_used": false,
  "effect_class": "NONE"
}
```

Duplicate candidates at the same precedence and priority fail as
`SKILL_RESOLUTION_AMBIGUOUS`; the resolver does not pick by filesystem order.

## Bounded Fallback

When no installed Skill matches, Universe resolves a fallback by Capability and
effect class.

```text
NONE
-> generic structured reasoning / response handler

RUNTIME_STATE_WRITE
-> first-class Runtime state adapter

USER_ARTIFACT_WRITE
-> registered artifact writer for the requested artifact type

BOUNDED_LOCAL_WORK
-> direct Instruction and Work Receipt route

STRICT_MUTATION
-> strict Assignment and Execution Guard
```

Fallback does not mean unrestricted tool use. If the required adapter or Host
capability is absent, the result is `CAPABILITY_UNAVAILABLE`. A Todo request
without a Todo adapter does not fall back to raw SQL or an arbitrary file. A
document request without a registered artifact writer does not silently write
to a guessed path.

Fallback uses the same result contract expected from a future dedicated Skill
when possible. For example, a generic planning fallback returns
`universe.structured-plan.v1`. This makes later Skill substitution transparent
to callers.

## Skill Gap Observation

Every successful or failed fallback may emit one redacted observation:

```json
{
  "schema": "universe.skill-gap-observation.v1",
  "observation_id": "skill_gap_...",
  "project_id": "universe",
  "node_ref": null,
  "intent_class": "PLAN_REQUEST",
  "capability": "PLAN_CREATE",
  "effect_class": "NONE",
  "fallback_handler": "GENERIC_STRUCTURED_REASONING",
  "output_contract": "universe.structured-plan.v1",
  "outcome": "SUCCESS",
  "validation_state": "VALIDATED",
  "user_revision_state": "NOT_REQUIRED",
  "context_fingerprint": "...",
  "observed_at": "..."
}
```

The observation excludes raw prompts, source text, commands, credentials,
provider responses, and arbitrary extension fields. Replaying the same
observation ID with the same digest is idempotent; conflicting reuse is
rejected.

## Skill Bench Lifecycle

Intent Gate is the real-time routing surface. Skill Bench is the asynchronous
learning surface. Intent Gate emits observations but never authors or installs
a Skill.

### Promotion and Registry Tiers

Skill authoring and Skill Pack release are separate promotion pipelines. A
newly generated Skill may be exercised across local sessions before it becomes
an official Universe source, but that experimental registration must not make
it an adopted default.

```text
SESSION_DRAFT
-> BENCH_CANDIDATE
-> EXPERIMENTAL_REGISTRY
-> SOURCE_ADOPTED
-> PACKAGED
-> VERIFIED
-> ADOPTED
```

The visibility and resolution boundary for each state is:

| State | Visibility and use |
| --- | --- |
| `SESSION_DRAFT` | Temporary use by the authoring session only. |
| `BENCH_CANDIDATE` | Review, edit, compare, reject, or supersede in Bench; not a default resolver target. |
| `EXPERIMENTAL_REGISTRY` | Explicitly enabled local experimentation across sessions in the same Universe; always identified as experimental. |
| `SOURCE_ADOPTED` | User-approved canonical Universe Skill source with version, provenance, capability, effect, and evidence metadata. |
| `PACKAGED` | Deterministic Skill Pack candidate; no Registry activation. |
| `VERIFIED` | Digest and manifest verified and eligible for Release adoption; still inactive. |
| `ADOPTED` | Included in an immutable current Registry snapshot and eligible for normal resolution within its scope. |

Two explicit user decisions remain distinct:

1. **Skill source adoption** promotes an experimental Skill into the canonical
   Universe source tree.
2. **Skill Pack Release adoption** promotes one verified Pack version into the
   current immutable Registry snapshot.

Registering a Skill in the Experimental Registry is not either adoption. It
exists so more than one session can dogfood the exact candidate while the
resolver preserves stable adopted defaults and records experimental use.
Rejection removes eligibility for future resolution without rewriting past
receipts. A replacement creates a new candidate or version and links the prior
record through `supersedes`.

The first implementation slice for this promotion pipeline is:

```text
Bench Candidate
-> explicit local experimental registration
-> cross-session resolution only when experimental use is enabled
-> usage and validation evidence returned to Bench
-> user source-adoption decision
-> canonical source handoff for the next deterministic Pack build
```

This slice must not auto-author canonical source, auto-build a Pack, or
auto-adopt a Release.

```text
fallback execution
-> Skill Gap Observation
-> Project-local aggregation
-> stable pattern candidate
-> Skill Draft
-> contract and regression validation
-> Release Candidate
-> explicit Release adoption
-> installed Registry snapshot
-> future automatic resolution
```

A pattern may become a Skill Candidate only when all configured evidence gates
pass:

- repeated support across more than one conversation turn;
- a stable Capability and output contract;
- sufficient validated successes;
- bounded and explainable effect requirements;
- no unresolved high-severity failures;
- no dependence on raw secrets or unbounded hidden context;
- measurable value over the generic fallback.

Thresholds are policy, not universal constants. The candidate records the
exact threshold policy version and supporting observation IDs. High frequency
alone is insufficient.

Candidate states are:

```text
OBSERVED
ELIGIBLE
DRAFTED
VALIDATED
RELEASE_CANDIDATE
ADOPTED
REJECTED
SUPERSEDED
```

Automatic processing may aggregate observations, determine eligibility,
generate a bounded draft, and run deterministic validation. Activation remains
a Release adoption decision. An installed Skill cannot overwrite another Skill
solely because it has more observations.

## Persistence Model

Universe should persist the following append-oriented records:

- `intent_decision`;
- `skill_resolution`;
- `skill_gap_observation`;
- `skill_candidate`;
- `skill_candidate_support`;
- `skill_candidate_validation`;
- `skill_release_adoption`.

Intent decisions and resolutions are session-scoped operational records.
Redacted gap observations and candidates are durable Bench records. Raw context
remains in its owning conversation store and is referenced only by bounded
digests where permitted.

Registry snapshots are immutable and digest-addressed. A resolution records the
exact snapshot digest so later Registry changes cannot rewrite why a Skill or
fallback was selected.

## API Surface

The first vertical slice should expose:

```text
POST /v1/intent-decisions
POST /v1/skill-resolutions
GET  /v1/skill-resolutions/{resolution_id}
POST /v1/projects/{project_id}/skill-gap-observations
GET  /v1/projects/{project_id}/skill-gap-summary
POST /v1/projects/{project_id}/skill-candidates
GET  /v1/projects/{project_id}/skill-candidates
```

The intent endpoint records a Host-classified decision against verified context
evidence; it does not claim that deterministic code understands arbitrary
natural language. The resolution endpoint is deterministic against the selected
Registry snapshot.

Existing Skill observation, Experience, Context Pack, and Skill Plan routes
remain separate. A run of an installed Skill emits a Skill observation. A run
of a generic fallback emits a Skill Gap observation. A Candidate may later
enter the existing Skill Plan and Release flows after validation.

## Receipt and Authority Boundary

Intent and Skill Resolution receipts are routing evidence, not authority.

For effectful work, the selected Skill or fallback must still enter its normal
effect boundary:

```text
RUNTIME_STATE_WRITE -> first-class Runtime adapter
USER_ARTIFACT_WRITE -> receipt-aware artifact writer
BOUNDED_LOCAL_WORK -> Instruction Receipt + Work Receipt
STRICT_MUTATION -> exact Assignment + Execution Guard
```

Write Gate verifies the Intent Decision -> Skill Resolution -> effect receipt
chain but does not reinterpret natural language. A Skill match, fallback
success, Bench score, or Candidate state never grants execution authority.

## Failure Semantics

Canonical failures include:

```text
INTENT_EVIDENCE_INVALID
INTENT_DECISION_STALE
INTENT_TARGET_AMBIGUOUS
SKILL_REGISTRY_UNAVAILABLE
SKILL_RESOLUTION_AMBIGUOUS
SKILL_EFFECT_MISMATCH
FALLBACK_HANDLER_UNAVAILABLE
CAPABILITY_UNAVAILABLE
SKILL_GAP_OBSERVATION_INVALID
SKILL_CANDIDATE_SUPPORT_INSUFFICIENT
```

Failure to resolve a dedicated Skill should normally continue to fallback.
Failure to obtain a safe effect adapter is terminal for that effect.

## Initial Vertical Slice

Implement one end-to-end, non-mutating Capability first:

```text
PLAN_REQUEST
-> PLAN_CREATE
-> installed planning Skill when present
-> otherwise GENERIC_STRUCTURED_REASONING
-> universe.structured-plan.v1
-> redacted Skill Gap Observation
-> Skill Gap summary in Bench UI
```

This slice proves intent evidence, deterministic resolution, fallback, output
contract compatibility, observation idempotency, and Bench aggregation without
mixing in mutation authority.

The second slice adds `TODO_WRITE` through the existing Todo adapter. The third
adds `DOCUMENT_CREATE`. Source mutation and strict effects remain later slices
after the read-only and Runtime-state contracts are stable.

## Validation Matrix

Required tests include:

1. Intent evidence rejects non-USER provenance, stale context, and coordinate
   mismatch.
2. Questions and design discussion never route to an effectful handler.
3. Synonymous planning requests normalize to `PLAN_CREATE`.
4. An installed Project Skill outranks Universe and common Skills.
5. An explicit user selection outranks automatic resolution when compatible.
6. Duplicate equal-priority candidates fail deterministically.
7. Missing Skills use fallback and preserve the output contract.
8. Missing effect adapters fail without raw filesystem, SQL, or network
   fallback.
9. Gap observations are redacted, exact-field validated, and idempotent.
10. Candidate eligibility requires threshold policy and supporting evidence.
11. Candidate generation does not install or activate a Skill.
12. Registry adoption changes later resolution without rewriting old receipts.

Dogfood fixtures should include short and colloquial Korean requests such as:

```text
계획 좀 짜볼래
이거 어떻게 생각하냐
todo 등록하고
진행됨
작업 끝난 것 같은데 응답은 안 옴
그래 그거 공통 스킬 추가하고
```

## Completion Criteria

The design is implemented only when:

- an unmatched planning request completes through fallback;
- the resolution and redacted gap observation are durable and inspectable;
- adding a validated planning Skill changes future resolution through a new
  Registry snapshot without changing the Intent Gate;
- effectful fallback cannot bypass its adapter or receipt boundary;
- repeated observations can produce a Candidate, but cannot silently install
  it; and
- API, isolated service, resident service, and visible UI evidence agree.
