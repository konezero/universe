from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


RUNTIME_WORKER_REQUEST_SCHEMA = "universe.runtime-worker-invocation-request.v1"
SUPPORTED_PROVIDERS = frozenset({"GROK", "CODEX"})


@dataclass(frozen=True)
class RuntimeHostError(Exception):
    code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


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


def normalize_read_only_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeHostError("RUNTIME_WORKER_REQUEST_INVALID", "request must be an object")
    if value.get("schema") not in {None, RUNTIME_WORKER_REQUEST_SCHEMA}:
        raise RuntimeHostError("RUNTIME_WORKER_REQUEST_INVALID", "request schema is unsupported")
    provider = _required_text(value.get("provider"), "provider").upper()
    if provider not in SUPPORTED_PROVIDERS:
        raise RuntimeHostError("WORKER_PROVIDER_UNSUPPORTED", "provider is unsupported")
    endpoint = _required_text(value.get("endpoint"), "endpoint")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeHostError(
            "RUNTIME_HOST_ENDPOINT_INVALID", "endpoint must be loopback HTTP"
        )
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
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= 4:
        raise RuntimeHostError("RUNTIME_WORKER_REQUEST_INVALID", "max_turns must be 1..4")
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
    }
    record["request_digest"] = _digest(record)
    return record


class UniverseRuntimeHost:
    def __init__(
        self,
        repository_root: Path,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.dispatcher = self.repository_root / "tools" / "universe_runtime_host_dispatch.ps1"
        self.runner = runner or subprocess.run

    def provider_capabilities(self) -> list[dict[str, str]]:
        return [self.provider_capability(provider) for provider in sorted(SUPPORTED_PROVIDERS)]

    def provider_capability(self, provider: str) -> dict[str, str]:
        normalized = _required_text(provider, "provider").upper()
        if normalized not in SUPPORTED_PROVIDERS:
            return {
                "provider": normalized,
                "status": "UNAVAILABLE",
                "reason": "WORKER_PROVIDER_UNSUPPORTED",
            }
        response = self._invoke_dispatch(
            ["-Provider", normalized, "-CapabilityOnly"], timeout=20
        )
        result = {
            "provider": normalized,
            "status": "AVAILABLE" if response.get("status") == "AVAILABLE" else "UNAVAILABLE",
        }
        if isinstance(response.get("reason"), str) and response["reason"]:
            result["reason"] = response["reason"]
        return result

    def invoke_read_only(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = normalize_read_only_request(value)
        with self._transient_request_file(request) as request_path:
            response = self._invoke_dispatch(["-RequestPath", str(request_path)], timeout=180)
        return {
            "status": _text_or(response.get("status"), "WORKER_PROVIDER_FAILED"),
            "provider": request["provider"],
            "worker_id": _text_or(response.get("worker_id"), "UNKNOWN"),
            "result_receipt_ref": _text_or(
                response.get("result_receipt_ref"), "UNKNOWN"
            ),
            "repository_write": False,
            **{
                key: value
                for key in ("reason", "stage")
                if isinstance((value := response.get(key)), str) and value
            },
        }

    def _invoke_dispatch(self, arguments: list[str], *, timeout: int) -> dict[str, Any]:
        try:
            completed = self.runner(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.dispatcher),
                    *arguments,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
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
        return result

    def _transient_request_file(self, request: Mapping[str, Any]) -> "_TransientRequestFile":
        root = Path(
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("TEMP")
            or tempfile.gettempdir()
        ) / "Universe" / "runtime-tmp"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="runtime-worker-",
            dir=root,
            delete=False,
        ) as handle:
            json.dump(request, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
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
