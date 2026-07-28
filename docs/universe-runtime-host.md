# Universe Runtime Host

The Universe application owns local Task Frame provider processes and the worker
dispatcher. They live under `tools/`, not under `.ai/`.

## Boundary

```text
Career Release DB -> installed project Runtime
Universe Runtime Host -> provider CLI / local process -> Task Frame result
Project Runtime -> Execution Guard / source mutation gateway
```

The Host can invoke a declared read-only Task Frame turn only after the Task
Frame Runtime returns `WORKER_INVOCATION_READY`. Provider configuration never
creates a Task Frame, authority, assignment, write scope, or source-mutation
permission.

Current providers are `GROK` and `CODEX`. Provider capability is Host-dependent.
An unavailable CLI remains `UNAVAILABLE`; it is not replaced by a simulated
Worker result.

Every provider request requires `repository_write_scope: NONE` and an empty
mutation scope. Source mutation stays on the attached Project's Execution
Guard and receipt-aware mutation gateway path.

## Local State

Temporary dispatcher request files are stored under:

```text
%LOCALAPPDATA%\Universe\runtime-tmp
```

If `LOCALAPPDATA` is unavailable, the Host uses `%TEMP%\Universe\runtime-tmp`.
Those files are transient Host transport data and are not project evidence.
