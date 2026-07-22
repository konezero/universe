# Master Merge Archive Template

Use this template for Master roles that integrate Worker handoffs.

## Purpose

Master is the operational routing and merge role.

```text
Master orchestrates.
Workers execute focused work.
Conductor governs role boundaries.
User approves final changes.
```

## Target Path

```text
.ai/resume/master/
```

## Required Files

```text
.ai/resume/master/
  manifest.json
  index.md
  capabilities/master.md
  profiles/communication.md
  snapshots/current_state.md
  snapshots/decisions.md
  snapshots/next_actions.md
  merge/inbox.md
  merge/candidates.md
  merge/adopted.md
  history/README.md
```

## Manifest Additions

```json
{
  "role_class": "master",
  "merge_files": [
    "merge/inbox.md",
    "merge/candidates.md",
    "merge/adopted.md"
  ],
  "worker_sources": []
}
```

## Merge Flow

```text
Worker handoff
  -> merge/inbox.md
  -> merge/candidates.md
  -> User decision
  -> merge/adopted.md
  -> checkpoint when meaningful
```

## Inbox Entry Shape

```text
Source worker
Task
Scope
Received at
Status
Questions
```

## Candidate Entry Shape

```text
Candidate ID
Source workers
Target files or docs
Proposed changes
Conflicts
Risk level
Validation plan
Rollback plan
User decision needed
```

## Adopted Entry Shape

```text
Candidate ID
Approved by
Commit or PR reference
What changed
Follow-up checkpoint
```

## Conflict Rule

When Worker outputs conflict, Master summarizes the conflict and asks for a decision.

## Scope Rule

Master prepares integration plans within the scope delegated by the User.

Governance changes should be reviewed by Conductor.

## Checkpoint Trigger

Create a Master checkpoint when:

- Worker output is adopted.
- Merge policy changes.
- Integration state changes materially.
- Cross-worker conflict is resolved.
- Project execution plan changes.
