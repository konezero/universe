# Runtime Commands

Status: Candidate Core Architecture  
Scope: ai-career  
Layer: Runtime Command Entry / Mission Routing  
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`  
Created: 2026-07-01  
Updated: 2026-07-03

## Purpose

Runtime Commands define the command entry points that route a user or attached project into the correct runtime path.

This document closes the gap between a natural trigger and the correct instruction path.

Without a command entry, a runtime can read the Core Stack Map and still choose a Conductor Resume or Inbox route when the work is actually an OS update.

## Core Declaration

```text
COMMANDS ROUTE BEFORE RESUME.

OS_INSTALL INSTALLS THE DURABLE PROJECT RUNTIME.
OS_INSTALL REQUIRES AN EXPLICIT TARGET, PROPOSAL, APPROVAL, AND WRITE SCOPE.
PROJECT_RUNTIME_INSTALL IS AN INTERNAL INSTALLER OPERATION, NOT A USER COMMAND.

BOOT / REBOOT ROUTE THROUGH SESSION_RUNTIME_GOVERNANCE.
SESSION_ATTACH CONNECTS A SESSION TO AVAILABLE SOURCE OR A RUNNING RUNTIME.
SESSION_ATTACH DOES NOT INSTALL PROJECT FILES.
GENERIC BOOT / REBOOT REHYDRATE GOVERNANCE CONTEXT FROM ANCHOR SNAPSHOT EVIDENCE.

OS_UPDATE ROUTES TO RUNTIME_INSTRUCTION_SET FIRST.
OS_UPDATE UPDATES THE DURABLE PROJECT RUNTIME.

CONDUCTOR RESUME IS NOT THE DEFAULT PATH FOR OS_UPDATE.

DO NOT FORCE INFERENCE WHEN COMMAND PURPOSE IS INCOMPLETE.

RECOGNIZED COMMAND WORDS DO NOT BYPASS PROPOSAL GATES WHEN SCOPE IS INCOMPLETE.

COMPLETE COMMANDS STILL REQUIRE LIFECYCLE AUTHORITY AND PRE-EXECUTION VERIFICATION.
```

Canonical boundary:

```text
RUNTIME_COMMANDS.md
  -> user-facing lifecycle command meanings

PROJECT_RUNTIME_INSTALL.md
  -> internal durable installation operation
```

## User-Facing Update Vocabulary

```text
OS_UPDATE
  -> sole user-facing command for installed Project Runtime reconciliation
  -> may update propagated Core contracts, Runtime implementation, Skills,
     Templates, and project-instance surfaces in one managed package

RUNTIME_UPDATE
  -> internal Host lifecycle operation selected by OS_UPDATE
  -> permitted in approval payloads, adapter input, and raw evidence
  -> not a second command for the user to invoke
```

Core and Runtime remain distinct responsibility boundaries, but they are not
separate user-facing update branches. Do not invent or present separate Core
and Runtime update choices. Proposal titles, approval prompts, progress
summaries, completion messages, and next-command instructions must use
`OS_UPDATE`.

When exact approval evidence must expose the implementation operation, present
it only as technical detail in the user-facing summary:

```yaml
user_command: OS_UPDATE
internal_operation: RUNTIME_UPDATE
```

The raw adapter request and approval object continue to use
`operation: RUNTIME_UPDATE`.

## Command vs Instruction

```text
Runtime Command
  -> entry point / mission route
  -> decides which runtime path to load first

Runtime Instruction
  -> project attachment contract
  -> defines what a project can assemble or update after the route is selected
```

Example:

```text
Command: OS_INSTALL
  -> resolve immutable source and explicit durable target
  -> scan and classify the target without mutation
  -> present the exact install proposal
  -> wait for approval and write scope
  -> invoke the internal Project Runtime installer
  -> validate the installed `.ai` surface
  -> return READY_FOR_BOOT without claiming Session Runtime readiness

Command: OS_UPDATE
  -> route to Runtime Instruction Set
  -> update project-local or repository runtime surfaces only with write scope
