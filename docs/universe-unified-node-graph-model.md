# Universe Unified Node Graph Model (Phase 0 draft)

Status: DRAFT — not committed, not a contract yet. Supersedes the split
`functional-graph.v1` / `implementation-graph.v1` / `implementation-bindings.v1`
seed model once accepted.

Decision lineage:

- RAG `universe-unified-node-graph-rag-model` (2026-09-01) — one typed node
  graph, many Views; RAG = searchable knowledge connected to nodes.
- RAG `functional-node-ownership-todo-hierarchy` (2026-08-25) — a node exists
  before its work items; a TODO is owned by exactly one node.
- RAG `fleet-kanban-activity-projection` (2026-08-27) — Galaxy shows Feature
  Nodes and Expected Paths; kanban is a View of nodes plus their attached Work
  Items; lineage Feature Node → Expected Path → Goal → Milestone → Todo → Task
  Frame → Session Anchor → PTY → Activity; execution lanes Planned · Ready ·
  Executing · Verifying · Blocked · Done.
- RAG `rust-desktop-action-ir` (2026-08-27) — web / mobile / Rust desktop are
  projections of one Action Registry and the same runtime objects.
- RAG `universe-seed-projection-adopt-unified-node-graph` (2026-09-02) — rebuild
  seed + projection to this model before the dogfood loop; governance layer
  frozen (option 1).

## 1. Core idea

One graph. Typed nodes, typed edges. Every screen — the Galaxy map, the
functional / structural / flow maps, the Obsidian-style knowledge graph, the
kanban board — is a **View**: a projection over `(node.kind, edge.relation)`,
plus a zoom level, plus (for kanban / Galaxy) the Work Items and live execution
attached to each node.

**The display target is spatial.** Universe = planets gathered in space. A
planet is a node (a project / feature / capability world). Ships fly between
planets — a ship is a Task Frame or session in motion toward its target node.
Fly into a planet and you see its information: the local knowledge graph
(documents, decisions, memories and their links) and its work board.

**Nodes come from documents.** In the fresh-project flow the author writes the
skeleton documents (project brief, goal, feature map, architecture, design, …);
mapping a document to a node is that node's evidence. Automation (memory
collection, link proposals, work planning) runs *on top of* the nodes — it
never creates or redefines a structural node.

```text
author documents ─┐
                  ├─► structural nodes (typed, evidence-backed, positioned)
draw edges ───────┘        │
                           ├─► knowledge nodes connect in (DOCUMENT / DECISION / MEMORY)
                           ├─► Views: galaxy · functional · structural · flow · knowledge · kanban
                           ├─► Work Items attach (Goal / Milestone / TODO / Task Frame)
                           └─► live execution attaches as ships (Task Frame / session in motion)
```

## 2. Nodes (`universe.node-graph.v1`)

Two node groups in one graph.

### 2a. Structural nodes — the skeleton (planets)

| kind | meaning | replaces |
|---|---|---|
| `PRODUCT` | the product itself (root under the project) | — |
| `APP` | a distributable app: web, Rust desktop, CLI, mobile. One instance today (web); grows with the Rust desktop | — |
| `SURFACE` | a screen / panel inside an app (Galaxy, Fleet, Activity, Terminal, Inspector …) | — |
| `FEATURE` | a Feature Node — spec, scope, acceptance, Expected Paths anchor. May be `PROPOSED` | seed-v2 `FEATURE` |
| `CAPABILITY` | a settled functional capability the product has | `functional-graph` `capability` |
| `FLOW` | an end-to-end flow across capabilities | `functional-graph` `flow` |
| `EXTERNAL_BOUNDARY` | an external system / trust boundary (GitHub, provider CLIs, Career, remote Universes) | `functional-graph` `external-boundary` |
| `STRUCTURE` | a code grouping — `structure_role`: `package` \| `layer` | `implementation-graph` `PACKAGE` / `MODULE` |
| `COMPONENT` | a concrete implementation unit — `component_role`: `service` \| `adapter` \| `endpoint` \| `class` | `implementation-graph` `SERVICE` / `ADAPTER` / `ENDPOINT` / `CLASS` |

`FEATURE` vs `CAPABILITY`: a FEATURE is the intake/spec unit (has Expected
Paths, can be PROPOSED); a CAPABILITY is an established world. Adopting a
Feature's path can `REALIZES` one or more CAPABILITY nodes. (Open: whether to
collapse the two — deferred, both are cheap to keep.)

### 2b. Knowledge nodes — the Obsidian layer

