---
name: boot
description: Route a boot request through installed project runtime entry and source-backed state surfaces.
---

# Boot Invocation

Invocation class: `GOVERNANCE_ROUTER`

Capability classification: `governance_boot = SOURCE_BACKED`;
`session_boot_executor invocation = HOST_DEPENDENT`

This Skill routes user BOOT intent into PREPARING_SESSION from source-backed evidence. It does not
define boot policy, assign authority, restore a previous session, approve
execution, or write repository state. Natural-language Mode intent routes
internally through `MODE_CHANGE`; unresolved BOOT intent proposes Mode selection when it
is unresolved.

## Targets

Use the installed project surfaces in this order:

```text
.ai/runtime/project_instance/boot_command_entry.md
  -> its declared source-backed read order
  -> .ai/runtime/project_instance/mode_registry.json
  -> .ai/runtime/state/session.md when present
  -> .ai/runtime/state/current_anchor_frame.md when present
  -> .ai/runtime/project_instance/status.md
  -> .ai/runtime/project_instance/validation/latest.md
```

Route command interpretation to `.ai/core/RUNTIME_COMMANDS.md` and lifecycle
handling to the Core surfaces referenced by the installed boot entry.

## PREPARING_SESSION

```text
BOOT intent
  -> read source-backed governance and available project/session evidence
  -> unresolved Mode: MODE_SELECTION_REQUIRED
  -> resolved natural-language Mode intent: internal MODE_CHANGE
  -> reject Mode not present in the central Registry
  -> loaded source-backed policy/profile: mode_context_active may be reported
```

An unregistered Mode returns `MODE_NOT_REGISTERED` and ends that Mode request.
Do not continue into session preparation, Current Anchor access, or executable
Runtime decisions. Host evidence is not a retry condition for an unregistered
Mode.

`Mode: UNKNOWN` during generic BOOT is not an unregistered Mode request. Return
`MODE_SELECTION_REQUIRED` before Registry resolution. Once the user selects a
Mode, missing source-backed Registry evidence returns
`MODE_REGISTRY_UNAVAILABLE`; never accept caller-supplied Role/Scope/Profile as
a replacement.

PREPARING_SESSION must not require durable Project Runtime installation and must not
start Python merely because a Host is local or source is attached. Source-only
Hosts leave executable Runtime fields, endpoint, and Runtime Currentness
`UNKNOWN` unless they separately return raw Host Runtime evidence.

`SOURCE_READY` means the source is readable. It does not mean the Preparing
Session image has been loaded. `SESSION_PREPARED` additionally requires
Registry-backed Mode/Role/Scope/Profile resolution and loading that context.
A Host session reference is optional observation provenance, not a preparation
precondition. When Mode context is active and a provider session coordinate is
known, Hosts should best-effort run
`tools/universe_session_inject_hook.py` (see mode-change and session-ref-inject
Skills). Universe offline must not block BOOT.

PREPARING_SESSION and REBOOT rehydrate governance context from readable Anchor
Snapshot evidence. In every source-only Mode, the snapshot is an
`OBSERVED_REFERENCE` input. Verified rehydration reports
`session_preparation_state: REHYDRATED`. The Mode Current Anchor is not
created, advanced, or proven by generic preparation without a selected Mode.
When a Mode is selected and a project Anchor store is bound, MODE_CHANGE creates
or observes that Mode's Current Anchor. `Parent` is a Task Frame role, not an
Anchor owner. Conversation Resume or Archive material supports recall only.
Neither it nor a snapshot establishes executable Runtime session/frame,
`executable_runtime_currentness`, processed inbox state, authority, or execution
assignment without its own evidence.

## Local Runtime Start

`EXECUTABLE_RUNTIME_START` is a separate Host/profile decision. Mode profile
sets the Mode-entry default: `GOVERNANCE_ONLY` means that selecting the Mode
alone does not start an executor. It does not veto a later explicitly approved
task. When the current Task requirement and Evidence profile both require
executable proof, an available Host routes to executor start.

An explicit implementation request carries `execution_intent: IMPLEMENTATION`.
When the Task and Evidence profiles are not yet bound, an available Host must
return `EXECUTABLE_RUNTIME_START_PROPOSAL_REQUIRED` and keep the executor
stopped. The Host presents the bounded start proposal for approval. After that
approval is bound, it re-evaluates with both
`task_requirement: EXECUTABLE_PROOF_REQUIRED` and
`evidence_profile: EXECUTABLE_PROOF_REQUIRED`; only then does the result use
`next_operation: EXECUTABLE_RUNTIME_START`. This transition does not grant
authority or write permission.

The Host first evaluates this with the deterministic Runtime surface:

```text
python .ai/runtime/reference_runtime/cli.py prepare-session --request <request.json>
  --repo-root <project-root>
```

A source-only Host that materializes only governance source may use
`--registry-root <source-root>` without binding a project Anchor store.

When no executable filesystem is available, the Host must still read the
source-backed Registry and apply the same Mode/Role/Scope/Profile match before
reporting an active Mode context.

