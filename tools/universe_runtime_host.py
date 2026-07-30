from __future__ import annotations

import hashlib
import json
import os

# Provider workers use fixed argv lists; shell execution is disabled.
import subprocess  # nosec B404
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from host_profile import resolve_host_tool
from universe_runtime_worker_dispatch import (
    RuntimeWorkerDispatcher,
    WorkerDispatchError,
)

RUNTIME_WORKER_REQUEST_SCHEMA = "universe.runtime-worker-invocation-request.v1"
SUPPORTED_PROVIDERS = frozenset({"GROK", "CODEX"})
RESULT_MODES = frozenset({"REDACTED", "STRUCTURED_JSON"})
PLANNING_PROFILE = Path(
    ".ai/runtime/reference_runtime/profiles/task-frame-debate-v1.json"
)
PLANNING_MODELS = {
    "GROK": "grok-build",
    "CODEX": "default",
}


@dataclass(frozen=True)
class RuntimeHostError(Exception):
    code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


def _required_host_executable(tool: str) -> Path:
    resolved = resolve_host_tool(tool)
    if resolved is None:
        raise RuntimeHostError(
            "HOST_TOOL_UNAVAILABLE",
            f"{tool.upper()}_HOST_TOOL_UNAVAILABLE",
        )
    return resolved.executable


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 1024:
        raise RuntimeHostError(
            "RUNTIME_WORKER_REQUEST_INVALID",
            f"{field} must be a bounded non-empty string",
        )
    return value.strip()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeHostError(
            "RUNTIME_WORKER_REQUEST_INVALID", f"{field} must be an object"
        )
    return dict(value)


def _loopback_endpoint(value: Any, field: str) -> str:
    endpoint = _required_text(value, field)
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeHostError(
            "RUNTIME_HOST_ENDPOINT_INVALID",
            f"{field} has an invalid port",
        ) from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeHostError(
            "RUNTIME_HOST_ENDPOINT_INVALID",
            f"{field} must be a loopback HTTP origin",
        )
    return endpoint.rstrip("/")


def normalize_read_only_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeHostError(
            "RUNTIME_WORKER_REQUEST_INVALID", "request must be an object"
        )
    if value.get("schema") not in {None, RUNTIME_WORKER_REQUEST_SCHEMA}:
        raise RuntimeHostError(
            "RUNTIME_WORKER_REQUEST_INVALID", "request schema is unsupported"
        )
    provider = _required_text(value.get("provider"), "provider").upper()
    if provider not in SUPPORTED_PROVIDERS:
        raise RuntimeHostError("WORKER_PROVIDER_UNSUPPORTED", "provider is unsupported")
    endpoint = _loopback_endpoint(value.get("endpoint"), "endpoint")
    mutation_scope = _mapping(value.get("mutation_scope"), "mutation_scope")
    if (
        value.get("repository_write_scope") != "NONE"
        or mutation_scope.get("operations") != []
        or mutation_scope.get("targets") != []
    ):
        raise RuntimeHostError(
            "READ_ONLY_SCOPE_REQUIRED",
            "Runtime Host accepts read-only Task Frame turns only",
        )
    max_turns = value.get("max_turns", 1)
    if (
        not isinstance(max_turns, int)
        or isinstance(max_turns, bool)
        or not 1 <= max_turns <= 4
    ):
        raise RuntimeHostError(
            "RUNTIME_WORKER_REQUEST_INVALID", "max_turns must be 1..4"
        )
    result_mode = _required_text(
        value.get("result_mode", "REDACTED"), "result_mode"
    ).upper()
    if result_mode not in RESULT_MODES:
        raise RuntimeHostError(
            "RUNTIME_WORKER_REQUEST_INVALID",
            "result_mode must be REDACTED or STRUCTURED_JSON",
        )
    return {
        "schema": RUNTIME_WORKER_REQUEST_SCHEMA,
        "invocation_id": _required_text(value.get("invocation_id"), "invocation_id"),
        "provider": provider,
        "endpoint": endpoint.rstrip("/"),
        "token": _required_text(value.get("token"), "token"),
        "session_id": _required_text(value.get("session_id"), "session_id"),
        "frame_id": _required_text(value.get("frame_id"), "frame_id"),
        "turn_id": _required_text(value.get("turn_id"), "turn_id"),
        "invoker_actor_ref": _required_text(
            value.get("invoker_actor_ref"), "invoker_actor_ref"
        ),
        "repository_write_scope": "NONE",
        "mutation_scope": {"operations": [], "targets": []},
        "context_pack": _mapping(value.get("context_pack"), "context_pack"),
        "output_contract": _mapping(value.get("output_contract"), "output_contract"),
        "max_turns": max_turns,
        "result_mode": result_mode,
    }


