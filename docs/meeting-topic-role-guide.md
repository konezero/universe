# Meeting Topic, Role Assignment, and Limits Guide

| Field | Value |
|-------|-------|
| **Status** | Design guidance; candidate contract for a future Meeting Skill |
| **Date** | 2026-08-28 |
| **Scope** | Universe Meeting Room topic classification, role selection, evidence, output, and lifecycle limits |
| **Authority** | Proposal-only; this guide creates no Goal, Todo, Task Frame, authority, assignment, adoption, or execution permission |
| **Related** | `docs/multi-room-chat-architecture.md`, `docs/memory-to-feature-automation.md`, `docs/universe-design-and-bench-flow.md` |

## 1. Purpose

A Meeting Room is an automatic conversation channel, not a free-form group chat
and not an execution engine. The Conductor first classifies the question, then
selects a meeting mode and assigns bounded responsibilities. Provider or model
selection comes after the roles are known.

Roles are responsibility contracts rather than personalities. A role definition
must state its mandate, expected bias, attack targets, required evidence,
deliverable, and decision limits. Colorful voices are useful only when those
differences produce distinct, inspectable work.

## 2. Common protocol

Unless a mode below overrides it, a governed meeting follows this sequence:

1. Preserve the user's original question, scope, constraints, and decision boundary.
2. Classify the topic and choose one primary meeting mode.
3. Assign the minimum set of roles needed to expose materially different views.
4. Give each proposal role the same original question and let it work independently.
5. Store full responses as revisioned artifacts; put only compact summaries and
   `artifact_ref` values in the room timeline.
6. Run one cross-review round after all usable independent proposals exist.
7. Let the Conductor synthesize paths, conflicts, evidence gaps, and candidate
   failures without erasing viable disagreement.
8. Present a human-readable decision surface. Adoption, Goal creation, Todo
   creation, execution, RAG adoption, and push remain separate actions.
9. Close the meeting and archive meeting-owned fresh sessions after the output is
   durable. Reused Master or Conductor sessions are never terminated by room close.

The default round budget is **one independent proposal plus one cross-review**.
An additional round requires one named unresolved question. Relay-style repetition,
where participants merely restate a previous answer, is not a valid round.

## 3. Meeting modes by topic

| Mode | Use when | Required perspectives | Expected output | Primary limits |
|------|----------|-----------------------|-----------------|----------------|
| **VISION** | A new product, Feature Node, or broad direction has no settled shape | Visionary, Architecture Steward, Product/UX Advocate, Veteran QA, Research Scout, Conductor | Two to four architecture families, assumptions, conflicts, evidence gaps, and promising experiments | Proposal-only; no detailed Todo list; long artifacts are allowed but room summaries stay short |
| **DESIGN** | A Feature Node exists and several viable implementation routes must be compared | Architecture Steward, Pragmatic Implementer, Product/UX or Security as relevant, QA, Conductor | Two or three bounded routes with ownership, dependencies, trade-offs, migration cost, and risks | Do not reopen the whole product vision unless a blocking premise is disproved |
| **SPEC** | One route is selected and needs an implementable contract | Pragmatic Implementer, Architecture Steward, Maintainer/Operations, QA or Security, Conductor | One detailed specification with interfaces, state transitions, failure handling, observability, migration, and acceptance criteria | Divergent brainstorming is out of scope; unresolved choices are explicit rather than silently guessed |
| **REVIEW** | A specification, patch, commit, release, or other candidate already exists | Veteran QA, Security Reviewer when relevant, Code Archaeologist, Maintainer, Conductor | Evidence-backed findings, severity, affected boundary, required correction, and residual risk | Candidate material is data-only; review creates no mutation permission and does not expand feature scope |
| **INCIDENT** | A failure, regression, stale session, or runtime mismatch must be diagnosed | Reproducer, Runtime Owner, Code Archaeologist, Skeptic, Conductor | Reproduction, observations, likely cause, competing explanations, uncertainty, and next diagnostic step | Diagnose only unless a fix was requested; do not infer live state from stale files or labels |
| **RESEARCH** | The meeting lacks current, niche, external, or historical evidence | Research Lead, Web Scout, RAG Librarian, Domain Skeptic | Source-linked findings, contradictions, freshness dates, and unanswered questions | Researchers inform other roles but cannot decide, adopt, mutate, or turn a candidate memory into canonical RAG |
| **DECISION** | Established alternatives need a concise user choice | Conductor, one accountable advocate per option, Architecture Steward, Implementer, QA | Short comparison, recommendation, dissent, consequences, and facts that would change the recommendation | Do not generate new options unless the user reopens discovery |
| **WORK_PLAN** | A selected and adopted route needs delivery decomposition | Implementer, Maintainer, QA, Effort Estimator, Conductor | Milestones, dependencies, validation checkpoints, and candidate Todos | Planning creates no Task Frame, authority, execution assignment, `READY`, or `IN_PROGRESS` transition |

