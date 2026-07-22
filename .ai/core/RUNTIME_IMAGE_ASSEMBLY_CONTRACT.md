# Runtime Image Assembly Contract

Status: core runtime contract
Scope: ai-career Session Runtime / Project Attachment
Layer: boot evidence bundle / runtime image assembly
Parent: `.ai/core/RUNTIME_LIFECYCLE.md`
Created: 2026-07-05

## Purpose

This document defines the minimum Core contract for assembling a session-scoped
Runtime Image from source-backed runtime evidence.

It does not define one production implementation.

## Core Declaration

```text
GIT-BACKED SOURCE REMAINS AUTHORITY.

BOOT EVIDENCE BUNDLE IS THE SOURCE-BACKED INPUT PACKAGE.

RUNTIME IMAGE IS A SESSION-SCOPED BOOT ARTIFACT.

RUNTIME IMAGE IS NOT AUTHORITY.

RUNTIME IMAGE ASSEMBLY IS ENVIRONMENT-SPECIFIC.
```

## Boot Evidence Bundle

A Runtime Image may be assembled only from a Boot Evidence Bundle.

Minimum evidence coordinates:

```text
source_repo
source_ref or branch
source_commit or UNKNOWN
surface_hashes or source_digest
core_surface_refs
contract_template_refs
runtime_state_ref
runtime_frame_ref
anchor_ref
validation_ref
node_mode_ref
currentness_ref
checked_at or UNKNOWN
```

Unknown values must remain `UNKNOWN`.

The Boot Evidence Bundle is evidence input, not authority by itself.

## Runtime Image

Runtime Image means:

```text
a compact session-scoped running image assembled from source-backed evidence
```

It may be queryable, indexed, or reduced for the current session.

It must be disposable.

It must not be treated as the source of truth.

## Session Boot Image

`SESSION_BOOT_IMAGE` is a permitted Runtime Image profile for chat, mobile,
browser, connector, sandbox, and other ephemeral session hosts.

It may be represented as:

```text
in-memory profile
Markdown snapshot
JSON object
sandbox-local file
other session-scoped artifact
```

Creating a `SESSION_BOOT_IMAGE` means the session host assembled a disposable
boot artifact from the selected Boot Evidence Bundle.

It does not mean the selected mode is active.

It does not mean repository runtime surfaces are verified.

It does not grant repository write authority.

Minimum status coordinates:

```text
source_repo
source_commit or UNKNOWN
source_surfaces or source_digest or UNKNOWN
host_action
assembly_profile
mode_requested or UNKNOWN
mode_authority_source or UNKNOWN
authority
execution_assignment
evidence_ref or session_artifact_ref or UNKNOWN
validation_ref or UNKNOWN
verification_status
```

Unknown values must remain `UNKNOWN`.

## Status Evidence Rule

Runtime Image status claims must be evidence-backed.

```text
SESSION_BOOT_IMAGE_CREATED
  requires an assembled session artifact or in-memory profile coordinate.

SESSION_ATTACH_ACTIVE
  requires a session attach target and host action.

<MODE>_REQUESTED
  may come from Commander intent or explicit mode selection.

<MODE>_ACTIVE
  requires source-backed mode authority and applicable session approval.

repository_runtime_surface: SOURCE_READY
  means source surfaces were found or attached.

repository_runtime_surface: VERIFIED
  requires validation evidence that passed.
```

If validation evidence is absent, stale, or unchecked, report:

```text
SOURCE_READY
PARTIAL
UNKNOWN
```

Do not report `VERIFIED`.

If mode authority evidence is absent, stale, or unchecked, report:

```text
mode_requested: <mode>
mode_authority: UNKNOWN
mode_active: false
```

Do not report `<MODE>_ACTIVE`.

Recognizing that a Runtime Image should be assembled is not assembly.

Recognizing a role or mode label is not role or mode authority.

## Implementation Profiles

Projects and session hosts may implement the Runtime Image with any profile that
preserves the contract.

Allowed profiles include:

```text
process memory
Markdown / YAML snapshot
JSON object
SQLite :memory:
SQLite file cache
file-backed local cache
other project-local runtime object
```

The implementation profile is not Core authority.

Core must not require SQLite.

Core must not require a durable `runtime.db`.

Ephemeral hosts such as mobile or connector-only sessions should prefer compact
session-scoped images when repeated source reads are expensive.

Local filesystem hosts may use memory, JSON, file cache, or database-backed
profiles according to project needs.

## Currentness And Invalidation

A Runtime Image is current only for the source, frame, anchor, and validation
state it was assembled against.

Runtime must treat the image as stale when:

```text
source_commit or source_digest changes
runtime_frame advances semantically
current anchor changes
validation evidence changes
another verified writer frame becomes current
authority source changes
the image cannot prove its source coordinates
```

`runtime_frame advances semantically` means the frame, Anchor, source,
validation, writer, or authority basis changed. A same-Anchor user input that
advances `observed_at` only is a physical observation touch and does not make
the Runtime Image stale by itself.

```text
observed_at-only touch -> Runtime Image remains current
Anchor / frame / source / validation replacement -> Runtime Image stale
```

Stale Runtime Image means:

```text
mutation blocked
re-bootstrap or reassembly required
read / review may continue only with stale status surfaced
```

## Relationship To OS_VALIDATE

OS_VALIDATE may read a Runtime Image.

OS_VALIDATE must still validate against Git-backed source.

```text
Runtime Image result
  vs
Git-backed source validation
  -> final OS_VALIDATE result
```

PASS is allowed only when Runtime Image and Git-backed source agree.

If Runtime Image and Git-backed source disagree, Git-backed source wins.

If the disagreement cannot be resolved safely, report `UNKNOWN`.

If Runtime Image is behind while Git-backed source validates, report `STALE`.

## Relationship To Runtime Authority

Runtime Image may carry evidence used to build or check a Runtime Authority
Certificate.

Runtime Image does not create authority.

Authority reads Git-backed source.

Certificate freshness must be checked against the image source coordinates and
current Git-backed evidence immediately before execution.

## Non-Goals

This contract does not:

- promote the full Runtime Image Builder implementation to Core;
- require SQLite;
- require a durable runtime database;
- make Runtime Image canonical authority;
- replace Git-backed source verification;
- replace OS_VALIDATE;
- replace Runtime Authority Certificate;
- replace Pre-Execution Verification.

## Validation Questions

Runtime QA should ask:

```text
Was a Boot Evidence Bundle built from source-backed evidence?
Are unknown bundle fields preserved as UNKNOWN?
Does the Runtime Image record source coordinates?
Does SESSION_BOOT_IMAGE_CREATED point to an actual session artifact or profile coordinate?
Does VERIFIED have validation evidence?
Does MODE_ACTIVE have source-backed mode authority?
Is the Runtime Image disposable and session-scoped?
Does the implementation avoid treating the image as authority?
Can OS_VALIDATE compare image evidence against Git-backed source?
Does source/frame/anchor/validation advancement mark the image stale?
Does an observed_at-only Current Anchor touch avoid invalidating the image?
Does a stale image block mutation until reassembly or re-bootstrap?
```
