---
name: receipt-aware-text-edit
description: Apply one exact, bounded repository text replacement by calculating preimage and payload SHA-256, invoking execution-guard check once, and immediately consuming the one-time receipt through mutation-gateway apply-file.
---

# Receipt-Aware Text Edit

Invocation class: `REFERENCE_RUNTIME_ADAPTER`

Capability classification:

```text
execution_guard = AVAILABLE
file_mutation_gateway = AVAILABLE
mutation_gateway = HOST_DEPENDENT
pre_write_hook = HOST_DEPENDENT
```

This Skill performs one governed `MODIFY` of an existing UTF-8 repository file.
It does not create Authority, Write Scope, Execution Assignment, Host
capability, approval, currentness, or permission. It reuses the existing
Execution Guard and mutation-gateway contracts only.

## When To Use

Use this Skill instead of a raw editor write when the intended change is an
exact, single-occurrence text replacement and the active Session Boot endpoint
already exists.

Do not use this Skill for CREATE, DELETE, MOVE, multi-hunk patches, or fuzzy
search-and-replace. Those remain separate Guard-checked operations.

## Mandatory Route

```text
STOP before mutation
  -> confirm active Session Boot endpoint and token
  -> prepare exact old_text and new_text
  -> invoke the bundled helper once
  -> helper reads exact target bytes
  -> helper preserves UTF-8 BOM and existing LF/CRLF unless the replacement
     intentionally changes those bytes
  -> helper calculates target preimage and payload SHA-256
  -> helper calls execution-guard check once
  -> on EXECUTION_GUARD_PERMITTED, helper immediately calls
     mutation-gateway apply-file with that receipt and Base64 payload
  -> on any blocked or failed result, stop without retry
```

Direct raw writes, delayed receipt consumption, remapping a receipt to another
target, or retrying after block/failure are contract violations.

## Request

Create a UTF-8 `receipt-aware-text-edit.request.v1` document. Prefer a temporary
file under `<project-root>/.ai/runtime/tmp/` when the Project Runtime is
installed. Delete transport files after the command completes.

```json
{
  "schema": "receipt-aware-text-edit.request.v1",
  "endpoint": "<session-boot-endpoint>",
  "token": "<session-boot-token>",
  "session_id": "<active-session-id>",
  "frame_id": "<active-frame-id>",
  "anchor_id": "<active-anchor-id>",
  "target": "<absolute-target-file>",
  "boundary": "<exact-boundary>",
  "source_commit": "<active-source-commit>",
  "validation_ref": "<active-validation-evidence-ref>",
  "old_text": "<exact text to replace once>",
  "new_text": "<exact replacement text>",
  "host_capability": {
    "filesystem_write": "AVAILABLE",
    "pre_write_hook": "AVAILABLE",
    "evidence_ref": "<host-evidence-ref>"
  },
  "approval": {
    "status": "APPROVED",
    "operation": "MODIFY",
    "target": "<absolute-target-file>",
    "boundary": "<exact-boundary>",
    "evidence_ref": "<approval-ref>"
  }
}
```

Optional fields:

```text
task_frame_lineage  -> same shape as execution-guard when Sub mutation is active
observed_at         -> informational; Host adapter replaces with physical time
cli_path            -> absolute path to reference_runtime/cli.py when not default
runtime_tmp         -> absolute directory for transient request JSON
```

`old_text` must match exactly once in the UTF-8 body after any leading UTF-8
BOM. Zero matches and multiple matches fail closed with no Guard call when the
helper can detect them locally, and never mutate.

## Invoke

```text
python .ai/skills/common/receipt-aware-text-edit/scripts/receipt_aware_text_edit.py
  --request <json-file>
  --result <json-file>
  --repo-root <project-root>
```

On Windows PowerShell, pass UTF-8 request and result files. Do not rely on a
transformed pipeline for non-ASCII text.

## Result

```text
TEXT_EDIT_APPLIED
  -> one exact replacement written through mutation-gateway apply-file

TEXT_EDIT_BLOCKED
  -> no repository write from this helper
  -> report exact reasons (ZERO_MATCH, AMBIGUOUS_MATCH, Guard, apply, transport)
  -> do not retry the same receipt or invent a new attempt from failure alone
```

The result JSON redacts endpoint tokens. Do not copy tokens into durable
evidence, logs, Resume Archives, or Skill Observation artifacts.

## Included Scripts

- `scripts/receipt_aware_text_edit.py`: deterministic exact replacement helper
  that calculates hashes and performs immediate check + apply-file

## Boundaries

- Reuses `.ai/skills/common/execution-guard/SKILL.md` request and consume shapes.
- Uses `python .ai/runtime/reference_runtime/cli.py execution-guard check`.
- Uses `python .ai/runtime/reference_runtime/cli.py mutation-gateway apply-file`.
- Does not add a separate hash-generation Skill.
- Does not create authority, Assignment, approval, Host evidence, or currentness.
- Does not retry after a blocked or failed Guard or apply result.
