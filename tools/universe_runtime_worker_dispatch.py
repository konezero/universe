from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli


DISPATCH_SCHEMA = "universe.task-frame-worker-dispatch-request.v1"
SUPPORTED_PROVIDERS = frozenset({"GROK", "CODEX"})
RESULT_MODES = frozenset({"REDACTED", "STRUCTURED_JSON"})
PROVIDER_MODELS = {"GROK": "grok-build", "CODEX": "default"}


@dataclass(frozen=True)
class WorkerDispatchError(Exception):
    code: str
    stage: str
    reason: str

    def __str__(self) -> str:
        return f"{self.stage}: {self.reason}"


NativeRunner = Callable[[NativeCliRequest], NativeCliResult]
PostJson = Callable[[str, str, str, Mapping[str, Any]], dict[str, Any]]


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerDispatchError(
            "WORKER_TRANSPORT_FAILED",
            "REQUEST_LOAD",
            f"{field.upper()}_REQUIRED",
        )
    return value.strip()


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


def _runtime_tmp() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    root = Path(base) / "Universe" / "runtime-tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


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


def _resolve_grok() -> tuple[Path | None, dict[str, str]]:
    grok_home = Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok")).resolve()
    executable = grok_home / "bin" / "grok.exe"
    return (executable if executable.is_file() else None), {"GROK_HOME": str(grok_home)}


def _resolve_codex() -> tuple[Path | None, dict[str, str]]:
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured:
        candidate = Path(configured).resolve()
        return (candidate if candidate.is_file() else None), {}
    resolved = shutil.which("codex.exe") or shutil.which("codex")
    if not resolved:
        return None, {}
    candidate = Path(resolved).resolve()
    if candidate.suffix.lower() != ".exe":
        return None, {}
    return candidate, {}


def _provider_executable(provider: str) -> tuple[Path | None, dict[str, str]]:
    if provider == "GROK":
        return _resolve_grok()
    if provider == "CODEX":
        return _resolve_codex()
    return None, {}


