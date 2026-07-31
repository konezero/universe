# Universe Node Memory Template

Status: active project contract template
Owner: Project Memory Sync

## Purpose

Node memory attaches a Project-local brainstorm, question, observation, or
unresolved idea to one published Universe graph node. It is append-only memory,
not a Seed revision, Queue candidate, Bench observation, or Career candidate.

Installing this template creates no memory record or directory.

## Storage Shape

```text
.ai/memory/universe_nodes/
  functional/<node_id>/
  implementation/<node_id>/
```

Each appended record must identify the graph coordinate it describes:

```yaml
schema: ai-career.universe-node-memory.v1
memory_id: <project-local-memory-id>
state: BRAINSTORM | OBSERVED | QUESTION | DECISION_NOTE
node_ref:
  graph: functional | implementation
  node_id: <published-node-id>
  seed_manifest_ref: .ai/universe/manifest.json
  seed_id: <published-seed-id>
origin:
  selection_ref: <explicit-user-selection>
  source_refs: [<conversation-or-evidence-ref>]
  observed_at: <timestamp>
  body: <bounded project-local note>
```

The record may link to existing Project documents or evidence references. It
must not rewrite a graph node, invent a document binding, contain secrets, or
include raw worker transcripts.

## Invariant

```text
MEMORY_SYNC != Candidate creation != Queue publication
```

`MEMORY_SYNC` may append a node memory record after the user selects the note.
It does not create a Project-to-Universe observation candidate or publish
anything. A later Project Master or Universe review may explicitly propose a
separate candidate with its own provenance, redaction, approval, and receipt.

The reference Runtime prepares this form without a write:

```text
memory-sync node-prepare
  -> PREPARED node memory candidate
  -> .ai/memory/universe_nodes/<functional|implementation>/<node_id>/<memory_id>.json
  -> separately approved provider HANDOFF_APPEND
```

For repository-backed storage, the append target is the provider-observed
repository default branch. The source PR, Candidate branch, BOOT source, and
current checkout remain source references only. If provider write or
default-branch evidence is unavailable, stop without writing to the local
checkout.

Preparation does not create a directory or file. The target is valid only for
the selected normalized graph and node identifier.
