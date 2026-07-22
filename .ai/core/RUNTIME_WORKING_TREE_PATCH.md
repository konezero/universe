# Runtime Working Tree Patch

Status: Candidate Core Architecture
Scope: ai-career / OS_INSTALL / OS_UPDATE / OS_REPAIR
Layer: Working Tree / Diff / Patch
Parent: `.ai/core/SESSION_HOST_NOTE.md`
Created: 2026-07-02

## Purpose

Runtime Working Tree Patch defines the safe execution model for repository-affecting runtime commands.

Runtime commands should prepare changes in a temporary session working tree first.

The repository should receive only an approved patch.

## Core Declaration

```text
REPOSITORY IS SOURCE OF TRUTH.
SESSION WORKING TREE IS WHERE CHANGES ARE PREPARED.
DIFF PRECEDES APPROVAL.
PATCH FOLLOWS APPROVAL.
VALIDATION FOLLOWS PATCH.
```

## Model

```text
Repository
  -> Fetch Sources
  -> Session Working Tree
  -> Validate / Install / Update / Repair
  -> Diff
  -> Approval
  -> Patch Repository
  -> OS_VALIDATE
```

## Meaning

```text
Repository
  -> durable source-backed state

Session Working Tree
  -> temporary workspace for preparing runtime changes

Diff
  -> reviewable change set

Patch
  -> approved repository write

Validation
  -> source-backed evidence after patch
```

## Command Coverage

This model applies to:

```text
OS_INSTALL
OS_UPDATE
OS_REPAIR
```

It may also apply to any command that creates, updates, or repairs repository-backed runtime files.

## Safe Flow

```text
1. Fetch current repository files.
2. Build a session working tree from fetched sources.
3. Run install, update, or repair logic in the working tree.
4. Produce a diff.
5. Ask for approval of that diff or proposal.
6. Patch the repository only after approval.
7. Run OS_VALIDATE after patch.
8. Record validation evidence.
```

## Why

This keeps the repository stable while the runtime prepares changes.

If preparation fails, discard the session working tree.

If the diff is wrong, do not patch.

If validation fails after patch, report PARTIAL or FAIL with evidence.

## OS_INSTALL

```text
OS_INSTALL
  -> fetch repository
  -> build working tree
  -> prepare runtime surface
  -> diff
  -> approval
  -> patch
  -> validate
```

## OS_UPDATE

```text
OS_UPDATE
  -> fetch current surfaces
  -> fetch current Core contract
  -> build working tree
  -> apply contract changes in working tree
  -> diff
  -> approval
  -> patch
  -> validate
```

## OS_REPAIR

```text
OS_VALIDATE
  -> PASS: stop
  -> PARTIAL / FAIL / UNKNOWN: produce repair report

OS_REPAIR
  -> fetch repository
  -> build working tree
  -> restore only missing or mismatched current-contract surfaces
  -> diff
  -> approval
  -> patch
  -> OS_VALIDATE
```

## OS_REPAIR Boundary

```text
OS_REPAIR DOES NOT EXPAND SCOPE.
OS_REPAIR RESTORES THE CURRENT RUNTIME CONTRACT.
```

OS_REPAIR should not invent new Modes, Rules, or architecture.

OS_REPAIR should only fix missing, stale, mismatched, or damaged surfaces required by the current contract.

## Approval Boundary

Approval applies to the proposed diff or write plan.

If a new change is discovered during execution, stop and produce a new proposal.

```text
Approved diff
  -> patch allowed

Unlisted change
  -> stop
  -> report updated diff
  -> require approval again
```

## Status Vocabulary

```text
WORKING_TREE_READY
  -> temporary session workspace exists

DIFF_READY
  -> proposed change set exists

APPROVAL_REQUIRED
  -> diff exists but is not approved

PATCH_APPLIED
  -> approved changes were written

VALIDATION_REQUIRED
  -> patch applied but validation not recorded

VALIDATION_PASSED
  -> source-backed validation evidence passes
```

## Anti-Patterns

Avoid:

```text
- Writing repository files before preparing a diff.
- Treating working tree success as repository success.
- Treating approval of a command as approval of an unseen diff.
- Expanding repair into redesign.
- Reporting PASS before post-patch validation evidence exists.
```

Prefer:

```text
- Fetch first.
- Prepare in session working tree.
- Show diff.
- Patch only after approval.
- Validate after patch.
```

## Relationship

```text
SESSION_HOST_NOTE.md
  -> session host and repository persistence distinction

RUNTIME_SOURCE_VERIFICATION.md
  -> fetch as source-backed truth

RUNTIME_INSTALL_UX.md
  -> request / proposal / approval / execution UX

PROJECT_RUNTIME_INSTALL.md
  -> install phases

OS_VALIDATION_EVIDENCE.md
  -> post-patch evidence shape
```

## Adoption Status

This is a candidate Core architecture document.

It should be tested by asking runtime commands to produce a diff before applying repository changes.
