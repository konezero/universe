# Project Master Automation Template

Use this template for project-level audit automation.

Project audit belongs to Project Master, not Carrier.

## Target Path

```text
.ai/master/<project_id>-audit/README.md
.ai/master/<project_id>-audit/scheduler_instruction.md
.ai/master/<project_id>-audit/checkpoint.json
```

## README.md Shape

```md
# <Project> Night Audit

Status: candidate
Type: project-master-audit
Repository: <owner/repo>
Role: Project Master
Mode: Automation
Session Label: Master | <Project>

## Purpose

<Project-level health, queue, documentation, and governance audit.>

## Scope

```yaml
scope:
  project_id: <project_id>
  audit_targets:
    - task_board
    - worklog
    - current_state
    - docs_drift
    - governance_profile
    - implementation_queue
```

## Outputs

```yaml
outputs:
  report_target: project
  candidate_target: ai-career_when_reusable
```

## Rules

1. Audit project state.
2. Report project findings to the project first.
3. Promote only reusable findings deliberately.
4. Do not merge PRs without explicit authority.
5. Do not perform live final decisions.
```

## scheduler_instruction.md Shape

```md
# <Project> Night Audit Scheduler Instruction

## Boot Order

1. Load this scheduler instruction.
2. Restore checkpoint state.
3. Inspect scoped project files and queues.
4. Classify work queue drift.
5. Report project findings.
6. Create reusable candidate only when needed.
7. Save checkpoint cursors.
8. Sleep.

## Principle

Project Master audits project state.
Carrier collects event streams.
Conductor reviews reusable governance candidates.
```

## checkpoint.json Shape

```json
{
  "automation_id": "<project_id>-night-audit",
  "project_id": "<project_id>",
  "last_audit_at": null,
  "last_seen_pr": null,
  "last_seen_worklog_entry": null,
  "last_seen_task_board_state": null,
  "updated_at": null
}
```
