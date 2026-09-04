# Universe — Vision: Pull Forward Anchor / Seed / Causal RAG

Status: NORTH STAR
Date: 2026-09-04
Source: operator design memo.

Universe is not a coding-only harness. Coding is the first experiment
domain. The long-term target is a **general causal knowledge and work
world** applicable across domains (research, design, social/economic
analysis, …).

## 1. Pull Forward Anchor

The core idea of the Anchor is not a session id or an execution-process
identifier — it is a **Pull Forward Anchor**.

It does not retain the entire past state or re-inject it repeatedly.
Instead it selectively carries forward the **meaning, decisions,
rationale, and context that are still valid** from the previous state into
the next current state.

```
Past Anchor
  ↓  extract still-valid state / decisions / rationale
  ↓  Pull Forward
Current Anchor
  ↓  absorb new observation / change / outcome
Next Anchor
```

So the Anchor is closer to a **semantic coordinate of the current world
state that moves through time**.

Session Anchor, Rust Host, PTY and the rest of the execution layer are a
*later, coding-domain concretization* of this Anchor concept — a
sub-application, not the definition.

## 2. How Universe accumulates knowledge

Not the usual `collect → store → embed → search`.

It always **observes the current state first**, then traces *backward* to
understand why the present became this way.

```
observe the current state
  ↓
why is it in this state?
  ↓
trace the immediately preceding change
  ↓
trace the cause that produced that change
  ↓
investigate the tech / industry / society / knowledge level /
  decisions of that time
  ↓
secure Evidence
  ↓
construct cause / condition / relation
```

Therefore the thing worth storing is **the relations that explain the
present**, more than the documents themselves.

Relation objects, e.g.:

```
Observation · Claim · Cause · Condition · Transition · Outcome · Evidence
```

Documents, papers, articles, GitHub history, etc. are the **Evidence** that
proves these relations.

## 3. What a Seed is

A Seed is not initial RAG data or a bulk bundle of knowledge.

It is the **minimum set of historical reference points** that keeps
Universe from getting lost while tracing the causes of the present
backward.

Investigating an entire field's history from the start is too expensive
(search, LLM, verification). So: lay down only the Seed first, then expand
**only the parts needed to explain the actual current state**,
incrementally.

```
Seed
  ↓
current Observation
  ↓
explainable with existing knowledge?
  ├─ YES → Pull Forward the existing knowledge
  └─ NO
       ↓
     generate Why? / Research Gap
       ↓
     investigate only the required past branch
       ↓
     add Evidence / Cause / Relation
```

The knowledge graph is not completed like an encyclopedia up front — it
**grows only where it is needed**.

## 4. Cost strategy for Seed-based RAG

Investigation priority:

> Investigate first the things that have **large current impact** + an
> **unclear cause** + **influence future judgment**.

This concentrates RAG research cost exactly where it directly serves a
current decision.

One key object is the **Research Gap**:

```
Research Gap = "something not yet known that is needed to explain the
                current state"
```

A Research Gap can later be handed to a research Agent (e.g. Grok) or to a
Remote Universe to be investigated independently.

## 5. Backward tracing + Pull Forward + future prediction

Two directions combine:

```
             cause tracing
        ◀──────────────────
PAST ─────────────────────── CURRENT
        ──────────────────▶
             Pull Forward
```

Then a forward direction is added from the present:

```
PAST
  ↓ Pull Forward
CURRENT
  ↓
analyze the Cause / Condition that produced the present
  ↓
which causes persist?
which causes weaken?
which conditions disappear?
what new forces appear?
  ↓
Future Path A / B / C
```

So Universe's forecasting is **not** simple pattern extension or
time-series projection. It first understands the causes that produced the
present, then generates possible future paths based on how those causes
and conditions will change.

## 6. Future Path and execution outcome

A future path is not just an AI suggestion — it must be **comparable
against the real later outcome**.

```
Future Path
- as_of
- evidence snapshot
- assumptions
- causal factors
- confidence / probability
- invalidation conditions
- expected outcome
```

When the user adopts a Path:

```
Future Path
  ↓ adopt
Goal / Work
  ↓
Execution
  ↓
Outcome
  ↓
compare predicted vs actual
  ↓
Memory / Evidence
  ↓
Pull Forward into the next Anchor
```

The prediction itself accumulates back into Universe as experience.

## 7. General Universe and Domain

The structure is not tied to coding.

| Domain | Node | Evidence | Worker / Future |
|---|---|---|---|
| Coding | feature / module | Git / Test | Codex / Claude |
| Research | concept / research topic | paper / experiment | Research Agent |
| Design | design pattern | product / history / industry change | — |
| Social / economic | company / industry / policy / event | statistics / articles / research | Future Path = scenarios |

So Universe Core keeps **general** concepts at the center — Resource,
Knowledge, Observation, Evidence, Decision, Prediction, Execution, Outcome
— rather than coding-specific concepts like Git / File.

## Core, in one sentence

> Universe is a system that observes the present, traces the causes that
> produced the present into the past only as far as needed, carries the
> meaning that is still valid forward to the present via the Pull Forward
> Anchor, and uses that causal structure to generate, execute, and verify
> possible future paths — expanding its own knowledge as it goes.

## Relation to other design notes

- `docs/unified-node-graph-model.md` — the typed-node graph is where
  Observation / Evidence / Cause / Condition relations live.
- `docs/cooperative-contention-resolution-proposal.md` — how live sessions
  coordinate while working against the same resources.
- The three-axis model (Todo = work item, Kanban = lifecycle, Fleet = live
  executors) is the operational surface; this vision is the substrate.
