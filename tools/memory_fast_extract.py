"""Governed FAST_EXTRACT provider contract.

Durable records retain reduced Activity references only.  The provider may
receive bounded, redacted semantic excerpts transiently so it can derive a
useful candidate; those excerpts never enter Universe persistence.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from universe_memory import MemoryError, normalize_memory_candidate


FAST_EXTRACT_REQUEST_SCHEMA = "universe.memory-fast-extract-request.v1"
FAST_EXTRACT_RESULT_SCHEMA = "universe.memory-fast-extract-result.v1"
FAST_EXTRACT_MODEL = "gpt-5.6-luna"
FAST_EXTRACT_EFFORT = "MAX"
FAST_EXTRACT_PROVIDER = "CODEX"
FAST_EXTRACT_OPERATION = "MEMORY_FAST_EXTRACT"
FAST_EXTRACT_SKILL_ID = "universe.memory.fast-extract"
FAST_EXTRACT_SKILL_VERSION = "v1"
FAST_EXTRACT_INPUT_SCHEMA = "universe.memory-fast-extract-context.v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
WORD = re.compile(r"[\w'-]+", re.UNICODE)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\b(?:sk|xai|ghp|github_pat)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S{8,}",
        re.IGNORECASE,
    ),
)


class FastExtractError(ValueError):
    """Bounded FAST_EXTRACT validation or execution-contract failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: Any, field: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FastExtractError("FAST_EXTRACT_FIELD_INVALID", f"{field} is required")
    result = value.strip()
    if len(result) > maximum:
        raise FastExtractError("FAST_EXTRACT_FIELD_INVALID", f"{field} is too long")
    return result


def _optional_text(value: Any, field: str, *, maximum: int = 256) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum=maximum)


def _bounded_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FastExtractError(
            "FAST_EXTRACT_FIELD_INVALID", f"{field} must be a non-negative integer"
        )
    if value > 2**63 - 1:
        raise FastExtractError("FAST_EXTRACT_FIELD_INVALID", f"{field} is too large")
    return value


