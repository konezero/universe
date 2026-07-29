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
being present in `PATH`.

## Windows Native CLI Boundary

On Windows, every active Runtime Host provider invocation follows
`universe.windows-native-cli.v1`:

```text
UniverseRuntimeHost
  -> RuntimeWorkerDispatcher
  -> NativeCliRequest(executable, argv[], cwd, environment, timeout)
  -> subprocess(shell=False)
  -> provider executable
```

The executable and every argument remain separate values. PowerShell command
strings, `Start-Process -ArgumentList`, `cmd /c`, batch shims, and direct
provider invocation from a `.ps1` adapter are not active execution routes.
Legacy PowerShell adapter files fail closed with
`WINDOWS_NATIVE_CLI_ROUTE_REQUIRED`.

Provider capability checks use the same runner as provider work. Grok prompts
are written as transient UTF-8 files and passed with `--prompt-file`; structured
JSON schemas remain one explicit argument. Codex prompts remain one explicit
argument in the runner's argv array. Empty strings, spaces, quotes, JSON,
newlines, non-ASCII text, timeout status, and separated stdout/stderr are
covered by Runtime Host regression tests.

The Python interpreter hosting Universe is not discovered through a PowerShell
shim for provider execution. `grok.exe` and `codex.exe` must resolve to native
executables; `.bat`, `.cmd`, and `.ps1` entrypoints are rejected.

Other product-owned external CLI paths must satisfy the same contract even when
they need a specialized transport. The Release Builder's byte-oriented Git
object reader therefore keeps an explicit argv array, `shell=False`, separated
stdout/stderr, and a fixed timeout rather than routing binary blob output through
the text-oriented provider runner.

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

Fresh Project planning uses a separate process-local binding:

```text
POST /v1/runtime/planning-binding
GET  /v1/runtime/planning-binding
```

The Host supplies the active loopback Session Boot endpoint, token, session,
Anchor, frame, Parent actor, and evidence references. Universe keeps the full
binding only in process memory. The GET response is redacted, and SQLite never
receives the endpoint or token. Restarting Universe returns the binding to
`UNBOUND`; a Host must attach it again.

`POST /v1/fresh-project-refinement-runs` creates an exact, durable one-turn
Planning Frame proposal without invoking a model. The proposal fixes the
provider, model, BOSS turn, read-only repository boundary, request digest, and
Composition digest. Only
`POST /v1/fresh-project-refinement-runs/{run_id}/execute` with matching
`proposal_id` and `plan_digest` starts the provider.

Planning adapters return text only to the dispatcher process. In
`STRUCTURED_JSON` mode the dispatcher parses that text before submitting the
Worker envelope, requires one JSON object, and discards the raw text. The Task
Frame journal and Universe database receive only the validated structured
object and bounded provider receipt metadata.

Fresh Project requests also carry an explicit JSON Schema. The Grok adapter
passes it through the CLI `--json-schema` option so the provider is constrained
before dispatcher validation. A failed adapter response preserves its bounded
`status`, `stage`, and `reason`; raw response text still does not cross the
Runtime Host boundary.

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

When a Sub turn carries Boss-declared project Skill bindings, the Host appends
one bounded observation per binding to the unchanged Worker envelope. The Host
records only the declared binding digest, Runtime-selected model reference,
terminal result receipt, and measured adapter duration. A completed transport
maps to `outcome: SUCCEEDED`, while validation remains `NOT_RUN`; the Host does
not infer task quality from model text. The project Task Frame journal validates
and owns these observations. The model reference is canonicalized as
`provider://<PROVIDER>/model/<MODEL>`, allowing Universe Bench queries to expose
the Provider dimension without changing the installed Task Frame schema.
Universe persists only the observation count in the redacted invocation
timeline. Publishing the reviewed Result Packet requires a separate,
digest-bound Project Master approval before the Project-to-Universe queue
accepts it.
