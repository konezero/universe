# ai-career Reference Runtime

Status: canonical production runtime surface

This package promotes the reviewed generic Task Frame ledger and Anchor Session
Memory behavior into a distribution-safe Python surface. It uses only the
Python standard library and ships no proof fixtures, demos, receipts, execution
logs, cross-Host comparators, or benchmarks.

## Boundaries

The package provides deterministic, process-local behavior only:

```text
Task Frame ledger
  -> declared turns, append-only evidence, candidate Result Packets

Anchor Session Memory
  -> explicit session_id, opaque snapshot cache, raw event evidence

Mode Registry Resolution
  -> source-backed Mode allow-list validation and MASTER-managed project plans

Session Surface Observation
  -> transport evidence isolation, Commander input allow-list transitions,
     protected-coordinate mutation detection

Session Boot Executor
  -> validated project-runtime status, fresh session/frame coordinate,
     current interpretation basis, fresh Session Boot Anchor derivation,
     process-local Runtime Image activation

Execution Guard
  -> current Anchor evidence intersection, blocked decision,
     process-local one-time permit receipt

Execution Assignment / Binding
  -> exact mutation proposal, exact approval binding,
     process-local Anchor update without canonical authority creation

Runtime Status Collection
  -> raw installed repository status plus raw process-local session status,
     with no semantic merge

Continuity Command Preparation
  -> deterministic checkpoint and memory candidates, bounded Resume and
     conversation discovery, and source-backed Anchor currentness evaluation
```

It does not create canonical authority, final execution permission, resume
state, or Parent adoption. It may prepare one mutation-specific Execution
Assignment proposal and bind exact approval plus source-backed authority
evidence into the current process-local Anchor snapshot. That binding remains
session-scoped and still requires Execution Guard. Task Frame results stay
`CANDIDATE`; missing Worker capability stays `UNKNOWN`; Anchor snapshots remain
opaque Host-supplied evidence. Session Boot may create `CURRENT` only for the
fresh in-process `session_id + frame_id` that it just activated from a validated
project runtime. It never upgrades prior durable currentness or restores a prior
session by inference. Continuity preparation does not persist a checkpoint or
memory item, activate a Resume candidate, adopt recalled conversation, or
invent a physical freshness threshold.

For an installed project Host, each project Mode's Current Anchor and retired
Beyond Anchor footprints are persisted in a project-local SQLite file under
`.ai/runtime/anchor_store/`. This durable store preserves raw coordinates and
candidate history only: it does not restore an executable Runtime, make a
snapshot current by itself, or create authority, assignment, or repository
write permission. Session Boot/Guard state and receipts remain process-local
SQLite `:memory:` state. Task Frame journaling remains a separate store.

Session Boot protects its `activate` and `stop` routes with a separate
lifecycle token that is not returned in Host metadata. The public session token
cannot replace the Boot-owned snapshot with caller-supplied state.

The Task Frame profile loader preserves the reviewed immutable-commit-bound v0
profile schema for behavioral compatibility. It also accepts an installed v1
profile whose contract files match the local distribution manifest. The
package ships `profiles/task-frame-debate-v1.json` as the default bounded
Boss/reviewer profile, but the Host still selects and invokes it explicitly.
That installed profile requires an exact pre-execution proposal and user
approval. The proposal is the model re-selection point: changing a model,
reasoning effort, Worker slot, or route changes the digest and invalidates prior
approval. Parent self-substitution and missing Host Worker receipts are blocked.
The proposal also binds Parent visibility to
`BOUNDED_RETURNED_MESSAGES_ONLY`: returned Worker messages/results and journal
evidence may be observed read-only; hidden reasoning is never exposed or
reconstructed.

## CLI

`cli.py` emits one JSON value and a process exit code. Supported commands are:

