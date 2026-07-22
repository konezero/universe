# App Runtime Boundary Rule

Status: core runtime candidate
Repository: `konezero/ai-career`
Scope: runtime capability boundary for ChatGPT apps, platform connectors, and Codex / CLI-backed executors

## Purpose

Different apps and sessions expose different runtime capabilities.

A ChatGPT web, mobile, or general desktop app session may still use platform connectors if those connectors are available in the current session.

A Codex App, Codex CLI, or other CLI-backed executor may additionally own local execution environment state and project mutation capability.

The runtime must distinguish app type from actual connector / executor availability.

## Core Rule

```text
Do not infer capability from app type alone.
Always resolve actual Connector / Executor availability for the current session.
```

## Runtime Capability Axes

The Resolver should distinguish four axes:

```text
1. App Capability
   - what the current app class generally supports
   - examples: web, mobile, desktop, codex app

2. Connector Availability
   - which platform connectors are actually available in this session
   - examples: GitHub, Gmail, Drive, Calendar, Web

3. Executor Availability
   - which execution backends are actually available
   - examples: ChatGPT tool executor, Codex App, Codex CLI, local shell

4. Access Mode
   - whether the resolved connector/executor supports read and/or write for this task
```

## Manifest Edit Boundary

Runtime does not magically change connector or executor availability.

It only reads the current app capability, current connector state, and current manifest/snapshot state.

Apps where the user cannot modify local runtime files during the session must treat manifests as preconfigured inputs.

```text
Mobile / web / general app
  -> cannot assume local manifest edit during runtime
  -> initial connector/executor setup must be reflected in the manifest beforehand
  -> runtime resolves from the manifest as loaded
```

Local project / Codex-backed environments may maintain concrete local runtime instances.

```text
Project local runtime
  -> may edit local manifest / snapshot
  -> may keep project-specific runtime state outside source control
  -> may refresh concrete environment state independently of ai-career templates
```

Therefore:

```text
ai-career owns templates and contracts.
Project/runtime instances own mutable local state.
```

## Correct Interpretation

ChatGPT web/mobile/general app:

```text
May use connected platform connectors.
Must not assume local filesystem / shell / Codex CLI access.
Must not assume manifest edits take effect unless the manifest source is actually updated and reloaded.
```

Codex App / CLI-backed executor:

```text
May use local CLI / filesystem / test tools when available.
May also use platform connectors if connected.
May choose connector, CLI, or hybrid execution per task.
May own concrete local snapshot / manifest instances.
```

## Connector Availability Rule

A connector must be treated as unavailable unless it is actually present in the current runtime session.

```yaml
connector_status:
  connector: github
  registered: true
  available: true | false | unknown
  access:
    read: true | false | unknown
    write: true | false | unknown
  source: connector_manifest | platform_tool | runtime_state | unknown
```

Resolution rules:

```text
registered=false
  -> BLOCK

registered=true but available=false
  -> BLOCK

registered=true but available=unknown
  -> BLOCK or ask for confirmation before execution

available=true but required access missing
  -> BLOCK
```

## Read / Write Rule

Read and write must be resolved separately.

```text
Read task
  -> connector registered
  -> connector available
  -> access.read true
  -> execute

Write task
  -> connector registered
  -> connector available
  -> access.write true
  -> write_policy compatible
  -> explicit authority present
  -> execute
```

A connector may allow read but not write.

A task must not upgrade from read to write implicitly.

## Executor Availability Rule

CLI-style executors are not ordinary platform connectors.

If local execution is required but the current runtime has no CLI-backed executor, the task must be blocked.

```yaml
task_result:
  state: blocked
  result_type: executor_not_available
  pop: false
  reason: current_session_has_no_cli_backed_executor
  next_action: run_from_codex_app_or_cli_backed_executor
```

Do not suggest platform connector linking when the missing capability is local CLI execution.

## App Boundary Examples

### Mobile app with GitHub connector

```text
Task: check open PRs
App: mobile
Connector: GitHub available
Access: read
Result: execute
```

### Mobile app requiring local shell

```text
Task: run pytest locally
App: mobile
Executor: CLI unavailable
Result: BLOCK
```

### Mobile app with GitHub write connector

```text
Task: append a user-selected Memory Inbox artifact
App: mobile
Connector: GitHub available
Access: write
Path: declared Runtime-owned append path
Approval: explicit selection recorded
Result: HANDOFF_APPEND with provider commit/blob evidence
```

This does not authorize source, Core, template, configuration, or project file
changes. A source mutation without an execution evidence Host returns
`BLOCKED_EXECUTION_HOST_REQUIRED`.

### Web app with GitHub write connector

```text
Task: update a Runtime-owned append-only handoff artifact
App: web
Connector: GitHub available
Access: write
Authority: explicit user task
Result: HANDOFF_APPEND with provider evidence
```

### Web/mobile app needs new connector configuration

```text
Task: use connector not in manifest
App: web/mobile
Manifest: not editable in-session
Result: BLOCK
Next: update manifest source first, then reload/boot
```

### Desktop app without Codex CLI executor

```text
Task: patch local source and run tests
App: desktop
Executor: Codex CLI unavailable
Result: BLOCK
```

### Codex App hybrid execution

```text
Task: read PR, patch local file, run tests
Executor: Codex App available
Connector: GitHub available
CLI: available
Result: hybrid execution path may be resolved per task
```

## Snapshot Ownership

ai-career owns:

```text
- contracts
- templates
- Core Runtime rules
- Connector Manifest contracts
```

Project workspaces own:

```text
- concrete environment snapshots
- local runtime state
- workspace-specific executor state
- project-specific SCM state when defined
```

## Session Capture Rule

For Codex App / CLI-backed executors:

```text
Session Start
  -> capture environment snapshot once
  -> reuse while valid
  -> refresh only when dirty or when required by task validation
```

For ChatGPT web/mobile/general app:

```text
Session Start
  -> follow runtime protocol
  -> do not claim local snapshot capture unless a real executor provides it
```

## Status Reporting Rule

When reporting environment or runtime status, the assistant must distinguish source-backed state from protocol-only state.

```text
Confirmed
  -> read from connector, runtime state file, project snapshot, manifest source, or tool output

Inferred
  -> derived from current conversation behavior

Unknown
  -> no source exists
```

## Relation to Codex Environment Snapshot Template

The Codex Environment Snapshot Template defines what a capable Codex or CLI-backed executor should capture.

This rule defines when such capture is possible and when it must be treated as unavailable.

## Status

Core runtime candidate.

Recommended next step:

```text
Index this rule in .ai/core/README.md and use it when validating Runtime V3 across ChatGPT web/mobile and Codex-backed project sessions.
```
