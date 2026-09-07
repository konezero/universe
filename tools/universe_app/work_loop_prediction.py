"""Deterministic, review-only Goal/Plan/Milestone/risk predictions.

Predictions combine curated Seeds, Experience, Bench observations, Todo
outcomes, and Memory/RAG hits. They never create Goals, Todos, authority,
or execution assignments.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


WORK_LOOP_PREDICTION_SCHEMA = "universe.work-loop-prediction.v1"
WORK_LOOP_RESULT_FANOUT_SCHEMA = "universe.work-loop-result-fanout.v1"
SUGGESTION_KINDS = frozenset({"GOAL", "PLAN", "MILESTONE", "RISK"})
REVIEW_STATES = frozenset({"PROPOSAL_ONLY", "KEPT", "REJECTED"})
FAILURE_OUTCOMES = frozenset({"FAILED", "FAIL", "ERROR"})
FAILURE_VALIDATION = frozenset({"FAIL", "FAILED"})
TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "project",
        "todo",
        "goal",
        "plan",
    }
)
LOW_CONFIDENCE_THRESHOLD = 0.45
MIN_EVIDENCE_KINDS = 2
KIND_WEIGHT = 0.20
SUCCESS_BONUS = 0.10
BENCH_BONUS = 0.10
MEMORY_BONUS = 0.05
RISK_BASE = 0.70
MAX_CONFIDENCE = 0.95
CALIBRATION_MATCHES = frozenset({"HIT", "PARTIAL", "MISS"})
CALIBRATION_STEP = 0.04
CALIBRATION_CLAMP = 0.15
PROGRESS_TODO_STATES = frozenset({"DONE"})
BLOCKED_TODO_STATES = frozenset({"BLOCKED"})


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def tokenize(value: Any) -> frozenset[str]:
    text = str(value or "").lower()
    return frozenset(
        token for token in TOKEN_RE.findall(text) if token not in STOP_WORDS
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return []


def _observation_failed(observation: Mapping[str, Any]) -> bool:
    outcome = str(observation.get("outcome") or "").upper()
    validation = str(observation.get("validation_state") or "").upper()
    return outcome in FAILURE_OUTCOMES or validation in FAILURE_VALIDATION


def _case_failed(case: Mapping[str, Any]) -> bool:
    observations = [
        item for item in _items(case.get("observations")) if isinstance(item, Mapping)
    ]
    if observations:
        return any(_observation_failed(item) for item in observations)
    return str(case.get("outcome") or "").upper() in FAILURE_OUTCOMES


def _case_tokens(case: Mapping[str, Any]) -> frozenset[str]:
    parts = [case.get("title"), case.get("case_id")]
    for observation in _items(case.get("observations")):
        if not isinstance(observation, Mapping):
            continue
        skill = _mapping(observation.get("skill"))
        parts.extend(
            [
                skill.get("skill_id"),
                observation.get("task_kind"),
                observation.get("node_ref"),
                observation.get("failure_kind"),
            ]
        )
    return tokenize(" ".join(_text(part) for part in parts if part))


def retrieve_recurrence_prevention(
    experience_cases: Sequence[Mapping[str, Any]],
    candidate_tokens: frozenset[str],
) -> list[dict[str, Any]]:
    """Return failed Experience that overlaps the candidate tokens."""

    hits: list[dict[str, Any]] = []
    for case in experience_cases:
        if not isinstance(case, Mapping) or not _case_failed(case):
            continue
        shared = sorted(candidate_tokens & _case_tokens(case))
        if not shared:
            continue
        hits.append(
            {
                "case_id": _text(case.get("case_id")),
                "title": _text(case.get("title")) or None,
                "shared_tokens": shared,
                "relation": "RECURRENCE_PREVENTION",
                "causal_state": "NOT_INFERRED",
            }
        )
    hits.sort(key=lambda item: (-len(item["shared_tokens"]), item["case_id"]))
    return hits


def _seed_candidates(seed: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not seed:
        return []
    candidates: list[dict[str, Any]] = []
    project = _mapping(seed.get("project"))
    goal = _text(project.get("goal") or seed.get("goal"))
    if goal:
        candidates.append(
            {
                "kind": "GOAL",
                "title": goal,
                "source_kind": "SEED",
                "source_ref": _text(seed.get("seed_id")) or "seed:current",
            }
        )
    for node in _items(seed.get("nodes")):
        if not isinstance(node, Mapping):
            continue
        title = _text(node.get("title") or node.get("label") or node.get("node_id"))
        if not title:
            continue
        candidates.append(
            {
                "kind": "MILESTONE",
                "title": title,
                "source_kind": "SEED",
                "source_ref": _text(node.get("node_id")) or title,
            }
        )
    return candidates


def _experience_candidates(cases: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        title = _text(case.get("title")) or _text(case.get("case_id"))
        if not title:
            continue
        failed = _case_failed(case)
        candidates.append(
            {
                "kind": "RISK" if failed else "PLAN",
                "title": title,
                "source_kind": (
                    "EXPERIENCE_FAILURE" if failed else "EXPERIENCE_SUCCESS"
                ),
                "source_ref": _text(case.get("case_id")),
            }
        )
    return candidates


def _todo_candidates(todos: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for todo in todos:
        if not isinstance(todo, Mapping):
            continue
        state = str(todo.get("state") or "").upper()
        title = _text(todo.get("title"))
        if not title or state not in {"DONE", "BLOCKED"}:
            continue
        candidates.append(
            {
                "kind": "RISK" if state == "BLOCKED" else "PLAN",
                "title": title,
                "source_kind": "TODO_OUTCOME",
                "source_ref": _text(todo.get("todo_id")),
                "todo_state": state,
            }
        )
    return candidates


def _memory_candidates(memories: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for memory in memories:
        if not isinstance(memory, Mapping):
            continue
        title = _text(memory.get("title") or memory.get("body"))
        if not title:
            continue
        candidates.append(
            {
                "kind": "PLAN",
                "title": title[:160],
                "source_kind": "MEMORY",
                "source_ref": _text(memory.get("memory_id")) or title[:80],
            }
        )
    return candidates


def _bench_hits(
    observations: Sequence[Mapping[str, Any]], tokens: frozenset[str]
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for observation in observations:
        if not isinstance(observation, Mapping):
            continue
        skill = _mapping(observation.get("skill"))
        haystack = tokenize(
            " ".join(
                [
                    _text(skill.get("skill_id")),
                    _text(observation.get("task_kind")),
                    _text(observation.get("node_ref")),
                    _text(observation.get("outcome")),
                ]
            )
        )
        shared = sorted(tokens & haystack)
        if not shared:
            continue
        hits.append(
            {
                "kind": "BENCH",
                "ref": _text(observation.get("observation_id")),
                "shared_tokens": shared,
                "outcome": _text(observation.get("outcome")),
            }
        )
    return hits


def _direction_for_kind(kind: Any) -> str:
    """What a kept suggestion of this kind predicts will happen: a RISK
    predicts a block/failure, everything else predicts forward progress."""

    return "BLOCKED" if str(kind or "").upper() == "RISK" else "PROGRESS"


def _realised_todo_directions(
    todos: Sequence[Mapping[str, Any]],
) -> list[tuple[frozenset[str], str, str]]:
    out: list[tuple[frozenset[str], str, str]] = []
    for todo in todos:
        if not isinstance(todo, Mapping):
            continue
        state = str(todo.get("state") or "").upper()
        title = _text(todo.get("title"))
        if not title:
            continue
        if state in PROGRESS_TODO_STATES:
            direction = "PROGRESS"
        elif state in BLOCKED_TODO_STATES:
            direction = "BLOCKED"
        else:
            continue
        out.append(
            (
                tokenize(title),
                direction,
                "universe://todos/" + _text(todo.get("todo_id")),
            )
        )
    return out


def calibrate_predictions(
    *,
    predictions: Sequence[Mapping[str, Any]],
    todos: Sequence[Mapping[str, Any]],
    experience_cases: Sequence[Mapping[str, Any]],
    observed_at: str,
) -> list[dict[str, Any]]:
    """For each suggestion of every KEPT prediction, resolve what actually
    happened downstream (Todo state transitions and failed Experience) and
    compare it with what the suggestion predicted. Deterministic and
    review-only - it records a match, never a Goal / Todo / adoption.
    """

    todo_directions = _realised_todo_directions(todos)
    failed_cases = [
        (
            _case_tokens(case),
            "universe://experience-cases/" + _text(case.get("case_id")),
        )
        for case in experience_cases
        if isinstance(case, Mapping) and _case_failed(case)
    ]
    records: list[dict[str, Any]] = []
    for prediction in predictions:
        if not isinstance(prediction, Mapping):
            continue
        if str(prediction.get("review_state") or "").upper() != "KEPT":
            continue
        prediction_id = _text(prediction.get("proposal_id"))
        if not prediction_id:
            continue
        for index, suggestion in enumerate(_items(prediction.get("suggestions"))):
            if not isinstance(suggestion, Mapping):
                continue
            kind = str(suggestion.get("kind") or "").upper()
            if kind not in SUGGESTION_KINDS:
                continue
            predicted = _direction_for_kind(kind)
            tokens = tokenize(
                _text(suggestion.get("title"))
                + " "
                + _text(suggestion.get("rationale"))
            )
            if not tokens:
                continue
            realised_dirs: set[str] = set()
            evidence_refs: list[str] = []
            for todo_tokens, direction, ref in todo_directions:
                if len(tokens & todo_tokens) >= 2:
                    realised_dirs.add(direction)
                    if ref not in evidence_refs:
                        evidence_refs.append(ref)
            for case_tokens, ref in failed_cases:
                if len(tokens & case_tokens) >= 2:
                    realised_dirs.add("BLOCKED")
                    if ref not in evidence_refs:
                        evidence_refs.append(ref)
            if not realised_dirs:
                continue
            if len(realised_dirs) > 1:
                realised = "MIXED"
                match = "PARTIAL"
            else:
                realised = next(iter(realised_dirs))
                match = "HIT" if realised == predicted else "MISS"
            records.append(
                {
                    "prediction_proposal_id": prediction_id,
                    "suggestion_index": index,
                    "kind": kind,
                    "title": _text(suggestion.get("title"))[:160],
                    "predicted_direction": predicted,
                    "realised_direction": realised,
                    "match": match,
                    "evidence_refs": sorted(evidence_refs)[:10],
                    "calibrated_at": observed_at,
                }
            )
    records.sort(
        key=lambda item: (
            item["prediction_proposal_id"],
            item["suggestion_index"],
        )
    )
    return records


def summarize_calibration(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    kind_stats: dict[str, dict[str, int]] = {}
    totals = {"HIT": 0, "PARTIAL": 0, "MISS": 0}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        match = str(record.get("match") or "").upper()
        if match not in CALIBRATION_MATCHES:
            continue
        kind = str(record.get("kind") or "").upper()
        stats = kind_stats.setdefault(
            kind, {"HIT": 0, "PARTIAL": 0, "MISS": 0}
        )
        stats[match] += 1
        totals[match] += 1
    return {
        "kind_stats": kind_stats,
        "totals": totals,
        "count": totals["HIT"] + totals["PARTIAL"] + totals["MISS"],
    }


def calibration_confidence_delta(kind: Any, summary: Mapping[str, Any]) -> float:
    """Deterministic confidence nudge for a suggestion kind: accumulated
    HITs raise it, MISSes lower it, both bounded. PARTIAL does not move it.
    """

    stats = (summary.get("kind_stats") or {}).get(str(kind or "").upper())
    if not isinstance(stats, Mapping):
        return 0.0
    raw = CALIBRATION_STEP * min(int(stats.get("HIT", 0)), 3) - (
        CALIBRATION_STEP * min(int(stats.get("MISS", 0)), 3)
    )
    return round(max(-CALIBRATION_CLAMP, min(CALIBRATION_CLAMP, raw)), 3)


def _score(
    *,
    evidence_kinds: set[str],
    recurrence: Sequence[Mapping[str, Any]],
    bench_hits: Sequence[Mapping[str, Any]],
    memory_hit: bool,
    calibration_delta: float = 0.0,
) -> float:
    if recurrence:
        return round(
            max(
                0.0,
                min(
                    MAX_CONFIDENCE,
                    RISK_BASE
                    + 0.05 * min(len(recurrence), 4)
                    + calibration_delta,
                ),
            ),
            2,
        )
    score = KIND_WEIGHT * len(evidence_kinds)
    if "EXPERIENCE_SUCCESS" in evidence_kinds:
        score += SUCCESS_BONUS
    if bench_hits:
        score += BENCH_BONUS
    if memory_hit:
        score += MEMORY_BONUS
    score += calibration_delta
    return round(max(0.0, min(MAX_CONFIDENCE, score)), 2)


def build_work_loop_predictions(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Build explainable, non-adopting Goal/Plan/Milestone/risk suggestions."""

    project_id = _text(bundle.get("project_id"))
    seed = bundle.get("seed")
    seed_map = _mapping(seed) if seed is not None else {}
    experience_cases = [
        item for item in _items(bundle.get("experience_cases")) if isinstance(item, Mapping)
    ]
    bench_observations = [
        item
        for item in _items(bundle.get("bench_observations"))
        if isinstance(item, Mapping)
    ]
    todos = [item for item in _items(bundle.get("todos")) if isinstance(item, Mapping)]
    memories = [
        item for item in _items(bundle.get("memories")) if isinstance(item, Mapping)
    ]
    calibration_records = [
        item
        for item in _items(bundle.get("calibration"))
        if isinstance(item, Mapping)
    ]
    calibration_summary = summarize_calibration(calibration_records)

    def _calibration_for(kind: Any) -> dict[str, Any] | None:
        if not calibration_records:
            return None
        return {
            "delta": calibration_confidence_delta(kind, calibration_summary),
            "kind_stats": (calibration_summary["kind_stats"]).get(
                str(kind or "").upper(), {"HIT": 0, "PARTIAL": 0, "MISS": 0}
            ),
        }

    raw_candidates = (
        _seed_candidates(seed_map or None)
        + _experience_candidates(experience_cases)
        + _todo_candidates(todos)
        + _memory_candidates(memories)
    )

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in raw_candidates:
        key = (str(candidate["kind"]), str(candidate["title"]).casefold())
        current = grouped.setdefault(
            key,
            {
                "kind": candidate["kind"],
                "title": candidate["title"],
                "provenance": [],
                "tokens": set(),
            },
        )
        current["provenance"].append(
            {
                "kind": candidate["source_kind"],
                "ref": candidate["source_ref"],
            }
        )
        current["tokens"].update(tokenize(candidate["title"]))

    suggestions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in grouped.values():
        tokens = frozenset(item["tokens"])
        provenance = item["provenance"]
        evidence_kinds = {entry["kind"] for entry in provenance}
        recurrence = retrieve_recurrence_prevention(experience_cases, tokens)
        bench_hits = _bench_hits(bench_observations, tokens)
        if bench_hits:
            evidence_kinds.add("BENCH")
            provenance.extend(
                {"kind": "BENCH", "ref": hit["ref"]} for hit in bench_hits[:3]
            )
        memory_hit = "MEMORY" in evidence_kinds
        if recurrence:
            suggestion = {
                "kind": "RISK",
                "title": item["title"],
                "rationale": (
                    "Prior failed Experience overlaps this candidate; "
                    "repeating it is blocked as a reviewable risk."
                ),
                "provenance": provenance
                + [
                    {"kind": "EXPERIENCE_FAILURE", "ref": hit["case_id"]}
                    for hit in recurrence[:3]
                ],
                "confidence": _score(
                    evidence_kinds=evidence_kinds,
                    recurrence=recurrence,
                    bench_hits=bench_hits,
                    memory_hit=memory_hit,
                    calibration_delta=calibration_confidence_delta(
                        "RISK", calibration_summary
                    ),
                ),
                "adoption_state": "PROPOSAL_ONLY",
                "recurrence_prevention": recurrence[:5],
            }
            _calib = _calibration_for("RISK")
            if _calib is not None:
                suggestion["calibration"] = _calib
            suggestions.append(suggestion)
            continue
        if item["kind"] == "RISK":
            suggestion = {
                "kind": "RISK",
                "title": item["title"],
                "rationale": "Blocked or failed outcomes support a reviewable risk only.",
                "provenance": provenance,
                "confidence": _score(
                    evidence_kinds=evidence_kinds,
                    recurrence=[],
                    bench_hits=bench_hits,
                    memory_hit=memory_hit,
                    calibration_delta=calibration_confidence_delta(
                        "RISK", calibration_summary
                    ),
                ),
                "adoption_state": "PROPOSAL_ONLY",
                "recurrence_prevention": [],
            }
            _calib = _calibration_for("RISK")
            if _calib is not None:
                suggestion["calibration"] = _calib
            if suggestion["confidence"] < LOW_CONFIDENCE_THRESHOLD:
                rejected.append(
                    {
                        "title": item["title"],
                        "kind": "RISK",
                        "reason": "LOW_CONFIDENCE",
                        "confidence": suggestion["confidence"],
                        "provenance": provenance,
                    }
                )
            else:
                suggestions.append(suggestion)
            continue
        if len(evidence_kinds) < MIN_EVIDENCE_KINDS:
            rejected.append(
                {
                    "title": item["title"],
                    "kind": item["kind"],
                    "reason": "UNSUPPORTED",
                    "confidence": _score(
                        evidence_kinds=evidence_kinds,
                        recurrence=[],
                        bench_hits=bench_hits,
                        memory_hit=memory_hit,
                    ),
                    "provenance": provenance,
                    "detail": "fewer than two evidence kinds support this prediction",
                }
            )
            continue
        confidence = _score(
            evidence_kinds=evidence_kinds,
            recurrence=[],
            bench_hits=bench_hits,
            memory_hit=memory_hit,
            calibration_delta=calibration_confidence_delta(
                item["kind"], calibration_summary
            ),
        )
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            rejected.append(
                {
                    "title": item["title"],
                    "kind": item["kind"],
                    "reason": "LOW_CONFIDENCE",
                    "confidence": confidence,
                    "provenance": provenance,
                }
            )
            continue
        _general = {
            "kind": item["kind"],
            "title": item["title"],
            "rationale": (
                "Supported by "
                + ", ".join(sorted(evidence_kinds))
                + "; reviewable proposal only."
            ),
            "provenance": provenance,
            "confidence": confidence,
            "adoption_state": "PROPOSAL_ONLY",
            "recurrence_prevention": [],
        }
        _calib = _calibration_for(item["kind"])
        if _calib is not None:
            _general["calibration"] = _calib
        suggestions.append(_general)

    if not raw_candidates:
        rejected.append(
            {
                "title": None,
                "kind": None,
                "reason": "UNSUPPORTED",
                "confidence": 0.0,
                "provenance": [],
                "detail": "no Seed, Experience, Todo outcome, or Memory evidence",
            }
        )

    suggestions.sort(key=lambda item: (-float(item["confidence"]), item["kind"], item["title"]))
    rejected.sort(key=lambda item: (item.get("reason") or "", item.get("title") or ""))
    material = {
        "schema": WORK_LOOP_PREDICTION_SCHEMA,
        "status": "WORK_LOOP_PREDICTION_READY",
        "project_id": project_id,
        "suggestions": suggestions,
        "rejected": rejected,
        "adoption_policy": {
            "auto_adopt": False,
            "creates_goal": False,
            "creates_todo": False,
            "creates_authority": False,
            "review_states": sorted(REVIEW_STATES),
        },
        "effects": {
            "project_source_write": "NONE",
            "project_runtime_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
        },
        "next_operation": "USER_REVIEW_ONLY",
    }
    material["calibration"] = {
        **calibration_summary,
        "applied": bool(calibration_records),
    }
    material["proposal_digest"] = _digest(
        {
            "project_id": project_id,
            "suggestions": suggestions,
            "rejected": rejected,
            "adoption_policy": material["adoption_policy"],
        }
    )
    material["proposal_id"] = "workloop_" + material["proposal_digest"][:24]
    return material


def build_result_fanout(
    *,
    project_id: str,
    source_kind: str,
    source_id: str,
    outcome: str,
    state: str,
) -> dict[str, Any]:
    material = {
        "schema": WORK_LOOP_RESULT_FANOUT_SCHEMA,
        "project_id": project_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "outcome": outcome,
        "state": state,
        "targets": [
            {"kind": "GOAL_PLAN_OBSERVATION", "action": "RECORD_ONLY"},
            {"kind": "EXPERIENCE_CANDIDATE", "action": "RECORD_ONLY"},
            {"kind": "MEMORY_REVIEW", "action": "RECORD_ONLY"},
        ],
        "auto_adopted": False,
        "effects": {
            "creates_goal": False,
            "creates_todo": False,
            "creates_experience_case": False,
            "authority": "NONE",
        },
    }
    material["fanout_digest"] = _digest(material)
    material["fanout_id"] = "fanout_" + material["fanout_digest"][:24]
    return material
