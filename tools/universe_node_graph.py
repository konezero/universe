"""Pure unified-node-graph helpers.

See ``docs/universe-unified-node-graph-model.md``.

This module has no I/O and no service dependency. It turns a loaded or
normalized Project Seed (the split ``functional`` / ``implementation`` /
``bindings`` shape) into one typed node graph, turns a document catalogue into
knowledge nodes and edges, and computes the display Views.

Wiring this into ``build_projection`` and an on-disk ``universe.node-graph.v1``
seed asset is a later step; keeping the logic here means it is testable in
isolation first.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

NODE_GRAPH_SCHEMA = "universe.node-graph.v1"

STRUCTURAL_KINDS: frozenset[str] = frozenset(
    {
        "PRODUCT",
        "APP",
        "SURFACE",
        "FEATURE",
        "CAPABILITY",
        "FLOW",
        "EXTERNAL_BOUNDARY",
        "STRUCTURE",
        "COMPONENT",
    }
)

KNOWLEDGE_KINDS: frozenset[str] = frozenset({"DOCUMENT", "DECISION", "MEMORY"})

NODE_STATES: frozenset[str] = frozenset({"ADOPTED", "PROPOSED"})

# Legacy functional-graph node kind -> unified kind. Legacy assets use lower or
# kebab case; normalized seeds upper-case and keep the hyphen.
_FUNCTIONAL_KIND_MAP: dict[str, str] = {
    "CAPABILITY": "CAPABILITY",
    "FLOW": "FLOW",
    "EXTERNAL-BOUNDARY": "EXTERNAL_BOUNDARY",
    "EXTERNAL_BOUNDARY": "EXTERNAL_BOUNDARY",
    "FEATURE": "FEATURE",
    "PRODUCT": "PRODUCT",
    "APP": "APP",
    "SURFACE": "SURFACE",
}

# Legacy implementation-graph node kind -> (unified kind, role value).
_IMPLEMENTATION_KIND_MAP: dict[str, tuple[str, str]] = {
    "PACKAGE": ("STRUCTURE", "package"),
    "MODULE": ("STRUCTURE", "module"),
    "LAYER": ("STRUCTURE", "layer"),
    "CLASS": ("COMPONENT", "class"),
    "SERVICE": ("COMPONENT", "service"),
    "ADAPTER": ("COMPONENT", "adapter"),
    "ENDPOINT": ("COMPONENT", "endpoint"),
}

_BINDING_RELATIONS: frozenset[str] = frozenset(
    {"IMPLEMENTS", "SUPPORTS", "ADAPTS", "EXPOSES"}
)

# document role -> the relation a DOCUMENT knowledge node draws to its structural
# node. SPECIFICATION nails down a node; EVIDENCE proves it; everything else
# informs it.
_DOCUMENT_ROLE_RELATION: dict[str, str] = {
    "SPECIFICATION": "SPECIFIES",
    "EVIDENCE": "EVIDENCES",
}

_KNOWLEDGE_RELATIONS: frozenset[str] = frozenset(
    {
        "SPECIFIES",
        "INFORMS",
        "EVIDENCES",
        "DERIVED_FROM",
        "DUPLICATE_OF",
        "MERGED_FROM",
        "CONFLICTS_WITH",
        "SUPERSEDES",
        "REFERENCES",
    }
)


class NodeGraphError(ValueError):
    """Raised for a structurally invalid seed graph."""


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _kind(value: Any) -> str:
    return _text(value).upper().replace(" ", "_")


def _map_functional_kind(raw: Any) -> tuple[str, str | None]:
    """Map a functional-graph node kind to (unified kind, optional role).

    A seed may already be flat (GCS records ``SERVICE`` as a node kind rather
    than in a separate implementation graph), so an implementation kind seen
    here still maps to COMPONENT / STRUCTURE. An unrecognised kind passes
    through unchanged and simply lands in no View.
    """

    kind = _kind(raw)
    if kind in _FUNCTIONAL_KIND_MAP:
        return (_FUNCTIONAL_KIND_MAP[kind], None)
    if kind in _IMPLEMENTATION_KIND_MAP:
        return _IMPLEMENTATION_KIND_MAP[kind]
    return (kind or "CAPABILITY", None)


def _map_implementation_kind(raw: Any) -> tuple[str, str | None]:
    kind = _kind(raw)
    mapped = _IMPLEMENTATION_KIND_MAP.get(kind)
    if mapped is not None:
        return mapped
    # Unknown implementation kind: treat as a COMPONENT, keep the raw as role.
    return ("COMPONENT", kind.lower() or None)


def _seq(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        raise NodeGraphError("expected a list")
    if isinstance(value, Sequence):
        return list(value)
    if isinstance(value, Iterable):
        return list(value)
    raise NodeGraphError("expected a list")


def unify_seed_graph(seed: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a split seed graph into one ``universe.node-graph.v1`` graph.

    Accepts either the raw ``load_project_seed_assets`` shape or the normalized
    ``normalize_project_seed`` shape. Returns ``{"schema", "nodes", "edges"}``
    with structural nodes only (knowledge nodes are grafted separately).
    """

    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def _add(node: dict[str, Any]) -> None:
        node_id = node["node_id"]
        if node_id in node_ids:
            raise NodeGraphError(f"duplicate node id after unification: {node_id}")
        node_ids.add(node_id)
        nodes.append(node)

    for index, raw in enumerate(_seq(seed.get("nodes"))):
        if not isinstance(raw, Mapping):
            raise NodeGraphError(f"nodes[{index}] is not an object")
        node_id = _text(raw.get("node_id"))
        if not node_id:
            raise NodeGraphError(f"nodes[{index}] has no node_id")
        kind, role = _map_functional_kind(raw.get("kind"))
        node: dict[str, Any] = {
            "node_id": node_id,
            "kind": kind,
            "state": _node_state(raw.get("state")),
            "title": _text(raw.get("title")) or node_id,
            "refs": list(raw.get("refs") or []),
        }
        if role:
            node["structure_role" if kind == "STRUCTURE" else "component_role"] = role
        if _text(raw.get("summary")):
            node["summary"] = _text(raw.get("summary"))
        if isinstance(raw.get("layout"), Mapping):
            node["layout"] = dict(raw["layout"])
        if _text(raw.get("parent_ref")):
            node["parent_ref"] = _text(raw.get("parent_ref"))
        if _text(raw.get("expected_path_ref")):
            node["expected_path_ref"] = _text(raw.get("expected_path_ref"))
        _add(node)

    for index, raw in enumerate(_implementation_nodes(seed)):
        if not isinstance(raw, Mapping):
            raise NodeGraphError(f"implementation_nodes[{index}] is not an object")
        node_id = _text(raw.get("implementation_id")) or _text(raw.get("node_id"))
        if not node_id:
            raise NodeGraphError(
                f"implementation_nodes[{index}] has no implementation_id"
            )
        kind, role = _map_implementation_kind(raw.get("kind"))
        node = {
            "node_id": node_id,
            "kind": kind,
            "state": _node_state(raw.get("state")),
            "title": _text(raw.get("title")) or node_id,
            "refs": list(raw.get("refs") or []),
        }
        if role:
            node["structure_role" if kind == "STRUCTURE" else "component_role"] = role
        if _text(raw.get("summary")):
            node["summary"] = _text(raw.get("summary"))
        _add(node)

    edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()

    def _add_edge(edge: dict[str, Any]) -> None:
        edge_id = edge["edge_id"]
        if edge_id in edge_ids:
            raise NodeGraphError(f"duplicate edge id after unification: {edge_id}")
        if edge["from_node"] not in node_ids or edge["to_node"] not in node_ids:
            raise NodeGraphError(
                f"edge {edge_id} references an unknown node "
                f"({edge['from_node']} -> {edge['to_node']})"
            )
        edge_ids.add(edge_id)
        edges.append(edge)

    for index, raw in enumerate(_seq(seed.get("edges"))):
        if not isinstance(raw, Mapping):
            raise NodeGraphError(f"edges[{index}] is not an object")
        from_node = _text(raw.get("from_node"))
        to_node = _text(raw.get("to_node"))
        relation = _kind(raw.get("relation") or raw.get("kind"))
        edge_id = _text(raw.get("edge_id")) or f"{from_node}-{relation}-{to_node}".lower()
        edge = {
            "edge_id": edge_id,
            "from_node": from_node,
            "to_node": to_node,
            "relation": relation or "RELATES_TO",
        }
        if isinstance(raw.get("contract_ref"), Mapping):
            edge["contract_ref"] = dict(raw["contract_ref"])
        if _text(raw.get("summary")):
            edge["summary"] = _text(raw.get("summary"))
        _add_edge(edge)

    for index, raw in enumerate(_seq(seed.get("implementation_bindings"))):
        if not isinstance(raw, Mapping):
            raise NodeGraphError(
                f"implementation_bindings[{index}] is not an object"
            )
        functional_id = _text(raw.get("functional_node_id"))
        implementation_id = _text(raw.get("implementation_node_id"))
        relation = _kind(raw.get("relation")) or "IMPLEMENTS"
        if relation not in _BINDING_RELATIONS:
            relation = "IMPLEMENTS"
        edge_id = _text(raw.get("binding_id")) or (
            f"{implementation_id}-{relation}-{functional_id}".lower()
        )
        edge = {
            "edge_id": edge_id,
            "from_node": implementation_id,
            "to_node": functional_id,
            "relation": relation,
        }
        if _text(raw.get("summary")):
            edge["summary"] = _text(raw.get("summary"))
        _add_edge(edge)

    return {"schema": NODE_GRAPH_SCHEMA, "nodes": nodes, "edges": edges}


