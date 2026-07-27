# Project Universe Seed Template

This template is materialized by a Project Master under `.ai/universe/` after
read-only Project discovery and explicit Project-side write authorization.

```text
.ai/universe/
  manifest.json
  functional-graph.json
  implementation-graph.json
  bindings.json
  documents.json
```

The five files are one canonical Project Seed revision:

- `functional-graph.json` records capabilities, flows, and external boundaries.
- `implementation-graph.json` records packages, modules, classes, services,
  adapters, and endpoints.
- `bindings.json` records many-to-many links from functional nodes to
  implementation nodes.
- `documents.json` records Project-wide and node-linked canonical references.
- `manifest.json` pins the source coordinate and digest of every asset.

Universe reads and verifies this bundle. It does not create it in a Project.
Legacy README and `docs/` files are discovery inputs only; after publication,
the `.ai/universe/` bundle is the canonical Universe-facing context.
