# Worker Resume Archive Template

Status: **DEPRECATED (runtime resume path)**  
Deprecation date: 2026-07-31  
Branch / track: ai-career PR #269 (`codex/source-only-conductor-boundary`)

## Runtime Resume SSOT

```text
Runtime Resume / Checkpoint durability
  -> .ai/runtime/continuity/continuity.sqlite

Restore filter
  -> same node + same mode

Do not use .ai/resume/<worker_role>/ as RESUME_SAVE or RESUME_RESTORE identity.
Do not delete this template solely for deprecation; keep it historical.
```

See `.ai/templates/runtime_continuity/README.md` and the resume-save /
resume-restore Skills.

---

Use this template only as historical layout for specialized worker roles.

Worker archives are narrower than Conductor or Master archives.

## Worker Purpose

A Worker performs focused work within a bounded scope.

Examples:

```text
worker_strategy
worker_risk
worker_docs
worker_review
worker_ui
worker_data
```

## Worker Doctrine

```text
Workers execute specialized work.
Master integrates.
Conductor governs boundaries.
User approves.
```

## Target Path

```text
.ai/resume/<worker_role>/
```

## Required Files

```text
.ai/resume/<worker_role>/
  manifest.json
  index.md
  capabilities/<worker_role>.md
  profiles/communication.md
  snapshots/current_state.md
  snapshots/decisions.md
  snapshots/next_actions.md
  handoff/latest.md
  history/README.md
```

## Worker Manifest Additions

A worker manifest should include:

```json
{
  "role_class": "worker",
  "worker_specialty": "<specialty>",
  "master_role": "Master",
  "handoff_files": [
    "handoff/latest.md"
  ]
}
```

## Capability Boundary

Worker capability files should clearly define:

- allowed task class
- non-capabilities
- target project boundaries
- output format
- test/report expectations

## Handoff File

`handoff/latest.md` is the worker's latest output for Master/User review.

It should include:

```text
Task
Scope
Files inspected
Findings
Proposed changes
Risks
Validation notes
Next recommended action
```

## Canonical Adoption Rule

Worker output is not canonical by default.

Adoption path:

```text
Worker handoff
  -> Master merge candidate
  -> User approval
  -> repository update
```

## Worker Checkpoint Trigger

Create a worker checkpoint when:

- specialty scope changes
- a major handoff is completed
- decisions change
- next actions change
- Master adopts or rejects worker output

## Safety Rule

Workers must not broaden their own scope.

If a task requires broader governance or cross-role integration, hand off to Master or Conductor.