def _node_state(value: Any) -> str:
    state = _text(value).upper()
    return state if state in NODE_STATES else "ADOPTED"


def _implementation_nodes(seed: Mapping[str, Any]) -> list[Any]:
    """The implementation node list, however the seed carries it.

    ``load_project_seed_assets`` returns ``implementation_nodes`` (a list); the
    stored / normalized seed carries ``implementation`` (``{"nodes": [...]}``).
    """

    direct = seed.get("implementation_nodes")
    if direct is not None:
        return _seq(direct)
    nested = seed.get("implementation")
    if isinstance(nested, Mapping):
        return _seq(nested.get("nodes"))
    return []


def document_graph(
    documents: Sequence[Mapping[str, Any]],
    structural_node_ids: Iterable[str],
) -> dict[str, Any]:
    """Turn a document catalogue into DOCUMENT knowledge nodes and edges.

    Each catalogue entry becomes one ``DOCUMENT`` node; each of its ``node_ids``
    that names a known structural node becomes one edge (relation by role).
    """

    known = set(structural_node_ids)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(documents or []):
        if not isinstance(raw, Mapping):
            raise NodeGraphError(f"documents[{index}] is not an object")
        document_id = _text(raw.get("document_id"))
        if not document_id:
            raise NodeGraphError(f"documents[{index}] has no document_id")
        node_id = f"doc:{document_id}"
        if node_id in seen:
            raise NodeGraphError(f"duplicate document id: {document_id}")
        seen.add(node_id)
        role = _kind(raw.get("role")) or "REFERENCE"
        node: dict[str, Any] = {
            "node_id": node_id,
            "kind": "DOCUMENT",
            "state": "ADOPTED",
            "title": _text(raw.get("title")) or document_id,
            "role": role,
            "refs": [
                {
                    "kind": "document",
                    "path": _text(raw.get("path")),
                    "sha256": _text(raw.get("sha256")),
                }
            ],
        }
        if raw.get("project_wide"):
            node["project_wide"] = True
        nodes.append(node)
        relation = _DOCUMENT_ROLE_RELATION.get(role, "INFORMS")
        for target in raw.get("node_ids") or []:
            target_id = _text(target)
            if target_id and target_id in known:
                edges.append(
                    {
                        "edge_id": f"{node_id}-{relation}-{target_id}".lower(),
                        "from_node": node_id,
                        "to_node": target_id,
                        "relation": relation,
                    }
                )
    return {"nodes": nodes, "edges": edges}


