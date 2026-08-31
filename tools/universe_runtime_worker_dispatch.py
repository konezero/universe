from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from agent_session_gateway import (
    AgentSessionError,
    ClaudeCodeSession,
    CodexAppServerSession,
    GrokAcpSession,
    UniverseAcpGateway,
    cli_auto_approve_status,
)
from claude_permission_bridge import ClaudePermissionBridge
from claude_permission_broker import ClaudePermissionBroker
from claude_resident_session import ClaudeResidentError, ClaudeResidentSession
from host_profile import resolve_host_tool
from universe_app.terminal_host import TerminalHostError
from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli
from worker_failure_evidence import (
    WorkerFailureEvidenceError,
    WorkerFailureEvidenceStore,
)


DISPATCH_SCHEMA = "universe.task-frame-worker-dispatch-request.v1"
SUPPORTED_PROVIDERS = frozenset({"GROK", "CODEX", "CLAUDE"})
PROVIDER_ALIASES = {
    "OPENAI": "CODEX",
    "ANTHROPIC": "CLAUDE",
    "XAI": "GROK",
}
RESULT_MODES = frozenset({"REDACTED", "STRUCTURED_JSON"})
TASK_FRAME_WORKER_RESPONSE_TIMEOUT_SECONDS = 90
READ_ONLY_WORKER_RESPONSE_TIMEOUT_SECONDS = 240
QA_WORKER_RESPONSE_TIMEOUT_SECONDS = 900


@dataclass(frozen=True)
class WorkerDispatchError(Exception):
    code: str
    stage: str
    reason: str
    host_evidence_ref: str = ""
    recovery_status: str = ""

    def __str__(self) -> str:
        detail = f"{self.stage}: {self.reason}"
        if self.host_evidence_ref:
            detail += f" [host_evidence_ref={self.host_evidence_ref}]"
        if self.recovery_status:
            detail += f" [recovery_status={self.recovery_status}]"
        return detail


NativeRunner = Callable[[NativeCliRequest], NativeCliResult]
PostJson = Callable[[str, str, str, Mapping[str, Any]], dict[str, Any]]
WorkerHostCoordinateResolver = Callable[[str, str, str], Mapping[str, Any]]


def _worker_response_timeout_seconds(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or value > 300
    ):
        raise ValueError("worker response timeout must be between 0 and 300 seconds")
    return float(value)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerDispatchError(
            "WORKER_TRANSPORT_FAILED",
            "REQUEST_LOAD",
            f"{field.upper()}_REQUIRED",
        )
    return value.strip()


def _canonical_provider(value: Any, field: str = "provider") -> str:
    """Map Runtime provider names to the local CLI adapter names."""

    normalized = _required_text(value, field).upper()
    return PROVIDER_ALIASES.get(normalized, normalized)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkerDispatchError(
            "WORKER_TRANSPORT_FAILED",
            "REQUEST_LOAD",
            f"{field.upper()}_OBJECT_REQUIRED",
        )
    return dict(value)


