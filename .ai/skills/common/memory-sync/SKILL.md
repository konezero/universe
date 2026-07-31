---
name: memory-sync
description: Prepare user-selected memory notes without creating candidates, authority, or publication.
---

# Memory Sync

Invocation class: `REFERENCE_RUNTIME_PREPARATION`

Preparation capability: `memory-sync.prepare = AVAILABLE`

Durable handoff capability: `HOST_DEPENDENT`

The reasoning layer may identify notes worth preserving, but the user selects
what is eligible for memory packaging. A selected memory note is not a
Candidate. Invoke `memory-sync prepare` only with that explicit selection and
its source references.

```text
Conversation
  -> optional note identification
  -> user-selected memory note
  -> memory-sync prepare
  -> PREPARED passive artifact
  -> repository default branch HANDOFF_APPEND when an approved provider writer is available
```

```text
MEMORY_SYNC != Candidate creation != Queue publication
```

Do not save every conversation, choose Candidates for the user, infer a
storage provider, or let stored memory override the Current Anchor. Provider
evidence is required before reporting durable completion. Memory persistence is
an Execution Guard exception only within declared Runtime memory/inbox paths;
source or project-owned changes still use the normal guard.

## Repository Target

For a repository-backed Memory Inbox, the selected source and the append target
are separate coordinates.

```text
PR / Candidate / working branch -> base_source_ref only
repository default branch       -> HANDOFF_APPEND target
```

The Host must obtain the repository default branch from the provider and append
the selected artifact there. It must not infer the append target from the
currently checked-out branch, the source used for BOOT, a PR HEAD, or the active
conversation.

`memory-sync prepare` is not a prerequisite for provider append when the user
has already selected the exact memory content and that selection can be
recorded as `selection_ref`. A missing installed
`.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json` blocks the installed
Runtime preparation route only. It does not block an otherwise approved
provider append.

If provider write capability or provider-observed default-branch evidence is
unavailable, stop at the passive artifact. Do not create an untracked local
file, write to the source checkout, switch branches, commit, or push as a
fallback.

## Node-Scoped Project Memory

When a Project has a published Universe Seed, a selected memory note may carry
a `node_ref` for one functional or implementation node. Append it only under
the Project-local path defined by:

```text
.ai/templates/universe_node_memory/README.md
```

The node reference improves later recall. It does not modify the Seed graph,
create a Universe connection, publish to a queue, or promote the note.

Use `memory-sync node-prepare` for this form. It returns one passive
`ai-career.universe-node-memory.v1` candidate and its exact target under
`.ai/memory/universe_nodes/`; a separately approved provider
`HANDOFF_APPEND` is still required to append the record.

## Handoff Append

`HANDOFF_APPEND` is distinct from `SOURCE_MUTATION`.

It may persist only a selected append-only handoff artifact under a declared
Runtime-owned path, for example `.ai/memory/inbox/`,
`.ai/memory/universe_nodes/`, `.ai/queue/`, or an Archive evidence path. A
source-only mobile or web Host may perform it when all
of the following are true:

```text
provider write capability: AVAILABLE
exact Runtime-owned append path: approved
memory selection or user approval: recorded
provider write result: returned
```

The persistence evidence must be provider-native and separate from execution
receipts:

```yaml
schema: ai-career.handoff-append-evidence.v2
operation_class: HANDOFF_APPEND
provider: <github|drive|other>
target_path: <runtime-owned-append-path>
target_repository_ref: <repository-provider-ref>
target_branch: <provider-observed-default-branch>
repository_default_branch: <provider-observed-default-branch>
selection_ref: <approved-memory-note-or-user-selection>
base_source_ref: <immutable-source-or-parent-ref>
result_ref: <commit|blob|pr|provider-object>
provider_receipt_ref: <provider-evidence-or-UNKNOWN>
```

For non-repository providers, repository and branch fields are omitted. For a
repository provider, `target_branch` must equal
`repository_default_branch`. `base_source_ref` remains evidence about where the
memory originated and never selects the append branch.

Existing Inbox records are `OBSERVED_REFERENCE` until a Parent explicitly reads
and adopts them. `HANDOFF_APPEND` never edits source, Core, templates,
configuration, project-owned files, or external systems. Those are
`SOURCE_MUTATION` and require an execution evidence Host; without one, report
`BLOCKED_EXECUTION_HOST_REQUIRED`.
