---
name: skill-observation
description: Prepare redacted Task Frame Skill-run observations for optional provider-backed handoff.
---

# Skill Observation Export

Invocation class: `REFERENCE_RUNTIME_PREPARATION`

Preparation capability: `skill-observation.prepare = AVAILABLE`

Durable handoff capability: `HOST_DEPENDENT`

This Skill prepares observations already returned by a completed Task Frame.
It is not a Skill catalog, benchmark engine, model selector, publisher, or
Universe client.

## Required Input

Use only the bounded `skill_run_observations` returned in a reviewed Task Frame
Result Packet. Do not construct observations from conversation text, raw
prompts, hidden reasoning, source contents, or secrets.

```text
Task Frame Result Packet
  -> select bounded Skill observations
  -> skill-observation prepare
  -> PREPARED passive candidate
  -> optional Project-to-Universe queue publication
  -> optional HANDOFF_APPEND by a project-owned provider path
```

Prepare the candidate with the installed continuity profile:

```text
python .ai/runtime/reference_runtime/cli.py skill-observation prepare \
  --repo-root <repo-root> --request <request.json>
```

The request contains the project, Task Frame, and immutable source references,
plus each observation's Runtime-computed observation and binding digests, the
complete declared binding, model reference, outcome, validation state, bounded
evidence references, and allow-listed numeric metrics. The Runtime verifies the
binding digest, then removes the project-local `skill_ref` from the prepared
candidate while preserving Skill ID, version, operation class, and Context Pack
digest for downstream Bench use. The candidate carries
`schema: ai-career.skill-observation-candidate.v1` and
`redaction_state: REDACTED`.

## Boundary

`PREPARED` is not durable delivery. It creates no local continuity row, source
mutation, Universe record, authority, execution assignment, or active state.
The Task Frame is complete whether this candidate is exported or not.

For a selected Project-to-Universe publication, send only the prepared
candidate to the declared Universe ingress queue and retain its durable queue
receipt. Queue delivery is not Bench ingest, Career promotion, a source write,
or a Task Frame state change. A scheduler may wake the Universe queue consumer
but does not create the candidate or replace the selected publication approval.

For a later provider append, use a declared Runtime-owned archive path and
record a separate `HANDOFF_APPEND` provider receipt. The provider operation
must not edit project source, Core, templates, configuration, or external
systems. Those mutations remain outside this Skill and use their normal
execution path.