def _structured_text(value: Any, field: str) -> str:
    if isinstance(value, str):
        return _required_text(value, field)
    if value is None:
        raise WorkerDispatchError(
            "WORKER_TRANSPORT_FAILED",
            "WORKER_ADAPTER",
            f"{field.upper()}_REQUIRED",
        )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loopback_endpoint(value: Any) -> str:
    endpoint = _required_text(value, "endpoint")
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError as error:
        raise WorkerDispatchError(
            "RUNTIME_HOST_ENDPOINT_INVALID",
            "REQUEST_LOAD",
            "ENDPOINT_PORT_INVALID",
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
        raise WorkerDispatchError(
            "RUNTIME_HOST_ENDPOINT_INVALID",
            "REQUEST_LOAD",
            "LOOPBACK_ENDPOINT_REQUIRED",
        )
    return endpoint.rstrip("/")


def post_json(
    endpoint: str,
    token: str,
    path: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    request = Request(
        endpoint.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Anchor-Session-Memory-Token": token,
        },
        method="POST",
    )
    try:
        # _loopback_endpoint validates the origin before this call.
        with urlopen(request, timeout=30) as response:  # nosec B310
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WorkerDispatchError(
            "WORKER_TRANSPORT_FAILED",
            "TASK_FRAME_HTTP",
            "TASK_FRAME_ENDPOINT_UNAVAILABLE",
        ) from error
    if not isinstance(result, dict):
        raise WorkerDispatchError(
            "WORKER_TRANSPORT_FAILED",
            "TASK_FRAME_HTTP",
            "TASK_FRAME_RESPONSE_INVALID",
        )
    return result


def _resolve_grok() -> tuple[Path | None, dict[str, str], str]:
    resolved = resolve_host_tool("grok")
    if resolved is None:
        return None, {}, "UNKNOWN"
    return resolved.executable, dict(resolved.environment), resolved.model


def _resolve_codex() -> tuple[Path | None, dict[str, str], str]:
    resolved = resolve_host_tool("codex")
    if resolved is None:
        return None, {}, "UNKNOWN"
    return resolved.executable, dict(resolved.environment), resolved.model


def _resolve_claude() -> tuple[Path | None, dict[str, str], str]:
    resolved = resolve_host_tool("claude")
    if resolved is None:
        return None, {}, "UNKNOWN"
    return resolved.executable, dict(resolved.environment), resolved.model


def _provider_executable(
    provider: str,
) -> tuple[Path | None, dict[str, str], str]:
    if provider == "GROK":
        return _resolve_grok()
    if provider == "CODEX":
        return _resolve_codex()
    if provider == "CLAUDE":
        return _resolve_claude()
    return None, {}, "UNKNOWN"


class RuntimeWorkerDispatcher:
    def __init__(
        self,
        repository_root: Path,
        *,
        native_runner: NativeRunner = run_native_cli,
        post: PostJson = post_json,
        failure_evidence_store: WorkerFailureEvidenceStore | None = None,
        worker_response_timeout_seconds: float = TASK_FRAME_WORKER_RESPONSE_TIMEOUT_SECONDS,
        terminal_host: Any | None = None,
        project_id: str = "",
        mode: str = "MASTER",
        worker_host_coordinate_resolver: WorkerHostCoordinateResolver | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.native_runner = native_runner
        self.post = post
        self.failure_evidence_store = failure_evidence_store
        self.terminal_host = terminal_host
        self.project_id = str(project_id or "").strip()
        self.mode = str(mode or "MASTER").strip().upper()
        self.worker_host_coordinate_resolver = worker_host_coordinate_resolver
        self.worker_response_timeout_seconds = _worker_response_timeout_seconds(
            worker_response_timeout_seconds
        )

    def provider_capability(self, provider: str) -> dict[str, str]:
        normalized = _canonical_provider(provider)
        if normalized not in SUPPORTED_PROVIDERS:
            return {
                "status": "UNAVAILABLE",
                "provider": normalized,
                "reason": "WORKER_PROVIDER_UNSUPPORTED",
            }
        executable, environment, model = _provider_executable(normalized)
        if executable is None:
            return {
                "status": "UNAVAILABLE",
                "provider": normalized,
                "reason": f"{normalized}_CLI_UNAVAILABLE",
            }
        result = self.native_runner(
            NativeCliRequest(
                executable=executable,
                arguments=("--version",),
                cwd=self.repository_root,
                timeout_seconds=20,
                environment=environment,
            )
        )
        version = (result.stdout or result.stderr).strip()
        if result.status != "COMPLETED" or not version:
            return {
                "status": "UNAVAILABLE",
                "provider": normalized,
                "reason": f"{normalized}_CLI_LAUNCH_FAILED",
            }
        encoded_path = base64.b64encode(str(executable).encode("utf-8")).decode("ascii")
        return {
            "status": "AVAILABLE",
            "provider": normalized,
            "model": model,
            "model_ref": f"provider://{normalized}/model/{quote(model, safe='')}",
            "cli_auto_approve": cli_auto_approve_status(normalized),
            "capability_evidence_ref": (
                f"{normalized.lower()}-cli:{encoded_path}:{version}"
            ),
        }

    def dispatch(self, raw_request: Mapping[str, Any]) -> dict[str, Any]:
        request = self._normalize_request(raw_request)
        provider = request["provider"]
        capability = self.provider_capability(provider)
        if capability["status"] != "AVAILABLE":
            raise WorkerDispatchError(
                "WORKER_INVOCATION_UNAVAILABLE",
                "CAPABILITY_CHECK",
                capability.get("reason", "WORKER_PROVIDER_UNAVAILABLE"),
            )

        plan = self.post(
            request["endpoint"],
            request["token"],
            "/v1/task-frame/operation",
            {
                "session_id": request["session_id"],
                "frame_id": request["frame_id"],
                "operation": {
                    "operation": "worker_invocation_plan",
                    "turn_id": request["turn_id"],
                    "host_capability_status": "AVAILABLE",
                    "capability_evidence_ref": capability["capability_evidence_ref"],
                    "invoker_actor_ref": request["invoker_actor_ref"],
                    "observed_at": _utc_now(),
                },
            },
        )
        if (
            plan.get("status") != "TASK_FRAME_OPERATION_APPLIED"
            or not isinstance(plan.get("output"), Mapping)
            or plan["output"].get("status") != "WORKER_INVOCATION_READY"
        ):
            raise WorkerDispatchError(
                "WORKER_INVOCATION_NOT_READY",
                "TASK_FRAME_PLAN",
                "TASK_FRAME_PLAN_REJECTED",
            )
        planned_invocation = _mapping(
            plan["output"].get("worker_invocation"),
            "worker_invocation",
        )
        planned_provider_value = planned_invocation.get("provider", "")
        planned_provider = (
            _canonical_provider(planned_provider_value, "worker_invocation.provider")
            if isinstance(planned_provider_value, str) and planned_provider_value.strip()
            else ""
        )
        if planned_provider and planned_provider != provider:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_PLAN_MISMATCH",
                "TASK_FRAME_PLAN",
                "PLANNED_PROVIDER_MISMATCH",
            )
        # Provider capability proves that this Host can launch the selected
        # provider. The Task Frame, created under the owning Node/Mode Master,
        # is the authority for the declared model of this turn.
        planned_model = _required_text(planned_invocation.get("model"), "model")
        planned_effort = str(
            planned_invocation.get("reasoning_effort") or "AUTO"
        ).strip().upper()
        if request["defer_terminal_result"] and planned_invocation.get("role") != "BOSS":
            raise WorkerDispatchError(
                "WORKER_DEFERRED_RESULT_INVALID",
                "TASK_FRAME_PLAN",
                "DEFERRED_RESULT_REQUIRES_ROOT_BOSS",
            )

        skill_bindings = self._skill_bindings(planned_invocation)
        worker_id = f"universe-runtime-worker:{uuid4().hex}"
        worker_run_ref = f"universe-runtime-host:{uuid4().hex}"
        supervisor_transport = self._supervisor_transport(provider, request)
        worker_request = {
            "schema": {
                "GROK": "universe.grok-worker-request.v1",
                "CODEX": "universe.codex-worker-request.v1",
                "CLAUDE": "universe.claude-worker-request.v1",
            }[provider],
            "runtime_profile": "TASK_FRAME_RUNTIME",
            "model": planned_model,
            "effort": planned_effort,
            "task_frame_id": request["frame_id"],
            "turn_id": request["turn_id"],
            "worker_id": worker_id,
            "worker_run_ref": worker_run_ref,
            "repository_write_scope": str(
                request.get("repository_write_scope") or "NONE"
            ).upper(),
            "mutation_scope": request["mutation_scope"],
            "context_pack": request["context_pack"],
            "output_contract": request["output_contract"],
            "response_timeout_seconds": self._response_timeout_seconds_for(request),
            "result_mode": request["result_mode"],
            "supervisor_transport": supervisor_transport,
        }

        claim = self.post(
            request["endpoint"],
            request["token"],
            "/v1/task-frame/operation",
            {
                "session_id": request["session_id"],
                "frame_id": request["frame_id"],
                "operation": {
                    "operation": "claim_turn",
                    "turn_id": request["turn_id"],
                    "worker_id": worker_id,
                    "worker_run_ref": worker_run_ref,
                    "capability_evidence_ref": capability["capability_evidence_ref"],
                    "invoker_actor_ref": request["invoker_actor_ref"],
                    "observed_at": _utc_now(),
                },
            },
        )
        if (
            claim.get("status") != "TASK_FRAME_OPERATION_APPLIED"
            or not isinstance(claim.get("output"), Mapping)
            or claim["output"].get("status") != "TURN_CLAIMED"
        ):
            raise WorkerDispatchError(
                "WORKER_CLAIM_FAILED",
                "TASK_FRAME_CLAIM",
                "TURN_CLAIM_REJECTED",
            )
        claimed_turn = claim["output"].get("turn")
        if (
            not isinstance(claimed_turn, Mapping)
            or claimed_turn.get("turn_id") != request["turn_id"]
            or claimed_turn.get("state") != "CLAIMED"
            or claimed_turn.get("claimed_by") != worker_id
        ):
            raise WorkerDispatchError(
                "WORKER_CLAIM_EVIDENCE_MISMATCH",
                "TASK_FRAME_CLAIM",
                "CLAIMED_TURN_IDENTITY_MISMATCH",
            )

        try:
            started = time.monotonic()
            worker = self._invoke_provider(provider, worker_request)
            duration_ms = round((time.monotonic() - started) * 1000, 3)
            if worker.get("status") != "COMPLETED":
                raise WorkerDispatchError(
                    "WORKER_PROVIDER_FAILED",
                    "WORKER_ADAPTER",
                    "WORKER_DID_NOT_COMPLETE",
                )
            if worker.get("worker_run_ref") != worker_run_ref:
                raise WorkerDispatchError(
                    "WORKER_RUN_REF_MISMATCH",
                    "WORKER_ADAPTER",
                    "WORKER_RUN_REF_MISMATCH",
                )
            if (
                worker.get("session_persistence") != "EPHEMERAL"
                or worker.get("persistent_session_ref") != "UNKNOWN"
                or worker.get("universe_coordinate_persisted")
                is not bool(supervisor_transport)
            ):
                raise WorkerDispatchError(
                    "WORKER_SESSION_BOUNDARY_INVALID",
                    "WORKER_ADAPTER",
                    "EPHEMERAL_SESSION_ATTESTATION_REQUIRED",
                )
            provider_worker_ref = _required_text(
                worker.get("worker_id"), "worker.worker_id"
            )
            result_receipt_ref = _required_text(
                worker.get("result_receipt_ref"), "worker.result_receipt_ref"
            )

            recorded_result: Any = worker.get("result")
            structured_result: dict[str, Any] | None = None
            if request["result_mode"] == "STRUCTURED_JSON":
                structured_result = self._parse_structured_worker_result(
                    worker,
                    request["output_contract"],
                )
                recorded_result = structured_result

            model = planned_model
            model_ref = f"provider://{provider}/model/{quote(model, safe='')}"
            observations = [
                {
                    "skill_binding_digest": _required_text(
                        binding.get("skill_binding_digest"),
                        "skill_binding_digest",
                    ),
                    "model_ref": model_ref,
                    "outcome": "SUCCEEDED",
                    "validation_state": "NOT_RUN",
                    "evidence_refs": [result_receipt_ref],
                    "metrics": {"duration_ms": duration_ms},
                }
                for binding in skill_bindings
            ]
            envelope = {
                "turn_id": request["turn_id"],
                "worker_id": worker_id,
                "worker_run_ref": worker_run_ref,
                "result_receipt_ref": result_receipt_ref,
                "status": worker["status"],
                "evidence_refs": [result_receipt_ref],
                "result": recorded_result,
                "review_decision": "",
            }
            if observations:
                envelope["skill_run_observations"] = observations
            if request["defer_terminal_result"]:
                return {
                    "status": "WORKER_OUTPUT_CAPTURED",
                    "provider": provider,
                    "model_ref": model_ref,
                    "worker_id": worker_id,
                    "provider_worker_ref": provider_worker_ref,
                    "worker_run_ref": worker_run_ref,
                    "result_receipt_ref": result_receipt_ref,
                    "terminal_result_verified": True,
                    "duration_ms": duration_ms,
                    "result": recorded_result,
                    "structured_result": structured_result,
                    "skill_run_observation_count": len(observations),
                    "repository_write": False,
                    "session_persistence": worker["session_persistence"],
                    "persistent_session_ref": worker["persistent_session_ref"],
                    "universe_coordinate_persisted": worker[
                        "universe_coordinate_persisted"
                    ],
                    "provider_durable_chat_state": worker.get(
                        "provider_durable_chat_state", "UNKNOWN"
                    ),
                    "host_session_ref": worker.get("host_session_ref", "UNKNOWN"),
                    "session_anchor_ref": worker.get("session_anchor_ref", "UNKNOWN"),
                    "worker_envelope": envelope,
                }
            result = self.post(
                request["endpoint"],
                request["token"],
                "/v1/task-frame/worker-result",
                {
                    "session_id": request["session_id"],
                    "frame_id": request["frame_id"],
                    "envelope": envelope,
                    "observed_at": _utc_now(),
                },
            )
            terminal_status = str(result.get("status") or "").upper()
            if not (
                terminal_status in {"TASK_COMPLETED", "TASK_FRAME_RESULT_RECORDED"}
                or terminal_status.startswith("TURN_COMPLETED")
            ):
                raise WorkerDispatchError(
                    "WORKER_RESULT_NOT_RECORDED",
                    "TASK_FRAME_RESULT",
                    terminal_status or "TASK_FRAME_RESULT_REJECTED",
                )
        except Exception as error:
            failure = (
                error
                if isinstance(error, WorkerDispatchError)
                else WorkerDispatchError(
                    "WORKER_INITIALIZATION_UNEXPECTED_FAILURE",
                    "WORKER_ADAPTER",
                    type(error).__name__,
                )
            )
            raise self._recover_claim_failure(
                request=request,
                worker_id=worker_id,
                worker_run_ref=worker_run_ref,
                failure=failure,
            ) from error

        response = {
            "status": result.get("status", "WORKER_PROVIDER_FAILED"),
            "provider": provider,
            "model_ref": model_ref,
            "worker_id": worker_id,
            "provider_worker_ref": provider_worker_ref,
            "worker_run_ref": worker_run_ref,
            "result_receipt_ref": result_receipt_ref,
            "terminal_result_verified": True,
            "task_frame_result_status": terminal_status,
            "duration_ms": duration_ms,
            "result": recorded_result,
            "skill_run_observation_count": len(observations),
            "repository_write": False,
            "session_persistence": worker["session_persistence"],
            "persistent_session_ref": worker["persistent_session_ref"],
            "universe_coordinate_persisted": worker[
                "universe_coordinate_persisted"
            ],
            "provider_durable_chat_state": worker.get(
                "provider_durable_chat_state", "UNKNOWN"
            ),
            "host_session_ref": worker.get("host_session_ref", "UNKNOWN"),
            "session_anchor_ref": worker.get("session_anchor_ref", "UNKNOWN"),
            "runtime_result": result,
        }
        if structured_result is not None:
            response["structured_result"] = structured_result
        return response

    def record_captured_result(
        self,
        raw_request: Mapping[str, Any],
        envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Commit one previously captured root-Boss result after child turns finish."""

        request = self._normalize_request(raw_request)
        if not request["defer_terminal_result"]:
            raise WorkerDispatchError(
                "WORKER_DEFERRED_RESULT_INVALID",
                "RESULT_COMMIT",
                "DEFERRED_RESULT_NOT_REQUESTED",
            )
        normalized_envelope = _mapping(envelope, "worker_envelope")
        if normalized_envelope.get("turn_id") != request["turn_id"]:
            raise WorkerDispatchError(
                "WORKER_DEFERRED_RESULT_INVALID",
                "RESULT_COMMIT",
                "DEFERRED_RESULT_TURN_MISMATCH",
            )
        result = self.post(
            request["endpoint"],
            request["token"],
            "/v1/task-frame/worker-result",
            {
                "session_id": request["session_id"],
                "frame_id": request["frame_id"],
                "envelope": normalized_envelope,
                "observed_at": _utc_now(),
            },
        )
        terminal_status = str(result.get("status") or "").upper()
        if terminal_status not in {"TASK_COMPLETED", "TASK_FRAME_RESULT_RECORDED"} and not terminal_status.startswith("TURN_COMPLETED"):
            raise WorkerDispatchError(
                "WORKER_RESULT_NOT_RECORDED",
                "TASK_FRAME_RESULT",
                terminal_status or "TASK_FRAME_RESULT_REJECTED",
            )
        return {
            "status": terminal_status,
            "task_frame_result_status": terminal_status,
            "runtime_result": result,
            "repository_write": False,
        }

    def recover_claimed_worker(
        self,
        raw_request: Mapping[str, Any],
        *,
        worker_id: str,
        worker_run_ref: str,
        failure_code: str,
        failure_reason: str,
    ) -> dict[str, Any]:
        """Fail-close a claimed Worker when its Parent cannot resume it."""

        request = self._normalize_request(raw_request)
        failure = WorkerDispatchError(
            _required_text(failure_code, "failure_code"),
            "PARENT_FINALIZATION",
            _required_text(failure_reason, "failure_reason"),
        )
        recovered = self._recover_claim_failure(
            request=request,
            worker_id=_required_text(worker_id, "worker_id"),
            worker_run_ref=_required_text(worker_run_ref, "worker_run_ref"),
            failure=failure,
        )
        if recovered.recovery_status not in {
            "WORKER_INITIALIZATION_FAILURE_RECORDED",
            "WORKER_INITIALIZATION_FAILURE_REPLAYED",
        }:
            raise recovered
        return {
            "status": "WORKER_CLAIM_RECOVERED",
            "failure_code": failure.code,
            "host_evidence_ref": recovered.host_evidence_ref,
            "recovery_status": recovered.recovery_status,
            "repository_write": False,
        }

    def _recover_claim_failure(
        self,
        *,
        request: Mapping[str, Any],
        worker_id: str,
        worker_run_ref: str,
        failure: WorkerDispatchError,
    ) -> WorkerDispatchError:
        observed_at = _utc_now()
        source = json.dumps(
            {
                "code": failure.code,
                "stage": failure.stage,
                "reason": failure.reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if self.failure_evidence_store is None:
            return WorkerDispatchError(
                "WORKER_FAILURE_EVIDENCE_UNAVAILABLE",
                "HOST_FAILURE_EVIDENCE",
                "durable failure evidence store is not configured",
            )
        try:
            evidence = self.failure_evidence_store.record_live_failure(
                repository_ref=self.repository_root.as_uri(),
                session_id=str(request["session_id"]),
                frame_id=str(request["frame_id"]),
                turn_id=str(request["turn_id"]),
                worker_id=worker_id,
                worker_run_ref=worker_run_ref,
                failure_code=failure.code,
                failure_detail=f"{failure.stage}: {failure.reason}",
                source_locator=(
                    "universe://runtime-worker-failure/"
                    f"{quote(str(request['frame_id']), safe='')}/"
                    f"{quote(str(request['turn_id']), safe='')}/"
                    f"{quote(worker_run_ref, safe='')}"
                ),
                source_content=source,
                failure_observed_at=observed_at,
            )
        except WorkerFailureEvidenceError as error:
            return WorkerDispatchError(
                "WORKER_FAILURE_EVIDENCE_WRITE_FAILED",
                "HOST_FAILURE_EVIDENCE",
                str(error),
            )

        host_evidence_ref = str(evidence["host_evidence_ref"])
        try:
            recovery = self.post(
                str(request["endpoint"]),
                str(request["token"]),
                "/v1/task-frame/operation",
                {
                    "session_id": request["session_id"],
                    "frame_id": request["frame_id"],
                    "operation": {
                        "operation": "worker_initialization_failed",
                        "turn_id": request["turn_id"],
                        "worker_id": worker_id,
                        "worker_run_ref": worker_run_ref,
                        "failure_code": failure.code,
                        "failure_detail": f"{failure.stage}: {failure.reason}",
                        "host_evidence_ref": host_evidence_ref,
                        "observed_at": observed_at,
                    },
                },
            )
        except Exception as error:
            return WorkerDispatchError(
                "WORKER_INITIALIZATION_RECOVERY_FAILED",
                "TASK_FRAME_RECOVERY",
                type(error).__name__,
                host_evidence_ref=host_evidence_ref,
                recovery_status="RECOVERY_TRANSPORT_FAILED",
            )

        output = recovery.get("output")
        recovery_status = (
            str(output.get("status"))
            if isinstance(output, Mapping)
            else str(recovery.get("status") or "UNKNOWN")
        )
        if (
            recovery.get("status") != "TASK_FRAME_OPERATION_APPLIED"
            or recovery_status
            not in {
                "WORKER_INITIALIZATION_FAILURE_RECORDED",
                "WORKER_INITIALIZATION_FAILURE_REPLAYED",
            }
        ):
            return WorkerDispatchError(
                "WORKER_INITIALIZATION_RECOVERY_FAILED",
                "TASK_FRAME_RECOVERY",
                recovery_status,
                host_evidence_ref=host_evidence_ref,
                recovery_status=recovery_status,
            )
        return WorkerDispatchError(
            failure.code,
            failure.stage,
            failure.reason,
            host_evidence_ref=host_evidence_ref,
            recovery_status=recovery_status,
        )

    def _normalize_request(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if raw.get("schema") != DISPATCH_SCHEMA:
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "REQUEST_LOAD",
                "DISPATCH_SCHEMA_INVALID",
            )
        provider = _canonical_provider(raw.get("provider"))
        if provider not in SUPPORTED_PROVIDERS:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_UNSUPPORTED",
                "CAPABILITY_CHECK",
                "WORKER_PROVIDER_UNSUPPORTED",
            )
        result_mode = str(raw.get("result_mode", "REDACTED")).strip().upper()
        if result_mode not in RESULT_MODES:
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "REQUEST_LOAD",
                "RESULT_MODE_INVALID",
            )

        repository_write_scope = str(raw.get("repository_write_scope") or "").strip().upper()
        if repository_write_scope not in {"NONE", "BOUNDED"}:
            raise WorkerDispatchError(
                "WORKER_MUTATION_SCOPE_INVALID",
                "REQUEST_LOAD",
                "REPOSITORY_WRITE_SCOPE_INVALID",
            )
        mutation_scope = _mapping(raw.get("mutation_scope"), "mutation_scope")
        if set(mutation_scope) != {"operations", "targets"}:
            raise WorkerDispatchError(
                "WORKER_MUTATION_SCOPE_INVALID",
                "REQUEST_LOAD",
                "MUTATION_SCOPE_SHAPE_INVALID",
            )
        operations_value = mutation_scope.get("operations")
        targets_value = mutation_scope.get("targets")
        if not isinstance(operations_value, list) or not isinstance(targets_value, list):
            raise WorkerDispatchError(
                "WORKER_MUTATION_SCOPE_INVALID",
                "REQUEST_LOAD",
                "MUTATION_SCOPE_LISTS_REQUIRED",
            )
        operations: list[str] = []
        for value in operations_value:
            if not isinstance(value, str) or not value.strip():
                raise WorkerDispatchError(
                    "WORKER_MUTATION_SCOPE_INVALID",
                    "REQUEST_LOAD",
                    "MUTATION_OPERATION_INVALID",
                )
            operation = value.strip().upper()
            if operation not in {"CREATE", "MODIFY", "DELETE"} or operation in operations:
                raise WorkerDispatchError(
                    "WORKER_MUTATION_SCOPE_INVALID",
                    "REQUEST_LOAD",
                    "MUTATION_OPERATION_INVALID",
                )
            operations.append(operation)
        targets: list[str] = []
        for value in targets_value:
            if not isinstance(value, str) or not value.strip():
                raise WorkerDispatchError(
                    "WORKER_MUTATION_SCOPE_INVALID",
                    "REQUEST_LOAD",
                    "MUTATION_TARGET_INVALID",
                )
            target = value.strip()
            if not Path(target).is_absolute() or target in targets:
                raise WorkerDispatchError(
                    "WORKER_MUTATION_SCOPE_INVALID",
                    "REQUEST_LOAD",
                    "MUTATION_TARGET_INVALID",
                )
            targets.append(target)
        if bool(operations) != bool(targets):
            raise WorkerDispatchError(
                "WORKER_MUTATION_SCOPE_INVALID",
                "REQUEST_LOAD",
                "MUTATION_SCOPE_INCOMPLETE",
            )
        if repository_write_scope == "NONE" and (operations or targets):
            raise WorkerDispatchError(
                "WORKER_MUTATION_SCOPE_INVALID",
                "REQUEST_LOAD",
                "READ_ONLY_SCOPE_REQUIRED",
            )
        if repository_write_scope == "BOUNDED" and not operations:
            raise WorkerDispatchError(
                "WORKER_MUTATION_SCOPE_INVALID",
                "REQUEST_LOAD",
                "BOUNDED_MUTATION_SCOPE_REQUIRED",
            )

        defer_terminal_result = raw.get("defer_terminal_result", False)
        if not isinstance(defer_terminal_result, bool):
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "REQUEST_LOAD",
                "DEFERRED_RESULT_INVALID",
            )
        return {
            "provider": provider,
            "endpoint": _loopback_endpoint(raw.get("endpoint")),
            "token": _required_text(raw.get("token"), "token"),
            "session_id": _required_text(raw.get("session_id"), "session_id"),
            "frame_id": _required_text(raw.get("frame_id"), "frame_id"),
            "turn_id": _required_text(raw.get("turn_id"), "turn_id"),
            "invoker_actor_ref": _required_text(
                raw.get("invoker_actor_ref"), "invoker_actor_ref"
            ),
            "repository_write_scope": repository_write_scope,
            "mutation_scope": {"operations": operations, "targets": targets},
            "context_pack": _mapping(raw.get("context_pack"), "context_pack"),
            "output_contract": _mapping(raw.get("output_contract"), "output_contract"),
            "result_mode": result_mode,
            "defer_terminal_result": defer_terminal_result,
        }

    @classmethod
    def _parse_structured_worker_result(
        cls,
        worker: Mapping[str, Any],
        output_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        result_object = _mapping(worker.get("result"), "worker.result")
        text = _required_text(result_object.get("text"), "worker.result.text")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise WorkerDispatchError(
                "WORKER_STRUCTURED_RESULT_INVALID",
                "WORKER_ADAPTER",
                "WORKER_RESULT_JSON_INVALID",
            ) from error
        if not isinstance(parsed, dict):
            raise WorkerDispatchError(
                "WORKER_STRUCTURED_RESULT_INVALID",
                "WORKER_ADAPTER",
                "WORKER_RESULT_OBJECT_REQUIRED",
            )
        cls._validate_json_schema_subset(parsed, output_contract.get("json_schema"))
        cls._validate_structured_result(parsed, output_contract)
        return parsed

    @classmethod
    def _validate_json_schema_subset(cls, value: Any, raw_schema: Any) -> None:
        if not isinstance(raw_schema, Mapping):
            return

        def invalid(reason: str) -> None:
            raise WorkerDispatchError(
                "WORKER_STRUCTURED_RESULT_INVALID",
                "WORKER_ADAPTER",
                reason,
            )

        expected = raw_schema.get("type")
        type_checks = {
            "object": lambda item: isinstance(item, Mapping),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if isinstance(expected, str) and expected in type_checks and not type_checks[expected](value):
            invalid("WORKER_RESULT_SCHEMA_TYPE_INVALID")
        if isinstance(value, Mapping):
            required = raw_schema.get("required")
            if isinstance(required, list):
                for key in required:
                    if isinstance(key, str) and key not in value:
                        invalid("WORKER_RESULT_SCHEMA_REQUIRED_FIELD_MISSING")
            properties = raw_schema.get("properties")
            if isinstance(properties, Mapping):
                for key, child_schema in properties.items():
                    if key in value:
                        cls._validate_json_schema_subset(value[key], child_schema)
                if raw_schema.get("additionalProperties") is False:
                    if any(key not in properties for key in value):
                        invalid("WORKER_RESULT_SCHEMA_ADDITIONAL_PROPERTY")
        if isinstance(value, list) and isinstance(raw_schema.get("items"), Mapping):
            for item in value:
                cls._validate_json_schema_subset(item, raw_schema["items"])

    def invoke_structured_provider(
        self,
        provider: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        # Validate a claimed provider turn without bypassing the output contract.
        normalized = PROVIDER_ALIASES.get(str(provider).strip().upper(), str(provider).strip().upper())
        if str(request.get("result_mode", "")).upper() != "STRUCTURED_JSON":
            raise WorkerDispatchError(
                "WORKER_STRUCTURED_RESULT_REQUIRED",
                "WORKER_ADAPTER",
                "STRUCTURED_JSON_RESULT_MODE_REQUIRED",
            )
        output_contract = _mapping(request.get("output_contract"), "output_contract")
        worker = self._invoke_provider(normalized, request)
        structured_result = self._parse_structured_worker_result(worker, output_contract)
        return {**worker, "structured_result": structured_result}

    @staticmethod
    def _validate_structured_result(
        result: Mapping[str, Any], output_contract: Mapping[str, Any]
    ) -> None:
        if output_contract.get("schema") != "universe.task-frame-child-result.v1":
            return

        def invalid(reason: str) -> None:
            raise WorkerDispatchError(
                "WORKER_STRUCTURED_RESULT_INVALID",
                "WORKER_ADAPTER",
                reason,
            )

        def evidence_refs(value: Any, reason: str) -> list[str]:
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                invalid(reason)
            return [item.strip() for item in value]

        if result.get("outcome") != "SUCCEEDED":
            invalid("WORKER_OUTCOME_NOT_SUCCEEDED")
        if not isinstance(result.get("summary"), str) or not result["summary"].strip():
            invalid("WORKER_SUMMARY_REQUIRED")
        evidence_refs(result.get("evidence_refs"), "WORKER_EVIDENCE_REQUIRED")
        validation = result.get("validation")
        if not isinstance(validation, list) or not validation:
            invalid("WORKER_VALIDATION_REQUIRED")
        pass_seen = False
        fail_seen = False
        mutation_evidence_required = (
            output_contract.get("mutation_evidence_required") is True
        )
        for item in validation:
            if not isinstance(item, Mapping):
                invalid("WORKER_VALIDATION_ENTRY_INVALID")
            if not isinstance(item.get("plane"), str) or not item["plane"].strip():
                invalid("WORKER_VALIDATION_PLANE_REQUIRED")
            state = item.get("state")
            if state not in {"PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE"}:
                invalid("WORKER_VALIDATION_STATE_INVALID")
            evidence_refs(
                item.get("evidence_refs"),
                "WORKER_VALIDATION_EVIDENCE_REQUIRED",
            )
            if state == "FAIL" and mutation_evidence_required:
                invalid("WORKER_VALIDATION_FAILED")
            pass_seen = pass_seen or state == "PASS"
            fail_seen = fail_seen or state == "FAIL"
        if mutation_evidence_required and not pass_seen:
            invalid("WORKER_VALIDATION_PASS_REQUIRED")
        if not mutation_evidence_required and not (pass_seen or fail_seen):
            invalid("WORKER_REVIEW_CONCLUSION_REQUIRED")
        if mutation_evidence_required:
            mutation_evidence = evidence_refs(
                result.get("mutation_evidence_refs"),
                "WORKER_MUTATION_EVIDENCE_REQUIRED",
            )
            if any("no-mutation-performed" in item for item in mutation_evidence):
                invalid("WORKER_MUTATION_NOT_PERFORMED")
    @staticmethod
    def _skill_bindings(
        planned_invocation: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        input_bundle = planned_invocation.get("input_bundle")
        if not isinstance(input_bundle, Mapping):
            return []
        allocation = input_bundle.get("boss_allocation")
        if not isinstance(allocation, Mapping):
            return []
        bindings = allocation.get("skill_bindings")
        if not isinstance(bindings, list):
            return []
        return [dict(binding) for binding in bindings if isinstance(binding, Mapping)]

    def _invoke_provider(
        self,
        provider: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if provider == "GROK":
            return self._invoke_grok(request)
        if provider == "CODEX":
            return self._invoke_codex(request)
        if provider == "CLAUDE":
            return self._invoke_claude(request)
        raise WorkerDispatchError(
            "WORKER_PROVIDER_UNSUPPORTED",
            "WORKER_ADAPTER",
            "WORKER_PROVIDER_UNSUPPORTED",
        )

    def _invoke_grok(self, request: Mapping[str, Any]) -> dict[str, Any]:
        executable, environment, _configured_model = _resolve_grok()
        if executable is None:
            raise WorkerDispatchError(
                "WORKER_INVOCATION_UNAVAILABLE",
                "WORKER_ADAPTER",
                "GROK_CLI_UNAVAILABLE",
            )
        model = self._request_model(request)
        effort = self._request_effort(request)
        supervisor_transport = self._request_supervisor_transport(request)
        runtime_profile = str(request.get("runtime_profile", "READ_ONLY")).upper()
        if runtime_profile not in {"READ_ONLY", "TASK_FRAME_RUNTIME"}:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                "GROK_RUNTIME_PROFILE_INVALID",
            )
        session_ids: list[str] = []
        try:
            gateway = UniverseAcpGateway(
                GrokAcpSession(
                    executable=executable,
                    cwd=self._worker_cwd(request),
                    environment=environment,
                    model=model,
                    effort=effort,
                    system_prompt=self._system_prompt(runtime_profile),
                    session_id=None,
                    permission_requester=lambda permission: self._task_frame_permission(
                        request, permission
                    ),
                    session_observer=session_ids.append,
                    ephemeral=True,
                    response_timeout_seconds=float(
                        request["response_timeout_seconds"]
                    ),
                    **supervisor_transport,
                )
            )
            try:
                text = gateway.reply_stream(
                    self._worker_prompt(request),
                    lambda _delta: None,
                )
                session_ref = gateway.session_ref
            finally:
                gateway.close()
        except AgentSessionError as error:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                f"GROK_ACP_{error}",
            ) from error
        session_id = session_ids[-1] if session_ids else session_ref.split(":", 1)[-1]
        return {
            "schema": "universe.grok-worker-result.v1",
            "status": "COMPLETED",
            "runtime_provider": "GROK_ACP",
            "runtime_profile": runtime_profile,
            "source_mutation": "HOST_GATEWAY_ONLY",
            "worker_id": f"grok-acp:{session_id}",
            "worker_run_ref": request["worker_run_ref"],
            "result_receipt_ref": (
                f"grok-acp:{session_id}:{request['worker_run_ref']}"
            ),
            "result": {"text": text, "stop_reason": "COMPLETED"},
            "sandbox_profile": "read-only",
            "permission_mode": "plan",
            "repository_write_scope": "NONE",
            "session_persistence": "EPHEMERAL",
            "persistent_session_ref": "UNKNOWN",
            "universe_coordinate_persisted": bool(supervisor_transport),
            "provider_durable_chat_state": "UNKNOWN",
            "host_session_ref": supervisor_transport.get(
                "supervisor_session_id", "UNKNOWN"
            ),
            "session_anchor_ref": supervisor_transport.get(
                "session_anchor_ref", "UNKNOWN"
            ),
        }

    def _invoke_codex(self, request: Mapping[str, Any]) -> dict[str, Any]:
        executable, environment, _configured_model = _resolve_codex()
        if executable is None:
            raise WorkerDispatchError(
                "WORKER_INVOCATION_UNAVAILABLE",
                "WORKER_ADAPTER",
                "CODEX_CLI_UNAVAILABLE",
            )
        model = self._request_model(request)
        effort = self._request_effort(request)
        supervisor_transport = self._request_supervisor_transport(request)
        session_ids: list[str] = []
        try:
            gateway = UniverseAcpGateway(
                CodexAppServerSession(
                    executable=executable,
                    cwd=self._worker_cwd(request),
                    environment=environment,
                    model=model,
                    effort=effort,
                    system_prompt=self._system_prompt("TASK_FRAME_RUNTIME"),
                    session_id=None,
                    permission_requester=lambda permission: self._task_frame_permission(
                        request, permission
                    ),
                    session_observer=session_ids.append,
                    ephemeral=True,
                    # Task Frame turns end on provider completion, explicit
                    # cancellation, or Host/provider failure. Their bounded
                    # assignment is not a wall-clock execution estimate.
                    response_timeout_seconds=None,
                    **supervisor_transport,
                )
            )
            try:
                text = gateway.reply_stream(
                    self._worker_prompt(request),
                    lambda _delta: None,
                )
                session_ref = gateway.session_ref
            finally:
                gateway.close()
        except TerminalHostError as error:
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "RUST_HOST",
                f"{error.code}:{error.detail}",
            ) from error
        except AgentSessionError as error:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                f"CODEX_APP_SERVER_{error}",
            ) from error
        session_id = session_ids[-1] if session_ids else session_ref.split(":", 1)[-1]
        return {
            "schema": "universe.codex-worker-result.v1",
            "status": "COMPLETED",
            "runtime_provider": "CODEX_APP_SERVER_ACP_ADAPTER",
            "runtime_profile": "TASK_FRAME_RUNTIME",
            "source_mutation": "HOST_GATEWAY_ONLY",
            "worker_id": f"codex-app-server:{session_id}",
            "worker_run_ref": request["worker_run_ref"],
            "result_receipt_ref": (
                f"codex-app-server:{session_id}:{request['worker_run_ref']}"
            ),
            "result": {"text": text, "stop_reason": "COMPLETED"},
            "repository_write_scope": "NONE",
            "session_persistence": "EPHEMERAL",
            "persistent_session_ref": "UNKNOWN",
            "universe_coordinate_persisted": bool(supervisor_transport),
            "provider_durable_chat_state": "NOT_PERSISTED",
            "host_session_ref": supervisor_transport.get(
                "supervisor_session_id", "UNKNOWN"
            ),
            "session_anchor_ref": supervisor_transport.get(
                "session_anchor_ref", "UNKNOWN"
            ),
        }

    def _invoke_claude(self, request: Mapping[str, Any]) -> dict[str, Any]:
        executable, environment, _configured_model = _resolve_claude()
        if executable is None:
            raise WorkerDispatchError(
                "WORKER_INVOCATION_UNAVAILABLE",
                "WORKER_ADAPTER",
                "CLAUDE_CLI_UNAVAILABLE",
            )
        model = self._request_model(request)
        effort = self._request_effort(request)
        supervisor_transport = self._request_supervisor_transport(request)
        json_schema: dict[str, Any] | None = None
        if str(request.get("result_mode", "REDACTED")).upper() == "STRUCTURED_JSON":
            output_contract = _mapping(
                request.get("output_contract"), "output_contract"
            )
            raw_schema = output_contract.get("json_schema")
            if not isinstance(raw_schema, Mapping):
                raise WorkerDispatchError(
                    "WORKER_OUTPUT_SCHEMA_REQUIRED",
                    "WORKER_ADAPTER",
                    "CLAUDE_JSON_SCHEMA_REQUIRED",
                )
            json_schema = dict(raw_schema)
        if supervisor_transport:
            return self._invoke_managed_claude(
                request,
                executable=executable,
                environment=environment,
                model=model,
                effort=effort,
                json_schema=json_schema,
                supervisor_transport=supervisor_transport,
            )
        session_ids: list[str] = []
        try:
            gateway = UniverseAcpGateway(
                ClaudeCodeSession(
                    executable=executable,
                    cwd=self._worker_cwd(request),
                    environment=environment,
                    model=model,
                    system_prompt=self._system_prompt("TASK_FRAME_RUNTIME"),
                    session_id=None,
                    permission_requester=lambda permission: self._task_frame_permission(
                        request, permission
                    ),
                    session_observer=session_ids.append,
                    ephemeral=True,
                    allow_read_only_tools=self._is_qa_reviewer(request),
                    # Task Frames complete on a terminal structured result. A
                    # provider-loop turn cap would make tool use itself look
                    # like task completion, so this adapter intentionally omits
                    # the Claude CLI --max-turns option for this runtime.
                    max_turns=None,
                    response_timeout_seconds=float(
                        request["response_timeout_seconds"]
                    ),
                    json_schema=json_schema,
                    native_runner=self.native_runner,
                )
            )
            try:
                text = gateway.reply_stream(
                    self._worker_prompt(request),
                    lambda _delta: None,
                )
                session_ref = gateway.session_ref
            finally:
                gateway.close()
        except AgentSessionError as error:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                f"CLAUDE_CODE_{error}",
            ) from error
        session_id = session_ref.split(":", 1)[-1]
        return {
            "schema": "universe.claude-worker-result.v1",
            "status": "COMPLETED",
            "runtime_provider": "CLAUDE_CODE_CLI_ADAPTER",
            "runtime_profile": "TASK_FRAME_RUNTIME",
            "source_mutation": "HOST_GATEWAY_ONLY",
            "worker_id": f"claude-code:{session_id}",
            "worker_run_ref": request["worker_run_ref"],
            "result_receipt_ref": (
                f"claude-code:{session_id}:{request['worker_run_ref']}"
            ),
            "result": {"text": text, "stop_reason": "COMPLETED"},
            "permission_mode": "default",
            "repository_write_scope": "NONE",
            "session_persistence": "EPHEMERAL",
            "persistent_session_ref": "UNKNOWN",
            "universe_coordinate_persisted": False,
            "provider_durable_chat_state": "NOT_PERSISTED",
            "host_session_ref": "UNKNOWN",
            "session_anchor_ref": "UNKNOWN",
        }

    def _invoke_managed_claude(
        self,
        request: Mapping[str, Any],
        *,
        executable: Path,
        environment: Mapping[str, str],
        model: str,
        effort: str,
        json_schema: Mapping[str, Any] | None,
        supervisor_transport: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_ids: list[str] = []
        broker: ClaudePermissionBroker | None = None
        config_root: Path | None = None
        gateway: UniverseAcpGateway | None = None
        try:
            bridge = ClaudePermissionBridge(
                session_ref=f"claude-code:pending:{uuid4().hex}",
                permission_requester=lambda permission: self._task_frame_permission(
                    request, permission
                ),
            )
            broker = ClaudePermissionBroker(
                bridge=bridge,
                target=(
                    f"{self.project_id}/{self.mode}/"
                    f"{request['task_frame_id']}/{request['turn_id']}"
                ),
            ).start()
            config_root = Path(tempfile.mkdtemp(prefix="universe-task-frame-claude-mcp-"))
            mcp_config = broker.write_mcp_config(config_root / "mcp.json")

            def observe_session(session_id: str) -> None:
                session_ids.append(session_id)
                bridge.bind_session_ref(f"claude-code:{session_id}")

            session = ClaudeResidentSession(
                executable=executable,
                cwd=self._worker_cwd(request),
                environment=broker.provider_environment(dict(environment)),
                model=model,
                effort=effort,
                json_schema=json_schema,
                system_prompt=self._system_prompt("TASK_FRAME_RUNTIME"),
                session_id=None,
                session_observer=observe_session,
                extra_arguments=("--no-session-persistence",),
                # A Task Frame finishes on the provider's terminal structured
                # result (or an explicit cancel/failure). Editing and review do
                # not have a defensible wall-clock duration, so the managed
                # Claude path intentionally has no per-turn timer.
                turn_timeout_seconds=None,
                permission_mcp_config=mcp_config,
                permission_bridge=bridge,
                permission_ready=broker.wait_for_registration,
                permission_failure=broker.close,
                **dict(supervisor_transport),
            )
            bridge.bind_session_ref(session.session_ref)
            gateway = UniverseAcpGateway(session)
            text = gateway.reply_stream(
                self._worker_prompt(request),
                lambda _delta: None,
            )
            session_ref = gateway.session_ref
        except TerminalHostError as error:
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "RUST_HOST",
                f"{error.code}:{error.detail}",
            ) from error
        except (AgentSessionError, ClaudeResidentError) as error:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                f"CLAUDE_CODE_{error}",
            ) from error
        finally:
            if gateway is not None:
                gateway.close()
            if broker is not None:
                broker.close()
            if config_root is not None:
                shutil.rmtree(config_root, ignore_errors=True)
        session_id = (
            session_ids[-1]
            if session_ids
            else session_ref.split(":", 1)[-1]
        )
        return {
            "schema": "universe.claude-worker-result.v1",
            "status": "COMPLETED",
            "runtime_provider": "CLAUDE_CODE_STREAM_ADAPTER",
            "runtime_profile": "TASK_FRAME_RUNTIME",
            "source_mutation": "HOST_GATEWAY_ONLY",
            "worker_id": f"claude-code:{session_id}",
            "worker_run_ref": request["worker_run_ref"],
            "result_receipt_ref": (
                f"claude-code:{session_id}:{request['worker_run_ref']}"
            ),
            "result": {"text": text, "stop_reason": "COMPLETED"},
            "permission_mode": "default",
            "repository_write_scope": str(
                request.get("repository_write_scope") or "NONE"
            ).upper(),
            "session_persistence": "EPHEMERAL",
            "persistent_session_ref": "UNKNOWN",
            "universe_coordinate_persisted": True,
            "provider_durable_chat_state": "NOT_PERSISTED",
            "host_session_ref": supervisor_transport["supervisor_session_id"],
            "session_anchor_ref": supervisor_transport["session_anchor_ref"],
        }

    @staticmethod
    def _request_model(request: Mapping[str, Any]) -> str:
        return _required_text(request.get("model"), "model")

    @staticmethod
    def _request_effort(request: Mapping[str, Any]) -> str:
        effort = str(request.get("effort") or "AUTO").strip().upper()
        if effort not in {"AUTO", "LOW", "MEDIUM", "HIGH", "MAX"}:
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "REQUEST_LOAD",
                "WORKER_EFFORT_INVALID",
            )
        return effort

    def _supervisor_transport(
        self,
        provider: str,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.terminal_host is None:
            return {}
        if (
            not self.project_id
            or not self.mode
            or self.worker_host_coordinate_resolver is None
        ):
            raise WorkerDispatchError(
                "WORKER_HOST_COORDINATE_UNAVAILABLE",
                "WORKER_ADAPTER",
                "RUST_HOST_COORDINATE_RESOLVER_UNAVAILABLE",
            )
        coordinate = self.worker_host_coordinate_resolver(
            provider,
            request["frame_id"],
            request["turn_id"],
        )
        if not isinstance(coordinate, Mapping):
            raise WorkerDispatchError(
                "WORKER_HOST_COORDINATE_UNAVAILABLE",
                "WORKER_ADAPTER",
                "RUST_HOST_COORDINATE_INVALID",
            )
        supervisor_session_id = str(
            coordinate.get("supervisor_session_id") or ""
        ).strip()
        session_anchor_ref = str(coordinate.get("session_anchor_ref") or "").strip()
        if not supervisor_session_id or not session_anchor_ref:
            raise WorkerDispatchError(
                "WORKER_HOST_COORDINATE_UNAVAILABLE",
                "WORKER_ADAPTER",
                "RUST_HOST_COORDINATE_INCOMPLETE",
            )
        return {
            "terminal_host": self.terminal_host,
            "project_id": self.project_id,
            "mode": self.mode,
            "supervisor_session_id": supervisor_session_id,
            "session_anchor_ref": session_anchor_ref,
        }

    @staticmethod
    def _request_supervisor_transport(
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = request.get("supervisor_transport")
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise WorkerDispatchError(
                "WORKER_HOST_COORDINATE_UNAVAILABLE",
                "WORKER_ADAPTER",
                "RUST_HOST_TRANSPORT_INVALID",
            )
        return dict(value)

    @staticmethod
    def _system_prompt(runtime_profile: str) -> str:
        if runtime_profile == "TASK_FRAME_RUNTIME":
            return (
                "You are a bounded Task Frame Runtime provider. You receive all "
                "usable context in the supplied Context Pack. Perform the assigned "
                "task according to its Runtime-validated purpose, scope, and output "
                "contract. Do not invoke subagents or claim authority. Any source "
                "mutation must remain within the declared mutation scope and use "
                "the Host gateway. Return only the requested result content."
            )
        return (
            "You are a bounded read-only Task Frame worker. You receive all "
            "usable context in the supplied Context Pack. Do not inspect local "
            "files, execute commands, use network tools, create files, modify "
            "files, invoke subagents, or claim authority. Return only the "
            "requested result content."
        )

    @staticmethod
    def _worker_prompt(request: Mapping[str, Any]) -> str:
        context_pack = _structured_text(request.get("context_pack"), "context_pack")
        output_contract = _structured_text(
            request.get("output_contract"), "output_contract"
        )
        format_instruction = (
            "\nReturn exactly one JSON object matching the Output Contract. "
            "Do not use Markdown fences or explanatory text."
            if str(request.get("result_mode", "REDACTED")).upper() == "STRUCTURED_JSON"
            else ""
        )
        role_scope = (
            "\n\nRole Scope: Read the necessary files and run tests, builds, and "
            "validation commands. Report discovered problems with evidence and "
            "reproduction steps. Do not modify files."
            if RuntimeWorkerDispatcher._is_qa_reviewer(request)
            else ""
        )
        return (
            f"Task Frame ID: "
            f"{_required_text(request.get('task_frame_id'), 'task_frame_id')}\n"
            f"Turn ID: {_required_text(request.get('turn_id'), 'turn_id')}\n\n"
            f"Context Pack:\n{context_pack}\n\n"
            f"Output Contract:\n{output_contract}{format_instruction}{role_scope}"
        )

    @staticmethod
    def _is_qa_reviewer(request: Mapping[str, Any]) -> bool:
        context = request.get("context_pack")
        return isinstance(context, Mapping) and (
            str(context.get("semantic_role") or "").upper() == "QA_REVIEWER"
        )

    def _response_timeout_seconds_for(self, request: Mapping[str, Any]) -> float:
        if self._is_qa_reviewer(request):
            return max(
                self.worker_response_timeout_seconds,
                QA_WORKER_RESPONSE_TIMEOUT_SECONDS,
            )
        if request["repository_write_scope"] == "NONE":
            return max(
                self.worker_response_timeout_seconds,
                READ_ONLY_WORKER_RESPONSE_TIMEOUT_SECONDS,
            )
        return self.worker_response_timeout_seconds

    def _worker_cwd(self, request: Mapping[str, Any]) -> Path:
        if str(request.get("runtime_profile") or "").upper() == "TASK_FRAME_RUNTIME":
            return self.repository_root
        return Path(tempfile.gettempdir())

    @classmethod
    def _task_frame_permission(
        cls, task_request: Mapping[str, Any], permission: Mapping[str, Any]
    ) -> str | None:
        tool_call = permission.get("tool_call")
        tool_name = (
            str(tool_call.get("toolName") or tool_call.get("title") or "").strip()
            if isinstance(tool_call, Mapping)
            else ""
        )
        allow = tool_name.casefold() in {"read", "glob", "grep"}
        if cls._is_qa_reviewer(task_request):
            allow = True
        elif (
            str(task_request.get("repository_write_scope") or "").upper()
            == "BOUNDED"
            and isinstance(tool_call, Mapping)
        ):
            mutation_scope = task_request.get("mutation_scope")
            if isinstance(mutation_scope, Mapping):
                targets = mutation_scope.get("targets")
                operations = {
                    str(item).upper()
                    for item in mutation_scope.get("operations", [])
                    if isinstance(item, str)
                }
                target_values = targets if isinstance(targets, list) else []
                normalized_targets = {
                    str(Path(str(item)).resolve(strict=False)).casefold()
                    for item in target_values
                    if isinstance(item, str)
                }
                cwd = Path(str(tool_call.get("cwd") or Path.cwd()))

                def normalize_requested_path(value: Any) -> str:
                    raw_path = str(value or "").strip()
                    if not raw_path:
                        return ""
                    path = Path(raw_path)
                    if not path.is_absolute():
                        path = cwd / path
                    return str(path.resolve(strict=False)).casefold()

                tool_input = tool_call.get("input")
                if isinstance(tool_input, Mapping):
                    requested_path = normalize_requested_path(
                        tool_call.get("path")
                        or tool_input.get("file_path")
                        or tool_input.get("notebook_path")
                    )
                    required_operations = {
                        "write": {"CREATE", "MODIFY"},
                        "edit": {"MODIFY"},
                        "notebookedit": {"MODIFY"},
                    }.get(tool_name.casefold(), set())
                    allow = bool(
                        requested_path
                        and requested_path in normalized_targets
                        and required_operations.intersection(operations)
                    )
                elif (
                    tool_name.casefold()
                    == "item/filechange/requestapproval"
                    and not tool_call.get("grantRoot")
                ):
                    changes = tool_call.get("fileChanges")
                    required_by_change = {
                        "add": "CREATE",
                        "update": "MODIFY",
                    }
                    allow = bool(changes) and isinstance(changes, list)
                    for change in changes if isinstance(changes, list) else []:
                        if not isinstance(change, Mapping) or change.get("move_path"):
                            allow = False
                            break
                        required_operation = required_by_change.get(
                            str(change.get("type") or "").casefold()
                        )
                        requested_path = normalize_requested_path(change.get("path"))
                        if (
                            required_operation is None
                            or required_operation not in operations
                            or requested_path not in normalized_targets
                        ):
                            allow = False
                            break
        wanted = (
            {"allow_once"}
            if allow
            else {"reject_once", "reject_always"}
        )
        for option in permission.get("options", []):
            if (
                isinstance(option, Mapping)
                and option.get("kind") in wanted
                and isinstance(option.get("optionId"), str)
            ):
                return option["optionId"]
        return None

    @staticmethod
    def _reject_task_frame_permission(request: Mapping[str, Any]) -> str | None:
        for option in request.get("options", []):
            if (
                isinstance(option, Mapping)
                and option.get("kind") in {"reject_once", "reject_always"}
                and isinstance(option.get("optionId"), str)
            ):
                return option["optionId"]
        return None