```

## Primary Runtime Commands

```text
OS_INSTALL
OS_UPDATE
OS_STATUS
OS_PREFLIGHT
OS_VALIDATE
OS_ROLLBACK
OS_SYNC
BOOT
REBOOT
RESUME
CHECKPOINT
SNAPSHOT_SAVE
RESUME_SAVE
ARCHIVE_SAVE
MEMORY_SYNC
CARRIER
DISPATCH
AUDIT
STATUS
```

Korean aliases:

```text
스냅샷 저장 -> SNAPSHOT_SAVE / CHECKPOINT
리쥼 저장   -> RESUME_SAVE
아카이브 저장 -> ARCHIVE_SAVE
```

## OS_INSTALL Meaning

Primary durable-runtime meaning:

```text
OS_INSTALL
  -> install the complete Project Runtime into one explicit durable target
  -> bind one immutable ai-career source
  -> classify FRESH / ALREADY_INSTALLED / UPDATE_REQUIRED / MIGRATION_REVIEW / UNKNOWN
  -> require an exact proposal and approval before mutation
  -> use the Host-owned Fresh Install adapter only for FRESH
  -> validate installed Core, Runtime, Skill, Template, and Project Instance surfaces
  -> report REPOSITORY_RUNTIME_VERIFIED / PARTIAL / UNKNOWN from raw evidence
  -> return READY_FOR_BOOT when installation succeeds
```

Internal install operation:

```text
PROJECT_RUNTIME_INSTALL
  -> compatibility name for the deterministic internal install contract
  -> invoked by OS_INSTALL after proposal and approval
  -> not exposed as a second user-facing lifecycle command
```

Install and session boundary:

```text
OS_INSTALL success
  -> repository_runtime: VERIFIED
  -> boot_handoff: READY_FOR_BOOT
  -> session_runtime: UNKNOWN
  -> currentness: UNKNOWN

PREPARING_SESSION
  -> read source-backed governance and available project/session evidence
  -> propose Mode selection when Mode is unresolved
  -> does not require a local executor, Current Anchor Runtime state, or SQLite image

Selected Mode
  -> natural-language Mode intent routes internally through MODE_CHANGE
  -> apply Role / Scope and governance alignment
  -> may report mode_context_active from source-backed policy/profile evidence
```

Mode intent boundary:

```text
Role/mode shorthand
  -> natural-language Mode intent
  -> internal MODE_CHANGE and PREPARING_SESSION alignment
  -> do not claim executable Runtime state without raw Host evidence
  -> do not require durable Project Runtime installation for source-backed Mode context
```

Examples:

```text
컨덕터모드
Conductor mode
마스터모드
MASTER mode
```

Expected route:

```text
role/mode intent detected
  -> internal MODE_CHANGE
  -> source-backed Role / Scope resolution
  -> mode_context_active only when the selected policy/profile is loaded
  -> Local Runtime start remains a separate Host/profile decision
```

Do not skip directly from mode shorthand to tool execution. A bare
`<MODE>_ACTIVE` status is ambiguous; report Mode context and executable Runtime
state separately.

Tutorial interaction-profile exception:

```text
튜토리얼 / Tutorial
  -> route to TUTORIAL_GUIDE_MODE.md
  -> read-only tutorial entry
  -> no OS_INSTALL or BOOT
  -> no runtime mutation
  -> no repository write
  -> no external tool execution
```

The Tutorial profile is available without session runtime assembly because it
is a read-only explanation path, not a registered Mode or runtime authority
path.

Source-only connector meaning:

```text
SOURCE_ATTACH + PREPARING_SESSION + GitHub repository attachment
  -> fetch source before trusting registry or search
  -> use repository content as source-backed Boot evidence
  -> report SOURCE_READY / PROPOSAL_READY / PARTIAL / UNKNOWN
  -> may report source-backed mode_context_active after Mode resolution
  -> do not claim endpoint, executable Runtime, or Runtime Currentness
  -> readable Anchor Snapshot is governance rehydration input
  -> session_preparation_state is UNKNOWN until Mode selection
  -> session_preparation_state is PREPARED for a new Mode Current Anchor
  -> session_preparation_state is REHYDRATED for an existing Mode Current Anchor
  -> live Parent Anchor currentness is not evaluated by BOOT
  -> executable_runtime_currentness remains UNKNOWN without raw execution evidence
  -> conversation Resume / Archive remains recall material, not Runtime restore
  -> do not claim executable session/frame, active archive, or processed inbox
     state without its own evidence
  -> do not claim OS_INSTALL without a durable target and approved write path
```

For `OS_STATUS`, source observation alone must use the source-only baseline:

```text
immutable source observed
  -> SOURCE_READY
repository checkpoint / Resume / validation / Runtime Image documents
  -> OBSERVED_REFERENCE only
Resume restore
  -> NOT_PERFORMED
current validation
  -> NOT_RUN
Mode Current Anchor / executable Runtime currentness
  -> UNKNOWN
Authority / Execution Assignment
  -> UNASSIGNED
