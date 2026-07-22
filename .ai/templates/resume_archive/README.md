# Resume Archive Template

Use this template to create a repository-backed Resume Archive for a role.

## Target Path

```text
.ai/resume/<role>/
```

## Required Files

```text
.ai/resume/<role>/
  manifest.json
  index.md
  checkpoints.md
  capabilities/<role>.md
  profiles/communication.md
  snapshots/current_state.md
  snapshots/decisions.md
  snapshots/next_actions.md
  history/README.md
  conversation/
```

## Identity Rule

```text
Repository + Role = Resume Identity
```

Do not use old chat session id as the restore identity.

## Resume Archive vs Runtime Archive

Resume Archive restores Role continuity.

Runtime Archive records one scheduled or automation Runtime execution.

```text
Resume Archive
  -> role-scoped recovery source

Runtime Archive
  -> immutable execution record
  -> derived runtime state source
```

Use `.ai/templates/runtime_archive/README.md` for Carrier, Dispatch, Night Audit, or other scheduled Runtime execution records.

## Minimal Manifest Shape

```json
{
  "archive_schema": "1.0",
  "archive_type": "resume-archive",
  "resume_version": 1,
  "checkpoint_id": "<role>-<date>-<checkpoint>",
  "parent_checkpoint": null,
  "repository": "<owner>/<repo>",
  "role": "<Role>",
  "scope": "<scope>",
  "created_at": "<iso8601>",
  "storage_form": "main-unpacked-archive",
  "status": "active",
  "resume_identity": "repository + role",
  "snapshot_files": [
    "snapshots/current_state.md",
    "snapshots/decisions.md",
    "snapshots/next_actions.md"
  ],
  "safety": {
    "contains_hidden_instructions": false,
    "contains_private_chain_of_thought": false,
    "contains_secrets": false,
    "contains_project_source_patch": false
  }
}
```

## Minimal Restore Order

```text
1. manifest.json
2. index.md
3. snapshots/current_state.md
4. snapshots/decisions.md
5. snapshots/next_actions.md
6. capabilities/<role>.md
7. profiles/communication.md
```

Optional:

```text
8. checkpoints.md
9. history/
10. conversation/
```

## Snapshot Rules

Snapshots are fast boot state.

They should answer:

- What is the role?
- What is active now?
- What decisions are already settled?
- What should happen next?

Snapshots should not become full conversation dumps.

## History Rules

History entries explain why a checkpoint matters.

Do not copy full snapshots into history unless there is a specific reason.

Git stores exact diffs. History stores checkpoint intent.

## Safety Rule

Resume Archive restores operating context, not authority.

User approval remains final.
