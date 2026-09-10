"""Group Memory/RAG review items onto existing Feature/TODO next-work."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from universe_app.feature_node_proposal import _related, _text, _tokens


REVIEW_INBOX_SCHEMA = "universe.review-inbox-next-work.v1"
MEMORY_CANDIDATE_PAGE_LIMIT = 200
PREDICTION_PAGE_LIMIT = 50
RESULT_REVIEW_PAGE_LIMIT = 250
BUNDLE_LIMIT = 12

OPEN_TODO_STATES = frozenset({"READY", "IN_PROGRESS", "BLOCKED", "BACKLOG"})
MEMORY_REVIEW_STATES = frozenset(
    {"REVIEW_REQUIRED", "EXPLORE", "START_PRODUCT_DESIGN", "CONFLICTED"}
)
PREDICTION_REVIEW_STATES = frozenset({"PROPOSAL_ONLY", "KEPT"})
PRODUCT_CANDIDATE_KINDS = frozenset({"IDEA", "HYPOTHESIS", "PRODUCT"})
PRODUCT_PREDICTION_KINDS = frozenset({"GOAL", "PLAN", "MILESTONE"})
TEST_ONLY_MARKERS = (
    "test-only",
    "respond with",
    "do not implement",
    "unit test fixture",
)
GENERIC_MARKERS = (
    "generic summary",
    "no summary",
    "placeholder",
    "lorem ipsum",
)
STALE_AFTER = timedelta(days=14)


def _page_count(items: Sequence[Any], limit: int) -> dict[str, Any]:
    returned = len(items)
    bounded = max(1, int(limit))
    return {
        "returned": returned,
        "limit": bounded,
        "truncated": returned >= bounded,
    }


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_stale(value: Any, *, now: datetime) -> bool:
    parsed = _parse_ts(value)
    if parsed is None:
        return False
    return now - parsed >= STALE_AFTER


def _classify_text(value: Any) -> str:
    folded = _text(value).casefold()
    if not folded:
        return "GENERIC"
    if any(marker in folded for marker in TEST_ONLY_MARKERS):
        return "TEST_ONLY"
    if any(marker in folded for marker in GENERIC_MARKERS):
        return "GENERIC"
    tokens = _tokens(folded)
    if len(tokens) < 2:
        return "GENERIC"
    return "PRODUCT_INTENT"


def _entry(
    *,
    source_kind: str,
    source_id: str,
    source_ref: str,
    title: str,
    state: str,
    classification: str,
    stale: bool,
    conflict: bool,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "source_kind": source_kind,
        "source_id": source_id,
        "source_ref": source_ref,
        "title": title,
        "state": state,
        "classification": classification,
        "stale": stale,
        "conflict": conflict,
        "tokens": _tokens(title),
        "intent_text": title,
        "weight": 0.10,
    }
    if extra:
        item.update(extra)
    return item


def _memory_entries(
    candidates: Sequence[Mapping[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for candidate in candidates:
        state = str(candidate.get("state") or "").upper()
        candidate_id = _text(candidate.get("candidate_id"))
        title = _text(candidate.get("title") or candidate.get("summary"))
        if state not in MEMORY_REVIEW_STATES or not candidate_id or not title:
            continue
        kind = str(candidate.get("kind") or "").upper()
        classification = _classify_text(title)
        product_kind = kind in PRODUCT_CANDIDATE_KINDS or (
            kind == "MEMORY" and state == "START_PRODUCT_DESIGN"
        )
        if classification == "PRODUCT_INTENT" and not product_kind:
            if state != "START_PRODUCT_DESIGN":
                classification = "REVIEW_ONLY"
        relations = candidate.get("relations")
        conflict = state == "CONFLICTED" or (
            isinstance(relations, Sequence)
            and any(
                isinstance(item, Mapping)
                and str(item.get("kind") or item.get("relation") or "").upper()
                == "CONFLICTS_WITH"
                for item in relations
            )
        )
        entries.append(
            _entry(
                source_kind="MEMORY_CANDIDATE",
                source_id=candidate_id,
                source_ref=f"universe://memory-candidates/{candidate_id}",
                title=title,
                state=state,
                classification=classification,
                stale=_is_stale(
                    candidate.get("updated_at") or candidate.get("created_at"),
                    now=now,
                ),
                conflict=conflict,
                extra={"kind": kind},
            )
        )
    return entries


def _prediction_entries(
    predictions: Sequence[Mapping[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for prediction in predictions:
        review_state = str(prediction.get("review_state") or "").upper()
        proposal_id = _text(prediction.get("proposal_id"))
        if review_state not in PREDICTION_REVIEW_STATES or not proposal_id:
            continue
        suggestions = prediction.get("suggestions")
        if not isinstance(suggestions, Sequence):
            continue
        for index, suggestion in enumerate(suggestions):
            if not isinstance(suggestion, Mapping):
                continue
            kind = str(suggestion.get("kind") or "").upper()
            title = _text(suggestion.get("title"))
            if kind not in PRODUCT_PREDICTION_KINDS or not title:
                continue
            classification = _classify_text(
                f"{title} {_text(suggestion.get('rationale'))}"
            )
            entries.append(
                _entry(
                    source_kind="WORK_LOOP_PREDICTION",
                    source_id=f"{proposal_id}#{index}",
                    source_ref=(
                        f"universe://work-loop/predictions/{proposal_id}"
                        f"/suggestions/{index}"
                    ),
                    title=title,
                    state=review_state,
                    classification=classification,
                    stale=_is_stale(
                        prediction.get("updated_at") or prediction.get("created_at"),
                        now=now,
                    ),
                    conflict=False,
                    extra={"kind": kind},
                )
            )
    return entries


def _result_entries(
    reviews: Sequence[Mapping[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for candidate in reviews:
        state = str(candidate.get("review_state") or "PENDING_REVIEW").upper()
        if state != "PENDING_REVIEW":
            continue
        candidate_id = _text(candidate.get("candidate_id"))
        title = _text(
            candidate.get("title")
            or candidate.get("summary")
            or candidate.get("outcome")
            or candidate.get("sink_kind")
        )
        if not candidate_id or not title:
            continue
        classification = _classify_text(title)
        entries.append(
            _entry(
                source_kind="RESULT_REVIEW",
                source_id=candidate_id,
                source_ref=f"universe://work-loop/review-candidates/{candidate_id}",
                title=title,
                state=state,
                classification=classification,
                stale=_is_stale(
                    candidate.get("updated_at") or candidate.get("created_at"),
                    now=now,
                ),
                conflict=False,
                extra={
                    "todo_id": _text(candidate.get("todo_id")),
                    "sink_kind": str(candidate.get("sink_kind") or ""),
                },
            )
        )
    return entries


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


def _match_todo(
    members: Sequence[Mapping[str, Any]],
    todos: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    explicit_ids = {
        str(item.get("todo_id") or "")
        for item in members
        if item.get("todo_id")
    }
    open_todos = [
        todo
        for todo in todos
        if str(todo.get("state") or "").upper() in OPEN_TODO_STATES
        and _text(todo.get("todo_id"))
        and _text(todo.get("title"))
    ]
    for todo in open_todos:
        if todo["todo_id"] in explicit_ids:
            return todo
    probe = {
        "title": " ".join(_text(item.get("title")) for item in members),
        "tokens": frozenset().union(*(item.get("tokens") or [] for item in members)),
    }
    for todo in open_todos:
        candidate = {
            "title": _text(todo.get("title")),
            "tokens": _tokens(f"{todo.get('title') or ''} {todo.get('detail') or ''}"),
        }
        if _related(probe, candidate):
            return todo
    return None


def _match_feature(
    members: Sequence[Mapping[str, Any]],
    feature_nodes: Sequence[Mapping[str, Any]],
    matched_todo: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    node_ref = _text((matched_todo or {}).get("node_ref"))
    for feature in feature_nodes:
        if node_ref and _text(feature.get("feature_id")) == node_ref:
            return feature
    probe = {
        "title": " ".join(_text(item.get("title")) for item in members),
        "tokens": frozenset().union(*(item.get("tokens") or [] for item in members)),
    }
    for feature in feature_nodes:
        candidate = {
            "title": _text(feature.get("title")),
            "tokens": _tokens(
                f"{feature.get('title') or ''} {feature.get('intent_text') or ''}"
            ),
        }
        if _related(probe, candidate):
            return feature
    return None


def _next_action(
    members: Sequence[Mapping[str, Any]],
    matched_todo: Mapping[str, Any] | None,
) -> str:
    if matched_todo is not None:
        return "OPEN_EXISTING_TODO"
    if any(
        item.get("classification") == "PRODUCT_INTENT"
        and not item.get("conflict")
        and str(item.get("state") or "").upper()
        in {"START_PRODUCT_DESIGN", "EXPLORE", "KEPT", "PROPOSAL_ONLY"}
        for item in members
    ):
        return "DISCOVER_FEATURE_PROPOSAL"
    return "REVIEW_CANDIDATES"


def build_review_inbox_next_work(
    *,
    project_id: str,
    memory_candidates: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    result_reviews: Sequence[Mapping[str, Any]],
    feature_nodes: Sequence[Mapping[str, Any]],
    todos: Sequence[Mapping[str, Any]],
    memory_candidate_limit: int = MEMORY_CANDIDATE_PAGE_LIMIT,
    prediction_limit: int = PREDICTION_PAGE_LIMIT,
    result_review_limit: int = RESULT_REVIEW_PAGE_LIMIT,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project review items onto existing TODOs without adopting or starting work."""

    clock = now or datetime.now(timezone.utc)
    entries = (
        _memory_entries(memory_candidates, now=clock)
        + _prediction_entries(predictions, now=clock)
        + _result_entries(result_reviews, now=clock)
    )
    bundles: list[dict[str, Any]] = []
    for members in _clusters(entries):
        matched_todo = _match_todo(members, todos)
        matched_feature = _match_feature(members, feature_nodes, matched_todo)
        duplicate = len(members) > 1
        next_action = _next_action(members, matched_todo)
        labels = []
        if duplicate:
            labels.append("DUPLICATE")
        if any(item.get("stale") for item in members):
            labels.append("STALE")
        if any(item.get("conflict") for item in members):
            labels.append("CONFLICT")
        if matched_feature is not None:
            labels.append("RELATED_FEATURE")
        if any(item.get("classification") == "TEST_ONLY" for item in members):
            labels.append("TEST_ONLY")
        if any(item.get("classification") == "GENERIC" for item in members):
            labels.append("GENERIC")
        representative = sorted(
            members,
            key=lambda item: (
                0 if item.get("classification") == "PRODUCT_INTENT" else 1,
                item["source_kind"],
                item["source_id"],
            ),
        )[0]
        bundles.append(
            {
                "title": representative["title"][:160],
                "labels": labels,
                "item_count": len(members),
                "item_refs": [str(item["source_ref"]) for item in members],
                "classifications": sorted(
                    {str(item["classification"]) for item in members}
                ),
                "related_todo_id": (
                    str(matched_todo.get("todo_id")) if matched_todo else None
                ),
                "related_todo_title": (
                    str(matched_todo.get("title")) if matched_todo else None
                ),
                "related_todo_state": (
                    str(matched_todo.get("state")) if matched_todo else None
                ),
                "related_feature_id": (
                    str(matched_feature.get("feature_id")) if matched_feature else None
                ),
                "next_action": next_action,
                "next_operation": "USER_REVIEW_ONLY",
            }
        )
    bundles.sort(
        key=lambda item: (
            0 if item["next_action"] == "OPEN_EXISTING_TODO" else 1,
            0 if "CONFLICT" in item["labels"] else 1,
            -int(item["item_count"]),
            str(item["title"]).casefold(),
        )
    )
    return {
        "schema": REVIEW_INBOX_SCHEMA,
        "project_id": project_id,
        "counts": {
            "memory_candidates": _page_count(
                memory_candidates, memory_candidate_limit
            ),
            "predictions": _page_count(predictions, prediction_limit),
            "result_reviews": _page_count(result_reviews, result_review_limit),
            "open_todos": len(
                [
                    todo
                    for todo in todos
                    if str(todo.get("state") or "").upper() in OPEN_TODO_STATES
                ]
            ),
            "bundles": min(len(bundles), BUNDLE_LIMIT),
            "bundles_truncated": len(bundles) > BUNDLE_LIMIT,
        },
        "bundles": bundles[:BUNDLE_LIMIT],
        "effects": {
            "auto_adopt": False,
            "goal_started": False,
            "todo_created": False,
            "rag_adopted": False,
            "task_frame_created": False,
            "authority_created": False,
        },
        "next_operation": "USER_REVIEW_ONLY",
    }