| kind | source | notes |
|---|---|---|
| `DOCUMENT` | `documents.json` catalog | `role` ∈ SPECIFICATION / ARCHITECTURE / DESIGN / CONTRACT / POLICY / REFERENCE / CHANGELOG / EVIDENCE |
| `DECISION` | RAG `DECISION_NOTE` memories | 7 today |
| `MEMORY` | RAG `OBSERVED` / `BRAINSTORM` / `QUESTION` memories | 43 today |

Knowledge nodes are real graph nodes (Obsidian: a note is a node, a `[[link]]`
is an edge). They connect to structural nodes and to each other. They are shown
at the Surface zoom, not the Galaxy zoom.

### Node schema

```json
{
  "schema": "universe.node-graph.v1",
  "nodes": [
    {
      "node_id": "session-continuity",
      "kind": "CAPABILITY",
      "state": "ADOPTED",
      "title": "Keep AI sessions connected",
      "summary": "Observe, route, and safely present provider sessions.",
      "parent_ref": "universe",
      "layout": {"x": -120, "y": 40, "pinned": false},
      "refs": [
        {"kind": "document", "path": "docs/live-session-room-routing.md", "sha256": "..."},
        {"kind": "source",   "path": "tools/session_supervisor.py",       "sha256": "..."}
      ]
    }
  ],
  "edges": [
    {"edge_id": "session-enables-work", "from_node": "session-continuity",
     "to_node": "intent-to-work", "relation": "ENABLES",
     "contract_ref": {"kind": "document", "path": "docs/live-session-room-routing.md", "sha256": "..."}}
  ]
}
```

- `layout` — persisted position for the Galaxy / graph View. Optional; a
  deterministic force layout fills gaps. `pinned` locks a hand-placed node.
- Knowledge-node `refs` point at the doc / memory record; `layout` optional.

### node.state

- `ADOPTED` — a real node. Only adopted structural nodes accept Work Items.
- `PROPOSED` — a candidate node from a Feature Node's Expected Path. Adopting an
  Expected Path materialises its nodes as `ADOPTED`.

**PROPOSED nodes are never deleted or hidden when a sibling Expected Path is
adopted.** Showing the alternative future branches is the point — in the Galaxy
they are the routes not yet taken. Each PROPOSED node carries
`expected_path_ref`; the Feature Node records which path is `adopted`, and that
never demotes the other proposals. An adopted PROPOSED node keeps its
`expected_path_ref` for lineage.

### node.rollup (projection-computed, not authored)

Per structural node, for planet appearance and kanban:

```text
work_status      : aggregate of attached Work Items by lane (Planned … Done)
active_ships     : count of Task Frames / sessions currently in motion to this node
knowledge_count  : DOCUMENT + DECISION + MEMORY nodes connected
evidence_fresh   : all / some / no refs match current file digests
blocked          : any attached Work Item BLOCKED
```

### edge.relation

| relation | typical from → to |
|---|---|
| `CONTAINS` | PRODUCT→APP, APP→SURFACE, STRUCTURE→COMPONENT |
| `ENABLES` / `INFORMS` | CAPABILITY→CAPABILITY, CAPABILITY→FLOW |
| `REQUESTS` | FLOW→EXTERNAL_BOUNDARY |
| `DELIVERS` | EXTERNAL_BOUNDARY→CAPABILITY |
| `DEPENDS_ON` | COMPONENT→COMPONENT, STRUCTURE→STRUCTURE |
| `IMPLEMENTS` / `SUPPORTS` / `ADAPTS` / `EXPOSES` | COMPONENT/STRUCTURE → CAPABILITY/FEATURE (was `implementation-bindings`) |
| `REALIZES` | FEATURE → CAPABILITY (adopted path built) |
| `SPECIFIES` / `INFORMS` / `EVIDENCES` | DOCUMENT/DECISION/MEMORY → structural node (by document `role`) |
| `DERIVED_FROM` / `DUPLICATE_OF` / `MERGED_FROM` / `CONFLICTS_WITH` / `SUPERSEDES` | knowledge → knowledge (already in `universe-memory-rag`) |
| `REFERENCES` | DOCUMENT → DOCUMENT (markdown cross-links) |

## 3. Documents

`documents.json` (`universe.project-document-catalog.v1`) is unchanged in shape;
each entry becomes a `DOCUMENT` knowledge node, and each `node_ids[]` entry
becomes a `SPECIFIES` / `INFORMS` / `EVIDENCES` edge (by `role`).

```json
{"documents": [
  {"document_id": "session-routing", "path": "docs/live-session-room-routing.md",
   "title": "Live Session and Room Routing", "role": "ARCHITECTURE",
   "node_ids": ["session-continuity"], "project_wide": false, "sha256": "..."}
]}
```

Fresh-project living documents (seed-v2 `living_documents`) carry
`skeleton_sections`; filling a section and committing the doc is what
materialises / evidences the node.

