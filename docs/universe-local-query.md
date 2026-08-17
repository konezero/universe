# Universe Local Query

Status: first slice
Scope: local Universe query surface for general CLIs
Not: MCP protocol wrapper, CLI-side index, automatic Skill binding

## Rule

A general CLI does not walk the repository or read `session.md` as current
Mode. Standalone and Universe-attached Hosts both query
`project_runtime.sqlite3` for the Registry snapshot and Mode Current Anchor.
Universe HTTP is only for search and Memory/Bench.

```text
requested Mode Current Anchor
  -> session SQL bound to that Anchor
  -> Universe incremental file index
  -> Universe Memory/Bench retrieval
```

`session.md` and `current_anchor_frame.md` are companion refs only.

## Surfaces

```text
POST /v1/projects/{project_id}/file-index/sync
POST /v1/projects/{project_id}/file-index/search
POST /v1/projects/{project_id}/retrieval-context
GET  /v1/projects/{project_id}/file-index
```

Every mutating or search request must name `mode` and `anchor_id`. Those
values must match `project_runtime.sqlite3` for that project root.

## Index

Universe incrementally indexes text files under the attached `project_root`.
`.git`, dependency caches, and Runtime tmp/session/task-frame stores are
skipped. Unchanged `size` plus `mtime` rows are left in place.

Search is mechanical: path and excerpt substring, then token overlap. It is
not semantic RAG. Memory/Bench remain the existing retrieval projection.

## CLI

Common Skills:
`.ai/skills/common/companion-state-ref.md`,
`.ai/skills/common/mode-current-anchor-query.md`,
`.ai/skills/common/universe-local-endpoint.md`, and
`.ai/skills/common/universe-local-query.md`.

Helpers: `query_mode_current_anchor.py`, `resolve_universe_endpoint.py`,
and `universe_local_query.py`. They do not create a second index.
