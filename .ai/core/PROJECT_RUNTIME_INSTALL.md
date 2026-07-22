# OS Install / Project Runtime Installation

Status: Candidate Core Architecture
Scope: ai-career / attached project runtime
Layer: Project Runtime Assembly
Parent: `.ai/core/RUNTIME_INSTRUCTION_SET.md`
Created: 2026-07-02
Updated: 2026-07-02

## Purpose

This document defines the durable Project Runtime installation performed by the
user-facing `OS_INSTALL` command.

It replaces a fixed role-tree model with a scan, proposal, approval, install, and validation model.

The filename remains `PROJECT_RUNTIME_INSTALL.md` for source and distribution
compatibility. `PROJECT_RUNTIME_INSTALL` is an internal operation name, not a
second user-facing lifecycle command.

## Core Declaration

```text
OS_INSTALL INSTALLS THE COMPLETE DURABLE PROJECT RUNTIME.
PROJECT_RUNTIME_INSTALL IS THE INTERNAL INSTALLER OPERATION.

OS_INSTALL REQUIRES ONE IMMUTABLE SOURCE AND ONE EXPLICIT DURABLE TARGET.
OS_INSTALL MUST PRODUCE A PROPOSAL BEFORE WRITING.
OS_INSTALL SUCCESS ENDS AT READY_FOR_BOOT.

BOOT / REBOOT OWN SESSION RUNTIME CREATION.
SESSION_ATTACH OWNS CONNECTION TO SOURCE OR A RUNNING RUNTIME.
SESSION_ATTACH MUST NOT WRITE PROJECT FILES.

PROJECT RUNTIME LIVES IN THE PROJECT ONLY AFTER APPROVAL.
CAREER INDEXES PROJECT RUNTIMES.

NODE IS PROJECT CONTEXT.
MODE IS BEHAVIOR CONTRACT.
MODE OWNS RULES.
MODE RESOLVES ROLE AND SCOPE.
SCOPE IS NOT NODE.

FETCH CURRENT REPOSITORY SOURCES BEFORE PROPOSAL OR PATCH.
OS_INSTALL MUST NOT CLAIM SESSION_RUNTIME READY.
```

## Model

```text
AI Core OS
  -> OS_INSTALL
  -> Source / Target / Host Resolution
  -> Proposal / Approval
  -> Host Fresh Install Adapter
  -> internal Project Runtime installer
  -> OS_VALIDATE
  -> READY_FOR_BOOT
  -> Mode Selection
  -> BOOT
  -> Node
  -> Mode
  -> Rule Set
  -> Work
```

Meaning:

```text
OS_INSTALL
  -> user-facing durable Project Runtime installation command

Source / Target / Host Resolution
  -> selects immutable source transport, durable target, and capable executor

Session Runtime
  -> live runtime created later by BOOT

Career Runtime
  -> integrated runtime manager

Project Runtime installer
  -> internal deterministic operation invoked by OS_INSTALL

Node
  -> project slot / project context / runtime node

Mode
  -> user-facing behavior contract for that node

Role / Scope
  -> internal runtime fields resolved from Mode

Rule Set
  -> internal rules owned by Mode
```

## OS_INSTALL Entry

`OS_INSTALL` is the only user-facing command for first durable installation.

```text
OS_INSTALL
  -> resolve one immutable ai-career source
  -> confirm one absolute durable target
  -> scan and classify target without mutation
  -> propose exact managed-path writes and validation
  -> require exact user approval and write scope
  -> dispatch FRESH to the Host Fresh Install adapter
  -> invoke the internal Project Runtime installer
  -> update Career registry when available
  -> validate installed Runtime surface
  -> return READY_FOR_BOOT
```

`OS_INSTALL` must not write files on first contact. It first produces a
source-backed proposal.

## Host Resolution

Host Resolution determines whether the current Host can materialize the
immutable source and mutate the explicit durable target. It does not turn
`OS_INSTALL` into session attachment.

```text
local Git object database available
  -> source-root provider

GitHub connector + writable sandbox available
  -> provider-attested source bundle

authenticated GitHub CLI + writable sandbox available
  -> github-cli source bundle

source readable but no durable writable target or installer execution
  -> INSTALL_EXECUTOR_UNAVAILABLE
  -> no OS_INSTALL success claim
```

## Internal Project Runtime Install Operation

`PROJECT_RUNTIME_INSTALL` remains the compatibility name used by the
deterministic installer and historical source references.

```text
OS_INSTALL
  -> approved Host dispatch
  -> internal PROJECT_RUNTIME_INSTALL operation
  -> repository_runtime: VERIFIED | PARTIAL | UNKNOWN
```

It must not be presented as a second command that the user runs after
`OS_INSTALL`.