# View definitions: node kinds and edge relations each View keeps.
_VIEW_SPECS: dict[str, dict[str, Any]] = {
    "galaxy": {
        "zoom": "far",
        "kinds": {"PRODUCT", "APP", "SURFACE", "FEATURE", "CAPABILITY"},
        "relations": {"CONTAINS", "ENABLES", "REALIZES", "INFORMS"},
        "include_proposed": True,
    },
    "functional": {
        "zoom": "mid",
        "kinds": {
            "PRODUCT",
            "APP",
            "SURFACE",
            "FEATURE",
            "CAPABILITY",
            "FLOW",
            "EXTERNAL_BOUNDARY",
        },
        "relations": {"CONTAINS", "ENABLES", "INFORMS", "REQUESTS", "DELIVERS"},
        "include_proposed": True,
    },
    "structural": {
        "zoom": "mid",
        "kinds": {"STRUCTURE", "COMPONENT"},
        "relations": {
            "CONTAINS",
            "DEPENDS_ON",
            "IMPLEMENTS",
            "SUPPORTS",
            "ADAPTS",
            "EXPOSES",
        },
        "include_proposed": False,
    },
    "flow": {
        "zoom": "mid",
        "kinds": {"FLOW"},
        "relations": {"ENABLES", "INFORMS", "REQUESTS", "DELIVERS"},
        "include_proposed": True,
        "include_edge_endpoints": True,
    },
    "knowledge": {
        "zoom": "near",
        "kinds": KNOWLEDGE_KINDS,
        "relations": _KNOWLEDGE_RELATIONS,
        "include_proposed": True,
        "include_edge_endpoints": True,
    },
    "kanban": {
        "zoom": "board",
        "kinds": STRUCTURAL_KINDS,
        "relations": set(),
        "include_proposed": False,
    },
}

