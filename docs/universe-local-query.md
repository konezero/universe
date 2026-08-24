# Universe Local Query

Status: project-owned index
Scope: local Universe query surface for general CLIs
Not: MCP protocol wrapper, Universe-owned index, polling freshness loop

## Rule

A general CLI does not walk the repository or read `session.md` as current
Mode. Standalone and Universe-attached Hosts both query
`project_runtime.sqlite3` for the Registry snapshot and Mode Current Anchor.
Universe HTTP is only for search and Memory/Bench.

```text
requested Mode Current Anchor
  -> session SQL bound to that Anchor
  -> project hook updates project_file_index.sqlite3
  -> Universe opens that project index read-only
  -> Universe Memory/Bench retrieval
```

`session.md` and `current_anchor_frame.md` are companion refs only.

## Surfaces

```text
POST /v1/projects/{project_id}/file-index/search
POST /v1/projects/{project_id}/retrieval-context
GET  /v1/projects/{project_id}/file-index
```

Search requests must name `mode` and `anchor_id`. Those values must match
`project_runtime.sqlite3` for that project root. The former HTTP sync route is
retained only as a fail-closed compatibility surface and returns
`PROJECT_INDEX_HOOK_REQUIRED`; Universe does not write the project index.

## Index

Each project owns this database:

```text
<project_root>/.ai/runtime/state/project_file_index.sqlite3
```

`universe_project_index_hook.py` is the writer. A normalized file-change hook
passes `changed_paths` and only those rows are fingerprinted, inserted,
updated, or removed. An omitted `changed_paths` performs the initial full
bootstrap. `.git`, dependency caches, and Runtime tmp/session/task-frame
stores are skipped.

The database seals its schema, canonical project root, and project ID in
`project_index_identity`. Universe verifies that identity and opens it with
SQLite `mode=ro` plus `query_only=ON`. File rows, graph candidates, and sync
state are not created in or written to the central `universe.sqlite3`.

Search is mechanical: path and excerpt substring, then token overlap. It is
not semantic RAG. Memory/Bench remain the existing retrieval projection.

## CLI

Common Skills:
`.ai/skills/common/companion-state-ref.md`,
`.ai/skills/common/mode-current-anchor-query.md`,
`.ai/skills/common/universe-local-endpoint.md`, and
`.ai/skills/common/universe-local-query.md`.

Helpers: `query_mode_current_anchor.py`, `resolve_universe_endpoint.py`,
`universe_project_index_hook.py`, and `universe_local_query.py`.