def redacted_invocation_record(value: Mapping[str, Any]) -> dict[str, Any]:
    request = normalize_read_only_request(value)
    record = {
        "schema": RUNTIME_WORKER_REQUEST_SCHEMA,
        "invocation_id": request["invocation_id"],
        "provider": request["provider"],
        "session_id": request["session_id"],
        "frame_id": request["frame_id"],
        "turn_id": request["turn_id"],
        "invoker_actor_ref": request["invoker_actor_ref"],
        "repository_write_scope": "NONE",
        "mutation_scope": {"operations": [], "targets": []},
        "context_pack_digest": _digest(request["context_pack"]),
        "output_contract_digest": _digest(request["output_contract"]),
        "max_turns": request["max_turns"],
        "result_mode": request["result_mode"],
    }
    record["request_digest"] = _digest(record)
    return record


class UniverseRuntimeHost:
    def __init__(
        self,
        repository_root: Path,
        runner: Callable[..., Any] | None = None,
        worker_dispatcher: RuntimeWorkerDispatcher | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.runner = runner or subprocess.run
        self.worker_dispatcher = worker_dispatcher or RuntimeWorkerDispatcher(
            self.repository_root
        )

    def provider_capabilities(self) -> list[dict[str, str]]:
        return [
            self.provider_capability(provider)
            for provider in sorted(SUPPORTED_PROVIDERS)
        ]

    def provider_capability(self, provider: str) -> dict[str, str]:
        normalized = _required_text(provider, "provider").upper()
        if normalized not in SUPPORTED_PROVIDERS:
            return {
                "provider": normalized,
                "status": "UNAVAILABLE",
                "reason": "WORKER_PROVIDER_UNSUPPORTED",
            }
        response = self.worker_dispatcher.provider_capability(normalized)
        result = {
            "provider": normalized,
            "status": "AVAILABLE"
            if response.get("status") == "AVAILABLE"
            else "UNAVAILABLE",
        }
        if isinstance(response.get("reason"), str) and response["reason"]:
            result["reason"] = response["reason"]
        return result

    def invoke_read_only(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = normalize_read_only_request(value)
        if request["result_mode"] != "REDACTED":
            raise RuntimeHostError(
                "RUNTIME_WORKER_REQUEST_INVALID",
                "invoke_read_only requires result_mode REDACTED",
            )
        response = self._dispatch_worker(request)
        return self._invocation_result(request, response)

    def invoke_structured(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = normalize_read_only_request(value)
        if request["result_mode"] != "STRUCTURED_JSON":
            raise RuntimeHostError(
                "RUNTIME_WORKER_REQUEST_INVALID",
                "invoke_structured requires result_mode STRUCTURED_JSON",
            )
        response = self._dispatch_worker(request)
        result = self._invocation_result(request, response)
        structured = response.get("structured_result")
        if not isinstance(structured, Mapping):
            raise RuntimeHostError(
                "WORKER_STRUCTURED_RESULT_INVALID",
                "Runtime Host did not return a structured result object",
            )
        result["structured_result"] = dict(structured)
        result["model_ref"] = _text_or(response.get("model_ref"), "UNKNOWN")
        return result

    def build_planning_proposal(
        self,
        *,
        runtime_binding: Mapping[str, Any],
        refinement_request: Mapping[str, Any],
        provider: str,
        run_id: str,
    ) -> dict[str, Any]:
        normalized_provider = _required_text(provider, "provider").upper()
        capability = self.provider_capability(normalized_provider)
        if capability["status"] != "AVAILABLE":
            raise RuntimeHostError(
                "WORKER_PROVIDER_UNAVAILABLE",
                capability.get("reason", "selected provider is unavailable"),
            )
        model = PLANNING_MODELS[normalized_provider]
        frame_id = f"fresh-project-planning:{_required_text(run_id, 'run_id')}"
        turn_id = "planning-boss"
        request_id = _required_text(refinement_request.get("request_id"), "request_id")
        source_ref = f"universe://fresh-project-refinement/{request_id}"
        execution_plan: dict[str, Any] = {
            "profile_id": "task-frame-debate-v1",
            "requested_shape": "DEBATE",
            "resolved_shape": "DEBATE",
            "model_mode": "EXPLICIT",
            "frame_id": frame_id,
            "origin_anchor_ref": _required_text(
                runtime_binding.get("origin_anchor_ref"), "origin_anchor_ref"
            ),
            "origin_session_id": _required_text(
                runtime_binding.get("session_id"), "session_id"
            ),
            "origin_frame_id": _required_text(
                runtime_binding.get("origin_frame_id"), "origin_frame_id"
            ),
            "task_summary_ref": source_ref + "#planning",
            "source_ref": source_ref,
            "candidate_source_ref": "NONE",
            "source_review_result": None,
            "parent_actor_ref": _required_text(
                runtime_binding.get("parent_actor_ref"), "parent_actor_ref"
            ),
            "commander_surface": "universe-ui",
            "execution_assignment_ref": "UNASSIGNED",
            "host_worker_capability": "AVAILABLE",
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "fallback_reason": "NONE",
            "transcript_policy": "BOUNDED_RETURNED_MESSAGES_ONLY",
            "turns": [
                {
                    "turn_id": turn_id,
                    "role": "BOSS",
                    "worker_slot_ref": "planning-boss-slot",
                    "provider": normalized_provider,
                    "model": model,
                    "reasoning_effort": "standard",
                }
            ],
        }
        with self._transient_json_file(
            {"execution_plan": execution_plan},
            prefix="planning-proposal-",
        ) as request_path:
            response = self._invoke_json_command(
                [
                    str(_required_host_executable("python")),
                    str(
                        self.repository_root
                        / ".ai"
                        / "runtime"
                        / "reference_runtime"
                        / "cli.py"
                    ),
                    "task-frame",
                    "propose",
                    "--repo-root",
                    str(self.repository_root),
                    "--profile",
                    str(PLANNING_PROFILE),
                    "--request",
                    str(request_path),
                ],
                timeout=30,
            )
        proposal = response.get("execution_proposal")
        if not isinstance(proposal, Mapping):
            raise RuntimeHostError(
                "TASK_FRAME_PROPOSAL_INVALID",
                "Task Frame CLI did not return an execution proposal",
            )
        return {
            "provider": normalized_provider,
            "model_ref": (f"provider://{normalized_provider}/model/{model}"),
            "frame_id": frame_id,
            "turn_id": turn_id,
            "execution_proposal": dict(proposal),
        }

    def invoke_structured_planning(
        self,
        *,
        runtime_binding: Mapping[str, Any],
        run: Mapping[str, Any],
        refinement_request: Mapping[str, Any],
        approval: Mapping[str, Any],
    ) -> dict[str, Any]:
        endpoint = _loopback_endpoint(runtime_binding.get("endpoint"), "endpoint")
        token = _required_text(runtime_binding.get("token"), "token")
        session_id = _required_text(runtime_binding.get("session_id"), "session_id")
        frame_id = _required_text(run.get("frame_id"), "frame_id")
        turn_id = _required_text(run.get("turn_id"), "turn_id")
        execution_proposal = _mapping(
            run.get("task_frame_execution_proposal"),
            "task_frame_execution_proposal",
        )
        request_id = _required_text(refinement_request.get("request_id"), "request_id")
        created = False
        try:
            created_result = self._post_runtime(
                endpoint,
                token,
                "/v1/task-frame/create",
                {
                    "session_id": session_id,
                    "profile": str(PLANNING_PROFILE),
                    "frame": {
                        "frame_id": frame_id,
                        "origin_anchor_ref": _required_text(
                            runtime_binding.get("origin_anchor_ref"),
                            "origin_anchor_ref",
                        ),
                        "origin_session_id": session_id,
                        "origin_frame_id": _required_text(
                            runtime_binding.get("origin_frame_id"),
                            "origin_frame_id",
                        ),
                        "task_summary_ref": (
                            f"universe://fresh-project-refinement/{request_id}#planning"
                        ),
                        "source_ref": (
                            f"universe://fresh-project-refinement/{request_id}"
                        ),
                        "execution_assignment_ref": "UNASSIGNED",
                        "task_frame_execution_proposal": execution_proposal,
                        "task_frame_execution_approval": dict(approval),
                        "parent_instruction": {
                            "instruction_id": f"instruction:{run['run_id']}",
                            "user_instruction_raw": (
                                "Refine the prepared Fresh Project composition "
                                "within the supplied structured contract."
                            ),
                            "constraints": [
                                "READ_ONLY",
                                "NO_REPOSITORY_ACCESS",
                                "STRUCTURED_JSON_ONLY",
                            ],
                            "expected_output": refinement_request["output_contract"],
                            "repository_write_scope": "NONE",
                            "mutation_scope": {"operations": [], "targets": []},
                        },
                        "parent_observation": {
                            "status": "MATCHED",
                            "evidence_ref": _required_text(
                                runtime_binding.get("parent_evidence_ref"),
                                "parent_evidence_ref",
                            ),
                        },
                        "observed_at": _utc_now(),
                    },
                },
            )
            if created_result.get("status") != "TASK_FRAME_HOST_ACTIVE":
                raise RuntimeHostError(
                    "TASK_FRAME_CREATE_FAILED",
                    _text_or(created_result.get("status"), "Task Frame create failed"),
                )
            created = True
            declared = self._post_runtime(
                endpoint,
                token,
                "/v1/task-frame/operation",
                {
                    "session_id": session_id,
                    "frame_id": frame_id,
                    "operation": {
                        "operation": "declare_turns",
                        "turns": [{"turn_id": turn_id, "role": "BOSS"}],
                        "observed_at": _utc_now(),
                    },
                },
            )
            if (
                declared.get("status") != "TASK_FRAME_OPERATION_APPLIED"
                or not isinstance(declared.get("output"), Mapping)
                or declared["output"].get("status") != "TASK_TURNS_DECLARED"
            ):
                raise RuntimeHostError(
                    "TASK_FRAME_TURN_DECLARATION_FAILED",
                    "Planning Frame turn declaration failed",
                )
            result = self.invoke_structured(
                {
                    "schema": RUNTIME_WORKER_REQUEST_SCHEMA,
                    "invocation_id": f"planning:{run['run_id']}",
                    "provider": run["provider"],
                    "endpoint": endpoint,
                    "token": token,
                    "session_id": session_id,
                    "frame_id": frame_id,
                    "turn_id": turn_id,
                    "invoker_actor_ref": runtime_binding["parent_actor_ref"],
                    "repository_write_scope": "NONE",
                    "mutation_scope": {"operations": [], "targets": []},
                    "context_pack": {
                        "schema": refinement_request["schema"],
                        "request_id": request_id,
                        "request_digest": refinement_request["request_digest"],
                        "composition_id": refinement_request["composition_id"],
                        "composition_digest": refinement_request["composition_digest"],
                        "purpose": refinement_request["purpose"],
                        "context": refinement_request["context"],
                    },
                    "output_contract": refinement_request["output_contract"],
                    "max_turns": 1,
                    "result_mode": "STRUCTURED_JSON",
                }
            )
            packet = self._post_runtime(
                endpoint,
                token,
                "/v1/task-frame/operation",
                {
                    "session_id": session_id,
                    "frame_id": frame_id,
                    "operation": {"operation": "build_result_packet"},
                },
            )
            if (
                packet.get("status") != "TASK_FRAME_OPERATION_APPLIED"
                or not isinstance(packet.get("output"), Mapping)
                or packet["output"].get("status") != "RESULT_PACKET_BUILT"
            ):
                raise RuntimeHostError(
                    "TASK_FRAME_RESULT_PACKET_FAILED",
                    "Planning Frame Result Packet was not built",
                )
            return result
        finally:
            if created:
                try:
                    self._post_runtime(
                        endpoint,
                        token,
                        "/v1/task-frame/close",
                        {"session_id": session_id, "frame_id": frame_id},
                    )
                except RuntimeHostError:
                    pass

    def invoke_conductor_message(
        self,
        *,
        runtime_binding: Mapping[str, Any],
        message: Mapping[str, Any],
        history: list[Mapping[str, Any]],
        provider: str,
    ) -> dict[str, Any]:
        normalized_provider = _required_text(provider, "provider").upper()
        capability = self.provider_capability(normalized_provider)
        if capability["status"] != "AVAILABLE":
            raise RuntimeHostError(
                "WORKER_PROVIDER_UNAVAILABLE",
                capability.get("reason", "selected provider is unavailable"),
            )
        message_id = _required_text(message.get("message_id"), "message_id")
        body = message.get("body")
        if not isinstance(body, str) or not body.strip() or len(body.strip()) > 12000:
            raise RuntimeHostError(
                "RUNTIME_WORKER_REQUEST_INVALID",
                "message.body must be bounded non-empty text",
            )
        endpoint = _loopback_endpoint(runtime_binding.get("endpoint"), "endpoint")
        token = _required_text(runtime_binding.get("token"), "token")
        session_id = _required_text(runtime_binding.get("session_id"), "session_id")
        frame_id = f"conductor-chat:{message_id}"
        turn_id = "conductor"
        source_ref = f"universe://conductor-room/messages/{message_id}"
        model = PLANNING_MODELS[normalized_provider]
        execution_plan = {
            "profile_id": "task-frame-debate-v1",
            "requested_shape": "DEBATE",
            "resolved_shape": "DEBATE",
            "model_mode": "EXPLICIT",
            "frame_id": frame_id,
            "origin_anchor_ref": _required_text(
                runtime_binding.get("origin_anchor_ref"), "origin_anchor_ref"
            ),
            "origin_session_id": session_id,
            "origin_frame_id": _required_text(
                runtime_binding.get("origin_frame_id"), "origin_frame_id"
            ),
            "task_summary_ref": source_ref,
            "source_ref": source_ref,
            "candidate_source_ref": "NONE",
            "source_review_result": None,
            "parent_actor_ref": _required_text(
                runtime_binding.get("parent_actor_ref"), "parent_actor_ref"
            ),
            "commander_surface": "universe-ui",
            "execution_assignment_ref": "UNASSIGNED",
            "host_worker_capability": "AVAILABLE",
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "fallback_reason": "NONE",
            "transcript_policy": "BOUNDED_RETURNED_MESSAGES_ONLY",
            "turns": [
                {
                    "turn_id": turn_id,
                    "role": "BOSS",
                    "worker_slot_ref": "conductor-chat-slot",
                    "provider": normalized_provider,
                    "model": model,
                    "reasoning_effort": "standard",
                }
            ],
        }
        proposal = self._build_task_frame_proposal(
            execution_plan, prefix="conductor-chat-proposal-"
        )
        approval = {
            "status": "APPROVED",
            "proposal_id": proposal["proposal_id"],
            "plan_digest": proposal["plan_digest"],
            "commander_surface": "universe-ui",
            "evidence_ref": source_ref,
        }
        bounded_history = [
            {
                "sender": str(item.get("sender") or "UNKNOWN"),
                "kind": str(item.get("kind") or "QUESTION"),
                "body": str(item.get("body") or "")[:12000],
            }
            for item in history[-12:]
            if isinstance(item, Mapping) and str(item.get("body") or "").strip()
        ]
        output_contract = {
            "schema": "universe.conductor-chat-output-contract.v1",
            "format": "PLAIN_TEXT",
            "language_policy": "MATCH_LATEST_USER_MESSAGE",
            "instruction": (
                "Answer the latest user message as the Universe Conductor. "
                "Use only the supplied bounded conversation context. "
                "Do not claim repository access, execution authority, or completed work."
            ),
        }
        created = False
        try:
            created_result = self._post_runtime(
                endpoint,
                token,
                "/v1/task-frame/create",
                {
                    "session_id": session_id,
                    "profile": str(PLANNING_PROFILE),
                    "frame": {
                        "frame_id": frame_id,
                        "origin_anchor_ref": execution_plan["origin_anchor_ref"],
                        "origin_session_id": session_id,
                        "origin_frame_id": execution_plan["origin_frame_id"],
                        "task_summary_ref": source_ref,
                        "source_ref": source_ref,
                        "execution_assignment_ref": "UNASSIGNED",
                        "task_frame_execution_proposal": proposal,
                        "task_frame_execution_approval": approval,
                        "parent_instruction": {
                            "instruction_id": f"instruction:{message_id}",
                            "user_instruction_raw": body.strip(),
                            "constraints": [
                                "READ_ONLY",
                                "NO_REPOSITORY_ACCESS",
                                "NO_SOURCE_MUTATION",
                                "NO_SUBAGENTS",
                            ],
                            "expected_output": output_contract,
                            "repository_write_scope": "NONE",
                            "mutation_scope": {"operations": [], "targets": []},
                        },
                        "parent_observation": {
                            "status": "MATCHED",
                            "evidence_ref": _required_text(
                                runtime_binding.get("parent_evidence_ref"),
                                "parent_evidence_ref",
                            ),
                        },
                        "observed_at": _utc_now(),
                    },
                },
            )
            if created_result.get("status") != "TASK_FRAME_HOST_ACTIVE":
                raise RuntimeHostError(
                    "TASK_FRAME_CREATE_FAILED",
                    _text_or(
                        created_result.get("status"),
                        "Conductor chat Task Frame create failed",
                    ),
                )
            created = True
            declared = self._post_runtime(
                endpoint,
                token,
                "/v1/task-frame/operation",
                {
                    "session_id": session_id,
                    "frame_id": frame_id,
                    "operation": {
                        "operation": "declare_turns",
                        "turns": [{"turn_id": turn_id, "role": "BOSS"}],
                        "observed_at": _utc_now(),
                    },
                },
            )
            if (
                declared.get("status") != "TASK_FRAME_OPERATION_APPLIED"
                or not isinstance(declared.get("output"), Mapping)
                or declared["output"].get("status") != "TASK_TURNS_DECLARED"
            ):
                raise RuntimeHostError(
                    "TASK_FRAME_TURN_DECLARATION_FAILED",
                    "Conductor chat turn declaration failed",
                )
            result = self.invoke_read_only(
                {
                    "schema": RUNTIME_WORKER_REQUEST_SCHEMA,
                    "invocation_id": f"conductor-chat:{message_id}",
                    "provider": normalized_provider,
                    "endpoint": endpoint,
                    "token": token,
                    "session_id": session_id,
                    "frame_id": frame_id,
                    "turn_id": turn_id,
                    "invoker_actor_ref": runtime_binding["parent_actor_ref"],
                    "repository_write_scope": "NONE",
                    "mutation_scope": {"operations": [], "targets": []},
                    "context_pack": {
                        "schema": "universe.conductor-chat-context.v1",
                        "mode": "UNIVERSE",
                        "role": "CONDUCTOR",
                        "room_id": "UNIVERSE_CONDUCTOR",
                        "history": bounded_history,
                        "latest_message": {
                            "message_id": message_id,
                            "body": body.strip(),
                        },
                    },
                    "output_contract": output_contract,
                    "max_turns": 1,
                    "result_mode": "REDACTED",
                }
            )
            packet = self._post_runtime(
                endpoint,
                token,
                "/v1/task-frame/operation",
                {
                    "session_id": session_id,
                    "frame_id": frame_id,
                    "operation": {"operation": "build_result_packet"},
                },
            )
            if (
                packet.get("status") != "TASK_FRAME_OPERATION_APPLIED"
                or not isinstance(packet.get("output"), Mapping)
                or packet["output"].get("status") != "RESULT_PACKET_BUILT"
            ):
                raise RuntimeHostError(
                    "TASK_FRAME_RESULT_PACKET_FAILED",
                    "Conductor chat Result Packet was not built",
                )
            return result
        finally:
            if created:
                try:
                    self._post_runtime(
                        endpoint,
                        token,
                        "/v1/task-frame/close",
                        {"session_id": session_id, "frame_id": frame_id},
                    )
                except RuntimeHostError:
                    pass

    @staticmethod
    def _invocation_result(
        request: Mapping[str, Any], response: Mapping[str, Any]
    ) -> dict[str, Any]:
        observation_count = response.get("skill_run_observation_count", 0)
        if (
            isinstance(observation_count, bool)
            or not isinstance(observation_count, int)
            or observation_count < 0
        ):
            observation_count = 0
        optional_details: dict[str, str] = {}
        for key in ("reason", "stage"):
            detail = response.get(key)
            if isinstance(detail, str) and detail:
                optional_details[key] = detail
        result = {
            "status": _text_or(response.get("status"), "WORKER_PROVIDER_FAILED"),
            "provider": request["provider"],
            "worker_id": _text_or(response.get("worker_id"), "UNKNOWN"),
            "result_receipt_ref": _text_or(
                response.get("result_receipt_ref"), "UNKNOWN"
            ),
            "skill_run_observation_count": observation_count,
            "repository_write": False,
            **optional_details,
        }
        returned = response.get("result")
        if isinstance(returned, Mapping):
            result["result"] = dict(returned)
        return result

    def _build_task_frame_proposal(
        self,
        execution_plan: Mapping[str, Any],
        *,
        prefix: str,
    ) -> dict[str, Any]:
        with self._transient_json_file(
            {"execution_plan": dict(execution_plan)},
            prefix=prefix,
        ) as request_path:
            response = self._invoke_json_command(
                [
                    str(_required_host_executable("python")),
                    str(
                        self.repository_root
                        / ".ai"
                        / "runtime"
                        / "reference_runtime"
                        / "cli.py"
                    ),
                    "task-frame",
                    "propose",
                    "--repo-root",
                    str(self.repository_root),
                    "--profile",
                    str(PLANNING_PROFILE),
                    "--request",
                    str(request_path),
                ],
                timeout=30,
            )
        proposal = response.get("execution_proposal")
        if not isinstance(proposal, Mapping):
            raise RuntimeHostError(
                "TASK_FRAME_PROPOSAL_INVALID",
                "Task Frame CLI did not return an execution proposal",
            )
        return dict(proposal)

    def _dispatch_worker(self, request: Mapping[str, Any]) -> dict[str, Any]:
        dispatch_request = {
            **request,
            "schema": "universe.task-frame-worker-dispatch-request.v1",
        }
        try:
            return self.worker_dispatcher.dispatch(dispatch_request)
        except WorkerDispatchError as error:
            raise RuntimeHostError(
                error.code,
                f"{error.stage}: {error.reason}",
            ) from error

    def _invoke_json_command(
        self, command: list[str], *, timeout: int
    ) -> dict[str, Any]:
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeHostError("RUNTIME_HOST_UNAVAILABLE", str(error)) from error
        stdout = str(getattr(completed, "stdout", "") or "").strip()
        returncode = int(getattr(completed, "returncode", 0) or 0)
        if not stdout:
            code = (
                "RUNTIME_HOST_TRANSPORT_FAILED"
                if returncode
                else "RUNTIME_HOST_RESPONSE_INVALID"
            )
            raise RuntimeHostError(
                code,
                f"Runtime Host dispatcher returned no JSON (exit={returncode})",
            )
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise RuntimeHostError(
                "RUNTIME_HOST_RESPONSE_INVALID",
                f"Runtime Host returned invalid JSON (exit={returncode})",
            ) from error
        if not isinstance(result, dict):
            raise RuntimeHostError(
                "RUNTIME_HOST_RESPONSE_INVALID", "Runtime Host JSON must be an object"
            )
        if returncode:
            code = _text_or(
                result.get("status"),
                "RUNTIME_HOST_TRANSPORT_FAILED",
            )
            reason = _text_or(result.get("reason"), code)
            stage = result.get("stage")
            detail = (
                f"{stage}: {reason}" if isinstance(stage, str) and stage else reason
            )
            raise RuntimeHostError(code, detail)
        return result

    @staticmethod
    def _post_runtime(
        endpoint: str,
        token: str,
        path: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request = Request(
            endpoint.rstrip("/") + path,
            data=_canonical_json(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Anchor-Session-Memory-Token": token,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:  # nosec B310
                value = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as error:
            raise RuntimeHostError("RUNTIME_HOST_UNAVAILABLE", str(error)) from error
        if not isinstance(value, dict):
            raise RuntimeHostError(
                "RUNTIME_HOST_RESPONSE_INVALID",
                "Runtime Host HTTP response must be an object",
            )
        return value

    @staticmethod
    def _transient_json_file(
        value: Mapping[str, Any], *, prefix: str
    ) -> "_TransientRequestFile":
        root = (
            Path(
                os.environ.get("LOCALAPPDATA")
                or os.environ.get("TEMP")
                or tempfile.gettempdir()
            )
            / "Universe"
            / "runtime-tmp"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix=prefix,
            dir=root,
            delete=False,
        ) as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            return _TransientRequestFile(Path(handle.name))


class _TransientRequestFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *_: object) -> None:
        self.path.unlink(missing_ok=True)


def _text_or(value: Any, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
