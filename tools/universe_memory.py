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
MEMORY_SCORERS = frozenset({"DETERMINISTIC", "HEURISTIC", "LLM", "AUTO"})


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


def normalize_memory_maintain(value: Any) -> dict[str, Any]:
    """Normalize memory maintenance batch request (deterministic/heuristic/LLM)."""

    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise MemoryError("MEMORY_REQUEST_INVALID", "maintain body must be an object")
    apply_proposals = bool(value.get("apply_proposals", False))
    limit = value.get("limit", 20)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise MemoryError("MEMORY_FIELD_INVALID", "limit must be an integer from 1 through 100")
    per_memory = value.get("per_memory", 1)
    if (
        isinstance(per_memory, bool)
        or not isinstance(per_memory, int)
        or not 1 <= per_memory <= 5
    ):
        raise MemoryError(
            "MEMORY_FIELD_INVALID",
            "per_memory must be an integer from 1 through 5",
        )
    min_score = value.get("min_score", 1)
    if (
        isinstance(min_score, bool)
        or not isinstance(min_score, int)
        or not 1 <= min_score <= 50
    ):
        raise MemoryError(
            "MEMORY_FIELD_INVALID",
            "min_score must be an integer from 1 through 50",
        )
    scorer = str(value.get("scorer", "DETERMINISTIC")).strip().upper()
    if scorer not in MEMORY_SCORERS:
        raise MemoryError(
            "MEMORY_SCORER_INVALID",
            "scorer must be DETERMINISTIC, HEURISTIC, LLM, or AUTO",
        )
    llm_proposals = value.get("llm_proposals")
    if llm_proposals is None:
        normalized_llm: list[dict[str, Any]] | None = None
    elif not isinstance(llm_proposals, list):
        raise MemoryError(
            "MEMORY_FIELD_INVALID",
            "llm_proposals must be an array when provided",
        )
    else:
        if len(llm_proposals) > 200:
            raise MemoryError(
                "MEMORY_FIELD_INVALID",
                "llm_proposals may contain at most 200 items",
            )
        normalized_llm = []
        for index, item in enumerate(llm_proposals):
            if not isinstance(item, dict):
                raise MemoryError(
                    "MEMORY_FIELD_INVALID",
                    f"llm_proposals[{index}] must be an object",
                )
            memory_id = _text(item.get("memory_id"), f"llm_proposals[{index}].memory_id", max_len=160)
            node_ref = _text(item.get("node_ref"), f"llm_proposals[{index}].node_ref", max_len=160)
            if MEMORY_ID_PATTERN.fullmatch(node_ref) is None:
                raise MemoryError("MEMORY_NODE_REF_INVALID", "llm proposal node_ref is invalid")
            graph = _text(
                item.get("graph", "functional"),
                f"llm_proposals[{index}].graph",
                max_len=32,
            ).lower()
            if graph not in {"functional", "implementation"}:
                raise MemoryError(
                    "MEMORY_GRAPH_INVALID",
                    "llm proposal graph must be functional or implementation",
                )
            score = item.get("score", 1)
            if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 100:
                raise MemoryError(
                    "MEMORY_FIELD_INVALID",
                    f"llm_proposals[{index}].score must be an integer from 1 through 100",
                )
            reason = str(item.get("reason") or "llm_batch").strip()[:200] or "llm_batch"
            normalized_llm.append(
                {
                    "memory_id": memory_id,
                    "node_ref": node_ref,
                    "graph": graph,
                    "score": score,
                    "reason": reason,
                    "proposal_kind": "LLM_BATCH",
                    "effects": {
                        "seed_write": "NONE",
                        "candidate": "NONE",
                        "authority": "NONE",
                    },
                }
            )
    return {
        "apply_proposals": apply_proposals,
        "limit": limit,
        "per_memory": per_memory,
        "min_score": min_score,
        "scorer": scorer,
        "llm_proposals": normalized_llm,
    }


def select_best_proposals(
    proposals: list[dict[str, Any]],
    *,
    per_memory: int = 1,
    min_score: int = 1,
) -> list[dict[str, Any]]:
    """Pick top proposals per memory for maintenance apply."""

    by_memory: dict[str, list[dict[str, Any]]] = {}
    for proposal in proposals:
        score = int(proposal.get("score") or 0)
        if score < min_score:
            continue
        memory_id = str(proposal.get("memory_id") or "")
        if not memory_id:
            continue
        by_memory.setdefault(memory_id, []).append(proposal)
    selected: list[dict[str, Any]] = []
    for memory_id in sorted(by_memory):
        items = by_memory[memory_id]
        items.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                str(item.get("node_ref") or ""),
            )
        )
        selected.extend(items[:per_memory])
    return selected


def tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", text.lower())
        if token not in {"the", "and", "for", "with", "from", "this", "that"}
    }


def _prepared_nodes(nodes: list[dict[str, Any]]) -> list[tuple[dict[str, Any], set[str]]]:
    prepared: list[tuple[dict[str, Any], set[str]]] = []
    for node in nodes:
        node_id = str(node.get("node_id") or node.get("id") or "")
        if not node_id:
            continue
        label = str(node.get("label") or node.get("title") or node_id)
        kind = str(node.get("kind") or "functional")
        graph = "implementation" if kind == "implementation" else "functional"
        bag = tokenize(f"{node_id} {label} {kind}")
        prepared.append(
            (
                {
                    "node_id": node_id,
                    "label": label,
                    "graph": graph,
                    "kind": kind,
                },
                bag,
            )
        )
    return prepared


def propose_node_links(
    *,
    memories: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Deterministic token-overlap proposals (no LLM)."""

    proposals: list[dict[str, Any]] = []
    prepared_nodes = _prepared_nodes(nodes)
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


def propose_node_links_heuristic(
    *,
    memories: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Weighted heuristic scorer (offline nightly stand-in when LLM is unavailable).

    Scoring:
      exact node_id token in memory text: +4
      label token overlap: +2 each
      kind token overlap: +1 each
    """

    proposals: list[dict[str, Any]] = []
    prepared_nodes = _prepared_nodes(nodes)
    for memory in memories:
        if memory.get("link_state") != "UNLINKED":
            continue
        memory_text = f"{memory.get('title', '')} {memory.get('body', '')}".lower()
        memory_tokens = tokenize(memory_text)
        if not memory_tokens:
            continue
        scored: list[tuple[int, dict[str, Any], str]] = []
        for node, bag in prepared_nodes:
            node_id = node["node_id"]
            label_tokens = tokenize(node["label"])
            kind_tokens = tokenize(node.get("kind") or "")
            score = 0
            reasons: list[str] = []
            id_tokens = tokenize(node_id.replace("-", " ").replace("_", " ")) | {node_id.lower()}
            id_hits = memory_tokens & id_tokens
            if id_hits or node_id.lower() in memory_text:
                score += 4
                reasons.append("id_match")
            label_hits = memory_tokens & label_tokens
            if label_hits:
                score += 2 * len(label_hits)
                reasons.append(f"label:{len(label_hits)}")
            kind_hits = memory_tokens & kind_tokens
            if kind_hits:
                score += len(kind_hits)
                reasons.append(f"kind:{len(kind_hits)}")
            # weak bag overlap fallback
            bag_hits = memory_tokens & bag
            if bag_hits and score == 0:
                score += len(bag_hits)
                reasons.append(f"bag:{len(bag_hits)}")
            if score <= 0:
                continue
            scored.append((score, node, ",".join(reasons) if reasons else "heuristic"))
        scored.sort(key=lambda item: (-item[0], item[1]["node_id"]))
        for score, node, reason in scored[: max(1, min(limit, 5))]:
            proposals.append(
                {
                    "memory_id": memory["memory_id"],
                    "node_ref": node["node_id"],
                    "graph": node["graph"],
                    "score": score,
                    "reason": f"heuristic:{reason}",
                    "proposal_kind": "HEURISTIC_WEIGHTED",
                    "effects": {
                        "seed_write": "NONE",
                        "candidate": "NONE",
                        "authority": "NONE",
                    },
                }
            )
    return proposals


def filter_llm_proposals(
    *,
    llm_proposals: list[dict[str, Any]],
    memories: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only LLM proposals that target known UNLINKED memories and known nodes."""

    unlinked = {
        str(item.get("memory_id"))
        for item in memories
        if item.get("link_state") == "UNLINKED" and item.get("memory_id")
    }
    node_map = {
        str(node.get("node_id") or node.get("id") or ""): node for node in nodes
    }
    accepted: list[dict[str, Any]] = []
    for proposal in llm_proposals:
        memory_id = str(proposal.get("memory_id") or "")
        node_ref = str(proposal.get("node_ref") or "")
        if memory_id not in unlinked:
            continue
        if node_ref not in node_map:
            continue
        accepted.append(proposal)
    return accepted


def merge_proposals(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge proposal lists preferring higher score for the same memory+node."""

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for proposal in primary + secondary:
        key = (str(proposal.get("memory_id")), str(proposal.get("node_ref")))
        current = best.get(key)
        if current is None or int(proposal.get("score") or 0) > int(
            current.get("score") or 0
        ):
            best[key] = proposal
    return sorted(
        best.values(),
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("memory_id") or ""),
            str(item.get("node_ref") or ""),
        ),
    )
