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

`worker_run_ref` is a transient Host correlation key, not a receipt. A
completed Worker envelope carries exactly one `result_receipt_ref`; the same
reference may also appear in `evidence_refs`, but it is not a second receipt.

The Grok adapter resolves `%GROK_HOME%\bin\grok.exe` (or
`%USERPROFILE%\.grok\bin\grok.exe`) directly. It does not depend on `grok`
being present in `PATH`, and provider invocation is a PowerShell process rather
than a Python virtual-environment operation.

## Local State

Temporary dispatcher request files are stored under:

```text
%LOCALAPPDATA%\Universe\runtime-tmp
```

If `LOCALAPPDATA` is unavailable, the Host uses `%TEMP%\Universe\runtime-tmp`.
Those files are transient Host transport data and are not project evidence.

## Universe Service Integration

The local Universe service can query provider capability through
`GET /v1/runtime/providers` and invoke one active Task Frame turn through
`POST /v1/projects/{project_id}/runtime-worker-invocations`.

The POST body carries a current loopback Task Frame endpoint and token only for
the duration of that request. Universe persists a redacted invocation timeline:
provider, session/frame/turn identifiers, request/context/output digests, status,
and the final `result_receipt_ref`. It never persists the endpoint, token,
Context Pack
contents, or Worker result text. The Project Task Frame journal remains the
canonical result record.

The route does not queue a live Worker call. Session credentials are volatile,
so an invocation is synchronous against an already active Task Frame. A later
durable Project dispatch can request that a Master prepare a new session.