## 4. Work Items and ships — attached, not nodes

A Work Item hangs off exactly one `ADOPTED` node via `owner_node_ref`.

```text
FEATURE node
  └─ Expected Path (revision-pinned SPECIFICATION)        [adopt]
       └─ Goal (owner_node_ref = a FEATURE or CAPABILITY)
            └─ Milestone
                 └─ TODO         (owner_node_ref = exactly one node)
                      └─ Task Frame  ─────────────┐
                           └─ Session Anchor → PTY │  = the ship in motion
```

A **ship** is the live projection of a Task Frame / session working toward its
target node. It is not stored in the graph — same rule as Work Items.

| Task Frame state | ship |
|---|---|
| `READY` / dispatched | departing |
| `ACTIVE` / Executing | in transit to target node |
| Verifying | entering orbit |
| `DONE` | landed |
| `BLOCKED` | stopped in space |

Work Items already exist in the service DB (`goals`, `milestones`, `todos`,
task frames). This model only requires each to carry a single `owner_node_ref`
and the projection to roll them up per node.

## 5. Views — a zoom hierarchy

`GET /v1/projects/{id}/projection` returns `views[]`. Each names a zoom level,
the node kinds and edge relations it includes, and its overlays.

| view | zoom | nodes | edges | overlay |
|---|---|---|---|---|
| `galaxy` | far | PRODUCT, APP, FEATURE, CAPABILITY (+ PROPOSED as un-taken routes) | CONTAINS, ENABLES, REALIZES, Expected-Path links | **ships** (live execution); planet appearance from `node.rollup` |
| `functional` | mid | PRODUCT, APP, SURFACE, FEATURE, CAPABILITY, FLOW, EXTERNAL_BOUNDARY | CONTAINS, ENABLES, INFORMS, REQUESTS, DELIVERS | — |
| `structural` | mid | STRUCTURE, COMPONENT | CONTAINS, DEPENDS_ON | IMPLEMENTS/SUPPORTS/ADAPTS/EXPOSES cross-edges to functional nodes |
| `flow` | mid | FLOW + edge endpoints | ENABLES, INFORMS, REQUESTS, DELIVERS | ordered |
| `knowledge` | near (per planet) | DOCUMENT, DECISION, MEMORY + the structural node(s) they connect to | SPECIFIES, INFORMS, EVIDENCES, DERIVED_FROM, DUPLICATE_OF, CONFLICTS_WITH, SUPERSEDES, REFERENCES | Obsidian local graph: n-hop from the selected node; global mode available |
| `kanban` | per planet / fleet | every ADOPTED structural node | — | Work Item rollup by lane; ships as in-flight cards |

