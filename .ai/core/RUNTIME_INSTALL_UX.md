# Runtime Install UX

Status: Candidate Core Architecture
Scope: ai-career / runtime install conversation
Layer: Install UX / Approval Boundary
Parent: `.ai/core/PROJECT_RUNTIME_INSTALL.md`
Created: 2026-07-02

## Purpose

Runtime Install UX defines how durable `OS_INSTALL` and `OS_UPDATE` should be
presented and approved in conversation, and how their `READY_FOR_BOOT` result
is kept separate from later Session Runtime activation.

It separates the install protocol from the user-facing interaction.

The protocol defines what must happen.

The UX defines how the runtime explains current state, proposal, approval, execution, and completion.

## Core Declaration

```text
REQUEST IS NOT APPROVAL.

PROPOSAL MUST PRECEDE APPROVAL.

APPROVAL APPLIES TO A SPECIFIC PROPOSAL.

USER AUTHORITY DOES NOT REPLACE PLATFORM BOUNDARIES OR RUNTIME PROTOCOL.
```

## Update Command Display

`OS_UPDATE` is the only user-facing command for reconciling an installed
Project Runtime. Core contracts and Runtime implementation may both be present
in the managed update inventory, but they must not be presented as separate
update choices.

The Host lifecycle adapter uses `RUNTIME_UPDATE` as an internal operation value.
That value remains visible where exact approval binding or raw evidence requires
it, but it must not replace `OS_UPDATE` in the conversation.

```text
User request / proposal title / approval prompt / completion
  -> OS_UPDATE

Technical approval payload / adapter request / raw result
  -> operation: RUNTIME_UPDATE
```

Do not ask the user to run `RUNTIME_UPDATE`. Do not present a Core update and a
Runtime update as two lifecycle branches. A successful internal update must be
reported as `OS_UPDATE complete`, followed by validation and Boot handoff
evidence.

## Responsibility Boundary

```text
Platform
  -> can this action be performed?

User
  -> do you want this action?

Runtime
  -> what exactly will change?

Session
  -> explains, proposes, waits, executes approved changes, reports evidence
```

A user can authorize repository changes, but the session must still respect platform limits and runtime safety rules.

## Request vs Approval

A user request starts the protocol.

A user approval authorizes a specific proposal.

```text
Request
  -> user asks for OS_INSTALL or install-related action

Proposal
  -> runtime reports target, source status, write plan, validation plan

Approval
  -> user explicitly approves the reported proposal

Execution
  -> runtime applies only the approved changes
```

Do not treat a generic install request as approval for an unseen write plan.

## Install UX Flow

```text
OS_INSTALL request
  -> environment / capability notice
  -> immutable source / durable target distinction
  -> Host execution capability resolution
  -> read-only target classification
  -> source fetch status
  -> exact managed-path install proposal
  -> approval prompt
  -> install durable Project Runtime only after approval
  -> validation evidence
  -> READY_FOR_BOOT handoff
```

## Mode-First Boot UX

When the Commander gives a role or mode shorthand before an active Session
Runtime exists, first determine whether a verified Project Runtime exists.

Examples:

```text
컨덕터모드
Conductor mode
마스터모드
MASTER mode
```

Expected response:

```text
Mode intent detected: <mode>

Active ai-career session runtime:
  <missing | unchecked | stale | UNKNOWN>

Project Runtime:
  <VERIFIED | missing | partial | UNKNOWN>

Next lifecycle:
  Project Runtime VERIFIED -> BOOT with requested Mode
  Project Runtime missing  -> OS_INSTALL_REQUIRED

BOOT proposal:
  Repository write: false
  Runtime Image: proposed when Host capability exists
  Authority: UNASSIGNED
  Execution Assignment: UNASSIGNED

Approve BOOT with the requested Mode?
```

The runtime must not report the requested Mode as active until raw BOOT evidence
contains `SESSION_BOOT_IMAGE_CREATED` and the applicable source coordinates are
verified.

If source surfaces can be read but validation has not passed, report
`SOURCE_READY` or `PARTIAL`, not `VERIFIED`.

If the Project Runtime is verified but the Session Runtime is missing, stale,
or unchecked, return `BOOT_REQUIRED` or `SESSION_BOOT_IMAGE_REQUIRED` instead
of routing execution.

If no verified Project Runtime exists, return `OS_INSTALL_REQUIRED`. Do not
partially assemble `.ai` during BOOT.

BOOT approval does not authorize repository writes. `OS_INSTALL` and
`OS_UPDATE` require their own exact durable-write proposals.

## Environment Notice

The runtime should distinguish chat execution from repository install capability.

Example statuses:

```text
READY_FOR_BOOT
  -> durable Project Runtime installation and validation succeeded

SESSION_READY
  -> a later BOOT created the disposable Session Runtime

REPOSITORY_READ_AVAILABLE
  -> source-backed fetch is available

REPOSITORY_WRITE_AVAILABLE
  -> repository writes are technically possible

APPROVAL_REQUIRED
  -> write cannot proceed until proposal approval

INSTALL_COMPLETE
  -> approved write and validation evidence are complete
```

Host / action wording:

```text
Current Host: <chat/mobile/browser/sandbox/local/source-only/repository>
Lifecycle: <OS_INSTALL|OS_UPDATE|BOOT|REBOOT|SESSION_ATTACH|SOURCE_ATTACH>
```

Use `attach` wording only when connecting a session to source or an already
running Runtime without installing files.

Use `install` wording only for durable project targets.

Mobile, browser, or chat execution is not itself an install target.

Repository write capability may still exist through an authorized connector.

## Proposal Shape

The proposal should be explicit and readable.

```text
Runtime Install Proposal

Immutable source:
  <repository + full commit + provider evidence>

Durable target:
  <absolute project root>

Execution environment:
  <connector / local repo / writable sandbox / UNKNOWN>

Lifecycle:
  OS_INSTALL

Project seed coordinates:
  Node: <node-or-UNKNOWN>
  Initial Mode template: MASTER
  Active Session Mode: not created by OS_INSTALL

Source status:
  <fetched / absent / stale-search-only / unknown>

Current finding:
  <not installed / partial / installed / unknown>

Target classification:
  <FRESH / ALREADY_INSTALLED / UPDATE_PROPOSAL / MIGRATION_REVIEW_REQUIRED / UNKNOWN_OR_BLOCKED>

Proposed changes:
  - create/update/delete <path>

Validation plan:
  - fetch written files
  - record latest evidence
  - append history evidence

Approval required:
  Reply with an explicit approval for this proposal to execute repository writes.
```

Session activation uses a separate BOOT proposal:

```text
No repository writes.
Create or attach the session-scoped Runtime Image after Mode approval.
```

Approval wording must distinguish:

```text
Approve BOOT session runtime assembly.
Approve this exact OS_INSTALL repository write proposal.
```

## Approval Language

Approval should reference the proposal.

Acceptable examples:

```text
Proposal approved. Proceed with OS_INSTALL.

Approve this proposal and install.

이 Proposal대로 설치해.
```

Insufficient examples:

```text
OS_INSTALL

고고

설치해

계속해
```

Those may start or continue the protocol, but they do not approve an unseen or changed write plan.

If the user has already seen the exact proposal in the same active session, short approval language can be accepted when unambiguous.

## Execution Boundary

Execution must stay within the approved proposal.

If the runtime discovers new required changes during execution, stop and report an updated proposal.

```text
Approved proposal
  -> execute listed changes only

New change needed
  -> stop
  -> update proposal
  -> request approval again
```

## Completion UX

After execution, report source-backed evidence.

```text
INSTALL_COMPLETE

Repository:
  <owner/repo>

Written surfaces:
  - <path>

Validation evidence:
  - <path>

Result:
  PASS / PARTIAL / FAIL / UNKNOWN

Next action:
  <BOOT / OS_STATUS / OS_VALIDATE / review registry>
```

Do not report INSTALL_COMPLETE from intention, search result, or historical evidence alone.

## Failure UX

When install cannot proceed, explain which boundary blocked it.

```text
Blocked by platform
  -> tool or permission does not allow write

Blocked by repository target
  -> no durable target confirmed

Blocked by source verification
  -> current files could not be fetched

Blocked by approval
  -> proposal exists but user has not approved it

Blocked by validation
  -> write happened but evidence did not pass
```

## Relationship to Core Protocol

```text
PROJECT_RUNTIME_INSTALL.md
  -> OS_INSTALL protocol and internal installer phases

SESSION_REPOSITORY_RUNTIME_MODEL.md
  -> Host, durable target, Session Runtime, and attach boundaries

RUNTIME_SOURCE_VERIFICATION.md
  -> search/fetch truth rule

RUNTIME_INSTALL_UX.md
  -> how the protocol is explained, approved, executed, and reported

OS_VALIDATION_EVIDENCE.md
  -> durable proof shape after write
```

## Anti-Patterns

Avoid:

```text
- Treating user request as approval.
- Treating platform capability as user approval.
- Treating user approval as platform permission.
- Writing files before showing the write plan.
- Expanding execution beyond the approved proposal.
- Reporting success without source-backed validation evidence.
```

Prefer:

```text
- Explain environment and capability.
- Distinguish immutable source, durable target, Host, and Session Runtime.
- Show fetched source status.
- Present a concrete write plan.
- Ask for approval of that proposal.
- Execute only approved changes.
- Report validation evidence.
```

## Placement Test

A concept belongs in Runtime Install UX when it answers:

```text
How should OS_INSTALL explain itself to the user?
What counts as approval?
How is responsibility split between platform, user, runtime, and session?
What should be shown before and after repository writes?
```

If it defines install phases, place it in `PROJECT_RUNTIME_INSTALL.md`.

If it defines session host and repository target, place it in `SESSION_REPOSITORY_RUNTIME_MODEL.md`.

If it defines current source truth, place it in `RUNTIME_SOURCE_VERIFICATION.md`.

If it defines evidence fields, place it in `OS_VALIDATION_EVIDENCE.md`.

## Adoption Status

This is a candidate Core architecture document.

It should be tested by asking for OS_INSTALL, then asking to install without approving a concrete proposal. The runtime should keep the proposal/approval boundary intact.
