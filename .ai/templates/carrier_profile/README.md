# Carrier Profile Template

Use this template to create a project-specific Carrier definition.

Carrier Profile and Automation are separate objects.

Install means ensuring both exist.

## Target Path

```text
.ai/carriers/<carrier_id>/README.md
.ai/carriers/<carrier_id>/scheduler_instruction.md
.ai/carriers/<carrier_id>/checkpoint.json
```

## Profile Shape

```md
# <Carrier Name>

Status: candidate
Type: <project-carrier | career-carrier>
Repository: <owner/repo>
Role: Carrier
Mode: Automation
Session Label: Carrier | ai-career

## Purpose

<What this Carrier watches and why.>

## Automation

```yaml
automation:
  status: installed | missing | disabled | broken | unknown
  name: <Carrier Name>
  cadence: monitoring
  runtime: platform-managed
```

## Watch Target

```yaml
watch:
  provider: github
  repository: <owner/repo>
  target: pull_requests
```

## Pipeline

```yaml
pipeline:
  raw_event_first: true
  memory_inbox: .ai/memory/inbox/
  conductor_inbox: .ai/inbox/conductor/queue.md
```

## Checkpoint

```yaml
checkpoint:
  path: .ai/carriers/<carrier_id>/checkpoint.json
  cursors:
    - last_processed_pr
    - highest_seen_pr
    - memory_cursor
    - inbox_cursor
```

## Install Semantics

```text
<Carrier Name> 설치
```

means:

```text
ensure profile exists
ensure scheduler instruction exists
ensure checkpoint exists
ensure automation is installed or provide install instruction
ensure monitoring is enabled when supported
```

## Rules

1. Profile existence does not prove Automation is installed.
2. Load scheduler instruction before checkpoint state.
3. Restore checkpoint state without resetting cursors.
4. Record raw events before promoting review work.
5. Reference memory events from promoted inbox items.
6. Do not decide adoption.
```

## scheduler_instruction.md Shape

```md
# <Carrier Name> Scheduler Instruction

## Boot Order

1. Load this scheduler instruction.
2. Restore checkpoint state.
3. Scan watch targets.
4. Write new raw events to `.ai/memory/inbox/`.
5. Classify events.
6. Promote review work to `.ai/inbox/conductor/queue.md` when required.
7. Save checkpoint cursors.
8. Sleep.

## Principle

Memory stores facts.
Carrier classifies facts.
Inbox stores review work.
Conductor makes decisions.
```

## checkpoint.json Shape

```json
{
  "carrier_id": "<carrier_id>",
  "automation_status": "unknown",
  "last_processed_pr": null,
  "highest_seen_pr": null,
  "last_processed_commit": null,
  "memory_cursor": {
    "last_memory_event_id": null,
    "last_memory_event_path": null
  },
  "inbox_cursor": {
    "last_inbox_item_id": null
  },
  "updated_at": null
}
```

## Status Output Shape

```text
Carrier: <name>
Profile: present|missing
Automation: installed|missing|disabled|broken|unknown
Monitoring: on|off|unknown
Checkpoint: <path>
Cursor: <summary>
```