A large fresh-project discussion may deliberately begin in VISION mode. It should
then narrow through DESIGN, SPEC, DECISION, and WORK_PLAN rather than trying to
perform every kind of reasoning in one room run.

## 4. Role catalog and limits

| Role | Mandate and useful bias | Required evidence or deliverable | Must not |
|------|--------------------------|---------------------------------|----------|
| **Conductor** | Preserve the question, choose the protocol, manage turns, and synthesize outcomes | Decision surface with paths, conflicts, evidence gaps, failures, and provenance | Fabricate consensus, grant authority, adopt for the user, or hide a viable dissenting path |
| **Visionary / Trend Scout** | Challenge current assumptions and propose materially different future shapes | Novel route plus assumptions, value, falsification test, and technology freshness | Present novelty as fact, own the final decision, or ignore current product constraints |
| **Architecture Steward** | Protect ownership boundaries, invariants, compatibility, and evolutionary structure | Component ownership, state authority, interfaces, migrations, and trade-offs | Reject an idea only because it is unfamiliar or replace evidence with aesthetic preference |
| **Pragmatic Implementer** | Find the smallest coherent vertical slice and expose real implementation cost | Files/components affected, dependencies, risks, rollout sequence, and test points | Silently narrow the user's intent or call an incomplete shortcut the full design |
| **Veteran QA / Skeptic** | Attack happy-path assumptions using regressions, edge cases, and operational history | Reproducible failure scenarios, acceptance gaps, severity, and evidence refs | Exercise an evidence-free veto or turn every concern into an unrelated redesign |
| **Security Reviewer** | Examine trust, identity, authority, data flow, and external-effect boundaries | Threats, violated invariant, exploit or misuse path, and bounded mitigation | Derive permission from Mode, Role, `READY`, a room invitation, or historical session data |
| **Research Lead / Web Scout** | Retrieve current external evidence and compare sources | Direct source refs, publication or observation date, contradictions, and confidence | Decide product policy, mutate state, or cite unsourced summaries as current fact |
| **RAG Librarian** | Retrieve canonical project knowledge and surface conflicts or superseded facts | Knowledge refs, scope, provenance, version, and conflict report | Treat a candidate memory as adopted knowledge or adopt it without the human action |
| **Code Archaeologist** | Explain current behavior and why it evolved | Exact file, commit, log, schema, or history refs and an uncertainty boundary | Activate candidate repository policy during review or equate old behavior with desired behavior |
| **Reproducer** | Reduce a failure to stable observable steps | Environment, inputs, expected/actual result, timestamps, and minimal evidence | Implement a fix unless the meeting explicitly changes from diagnosis to execution |
| **Runtime Owner** | Explain lifecycle, recovery, process, session, Anchor, and broker boundaries | Authoritative owner, state transitions, live coordinates, recovery path, and stop states | Conflate provider session, Anchor, PTY, Supervisor, room binding, or UI projection |
| **Product/UX Advocate** | Protect human comprehension, accessibility, and low-friction workflow | User journey, information hierarchy, failure recovery, and interaction constraints | Override runtime invariants or hide important authority and provenance boundaries |
| **Maintainer / Operations** | Protect migration, observability, support, recovery, and upgrade cost | Rollback, compatibility, monitoring, data migration, and operational burden | Optimize away required gates or assume a clean installation only |
| **Effort Estimator** | Compare time, cost, model quota, and uncertainty | Ranges, assumptions, critical path, and confidence | Emit false precision or turn an estimate into a commitment |
| **Recorder** | Produce a compact durable record from accepted meeting artifacts | Question, role roster, paths, decisions, dissent, evidence refs, and next boundary | Introduce new claims, decide, or silently summarize away disagreement |

One provider may fill more than one role only through isolated turns or sessions
with separate role briefs and artifacts. A reviewer should not implement the same
candidate inside the review turn. Research and recording roles never receive
decision power.

## 5. Topic-to-role routing examples