Zoom path: **Galaxy** (planets + ships) → click a planet → **System / kanban**
(that node, its neighbours, its work board) → **Surface / knowledge** (that
node's Obsidian local graph).

The `knowledge` global graph can be large (memories in the thousands). The
projection caps / paginates it; the UI uses local-graph + filters, as Obsidian
does.

Existing `build_projection` outputs are kept and extended for the new kinds:
`missing_connections` (`NODE_DISCONNECTED`, `DOCUMENT_UNMAPPED`,
`CONTRACT_REFERENCE_MISSING`) and `predicted_paths` (`CONNECT_NODE`,
`MAP_DOCUMENT_TO_NODE`, `DOCUMENT_CONNECTION_CONTRACT`).

## 6. Migration

### Seed assets (`.ai/<project>/`)

| before | after |
|---|---|
| `functional-graph.json` (`universe.functional-graph.v1`) | `node-graph.json` (`universe.node-graph.v1`) — structural nodes + edges |
| `implementation-graph.json` (`universe.implementation-graph.v1`) | folded in as STRUCTURE / COMPONENT nodes (with `*_role`) |
| `bindings.json` (`universe.implementation-bindings.v1`) | folded in as IMPLEMENTS/SUPPORTS/ADAPTS/EXPOSES edges |
| `documents.json` | unchanged; entries become DOCUMENT nodes at projection time |
| `manifest.json` | new `seed_id`, asset list = `node-graph` + `documents` |

DECISION / MEMORY nodes are **not** authored in the seed — the projection pulls
them from the RAG store and grafts them onto the graph by their `node_ref`.

### Code

| file | change | status |
|---|---|---|
| `tools/universe_node_graph.py` | **new, pure, no I/O.** `unify_seed_graph(seed)` merges the split functional / implementation / bindings shape into one `universe.node-graph.v1` graph; `document_graph(documents, node_ids)` turns the catalogue into DOCUMENT nodes + edges; `compute_views(nodes, edges)` projects the six Views. `tests/test_universe_node_graph.py` (21 cases, incl. the real `.ai/universe/*.json`). | **landed 2026-09-02** |
| `tools/project_seed_assets.py` | `load_project_seed_assets` reads `node-graph.json` → one `nodes` / `edges` list. Compat shim up-converts an old 3-file seed (GCS) in memory on read, so callers only ever see the unified shape. | todo |
| `tools/universe_server.py` `build_projection` (~:5009 `material`) | call `unify_seed_graph` + `compute_views`; add `unified_graph` + `views` to `material`. Additive; keeps `missing_connections` / `predicted_paths`. NOTE: `material` is hashed into `projection_digest`, so adding fields makes the next `sync` retire GCS's current projection and store a new one (non-destructive, versioned). | todo |
| `tools/universe_server.py` `build_projection` (~:5018) | today reads `seed.get("implementation", {"nodes": []})` but `normalize_project_seed` / `load_project_seed_assets` emit the key `implementation_nodes` — so implementation nodes are **currently dropped from every projection**. `unify_seed_graph` reads `implementation_nodes`; fixing the old read is part of the wiring. | todo |
| `tools/universe_server.py` `normalize_project_seed` (~:3904) | `_exact_object_fields` strictly rejects unknown keys. Node required = `{node_id, kind, title, refs}`, optional `{summary}`. To author `state` / `parent_ref` / `layout` / `expected_path_ref` in the seed, add them to the optional set; edges likewise (`relation` alongside `kind`). | todo |
| knowledge grafting | DECISION / MEMORY nodes come from the RAG store and change over time, so they must be grafted at projection **read** time (`get_project_projection` / the route), not baked into the stored `projection_json`. | todo |
| projection schema `universe.project-projection.v1` | add `unified_graph`, `views`, `node_rollup`, `ships`. | todo |
| Work Item stores | ensure `owner_node_ref` on goal / milestone / todo / task frame; default to the `universe` root node during migration. | todo |

### Data

- 50 existing `universe` memories are all `LINKED` to the synthetic `universe`
  node. After the real graph lands, re-link them to concrete nodes via a
  `propose-links` / `MAP_DOCUMENT_TO_NODE` pass.

## 7. Sequence

1. ✅ Accept this draft. Open: FEATURE↔CAPABILITY merge (both kept for now).
2. ✅ Pure logic landed — `tools/universe_node_graph.py` (`unify_seed_graph`,
   `document_graph`, `compute_views`) + `tests/test_universe_node_graph.py`.
2b. ✅ Wired into `build_projection` — additive `unified_graph` + `views` on the
   projection, defensive (`null` / `[]` on a shape that will not unify). GCS
   projection keeps working; its non-standard kinds (DOMAIN / RUNTIME) land in
   no View, SERVICE maps to COMPONENT.
3. ✅ Refreshed `.ai/universe/*.json` digests (kept the split format for now —
   `unify_seed_graph` consumes it fine) and `POST /v1/projects/universe/sync`.
   `universe` now has a live Project Seed + Projection: `unified_graph` = 25
   nodes (2 CAPABILITY, 2 FLOW, 1 EXTERNAL_BOUNDARY, 1 STRUCTURE, 11 COMPONENT,
   8 DOCUMENT), 27 edges, all six `views`. `missing_connections: 0`.
   Fix `3658dd8`: `unify_seed_graph` reads impl nodes from either
   `implementation_nodes` or the nested `implementation.nodes` shape.
4. ⏳ Read-time graft of DECISION / MEMORY nodes from the RAG store in
   `get_project_projection`, and recompute the `knowledge` view.
5. ⏳ Richer `.ai/universe/` seed — add PRODUCT / APP / SURFACE / FEATURE nodes
   so the `galaxy` view (2 nodes today) is meaningful. Optionally the unified
   on-disk `node-graph.json` loader + `project_seed_assets.py` GCS shim.
6. ⏳ UI: Galaxy render consuming `projection.views` (planets + ships), then
   drill-in to knowledge + kanban.
7. ⏳ Re-link the 51 memories (all on the synthetic root `universe`, which is
   not a graph node) to real nodes; `propose-links` only proposes for UNLINKED.
8. ⏳ Fresh dogfood pass: author docs → nodes → Feature Node → Goal → TODO →
   automation, watched on the Galaxy.

Known gaps in the current Views: `galaxy` is thin (old seed has no
PRODUCT/APP/FEATURE nodes); `structural` drops the IMPLEMENTS/SUPPORTS
cross-edges (`compute_views` needs both endpoints selected — should pull the
functional endpoint as an overlay). Steps 5–7 and any `normalize_project_seed`
optional-field change are a supervised pass.
