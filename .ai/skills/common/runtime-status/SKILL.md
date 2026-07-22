---
name: runtime-status
description: Collect installed repository status and process-local Current Anchor status without merging or inflating either source.
---

# Runtime Status

Invocation class: `REFERENCE_RUNTIME_ADAPTER`

Capability classification: `runtime_status_collection = AVAILABLE`

This Skill displays two independent status planes:

```text
repository_status
  -> installed files, manifest, validation, repository runtime

session_status
  -> active process-local session and Anchor snapshot
```

It does not infer one plane from the other. Repository `VERIFIED` does not make
the Session Runtime `READY`; process-local `CURRENT` does not make repository
validation pass.

## Invoke

```text
python .ai/runtime/reference_runtime/cli.py runtime-status
  --repo-root <absolute-project-root>
  --endpoint <session-boot-endpoint>
  --token <session-boot-token>
  --session-id <active-session-id>
```

Return both raw objects and the collection status unchanged. Preserve
`UNKNOWN`, `PARTIAL`, `UNINITIALIZED`, and source-specific failure states.
`semantic_merge` must remain `false`.

## Source-Only OS_STATUS

When only an immutable repository source is attached and no executable Runtime
endpoint is available, do not invoke the combined local collector. Use:

```text
python .ai/runtime/reference_runtime/cli.py os-status source-only
  --request <json-file-or-stdin>
```

The request supplies one provider-observed immutable source coordinate and
lists any checkpoint, Resume Archive, validation, or Runtime Image references
that were actually read. It cannot supply active-state claims.

```json
{
  "schema": "ai-career.source-only-os-status-request.v1",
  "source": {
    "repository": "owner/repository",
    "ref": "refs/pull/269/head",
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "provider": "github-connector",
    "evidence_ref": "github://provider-observation"
  },
  "observed_references": {
    "checkpoints": [],
    "resume_archives": [],
    "validations": [],
    "runtime_images": []
  }
}
```

Return the raw result unchanged:

```yaml
status: SOURCE_READY
checkpoint:
  status: OBSERVED_REFERENCE | NOT_OBSERVED
resume_restore:
  status: NOT_PERFORMED
validation:
  status: NOT_RUN
mode_current_anchor: UNKNOWN
session_runtime: UNKNOWN
repository_runtime: UNKNOWN
executable_runtime_currentness: UNKNOWN
authority: UNASSIGNED
execution_assignment: UNASSIGNED
repository_write: false
```

Reading source documents confirms only that those documents were observed at
the immutable source coordinate. It does not execute Resume restore,
validation, Runtime startup, Current Anchor forwarding, gate activation, Inbox
processing, or authority assignment.
