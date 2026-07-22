# Conductor Inbox Template

Use this template when a role needs an explicit inbox for Carrier-to-Conductor handoff.

Inbox records are append-only `HANDOFF_APPEND` artifacts. A mobile, web, or
local Host may append one only with approved provider write capability, an
exact Runtime-owned path, explicit selection, and provider evidence. Inbox
append is not a source mutation and does not imply adoption or execution
authority.

## Target Path

```text
.ai/inbox/conductor/
```

## Required Files

```text
.ai/inbox/conductor/
  README.md
  queue.md
  processed.md
```

## README.md Minimum Content

```md
# Conductor Inbox

Status: candidate
Owner: Conductor
Writer: Carrier
Reader: Conductor

## Purpose

Carrier records source events in `.ai/memory/inbox/`, classifies them, and writes promoted queue items here.

Conductor reads the queue and decides what happens next.

## Core Rule

Memory stores facts.
Carrier classifies facts.
Inbox stores review work.
Conductor makes decisions.
```

## queue.md Minimum Content

```md
# Conductor Inbox Queue

Status: active
Reader: Conductor
Writer: Carrier

## Queue

```yaml
items: []
```
```

## processed.md Minimum Content

```md
# Conductor Inbox Processed

Status: active
Reader: Conductor
Writer: Conductor

## Processed Items

```yaml
items: []
```
```

## Queue Item Shape

```yaml
id: inbox-YYYY-MM-DD-<source>-pr-<number>
status: unread
source_repository: <owner/repo>
source_pr: <number>
source_branch: <branch>
memory_event:
  id: memory-YYYYMMDD-NNN
  path: .ai/memory/inbox/YYYY-MM-DD-<short-topic>.md
labels:
  - kind:pr
  - role:project-master
  - flow:bottom-up
  - source:<project-id>
  - target:<target>
  - stage:candidate
  - decision:conductor
  - carry:review
summary: <short summary>
carrier_seen_at: <ISO-8601 timestamp>
cursor:
  source_pr: <number>
```

## Rules

1. Inbox items are metadata, not authority.
2. Promoted Carrier items should reference a memory event.
3. Carrier may append queue items.
4. Conductor may process or move items.
5. Do not store secrets.
6. Do not store private chain-of-thought.
7. Use related PRs for full evidence.
