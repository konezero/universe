# Runtime Lifecycle

Status: candidate core runtime architecture
Scope: ai-career Session Runtime Governance
Layer: lifecycle map / runtime ordering
Parent: `.ai/core/SESSION_RUNTIME_GOVERNANCE.md`
Source observation: `konezero/gcs` PR #29, PR #30, PR #31; ai-career issue #170
Created: 2026-07-02

## Purpose

This document organizes runtime gates and authority checks around the runtime lifecycle.

It is a map, not a replacement for the individual gate documents.

This session lifecycle begins only after a usable Project Runtime is available.
Durable Project Runtime installation, update, validation, and rollback belong to
the `OS_*` lifecycle. Session creation, attachment, bootstrap, and shutdown
belong to the `BOOT` / `REBOOT` lifecycle. The two lifecycles must not share one
readiness claim.

## Core Declaration

```text
RUNTIME IS SESSION-SCOPED.
SESSION IS DISPOSABLE.
AUTHORITY IS EPHEMERAL.
AUTHORITY MUST BE RECREATED DURING EVERY RUNTIME BOOT.

AUTHORITY AT BOOT DOES NOT GUARANTEE AUTHORITY AT EXECUTION.
ALWAYS VERIFY IMMEDIATELY BEFORE EXECUTION.

OS_INSTALL / OS_UPDATE MUTATE THE DURABLE PROJECT RUNTIME ONLY AFTER APPROVAL.
BOOT / REBOOT CREATE OR RECREATE THE DISPOSABLE SESSION RUNTIME.
SESSION_ATTACH DOES NOT INSTALL OR UPDATE PROJECT FILES.
```

## Canonical Lifecycle

```text
Session Start
  -> Session Runtime Bootstrap
  -> Boot Evidence Bundle
  -> Runtime Image Assembly when used
  -> Commander Wait Buffer Gate when Commander is still speaking
  -> Heartbeat Waiting Purpose Gate
  -> No Forced Inference Proposal Gate
  -> Role / Mode Authority Gate
  -> Runtime Authority Certificate
  -> Runtime Authority Execution Binding
  -> Boundary Check
  -> Pre-Execution Verification
  -> Execution
  -> Memory / Checkpoint Sync when requested
  -> Session End
  -> Certificate Destroy
```

## Lifecycle Stages

```text
01 Session Boot
02 Runtime Bootstrap
03 Purpose / Command Resolution
04 Runtime Gates
05 Runtime Image / Authority Reconstruction
06 Authority Execution Binding
07 Boundary Check
08 Pre-Execution Verification
09 Execution
10 Memory / Checkpoint Sync
11 Session Shutdown
```

## State Diagram

```text
SESSION_ABSENT
  -> BOOTSTRAP
  -> READY
  -> COMMANDER_WAIT
  -> EXECUTION_ACTIVE
  -> CHECKPOINT_SYNC
  -> SESSION_SHUTDOWN
  -> CERTIFICATE_DESTROYED
```

State meanings:

```text
SESSION_ABSENT
  -> no active runtime session

BOOTSTRAP
  -> Runtime is reading source-backed evidence and reconstructing authority

READY
  -> Runtime Authority Certificate exists for the current session

COMMANDER_WAIT
  -> Runtime is waiting for purpose, scope, confirmation, or missing authority source

EXECUTION_ACTIVE
  -> Pre-Execution Verification passed and action is in progress

CHECKPOINT_SYNC
  -> Runtime is preserving selected state, memory, or checkpoint material when requested

SESSION_SHUTDOWN
  -> Runtime is ending the session and invalidating current-session authority

CERTIFICATE_DESTROYED
  -> Runtime Authority Certificate is destroyed and cannot be reused
```

## Gate Placement

Heartbeat Waiting Purpose Gate:

```text
bare call / no purpose
  -> heartbeat only
  -> no command route
```

Commander Wait Buffer Rule:

```text
explicit wait / still speaking / multi-part instruction
  -> WAITING_COMMANDER
  -> collect fragments only as non-authoritative context
  -> no execution until explicit release intent
```

No Forced Inference Proposal Gate:

```text
partial command / action-only / incomplete scope
  -> proposal or missing-anchor question
  -> no execution before Commander confirmation
```

Role / Mode Authority Gate:

```text
role or mode label / transition request
  -> source-backed role/mode authority required
  -> UNKNOWN if authority source is unavailable
  -> no unverified simulation
```

Runtime Authority Certificate:

```text
source-backed runtime evidence
  -> Runtime Boot
  -> session-scoped Authority Credential
  -> current session only
```

Runtime Image Assembly:

```text
Boot Evidence Bundle
  -> Runtime Image
  -> session-scoped boot artifact
  -> not authority
```

Runtime Authority Execution Binding:

```text
Runtime Authority Certificate
  -> bind to current writer / execution surface / assignment / approval / source / anchor / boundary
  -> mutation only when binding is current
```

Pre-Execution Verification:

```text
before execution
  -> verify authority, anchor, state, boundary, target, and permission
  -> cancel execution if any check fails or is UNKNOWN
```

## Relationship To Existing Documents

```text
SESSION_RUNTIME_BOOTSTRAP.md
  -> defines how authority is reconstructed at session start

RUNTIME_AUTHORITY_CERTIFICATE.md
  -> defines the session-scoped authority credential

PRE_EXECUTION_VERIFICATION.md
  -> defines immediate-before-execution verification

INTENT_FIRST_ROUTING_GATE.md
  -> classifies utterance intent before command / mode token routing

RUNTIME_STATE_TRUST_GATE.md
  -> verifies active state, provenance, narrative time, and evidence priority before continuation

COMMANDER_WAIT_BUFFER_RULE.md
  -> blocks mutation while Commander is still speaking and releases fragments only through normal gates

RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md
  -> defines Boot Evidence Bundle and Runtime Image assembly as a session-scoped boot artifact

RUNTIME_AUTHORITY_EXECUTION_BINDING.md
  -> defines certificate binding to execution coordinates before mutation

RUNTIME_COMMANDS.md
  -> routes complete commands after pre-command gates

SESSION_RUNTIME_GOVERNANCE.md
  -> owns the session boundary and authority semantics
```

## Non-Goals

This lifecycle does not:

- merge every gate into one large document;
- remove existing gates;
- replace Runtime Commands;
- replace Runtime Preflight;
- make the session sandbox Canonical Authority;
- make Runtime Authority Certificate a cryptographic identity;
- make Runtime Image canonical authority;
- persist authority across session boundaries.

## Validation Questions

Runtime QA should ask:

```text
Did the session reconstruct authority during Runtime Boot?
Did known command / mode / role / anchor tokens pass through intent classification before routing?
Did explicit wait or still-speaking language enter WAITING_COMMANDER before routing?
Were buffered fragments treated as non-authoritative until release intent?
Did incomplete purpose/scope pass through the proper gates before routing?
Did active *_ING state pass state trust checks before continuation?
Did restored or stale active state route to OS_STATUS / OS_VALIDATE before continuation?
Did evidence conflicts resolve by source-backed priority or UNKNOWN?
Was any Runtime Image assembled from a Boot Evidence Bundle and treated only as a boot artifact?
Did role/mode labels require source-backed authority?
Was a session-scoped Runtime Authority Certificate generated only from source-backed evidence?
Was the certificate bound to current writer, execution surface, assignment, approval, source, anchor, and boundary before mutation?
Did execution wait for Pre-Execution Verification?
Was the certificate destroyed or invalidated at Session End?
```
