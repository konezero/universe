# Runtime Authority Certificate

Status: candidate core runtime architecture
Scope: ai-career Session Runtime Governance
Layer: session authority credential
Parent: `.ai/core/RUNTIME_LIFECYCLE.md`
Source observation: `konezero/gcs` PR #30, PR #31; ai-career issue #170
Created: 2026-07-02

## Purpose

This document defines the Runtime Authority Certificate used by a session after Runtime Boot.

Runtime Authority Certificate is a session-scoped Authority Credential, not a permanent certificate.

## Core Declaration

```text
RUNTIME AUTHORITY CERTIFICATE IS SESSION-SCOPED.
RUNTIME AUTHORITY CERTIFICATE IS GENERATED DURING RUNTIME BOOT.
RUNTIME AUTHORITY CERTIFICATE IS DESTROYED AT SESSION END.

THE CERTIFICATE IS NOT CANONICAL AUTHORITY.
THE CERTIFICATE IS NOT CRYPTOGRAPHIC IDENTITY.
```

## Meaning

Runtime Authority Certificate does not mean a cryptographic certificate.

It means:

```text
a current-session credential generated from source-backed runtime evidence
```

It allows the runtime to carry verified authority during the current session while preserving the rule that authority must originate from source-backed evidence.

Certificate presence is not execution authority.

Execution binding must follow:

```text
RUNTIME_AUTHORITY_EXECUTION_BINDING.md
```

## Lifetime

```text
Session Start
  -> Authority Bootstrap
  -> Runtime Authority Certificate generated
  -> Session Runtime
  -> Session End
  -> Certificate Destroy
```

Authority Lifetime is the same as Session Lifetime.

Authority must not persist beyond the session.

## Canonical Authority

The Source of Truth for Runtime Authority is Source-backed Runtime Evidence.

Examples:

```text
ai-career Runtime Contracts
Project Runtime
Project Anchor
Validation Evidence
Runtime Gates
Runtime State Snapshot
Boot Evidence Bundle
Runtime Image source coordinates
```

The certificate is generated from those sources.

The certificate itself is not Canonical Authority.

## Session Sandbox Relationship

```text
Runtime Authority Certificate
  -> may be stored in Session Sandbox
  -> current session only
  -> invalid after Session End
```

Session Sandbox is an authority cache, not an authority source.

## Issuance Contract

Runtime Authority Certificate issuance is part of Runtime Boot, but it is not
the same event as source attach, Runtime Image assembly, or mode selection.

Minimum issuance order:

```text
BOOT / REBOOT / SESSION_ATTACH / SOURCE_ATTACH
  -> read Source-backed Runtime Evidence
  -> create Boot Evidence Bundle
  -> assemble Runtime Image when available
  -> validate Runtime Authority coordinates
  -> issue Runtime Authority Certificate
  -> store certificate in Session Sandbox
```

If the runtime cannot validate authority coordinates, it must not invent a
certificate.

Expected result:

```text
certificate_status: MISSING | UNKNOWN
authority: UNKNOWN | UNASSIGNED
execution_assignment: UNASSIGNED
execution: prohibited
next: CERTIFICATE_REQUIRED | AUTHORITY_SOURCE_REQUIRED | OS_VALIDATE
```

## Minimum Certificate Fields

A Runtime Authority Certificate should be represented as a session-scoped
runtime credential with explicit source and frame coordinates.

Minimum fields:

```yaml
certificate_id: UNKNOWN
certificate_status: ISSUED | MISSING | UNKNOWN | STALE | INVALID
session_id: UNKNOWN
session_location: UNKNOWN
commander_surface: UNKNOWN
execution_surface: UNKNOWN
repository_location: UNKNOWN
node: UNKNOWN
mode: UNKNOWN
role: UNKNOWN
mode_scope: UNKNOWN
runtime_frame_id: UNKNOWN
anchor_id: UNKNOWN
source_repo: UNKNOWN
source_ref: UNKNOWN
source_commit: UNKNOWN
source_digest: UNKNOWN
boot_evidence_ref: UNKNOWN
runtime_image_ref: UNKNOWN
authority_source_ref: UNKNOWN
authority_status: UNKNOWN | UNASSIGNED | VERIFIED
execution_assignment: UNASSIGNED
validation_ref: UNKNOWN
issued_at: UNKNOWN
expires_at: SESSION_END
```

Unknown values must remain `UNKNOWN`.

`authority_status: VERIFIED` is allowed only when a source-backed authority
source has been read and validated in the current session.

`authority_status: UNASSIGNED` means the runtime may carry coordinates for
read-only context, but must not claim execution authority.

## Context Certificate

A session may issue a certificate whose authority status is `UNASSIGNED`.

This is a context certificate.

It may prove:

```text
which source, commit, frame, node, mode, role, and scope the session is using
```

It must not prove:

```text
permission to mutate
permission to execute external tools
permission to write repositories
permission to bypass Commander approval
```

This distinction allows a runtime mode to be active as session context while
execution authority remains unassigned.

```text
mode_context_active: true
certificate_status: ISSUED
authority_status: UNASSIGNED
execution_assignment: UNASSIGNED
mutation: prohibited
```

## Missing Certificate UX

When a role or Mode is selected after Project Runtime installation but before
BOOT has issued a Runtime Authority Certificate, the runtime must not silently
mark the Mode as active authority.

Recommended response:

```text
Mode selected as runtime context.
Runtime Authority Certificate is missing.
Authority remains UNASSIGNED.
Execution Assignment remains UNASSIGNED.
Create a session Runtime Authority Certificate from source-backed evidence?
```

If the session host cannot create or store a certificate:

```text
CERTIFICATE_REQUIRED
Reason: certificate cannot be issued or verified in this session host.
Allowed: read-only source-backed review, OS_STATUS, OS_VALIDATE
Blocked: mutation, repository write, external execution
```

## Invalidation

Runtime must treat the certificate as invalid when:

```text
Session ends.
Authority source changes.
Project Anchor changes.
Runtime State becomes stale.
Boundary changes.
Target changes.
Execution permission changes.
Execution assignment changes.
Approval evidence changes or falls out of scope.
Current writer frame changes.
Certificate validity cannot be verified.
```

Invalid certificate means:

```text
Authority: UNKNOWN
Execution: prohibited until re-bootstrap or Commander confirmation
```

## Non-Goals

Runtime Authority Certificate is not:

- a permanent certificate;
- a cryptographic certificate;
- long-term identity storage;
- valid after Session End;
- permission to trust Context Compression;
- hidden model memory;
- automatic role escalation;
- permission to execute from labels;
- replacement for project-local gates;
- replacement for user approval where required.

## Validation Questions

```text
Was the certificate generated during Runtime Boot?
Was it generated from source-backed evidence?
Is it limited to the current session?
Is it bound to current writer, execution surface, assignment, approval, source, anchor, and boundary before mutation?
Is it destroyed or invalidated at Session End?
Does Runtime reject it as Canonical Authority?
```
