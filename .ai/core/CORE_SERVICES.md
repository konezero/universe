# Core Services

Status: Candidate Core Architecture
Scope: ai-career
Layer: Core Services
Parent: `.ai/core/RUNTIME_ORCHESTRATOR.md`
Created: 2026-07-01

## Purpose

Core Services define the tool-like capabilities that perform actions under the Runtime Orchestrator.

Core Services do not decide authority.

Core Services do not create the Active Anchor.

Core Services do not make persistence active.

They act only after the Runtime Orchestrator has parsed the trigger, pulled the Active Anchor forward when needed, aligned the session, and built the active runtime frame.

## Core Declaration

```text
WHEN CORE SERVICES ARE INVOKED,
ACT UNDER ACTIVE FRAME.

WHEN A SERVICE RETURNS RESULT,
REPORT EVIDENCE TO ORCHESTRATOR.

WHEN A SERVICE TOUCHES STORAGE,
DO NOT CLAIM AUTHORITY FROM STORAGE.
```

## Position In The Architecture

```text
Current Command / Trigger
  -> Runtime Orchestrator
  -> L0 AI Runtime Governance
  -> L1 Session Framework
  -> L2 Runtime Model
  -> Core Services
  -> L3 Persistence Model
  -> Result Report
```

Short form:

```text
L0 INTERPRETS.
L1 ALIGNS.
L2 EXECUTES.
ORCHESTRATOR CONNECTS.
CORE SERVICES ACT.
L3 PRESERVES.
```

## Candidate Service Set

```text
GitHub Service
Storage Service
Memory Service
Archive Service
Search Service
Dispatch Service
Carrier Service
Status Service
Validation Service
```

## Common Service Contract

Every Core Service should follow the same boundary:

```text
INPUT
  -> active frame request
  -> scoped parameters
  -> source / target

ACTION
  -> read
  -> write
  -> search
  -> validate
  -> deliver
  -> report

OUTPUT
  -> result
  -> evidence
  -> completion state
  -> persistence candidate when useful
```

Required service properties:

```text
Source-backed
Scoped
Auditable
Non-authoritative
Recoverable when persisted
```

## GitHub Service

GitHub Service reads and writes repository-backed artifacts.

Examples:

```text
fetch repository file
create memory note
open pull request
read issue / PR
list branch state
```

Boundary:

```text
GITHUB SERVICE STORES OR RETRIEVES REPOSITORY ARTIFACTS.
GITHUB SERVICE DOES NOT DECIDE POLICY ADOPTION.
GITHUB WRITE SUCCESS IS NOT GOVERNANCE APPROVAL.
```

## Storage Service

Storage Service writes or reads artifacts from a storage provider.

Candidate providers:

```text
Local workspace / sandbox
GitHub
Files / iCloud Drive
Google Drive
NAS
Other providers
```

Boundary:

```text
STORAGE SERVICE WRITES ARTIFACTS.
STORAGE PROVIDER DOES NOT GRANT AUTHORITY.
SYNC COMPLETE REQUIRES APPROVED DURABLE TARGET.
```

## Memory Service

Memory Service prepares, reads, or writes memory candidates.

It should support Memory Sync without treating every conversation as durable memory.

Boundary:

```text
MEMORY SERVICE PRODUCES REVIEWED KNOWLEDGE ARTIFACTS.
MEMORY SERVICE DOES NOT MAKE MEMORY ACTIVE BY DEFAULT.
MEMORY REMAINS PASSIVE UNTIL SELECTED BY ACTIVE FRAME.
```

## Archive Service

Archive Service preserves durable history.

It may store carrier events, dispatch records, journal notes, policy candidates, runtime summaries, and decision evidence.

Boundary:

```text
ARCHIVE SERVICE PRESERVES HISTORY.
ARCHIVE SERVICE DOES NOT RESTORE AUTHORITY AUTOMATICALLY.
DERIVED STATE MAY BE CALCULATED FROM ARCHIVE.
```

