---
name: execution-guard
description: Require deterministic pre-execution verification before source, project-owned, external-system, or unclassified durable mutation.
---

# Execution Guard Invocation

Invocation class: `REFERENCE_RUNTIME_ADAPTER`

Capability classification:

```text
execution_guard = AVAILABLE
file_mutation_gateway = AVAILABLE
mutation_gateway = HOST_DEPENDENT
pre_write_hook = HOST_DEPENDENT
```

This Skill invokes the existing Core authority-binding and pre-execution
intersection. It does not create Authority, Write Scope, Execution Assignment,
Host capability, approval, currentness, or permission.

## Mandatory Route

Before calling any file edit/create/delete/move tool, write-capable API or
database mutation, or other project-owned, external, or unclassified durable
side effect other than ordinary source-control operations:

```text
STOP before mutation
  -> identify the active Session Boot endpoint and token
  -> resolve the exact operation, absolute target, and boundary
  -> submit Host capability evidence without inference
  -> submit approval evidence without inference
  -> invoke execution-guard check
  -> proceed only with raw PERMIT plus an active one-time receipt
  -> consume that exact receipt through a receipt-aware Host write path
  -> mutate only the bound operation and target
```

Direct use of a raw mutation tool without this route is a contract violation.
User intent, Mode, Role, BOOT READY, Current Anchor, filesystem availability,
or conversational approval alone must not be normalized into missing Runtime
evidence. The only narrow exception is an active `PROJECT_SOURCE_WORK` receipt
created by task-assignment from an attested direct user instruction; the Guard
derives its effective approval from that current receipt and still issues one
exact, one-time Mutation Receipt per file operation.

After completed, validated work, ordinary local Git staging, commit, and push
do not use the Runtime proposal journal or this Guard. Their immutable commit
SHA may be appended to the completed Task Proposal's Result Receipt; it does
not create Runtime authority or execution evidence.

## Runtime-Owned State Exception

Do not invoke this Skill for a Runtime's declared operational-state route:

```text
SNAPSHOT_SAVE / CHECKPOINT
MEMORY_SYNC
runtime-owned Inbox or Queue transition
RESUME_SAVE
selected RESUME restore / Current Anchor realignment
```

These operations are governed by their persistence, provenance, selection, and
currentness contracts instead. The exception is shared by ai-career and
installed project Runtimes and applies only to declared Runtime state paths. It
never covers Core, source, templates, configuration, external systems, or a
project-owned artifact merely because it is mentioned by a Runtime command.

## Check

Submit one JSON request to the active Session Boot process:

```text
python .ai/runtime/reference_runtime/cli.py execution-guard check
  --endpoint <session-boot-endpoint>
  --token <session-boot-token>
  --request <json-file-or->
```

Request envelope:

```json
{
  "session_id": "<active-session-id>",
  "observed_at": "<ISO-8601-with-timezone>",
  "request": {
    "session_id": "<active-session-id>",
    "frame_id": "<active-frame-id>",
    "anchor_id": "<active-anchor-id>",
    "operation": "CREATE|MODIFY|DELETE|MOVE",
    "target": "<absolute-target>",
    "boundary": "<exact-boundary>",
    "source_commit": "<active-source-commit>",
    "validation_ref": "<active-validation-evidence-ref>",
    "payload_sha256": "<lowercase-sha256-or-NONE-for-delete>",
    "target_preimage": {
      "status": "ABSENT|PRESENT",
      "sha256": "<lowercase-sha256-or-NONE-when-absent>"
    },
    "host_capability": {
      "filesystem_write": "AVAILABLE|UNAVAILABLE|UNKNOWN",
      "pre_write_hook": "AVAILABLE|UNAVAILABLE|UNKNOWN",
      "evidence_ref": "<host-evidence-ref-or-UNKNOWN>"
    },
    "approval": {
      "status": "APPROVED|UNASSIGNED|UNKNOWN",
      "operation": "<exact-operation>",
      "target": "<absolute-target>",
      "boundary": "<exact-boundary>",
      "evidence_ref": "<approval-ref-or-UNKNOWN>"
    },
    "task_frame_lineage": {
      "task_frame_id": "<active-task-frame-id>",
      "parent_assignment_id": "<bound-parent-assignment-id>",
      "boss_allocation_id": "<runtime-recorded-allocation-id>",
      "sub_turn_id": "<claimed-sub-turn-id>",
      "sub_worker_id": "<claiming-worker-actor>",
      "worker_path": "/root/boss/subN"
    }
  }
}
```