Search may discover candidate paths, but current install state must be confirmed by fetching repository files from the intended ref.

## Install Safety Gate

Before any project repository write, the runtime must have:

```text
Repository target confirmed
Current repository files fetched
Project scan completed
Runtime proposal reported
User approval received
```

If any of those are missing, report a non-complete state and do not write project runtime files.

## Install Source Provider Gate

After proposal approval and before target mutation, choose exactly one immutable
source transport:

```text
local Git CLI/object database available
  -> distribution installer --source-root

GitHub connector + writable execution sandbox available
  -> Host materializes the declared project Runtime source index at one commit
  -> distribution installer --source-bundle

authenticated GitHub CLI + writable execution sandbox available
  -> Host uses gh api to materialize the declared source index at one commit
  -> source provider remains github-cli
  -> distribution installer --source-bundle

source can be read but no writable execution target exists
  -> SOURCE_ATTACH only

neither complete transport is available
  -> SOURCE_PROVIDER_UNSUPPORTED
```

The remote source bundle path does not change the proposal, approval, migration,
ownership, validation, Authority, or Execution Assignment gates. It changes
only how immutable source bytes and evidence reach the deterministic installer.

The remote-provider Host must not manually write package files into the target. It
must transport the complete indexed bundle and let the installer verify it
before the first target write.

Recommended states:

```text
SESSION_READY
  -> live session runtime is active

SANDBOX_READY
  -> session runtime is active in a sandbox host

LOCAL_READY
  -> session runtime is active in a local workspace host

SOURCE_READY
  -> source-backed runtime context exists without writable execution target

PERSISTENCE_REQUIRED
  -> durable repository target is missing or unconfirmed

PROPOSAL_READY
  -> scan and install proposal exist

APPROVAL_REQUIRED
  -> proposal exists but write approval is missing

INSTALL_COMPLETE
  -> repository-backed runtime surface was written and validated

PENDING_SYNC
  -> session memory candidates exist but are not durably written
```

## Legacy Structure-Only Migration Gate

A project-local `.ai` footprint created before the standalone distribution
manifest existed is unmanaged. Normal `install` must reject its collisions and
must not infer installer ownership from familiar filenames or `READY` text.

Legacy adoption requires all of:

```text
explicit migrate command
source-backed migration profile selected by ID
declared legacy source commit matched by the profile fingerprint
no existing managed DISTRIBUTION_MANIFEST.json
complete profile path set present
no additional managed-target collisions
all declared legacy markers matched
pre-write SHA-256 inventory built
legacy bytes archived before replacement
```

`migrate` and `--force` are not equivalent. `--force` is not a migration proof
and must not produce legacy adoption evidence.

Legacy state claims are recovery input, not current Runtime evidence. The
installer must archive the old `session.md` and `current_anchor_frame.md` bytes,
then reinitialize their active paths under the current state schema. Therefore
the immediate post-migration state remains:

```text
repository_runtime: VERIFIED only after full current validation
session_runtime: UNKNOWN
session_initialization: UNINITIALIZED
currentness: UNKNOWN
authority: UNASSIGNED
execution_assignment: UNASSIGNED
```

The installed distribution manifest must retain the migration profile ID,
declared legacy source commit, replacement source commit, legacy inventory
digest, per-file legacy hashes, dispositions, and archive paths. BOOT or Resume
may later establish current session state from separate evidence; migration
itself does not activate it.

## Repository Target

Runtime can execute in a session host before a durable project repository target exists.

A phone, browser, chat app, or sandbox may host live Session Runtime.

```text
MOBILE / CHAT / SANDBOX CAN BE A SESSION INSTALL TARGET.
MOBILE / CHAT / SANDBOX IS NOT A DURABLE PROJECT RUNTIME TARGET.
```

Preferred wording:

```text
mobile/chat/browser/sandbox -> attach session runtime
local workspace -> install or attach local runtime
GitHub-only -> attach source-backed read-only context
repository durable runtime -> project runtime install
```

If no writable project repository target is confirmed:

```text
Do not create project runtime files.
Do not report project INSTALL_COMPLETE.
Run SESSION_READY only.
Produce PROPOSAL_READY if scan evidence exists.
Require repository target and approval before writing project-local .ai.
```

A sandbox may be used for temporary execution, testing, and OS session attach.

It should not be reported as a durable project runtime install unless the user explicitly requested a sandbox-only temporary project runtime.

## Career Registry

Career provides unified management by indexing distributed project runtimes.

```text
Career Registry
  -> project-a: /project-a/.ai
  -> project-b: /project-b/.ai
  -> project-c: github.com/user/project-c/.ai
```

