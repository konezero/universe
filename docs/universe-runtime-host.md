# Universe Runtime Host

The Universe application owns local Task Frame provider processes and the worker
dispatcher. They live under `tools/`, not under `.ai/`.

## Boundary

```text
Career Release DB -> installed project Runtime
Universe Project Lifecycle Host -> OS_INSTALL / OS_UPDATE -> validate/status
Universe Runtime Host -> Universe ACP Gateway -> provider process -> Task Frame result
Project Runtime -> Execution Guard / source mutation gateway
```

The Project Lifecycle Host is separate from the provider Worker Host and the
resident Project Master. It consumes an exact approved Release proposal,
re-verifies the immutable artifact and current installation state, materializes
the `universe-release-db` source bundle, and invokes the pinned ai-career Host
lifecycle entry. It accepts only matching `PASS`, `VERIFIED`, and
`READY_FOR_BOOT` evidence, then records one idempotent application receipt.
Fresh install therefore does not require a resident Master to exist first.

The Host can invoke a declared read-only Task Frame turn only after the Task
Frame Runtime returns `WORKER_INVOCATION_READY`. Provider configuration never
creates a Task Frame, authority, assignment, write scope, or source-mutation
permission.

Current providers are `GROK`, `CODEX`, and `CLAUDE`. Provider capability is
Host-dependent. An unavailable CLI remains `UNAVAILABLE`; it is not replaced
by a simulated Worker result.

Every provider request requires `repository_write_scope: NONE` and an empty
mutation scope. Source mutation stays on the attached Project's Execution
Guard and receipt-aware mutation gateway path.

`worker_run_ref` is a transient Host correlation key, not a receipt. A
completed Worker envelope carries exactly one `result_receipt_ref`; the same
reference may also appear in `evidence_refs`, but it is not a second receipt.

All product-owned external executables resolve through the local Host Profile.
The active Profile path is selected by `AI_CAREER_HOST_PROFILE` and defaults to
`%LOCALAPPDATA%\ai-career\host.json`. Runtime callers do not independently
search `PATH`, inspect `GROK_HOME`, inspect `CODEX_CLI_PATH`, or reuse a Python
shim.

The Universe service initializes the Profile once. Initial discovery accepts
the per-tool `AI_CAREER_*_EXECUTABLE` overrides, the current native Python
process, known native application locations, and `PATH`. `GROK_HOME` and
`CODEX_CLI_PATH` are migration inputs only. Once persisted, every Runtime
caller consumes the Profile record. A missing, stale, script-based, or failed
tool is `UNAVAILABLE`.

## Windows Native CLI Boundary

On Windows, every active Runtime Host provider process follows the native
executable and argv validation of `universe.windows-native-cli.v1`:

```text
UniverseRuntimeHost
  -> RuntimeWorkerDispatcher
  -> UniverseAcpGateway
  -> NativeCliRequest(executable, argv[], cwd, environment)
  -> open_native_cli(shell=False, stdin/stdout pipes)
  -> provider ACP or app-server process
```

The executable and every process-start argument remain separate values.
PowerShell command strings, `Start-Process -ArgumentList`, `cmd /c`, batch
shims, and direct provider invocation from a `.ps1` adapter are not active
execution routes. Legacy PowerShell adapter files fail closed with
`WINDOWS_NATIVE_CLI_ROUTE_REQUIRED`.

Provider capability checks use the bounded one-shot runner. Provider turns use
the persistent native process opener for Grok ACP and Codex app-server JSON-RPC
stdio sessions. Claude uses bounded print-mode JSON calls, resumes only the one
target-scoped `session_id`, and transports prompt text through a temporary stdin
file. Claude resident sessions expose only `Read`, `Glob`, and `Grep` under
`--permission-mode plan` and no MCP configuration. Task Frame Claude calls also
use `--tools ""` and `--no-session-persistence`. Exact argv boundaries,
non-ASCII protocol text, structured results, and provider permission boundaries
are covered by Runtime Host regression tests.

Python, Git, Grok, Codex, and Claude must resolve to native executables. `.bat`,
`.cmd`, and `.ps1` entrypoints are rejected. The Profile stores executable paths,
versions, verification timestamps, discovery sources, and the non-secret
`GROK_HOME` launch environment. Tokens, credentials, and provider sessions are
not Profile fields.

Other product-owned external CLI paths must satisfy the same contract even when
they need a specialized transport. The Release Builder's byte-oriented Git
object reader therefore keeps an explicit argv array, `shell=False`, separated
stdout/stderr, and a fixed timeout rather than routing binary blob output through
the text-oriented provider runner.

## Local State

Temporary Runtime lifecycle and Task Frame request files are stored under:

```text
%LOCALAPPDATA%\Universe\runtime-tmp
```

If `LOCALAPPDATA` is unavailable, the Host uses `%TEMP%\Universe\runtime-tmp`.
Those files are transient Host transport data and are not project evidence.
Grok and Codex carry prompts in protocol messages. Claude print mode uses a
per-call stdin file in this directory and deletes it immediately after return.

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


## Worker Binding Resolution

A user-managed Worker Binding chooses the preferred Provider, model, effort,
and Skill references for one Worker role and task type. The five roles are
`IMPLEMENTER`, `REVIEWER`, `QA`, `SCOUT`,
and `ROUTINE`. Bindings are stored in Universe SQLite and edited
through the Runtime settings UI or:

```text
GET  /v1/settings/worker-bindings
POST /v1/settings/worker-bindings
POST /v1/settings/worker-bindings/resolve
```

Resolution order is exact and deterministic:

```text
PROJECT exact task type
-> PROJECT wildcard
-> UNIVERSE exact task type
-> UNIVERSE wildcard
-> DEFAULT_AUTO
```

The resolved profile is copied into an immutable
`universe.worker-binding-snapshot.v1` with a `binding_digest`
before an invocation is claimed. Later settings changes cannot rewrite that
invocation's Provider, model, effort, or Skill inputs. A binding is routing
input only: it does not create a Task Frame, currentness, authority, assignment,
approval, or repository write scope. Skill references become executable
observations only after a Boss declares them in a bounded Task Frame allocation
and the Host returns evidence for that exact binding digest.

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

## Project Master Skill Plan context

An adopted Universe Skill Plan crosses the Project Master Bridge through
`POST /v1/project-master/skill-plans/apply`. The request carries one exact
handoff plus its digest-bound Universe approval. The resident Host verifies the
adoption and handoff digests, then stores the selected plan in its file-backed
session database.

The Host then resolves each Skill ID against exactly one installed
`.ai/skills/**/<skill_id>/SKILL.md` under the Project root. It records a passive
binding proposal with the project-relative `skill_ref` and file digest. Missing,
ambiguous, or out-of-root Skill refs block application.

This is planning context, not Task Frame execution. The Host creates no Task
Frame and grants no authority, assignment, write scope, or repository mutation
permission. The operation is idempotent by `handoff_id`; a conflicting digest
is rejected. The durable store retains every applied context and binding
proposal while provider prompts receive only the latest bounded set.
