# Official Development Seed v0

Status: EXPERIMENTAL_CURATED_BASELINE

## Purpose

A fresh Universe has no local project history. The Official Development Seed
provides a small, inspectable map of common software-development routes so the
first project can receive useful path candidates without pretending that a
model has already learned from outcomes.

## Boundary

The seed is a recall and proposal surface.

```text
seed observation != verified project history
route match      != probability
future candidate != decision
decision         != execution
execution result != Current Anchor adoption
```

The seed must never:

- claim that a future event will occur;
- emit fabricated probabilities or confidence percentages;
- mutate a project repository or Current Anchor;
- create authority, assignment, approval, or execution permission;
- mix official seed provenance with project-local experience.

## Cold-Start Flow

```text
project description + technology stack + final goal
  -> match project archetypes and route signals
  -> recall relevant curated routes and failure patterns
  -> return ranked FUTURE_PATH_CANDIDATES
  -> user may select one candidate
  -> a governed project runtime may turn the selection into work
  -> observed results remain project-local until separately promoted
```

## Data Planes

```text
official_development_seed
  read-only curated baseline distributed with Universe

project_local
  actual decisions, Anchors, Task Frames, Evidence, and outcomes

overlay
  local interpretation, corrections, and promotion candidates
```

Only the official seed is implemented in v0. Project-local and overlay stores
are later surfaces and must remain provenance-separated.

## Initial Contents

The v0 catalog includes:

- generic software, CLI, desktop, web-service, and agent-runtime archetypes;
- foundation, desktop state, service boundary, and governed-agent routes;
- recurring failure patterns around scope, contracts, state identity,
  migration, untrusted execution, and stopping conditions;
- pivot rules for changed goals, blocked dependencies, and failed validation.

These entries are curated hypotheses. Actual project trajectories will provide
the evidence needed to retain, revise, reject, or promote them.

## Release Contract

`tools/seed.py build` validates the JSON source and creates:

```text
dist/official-development-seed-v0.sqlite
dist/official-development-seed-v0.manifest.json
```

The manifest binds the source SHA-256 and generated database SHA-256. Runtime
consumers must open the official database read-only.