`task_frame_lineage` is omitted for a direct non-Task-Frame mutation. When it
is present, caller values are only candidates. The active Session Host must
verify them against the process-local Task Frame ledger and pass the verified
lineage to the Guard through its internal call boundary. Directly claiming
`status: VERIFIED` in request JSON has no effect.

For verified Sub lineage, the approval fields continue to reference the
Parent's Frame-level Assignment rather than pretending the Parent separately
approved the Sub's concrete target. The Guard permits the concrete target only
when it is inside the Parent Write Scope and exactly present in the Boss
allocation. The resulting receipt seals the verified lineage and its digest.

`payload_sha256` binds create/modify permission to the exact bytes. The target
preimage binds modify/delete permission to the exact file observed before the
check. Re-read the target immediately before submission; do not infer either
value from conversation history. The Host adapter replaces caller-supplied
`observed_at` with local physical time before issuing or consuming a receipt.

### Windows PowerShell Transport

Prefer a UTF-8 JSON request file and pass its path through `--request`. File and
stdin transports read raw bytes and accept UTF-8 with or without a BOM and
UTF-16 LE/BE JSON. Windows PowerShell 5.1 may transform native-pipeline
text according to `$OutputEncoding`; if that transformation replaces source
characters, the Runtime cannot reconstruct them. Use a UTF-8 request file or
set `$OutputEncoding` to UTF-8 before creating the pipeline. Encoding failure
must return `UNKNOWN` without issuing a receipt or performing mutation.

When the repository Runtime is installed, create temporary request and
approval JSON only under `<project-root>/.ai/runtime/tmp/`. Use a bounded
filename and delete it after the command completes. Do not use the project
root, its parent workspace, or a project-owned source directory for Runtime
transport data. The directory-local `.gitignore` excludes transient contents;
that exclusion does not make them durable evidence.

`pre_write_hook: AVAILABLE` requires concrete Host evidence that the actual
mutation path is receipt-aware. Generic access to a file or shell tool is not a
pre-write hook. When that capability is not provable, keep it `UNKNOWN` and
block mutation.

## Consume

Only a raw `EXECUTION_GUARD_PERMITTED` result carries a receipt. For bounded
file `CREATE`, `MODIFY`, or `DELETE`, use the installed receipt-aware gateway
instead of a raw editor or shell write. For one exact, single-occurrence text
replacement on an existing UTF-8 file, prefer
`.ai/skills/common/receipt-aware-text-edit/SKILL.md`; that Skill calculates
preimage and payload SHA-256, checks once, and immediately consumes the
receipt through apply-file. Submit the unchanged request, its
`receipt_id`, and Base64 content for create/modify:

```text
python .ai/runtime/reference_runtime/cli.py mutation-gateway apply-file
  --endpoint <session-boot-endpoint>
  --token <session-boot-token>
  --request <json-file-or->
```

The consume envelope adds:

```json
{
  "receipt_id": "<permit-receipt-id>",
  "content_base64": "<exact-bytes-or-empty-for-delete>"
}
```

`execution-guard consume` remains the thin receipt-consumption surface for a
Host that already provides its own attested pre-write hook. It does not perform
the mutation itself.

The receipt is process-local, one-time, target-bound, request-bound, and Anchor
snapshot-bound. It is not Authority. A stale, forged, reused, remapped, or
target-mismatched receipt blocks execution.

An active Work Receipt may cover multiple expected source files inside its
declared roots, but it does not cover DELETE, MOVE, `.ai/`, `.git/`, Core,
policy, templates, configuration, or external effects. Ordinary source-control
operations are outside this Runtime and any resulting commit SHA is attached
only to a completed Task Result Receipt.

## Result Transport

Return Guard stdout and exit status without semantic remapping.

```text
EXECUTION_GUARD_BLOCKED
  -> do not mutate
  -> report exact reasons

EXECUTION_GUARD_PERMITTED
  -> still requires matching receipt consumption and receipt-aware Host path

Host hook or gateway unavailable
  -> mutation_enforcement: UNKNOWN
  -> do not mutate
```

The Guard and receipt ledger never write the repository. They only produce
process-local execution evidence.