## Search Service

Search Service retrieves source context.

It may search repository files, memory inbox, archive, PRs, issues, or external sources when allowed.

Boundary:

```text
SEARCH SERVICE FINDS EVIDENCE.
SEARCH RESULT IS NOT ACTIVE AUTHORITY.
SEARCH RESULT MUST BE INTERPRETED FROM ACTIVE ANCHOR.
```

## Dispatch Service

Dispatch Service delivers approved work to a target project, role, queue, or inbox.

Boundary:

```text
DISPATCH SERVICE DELIVERS APPROVED ITEMS.
DISPATCH SERVICE DOES NOT APPROVE ITEMS.
DISPATCH SERVICE DOES NOT EXECUTE PROJECT WORK BY ITSELF.
```

## Carrier Service

Carrier Service collects candidate events from watched sources.

Boundary:

```text
CARRIER SERVICE COLLECTS.
CARRIER SERVICE DOES NOT ADOPT.
CARRIER SERVICE DOES NOT REJECT.
CARRIER SERVICE DOES NOT PROMOTE POLICY.
```

## Status Service

Status Service reports source-backed runtime, session, queue, archive, or service state.

Boundary:

```text
STATUS SERVICE REPORTS VERIFIED STATE.
IF STATE IS NOT SOURCE-BACKED,
MARK IT UNVERIFIED.
```

Candidate status fields:

```text
Repository
Current Role
Boot State
Execution Assignment
Runtime State
Archive State
Queue State
Last Scan / Cursor / Pending Events when role-specific
Completion State
```

## Validation Service

Validation Service checks evidence for a result.

Examples:

```text
file exists
PR opened
commit created
queue updated
status source read
sync target confirmed
```

Boundary:

```text
VALIDATION SERVICE CHECKS COMPLETION EVIDENCE.
VALIDATION SERVICE DOES NOT DECIDE ADOPTION.
```

## Service Invocation Flow

```text
ACTIVE FRAME
  -> SERVICE REQUEST
  -> SERVICE ACTION
  -> SERVICE RESULT
  -> VALIDATION WHEN NEEDED
  -> ORCHESTRATOR RESULT
  -> L3 PERSISTENCE WHEN USEFUL
```

## Completion State

Services should report enough evidence for the orchestrator to classify completion:

```text
Complete
Ready
Waiting
Failed
Unknown
```

Examples:

```text
Complete -> service action finished and evidence exists.
Ready    -> artifact prepared but not persisted or executed.
Waiting  -> user approval, source, target, or credential is needed.
Failed   -> service attempted action and failed.
Unknown  -> result cannot be verified from available source.
```

## Sync Boundary

Generated output is not automatically durable.

```text
SANDBOX FILE CREATED
  -> Sync Ready
  -> user must save or sync to durable target

GITHUB FILE CREATED
  -> Sync Complete when repository write succeeds

LOCAL FILE CREATED
  -> Sync Complete only if target storage is approved durable storage
```

## Anti-Patterns

Core Services must not become governance owners.

Avoid:

```text
- Tool call success means policy accepted.
- GitHub result means authority restored.
- Memory result means current task changed.
- Archive entry means active frame resumed.
- Search result means source of truth changed.
- Status dashboard invents state without source.
```

## Placement Test

A concept belongs in Core Services when it answers:

```text
Which capability performs the action?
What input does the capability need?
What output and evidence does it return?
What boundary prevents it from deciding authority?
```

If it defines reference or authority, it belongs in L0.

If it defines lifecycle, it belongs in L1.

If it defines active execution frame, it belongs in L2.

If it defines durable artifacts, it belongs in L3.

If it coordinates multiple services and layers, it belongs in Runtime Orchestrator.

## Adoption Status

This is a candidate core architecture document.

It should be reviewed after Runtime Orchestrator is accepted.
