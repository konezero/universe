# Session Runtime Bootstrap

Status: candidate core runtime architecture
Scope: ai-career Session Runtime Governance
Layer: session bootstrap / authority reconstruction
Parent: `.ai/core/RUNTIME_LIFECYCLE.md`
Source observation: `konezero/gcs` PR #31; ai-career issue #170
Created: 2026-07-02

## Purpose

This document defines how a runtime session reconstructs authority at session start.

Runtime does not trust conversation memory, compressed context, or sandbox presence as authority.

## Core Declaration

```text
RUNTIME IS SESSION-SCOPED.
SESSION IS DISPOSABLE.
AUTHORITY IS EPHEMERAL.
AUTHORITY MUST BE RECREATED DURING EVERY RUNTIME BOOT.

CURRENT INTERPRETATION BASIS IS DERIVED EVIDENCE, NOT STORED AUTHORITY.
HISTORICAL ANCHOR EVIDENCE IS HERITAGE INPUT, NOT THE CURRENT ANCHOR.
BOOT CREATES A FRESH SESSION BOOT ANCHOR FROM CURRENT SOURCE-BACKED EVIDENCE.
BOOT MUST NOT REACTIVATE A HISTORICAL ANCHOR.

CONVERSATION IS NOT AUTHORITY.
COMPRESSED CONTEXT IS NOT AUTHORITY.
ROLE LABEL IS NOT AUTHORITY.
SANDBOX PRESENCE IS NOT AUTHORITY.
```

## Bootstrap Flow

```text
Session Start
  -> Read Source-backed Runtime Evidence
  -> Build Boot Evidence Bundle
  -> Derive Current Interpretation Basis
  -> Create Fresh Session Boot Anchor Snapshot
  -> Assemble Runtime Image when used
  -> Validate Runtime Authority
  -> Generate Runtime Authority Certificate
  -> Store Certificate in Session Sandbox
  -> Runtime Ready
```

## Source-Backed Runtime Evidence

Authority may be reconstructed only from source-backed runtime evidence.

Examples:

```text
ai-career Runtime Contracts
Project Runtime
Project Anchor
Validation Evidence
Runtime Gates
Runtime State Snapshot
Active Session State
Boot Evidence Bundle
Runtime Image source coordinates
```

If the evidence is missing, stale, unreachable, or ambiguous, authority is `UNKNOWN`.

## Current Interpretation Basis / Fresh Anchor Derivation

`Current Interpretation Basis` is a transient, derived evidence bundle used to
interpret the current session. It is not a stored Runtime object, Authority
source, Resume record, or replacement for Git-backed source.

Minimum inputs are:

```text
current source repository and immutable source coordinate
current validation evidence
current project / Node / Mode / Role evidence
new session_id + frame_id + anchor_id
current Host surface coordinates
current Host physical observation time
current Authority and Execution Assignment evidence or explicit UNKNOWN / UNASSIGNED
```

Historical `Runtime Snapshot`, `Current Anchor Frame`, or `Beyond Anchor`
evidence may be supplied as heritage input. That input may help interpretation,
but Boot must not copy its session identity, Anchor identity, currentness,
source identity, validation result, Authority, or Execution Assignment into the
new Session Boot Anchor.

Required output boundary:

```text
Current Interpretation Basis
  + optional heritage Anchor evidence
  -> new Session Boot Anchor Snapshot
  -> historical Anchor remains unchanged
```

The new Anchor must use the current source and validation basis. Reusing a
historical `anchor_id` is direct reactivation and must fail without creating a
new Runtime artifact.

Resume and Archive are optional continuity evidence. Their absence must not
block a fresh Boot when the current source-backed Boot inputs are complete.

Runtime Image is a boot artifact assembled from source-backed evidence.

Runtime Image is not authority.

Runtime Image assembly must follow:

```text
RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md
```

## Session Sandbox

Session Sandbox is not Canonical Authority.

Session Sandbox is an authority cache for the current runtime session.

```text
Source-backed Runtime Evidence
  -> Runtime Boot
  -> Runtime Authority Certificate
  -> Session Sandbox
```

Runtime may reuse the certificate from the Session Sandbox during the current session only when it is still valid.

The basis for authority remains Source-backed Runtime Evidence.

Session Sandbox may contain:

```text
Boot Evidence Bundle
SESSION_BOOT_IMAGE
Runtime Image
Runtime Authority Certificate
Active Runtime State
```

Sandbox presence does not make any contained artifact authoritative.

`SESSION_BOOT_IMAGE_CREATED` only means a disposable boot artifact was assembled
for the current session host.

It does not mean:

```text
role active
mode active
repository runtime verified
repository write enabled
execution authority assigned
```

`ACTIVE` and `VERIFIED` status claims require their own source-backed evidence.

When a session host can read source surfaces but has not validated them, report
`SOURCE_READY`, `PARTIAL`, or `UNKNOWN`, not `VERIFIED`.

When a Commander requests or selects a mode but mode authority has not been
verified, report the mode as requested or selected, not active.

## Failure Handling

```text
Authority Source Missing
  -> Authority UNKNOWN
  -> Role / Mode Simulation prohibited
  -> Execution prohibited
  -> report status to Commander
```

If source-backed evidence becomes available later, Runtime may re-bootstrap.

## Non-Goals

Session Runtime Bootstrap is not:

- project-local runtime installation;
- OS_UPDATE local assembly;
- hidden model memory;
- permission to trust compressed context;
- permission to treat Current Interpretation Basis as durable canonical state;
- permission to copy historical Anchor identity or source identity into a new Boot Anchor;
- permission to persist authority after Session End.

## Validation Questions

```text
Did Runtime read source-backed evidence during session start?
Did Runtime build a Boot Evidence Bundle when a Runtime Image is used?
Did Runtime derive the Current Interpretation Basis from current source and validation evidence?
Did Boot create a fresh Session Boot Anchor instead of reactivating a historical Anchor?
Did historical Anchor evidence remain heritage input only?
Did fresh Boot succeed without requiring Resume or Archive evidence?
Did Runtime treat Runtime Image as a session-scoped boot artifact, not authority?
Did Runtime avoid treating SESSION_BOOT_IMAGE_CREATED as role/mode activation?
Did Runtime avoid reporting VERIFIED without validation evidence?
Did Runtime reject conversation, compressed context, role label, and sandbox presence as authority?
Did missing evidence produce UNKNOWN?
Did Runtime generate the Authority Certificate only after evidence validation?
```
