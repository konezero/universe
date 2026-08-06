---
name: host-state-projection
description: Project Runtime Mode Anchor and session coordinates into companion state files without Execution Guard.
---

# Host State Projection

Invocation class: `HOST_STATE_PROJECTION`

Host storage action: `HOST_DEPENDENT`

Capability classification: `runtime_owned_state = AVAILABLE` when the Host can
write declared Runtime state paths; otherwise `HOST_DEPENDENT` / `UNKNOWN`.

This Skill covers **continuity bookkeeping**: projecting already-decided Mode
Anchor and session coordinates into companion markdown (and related Runtime
state paths). It is not project mutation, not Task Assignment, and not an
authority transition.

```text
Mode Anchor store / Host Mode context  = operational truth
session.md / current_anchor_frame.md   = companions (projections)
projection never creates Authority, Write Scope, or Execution Assignment
```

## Do not use Execution Guard

These writes use the Runtime-owned operational-state exception in
`.ai/skills/common/execution-guard/SKILL.md` and
`.ai/core/PRE_EXECUTION_VERIFICATION.md`:

```text
HOST_STATE_PROJECTION
MODE_CHANGE / prepare-session Mode Anchor store update
session / provider observation under .ai/runtime/state/ or .ai/runtime/tmp/
SESSION_HANDOFF evidence
Runtime-owned HANDOFF_APPEND on declared handoff paths
automatic continuity flush
```

Do **not** open Task Assignment, request approval, or issue a Mutation Receipt
for this class. Requiring Guard here turns every mode switch and handoff into
false execution work.

## When to run

```text
after successful MODE_CHANGE / prepare-session
after Host records a new Mode Current Anchor
when companions are missing or lag the Mode Anchor store
on explicit operator request to refresh Runtime state companions
```

Projection failure is best-effort. It must not fail MODE_CHANGE, boot, or
handoff. Status surfaces that need truth should prefer the Mode Anchor store
when both exist (see `.ai/skills/common/runtime-status/SKILL.md`).

## Allowed targets (declared Runtime state only)

```text
.ai/runtime/state/session.md
.ai/runtime/state/current_anchor_frame.md
.ai/runtime/tmp/* observation / transport files when Host-declared
Runtime-owned handoff inbox metadata on declared handoff paths
```

Fixed fields only. Source of bytes:

```text
Mode Anchor store Current row for the selected Mode
Host process-local Mode / session coordinates
optional provider session observation already recorded by the Host
```

Do not accept free-form agent prose as the projection payload. Do not invent
Authority, Write Scope, Execution Assignment, or READY claims that the Host
did not already hold.

## Forbidden (still Guard)

```text
.ai/core/**
project source and product trees
templates, configuration, installers (except Host Runtime lifecycle routes)
external systems
free-form rewrite of session.md that invents mode/authority/assignment
```

Those remain project-owned or unclassified durable mutation and require
`.ai/skills/common/execution-guard/SKILL.md`.

## Host procedure

```text
1. Confirm MODE_CHANGE / prepare-session (or Host Mode context) succeeded
2. Read Mode Anchor store Current for the selected Mode
3. Project fixed fields into companions under .ai/runtime/state/
4. Optionally record handoff evidence
     previous_session_id -> current_session_id
5. Return PROJECTED | SKIPPED | UNKNOWN
6. Stop; do not chain Task Assignment or Guard
```

When a Host hook automates this path, keep it non-blocking for Mode
transition (same class as best-effort session continuity hooks).

## Related

```text
.ai/skills/common/mode-change/SKILL.md
.ai/skills/common/persistence/SKILL.md
.ai/skills/common/execution-guard/SKILL.md
.ai/core/SESSION_CURRENTNESS.md
.ai/core/PRE_EXECUTION_VERIFICATION.md
.ai/core/AUTOMATIC_CONTINUITY_CONTRACT.md
```
