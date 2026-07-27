# Instruction, Work, and Mutation Receipts

Status: active core runtime contract
Scope: user-directed project-source implementation work
Layer: execution delegation / pre-execution verification
Parent: `.ai/core/PRE_EXECUTION_VERIFICATION.md`

## Purpose

This contract separates the user-visible approval unit from the machine-visible
file mutation unit. It avoids asking a user to approve every expected source
file while preserving a target-bound one-time receipt immediately before each
write.

## Receipt Layers

```text
Direct User Instruction
  -> Instruction Receipt
  -> Work Receipt
  -> one-time Mutation Receipt
  -> ordinary local Git commit
  -> commit SHA
  -> Task Result Receipt (optional commit SHA links)
```

### Instruction Receipt

An Instruction Receipt is Host-attested evidence of a direct user request. It
records the task summary, declared project-source roots, permitted operations,
boundary, and instruction evidence reference. It is not a generic repository
delegation or authority outside its roots.

### Work Receipt

A Work Receipt is process-local activation of one Instruction Receipt. Its
current initial profile is deliberately narrow:

```text
scope_kind: PROJECT_SOURCE_WORK
operations: CREATE | MODIFY
scope expansion: within declared project-source roots only
source-control policy: HOST_AND_SCM_CONTROLLED
```

It may cover source files discovered during implementation. A file need not be
listed when the user gives the instruction, but every concrete target remains
checked against the active roots and boundary.

### Source-Root Resolution

The Host resolves the source root before activating a Work Receipt. A
project-owned `REPOSITORY_MANIFEST.md` declaration at
`layers.application.root` is canonical when present. It must resolve inside the
repository and already exist; a declared root is never created implicitly.

When the manifest does not declare a root, the Host classifies the local
project layout. `FRESH` means the repository has no project-owned top-level
surface after excluding Runtime-managed entries and Git metadata. Only in that
case may the Host default the Work Receipt root to `<repository>/src` and create
that root as part of the first in-scope `CREATE`.

`EXISTING` never defaults to or creates `src` merely because it is conventional.
Without a manifest declaration, it requires an explicit, already-existing
project source root. Once a root is validly selected, a receipt-aware Host may
create missing child directories for a `CREATE` target within that root.
Source-root selection, manifest provenance, and observed layout classification
are recorded in the Work Receipt. This setup remains part of the bounded Work
Receipt, not a raw filesystem escape.

### Mutation Receipt

The Execution Guard issues one Mutation Receipt per concrete write. It binds
the operation, target, payload hash, target preimage, current snapshot, and
effective Work Receipt provenance. It is one-time and Host-consumed; it is not
a separate user-facing approval prompt.

### Local Commit

After the bounded work and validation finish, ordinary local Git staging,
commit, and push remain source-control operations outside the Runtime. The
immutable commit SHA is Git evidence; it neither creates Runtime authority nor
requires a Runtime endpoint, execution receipt, proposal, or approval.

### Task Result Receipt

The file-backed Task Proposal journal preserves the user-facing task proposal
and its approval. After work completes, it appends a Result Receipt. That
receipt may link zero or more already-created immutable Git commit SHAs with a
repository reference. The journal never invokes or validates Git commands; Git
and any remote branch policy remain the responsible SCM boundary.

## Direct User Instruction Route

A Host may activate `PROJECT_SOURCE_WORK` without a second confirmation only
when it can attest a direct user instruction and all of the following are
declared:

```text
absolute project-source root, or Host-attested FRESH default resolution
CREATE and/or MODIFY only
bounded task summary
instruction evidence reference
explicit boundary
```

The Host must not infer this route from general conversation, Mode, Role,
BOOT, or an earlier unrelated approval.

## Escalation Boundary

The Work Receipt stops and returns to the strict Assignment -> approval ->
Execution Guard route when work would:

```text
leave the declared roots
DELETE or MOVE
change .ai/, .git/, Core, policy, templates, or configuration
touch an external system or unclassified durable target
run source-control operations through the Runtime
```

Task Frame Boss allocation may narrow an active Work Receipt for a Sub. It may
not expand roots, operations, or the user instruction boundary.

## Non-Goals

This contract does not remove the Execution Guard from file/source mutations,
make a Work Receipt durable authority, grant a Host capability, or allow a
Worker to self-delegate writes. Git commit and push evidence remain separate
from Runtime authority and are recorded only after the Host completes them.