def _reject_raw_keys(value: Any, field: str = "value") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            excluded_marker = normalized == "raw_transcript" and child == "EXCLUDED"
            if (
                normalized in {
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
                or normalized.startswith("raw_")
                or "transcript" in normalized
            ) and not excluded_marker:
                raise FastExtractError(
                    "FAST_EXTRACT_RAW_INPUT_FORBIDDEN",
                    f"{field}.{key} is not accepted",
                )
            _reject_raw_keys(child, f"{field}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_raw_keys(child, f"{field}[{index}]")


def _normalize_cursor(value: Any, field: str) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) - {"offset", "ordinal"}:
        raise FastExtractError("FAST_EXTRACT_CURSOR_INVALID", f"{field} is invalid")
    result: dict[str, int] = {}
    for key in ("offset", "ordinal"):
        if key in value:
            result[key] = _bounded_int(value[key], f"{field}.{key}")
    return result or None


def _normalize_activity_ref(value: Any, index: int) -> dict[str, Any]:
    field = f"activity_refs[{index}]"
    if not isinstance(value, Mapping):
        raise FastExtractError("FAST_EXTRACT_ACTIVITY_INVALID", f"{field} must be an object")
    allowed = {
        "activity_id",
        "activity_digest",
        "ordinal",
        "event_kind",
        "activity_state",
        "observed_at",
    }
    if set(value) - allowed:
        raise FastExtractError("FAST_EXTRACT_ACTIVITY_INVALID", f"{field} has unsupported fields")
    activity_id = _text(value.get("activity_id"), f"{field}.activity_id")
    activity_digest = _text(value.get("activity_digest"), f"{field}.activity_digest", maximum=128)
    if not HEX64.fullmatch(activity_digest.lower()):
        raise FastExtractError(
            "FAST_EXTRACT_ACTIVITY_INVALID",
            f"{field}.activity_digest must be a SHA-256 digest",
        )
    event_kind = _text(value.get("event_kind"), f"{field}.event_kind", maximum=96).upper()
    activity_state = _text(
        value.get("activity_state"), f"{field}.activity_state", maximum=96
    ).upper()
    ordinal = _bounded_int(value.get("ordinal"), f"{field}.ordinal")
    observed_at = _optional_text(value.get("observed_at"), f"{field}.observed_at", maximum=80)
    result = {
        "activity_id": activity_id,
        "activity_digest": activity_digest.lower(),
        "ordinal": ordinal,
        "event_kind": event_kind,
        "activity_state": activity_state,
    }
    if observed_at is not None:
        result["observed_at"] = observed_at
    return result


def redact_activity_batch(value: Any) -> dict[str, Any]:
    """Project one observer batch into the provider-safe Activity contract."""

    if not isinstance(value, Mapping):
        raise FastExtractError("FAST_EXTRACT_ACTIVITY_INVALID", "activity batch must be an object")
    _reject_raw_keys(value)
    source = value.get("source")
    if not isinstance(source, Mapping):
        raise FastExtractError("FAST_EXTRACT_ACTIVITY_INVALID", "source must be an object")
    allowed_source = {"provider", "provider_session_id", "source_id", "cursor"}
    if set(source) - allowed_source:
        raise FastExtractError("FAST_EXTRACT_ACTIVITY_INVALID", "source has unsupported fields")
    provider = _text(source.get("provider"), "source.provider", maximum=32).upper()
    if provider != FAST_EXTRACT_PROVIDER:
        raise FastExtractError(
            "FAST_EXTRACT_PROVIDER_INVALID",
            "FAST_EXTRACT accepts only a Codex Activity source",
        )
    provider_session_id = _text(
        source.get("provider_session_id"), "source.provider_session_id"
    )
    source_id = _text(source.get("source_id"), "source.source_id")
    refs = value.get("activity_refs")
    if not isinstance(refs, list) or not refs or len(refs) > 512:
        raise FastExtractError(
            "FAST_EXTRACT_ACTIVITY_INVALID",
            "activity_refs must contain 1..512 reduced references",
        )
    normalized_refs = [_normalize_activity_ref(item, index) for index, item in enumerate(refs)]
    cursor = _normalize_cursor(source.get("cursor"), "source.cursor")
    normalized_source: dict[str, Any] = {
        "provider": provider,
        "provider_session_id": provider_session_id,
        "source_id": source_id,
    }
    if cursor is not None:
        normalized_source["cursor"] = cursor
    material = {"source": normalized_source, "activity_refs": normalized_refs}
    return {
        "schema": "universe.provider-activity-batch-redacted.v1",
        "source": normalized_source,
        "activity_refs": normalized_refs,
        "batch_digest": digest(material),
    }


def redact_activity_batches(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values or len(values) > 64:
        raise FastExtractError(
            "FAST_EXTRACT_ACTIVITY_INVALID",
            "activity_batches must contain 1..64 batches",
        )
    batches = [redact_activity_batch(value) for value in values]
    unique = {batch["batch_digest"] for batch in batches}
    if len(unique) != len(batches):
        batches = list({batch["batch_digest"]: batch for batch in batches}.values())
    return sorted(batches, key=lambda item: item["batch_digest"])


def normalize_runtime_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FastExtractError(
            "FAST_EXTRACT_RUNTIME_BINDING_INVALID",
            "runtime_binding must be an object",
        )
    allowed = {
        "task_frame_ref",
        "session_id",
        "frame_id",
        "turn_id",
        "endpoint",
        "token",
        "invoker_actor_ref",
    }
    if set(value) - allowed:
        raise FastExtractError(
            "FAST_EXTRACT_RUNTIME_BINDING_INVALID",
            "runtime_binding has unsupported fields",
        )
    task_frame_ref = _text(
        value.get("task_frame_ref"), "runtime_binding.task_frame_ref"
    )
    frame_id = _text(value.get("frame_id"), "runtime_binding.frame_id")
    if task_frame_ref != frame_id:
        raise FastExtractError(
            "FAST_EXTRACT_RUNTIME_BINDING_INVALID",
            "task_frame_ref must equal frame_id",
        )
    return {
        "task_frame_ref": task_frame_ref,
        "session_id": _text(value.get("session_id"), "runtime_binding.session_id"),
        "frame_id": frame_id,
        "turn_id": _text(value.get("turn_id"), "runtime_binding.turn_id"),
        "endpoint": _text(value.get("endpoint"), "runtime_binding.endpoint"),
        "token": _text(value.get("token"), "runtime_binding.token"),
        "invoker_actor_ref": _text(
            value.get("invoker_actor_ref"),
            "runtime_binding.invoker_actor_ref",
        ),
    }


def normalize_transient_semantic_evidence(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values or len(values) > 256:
        raise FastExtractError(
            "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
            "semantic evidence must contain 1..256 excerpts",
        )
    normalized: list[dict[str, Any]] = []
    total_chars = 0
    for index, value in enumerate(values):
        field = f"semantic_evidence[{index}]"
        if not isinstance(value, Mapping):
            raise FastExtractError(
                "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
                f"{field} must be an object",
            )
        allowed = {
            "excerpt_id",
            "activity_digest",
            "ordinal",
            "role",
            "text",
            "text_digest",
        }
        if set(value) != allowed:
            raise FastExtractError(
                "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
                f"{field} has an invalid shape",
            )
        activity_digest = _text(
            value.get("activity_digest"), f"{field}.activity_digest", maximum=64
        ).lower()
        text_digest = _text(
            value.get("text_digest"), f"{field}.text_digest", maximum=64
        ).lower()
        if not HEX64.fullmatch(activity_digest) or not HEX64.fullmatch(text_digest):
            raise FastExtractError(
                "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
                f"{field} digests must be SHA-256",
            )
        role = _text(value.get("role"), f"{field}.role", maximum=32).upper()
        if role not in {"USER", "ASSISTANT"}:
            raise FastExtractError(
                "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
                f"{field}.role is unsupported",
            )
        text = _text(value.get("text"), f"{field}.text", maximum=2000)
        if digest(text) != text_digest:
            raise FastExtractError(
                "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
                f"{field}.text_digest does not match text",
            )
        total_chars += len(text)
        if total_chars > 32000:
            raise FastExtractError(
                "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
                "semantic evidence exceeds the 32000 character budget",
            )
        normalized.append(
            {
                "excerpt_id": _text(
                    value.get("excerpt_id"), f"{field}.excerpt_id"
                ),
                "activity_digest": activity_digest,
                "ordinal": _bounded_int(value.get("ordinal"), f"{field}.ordinal"),
                "role": role,
                "text": text,
                "text_digest": text_digest,
            }
        )
    return normalized


def build_provider_request(
    *,
    project_id: str,
    activity_batches: list[dict[str, Any]],
    semantic_evidence: list[dict[str, Any]],
    runtime_binding: Mapping[str, Any],
    invocation_id: str,
    config_digest: str,
    skill_binding_digest: str,
) -> dict[str, Any]:
    binding = normalize_runtime_binding(runtime_binding)
    project = _text(project_id, "project_id")
    invocation = _text(invocation_id, "invocation_id")
    if not HEX64.fullmatch(config_digest.lower()):
        raise FastExtractError("FAST_EXTRACT_FIELD_INVALID", "config_digest must be a SHA-256 digest")
    if not HEX64.fullmatch(skill_binding_digest.lower()):
        raise FastExtractError(
            "FAST_EXTRACT_FIELD_INVALID", "skill_binding_digest must be a SHA-256 digest"
        )
    batches = redact_activity_batches(activity_batches)
    semantics = normalize_transient_semantic_evidence(semantic_evidence)
    known_digests = {
        ref["activity_digest"]
        for batch in batches
        for ref in batch["activity_refs"]
    }
    if any(item["activity_digest"] not in known_digests for item in semantics):
        raise FastExtractError(
            "FAST_EXTRACT_SEMANTIC_EVIDENCE_INVALID",
            "semantic evidence is not bound to the selected Activity refs",
        )
    context = {
        "schema": FAST_EXTRACT_INPUT_SCHEMA,
        "project_id": project,
        "stage": "FAST_EXTRACT",
        "provider": FAST_EXTRACT_PROVIDER,
        "model_ref": FAST_EXTRACT_MODEL,
        "effort": FAST_EXTRACT_EFFORT,
        "config_digest": config_digest.lower(),
        "skill_binding_digest": skill_binding_digest.lower(),
        "activity_batches": batches,
        "activity_digest": digest(batches),
        "semantic_evidence": semantics,
        "semantic_digest": digest(
            [
                {
                    "excerpt_id": item["excerpt_id"],
                    "activity_digest": item["activity_digest"],
                    "text_digest": item["text_digest"],
                }
                for item in semantics
            ]
        ),
        "task_frame_ref": binding["task_frame_ref"],
    }
    return {
        "schema": "universe.runtime-worker-invocation-request.v1",
        "invocation_id": invocation,
        "provider": FAST_EXTRACT_PROVIDER,
        "endpoint": binding["endpoint"],
        "token": binding["token"],
        "session_id": binding["session_id"],
        "frame_id": binding["frame_id"],
        "turn_id": binding["turn_id"],
        "invoker_actor_ref": binding["invoker_actor_ref"],
        "repository_write_scope": "NONE",
        "mutation_scope": {"operations": [], "targets": []},
        "context_pack": context,
        "output_contract": {
            "json_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema", "candidates"],
                "properties": {
                    "schema": {"type": "string", "const": FAST_EXTRACT_RESULT_SCHEMA},
                    "candidates": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "summary", "ref_digests"],
                            "properties": {
                                "candidate_id": {"type": "string"},
                                "kind": {"type": "string"},
                                "summary": {"type": "string"},
                                "source_range": {"type": "object"},
                                "ref_digests": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "relevance": {"type": "object"},
                                "relations": {"type": "array"},
                            },
                        },
                    },
                },
            },
            "forbidden": [
                "prompt",
                "transcript",
                "source_text",
                "commands",
                "tool_args",
                "hidden_reasoning",
            ],
        },
        "max_turns": 1,
        "result_mode": "STRUCTURED_JSON",
    }


