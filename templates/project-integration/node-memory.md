# Universe Node Memory Contract

Owner: Universe project-integration template catalog

## Boundary

Node memory is append-only Project-local context attached to a published
functional or implementation node.

```text
MEMORY_SYNC != candidate creation != queue publication
```

The user selects the note and its target node. The resulting local record may
link Project documents or evidence references, but it cannot rewrite a Seed,
invent a document binding, include a secret, or include a raw Worker
transcript.

## Target

```text
.ai/memory/universe_nodes/
  functional/<node_id>/
  implementation/<node_id>/
```

The record is local Runtime state. Promotion to Universe Bench or Career is a
separate reviewed action with its own redaction and provenance.
