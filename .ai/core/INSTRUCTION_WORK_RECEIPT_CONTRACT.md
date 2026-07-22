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
  -> Push Proposal and separate approval
```

### Instruction Receipt

An Instruction Receipt is Host-attested evidence of a direct user request. It
records the task summary, declared project-source roots, permitted operations,
boundary, and instruction evidence reference. It is not a generic repository
delegation, push approval, or authority outside its roots.

### Work Receipt

A Work Receipt is process-local activation of one Instruction Receipt. Its
current initial profile is deliberately narrow:

```text
scope_kind: PROJECT_SOURCE_WORK
operations: CREATE | MODIFY
scope expansion: within declared project-source roots only
push policy: SEPARATE_DURABLE_PROPOSAL_APPROVAL_REQUIRED
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

After the bounded work and validation finish, the Host stages the completed
work paths and creates an ordinary local Git commit. The commit itself creates
the evidence: its immutable Git SHA. Local staging and commit do not create a
Runtime proposal, approval, closure record, or proposal-database entry and do
not require a separate user approval.

### Push Proposal

Push is never implied by a Work Receipt or local commit. After the local commit
exists, the Host creates a separate file-backed `PUSH` proposal
that binds the exact immutable local commit SHA, current branch, remote,
target branch, and observed remote HEAD. It requires a separate explicit
approval from a later user input and permits only a fast-forward push of that
bound HEAD. The input that creates the proposal cannot also approve it.

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
DELETE, MOVE, or execute COMMAND
change .ai/, .git/, Core, policy, templates, or configuration
touch an external system or unclassified durable target
push without a separately approved PUSH proposal
```

Task Frame Boss allocation may narrow an active Work Receipt for a Sub. It may
not expand roots, operations, or the user instruction boundary.

## Non-Goals

This contract does not remove the Execution Guard from file/source mutations
or push, make a Work Receipt durable authority, grant a Host capability, or
allow a Worker to self-delegate writes. It also does not treat a local commit
as push approval.
