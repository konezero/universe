---
name: memory-sync
description: Package only user-selected memory candidates without making memory active or durable.
---

# Memory Sync

Invocation class: `REFERENCE_RUNTIME_PREPARATION`

Preparation capability: `memory-sync.prepare = AVAILABLE`

Durable handoff capability: `HOST_DEPENDENT`

The reasoning layer may extract candidates, but the user selects what is
eligible for packaging. Invoke `memory-sync prepare` only with that explicit
selection and its source references.

```text
Conversation
  -> candidate extraction
  -> user selection
  -> memory-sync prepare
  -> PREPARED passive artifact
  -> HANDOFF_APPEND when an approved provider writer is available
```

Do not save every conversation, choose candidates for the user, infer a
storage provider, or let stored memory override the Current Anchor. Provider
evidence is required before reporting durable completion. Memory persistence is
an Execution Guard exception only within declared Runtime memory/inbox paths;
source or project-owned changes still use the normal guard.

## Handoff Append

`HANDOFF_APPEND` is distinct from `SOURCE_MUTATION`.

It may persist only a selected append-only handoff artifact under a declared
Runtime-owned path, for example `.ai/memory/inbox/`, `.ai/queue/`, or an
Archive evidence path. A source-only mobile or web Host may perform it when all
of the following are true:

```text
provider write capability: AVAILABLE
exact Runtime-owned append path: approved
candidate selection or user approval: recorded
provider write result: returned
```

The persistence evidence must be provider-native and separate from execution
receipts:

```yaml
schema: ai-career.handoff-append-evidence.v1
operation_class: HANDOFF_APPEND
provider: <github|drive|other>
target_path: <runtime-owned-append-path>
selection_ref: <approved-candidate-or-user-selection>
base_source_ref: <immutable-source-or-parent-ref>
result_ref: <commit|blob|pr|provider-object>
provider_receipt_ref: <provider-evidence-or-UNKNOWN>
```

Existing Inbox records are `OBSERVED_REFERENCE` until a Parent explicitly reads
and adopts them. `HANDOFF_APPEND` never edits source, Core, templates,
configuration, project-owned files, or external systems. Those are
`SOURCE_MUTATION` and require an execution evidence Host; without one, report
`BLOCKED_EXECUTION_HOST_REQUIRED`.
