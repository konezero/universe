# Grok CLI Worker Adapter

This project-local adapter invokes an authenticated Grok CLI process as a
profile-based Task Frame Runtime provider.

## Runtime Profiles

- `READ_ONLY` is the default bounded analysis profile.
- `TASK_FRAME_RUNTIME` executes a bounded Task Frame turn and returns provider
  evidence, but still receives no repository write scope.
- Both profiles require an empty mutation scope. Grok may propose an outcome,
  patch, or next action, but source mutation is performed only through the
  Host receipt-aware gateway after a separate approved assignment.
- External-tool execution is Host-capability-dependent and is not claimed by
  this adapter merely because the Grok CLI is installed.

## Boundary

- Input is a caller-supplied Context Pack. The adapter does not discover or
  load repository files on the Worker''s behalf.
- Requests must declare `repository_write_scope: NONE` and an empty mutation
  scope.
- The CLI uses `--permission-mode plan`, `--sandbox read-only`,
  `--no-subagents`, `--no-memory`, and `--disable-web-search`.
- `sandbox_profile: read-only` records the selected CLI profile. It is not an
  attestation that the host has independently proven sandbox isolation.
- The returned Task Frame evidence contains only `text`, `stop_reason`, the
  Grok session ID, and request ID. It excludes `thought`, usage, and cost
  fields.

## Invocation

`invoke.ps1 -RequestPath <utf8-json-request>` accepts:

```json
{
  "schema": "universe.grok-worker-request.v1",
  "task_frame_id": "<frame>",
  "turn_id": "<turn>",
  "repository_write_scope": "NONE",
  "mutation_scope": {"operations": [], "targets": []},
  "context_pack": "<bounded text>",
  "output_contract": "<bounded output requirement>",
  "max_turns": 3
}
```

The caller remains responsible for Task Frame declaration, capability evidence,
turn claim, and submitting the unchanged bounded result envelope. This adapter
does not create authority, assignment, adoption, or source-write permission.