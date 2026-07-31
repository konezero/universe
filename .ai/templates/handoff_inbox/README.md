# Handoff Inbox Template

Use this template when a registered project Mode or Role needs an explicit
append-only handoff inbox.

Inbox records are `HANDOFF_APPEND` artifacts. A mobile, web, or local Host may
append one only with approved provider write capability, an exact
Runtime-owned path, explicit selection, and provider evidence. Inbox append is
not source mutation and does not imply adoption, execution authority, or an
Execution Assignment.

For repository-backed storage, append only to the provider-observed repository
default branch. A PR, Candidate branch, BOOT source, or current checkout is a
source reference only. Missing installed Runtime metadata must not redirect the
append into the local checkout. If provider write or default-branch evidence is
unavailable, preserve only the passive artifact and stop.

## Assembly Gate

Before creating an inbox, resolve its owner through the installed Mode
Registry. A template name does not create a Mode, Role, or inbox instance.

```text
.ai/runtime/project_instance/mode_registry.json
  -> registered Mode
  -> resolved Role / Scope
  -> MASTER-approved project assembly
  -> .ai/inbox/<registered-inbox-key>/
```

The inbox key must be a project-local, normalized identifier selected during
assembly. It must not be inferred from an unregistered Mode or Role label.

For a Mode-owned Inbox, use the canonical lowercase Mode ID:

```text
MODE_ID -> .ai/inbox/<lowercase-mode-id>/
```

Mode Registry membership is the only activation check. A preserved directory
for an unregistered Mode is inactive and must not receive new handoffs.

## Target Shape

```text
.ai/inbox/<registered-inbox-key>/
  README.md
  queue.md
  processed.md
```

## README.md Minimum Content

```md
# <Owner> Handoff Inbox

Status: candidate
Owner: <registered Mode or Role>
Writer: <approved sender boundary>
Reader: <registered Mode or Role>

## Purpose

Receive selected handoff metadata for explicit review and adoption.

## Core Rule

Inbox stores review work.
Inbox records are not authority.
The receiving Parent decides adoption.
```

## queue.md Minimum Content

```md
# Handoff Inbox Queue

Status: active

## Queue

```yaml
items: []
```
```

## processed.md Minimum Content

```md
# Handoff Inbox Processed

Status: active

## Processed Items

```yaml
items: []
```
```

## Queue Item Shape

```yaml
id: handoff-YYYY-MM-DD-<sender>-<sequence>
status: unread
sender: <registered sender identity>
recipient: <registered inbox owner>
source_ref: <provider or repository evidence ref>
summary: <short summary>
observed_at: <ISO-8601 timestamp>
```

## Rules

1. Inbox items are metadata, not authority.
2. Append requires an approved provider capability and exact target path.
3. Repository append targets the provider-observed default branch; source and
   working branches never select the destination.
4. Processing or adoption is a separate receiving-Parent decision.
5. Do not store secrets.
6. Do not store private chain-of-thought.
7. Keep full evidence in the referenced provider or repository artifact.
8. Deleting a Mode preserves its Inbox in place without moving, archiving,
   deleting, or rewriting it.
9. Re-adding the same Mode reuses the preserved Inbox and continues its
   existing queue and processed history.
10. Adding a Mode with no Inbox assembles this target shape in the same approved
   Mode creation operation.
