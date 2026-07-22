# Runtime Authority Execution Binding

Status: core runtime gate
Scope: ai-career Session Runtime / Execution Safety
Layer: authority certificate binding / pre-execution guard
Parent: `.ai/core/RUNTIME_AUTHORITY_CERTIFICATE.md`
Created: 2026-07-05

## Purpose

This document defines the minimum execution binding for a Runtime Authority
Certificate.

A certificate that exists is not enough.

The certificate must still match the current execution surface, source-backed
frame, anchor, boundary, target, and approval evidence immediately before
mutation.

## Core Declaration

```text
ROLE LABEL IS NOT ROLE AUTHORITY.
MODE LABEL IS NOT MODE AUTHORITY.
CERTIFICATE PRESENCE IS NOT EXECUTION AUTHORITY.

RUNTIME AUTHORITY CERTIFICATE CARRIES VERIFIED AUTHORITY CONTEXT.
IT DOES NOT CREATE AUTHORITY.

EXECUTION REQUIRES CURRENT BINDING.
```

## Binding Fields

Before execution, Runtime must check the certificate against the current
source-backed runtime coordinates.

Minimum binding fields:

```text
session_id
session_location
commander_surface
execution_surface
repository_location
node
mode
role
mode_scope
anchor_id
runtime_frame_id
source_repo
source_ref or branch
source_commit or UNKNOWN
source_digest or surface_hashes
authority_source_ref
execution_assignment
target
boundary
approval_ref or UNKNOWN
validation_ref
```

Unknown binding values do not become permission.

## Current Writer Rule

Execution authority follows the verified current writer frame, not only the
Commander chat surface.

If another verified writer frame advances the current frame after this
certificate was issued, the older certificate is stale for mutation.

The older session may continue read-only review or status work only when it
surfaces stale status and does not mutate source or runtime state.

To mutate again, the older session must re-bootstrap, reassemble evidence when
needed, and issue a fresh certificate from current source-backed evidence.

## Execution Assignment

Execution requires a scoped execution assignment when the action mutates source,
repository state, external systems, or a durable artifact outside the declared
Runtime-owned operational-state exception in `PRE_EXECUTION_VERIFICATION.md`.

An exact Assignment binds one target. A `PROJECT_SOURCE_WORK` receipt may bind
declared source roots and `CREATE`/`MODIFY` operations from one direct user
instruction; the final Guard receipt still binds the exact target, payload, and
preimage. Commit and push remain separate explicit approvals.

```text
execution_assignment = UNASSIGNED
  -> mutation blocked
```

Role, mode, state, or certificate presence must not replace an execution
assignment.

## Approval Evidence

When Commander approval is required, the certificate must bind to approval
evidence.

Minimum approval coordinates:

```text
commander_surface
approval_ref
approval_scope
approved_target
approved_boundary
approved_action
approval_time or logical_event
```

Approval outside the current target or boundary is not reusable.

## Failure Handling

Runtime must block mutation and report the narrow failure reason when:

```text
certificate missing
certificate cannot be verified
session_id mismatch
session_location / execution_surface mismatch
repository_location mismatch
anchor_id or runtime_frame_id stale
source_commit or source_digest stale
authority_source_ref missing or changed
execution_assignment missing
approval missing or out of scope
target changed
boundary changed
Runtime Image disagrees with Git-backed source
```

Expected result:

```text
Authority: UNKNOWN | STALE | BLOCKED
Execution: prohibited
Next: OS_STATUS / OS_VALIDATE / re-bootstrap / Commander confirmation
```

## Relationship To Runtime Image

A Runtime Image may help check certificate binding.

Runtime Image disagreement never grants execution.

If Runtime Image and Git-backed source disagree:

```text
Git-backed source wins.
Final status = UNKNOWN or STALE.
Mutation blocked.
```

## Relationship To Pre-Execution Verification

Pre-Execution Verification consumes this binding contract.

Immediately before execution, Runtime must verify:

```text
certificate current?
binding fields still match?
execution assignment current?
approval evidence in scope?
anchor/frame/source/validation current?
target and boundary unchanged?
```

Any `UNKNOWN`, `STALE`, or `BLOCKED` check cancels mutation.

## Non-Goals

This contract does not:

- make certificates cryptographic identities;
- persist certificates after Session End;
- grant execution from role or mode labels;
- replace Commander approval;
- replace project-local write scope;
- replace Pre-Execution Verification;
- require one storage format for certificates.

## Validation Questions

Runtime QA should ask:

```text
Does the certificate bind to current session and execution coordinates?
Does it bind to source-backed authority evidence?
Does it bind to current anchor/frame/source/validation evidence?
Does it include or reference execution assignment when mutation is requested?
Does it include in-scope approval evidence when approval is required?
Does a stale or mismatched certificate block mutation?
Can stale sessions continue read-only review without mutating?
```
