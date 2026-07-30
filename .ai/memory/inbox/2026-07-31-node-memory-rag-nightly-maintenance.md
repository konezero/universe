# Node Memory RAG and Nightly Maintenance

Status: BRAINSTORM
State: UNLINKED
Observed: 2026-07-31

## Summary

Memory Sync preserves selected conversation notes as reference context. A note
is attached by `node_ref` when a target node is known. When no node is selected,
the note remains visible in the unlinked-memory surface until a later linking
pass resolves or proposes a node.

## Node Recall

```text
Conversation
  -> user-selected Memory Sync
  -> node_ref attachment or UNLINKED
  -> user selects a Project node
  -> attached memories are loaded automatically
  -> bounded node Context Pack
  -> Project Master or Task Frame Worker
```

Selecting a node is the recall key. The selected node's memories are loaded
automatically. Shared parent-node memory may be inherited; sibling-node memory
is excluded by default. Memory remains reference context and does not override
current source, documents, contracts, Bench evidence, or Current Anchor state.

## Nightly Maintenance

The later batch LLM may perform bounded maintenance:

1. Link newly selected or unlinked memories to likely nodes.
2. Extract Project Seed change proposals from current evidence.
3. Aggregate Bench observations by model, Skill, and task type.
4. Find repeated success, failure, and policy patterns.
5. Detect duplicate nodes and missing graph or document relationships.
6. Refresh future-route suggestions.
7. Rebuild node-scoped Context Pack indexes.

The batch may extract, link, summarize, rank, and propose. It does not silently
change the canonical Project Seed, project contracts, Candidate state,
execution instructions, or Career governance.

## UI Surfaces

- `Unlinked Memory`: lists memories without a confirmed node and shows proposed
  node matches and reasons.
- `Memory RAG`: searches the current node, current project, whole Universe, or
  unlinked memories.
- Node detail: shows attached memory, documents, Bench observations, decisions,
  failures, and validation references together.
- Chat: uses the selected node as the default retrieval scope.

## Invariant

```text
Node Memory = reference context
Node Memory != Candidate
Node Memory != execution instruction
Node Memory != current contract
MEMORY_SYNC != Queue publication
```