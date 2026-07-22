# Session Runtime Governance

Status: Candidate Core Architecture
Scope: ai-career
Layer: Runtime Governance / Session Boundary
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-02

## Purpose

This document defines ai-career Session Runtime boot, attachment, and
governance after a durable Project Runtime or source-backed Boot input is
available.

ai-career Runtime Governance is activated in the current AI session by
`BOOT`, `REBOOT`, or a bounded `SESSION_ATTACH`.

It constrains how the session interprets commands, roles, authority, approval, validation, memory, checkpointing, and repository mutation.

It is not primarily a device runtime architecture and it is not primarily a repository file layout.

## Core Declaration

```text
SESSION FIRST.

RUNTIME GOVERNANCE CONSTRAINS THE ACTIVE AI SESSION.

REPOSITORY ATTACHMENT IS CONTEXTUAL.

PROJECT-LOCAL RUNTIME SURFACES ARE INSTALLED AND UPDATED BY THE OS LIFECYCLE.
SESSION RUNTIME IS CREATED BY BOOT, NOT INSTALLED.

ROLE / MODE AUTHORITY MUST BE SOURCE-BACKED BEFORE ROLE OR MODE CLAIMS.

AUTHORITY MUST BE RECREATED DURING EVERY RUNTIME BOOT.

AUTHORITY AT BOOT DOES NOT GUARANTEE AUTHORITY AT EXECUTION.
```

## Runtime Placement

```text
AI Session
  -> Runtime Boot / Session Attach
  -> Runtime Governance
  -> constrained work
```

When repository or project work is required:

```text
AI Session
  -> Runtime Governance
  -> Repository / Project attachment
  -> source fetch
  -> proposal
  -> approval
  -> diff / patch
  -> validation evidence
```

## State Split

Runtime status must distinguish session readiness from repository verification.

```text
SESSION_RUNTIME_READY
  = the current session has the ai-career runtime governance contract active

REPOSITORY_RUNTIME_VERIFIED
  = the repository-backed project runtime surface exists and passes source-backed validation
```

Do not collapse these into one readiness claim.

## BOOT / SESSION_ATTACH Meaning

Primary session meaning:

```text
BOOT / REBOOT
  -> activate ai-career Runtime Governance in the current AI session
  -> resolve source-backed Node / Mode coordinates
  -> stop at Mode Selection when Mode is missing, ambiguous, or not source-backed
  -> wait for Commander approval before session runtime assembly proceeds
  -> start or attach to a Runtime process when supported
  -> create a fresh Session Bootstrap and Current Anchor
  -> constrain interpretation, command routing, role boundary, approval, validation, memory, and checkpoint behavior

SESSION_ATTACH
  -> connect the current session to a running Runtime or source-backed Boot input
  -> no durable Project Runtime mutation
```

Mode Selection is part of session runtime governance.

```text
Mode label is not Mode Authority.
Mode Selection is not execution authority.
Commander approval of Mode Selection permits session runtime assembly only.
Repository mutation and task execution still require separate authority.
```

Runtime Image Assembly during BOOT is boot artifact creation.

```text
Runtime Image is not authority.
Session Sandbox is not authority.
Git-backed source remains authority.
Pre-Execution Verification still applies before mutation.
```

Durable Project Runtime boundary:

```text
OS_INSTALL / OS_UPDATE
  -> source-backed scan
  -> proposal
  -> approval
  -> project-local runtime surface assembly or reconciliation
  -> OS_VALIDATE
  -> validation evidence

BOOT / SESSION_ATTACH
  -> consume validated Project Runtime or source-backed Boot evidence
  -> no Project Runtime write
```

## Repository Boundary

Repository is not ignored.

Repository is used when the session needs one or more durable capabilities:

```text
source
persistence
validation evidence
checkpoint handoff
memory sync
patch target
project attachment
```

Repository attachment is Session Runtime input. It is not Project Runtime
installation.

## Proof Rule

```text
Search is discovery.
Fetch is truth.
Registry is claim / index.
Fetch + validation evidence is proof.
```

A registry claim such as `INSTALLED` does not prove session readiness or repository runtime verification by itself.

## Mutation Rule

Repository mutation requires the strict path:

```text
fetch current source
  -> scan
  -> proposal
  -> approval
  -> working tree / diff
  -> patch
  -> validation
  -> evidence
```

A user request for OS_INSTALL is not approval to mutate repository files unless the proposal has been shown and approved.

## Role / Mode Authority Rule

Role or mode labels are not role or mode authority.

```text
ROLE / MODE LABEL IS NOT ROLE / MODE AUTHORITY.

ROLE / MODE TRANSITION REQUIRES SOURCE-BACKED AUTHORITY.

IF AUTHORITY SOURCE IS UNAVAILABLE,
AUTHORITY IS UNKNOWN.

DO NOT SIMULATE UNVERIFIED ROLES OR MODES.
```