VIEW_NAMES: tuple[str, ...] = tuple(_VIEW_SPECS)


def graft_knowledge_nodes(
    projection: Mapping[str, Any],
    memories: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Add DECISION / MEMORY nodes from the RAG store to a stored projection.

    Knowledge memories change over time, so they are grafted at projection read
    time, not baked into ``projection_json``. Returns a shallow copy with an
    enriched ``unified_graph``, recomputed ``views``, and a ``knowledge_grafted``
    summary. A memory whose ``node_ref`` names a real graph node gets an
    ``INFORMS`` edge; the rest float (until re-linked).
    """

    result = dict(projection)
    unified = projection.get("unified_graph")
    if not isinstance(unified, Mapping):
        result["knowledge_grafted"] = {"decisions": 0, "memories": 0, "linked": 0}
        return result

    base_nodes = list(unified.get("nodes") or [])
    base_edges = list(unified.get("edges") or [])
    node_ids = {n.get("node_id") for n in base_nodes}

    add_nodes: list[dict[str, Any]] = []
    add_edges: list[dict[str, Any]] = []
    decisions = memory_count = linked = 0
    for raw in memories or []:
        memory_id = _text(raw.get("memory_id"))
        if not memory_id:
            continue
        node_id = f"mem:{memory_id}"
        if node_id in node_ids:
            continue
        node_ids.add(node_id)
        is_decision = _text(raw.get("state")).upper() == "DECISION_NOTE"
        if is_decision:
            decisions += 1
        else:
            memory_count += 1
        add_nodes.append(
            {
                "node_id": node_id,
                "kind": "DECISION" if is_decision else "MEMORY",
                "state": "ADOPTED",
                "title": _text(raw.get("title"))
                or _text(raw.get("body"))[:60]
                or memory_id,
                "refs": [
                    {
                        "kind": "memory",
                        "path": _text(raw.get("origin_ref")),
                        "sha256": "",
                    }
                ],
            }
        )
        target = _text(raw.get("node_ref"))
        if target and target in node_ids and target != node_id:
            linked += 1
            add_edges.append(
                {
                    "edge_id": f"{node_id}-informs-{target}",
                    "from_node": node_id,
                    "to_node": target,
                    "relation": "INFORMS",
                }
            )

    new_nodes = base_nodes + add_nodes
    new_edges = base_edges + add_edges
    result["unified_graph"] = {
        "schema": NODE_GRAPH_SCHEMA,
        "nodes": new_nodes,
        "edges": new_edges,
    }
    result["views"] = compute_views(new_nodes, new_edges)
    result["knowledge_grafted"] = {
        "decisions": decisions,
        "memories": memory_count,
        "linked": linked,
    }
    return result


def compute_views(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project a unified graph into the display Views.

    Each result is ``{"view", "zoom", "node_ids", "edge_ids"}``. ``nodes`` and
    ``edges`` may include structural and knowledge nodes together.
    """

    by_id = {n["node_id"]: n for n in nodes}
    result: list[dict[str, Any]] = []
    for name, spec in _VIEW_SPECS.items():
        kinds: set[str] = spec["kinds"]
        relations: set[str] = spec["relations"]
        include_proposed: bool = spec["include_proposed"]

        selected: set[str] = set()
        for node in nodes:
            if node.get("kind") not in kinds:
                continue
            if not include_proposed and node.get("state") == "PROPOSED":
                continue
            selected.add(node["node_id"])

        endpoint_pull: bool = bool(spec.get("include_edge_endpoints"))
        edge_ids: list[str] = []
        if relations:
            for edge in edges:
                if edge.get("relation") not in relations:
                    continue
                a = edge.get("from_node")
                b = edge.get("to_node")
                if a not in by_id or b not in by_id:
                    continue
                if endpoint_pull:
                    touches_view = (
                        a in selected
                        or b in selected
                        or by_id[a].get("kind") in kinds
                        or by_id[b].get("kind") in kinds
                    )
                    if touches_view:
                        selected.add(a)
                        selected.add(b)
                        edge_ids.append(edge["edge_id"])
                elif a in selected and b in selected:
                    edge_ids.append(edge["edge_id"])

        result.append(
            {
                "view": name,
                "zoom": spec["zoom"],
                "node_ids": sorted(selected),
                "edge_ids": sorted(edge_ids),
            }
        )
    return result