def _reject_verbatim_summary(
    summary: str,
    semantic_evidence: list[dict[str, Any]],
    *,
    index: int,
) -> None:
    if any(pattern.search(summary) for pattern in SECRET_VALUE_PATTERNS):
        raise FastExtractError(
            "FAST_EXTRACT_RESULT_SECRET_FORBIDDEN",
            f"candidates[{index}].summary contains secret-like material",
        )
    summary_words = [word.casefold() for word in WORD.findall(summary)]
    if len(summary_words) < 12:
        return
    for item in semantic_evidence:
        source_words = [word.casefold() for word in WORD.findall(item["text"])]
        if len(source_words) < 12:
            continue
        source_windows = {
            tuple(source_words[offset : offset + 12])
            for offset in range(len(source_words) - 11)
        }
        if any(
            tuple(summary_words[offset : offset + 12]) in source_windows
            for offset in range(len(summary_words) - 11)
        ):
            raise FastExtractError(
                "FAST_EXTRACT_RESULT_VERBATIM_FORBIDDEN",
                f"candidates[{index}].summary copies a transcript span",
            )


def normalize_provider_candidates(
    value: Any,
    *,
    project_id: str,
    activity_batches: list[dict[str, Any]],
    semantic_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise FastExtractError("FAST_EXTRACT_RESULT_INVALID", "provider result must be an object")
    _reject_raw_keys(value)
    if value.get("schema") != FAST_EXTRACT_RESULT_SCHEMA:
        raise FastExtractError("FAST_EXTRACT_RESULT_INVALID", "provider result schema is invalid")
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) > 64:
        raise FastExtractError(
            "FAST_EXTRACT_RESULT_INVALID", "provider candidates must be an array of at most 64"
        )
    refs = [
        ref
        for batch in activity_batches
        for ref in batch["activity_refs"]
    ]
    source_session = activity_batches[0]["source"] if activity_batches else {}
    known_ref_digests = {item["activity_digest"] for item in refs}
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_candidates):
        if not isinstance(raw, Mapping):
            raise FastExtractError(
                "FAST_EXTRACT_RESULT_INVALID", f"candidates[{index}] must be an object"
            )
        allowed = {
            "candidate_id",
            "kind",
            "summary",
            "source_range",
            "ref_digests",
            "relevance",
            "relations",
        }
        if set(raw) - allowed:
            raise FastExtractError(
                "FAST_EXTRACT_RESULT_INVALID", f"candidates[{index}] has unsupported fields"
            )
        ref_digests = raw.get("ref_digests")
        if ref_digests is None:
            ref_digests = [item["activity_digest"] for item in refs]
        if (
            not isinstance(ref_digests, list)
            or not ref_digests
            or not all(
                isinstance(item, str) and item in known_ref_digests
                for item in ref_digests
            )
        ):
            raise FastExtractError(
                "FAST_EXTRACT_RESULT_INVALID",
                f"candidates[{index}].ref_digests must reference selected Activity",
            )
        summary = raw.get("summary")
        if not isinstance(summary, str):
            raise FastExtractError(
                "FAST_EXTRACT_RESULT_INVALID",
                f"candidates[{index}].summary must be a string",
            )
        _reject_verbatim_summary(summary, semantic_evidence, index=index)
        candidate = {
            "project_id": project_id,
            "stage": "FAST_EXTRACT",
            "kind": raw.get("kind", "MEMORY"),
            "summary": summary,
            "candidate_id": raw.get("candidate_id"),
            "source_range": raw.get("source_range") or {},
            "ref_digests": ref_digests,
            "relevance": raw.get("relevance") or {"repetition_count": 1},
            "relations": raw.get("relations") or [],
            "source_session": source_session,
        }
        try:
            normalized.append(normalize_memory_candidate(candidate))
        except MemoryError as error:
            raise FastExtractError(error.code, error.message) from error
    if not normalized:
        raise FastExtractError(
            "FAST_EXTRACT_RESULT_INVALID", "provider result must contain at least one candidate"
        )
    return sorted(normalized, key=lambda item: item["candidate_id"])