```

Do not reuse a checkpoint label, Resume Archive summary, old validation
statement, or Runtime Image description as the current `OS_STATUS` result.

`SOURCE_ATTACH` describes the available source provider. It is not a complete
Host topology classification. Interaction carrier, execution Host, and write
target belong to a bounded Task Frame dispatch record or an Execution Binding,
not to PREPARING_SESSION. Their presence establishes capability only; source mutation
still requires its exact approval, scope, Guard receipt, and validation route.

These source-only rules apply to every resolved Mode. `CONDUCTOR`, `MASTER`,
and other Mode contexts may differ in policy scope, but none obtains an active
executable Runtime from repository reading alone.

Repository/project durable install meaning:

```text
OS_INSTALL or OS_UPDATE + explicit write scope
  -> confirm repository target
  -> fetch current source
  -> scan repository or project-local structure
  -> prepare proposal
  -> wait for approval
  -> assemble or reconcile local boot/preflight/governance/runtime/service/persistence surfaces
  -> validate local installation
  -> report REPOSITORY_RUNTIME_VERIFIED / PARTIAL / UNKNOWN from validation evidence
```

Do not collapse `SESSION_RUNTIME_READY` into `REPOSITORY_RUNTIME_VERIFIED`.

Do not collapse repository attachment into repository write authority.

## OS_UPDATE Meaning

```text
OS_UPDATE
  -> update an attached project or project-local runtime instance
  -> use common ai-career instruction contract
  -> keep project-local assembly local
  -> validate local assembly
  -> record project-local evidence/checkpoint when needed
```

OS_UPDATE is the normal command for repository runtime reconciliation when the user grants write scope.

## Save Command Meanings

### SNAPSHOT_SAVE / CHECKPOINT

```text
SNAPSHOT_SAVE
  -> prepare a bounded current-state snapshot candidate
  -> save the exact candidate to the project-local continuity SQLite store
  -> return SAVED only after LOCAL_SQLITE_COMMITTED evidence
  -> keep the saved record passive
```

`CHECKPOINT` remains accepted as the general Snapshot First command. It does not
rewrite source files, activate an Anchor, or grant authority. Listing and
loading Checkpoint records are read-only operations.

### RESUME_SAVE

```text
RESUME_SAVE
  -> prepare a bounded Resume candidate for long continuity
  -> save the exact candidate to the project-local continuity SQLite store
  -> preserve enough selected context to propose intentional continuation
  -> keep saved and loaded records passive until a later adoption decision
```

Korean alias:

```text
리쥼 저장
```

### ARCHIVE_SAVE

```text
ARCHIVE_SAVE
  -> preserve historical context, decision rationale, long logs, or previous observations
  -> support later recall
  -> do not make archived state active automatically