Only `executable_runtime.next_operation: EXECUTABLE_RUNTIME_START` routes to the
existing `session-boot serve` launcher. Every other result leaves executable
Runtime fields `UNKNOWN` and does not start Python.

An approved durable `PUSH` proposal supplies the bounded current Task and
evidence basis for this decision. Evaluate it with
`task_requirement: EXECUTABLE_PROOF_REQUIRED` and
`evidence_profile: EXECUTABLE_PROOF_REQUIRED`; keep the Registry-resolved Mode
profile unchanged.

For that executable local/sandbox path, start the executor as a Host-managed
long-running process:

```text
python .ai/runtime/reference_runtime/session_boot_launcher.py
  --repo-root <project-root>
  --session-id <node-mode-date-runtime-writer-sequence>
  --frame-id current
  --anchor-id <fresh-current-anchor-id>
  --host-action <SESSION_ATTACH|LOCAL_INSTALL_OR_ATTACH>
  --session-location <runtime-writer-surface>
  --commander-surface <user-interaction-surface-or-UNKNOWN>
  --execution-surface <runtime-writer-surface>
  --repository-location <repo-host-or-UNKNOWN>
  --port 0
```

The launcher passes every coordinate to the long-running executor as a Python
argv list. The Host must not rebuild this command as one PowerShell or shell
string. Coordinate values such as `codex desktop` therefore remain one value;
Hosts should still prefer stable opaque surface identifiers such as
`codex-desktop`, `local-codex`, and `local-pc`.

## Process Lifecycle

```text
launcher lifecycle: ONE_SHOT
Session Boot executor lifecycle: SESSION_LONG_RUNNING
launcher exit != Session Boot executor termination
```

The launcher starts the executor, returns the executor's first raw Boot JSON,
and then exits. The executor is the process that owns the loopback endpoint and
SQLite `:memory:` Runtime Image. A completed launcher process is therefore not
evidence that the Session Boot executor stopped. In an installed project, the
executor also opens the mode-scoped Current/Beyond Anchor SQLite store under
`.ai/runtime/anchor_store/`; that durable coordinate store is separate from the
process-local Runtime Image.

The Host owns background-process launch and shutdown. Keep the process alive
for as long as the session uses the returned SQLite `:memory:` Runtime Image.
The executor reads the installed distribution manifest and the most recent
local `validation/latest.md` evidence, validates their binding, creates the
first process-local Runtime Anchor Frame, and returns the raw result. It does
not fetch, synchronize with, or run full installed-Runtime validation against
the ai-career source during Local Runtime Start. Use `OS_VALIDATE` or explicit `STATUS` for
the full managed-path validation path.

Stopping the executor discards its Runtime Image, endpoint, guard ledger, and
receipt state. It does not delete the project-local Current/Beyond Anchor store.
Reading that store later is coordinate recovery input only; it never proves a
new executor ready or restores execution authority.

A fresh installed state with `session_initialization: UNINITIALIZED` is valid
Local Runtime Start input. It must not be rejected with
`ANCHOR_FRAME_REQUIRED`; Local Runtime Start creates the first current frame. A
prior initialized session is not restored automatically. Resume remains a
separate command.

## Result Transport

Return executor stdout and exit status without semantic remapping. Report
Session Boot READY only when the raw result contains both:

```text
status: SESSION_BOOT_IMAGE_CREATED
session_runtime: READY
```

After receiving that result, the Host must query the returned endpoint with the
returned token and exact `session_id`. Report
`SESSION_BOOT_PROCESS_ACTIVE` only when the endpoint returns
`HOST_SESSION_MEMORY_ACTIVE`. When the Host exposes process inspection, also
record the listening PID as supporting evidence. If this post-launch probe is
not performed, executor liveness remains `UNKNOWN`; do not describe the
executor as stopped merely because the one-shot launcher exited.

`repository_write: false` and `boot_repository_write: false` describe the BOOT
command's own side effects. They do not claim that the Host filesystem is
technically read-only. After Boot, every requested mutation must route through
`.ai/skills/common/execution-guard/SKILL.md` using the returned endpoint and
token. `mutation_enforcement: HOST_DEPENDENT` must not be inflated to hard
enforcement without a receipt-aware Host hook.

Missing, unreadable, stale, conflicting, or unavailable execution evidence
remains `UNKNOWN`. If the Host cannot keep the process alive, report
`SESSION_BOOT_EXECUTOR_UNAVAILABLE`; do not simulate readiness. Boot evidence
and a fresh currentness coordinate do not become execution authority.

Provider-backed append-only handoff is separate from BOOT. A capable Host may
append an approved Memory Inbox, Queue, or Archive artifact through
`HANDOFF_APPEND`; it must record provider evidence and must not be represented
as a `SOURCE_MUTATION` or executable Runtime start.

`heartbeat_wait_buffer` is `CONTRACT_ONLY`; this Skill does not implement a
background wait buffer or delayed execution mechanism.
