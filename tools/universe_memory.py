"""Project-local Node Memory (Memory RAG surface) helpers.

MEMORY_SYNC is reference context only. It does not create Candidates, Task Frames,
Seed revisions, Bench observations, or Career promotions.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

MEMORY_SCHEMA = "universe.project-memory.v1"
MEMORY_RAG_BATCH_SCHEMA = "universe.project-memory-rag-batch.v1"
MEMORY_STATES = frozenset({"BRAINSTORM", "OBSERVED", "QUESTION", "DECISION_NOTE"})
MEMORY_LINK_STATES = frozenset({"UNLINKED", "LINKED", "PROPOSED"})
MEMORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MEMORY_SCORERS = frozenset({"DETERMINISTIC", "HEURISTIC", "LLM", "AUTO"})

# The batch/candidate contract deliberately lives beside the existing Memory
# RAG helpers.  It gives the service one redaction boundary for every source
# (provider activity, room summaries, and future observer adapters).
MEMORY_BATCH_CONFIG_SCHEMA = "universe.memory-batch-config.v1"
MEMORY_BATCH_RUN_SCHEMA = "universe.memory-batch-run.v1"
MEMORY_CANDIDATE_SCHEMA = "universe.memory-candidate.v1"
MEMORY_CANDIDATE_RELATION_SCHEMA = "universe.memory-candidate-relation.v1"
MEMORY_BATCH_STAGES = frozenset(
    {"FAST_EXTRACT", "CONSOLIDATE", "SYNTHESIZE", "INDEPENDENT_CHECK"}
)
MEMORY_CANDIDATE_KINDS = frozenset({"MEMORY", "IDEA", "HYPOTHESIS", "PRODUCT"})
MEMORY_CANDIDATE_STATES = frozenset(
    {
        "REVIEW_REQUIRED",
        "KEEP",
        "IGNORE",
        "EXPLORE",
        "START_PRODUCT_DESIGN",
        "SUPERSEDED",
        "CONFLICTED",
    }
)
MEMORY_CANDIDATE_DECISIONS = frozenset(
    {"IGNORE", "KEEP", "EXPLORE", "START_PRODUCT_DESIGN"}
)
MEMORY_CANDIDATE_RELATIONS = frozenset(
    {
        "DERIVED_FROM",
        "DUPLICATE_OF",
        "MERGED_FROM",
        "CONFLICTS_WITH",
        "SUPERSEDES",
        "MERGED_INTO",
    }
)
MEMORY_BATCH_PROVIDERS = frozenset({"AUTO", "GROK", "CODEX", "CLAUDE"})
MEMORY_BATCH_EFFORTS = frozenset({"AUTO", "LOW", "MEDIUM", "HIGH", "MAX"})
MEMORY_BATCH_SCHEDULES = frozenset({"MANUAL", "HOURLY", "DAILY", "WEEKLY"})
MEMORY_BATCH_FALLBACKS = frozenset(
    {"NONE", "DISABLE", "DETERMINISTIC", "AUTO", "GROK", "CODEX", "CLAUDE"}
)
MEMORY_CANDIDATE_FORBIDDEN_FIELDS = frozenset(
    {
        "body",
        "command",
        "commands",
        "content",
        "prompt",
        "prompts",
        "raw_prompt",
        "raw_prompts",
        "raw_source",
        "raw_command",
        "raw_commands",
        "source_text",
        "source_body",
        "tool_args",
        "tool_command",
        "tool_commands",
        "transcript",
        "transcripts",
    }
)


class MemoryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _redacted_memory_index(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only stable identifiers/state for provenance hashing."""

    return sorted(
        [
            {
                "memory_id": str(item.get("memory_id") or ""),
                "link_state": str(item.get("link_state") or "UNKNOWN").upper(),
            }
            for item in memories
            if item.get("memory_id")
        ],
        key=lambda item: item["memory_id"],
    )