def build_skill_observation_candidate(
    *,
    project_id: str,
    task_frame_ref: str,
    source_ref: str,
    activity_digest: str,
    skill_binding_digest: str,
    result_receipt_ref: str,
    worker_id: str,
    model_ref: str,
    activity_count: int,
    candidate_count: int,
    duration_ms: int | float,
    observed_at: str | None = None,
) -> dict[str, Any]:
    timestamp = observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    observation_material = {
        "project_id": project_id,
        "task_frame_ref": task_frame_ref,
        "source_ref": source_ref,
        "activity_digest": activity_digest,
        "skill_binding_digest": skill_binding_digest,
        "result_receipt_ref": result_receipt_ref,
        "worker_id": worker_id,
        "activity_count": activity_count,
        "candidate_count": candidate_count,
    }
    observation_digest = digest(observation_material)
    candidate_id = "skillobs_" + observation_digest[:24]
    return {
        "candidate_id": candidate_id,
        "candidate": {
            "schema": "ai-career.skill-observation-candidate.v1",
            "project_ref": f"project://{project_id}",
            "task_frame_ref": task_frame_ref,
            "source_ref": source_ref,
            "observations": [
                {
                    "observation_digest": observation_digest,
                    "skill_binding_digest": skill_binding_digest,
                    "skill": {
                        "skill_id": FAST_EXTRACT_SKILL_ID,
                        "skill_version": FAST_EXTRACT_SKILL_VERSION,
                        "operation_class": "PROPOSE",
                        "context_pack_digest": activity_digest,
                    },
                    "model_ref": model_ref,
                    "outcome": "SUCCEEDED",
                    "validation_state": "NOT_RUN",
                    "evidence_refs": [result_receipt_ref],
                    "metrics": {
                        "duration_ms": duration_ms,
                    },
                    "execution_context": {
                        "provider_ref": FAST_EXTRACT_PROVIDER,
                        "worker_role": "TASK_FRAME_WORKER",
                        "task_kind": FAST_EXTRACT_OPERATION,
                        "node_ref": "NONE",
                        "failure_kind": "NONE",
                        "quota_state": "AVAILABLE",
                    },
                }
            ],
            "observed_at": timestamp,
            "target_ref": f"universe://projects/{project_id}/bench",
            "redaction_state": "REDACTED",
        },
    }


def redacted_execution_record(
    *,
    status: str,
    provider: str,
    model_ref: str,
    worker_id: str,
    worker_run_ref: str,
    result_receipt_ref: str,
    invocation_id: str,
    task_frame_ref: str,
    skill_observation_candidate_id: str,
) -> dict[str, Any]:
    return {
        "schema": "universe.memory-fast-extract-execution.v1",
        "status": _text(status, "status", maximum=64),
        "provider": _text(provider, "provider", maximum=32).upper(),
        "model_ref": _text(model_ref, "model_ref"),
        "worker_id": _text(worker_id, "worker_id"),
        "worker_run_ref": _text(worker_run_ref, "worker_run_ref"),
        "result_receipt_ref": _text(result_receipt_ref, "result_receipt_ref"),
        "invocation_id": _text(invocation_id, "invocation_id"),
        "task_frame_ref": _text(task_frame_ref, "task_frame_ref"),
        "skill_observation_candidate_id": _text(
            skill_observation_candidate_id, "skill_observation_candidate_id"
        ),
        "repository_write": False,
        "raw_provider_result": "EXCLUDED",
    }
