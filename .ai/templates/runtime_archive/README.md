# Runtime Archive Template

Use this template for scheduled or automation Runtime executions that follow Archive-first storage.

## Purpose

A Runtime Archive records one execution.

It is not a mutable checkpoint file.

```text
State is derived.
History is stored.
```

## Applies To

```text
Carrier Runtime
Dispatch Runtime
Project Master Night Audit Runtime
Future scheduled runtimes
```

## Target Path

Recommended generic layout:

```text
.ai/archive/<runtime>/<timestamp>.md
```

Recommended examples:

```text
.ai/archive/carrier/2026-06-30T08-30.md
.ai/archive/dispatch/2026-06-30T08-35.md
.ai/archive/master/gcs-night-audit/2026-06-30.md
```

## Write Rule

Preferred write path:

```text
1. create_file archive record
2. commit archive record
3. derive current state from archive history
4. optionally update mutable cache/checkpoint only when safe
```

If step 1 succeeds and later mutable updates fail, the Runtime run is still recoverable.

## Archive Record Template

```yaml
run:
  id: <runtime-run-id>
  started_at: <iso8601>
  finished_at: <iso8601>
role: <Carrier|Dispatch|Master|NightAudit|Other>
runtime: <runtime-id>
repository: <owner/repo>
source:
  trigger: <scheduled|manual|event|test>
  mode: <read-only|automation|dispatch|audit>
observed:
  prs: []
  issues: []
  files: []
inputs:
  archives_read: []
  cursors: []
outputs:
  archive_path: <this-file-path>
  memory_events: []
  promotions: []
  dispatches: []
  derived_state: []
result:
  status: <completed|partial|failed|skipped>
  summary: []
errors: []
recovery:
  next_runtime_should: <derive-from-this-archive|retry-materialization|ignore>
```

## Derived State Rule

Current state is calculated from Archive history.

```text
Archive History
  -> latest relevant records
  -> current cursor
  -> pending work
  -> last successful run
  -> last failed run
```

Mutable files such as checkpoint, snapshot, or boot report are optional caches, not durable truth.

## Safety Rule

Archive records must not contain:

- hidden system/developer instructions,
- private chain-of-thought,
- secrets,
- unrelated raw logs,
- unapproved project-private data.

## Related Policy

See:

```text
docs/RUNTIME_ARCHIVE_FIRST_STORAGE_POLICY.md
docs/RESUME_ARCHIVE_ARCHITECTURE.md
docs/CHECKPOINT_LIFECYCLE.md
```