def _redacted_node_index(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only stable node coordinates; labels and source text are omitted."""

    return sorted(
        [
            {
                "node_id": str(item.get("node_id") or item.get("id") or ""),
                "kind": str(item.get("kind") or "functional"),
            }
            for item in nodes
            if item.get("node_id") or item.get("id")
        ],
        key=lambda item: item["node_id"],
    )


def run_nightly_memory_rag_batch(
    *,
    project_id: str,
    memory_loader: Callable[[], list[dict[str, Any]]],
    node_loader: Callable[[], list[dict[str, Any]]],
    proposal_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    scorer: str = "AUTO",
    limit: int = 20,
    per_memory: int = 1,
    min_score: int = 1,
    source_ref: str = "service://universe/memory-rag/nightly",
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Run a redacted, service-callable nightly Memory RAG proposal batch.

    The loaders are deliberately callback-based so a service can supply its
    current project projections without this module acquiring a database or a
    provider client. Only deterministic/heuristic local scoring is performed;
    no prompt, source body, command, or raw loader payload is returned or sent
    to ``proposal_sink``.
    """

    if not isinstance(project_id, str) or not project_id.strip():
        raise MemoryError("MEMORY_RAG_PROJECT_INVALID", "project_id is required")
    if not callable(memory_loader) or not callable(node_loader):
        raise MemoryError(
            "MEMORY_RAG_LOADER_INVALID",
            "memory_loader and node_loader must be callable",
        )
    if proposal_sink is not None and not callable(proposal_sink):
        raise MemoryError("MEMORY_RAG_SINK_INVALID", "proposal_sink must be callable")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise MemoryError("MEMORY_RAG_LIMIT_INVALID", "limit must be between 1 and 100")
    if (
        isinstance(per_memory, bool)
        or not isinstance(per_memory, int)
        or not 1 <= per_memory <= 5
    ):
        raise MemoryError(
            "MEMORY_RAG_PER_MEMORY_INVALID",
            "per_memory must be between 1 and 5",
        )
    if (
        isinstance(min_score, bool)
        or not isinstance(min_score, int)
        or not 1 <= min_score <= 50
    ):
        raise MemoryError(
            "MEMORY_RAG_MIN_SCORE_INVALID",
            "min_score must be between 1 and 50",
        )
    normalized_scorer = str(scorer or "AUTO").strip().upper()
    if normalized_scorer not in {"AUTO", "DETERMINISTIC", "HEURISTIC"}:
        raise MemoryError(
            "MEMORY_RAG_SCORER_INVALID",
            "nightly scorer must be AUTO, DETERMINISTIC, or HEURISTIC",
        )
    memories = memory_loader()
    nodes = node_loader()
    if not isinstance(memories, list) or not all(isinstance(item, dict) for item in memories):
        raise MemoryError("MEMORY_RAG_MEMORY_PAYLOAD_INVALID", "memory_loader must return objects")
    if not isinstance(nodes, list) or not all(isinstance(item, dict) for item in nodes):
        raise MemoryError("MEMORY_RAG_NODE_PAYLOAD_INVALID", "node_loader must return objects")
    memory_index = _redacted_memory_index(memories)
    node_index = _redacted_node_index(nodes)
    memory_set_digest = _digest(memory_index)
    node_set_digest = _digest(node_index)
    effective_scorer = (
        "HEURISTIC_WEIGHTED"
        if normalized_scorer in {"AUTO", "HEURISTIC"}
        else "DETERMINISTIC_TOKEN_OVERLAP"
    )
    if effective_scorer == "HEURISTIC_WEIGHTED":
        proposals = propose_node_links_heuristic(
            memories=memories,
            nodes=nodes,
            limit=limit,
        )
    else:
        proposals = propose_node_links(
            memories=memories,
            nodes=nodes,
            limit=limit,
        )
    selected = select_best_proposals(
        proposals,
        per_memory=per_memory,
        min_score=min_score,
    )
    selected = selected[:limit]
    safe_observed_at = observed_at or _utc_now()
    if not isinstance(safe_observed_at, str) or not safe_observed_at.strip():
        raise MemoryError("MEMORY_RAG_OBSERVED_AT_INVALID", "observed_at must be text")
    source_digest = _digest(str(source_ref))
    proposal_digest = _digest(selected)
    batch_key = {
        "project_id": project_id.strip(),
        "memory_set_digest": memory_set_digest,
        "node_set_digest": node_set_digest,
        "proposal_digest": proposal_digest,
        "scorer": effective_scorer,
        "observed_at": safe_observed_at,
    }
    batch_id = "memory_rag_batch_" + _digest(batch_key)[:24]
    record: dict[str, Any] = {
        "schema": MEMORY_RAG_BATCH_SCHEMA,
        "status": "MEMORY_RAG_PROPOSAL_BATCH_READY",
        "batch_id": batch_id,
        "project_ref": f"project://{project_id.strip()}",
        "observed_at": safe_observed_at,
        "scorer": effective_scorer,
        "proposal_count": len(selected),
        "proposals": selected,
        "provenance": {
            "source_digest": source_digest,
            "memory_set_digest": memory_set_digest,
            "node_set_digest": node_set_digest,
            "proposal_digest": proposal_digest,
            "raw_inputs": "NOT_RETAINED",
            "raw_prompts": "NOT_RETAINED",
            "raw_source": "NOT_RETAINED",
            "raw_commands": "NOT_RETAINED",
        },
        "effects": {
            "memory_write": "NONE",
            "seed_write": "NONE",
            "candidate_write": "NONE",
            "authority": "NONE",
        },
    }
    if proposal_sink is not None:
        try:
            sink_result = proposal_sink(record)
        except Exception as error:  # preserve service boundary and redaction
            raise MemoryError(
                "MEMORY_RAG_SINK_FAILED",
                f"proposal sink failed: {type(error).__name__}",
            ) from error
        record["sink"] = {
            "status": "ACCEPTED",
            "result_digest": _digest(sink_result),
        }
    else:
        record["sink"] = {"status": "NOT_CONFIGURED"}
    record["record_digest"] = _digest(
        {key: value for key, value in record.items() if key != "record_digest"}
    )
    return record


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


def _memory_batch_text(value: Any, field: str, *, max_len: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryError("MEMORY_BATCH_FIELD_INVALID", f"{field} must be non-empty text")
    result = value.strip()
    if len(result) > max_len:
        raise MemoryError("MEMORY_BATCH_FIELD_INVALID", f"{field} is too long")
    return result


def _memory_batch_optional_int(
    value: Any,
    field: str,
    *,
    maximum: int = 10_000_000,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemoryError(
            "MEMORY_BATCH_FIELD_INVALID",
            f"{field} must be a non-negative integer",
        )
    if value > maximum:
        raise MemoryError("MEMORY_BATCH_FIELD_INVALID", f"{field} is too large")
    return value


def normalize_memory_batch_schedule(value: Any) -> dict[str, Any]:
    if value is None:
        return {"kind": "MANUAL", "interval_minutes": 0}
    if isinstance(value, str):
        kind = value.strip().upper()
        if kind not in MEMORY_BATCH_SCHEDULES:
            raise MemoryError(
                "MEMORY_BATCH_SCHEDULE_INVALID",
                "schedule must be MANUAL, HOURLY, DAILY, or WEEKLY",
            )
        return {
            "kind": kind,
            "interval_minutes": {
                "MANUAL": 0,
                "HOURLY": 60,
                "DAILY": 1440,
                "WEEKLY": 10080,
            }[kind],
        }
    if not isinstance(value, Mapping):
        raise MemoryError(
            "MEMORY_BATCH_SCHEDULE_INVALID", "schedule must be text or an object"
        )
    unknown = set(value) - {"kind", "interval_minutes"}
    if unknown:
        raise MemoryError(
            "MEMORY_BATCH_SCHEDULE_INVALID",
            "schedule contains unsupported fields",
        )
    kind = str(value.get("kind", "MANUAL")).strip().upper()
    if kind not in MEMORY_BATCH_SCHEDULES:
        raise MemoryError(
            "MEMORY_BATCH_SCHEDULE_INVALID",
            "schedule.kind must be MANUAL, HOURLY, DAILY, or WEEKLY",
        )
    interval = value.get("interval_minutes")
    if interval is None:
        interval = {
            "MANUAL": 0,
            "HOURLY": 60,
            "DAILY": 1440,
            "WEEKLY": 10080,
        }[kind]
    interval = _memory_batch_optional_int(
        interval, "schedule.interval_minutes", maximum=10080
    )
    if interval is None or (kind == "MANUAL" and interval != 0) or (
        kind != "MANUAL" and interval == 0
    ):
        raise MemoryError(
            "MEMORY_BATCH_SCHEDULE_INVALID",
            "schedule.interval_minutes does not match schedule.kind",
        )
    return {"kind": kind, "interval_minutes": interval}


def normalize_memory_batch_budget(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise MemoryError(
                "MEMORY_BATCH_BUDGET_INVALID", "quota_or_budget must be non-negative"
            )
        return {"max_runs": value}
    if not isinstance(value, Mapping):
        raise MemoryError(
            "MEMORY_BATCH_BUDGET_INVALID",
            "quota_or_budget must be an object or integer",
        )
    allowed = {"max_runs", "max_tokens", "max_cost_units", "window_hours"}
    if set(value) - allowed:
        raise MemoryError(
            "MEMORY_BATCH_BUDGET_INVALID",
            "quota_or_budget contains unsupported fields",
        )
    result: dict[str, int] = {}
    for field in sorted(allowed):
        normalized = _memory_batch_optional_int(
            value.get(field), f"quota_or_budget.{field}"
        )
        if normalized is not None:
            result[field] = normalized
    return result or None


def normalize_memory_batch_config(
    value: Any,
    *,
    stage: str | None = None,
    worker_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one stage config without consulting a provider.

    Provider/model validation is intentionally a separate operation so callers
    can display a candidate configuration before resolving it against the
    current host catalog. Missing provider/model/effort values inherit the
    existing Worker Binding shape when one is supplied.
    """

    if not isinstance(value, Mapping):
        raise MemoryError(
            "MEMORY_BATCH_CONFIG_INVALID", "memory batch config must be an object"
        )
    allowed = {
        "stage",
        "provider",
        "model",
        "model_ref",
        "effort",
        "schedule",
        "quota",
        "budget",
        "quota_or_budget",
        "fallback",
        "enabled",
        "dry_run",
    }
    if set(value) - allowed:
        raise MemoryError(
            "MEMORY_BATCH_CONFIG_INVALID",
            "memory batch config contains unsupported fields",
        )
    normalized_stage = str(value.get("stage", stage or "")).strip().upper()
    if normalized_stage not in MEMORY_BATCH_STAGES:
        raise MemoryError(
            "MEMORY_BATCH_STAGE_INVALID",
            "stage must be FAST_EXTRACT, CONSOLIDATE, SYNTHESIZE, or INDEPENDENT_CHECK",
        )
    binding = worker_binding if isinstance(worker_binding, Mapping) else {}
    provider = str(value.get("provider", binding.get("provider", "AUTO"))).strip().upper()
    if provider not in MEMORY_BATCH_PROVIDERS:
        raise MemoryError(
            "MEMORY_BATCH_PROVIDER_INVALID",
            "provider must be AUTO, GROK, CODEX, or CLAUDE",
        )
    raw_model = value.get("model_ref", value.get("model", binding.get("model_ref", "")))
    model_ref = str(raw_model or "").strip()
    if len(model_ref) > 256 or (model_ref and not re.fullmatch(MEMORY_ID_PATTERN.pattern, model_ref)):
        raise MemoryError(
            "MEMORY_BATCH_MODEL_INVALID",
            "model_ref is not a valid provider model reference",
        )
    effort = str(value.get("effort", binding.get("effort", "AUTO"))).strip().upper()
    if effort not in MEMORY_BATCH_EFFORTS:
        raise MemoryError(
            "MEMORY_BATCH_EFFORT_INVALID",
            "effort must be AUTO, LOW, MEDIUM, HIGH, or MAX",
        )
    raw_budget = value.get("quota_or_budget")
    if raw_budget is None:
        raw_budget = value.get("quota", value.get("budget"))
    fallback = str(value.get("fallback", "DETERMINISTIC")).strip().upper()
    if fallback not in MEMORY_BATCH_FALLBACKS:
        raise MemoryError(
            "MEMORY_BATCH_FALLBACK_INVALID",
            "fallback is not a supported provider or deterministic fallback",
        )
    enabled = value.get("enabled", True)
    dry_run = value.get("dry_run", False)
    if not isinstance(enabled, bool):
        raise MemoryError("MEMORY_BATCH_ENABLED_INVALID", "enabled must be boolean")
    if not isinstance(dry_run, bool):
        raise MemoryError("MEMORY_BATCH_DRY_RUN_INVALID", "dry_run must be boolean")
    return {
        "schema": MEMORY_BATCH_CONFIG_SCHEMA,
        "stage": normalized_stage,
        "provider": provider,
        "model_ref": model_ref,
        "effort": effort,
        "schedule": normalize_memory_batch_schedule(value.get("schedule")),
        "quota_or_budget": normalize_memory_batch_budget(raw_budget),
        "fallback": fallback,
        "enabled": enabled,
        "dry_run": dry_run,
    }


def resolve_memory_batch_config(
    config: Mapping[str, Any],
    catalog: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve a normalized config against an observed provider model catalog.

    AUTO is allowed to select only a catalog entry currently marked
    AVAILABLE. Explicit providers may be configured while unavailable, but
    their model must still exist in the catalog; execution can then fail
    closed at the provider boundary instead of silently switching models.
    """

    if not isinstance(config, Mapping):
        raise MemoryError("MEMORY_BATCH_CONFIG_INVALID", "config must be an object")
    if not isinstance(catalog, Mapping) or not isinstance(catalog.get("providers"), Mapping):
        raise MemoryError(
            "MEMORY_BATCH_CATALOG_UNAVAILABLE",
            "provider model catalog is unavailable",
        )
    providers = catalog["providers"]
    entries: dict[str, Mapping[str, Any]] = {}
    for provider in ("GROK", "CODEX", "CLAUDE"):
        entry = providers.get(provider)
        if isinstance(entry, Mapping):
            entries[provider] = entry
    requested_provider = str(config.get("provider") or "AUTO").upper()
    requested_model = str(config.get("model_ref") or config.get("model") or "").strip()

    def models_for(provider: str) -> list[str]:
        raw = entries[provider].get("models")
        if not isinstance(raw, list):
            raise MemoryError(
                "MEMORY_BATCH_CATALOG_INVALID",
                f"catalog models for {provider} must be an array",
            )
        models = [str(item).strip() for item in raw if isinstance(item, str) and item.strip()]
        if not models:
            raise MemoryError(
                "MEMORY_BATCH_MODEL_UNAVAILABLE",
                f"catalog has no models for {provider}",
            )
        return list(dict.fromkeys(models))

    selected_provider: str | None = None
    if requested_provider == "AUTO":
        if requested_model:
            matches = [
                provider for provider in entries if requested_model in models_for(provider)
            ]
            if len(matches) != 1:
                raise MemoryError(
                    "MEMORY_BATCH_MODEL_AMBIGUOUS"
                    if len(matches) > 1
                    else "MEMORY_BATCH_MODEL_NOT_FOUND",
                    "AUTO model must resolve to exactly one catalog provider",
                )
            selected_provider = matches[0]
        else:
            for provider in ("GROK", "CODEX", "CLAUDE"):
                if provider in entries and str(entries[provider].get("status")).upper() == "AVAILABLE":
                    selected_provider = provider
                    break
            if selected_provider is None:
                raise MemoryError(
                    "MEMORY_BATCH_PROVIDER_UNAVAILABLE",
                    "AUTO has no currently available provider in the model catalog",
                )
    elif requested_provider in entries:
        selected_provider = requested_provider
    else:
        raise MemoryError(
            "MEMORY_BATCH_PROVIDER_NOT_FOUND",
            "configured provider is missing from the model catalog",
        )

    assert selected_provider is not None
    models = models_for(selected_provider)
    resolved_model = requested_model
    if not resolved_model:
        resolved_model = str(entries[selected_provider].get("default") or "").strip()
    if not resolved_model or resolved_model not in models:
        raise MemoryError(
            "MEMORY_BATCH_MODEL_NOT_FOUND",
            f"configured model is not present for {selected_provider}",
        )
    status = str(entries[selected_provider].get("status") or "UNKNOWN").upper()
    catalog_digest = _digest(
        {
            "updated_at": catalog.get("updated_at"),
            "discovered_at": catalog.get("discovered_at"),
            "providers": providers,
        }
    )
    result = dict(config)
    result["resolution"] = {
        "status": "AVAILABLE" if status == "AVAILABLE" else "UNAVAILABLE",
        "requested_provider": requested_provider,
        "requested_model_ref": requested_model,
        "resolved_provider": selected_provider,
        "resolved_model_ref": resolved_model,
        "catalog_digest": catalog_digest,
    }
    return result


def _candidate_input_has_forbidden_field(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).strip().lower()
            if (
                name in MEMORY_CANDIDATE_FORBIDDEN_FIELDS
                or name.startswith("raw_")
                or "transcript" in name
            ):
                return name
            nested = _candidate_input_has_forbidden_field(child)
            if nested:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _candidate_input_has_forbidden_field(child)
            if nested:
                return nested
    return None


def _candidate_ref_digest(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("sha256:"):
            text = text[7:]
        if re.fullmatch(r"[0-9a-f]{64}", text):
            return text
    return _digest(value)


def _candidate_source_session_digest(value: Any) -> str:
    if value is None:
        return _digest("UNKNOWN_SESSION")
    if isinstance(value, Mapping):
        value = {
            key: value.get(key)
            for key in ("provider", "provider_session_id", "source_id", "session_id")
            if value.get(key) is not None
        }
    return _candidate_ref_digest(value)


def _normalize_candidate_relations(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MemoryError(
            "MEMORY_CANDIDATE_RELATIONS_INVALID", "relations must be an array"
        )
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise MemoryError(
                "MEMORY_CANDIDATE_RELATIONS_INVALID",
                f"relations[{index}] must be an object",
            )
        relation = str(item.get("relation") or "").strip().upper()
        target = str(
            item.get("candidate_id") or item.get("target_candidate_id") or ""
        ).strip()
        if relation not in MEMORY_CANDIDATE_RELATIONS or not target:
            raise MemoryError(
                "MEMORY_CANDIDATE_RELATIONS_INVALID",
                "relation and target candidate_id are required",
            )
        if MEMORY_ID_PATTERN.fullmatch(target) is None:
            raise MemoryError(
                "MEMORY_CANDIDATE_RELATIONS_INVALID",
                f"relations[{index}] target candidate_id is invalid",
            )
        record = {"relation": relation, "candidate_id": target}
        if record not in result:
            result.append(record)
    return result


def normalize_memory_candidate(
    value: Any,
    *,
    project_id: str | None = None,
    stage: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic, redacted Memory/Idea/Hypothesis/Product candidate."""

    if not isinstance(value, Mapping):
        raise MemoryError(
            "MEMORY_CANDIDATE_INVALID", "candidate must be an object"
        )
    forbidden = _candidate_input_has_forbidden_field(value)
    if forbidden:
        raise MemoryError(
            "MEMORY_CANDIDATE_RAW_INPUT_FORBIDDEN",
            f"candidate field {forbidden} is not accepted; submit digests and summaries",
        )
    normalized_project = str(value.get("project_id") or project_id or "").strip()
    if not normalized_project:
        raise MemoryError("MEMORY_CANDIDATE_PROJECT_INVALID", "project_id is required")
    normalized_stage = str(value.get("stage") or stage or "").strip().upper()
    if normalized_stage not in MEMORY_BATCH_STAGES:
        raise MemoryError(
            "MEMORY_CANDIDATE_STAGE_INVALID", "candidate stage is invalid"
        )
    normalized_kind = str(value.get("kind") or kind or "MEMORY").strip().upper()
    if normalized_kind not in MEMORY_CANDIDATE_KINDS:
        raise MemoryError(
            "MEMORY_CANDIDATE_KIND_INVALID",
            "kind must be MEMORY, IDEA, HYPOTHESIS, or PRODUCT",
        )
    summary = _memory_batch_text(value.get("summary"), "summary", max_len=1600)
    state = str(value.get("state") or "REVIEW_REQUIRED").strip().upper()
    if state == "PROPOSED":
        state = "REVIEW_REQUIRED"
    if state not in MEMORY_CANDIDATE_STATES:
        raise MemoryError(
            "MEMORY_CANDIDATE_STATE_INVALID", "candidate state is invalid"
        )

    raw_provenance = value.get("provenance")
    if raw_provenance is not None and not isinstance(raw_provenance, Mapping):
        raise MemoryError(
            "MEMORY_CANDIDATE_PROVENANCE_INVALID",
            "provenance must be an object",
        )
    raw_provenance = raw_provenance if isinstance(raw_provenance, Mapping) else {}

    raw_range = value.get("source_range")
    if raw_range is None:
        raw_range = raw_provenance.get("source_range")
    if raw_range is None:
        raw_range = value.get("range")
    if raw_range is None:
        raw_range = {}
    if not isinstance(raw_range, Mapping):
        raise MemoryError(
            "MEMORY_CANDIDATE_SOURCE_RANGE_INVALID",
            "source_range must be an object",
        )
    start = raw_range.get("start", raw_range.get("start_ordinal"))
    end = raw_range.get("end", raw_range.get("end_ordinal"))
    start = _memory_batch_optional_int(start, "source_range.start", maximum=2**63 - 1)
    end = _memory_batch_optional_int(end, "source_range.end", maximum=2**63 - 1)
    if start is not None and end is not None and start > end:
        raise MemoryError(
            "MEMORY_CANDIDATE_SOURCE_RANGE_INVALID",
            "source_range.start cannot exceed source_range.end",
        )

    raw_refs = value.get(
        "ref_digests",
        value.get("source_refs", raw_provenance.get("ref_digests", [])),
    )
    if raw_refs is None:
        raw_refs = []
    if not isinstance(raw_refs, list):
        raise MemoryError(
            "MEMORY_CANDIDATE_REFS_INVALID", "ref_digests must be an array"
        )
    ref_digests: list[str] = []
    for item in raw_refs:
        if isinstance(item, Mapping):
            item = item.get("activity_digest", item.get("ref_digest", item.get("activity_id")))
        if item is None:
            continue
        digest = _candidate_ref_digest(item)
        if digest not in ref_digests:
            ref_digests.append(digest)
    ref_digests.sort()
    source_range = {
        "start": start,
        "end": end,
        "range_digest": _digest({"start": start, "end": end, "ref_digests": ref_digests}),
    }
    source_session = _candidate_source_session_digest(
        value.get(
            "source_session",
            value.get(
                "source_session_ref",
                value.get("source_session_id", raw_provenance.get("source_session")),
            ),
        )
    )
    relations = _normalize_candidate_relations(value.get("relations"))
    relevance = value.get("relevance")
    if relevance is None:
        relevance = {}
    if not isinstance(relevance, Mapping):
        raise MemoryError(
            "MEMORY_CANDIDATE_RELEVANCE_INVALID", "relevance must be an object"
        )
    repetition_count = relevance.get("repetition_count", 1)
    if (
        isinstance(repetition_count, bool)
        or not isinstance(repetition_count, int)
        or repetition_count < 1
        or repetition_count > 1_000_000
    ):
        raise MemoryError(
            "MEMORY_CANDIDATE_RELEVANCE_INVALID",
            "relevance.repetition_count must be a positive integer",
        )
    candidate_id = str(value.get("candidate_id") or "").strip()
    if candidate_id and MEMORY_ID_PATTERN.fullmatch(candidate_id) is None:
        raise MemoryError(
            "MEMORY_CANDIDATE_ID_INVALID", "candidate_id is invalid"
        )
    provenance = {
        "source_session": source_session,
        "source_range": source_range,
        "ref_digests": ref_digests,
        "redaction": "SUMMARY_AND_DIGESTS_ONLY",
    }
    material = {
        "project_id": normalized_project,
        "stage": normalized_stage,
        "kind": normalized_kind,
        "summary": summary,
        "provenance": provenance,
        "relations": relations,
        "relevance": {"repetition_count": repetition_count},
    }
    candidate_digest = _digest(material)
    if not candidate_id:
        candidate_id = "memory_candidate_" + candidate_digest[:24]
    return {
        "schema": MEMORY_CANDIDATE_SCHEMA,
        "candidate_id": candidate_id,
        "project_id": normalized_project,
        "stage": normalized_stage,
        "kind": normalized_kind,
        "state": state,
        "summary": summary,
        "provenance": provenance,
        "relations": relations,
        "relevance": {"repetition_count": repetition_count},
        "candidate_digest": candidate_digest,
        "authority": "NONE",
        "effects": {
            "current_anchor": "NONE",
            "project_facts": "NONE",
            "seed": "NONE",
            "source": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
            "auto_adoption": False,
        },
        "next_operation": "CANDIDATE_REVIEW",
    }


def extract_memory_candidates_from_activity_batch(
    value: Any,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    """Build deterministic Memory candidates from reduced activity references only."""

    if not isinstance(value, Mapping):
        raise MemoryError(
            "MEMORY_CANDIDATE_ACTIVITY_INVALID", "activity batch must be an object"
        )
    refs = value.get("activity_refs")
    if not isinstance(refs, list):
        raise MemoryError(
            "MEMORY_CANDIDATE_ACTIVITY_INVALID", "activity_refs must be an array"
        )
    source = value.get("source")
    source = source if isinstance(source, Mapping) else {}
    source_session = {
        "provider": source.get("provider"),
        "provider_session_id": source.get("provider_session_id"),
        "source_id": source.get("source_id"),
    }
    candidates: list[dict[str, Any]] = []
    allowed_events = {"TURN_COMPLETED", "ERROR", "QUOTA_STOP", "APPROVAL_WAIT"}
    for item in refs:
        if not isinstance(item, Mapping):
            raise MemoryError(
                "MEMORY_CANDIDATE_ACTIVITY_INVALID",
                "activity_refs entries must be objects",
            )
        event_kind = str(item.get("event_kind") or "").strip().upper()
        if event_kind not in allowed_events:
            continue
        ordinal = item.get("ordinal")
        ordinal = _memory_batch_optional_int(ordinal, "activity_ref.ordinal", maximum=2**63 - 1)
        if ordinal is None:
            raise MemoryError(
                "MEMORY_CANDIDATE_ACTIVITY_INVALID", "activity_ref.ordinal is required"
            )
        activity_state = str(item.get("activity_state") or "UNKNOWN").strip().upper()
        readable = event_kind.replace("_", " ").lower()
        candidate = normalize_memory_candidate(
            {
                "project_id": project_id,
                "stage": "FAST_EXTRACT",
                "kind": "MEMORY",
                "summary": f"Observed {readable} boundary ({activity_state}).",
                "source_session": source_session,
                "source_range": {"start": ordinal, "end": ordinal},
                "ref_digests": [
                    item.get("activity_digest") or item.get("activity_id") or item
                ],
                "relevance": {"repetition_count": 1},
            }
        )
        candidates.append(candidate)
    return sorted(candidates, key=lambda item: (item["provenance"]["source_range"]["start"], item["candidate_id"]))


def consolidate_memory_candidates(
    candidates: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministically dedupe, merge, conflict, and supersede candidates."""

    normalized_by_id: dict[str, dict[str, Any]] = {}
    for item in candidates:
        candidate = normalize_memory_candidate(item)
        existing = normalized_by_id.get(candidate["candidate_id"])
        if existing is None:
            normalized_by_id[candidate["candidate_id"]] = candidate
            continue
        existing["relevance"]["repetition_count"] = min(
            1_000_000,
            int(existing["relevance"]["repetition_count"])
            + int(candidate["relevance"]["repetition_count"]),
        )
    normalized = list(normalized_by_id.values())
    if not normalized:
        return []
    project_ids = {item["project_id"] for item in normalized}
    if len(project_ids) != 1:
        raise MemoryError(
            "MEMORY_CANDIDATE_PROJECT_MISMATCH",
            "consolidation input must contain one project",
        )
    by_id = {item["candidate_id"]: item for item in normalized}
    forced_state: dict[str, str] = {}
    forced_relations: dict[str, list[dict[str, str]]] = {}
    for item in normalized:
        for relation in item["relations"]:
            target = relation["candidate_id"]
            if target not in by_id:
                continue
            kind = relation["relation"]
            if kind == "CONFLICTS_WITH":
                forced_state[item["candidate_id"]] = "CONFLICTED"
                forced_state[target] = "CONFLICTED"
                forced_relations.setdefault(target, []).append(
                    {"relation": "CONFLICTS_WITH", "candidate_id": item["candidate_id"]}
                )
            elif kind in {"SUPERSEDES", "MERGED_INTO"}:
                forced_state[target if kind == "SUPERSEDES" else item["candidate_id"]] = "SUPERSEDED"

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in sorted(normalized, key=lambda candidate: candidate["candidate_id"]):
        if item["candidate_id"] in forced_state:
            continue
        key = re.sub(r"\s+", " ", item["summary"].strip().lower())
        groups.setdefault(key, []).append(item)
    duplicate_of: dict[str, str] = {}
    merged_repetition: dict[str, int] = {}
    for group in groups.values():
        canonical = group[0]["candidate_id"]
        merged_repetition[canonical] = sum(
            int(item["relevance"]["repetition_count"]) for item in group
        )
        for item in group[1:]:
            duplicate_of[item["candidate_id"]] = canonical

    stage_ids = {
        item["candidate_id"]: "memory_candidate_"
        + _digest(
            {
                "stage": "CONSOLIDATE",
                "source_candidate_id": item["candidate_id"],
                "candidate_digest": item["candidate_digest"],
            }
        )[:24]
        for item in normalized
    }
    result: list[dict[str, Any]] = []
    for item in normalized:
        candidate_id = item["candidate_id"]
        state = forced_state.get(candidate_id)
        relations = [
            {
                "relation": relation["relation"],
                "candidate_id": stage_ids.get(
                    relation["candidate_id"], relation["candidate_id"]
                ),
            }
            for relation in item["relations"]
        ]
        relations.extend(
            {
                "relation": relation["relation"],
                "candidate_id": stage_ids.get(
                    relation["candidate_id"], relation["candidate_id"]
                ),
            }
            for relation in forced_relations.get(candidate_id, [])
        )
        if candidate_id in duplicate_of:
            state = "SUPERSEDED"
            relations.append({
                "relation": "DUPLICATE_OF",
                "candidate_id": stage_ids[duplicate_of[candidate_id]],
            })
        if state is None:
            state = "REVIEW_REQUIRED"
        payload = {
            **item,
            "candidate_id": stage_ids[candidate_id],
            "stage": "CONSOLIDATE",
            "state": state,
            "relations": relations,
            "relevance": {
                "repetition_count": merged_repetition.get(
                    candidate_id, item["relevance"]["repetition_count"]
                )
            },
        }
        result.append(normalize_memory_candidate(payload))
    return sorted(result, key=lambda item: item["candidate_id"])


def synthesize_memory_candidates(
    candidates: list[Mapping[str, Any]],
    *,
    kinds: list[str] | tuple[str, ...] = ("IDEA", "HYPOTHESIS", "PRODUCT"),
) -> list[dict[str, Any]]:
    """Create typed, review-only candidates from consolidated Memory records."""

    normalized = [normalize_memory_candidate(item) for item in candidates]
    active = [
        item
        for item in normalized
        if item["state"] not in {"SUPERSEDED", "IGNORE", "CONFLICTED"}
    ]
    if not active:
        return []
    normalized_kinds: list[str] = []
    for kind in kinds:
        normalized_kind = str(kind).strip().upper()
        if normalized_kind not in {"IDEA", "HYPOTHESIS", "PRODUCT"}:
            raise MemoryError(
                "MEMORY_CANDIDATE_KIND_INVALID",
                "synthesis kinds must be IDEA, HYPOTHESIS, or PRODUCT",
            )
        if normalized_kind not in normalized_kinds:
            normalized_kinds.append(normalized_kind)
    source_ids = sorted(item["candidate_id"] for item in active)
    source_digest = _digest(source_ids)
    ref_digests = sorted(item["candidate_digest"] for item in active)
    result: list[dict[str, Any]] = []
    for kind in normalized_kinds:
        result.append(
            normalize_memory_candidate(
                {
                    "project_id": active[0]["project_id"],
                    "stage": "SYNTHESIZE",
                    "kind": kind,
                    "summary": (
                        f"{kind.title()} candidate derived from {len(active)} "
                        f"Memory candidates ({source_digest[:16]})."
                    ),
                    "source_session": "UNIVERSE_MEMORY_SYNTHESIS",
                    "ref_digests": ref_digests,
                    "relations": [
                        {"relation": "DERIVED_FROM", "candidate_id": candidate_id}
                        for candidate_id in source_ids
                    ],
                    "relevance": {"repetition_count": 1},
                }
            )
        )
    return result


def independent_check_memory_candidates(
    candidates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a bounded integrity report without changing candidate state."""

    failures: list[str] = []
    normalized: list[dict[str, Any]] = []
    for item in candidates:
        try:
            normalized.append(normalize_memory_candidate(item))
        except MemoryError as error:
            failures.append(error.code)
    ids = {item["candidate_id"] for item in normalized}
    for item in normalized:
        for relation in item["relations"]:
            if relation["candidate_id"] not in ids:
                failures.append("RELATION_TARGET_MISSING")
    return {
        "schema": "universe.memory-candidate-independent-check.v1",
        "status": "PASS" if not failures else "FAIL",
        "candidate_count": len(normalized),
        "failure_codes": sorted(set(failures)),
        "candidate_digest": _digest(
            sorted(item["candidate_digest"] for item in normalized)
        ),
        "effects": {
            "candidate_write": "NONE",
            "current_anchor": "NONE",
            "authority": "NONE",
        },
    }
