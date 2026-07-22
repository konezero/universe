# Project Governance Profile Template

Use this template to attach a real project to the ai-career Core runtime.

A project profile defines how Conductor, Master, Workers, and the User operate around a target project.

## Target Path

```text
.ai/projects/<project_id>/governance_profile.md
```

Example:

```text
.ai/projects/gcs/governance_profile.md
```

## Profile Shape

```md
# <Project Name> Governance Profile

Project ID: `<project_id>`
Target repository: `<owner>/<repo>` or local source package
Status: candidate

## Purpose

<What this project is and why LLM runtime support is attached.>

## Role Map

- Conductor: governance, role boundaries, checkpoint policy
- Master: task routing, worker integration, patch plan
- Workers: focused implementation or analysis
- User: final approval and scope authority

## Execution Boundary

<What runtime roles may change, inspect, or propose.>

## Intent / Boot / Mode Policy

Intent-first routing rule:

```text
Known command, mode, role, or anchor tokens are not routing triggers until the
utterance intent confirms command execution, mode switch, runtime boot, status,
validation, update, or another recognized runtime action.
```

Default intent policy:

- `SESSION_OPEN`: conservative session open; default `READ_ONLY`; show Work Queue.
- `BOOT_COMMAND`: explicit role/profile boot; apply Boot Profile; show Work Queue.
- `MODE_SWITCH`: user shorthand for `BOOT_COMMAND`, such as `마스터모드 / MASTER mode` or `문서모드 / DOCUMENT mode`.
- `TASK_EXECUTE`: execute a specific queued/scoped task after permission and scope check.
- `POLICY_UPDATE`: modify `.ai` control-plane/governance policy after target/impact report.

Boot state and execution assignment are separate:

```text
Boot State = READY
Execution Assignment = UNASSIGNED
```

Project-specific mode phrases:

```text
마스터모드 / MASTER mode -> <node/mode/resolved-role/resolved-scope>
문서모드 / DOCUMENT mode -> <node/mode/resolved-role/resolved-scope>
리뷰모드 / REVIEW mode -> <node/mode/resolved-role/resolved-scope>
개발모드 / IMPLEMENTATION mode -> <node/mode/resolved-role/resolved-scope>
패치모드 / PATCH mode -> optional alias for development/implementation work
```

Project-specific overrides:

<Define project-specific boot commands, mode phrases, default modes, and session pointer policy.>

## Storage Policy

Choose one:

- Shared Mode: `.ai` tracked by Git.
- Local Mode: `.ai` ignored by Git.
- Hybrid Mode: shared governance/profile files tracked by Git; runtime/resume/session/cache files kept local.

Selected mode:

```text
<Shared | Local | Hybrid>
```

## Patch Policy

<How patches are proposed, reviewed, applied, and rolled back.>

## Test Policy

<Required checks before a patch is considered ready.>

## Report Policy

<What must be reported back to the User.>

## Checkpoint Triggers

<When project state should be checkpointed.>

## Known Constraints

<Project-specific constraints.>

## Current Next Actions

<Immediate next steps.>
```

## Required Rules

1. Do not confuse the governance profile with the project source.
2. Do not widen scope without User direction.
3. Record rollback expectations before risky patches.
4. Create checkpoints only when they add useful continuity.
5. Keep project-specific facts separate from reusable `.ai` core rules.
6. Do not treat `BOOT_COMMAND` as file modification approval.
7. Do not treat `MODE_SWITCH` as file modification approval.
8. Do not treat `Boot State = READY` as execution assignment.