```text
python .ai/runtime/reference_runtime/cli.py capabilities
python .ai/runtime/reference_runtime/cli.py capability <name>
python .ai/runtime/reference_runtime/cli.py mode-registry list \
  --repo-root <project-root>
python .ai/runtime/reference_runtime/cli.py mode-registry show \
  --repo-root <project-root> --mode <MODE>
python .ai/runtime/reference_runtime/cli.py mode-registry plan \
  --repo-root <project-root> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py task-frame propose \
  --repo-root <path> --profile <path> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py task-frame run \
  --repo-root <path> --profile <path> --database <frame.sqlite3> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py task-frame continue \
  --repo-root <path> --profile <path> --database <frame.sqlite3> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py task-frame status \
  --repo-root <path> --profile <path> --database <frame.sqlite3>
python .ai/runtime/reference_runtime/cli.py anchor-memory batch \
  --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py anchor-memory serve \
  --port <loopback-port> --token <per-run-token>
python .ai/runtime/reference_runtime/cli.py session-boot serve \
  --repo-root <project-root> --session-id <session-id> \
  --anchor-id <anchor-id> --host-action <host-action> \
  --session-location <runtime-writer> --commander-surface <user-surface> \
  --execution-surface <runtime-writer> --repository-location <repo-location>
python .ai/runtime/reference_runtime/cli.py execution-binding propose \
  --endpoint <session-boot-url> --token <token> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py execution-binding apply \
  --endpoint <session-boot-url> --token <token> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py git-proposal propose-push \
  --repo-root <project-root> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py git-proposal approve \
  --repo-root <project-root> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py git-proposal status \
  --repo-root <project-root> --proposal-id <proposal-id>
python .ai/runtime/reference_runtime/cli.py execution-binding import-git-proposal \
  --endpoint <session-boot-url> --token <token> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py runtime-status \
  --repo-root <project-root> --endpoint <session-boot-url> \
  --token <token> --session-id <active-session-id>
python .ai/runtime/reference_runtime/cli.py os-status source-only \
  --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py execution-guard check \
  --endpoint <session-boot-url> --token <token> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py execution-guard consume \
  --endpoint <session-boot-url> --token <token> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py mutation-gateway apply-file \
  --endpoint <session-boot-url> --token <token> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py mutation-gateway apply-git \
  --endpoint <session-boot-url> --token <token> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py checkpoint prepare \
  --repo-root <project-root> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py memory-sync prepare \
  --repo-root <project-root> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py resume-restore discover \
  --repo-root <project-root> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py conversation-recall query \
  --repo-root <project-root> --request <json-file-or->
python .ai/runtime/reference_runtime/cli.py anchor-currentness observe \
  --endpoint <session-boot-endpoint> --token <token> \
  --session-id <id> --frame-id <id> --anchor-id <id>
python .ai/runtime/reference_runtime/cli.py anchor-currentness evaluate \
  --repo-root <project-root> --request <json-file-or->
```

Canonical request content is UTF-8 JSON. File and stdin transports read raw
bytes and accept UTF-8 with or without a BOM plus UTF-16 LE/BE JSON used by
Windows PowerShell. Unsupported input encoding returns
`REQUEST_ENCODING_UNSUPPORTED` and performs no Runtime operation. A UTF-8
request file remains the preferred cross-Host transport because a parent shell
may irreversibly replace characters before the child process receives them.

An unsupported capability returns `UNKNOWN` and a nonzero exit code. The CLI
does not implement `OS_UPDATE` or `OS_VALIDATE`, and it is not an installer.
`os-status source-only` accepts only an immutable provider-observed source and
observed document references. It returns `SOURCE_READY` while keeping Resume
restore `NOT_PERFORMED`, validation `NOT_RUN`, and Runtime / Mode Current
Anchor state `UNKNOWN`.
`session-boot serve` consumes the installed distribution runtime's raw `status`
result after installation or update; it implements only the session attach /
BOOT transition. Repository files remain unchanged.

Execution Guard requests are evaluated against the active process-local Anchor
snapshot. The Guard creates no Authority, Write Scope, Assignment, approval,
Host capability, or repository mutation. A receipt exists only when all
current bindings match. Receipts are process-local, one-time, request-bound,
and bind the Anchor snapshot, validation reference, exact payload hash, and
target preimage. The Host adapter timestamps issue and consume operations from
local physical time rather than trusting the request timestamp. Direct Host
write tools remain Host-dependent; the Runtime
must not claim hard enforcement without a receipt-aware pre-write hook.
The bundled file gateway is a hard-enforced path for exact `CREATE`, `MODIFY`,
and `DELETE` requests within the installed repository root. It verifies the
declared payload hash and target preimage before consuming the receipt. It does
not intercept unrelated Host tools, shell commands, APIs, or databases. The
same installed Host can expose a receipt-aware Git command path for exact,
shell-free `git add -- <relative-path...>`, `git commit -m <message>`, and
`git push origin HEAD:refs/heads/<current-branch>` requests. The command argv
is part of the receipt-bound request hash and its canonical SHA-256 payload;
its request uses `target_preimage: {status: NOT_APPLICABLE, sha256: NONE}`.
Force push, branch rewriting, arbitrary Git subcommands, shell composition,
and unrelated Host tools remain outside this gateway. Commit execution
disables repository hooks and commit signing.

The file-backed Git proposal journal can be used before a Session Runtime
endpoint exists. Local staging and commit are ordinary Git operations after
completed, validated work; the commit SHA is their evidence and no Runtime
proposal-database record is created. A `PUSH` proposal binds that immutable
commit SHA and the observed remote head. Its approval must come from a distinct
later user input. The journal stores only push proposal, approval, action, and
result linkage.