The registry is updated after an OS_INSTALL proposal is approved and installed,
or when an existing Project Runtime is discovered and validated.

## User-Facing Runtime Coordinates

Expose only the coordinates the user needs:

```text
Current Host: <sandbox|local|source-only>
Command: OS_INSTALL
Source: <immutable-source-coordinate>
Durable Target: <absolute-project-root>
Current Node: <project>
Boot Handoff: <READY_FOR_BOOT|BLOCKED|UNKNOWN>
```

Keep rule, policy, capability, and validation details internal unless audit or explanation is requested.

## Project Scan

OS_INSTALL begins with a source-backed project scan.

Search can help discover likely paths.

Fetch confirms current files.

Scan inputs may include fetched current sources such as:

```text
README
repository layout
docs/
src/
tests/
.github/workflows/
risk or safety markers
existing .ai/ surfaces
```

The proposal should include reasons and source status.

Project scans and proposals must follow `.ai/core/NODE_MODE_COORDINATE_CONTRACT.md`.

Do not propose architecture, governance, implementation, review, or source-audit
as Nodes when they are actually Mode scopes or task scopes.

## Runtime Proposal

Before modifying a project, report a runtime proposal.

```text
Runtime Proposal
  -> repository target
  -> source status from fetch
  -> Node name
  -> recommended Modes
  -> resolved Role / Mode Scope mapping when known
  -> Mode Rule Sets
  -> evidence from scan
  -> expected project-local surfaces
  -> validation plan
  -> write actions that would be performed after approval
```

Project modification waits for user approval.

## Session Runtime vs Repository Runtime

```text
Session Runtime
  -> live runtime attached to sandbox, local workspace, or source-only session context

Repository Runtime
  -> durable, source-backed project-local .ai surface
```

Lifecycle language:

```text
OS_INSTALL
  -> durable project-local runtime install after proposal and approval
  -> assemble the mandatory initial MASTER primary-mode contract from
     `.ai/templates/project_primary_mode/README.md`

BOOT / REBOOT
  -> create or recreate the disposable Session Runtime

SESSION_ATTACH / SOURCE_ATTACH
  -> connect a session to a running Runtime or source-backed Boot input
  -> no durable install claim
```

## Standalone Distribution Requirement

The mandatory MASTER outputs are the project-instance bootstrap minimum. They
do not by themselves prove that the project contains a standalone Runtime.

When `OS_INSTALL` is expected to leave a project that can boot
without the ai-career working tree remaining attached, installation must use
the source-backed project-runtime distribution manifest.

```text
selected immutable ai-career commit
  -> registered Core runtime surfaces
  -> registered contract templates
  -> generic Reference Runtime executables
  -> generated project instance and uninitialized session-state baseline
  -> materialized installed manifest with per-file hashes
  -> OS_VALIDATE
```

The canonical distribution source is:

```text
.ai/distribution/context_management_runtime_pack/project_runtime_distribution_manifest.json
```

The materialized project-local evidence is:

```text
.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json
```

The installer must copy source-backed bytes from one immutable Git commit. It
must not combine committed source with uncommitted working-tree content while
reporting a verified installation.

The project-local distribution must keep these classes separate:

```text
vendored Core / Template / Runtime
  -> immutable, commit-bound installed contract pack

generated project instance / state
  -> project-owned localization and boot coordinates
  -> session/currentness remain UNKNOWN until a later BOOT or attach observes them

source-only research / archive / proof fixtures
  -> excluded unless explicitly selected by another contract
```

`OS_INSTALL: COMPLETE` for a standalone install requires all of:

```text
project-instance bootstrap minimum present
registered Core pack present and hash-matched
registered Template pack present and hash-matched
required Runtime executables present and hash-matched
installed manifest present and source-commit-bound
no required dangling local contract references
OS_VALIDATE PASS recorded
```

If only the project-instance bootstrap minimum exists, report the structure
check separately and keep repository-runtime readiness `PARTIAL`.

Repository installation does not activate a session:

```text
repository_runtime: VERIFIED
!= session_runtime: READY
!= currentness: current

OS_INSTALL
  -> session_runtime: UNKNOWN
  -> session_initialization: UNINITIALIZED
  -> currentness: UNKNOWN

later BOOT / session attach with Host evidence
  -> may create session_id + frame_id
  -> may establish currentness under the existing Currentness contracts
```

The installer may record the Host and surfaces that performed installation as
install evidence. It must not reuse those values as proof of a future active
session or Current Anchor.

Recommended status language:

```text
SESSION_READY
SANDBOX_READY
LOCAL_READY
SOURCE_READY
PERSISTENCE_REQUIRED
PROPOSAL_READY
APPROVAL_REQUIRED
INSTALL_COMPLETE
PENDING_SYNC
```
