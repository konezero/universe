"""Role runner for the independent Task Frame Host.

One role attempt is one short-lived Task Frame turn: the Worker may write only
inside the exact scope the Master decided; the Reviewer is always read-only.
The persona the Master assigned travels in the Context Pack, and feedback from
the Master (rework, failed review) is appended verbatim.  The Host never judges
the result: a structured result is reported, and only a provider or transport
failure is a FAILED role.

The Runtime Host and its binding are ordinary Universe pieces; nothing here
talks to the Task Frame database directly.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from task_frame_host import RoleResult
from universe_runtime_host import RuntimeHostError, UniverseRuntimeHost

WORKER_OUTPUT_CONTRACT = {
    "schema": "universe.task-frame-host-worker-output.v1",
    "format": "STRUCTURED_JSON",
    "required": ["outcome", "result_text", "evidence_refs", "validation_state"],
    "json_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "result_text", "evidence_refs", "validation_state"],
        "properties": {
            "outcome": {"type": "string", "minLength": 3, "maxLength": 32},
            "result_text": {"type": "string", "minLength": 1, "maxLength": 16000},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "validation_state": {"type": "string", "minLength": 3, "maxLength": 32},
        },
    },
    "instruction": (
        "Complete the assigned task inside the declared scope and return one JSON "
        "object. Keep the task result separate from transport state."
    ),
}

REVIEWER_OUTPUT_CONTRACT = {
    "schema": "universe.task-frame-host-reviewer-output.v1",
    "format": "STRUCTURED_JSON",
    "required": ["verdict", "evidence_refs", "note", "next_action"],
    "json_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "evidence_refs", "note", "next_action"],
        "properties": {
            "verdict": {"type": "string", "minLength": 4, "maxLength": 32},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string", "maxLength": 4000},
            "next_action": {"type": "string", "maxLength": 1000},
        },
    },
    "instruction": (
        "Review the supplied Worker result against the Todo. Return one JSON object "
        "with verdict PASS, NEEDS_REVISION, or BLOCKED and cite evidence_refs."
    ),
}


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


class RuntimeHostRoleRunner:
    def __init__(self, spec: Mapping[str, Any], *, runtime_host: Any | None = None) -> None:
        self.spec = spec
        self.host = runtime_host or UniverseRuntimeHost(Path(str(spec["repository_root"])))
        self.last_worker_result: Mapping[str, Any] | None = None

    def run(self, role: str, *, attempt: int, feedback: str | None) -> RoleResult:
        spec = self.spec
        frame_id = f"{spec['task_frame_id']}_{role.lower()}_{attempt}"
        turn_id = f"{role.lower()}-turn"
        is_worker = role == "WORKER"
        scope = spec.get("worker_write_scope") if is_worker else None
        write_scope = str((scope or {}).get("repository_write_scope") or "NONE").upper()
        todo = spec.get("todo") if isinstance(spec.get("todo"), Mapping) else {}
        context: dict[str, Any] = {
            "schema": "universe.task-frame-host-context.v1",
            "semantic_role": "IMPLEMENTER" if is_worker else "REVIEWER",
            "runtime_role": "WORKER",
            "task_frame_id": spec["task_frame_id"],
            "todo_id": spec["todo_id"],
            "todo": {"title": todo.get("title"), "detail": todo.get("detail")},
            "persona": str(spec.get("persona_text") or ""),
            "attempt": attempt,
            "constraints": [
                "Use only the supplied Todo and repository evidence.",
                "Report the task result separately from transport state.",
                "Do not invoke subagents.",
            ],
        }
        if feedback:
            context["master_feedback"] = feedback
        if not is_worker and self.last_worker_result is not None:
            context["worker_result"] = dict(self.last_worker_result)
        contract = WORKER_OUTPUT_CONTRACT if is_worker else REVIEWER_OUTPUT_CONTRACT
        constraints = ["NO_SUBAGENTS", "STRUCTURED_JSON_ONLY"] + (
            ["SOURCE_MUTATION_WITHIN_SCOPE_ONLY"]
            if write_scope == "BOUNDED"
            else ["READ_ONLY", "NO_SOURCE_MUTATION"]
        )
        try:
            provider_result = self.host.invoke_structured_task(
                runtime_binding=spec["runtime_binding"],
                provider=str(spec["provider"]),
                invocation_id=f"host:{frame_id}",
                frame_id=frame_id,
                turn_id=turn_id,
                source_ref=str(spec.get("source_ref") or f"universe://todo/{spec['todo_id']}"),
                instruction=str(contract["instruction"]),
                context_pack=context,
                output_contract=contract,
                repository_write_scope=write_scope,
                mutation_scope=(scope or {}).get("mutation_scope"),
                constraints=constraints,
            )
        except RuntimeHostError as error:
            return RoleResult("FAILED", str(error.detail), error_code=str(error.code))
        structured = provider_result.get("structured_result")
        receipt = str(provider_result.get("result_receipt_ref") or "").strip()
        if not isinstance(structured, Mapping) or not receipt:
            return RoleResult(
                "FAILED",
                "provider returned no structured result or receipt",
                error_code="PROVIDER_RESULT_INVALID",
            )
        if is_worker:
            self.last_worker_result = dict(structured)
        summary = str(structured.get("result_text") or structured.get("note") or structured.get("verdict") or "")
        return RoleResult(
            "COMPLETED",
            summary[:500],
            result_ref=f"task-frame-result://{frame_id}/{turn_id}/{receipt}",
            result_digest=_digest(dict(structured)),
        )


def make(spec: Mapping[str, Any]) -> RuntimeHostRoleRunner:
    return RuntimeHostRoleRunner(spec)
