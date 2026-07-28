# Task Frame Worker Dispatcher

`worker-dispatch.ps1` is a local Host transport for an already-declared,
read-only Task Frame turn. It does not create a Task Frame, select a Worker
slot, create authority, or apply source mutation.

## Provider Selection

- `GROK` calls `.ai/adapters/grok/invoke.ps1` using `TASK_FRAME_RUNTIME`.
- `CODEX` calls `.ai/adapters/codex/worker.ps1` only when a launchable Codex
  CLI is available. An inaccessible Desktop-bundled executable is reported as
  `UNAVAILABLE`, not treated as a Worker.

Before calling a provider, the dispatcher obtains `WORKER_INVOCATION_READY`
from the active Task Frame Host. It then invokes the provider, claims the turn
with that provider's receipt, and submits the unchanged bounded envelope to
`/v1/task-frame/worker-result`.

The dispatcher currently accepts only `repository_write_scope: NONE` and an
empty mutation scope. A future write-capable transport must use the separate
Execution Binding and receipt-aware mutation gateway path.