# Codex Environment Snapshot Template

Status: template candidate
Repository: `konezero/ai-career`
Scope: reusable environment capture contract for Codex / hybrid executors

## Purpose

This template defines how a Codex-backed executor should save its execution environment before performing project work.

The goal is to make execution context explicit and replayable without relying on conversational memory.

This is especially useful when a project workspace can change freely while ai-career remains the reusable runtime source of truth.

```text
ai-career
  -> standard template / runtime contract

project workspace
  -> concrete executor environment snapshot
  -> available execution modes / tools / write capability / safety notes
```

## Hybrid Executor Rule

Codex App is a hybrid executor.

It may use:

```text
platform connectors
local CLI tools
or a hybrid combination
```

No global priority is assumed.

The execution path is resolved by the Runtime Resolver on a per-task basis.

```text
Task
  -> Resolver
  -> Connector Manifest
  -> Codex Environment Snapshot
  -> Resolved execution path
```

Examples:

```text
PR read / issue read
  -> platform connector may be preferred

local patch / test / filesystem inspection
  -> CLI mode may be preferred

PR context + local patch
  -> connector + CLI hybrid path may be selected
```

The Resolver chooses the path based on:

```text
task type
required capability
read/write need
connector availability
CLI availability
workspace state
safety / write policy
```

## Recommended Project Path

A target project may store its concrete snapshot under:

```text
.ai/runtime/environment/codex_environment_snapshot.md
```

or, for multiple executors:

```text
.ai/runtime/environment/<executor_id>.md
```

## Snapshot Fields

A Codex environment snapshot should record runtime execution capability, not source-control policy.

```yaml
snapshot:
  executor: codex_app
  captured_at: <timestamp>
  project_id: <project-id>
  workspace_path: <local-path-or-unknown>
  execution_modes:
    connector: available | unavailable | unknown
    cli: available | unavailable | unknown
    hybrid: available | unavailable | unknown
  write_mode: read_only | local_write | connector_write | hybrid_write | unknown
  available_connectors:
    - github
  available_cli_tools:
    - shell
    - python
    - pytest
  test_commands:
    - <command>
  build_commands:
    - <command>
  safety_notes:
    - no secrets
    - do not store private chain-of-thought
```

## Required Minimum

At minimum, Codex should capture:

```yaml
executor: codex_app
project_id: <project-id>
workspace_path: <local-path-or-unknown>
execution_modes:
  connector: available | unavailable | unknown
  cli: available | unavailable | unknown
write_mode: read_only | local_write | connector_write | hybrid_write | unknown
available_connectors: []
available_cli_tools: []
```

## Runtime Use

The Resolver may use this snapshot when building a Resolved Task for Codex-backed work.

```text
User Input
  -> Resolver
  -> Connector Manifest
  -> Codex Environment Snapshot
  -> Resolved Task
```

The snapshot does not grant permission by itself.

It only describes the execution environment.

## Write Rule

If a task requires CLI, connector, or hybrid write capability, Codex must verify the snapshot before execution.

```text
Write task
  -> snapshot exists
  -> write_mode compatible
  -> required execution mode available
  -> safety / write policy compatible
  -> proceed
```

If the snapshot is missing or incompatible:

```yaml
task_result:
  state: blocked
  result_type: environment_snapshot_missing_or_incompatible
  pop: false
  reason: codex_environment_not_captured
  next_action: capture_codex_environment_snapshot
```

## Project Freedom Rule

Target projects may change freely.

Therefore, the snapshot is not assumed to remain valid forever.

A project should refresh the snapshot when any of these changes:

```text
workspace path
execution mode availability
available connectors
available CLI tools
write mode
test/build commands
```

## Source Control Boundary

Source-control state is intentionally not part of this base template.

Branch, commit, dirty state, remote, and SCM policy belong to a later project-specific SCM layer.

The base Codex environment snapshot only records execution capability.

## Non-Goals

This template must not store:

- secrets
- tokens
- private chain-of-thought
- large source dumps
- account/order data
- unreviewed sensitive data

## Status

Template candidate.

Recommended next step:

```text
Install this template into GCS and ask Codex to create a concrete environment snapshot before Runtime V3 shadow validation.
```
