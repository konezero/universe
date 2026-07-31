"""Project-local Node Memory (Memory RAG surface) helpers.

MEMORY_SYNC is reference context only. It does not create Candidates, Task Frames,
Seed revisions, Bench observations, or Career promotions.
"""

from __future__ import annotations

import re
from typing import Any

MEMORY_SCHEMA = "universe.project-memory.v1"
MEMORY_STATES = frozenset({"BRAINSTORM", "OBSERVED", "QUESTION", "DECISION_NOTE"})
MEMORY_LINK_STATES = frozenset({"UNLINKED", "LINKED", "PROPOSED"})
MEMORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class MemoryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _text(value: Any, field: str, *, max_len: int = 4000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryError("MEMORY_FIELD_INVALID", f"{field} must be non-empty text")
    text = value.strip()
    if len(text) > max_len:
        raise MemoryError("MEMORY_FIELD_INVALID", f"{field} is too long")
    return text


def normalize_memory_create(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryError("MEMORY_REQUEST_INVALID", "memory body must be an object")
    state = _text(value.get("state", "BRAINSTORM"), "state", max_len=32).upper()
    if state not in MEMORY_STATES:
        raise MemoryError(
            "MEMORY_STATE_INVALID",
            "state must be BRAINSTORM, OBSERVED, QUESTION, or DECISION_NOTE",
        )
    body = _text(value.get("body"), "body", max_len=8000)
    title = _text(value.get("title", body[:80]), "title", max_len=160)
    node_ref = value.get("node_ref")
    graph = value.get("graph")
    link_state = "UNLINKED"
    normalized_node: str | None = None
    normalized_graph: str | None = None
    if node_ref is not None:
        normalized_node = _text(node_ref, "node_ref", max_len=160)
        if MEMORY_ID_PATTERN.fullmatch(normalized_node) is None:
            raise MemoryError("MEMORY_NODE_REF_INVALID", "node_ref is invalid")
        normalized_graph = _text(graph or "functional", "graph", max_len=32).lower()
        if normalized_graph not in {"functional", "implementation"}:
            raise MemoryError(
                "MEMORY_GRAPH_INVALID",
                "graph must be functional or implementation",
            )
        link_state = "LINKED"
    origin_ref = None
    if value.get("origin_ref") is not None:
        origin_ref = _text(value.get("origin_ref"), "origin_ref", max_len=320)
    return {
        "title": title,
        "body": body,
        "state": state,
        "link_state": link_state,
        "node_ref": normalized_node,
        "graph": normalized_graph,
        "origin_ref": origin_ref,
    }


def normalize_memory_link(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemoryError("MEMORY_REQUEST_INVALID", "link body must be an object")
    node_ref = _text(value.get("node_ref"), "node_ref", max_len=160)
    if MEMORY_ID_PATTERN.fullmatch(node_ref) is None:
        raise MemoryError("MEMORY_NODE_REF_INVALID", "node_ref is invalid")
    graph = _text(value.get("graph", "functional"), "graph", max_len=32).lower()
    if graph not in {"functional", "implementation"}:
        raise MemoryError(
            "MEMORY_GRAPH_INVALID",
            "graph must be functional or implementation",
        )
    link_state = _text(value.get("link_state", "LINKED"), "link_state", max_len=32).upper()
    if link_state not in {"LINKED", "PROPOSED"}:
        raise MemoryError(
            "MEMORY_LINK_STATE_INVALID",
            "link_state must be LINKED or PROPOSED",
        )
    return {"node_ref": node_ref, "graph": graph, "link_state": link_state}


def tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", text.lower())
        if token not in {"the", "and", "for", "with", "from", "this", "that"}
    }


def propose_node_links(
    *,
    memories: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Deterministic node proposals (no LLM). Nightly maintenance helper."""

    proposals: list[dict[str, Any]] = []
    prepared_nodes: list[tuple[dict[str, Any], set[str]]] = []
    for node in nodes:
        node_id = str(node.get("node_id") or node.get("id") or "")
        if not node_id:
            continue
        label = str(node.get("label") or node.get("title") or node_id)
        kind = str(node.get("kind") or "functional")
        graph = "implementation" if kind == "implementation" else "functional"
        bag = tokenize(f"{node_id} {label} {kind}")
        prepared_nodes.append(
            (
                {
                    "node_id": node_id,
                    "label": label,
                    "graph": graph,
                },
                bag,
            )
        )
    for memory in memories:
        if memory.get("link_state") != "UNLINKED":
            continue
        memory_tokens = tokenize(
            f"{memory.get('title', '')} {memory.get('body', '')}"
        )
        if not memory_tokens:
            continue
        scored: list[tuple[int, dict[str, Any]]] = []
        for node, bag in prepared_nodes:
            score = len(memory_tokens & bag)
            if score <= 0:
                continue
            scored.append((score, node))
        scored.sort(key=lambda item: (-item[0], item[1]["node_id"]))
        for score, node in scored[: max(1, min(limit, 5))]:
            proposals.append(
                {
                    "memory_id": memory["memory_id"],
                    "node_ref": node["node_id"],
                    "graph": node["graph"],
                    "score": score,
                    "reason": f"token_overlap:{score}",
                    "proposal_kind": "DETERMINISTIC_TOKEN_OVERLAP",
                    "effects": {
                        "seed_write": "NONE",
                        "candidate": "NONE",
                        "authority": "NONE",
                    },
                }
            )
    return proposals
