"""Deterministic, review-only Feature Node proposals from project evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


FEATURE_NODE_PROPOSAL_SCHEMA = "universe.feature-node-proposal.v1"
FEATURE_NODE_PROPOSAL_STATES = frozenset(
    {"PROPOSAL_ONLY", "EXPLORE", "REJECTED", "SUPERSEDED"}
)
FEATURE_NODE_PROPOSAL_DECISIONS = frozenset({"EXPLORE", "REJECT"})
INTENT_MEMORY_STATES = frozenset({"BRAINSTORM", "QUESTION", "DECISION_NOTE"})
INTENT_CANDIDATE_STATES = frozenset({"EXPLORE", "START_PRODUCT_DESIGN"})
INTENT_CANDIDATE_KINDS = frozenset({"IDEA", "HYPOTHESIS", "PRODUCT"})
TOKEN_RE = re.compile(r"[a-z0-9가-힣]{2,}", re.IGNORECASE)
STOP_WORDS = frozenset(
    {
        "the", "and", "for", "from", "with", "that", "this", "into",
        "project", "universe", "memory", "feature", "node", "result",
        "room", "conductor", "todo", "goal", "기능", "프로젝트", "유니버스",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _tokens(value: Any) -> frozenset[str]:
    return frozenset(
        token.casefold()
        for token in TOKEN_RE.findall(_text(value))
        if token.casefold() not in STOP_WORDS
    )


def _source_entries(
    memories: Sequence[Mapping[str, Any]],
    memory_candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for memory in memories:
        state = str(memory.get("state") or "").upper()
        title = _text(memory.get("title"))
        memory_id = _text(memory.get("memory_id"))
        if state not in INTENT_MEMORY_STATES or not title or not memory_id:
            continue
        entries.append(
            {
                "source_kind": "MEMORY",
                "source_id": memory_id,
                "source_ref": f"universe://memories/{memory_id}",
                "title": title,
                "intent_text": title,
                "tokens": _tokens(title),
                "weight": 0.10 if state == "DECISION_NOTE" else 0.05,
            }
        )
    for candidate in memory_candidates:
        state = str(candidate.get("state") or "").upper()
        kind = str(candidate.get("kind") or "").upper()
        candidate_id = _text(candidate.get("candidate_id"))
        title = _text(candidate.get("title") or candidate.get("summary"))
        if (
            state not in INTENT_CANDIDATE_STATES
            or kind not in INTENT_CANDIDATE_KINDS
            or not candidate_id
            or not title
        ):
            continue
        entries.append(
            {
                "source_kind": "MEMORY_CANDIDATE",
                "source_id": candidate_id,
                "source_ref": f"universe://memory-candidates/{candidate_id}",
                "title": title,
                "intent_text": title[:1000],
                "tokens": _tokens(title),
                "weight": 0.15 if kind == "PRODUCT" else 0.10,
            }
        )
    entries.sort(key=lambda item: (item["source_kind"], item["source_id"]))
    return entries


def _related(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_tokens = set(left.get("tokens") or [])
    right_tokens = set(right.get("tokens") or [])
    if not left_tokens or not right_tokens:
        return left["title"].casefold() == right["title"].casefold()
    shared = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(shared) >= 2 and len(shared) / max(1, len(union)) >= 0.30


def _clusters(entries: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    remaining = list(range(len(entries)))
    clusters: list[list[Mapping[str, Any]]] = []
    while remaining:
        pending = [remaining.pop(0)]
        component: list[int] = []
        while pending:
            index = pending.pop(0)
            if index in component:
                continue
            component.append(index)
            newly_related = [
                candidate
                for candidate in list(remaining)
                if any(_related(entries[candidate], entries[item]) for item in component)
            ]
            for candidate in newly_related:
                remaining.remove(candidate)
                pending.append(candidate)
        clusters.append([entries[index] for index in sorted(component)])
    return clusters


def _existing_target(
    title: str,
    feature_nodes: Sequence[Mapping[str, Any]],
) -> tuple[str | None, float]:
    tokens = _tokens(title)
    best_ref: str | None = None
    best_score = 0.0
    for feature in feature_nodes:
        feature_tokens = _tokens(
            f"{feature.get('title') or ''} {feature.get('intent_text') or ''}"
        )
        shared = tokens & feature_tokens
        union = tokens | feature_tokens
        score = len(shared) / max(1, len(union))
        if len(shared) >= 2 and score >= 0.35 and score > best_score:
            best_ref = str(feature.get("feature_id") or "") or None
            best_score = score
    return best_ref, best_score


def build_feature_node_proposals(
    *,
    project_id: str,
    memories: Sequence[Mapping[str, Any]],
    memory_candidates: Sequence[Mapping[str, Any]],
    feature_nodes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build stable proposal records without mutating Feature, Goal, or Todo state."""

    entries = _source_entries(memories, memory_candidates)
    proposals: list[dict[str, Any]] = []
    for members in _clusters(entries):
        evidence_refs = sorted(str(item["source_ref"]) for item in members)
        cluster_digest = _digest(
            {"project_id": project_id, "evidence_refs": evidence_refs}
        )
        cluster_id = "semantic_cluster_" + cluster_digest[:24]
        representative = sorted(
            members,
            key=lambda item: (-float(item["weight"]), item["source_kind"], item["source_id"]),
        )[0]
        title = str(representative["title"])
        intent_text = str(representative["intent_text"])
        target_node_ref, match_score = _existing_target(title, feature_nodes)
        proposal_kind = "LINK_EXISTING" if target_node_ref else "NEW_FEATURE"
        confidence = min(
            0.90,
            0.40
            + sum(float(item["weight"]) for item in members)
            + 0.10 * min(3, len(members) - 1)
            + (0.15 * match_score if target_node_ref else 0.0),
        )
        material = {
            "schema": FEATURE_NODE_PROPOSAL_SCHEMA,
            "project_id": project_id,
            "proposal_kind": proposal_kind,
            "title": title[:160],
            "intent_text": intent_text[:1000],
            "target_node_ref": target_node_ref,
            "cluster_refs": [cluster_id],
            "evidence_refs": evidence_refs,
            "constraints": [],
            "confidence": round(confidence, 4),
            "state": "PROPOSAL_ONLY",
            "review": None,
            "effects": {
                "feature_node_created": False,
                "goal_created": False,
                "todo_created": False,
                "task_frame_created": False,
                "authority_created": False,
                "execution_assignment_created": False,
                "rag_adopted": False,
            },
            "next_operation": "USER_REVIEW_ONLY",
        }
        proposal_digest = _digest(material)
        material["proposal_digest"] = proposal_digest
        material["proposal_id"] = "feature_proposal_" + proposal_digest[:24]
        proposals.append(material)
    proposals.sort(key=lambda item: (-float(item["confidence"]), item["proposal_id"]))
    return proposals
