# Project Instance Runtime Rule

Status: core runtime candidate
Repository: `konezero/ai-career`
Scope: relationship between ai-career templates/contracts and project-local runtime instances

## Purpose

ai-career owns reusable runtime contracts and templates.

Projects own concrete runtime instances, environment snapshots, and mutable local execution state.

This rule defines the boundary between the stable runtime contract layer and project-specific runtime state.

## Core Rule

```text
ai-career owns contracts and templates.
Projects own instances and mutable runtime state.
```

## Manifest to Instance Relationship

Connector Manifests and runtime templates define what may exist.

Project instances define what actually exists for a specific workspace and session.

```text
ai-career contract/template
  -> Connector Manifest contract
  -> Snapshot template
  -> Project runtime instance
  -> Resolved Task
```

In short:

```text
Manifest / Template
  -> Instance / Snapshot
```

## Ownership Boundary

ai-career owns:

```text
- Core Runtime rules
- Connector Manifest contracts
- Task Queue / Normalizer contracts
- Status source rules
- App/runtime boundary rules
- Snapshot templates
```

Project runtime owns:

```text
- concrete connector availability
- concrete executor availability
- concrete environment snapshot
- local manifest instance
- local runtime state
- workspace-specific paths/tools
- project-specific SCM state, when enabled
```

## Source Control Boundary

Project-local runtime instances may be intentionally excluded from source control.

```text
Project local runtime state
  -> may exist only on local machine
  -> may be regenerated at session start
  -> may be refreshed when dirty
  -> does not need to be committed by default
```

ai-career templates should be version-controlled.

Project snapshots should be version-controlled only when the project explicitly decides they are safe and useful to share.

## Runtime Resolution Flow

The Resolver should not resolve directly from generic templates when a project instance exists.

Recommended order:

```text
1. Load ai-career contract/template
2. Load project manifest/snapshot instance if available
3. Resolve actual connector/executor/access from project instance
4. If no project instance exists, use template only as a schema or setup guide
5. If required capability remains unknown, BLOCK rather than guess
```

## Example

ai-career template:

```yaml
codex_environment_snapshot_template:
  executor: codex_app
  execution_modes:
    connector: available | unavailable | unknown
    cli: available | unavailable | unknown
    hybrid: available | unavailable | unknown
```

Project instance:

```yaml
project_runtime_instance:
  project: gcs
  executor: codex_app
  workspace_path: C:/work/gcs
  execution_modes:
    connector: available
    cli: available
    hybrid: available
  available_connectors:
    github:
      read: true
      write: true
  local_tools:
    pytest: available
    git: available
```

The template defines the shape.

The instance defines the current truth.

## App / Web / Mobile Boundary

For apps where users cannot modify project files or manifests during the session:

```text
Runtime cannot assume manifest edits are immediately applied.
Initial setup must be reflected in the manifest/source before boot or reload.
```

For project-local Codex / CLI-backed runtimes:

```text
Runtime may update local instance state.
Local state may remain outside source control.
```

## Status Reporting Rule

When reporting project runtime status:

```text
Confirmed
  -> read from project instance, manifest source, snapshot file, connector output, or tool result

Unknown
  -> no loaded instance/source exists
```

Do not report template defaults as confirmed project state.

## Status

Core runtime candidate.

This rule complements:

```text
TASK_QUEUE_RUNTIME_V1.md
APP_RUNTIME_BOUNDARY_RULE.md
RUNTIME_STATUS_SOURCE_RULE.md
USER_INTERRUPT_RUNTIME_RULE.md
```
