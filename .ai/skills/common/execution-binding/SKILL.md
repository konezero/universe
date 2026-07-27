---
name: execution-binding
description: Bind either a strict approved proposal or a bounded direct-user project-source Work Receipt to current process-local state.
---

# Execution Binding

Invocation class: `REFERENCE_RUNTIME_ADAPTER`

Capability classification: `execution_binding = AVAILABLE`

This Skill validates and binds one strict approval to one exact assignment
proposal, or activates a bounded Work Receipt from a direct user instruction.
It does not create canonical authority, modify Core, persist an authority
certificate, or grant final execution permission.

## Project-Source Work Activation

`execution-binding begin-work` is available only after task-assignment has
classified a direct user request as `PROJECT_SOURCE_WORK`. The Instruction
Receipt evidence reference is the activation basis. It is not a reusable
conversation-wide approval and it cannot authorize DELETE, MOVE,
Core/policy/template/configuration changes, or external effects. Ordinary Git
staging, commit, and push after completed work are outside this Runtime path;
their immutable commit SHA may be appended to a Task Result Receipt.

The output is `WORK_RECEIPT_ACTIVATED`. It creates process-local Work Scope and
Assignment state; each later concrete write is still verified by Execution
Guard and receives a one-time target/payload-bound receipt.

## Strict Approval Gate

For strict exact work, display the proposal's exact operation, target,
boundary, and Write Scope. Continue only after an explicit user or Commander
approval whose evidence can be referenced by the Host.

```json
{
  "session_id": "<active-session-id>",
  "proposal": "<unchanged proposal object>",
  "approval": {
    "status": "APPROVED",
    "proposal_id": "<exact-proposal-id>",
    "commander_surface": "<exact-current-commander-surface>",
    "operation": "<exact-operation>",
    "target": "<exact-absolute-target>",
    "boundary": "<exact-boundary>",
    "evidence_ref": "<approval-evidence-ref>",
    "authority_source_ref": "<source-backed-authority-ref>"
  }
}
```

Conversation intent without an evidence reference is not silently normalized
into strict approval. Missing, `UNKNOWN`, stale, or mismatched evidence blocks
the binding.

Only `EXECUTION_BINDING_APPLIED` or `WORK_RECEIPT_ACTIVATED` updates the
process-local Anchor snapshot. Both outputs remain session-scoped and must
still pass `.ai/skills/common/execution-guard/SKILL.md` immediately before each
mutation.

## Task Result Recording

The file-backed Task Proposal journal is task history, not an execution route.
After the Host completes ordinary source-control work, append a Result Receipt
with any already-created immutable commit SHA values. This does not start a
Runtime endpoint, import an SCM action, or create authority.

```text
binding != canonical authority
binding != durable project state
binding != final execution permission
```