Git-backed HTTP calls use one bounded long-running budget for approved proposal
import and `add`, `commit`, or `push` execution. Local Git observations, remote
head observations, and mutation subprocesses retain separate finite limits, and
the HTTP budget is longer than the complete server-side mutation path.

Task Frame proposal requests contain one exact `execution_plan`. A successful
proposal remains passive and reports `task_frame_started: false`. Installed v1
Task Frame run requests contain a `frame` object with the matching proposal and
approval plus an ordered `operations` list. Passing `--database` creates or
reopens one file-backed Task Frame journal; `continue` opens only that recorded
dispatch and never manufactures fresh Parent input or Anchor state.
Anchor-memory batch requests contain only an ordered `operations` list. The CLI
dispatches an explicit allow-list of methods already implemented by the
promoted modules and rejects unknown fields or operations rather than inferring
them.

Continuity commands load a profile and Core contract files whose hashes must
match the installed distribution manifest. `checkpoint prepare` and
`memory-sync prepare` return passive candidates only. Resume and conversation
recall return unadopted candidates only. Anchor currentness uses
`session_id + frame_id`; a source-supplied `stale_after` may require recheck but
elapsed time alone never creates `STALE`. When a Session Boot Host is available,
`anchor-currentness observe` advances the same Current Anchor's `observed_at`
using Host physical time and changes no semantic state. Durable storage remains
Host-dependent and must be reported only from provider evidence.

## Modules

```text
task_frame_runtime.py
  -> Task Frame ledger with optional file-backed journal storage

task_frame_adapter.py
  -> transparent caller-selected Python callable transport

mode_registry_runtime.py
  -> strict source-backed Mode resolution and passive MASTER mutation planning

anchor_session_memory_runtime.py
  -> one opaque in-memory snapshot/event cache

anchor_session_memory_adapter.py
  -> explicit session map and loopback-only Host transport

session_surface_runtime.py
  -> deterministic transport and Commander-surface transition evaluator

session_boot_runtime.py
  -> deterministic Boot Evidence Bundle, heritage-input boundary,
     Current Interpretation Basis, fresh Anchor assembly, image-currentness check

session_boot_adapter.py
  -> installed-status transport and process-local boot image activation

execution_guard_runtime.py
  -> deterministic evidence intersection and process-local receipt ledger

execution_binding_runtime.py
  -> exact assignment proposal and process-local approval binding

execution_guard_adapter.py
  -> transparent loopback Guard transport

file_mutation_gateway.py
  -> receipt-aware bounded file mutation Host path

git_command_gateway.py
  -> receipt-aware bounded Git add/commit/push Host path

git_proposal_runtime.py
  -> file-backed push proposal/approval journal

receipt_verifying_write_gateway.py
  -> aggregate receipt-aware file and Git mutation paths

continuity_runtime.py
  -> deterministic continuity preparation, persistence, discovery, and currentness checks

continuity_store_runtime.py
  -> project-local append-only SQLite Checkpoint and Resume records

profiles/continuity-command-v1.json
  -> installed-distribution-bound continuity command policy

profiles/task-frame-debate-v1.json
  -> installed-distribution-bound default debate policy

capabilities.json
  -> static capability classification

cli.py
  -> JSON invocation surface
```

## Continuity Persistence

Checkpoint and Resume records use one fixed Runtime-owned database:

```text
.ai/runtime/continuity/continuity.sqlite
```

The database is created only by `checkpoint save` or `resume-save save`.
Preparation, listing, discovery, and loading do not create storage. A save is
reported as `SAVED` only after its SQLite transaction commits and returns
`LOCAL_SQLITE_COMMITTED` evidence.

```text
checkpoint prepare -> checkpoint save -> checkpoint list/load
resume-save prepare -> resume-save save
resume-restore discover -> Commander selection -> resume-restore load
```

The save commands accept only the unchanged candidate ID and candidate payload
returned by their corresponding prepare commands. Replaying the same immutable
record is idempotent; reusing its ID with different content is rejected.

All loaded records remain passive. Continuity persistence does not create or
activate Role, Authority, Execution Assignment, Mode Current Anchor, or
executable Runtime currentness. `repository_write` remains false because this
is Runtime operational state; `runtime_state_write` is true only for a committed
save transaction.

These local continuity operations are `HOST_DEPENDENT`. A source-only mobile or
web Connector cannot execute them unless a connected Execution Host exposes the
project filesystem. With only an approved Provider writer, the Connector stops
at `HANDOFF_APPEND`; it does not open or claim the local SQLite store.