```

Korean alias:

```text
아카이브 저장
```

## Command Routing Table

| Command | First Route | Purpose |
| --- | --- | --- |
| `OS_INSTALL` | `PROJECT_RUNTIME_INSTALL.md` through the instruction and Host Fresh Install flow | Install the complete durable project-local `.ai` Runtime after scan, proposal, and approval |
| `OS_UPDATE` | `RUNTIME_INSTRUCTION_SET.md` | Update local/repository runtime surface from common instruction contract after write scope is confirmed |
| `PROJECT_RUNTIME_INSTALL` | internal compatibility operation | Deterministic implementation used by `OS_INSTALL`; not a user-facing command |
| `OS_PREFLIGHT` | `RUNTIME_PREFLIGHT.md` | Check readiness for an instruction or task |
| `OS_STATUS` | `RUNTIME_INSTRUCTION_SET.md` + `RUNTIME_STATUS_SOURCE_RULE.md` | Report source-backed session and/or project-local runtime state |
| `OS_VALIDATE` | `RUNTIME_INSTRUCTION_SET.md` + validation evidence | Validate session or repository runtime compatibility |
| `OS_ROLLBACK` | `RUNTIME_INSTRUCTION_SET.md` | Return local runtime to a known project checkpoint |
| `OS_SYNC` | `RUNTIME_INSTRUCTION_SET.md` + candidate path | Route reusable observations back toward ai-career |
| `SNAPSHOT_SAVE` / `CHECKPOINT` | `PERSISTENCE_MODEL.md` + `.ai/runtime/continuity/continuity.sqlite` | Prepare and durably save a passive current-state Checkpoint |
| `RESUME_SAVE` | `PERSISTENCE_MODEL.md` + `.ai/runtime/continuity/continuity.sqlite` | Prepare and durably save a passive Resume candidate |
| `ARCHIVE_SAVE` | `PERSISTENCE_MODEL.md` + archive path | Preserve historical recall material |
| `MEMORY_SYNC` | Memory / Storage / L3 path | Extract or persist selected memory artifact |
| `CARRIER` | Carrier profile / queue path | Collect candidate events |
| `DISPATCH` | Dispatch service path | Deliver approved work |
| `AUDIT` | Audit / validation path | Check evidence and gaps |
| `TUTORIAL` | `TUTORIAL_GUIDE_MODE.md` | Activate a read-only interaction profile; it is not a registered ai-career Mode and performs no install, repository write, or feedback persistence |
| `RESUME` | Resume candidate path | Restore from durable state when selected |
| `BOOT` / `REBOOT` | `SESSION_RUNTIME_GOVERNANCE.md` + Session Framework path | Build, attach, or rebuild the disposable runtime session from an installed or source-backed Runtime |
| `STATUS` | Source-backed status path | Report verified or unknown state |

### Runtime-Owned Operational State

`SNAPSHOT_SAVE`, `CHECKPOINT`, `MEMORY_SYNC`, runtime-owned Inbox or Queue
state transitions, `RESUME_SAVE`, and selected `RESUME` candidate loading
maintain declared Runtime operational state. They use the
persistence/currentness route, not the project-mutation Execution Guard.
Checkpoint and Resume save report both `repository_write: false` and
`runtime_state_write: true` after the local SQLite commit.

Local Checkpoint and Resume operations are `HOST_DEPENDENT`. A source-only
mobile or web Connector must not report local prepare, save, list, or load as
executed without a bound Execution Host that can access the project filesystem.
With an approved Provider writer, the Connector may perform only the separate
`HANDOFF_APPEND` route described below.

`HANDOFF_APPEND` is the provider-backed operation class for selected,
append-only Runtime handoff artifacts such as Memory Inbox, Queue events, and
Archive evidence. It requires an approved provider write capability, an exact
Runtime-owned append path, explicit user selection or approval, and provider
write evidence. It does not require an executable Runtime merely to append.

`SOURCE_MUTATION` covers source, Core, templates, configuration,
project-owned artifacts, and external systems. It never uses `HANDOFF_APPEND`.
It requires an execution evidence Host. A Host that cannot provide one returns
`BLOCKED_EXECUTION_HOST_REQUIRED`; this does not mean that governance context
or provider-backed handoff append is unavailable.

## Pre-Command Interpretation Gates

Before mission routing, apply the lightweight pre-command gates when the utterance contains known runtime tokens, is incomplete, call-like, or action-only.

```text
INTENT_FIRST_ROUTING_GATE.md
  -> classify utterance intent before command or mode routing
  -> token match is evidence, not routing authority
  -> mentioned command / mode / role / anchor token is not a command by itself
  -> questions, reviews, wording discussions, comparisons, and examples do not route from token match alone

RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md
  -> runtime routing and execution originate from active Runtime Anchor Frame
  -> conversation context is reference only, not routing authority
  -> missing / stale / mismatched frame returns ANCHOR_FRAME_REQUIRED

HEARTBEAT_WAITING_PURPOSE_GATE.md
  -> bare Commander Call / incomplete purpose
  -> heartbeat only
  -> no command route

NO_FORCED_INFERENCE_PROPOSAL_GATE.md
  -> partial actionable direction
  -> recognized command with incomplete scope / target / purpose
  -> infer only when grounded
  -> propose one strong candidate or ask the missing anchor truthfully
  -> wait for Commander confirmation before execution

ROLE_MODE_AUTHORITY_GATE.md
  -> role or mode label / transition request
  -> require source-backed role/mode authority
  -> report UNKNOWN when authority source is unavailable
  -> do not simulate unverified roles or modes
```

These gates do not weaken explicit complete commands. They prevent premature route selection when utterance intent, command purpose, target, scope, or role/mode authority is not complete.

## Target / Source Gate

Before executing `OS_INSTALL`, distinguish durable target from source and Host:

```text
Source
  -> repository or files used to reconstruct runtime rules and evidence

Target
  -> explicit durable project root where the complete `.ai` Runtime will be installed

Host
  -> execution surface that materializes source, runs the installer, and validates the target

Session Runtime
  -> created later by BOOT; never the OS_INSTALL target
```

Default interpretation:

```text
@GitHub ai-career OS_INSTALL
  -> source: konezero/ai-career
  -> target: UNKNOWN until an explicit durable target is confirmed
  -> result: INSTALL_PROPOSAL_REQUIRED
  -> repository_write: false until exact approval