Before claiming a role or mode, the runtime must verify the applicable authority source.

Examples of authority sources:

```text
session runtime governance files;
project-local role / mode gate;
source-backed active session state;
explicit user-approved transition plus applicable runtime policy;
repository or connector source fetched in the active session.
```

If the source is unavailable, degraded, or not yet fetched, the runtime must report authority as `UNKNOWN` and wait.

Role/mode simulation is forbidden when authority is `UNKNOWN`.

## Session Runtime Bootstrap Rule

Runtime is session-scoped.

Session is disposable.

Authority is ephemeral.

Runtime must reconstruct authority during every Runtime Boot from source-backed runtime evidence.

```text
Session Start
  -> Read Source-backed Runtime Evidence
  -> Validate Runtime Authority
  -> Generate Runtime Authority Certificate
  -> Store Certificate in Session Sandbox
  -> Runtime Ready
```

Conversation, compressed context, role labels, and sandbox presence are not authority.

If authority cannot be reconstructed, report `UNKNOWN` and do not simulate role/mode authority or execute.

## Runtime Authority Certificate Rule

Runtime Authority Certificate is a session-scoped Authority Credential generated during Runtime Boot.

It is not Canonical Authority and it is not cryptographic identity.

```text
Source-backed Runtime Evidence
  -> Runtime Boot
  -> Runtime Authority Certificate
  -> current session only
  -> Certificate Destroy at Session End
```

Session Sandbox may cache the certificate during the current session, but Session Sandbox is not Canonical Authority.

## Pre-Execution Verification Rule

Authority verified at Boot does not immediately grant execution.

Immediately before execution, Runtime must verify:

```text
Authority Certificate still valid?
Project Anchor unchanged?
Runtime State still current?
Boundary unchanged?
Target unchanged?
Execution still permitted?
```

If any field changed or cannot be verified:

```text
Execution Cancel
  -> Authority UNKNOWN
  -> Re-bootstrap if necessary
  -> Commander confirmation
```

## Gate Integration

```text
Heartbeat Waiting Purpose Gate
  -> no purpose / bare call restraint

No Forced Inference Proposal Gate
  -> incomplete purpose / target / scope restraint

Role / Mode Authority Gate
  -> source-backed authority required for role/mode claims

Runtime Commands
  -> route complete commands after the gates

Runtime Authority Certificate
  -> session-scoped credential generated from source-backed evidence

Pre-Execution Verification
  -> final authority / anchor / state / boundary / target / permission check before execution
```

## Non-Goals

This document does not define:

- a general robot / vehicle / device runtime architecture,
- a universal host filesystem model,
- a specific sandbox implementation,
- a required repository file layout for every project.

Those may be implementation contexts, but the reusable ai-career concern is session governance.

## Relationship To Runtime Commands

`RUNTIME_COMMANDS.md` routes OS lifecycle commands separately from Session
Runtime commands.

This document clarifies the first semantic boundary:

```text
OS_INSTALL command detected
  -> durable Project Runtime install flow
  -> READY_FOR_BOOT only after validation

BOOT / REBOOT command or approved Mode entry detected
  -> session runtime governance activation
  -> consume installed or source-backed Boot evidence
```

## Relationship To Runtime Instruction Set

`RUNTIME_INSTRUCTION_SET.md` defines project attachment and project-local assembly contracts.

This document prevents durable Project Runtime installation from being confused
with Session Runtime activation.

```text
Session Runtime Governance
  -> BOOT / REBOOT / SESSION_ATTACH

OS Install
  -> durable repository/project surface assembly and validation
```

## Status Vocabulary Candidate

Recommended reporting fields:

```yaml
session_runtime:
  state: READY | PARTIAL | NOT_READY | UNKNOWN
  evidence: <session-visible source or command path>

repository_runtime:
  state: VERIFIED | PARTIAL | NOT_VERIFIED | UNKNOWN | NOT_ATTACHED
  runtime_root: <path-or-none>
  validation_evidence: <path-or-none>
```

## Validation Questions

A fresh-session QA test should ask:

```text
- Did the session activate runtime governance before attempting repository mutation?
- Did the session distinguish SESSION_RUNTIME_READY from REPOSITORY_RUNTIME_VERIFIED?
- Did the session fetch source before trusting registry or search?
- Did repository mutation wait for proposal and approval?
- Did status reports mark unknown fields as UNKNOWN instead of inventing state?
- Did role/mode transition requests verify source-backed authority before claiming the role or mode?
- Did unavailable authority sources produce UNKNOWN instead of role/mode simulation?
- Did Runtime reconstruct authority during every Runtime Boot?
- Did Runtime treat the Session Sandbox as cache rather than Canonical Authority?
- Did Runtime destroy or invalidate the Runtime Authority Certificate at Session End?
- Did Runtime verify authority again immediately before execution?
```