class RuntimeWorkerDispatcher:
    def __init__(
        self,
        repository_root: Path,
        *,
        native_runner: NativeRunner = run_native_cli,
        post: PostJson = post_json,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.native_runner = native_runner
        self.post = post

    def provider_capability(self, provider: str) -> dict[str, str]:
        normalized = _required_text(provider, "provider").upper()
        if normalized not in SUPPORTED_PROVIDERS:
            return {
                "status": "UNAVAILABLE",
                "provider": normalized,
                "reason": "WORKER_PROVIDER_UNSUPPORTED",
            }
        executable, environment = _provider_executable(normalized)
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
        planned_provider = str(planned_invocation.get("provider", "")).upper()
        if planned_provider and planned_provider != provider:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_PLAN_MISMATCH",
                "TASK_FRAME_PLAN",
                "PLANNED_PROVIDER_MISMATCH",
            )

        skill_bindings = self._skill_bindings(planned_invocation)
        worker_run_ref = f"universe-runtime-host:{uuid4().hex}"
        worker_request = {
            "schema": (
                "universe.grok-worker-request.v1"
                if provider == "GROK"
                else "universe.codex-worker-request.v1"
            ),
            "runtime_profile": "TASK_FRAME_RUNTIME",
            "task_frame_id": request["frame_id"],
            "turn_id": request["turn_id"],
            "worker_run_ref": worker_run_ref,
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "context_pack": request["context_pack"],
            "output_contract": request["output_contract"],
            "max_turns": request["max_turns"],
            "result_mode": request["result_mode"],
        }

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

        recorded_result: Any = worker.get("result")
        structured_result: dict[str, Any] | None = None
        if request["result_mode"] == "STRUCTURED_JSON":
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
            structured_result = parsed
            recorded_result = structured_result

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
                    "worker_id": worker["worker_id"],
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

        model = str(planned_invocation.get("model") or PROVIDER_MODELS[provider])
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
                "evidence_refs": [worker["result_receipt_ref"]],
                "metrics": {"duration_ms": duration_ms},
            }
            for binding in skill_bindings
        ]
        envelope = {
            "turn_id": request["turn_id"],
            "worker_id": worker["worker_id"],
            "worker_run_ref": worker_run_ref,
            "result_receipt_ref": worker["result_receipt_ref"],
            "status": worker["status"],
            "evidence_refs": [worker["result_receipt_ref"]],
            "result": recorded_result,
            "review_decision": "",
        }
        if observations:
            envelope["skill_run_observations"] = observations
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
        response = {
            "status": result.get("status", "WORKER_PROVIDER_FAILED"),
            "provider": provider,
            "model_ref": model_ref,
            "worker_id": worker["worker_id"],
            "result_receipt_ref": worker["result_receipt_ref"],
            "result": recorded_result,
            "skill_run_observation_count": len(observations),
            "repository_write": False,
            "runtime_result": result,
        }
        if structured_result is not None:
            response["structured_result"] = structured_result
        return response

    def _normalize_request(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if raw.get("schema") != DISPATCH_SCHEMA:
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "REQUEST_LOAD",
                "DISPATCH_SCHEMA_INVALID",
            )
        provider = _required_text(raw.get("provider"), "provider").upper()
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
        mutation_scope = _mapping(raw.get("mutation_scope"), "mutation_scope")
        if (
            raw.get("repository_write_scope") != "NONE"
            or mutation_scope.get("operations") != []
            or mutation_scope.get("targets") != []
        ):
            raise WorkerDispatchError(
                "READ_ONLY_SCOPE_REQUIRED",
                "REQUEST_LOAD",
                "READ_ONLY_SCOPE_REQUIRED",
            )
        max_turns = raw.get("max_turns", 1)
        if (
            not isinstance(max_turns, int)
            or isinstance(max_turns, bool)
            or not 1 <= max_turns <= 8
        ):
            raise WorkerDispatchError(
                "WORKER_TRANSPORT_FAILED",
                "REQUEST_LOAD",
                "MAX_TURNS_INVALID",
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
            "context_pack": _mapping(raw.get("context_pack"), "context_pack"),
            "output_contract": _mapping(raw.get("output_contract"), "output_contract"),
            "max_turns": max_turns,
            "result_mode": result_mode,
        }

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
        raise WorkerDispatchError(
            "WORKER_PROVIDER_UNSUPPORTED",
            "WORKER_ADAPTER",
            "WORKER_PROVIDER_UNSUPPORTED",
        )

    def _invoke_grok(self, request: Mapping[str, Any]) -> dict[str, Any]:
        executable, environment = _resolve_grok()
        if executable is None:
            raise WorkerDispatchError(
                "WORKER_INVOCATION_UNAVAILABLE",
                "WORKER_ADAPTER",
                "GROK_CLI_UNAVAILABLE",
            )
        runtime_profile = str(request.get("runtime_profile", "READ_ONLY")).upper()
        if runtime_profile not in {"READ_ONLY", "TASK_FRAME_RUNTIME"}:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                "GROK_RUNTIME_PROFILE_INVALID",
            )
        context_pack = _structured_text(request.get("context_pack"), "context_pack")
        output_contract = _structured_text(
            request.get("output_contract"), "output_contract"
        )
        result_mode = str(request.get("result_mode", "REDACTED")).upper()
        system_prompt = (
            "You are a bounded Task Frame Runtime provider. You receive all "
            "usable context in the supplied Context Pack. Do not inspect local "
            "files, create files, modify files, invoke subagents, or claim "
            "authority. Source mutation is Host-gateway-only. Return only the "
            "requested result content."
            if runtime_profile == "TASK_FRAME_RUNTIME"
            else "You are a bounded read-only Task Frame worker. You receive all "
            "usable context in the supplied Context Pack. Do not inspect local "
            "files, execute commands, use network tools, create files, modify "
            "files, invoke subagents, or claim authority. Return only the "
            "requested result content."
        )
        format_instruction = (
            "\nReturn exactly one JSON object matching the Output Contract. "
            "Do not use Markdown fences or explanatory text."
            if result_mode == "STRUCTURED_JSON"
            else ""
        )
        prompt = (
            f"Task Frame ID: {_required_text(request.get('task_frame_id'), 'task_frame_id')}\n"
            f"Turn ID: {_required_text(request.get('turn_id'), 'turn_id')}\n\n"
            f"Context Pack:\n{context_pack}\n\n"
            f"Output Contract:\n{output_contract}{format_instruction}"
        )
        prompt_path = _runtime_tmp() / f"grok-prompt-{uuid4().hex}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        try:
            arguments = [
                "--no-auto-update",
                "--no-subagents",
                "--no-memory",
                "--disable-web-search",
                "--permission-mode",
                "plan",
                "--sandbox",
                "read-only",
                "--max-turns",
                str(request.get("max_turns", 3)),
                "--cwd",
                tempfile.gettempdir(),
                "--system-prompt-override",
                system_prompt,
                "--prompt-file",
                str(prompt_path),
            ]
            if result_mode == "STRUCTURED_JSON":
                output = _mapping(request.get("output_contract"), "output_contract")
                schema = output.get("json_schema")
                if not isinstance(schema, Mapping):
                    raise WorkerDispatchError(
                        "WORKER_PROVIDER_FAILED",
                        "WORKER_ADAPTER",
                        "OUTPUT_JSON_SCHEMA_REQUIRED",
                    )
                arguments.extend(
                    [
                        "--json-schema",
                        json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                    ]
                )
            else:
                arguments.extend(["--output-format", "json"])
            result = self.native_runner(
                NativeCliRequest(
                    executable=executable,
                    arguments=tuple(arguments),
                    cwd=Path(tempfile.gettempdir()),
                    timeout_seconds=180,
                    environment=environment,
                )
            )
        finally:
            prompt_path.unlink(missing_ok=True)
        if result.status != "COMPLETED":
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                f"GROK_CLI_{result.status}",
            )
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                "GROK_RESULT_JSON_INVALID",
            ) from error
        if not isinstance(response, Mapping):
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                "GROK_RESULT_OBJECT_REQUIRED",
            )
        structured = response.get("structuredOutput")
        text = (
            _structured_text(structured, "response.structuredOutput")
            if result_mode == "STRUCTURED_JSON" and structured is not None
            else _required_text(response.get("text"), "response.text")
        )
        session_id = _required_text(response.get("sessionId"), "response.sessionId")
        request_id = _required_text(response.get("requestId"), "response.requestId")
        return {
            "schema": "universe.grok-worker-result.v1",
            "status": (
                "COMPLETED" if response.get("stopReason") == "EndTurn" else "UNKNOWN"
            ),
            "runtime_provider": "GROK_CLI",
            "runtime_profile": runtime_profile,
            "source_mutation": "HOST_GATEWAY_ONLY",
            "worker_id": f"grok-cli:{session_id}",
            "worker_run_ref": request["worker_run_ref"],
            "result_receipt_ref": f"grok-cli:{session_id}:{request_id}",
            "result": {"text": text, "stop_reason": response.get("stopReason")},
            "sandbox_profile": "read-only",
            "permission_mode": "plan",
            "repository_write_scope": "NONE",
        }

    def _invoke_codex(self, request: Mapping[str, Any]) -> dict[str, Any]:
        executable, environment = _resolve_codex()
        if executable is None:
            raise WorkerDispatchError(
                "WORKER_INVOCATION_UNAVAILABLE",
                "WORKER_ADAPTER",
                "CODEX_CLI_UNAVAILABLE",
            )
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
        prompt = (
            f"Task Frame ID: {_required_text(request.get('task_frame_id'), 'task_frame_id')}\n"
            f"Turn ID: {_required_text(request.get('turn_id'), 'turn_id')}\n\n"
            f"Context Pack:\n{context_pack}\n\n"
            f"Output Contract:\n{output_contract}{format_instruction}"
        )
        result = self.native_runner(
            NativeCliRequest(
                executable=executable,
                arguments=(
                    "exec",
                    "--json",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "-C",
                    tempfile.gettempdir(),
                    prompt,
                ),
                cwd=Path(tempfile.gettempdir()),
                timeout_seconds=180,
                environment=environment,
            )
        )
        if result.status != "COMPLETED":
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                f"CODEX_CLI_{result.status}",
            )
        messages: list[str] = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping):
                continue
            item = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, Mapping)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
                and item["text"].strip()
            ):
                messages.append(item["text"])
            elif (
                event.get("type") == "agent_message"
                and isinstance(event.get("text"), str)
                and event["text"].strip()
            ):
                messages.append(event["text"])
        if not messages:
            raise WorkerDispatchError(
                "WORKER_PROVIDER_FAILED",
                "WORKER_ADAPTER",
                "CODEX_RESULT_MESSAGE_MISSING",
            )
        run_id = uuid4().hex
        return {
            "schema": "universe.codex-worker-result.v1",
            "status": "COMPLETED",
            "runtime_provider": "CODEX_CLI",
            "runtime_profile": "TASK_FRAME_RUNTIME",
            "source_mutation": "HOST_GATEWAY_ONLY",
            "worker_id": f"codex-cli:{run_id}",
            "worker_run_ref": request["worker_run_ref"],
            "result_receipt_ref": f"codex-cli:{run_id}",
            "result": {"text": "\n".join(messages), "stop_reason": "COMPLETED"},
            "repository_write_scope": "NONE",
        }