```

Local workspace interpretation:

```text
Codex opened on one stable local workspace root + OS_INSTALL with no path
  -> target candidate: the absolute Host workspace root
  -> candidate source: HOST_CWD_EVIDENCE
  -> display the candidate in the installation proposal
  -> do not ask the user to repeat the already observed path
  -> target remains unconfirmed until exact proposal approval
  -> no repository write before approval
```

An automatically displayed CWD candidate is not an implicit target and is not
installation permission. It becomes the installer's explicit `--target` only
after the user approves the proposal containing that exact absolute path.

After the exact proposal is shown, the user may approve the bounded write with:

```text
@GitHub ai-career OS_INSTALL approval
@GitHub ai-career OS_UPDATE
```

## Mission Resolution

When a trigger contains a recognized Runtime Command and command purpose, target, and scope are complete, route by command first.

```text
@GitHub ai-career OS_UPDATE
  -> Command detected: OS_UPDATE
  -> First route: RUNTIME_INSTRUCTION_SET.md
```

When a trigger contains only a recognized command word with incomplete scope, do not route yet.

```text
리부트
  -> recognized command: REBOOT
  -> scope incomplete
  -> No Forced Inference Proposal Gate
  -> ask: 어떤 리부트를 말씀하시는 건가요?
```

Scoped recognized commands may route when the scope is sufficient:

```text
@GitHub ai-career 리부트
  -> recognized command: REBOOT
  -> scope: ai-career session/runtime
  -> boundary / authority check
  -> existing Runtime Commands route

@GitHub gcs 리부트
  -> recognized command: REBOOT
  -> scope: GCS project runtime
  -> boundary / authority check
  -> existing Runtime Commands route
```

When a trigger is natural language, resolve to a Runtime Command only when confidence is high and the candidate is grounded.

```text
@GitHub ai-career 업데이트
  -> Command candidate: OS_UPDATE

@GitHub ai-career 설치
  -> Command candidate: OS_INSTALL

@GitHub ai-career 상태
  -> Command candidate: OS_STATUS / STATUS

스냅샷 저장
  -> Command candidate: SNAPSHOT_SAVE / CHECKPOINT

리쥼 저장
  -> Command candidate: RESUME_SAVE

아카이브 저장
  -> Command candidate: ARCHIVE_SAVE
```

If command confidence is low, do not force an update path.

```text
No grounded candidate
  -> ask the missing anchor truthfully

One strong grounded candidate
  -> propose that single candidate and wait for Commander confirmation

Recognized command + incomplete scope
  -> ask missing scope / target / purpose or propose one grounded candidate

Role / mode label or transition request
  -> Role / Mode Authority Gate
  -> source-backed authority required before role/mode claim

Explicit wait / still speaking / multi-part instruction
  -> Commander Wait Buffer Rule
  -> WAITING_COMMANDER
  -> no command route until explicit release intent

No purpose / Commander still speaking
  -> heartbeat / waiting purpose
```

## Runtime Lifecycle Relationship

`RUNTIME_LIFECYCLE.md` defines the full order from Session Start to Certificate Destroy.

Runtime Commands own command routing only.

They do not grant execution authority by themselves.

```text
Recognized complete command
  -> Runtime Commands route
  -> Runtime Lifecycle authority reconstruction
  -> Boundary Check
  -> Pre-Execution Verification
  -> Execution only if still permitted
```

## Conductor Route Boundary

Conductor Resume and Inbox are used when the mission is:

```text
Conductor restore
Candidate review
Inbox processing
Governance adoption
Checkpoint lineage review
```

Runtime Instruction commands are used when the mission is:

```text
OS_INSTALL approved project-local Runtime installation
OS_UPDATE repository/runtime reconciliation
OS_VALIDATE session or repository validation
BOOT / REBOOT session runtime creation or reconstruction
```

## Adoption Status

This is a candidate runtime command entry document.

It should be tested against:

```text
@GitHub ai-career OS_INSTALL
@GitHub ai-career OS_UPDATE
@GitHub ai-career OS_VALIDATE
@GitHub ai-career Conductor mode
```

Expected behavior:

```text
OS_INSTALL does not mutate before an exact proposal, approval, and write scope.
OS_INSTALL success does not become Session Runtime readiness.
BOOT does not become repository write authority.
Project role restore does not become platform authority.
Repository source does not become repository target without explicit write delegation.
```