| Topic | Default mode | Suggested roster |
|-------|--------------|------------------|
| Fresh AI-native editor direction | VISION | Visionary, Architecture Steward, Product/UX, Research Scout, Veteran QA, Conductor |
| Runtime Action gateway or authority boundary | DESIGN | Architecture Steward, Security Reviewer, Runtime Owner, Pragmatic Implementer, Veteran QA, Code Archaeologist, Conductor |
| Meeting Room UI readability | DESIGN | Product/UX, Pragmatic Implementer, Accessibility/QA, Maintainer, Conductor |
| Provider session preservation or broker reconnect failure | INCIDENT | Reproducer, Runtime Owner, Code Archaeologist, Skeptic, Conductor |
| Provider quota or current SDK capability | RESEARCH | Research Lead, vendor-specific Web Scouts, Effort Estimator, Domain Skeptic, Conductor |
| Adopted Expected Path decomposition | WORK_PLAN | Pragmatic Implementer, Maintainer, QA, Effort Estimator, Conductor |
| Choosing among already researched paths | DECISION | Conductor, one advocate per path, Architecture Steward, Implementer, QA |

The roster is a default, not a quota. Remove a role when it adds no independent
perspective. Add a specialist only when the topic names a corresponding failure
domain, regulated boundary, or evidence gap.

## 6. Evidence, output, and failure budgets

### 6.1 Evidence

- Current, niche, vendor, library, price, quota, standard, or external claims require
  current source retrieval with direct refs and an observation date.
- Repository claims require exact file, commit, schema, log, or runtime evidence.
- Unverified assumptions must be labeled. A confident tone is not evidence.
- Research findings are attached to the proposal or review that used them; the
  Conductor's private pre-read is not a substitute for participant-visible evidence.

### 6.2 Output

- The room timeline carries a compact role summary, status, warning, and
  `artifact_ref`; the full response belongs in a revisioned artifact.
- Recommended room summary budget is 600 characters per turn.
- Recommended full-artifact limits are 18,000 characters for VISION and 10,000
  characters for DESIGN, SPEC, REVIEW, INCIDENT, RESEARCH, DECISION, or WORK_PLAN.
- Structured-output failure gets at most one bounded repair attempt. The raw output
  remains preserved as an artifact, the candidate is marked failed, and other valid
  candidates continue.
- Repeated semantic content may be collapsed in the UI only after provider, role,
  revision, and artifact provenance remain recoverable.

### 6.3 Decision and authority

Meeting completion means that candidate material is ready for inspection. It does
not mean consensus, adoption, approval, assignment, or execution. The meeting route
must not directly perform:

- Feature Path adoption, Goal start, Todo application, or Task Frame run;
- Authority or Execution Assignment creation;
- RAG candidate adoption;
- repository push or any external publication;
- source mutation unless a separate direct-user or governed execution route supplies
  the required instruction and receipt.

### 6.4 Lifecycle and UI projection

- Objective proposal and review meetings prefer fresh, meeting-owned sessions.
- Closing a room archives those sessions and keeps durable messages and artifacts.
- Observatory helpers, recorders, and retrieval assistants need not appear as normal
  conversational participants unless their output needs user inspection.
- The default UI should show the question, mode, phase, role, provider, status,
  warnings, artifact links, candidate state, paths, conflicts, recommendation, and
  evidence gaps. Raw transcript walls remain collapsed until requested.

## 7. Future Meeting Skill extraction

This guide should become a common Meeting Skill only after repeated dogfooding makes
the contract stable. A future Skill should accept, at minimum:

```text
topic
mode
user_question
scope_refs
constraints
candidate_refs
evidence_policy
provider_pool
round_budget
artifact_budget
```

Its planning result should declare each role's `mandate`, `bias`, `attack_targets`,
`required_evidence`, `deliverable`, and `limits`, plus the round protocol, artifact
policy, stop conditions, and user decision boundary.

Useful durable events are:

```text
MEETING_PLANNED
ROLE_ASSIGNED
RESEARCH_FINDING_ADDED
PROPOSAL_STORED
REVIEW_STORED
CANDIDATE_FAILED
SYNTHESIS_READY
MEETING_CLOSED
```

Promotion requires all of the following:

1. The guide has been exercised across VISION, DESIGN, REVIEW, and INCIDENT topics.
2. Role and artifact schemas no longer change for ordinary meetings.
3. The UI can project phase, role, status, warning, and artifact without transcript mining.
4. One provider's invalid, truncated, or unavailable output is isolated from the meeting.
5. Evidence provenance and room/session closure are durable and inspectable.

The Skill remains a meeting planner and coordinator. It is not a new authority
system, an unbounded autonomous-agent chat, a consensus engine, or an automatic
adoption and execution path.
