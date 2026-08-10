from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DISPATCH_SCHEMA = "universe.dispatch-envelope.v1"
DISPATCH_EVENT_SCHEMA = "universe.dispatch-event.v1"
RESULT_PACKET_SCHEMA = "universe.project-result-packet.v1"
DELIVERY_RECEIPT_SCHEMA = "universe.dispatch-delivery-receipt.v1"
WAKE_RECEIPT_SCHEMA = "universe.project-wake-receipt.v1"
MASTER_BRIDGE_ENVELOPE_SCHEMA = "universe.project-master-bridge-envelope.v1"
MASTER_BRIDGE_RECEIPT_SCHEMA = "universe.project-master-bridge-receipt.v1"
MASTER_SEED_APPLY_RECEIPT_SCHEMA = (
    "universe.project-master-seed-apply-delivery-receipt.v1"
)
MASTER_INTEGRATION_APPLY_RECEIPT_SCHEMA = (
    "universe.project-master-integration-apply-delivery-receipt.v1"
)
MASTER_SKILL_PLAN_APPLY_RECEIPT_SCHEMA = (
    "universe.project-master-skill-plan-apply-delivery-receipt.v1"
)
DISPATCH_ID_PATTERN = re.compile(r"^dispatch_[0-9a-f]{20,64}$")
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
TERMINAL_STATUSES = frozenset({"COMPLETED", "BLOCKED"})
DISPATCH_TRANSITIONS = {
    "QUEUED": frozenset({"DELIVERED"}),
    "DELIVERED": frozenset({"ACKNOWLEDGED"}),
    "ACKNOWLEDGED": frozenset({"STARTED"}),
    "STARTED": TERMINAL_STATUSES,
    "COMPLETED": frozenset(),
    "BLOCKED": frozenset(),
}
MAX_INSTRUCTION_BYTES = 256 * 1024
MAX_RESULT_BYTES = 1024 * 1024


class DispatchError(ValueError):
    pass


class ProjectInboxConnector(Protocol):
    def deliver(self, envelope: dict[str, Any]) -> dict[str, Any]: ...


class ProjectWakeAdapter(Protocol):
    def wake(self, envelope: dict[str, Any]) -> dict[str, Any]: ...


class ProjectMasterBridge(Protocol):
    def deliver(
        self,
        *,
        bridge: dict[str, Any],
        message: dict[str, Any],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class LocalInboxConnector:
    project_root: Path
    inbox_ref: str

    def deliver(self, envelope: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_dispatch_envelope(envelope)
        root = self.project_root.expanduser().resolve(strict=True)
        inbox = _inbox_path(root, self.inbox_ref)
        if not inbox.is_dir() or inbox.is_symlink():
            raise DispatchError("MASTER_INBOX_UNAVAILABLE")
        target = inbox / f"{normalized['dispatch_id']}.json"
        content = (
            json.dumps(
                normalized,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise DispatchError("MASTER_INBOX_TARGET_INVALID")
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise DispatchError("DISPATCH_DELIVERY_CONFLICT")
            operation = "ALREADY_DELIVERED"
        else:
            _create_file(target, content)
            operation = "DELIVERED"
        return {
            "schema": DELIVERY_RECEIPT_SCHEMA,
            "status": operation,
            "dispatch_id": normalized["dispatch_id"],
            "project_id": normalized["project_id"],
            "connector": "LOCAL_INBOX",
            "target_ref": target.relative_to(root).as_posix(),
            "content_sha256": digest,
            "delivered_at": utc_now(),
        }


@dataclass(frozen=True)
class NoWakeAdapter:
    def wake(self, envelope: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_dispatch_envelope(envelope)
        return {
            "schema": WAKE_RECEIPT_SCHEMA,
            "status": "WAKE_NOT_REQUESTED",
            "dispatch_id": normalized["dispatch_id"],
            "project_id": normalized["project_id"],
            "woken_at": utc_now(),
        }


@dataclass(frozen=True)
class HttpProjectWakeAdapter:
    endpoint: str
    token: str
    timeout_seconds: float = 5.0

    def wake(self, envelope: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_dispatch_envelope(envelope)
        endpoint = _loopback_endpoint(self.endpoint)
        token = _text(self.token, "token")
        body = json.dumps(
            {
                "schema": WAKE_RECEIPT_SCHEMA,
                "dispatch_id": normalized["dispatch_id"],
                "project_id": normalized["project_id"],
                "inbox_ref": normalized["inbox_ref"],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = Request(
            endpoint + "/v1/master/wake",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                status_code = response.status
        except HTTPError as error:
            raise DispatchError(f"PROJECT_WAKE_HTTP_{error.code}") from error
        except (URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DispatchError("PROJECT_WAKE_UNAVAILABLE") from error
        if not 200 <= status_code < 300 or not isinstance(payload, dict):
            raise DispatchError("PROJECT_WAKE_REJECTED")
        return {
            "schema": WAKE_RECEIPT_SCHEMA,
            "status": "WAKE_REQUESTED",
            "dispatch_id": normalized["dispatch_id"],
            "project_id": normalized["project_id"],
            "endpoint": endpoint,
            "host_response": payload,
            "woken_at": utc_now(),
        }


@dataclass(frozen=True)
class HttpProjectMasterBridge:
    """Deliver a room envelope to one registered local Project Master Host.

    The bridge is deliberately transport-only. It does not create a vendor
    chat session, infer authority, or execute an instruction received in a
    room message.
    """

    endpoint: str
    credential_env: str
    timeout_seconds: float = 5.0

    def validate(self) -> str:
        _environment_name(self.credential_env)
        return _loopback_endpoint(self.endpoint, label="master bridge")

    def deliver(
        self,
        *,
        bridge: dict[str, Any],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = self.validate()
        credential_env = _environment_name(self.credential_env)
        token = os.environ.get(credential_env)
        if not token:
            raise DispatchError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
        bridge_id = _text(bridge.get("bridge_id"), "bridge.bridge_id")
        project_id = _project_id(bridge.get("project_id"))
        master_session_ref = _text(
            bridge.get("master_session_ref"), "bridge.master_session_ref"
        )
        message_id = _text(message.get("message_id"), "message.message_id")
        body = json.dumps(
            {
                "schema": MASTER_BRIDGE_ENVELOPE_SCHEMA,
                "bridge_id": bridge_id,
                "project_id": project_id,
                "master_session_ref": master_session_ref,
                "message": message,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = Request(
            endpoint + "/v1/project-master/messages",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                status_code = response.status
        except HTTPError as error:
            raise DispatchError(f"MASTER_BRIDGE_HTTP_{error.code}") from error
        except (URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DispatchError("MASTER_BRIDGE_UNAVAILABLE") from error
        if not 200 <= status_code < 300 or not isinstance(payload, dict):
            raise DispatchError("MASTER_BRIDGE_REJECTED")
        return {
            "schema": MASTER_BRIDGE_RECEIPT_SCHEMA,
            "status": "DELIVERED",
            "bridge_id": bridge_id,
            "project_id": project_id,
            "message_id": message_id,
            "endpoint": endpoint,
            "host_response": payload,
            "delivered_at": utc_now(),
        }

    def apply_seed_assets(
        self,
        *,
        bridge: dict[str, Any],
        proposal: dict[str, Any],
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = self.validate()
        credential_env = _environment_name(self.credential_env)
        token = os.environ.get(credential_env)
        if not token:
            raise DispatchError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
        project_id = _project_id(bridge.get("project_id"))
        body = json.dumps(
            {
                "project_id": project_id,
                "proposal": proposal,
                "approval": approval,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = Request(
            endpoint + "/v1/project-master/seed-assets/apply",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=max(self.timeout_seconds, 60.0)) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                status_code = response.status
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
                code = str(detail.get("error_code") or f"HTTP_{error.code}")
            except (UnicodeError, json.JSONDecodeError):
                code = f"HTTP_{error.code}"
            raise DispatchError("MASTER_SEED_APPLY_" + code) from error
        except (URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DispatchError("MASTER_SEED_APPLY_UNAVAILABLE") from error
        if not 200 <= status_code < 300 or not isinstance(payload, dict):
            raise DispatchError("MASTER_SEED_APPLY_REJECTED")
        return {
            "schema": MASTER_SEED_APPLY_RECEIPT_SCHEMA,
            "status": "DELIVERED",
            "project_id": project_id,
            "proposal_id": proposal.get("proposal_id", "UNKNOWN"),
            "endpoint": endpoint,
            "host_response": payload,
            "delivered_at": utc_now(),
        }

    def apply_integration_assets(
        self,
        *,
        bridge: dict[str, Any],
        proposal: dict[str, Any],
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = self.validate()
        credential_env = _environment_name(self.credential_env)
        token = os.environ.get(credential_env)
        if not token:
            raise DispatchError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
        project_id = _project_id(bridge.get("project_id"))
        body = json.dumps(
            {
                "project_id": project_id,
                "proposal": proposal,
                "approval": approval,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = Request(
            endpoint + "/v1/project-master/integration-assets/apply",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=max(self.timeout_seconds, 60.0)) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                status_code = response.status
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
                code = str(detail.get("error_code") or f"HTTP_{error.code}")
            except (UnicodeError, json.JSONDecodeError):
                code = f"HTTP_{error.code}"
            raise DispatchError("MASTER_INTEGRATION_APPLY_" + code) from error
        except (URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DispatchError("MASTER_INTEGRATION_APPLY_UNAVAILABLE") from error
        if not 200 <= status_code < 300 or not isinstance(payload, dict):
            raise DispatchError("MASTER_INTEGRATION_APPLY_REJECTED")
        return {
            "schema": MASTER_INTEGRATION_APPLY_RECEIPT_SCHEMA,
            "status": "DELIVERED",
            "project_id": project_id,
            "proposal_id": proposal.get("proposal_id", "UNKNOWN"),
            "endpoint": endpoint,
            "host_response": payload,
            "delivered_at": utc_now(),
        }

    def apply_skill_plan(
        self,
        *,
        bridge: dict[str, Any],
        handoff: dict[str, Any],
        approval: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = self.validate()
        credential_env = _environment_name(self.credential_env)
        token = os.environ.get(credential_env)
        if not token:
            raise DispatchError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
        project_id = _project_id(bridge.get("project_id"))
        body = json.dumps(
            {
                "project_id": project_id,
                "handoff": handoff,
                "approval": approval,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = Request(
            endpoint + "/v1/project-master/skill-plans/apply",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=max(self.timeout_seconds, 30.0)) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                status_code = response.status
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
                code = str(detail.get("error_code") or f"HTTP_{error.code}")
            except (UnicodeError, json.JSONDecodeError):
                code = f"HTTP_{error.code}"
            raise DispatchError("MASTER_SKILL_PLAN_APPLY_" + code) from error
        except (URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DispatchError("MASTER_SKILL_PLAN_APPLY_UNAVAILABLE") from error
        if not 200 <= status_code < 300 or not isinstance(payload, dict):
            raise DispatchError("MASTER_SKILL_PLAN_APPLY_REJECTED")
        return {
            "schema": MASTER_SKILL_PLAN_APPLY_RECEIPT_SCHEMA,
            "status": "DELIVERED",
            "project_id": project_id,
            "handoff_id": handoff.get("handoff_id", "UNKNOWN"),
            "endpoint": endpoint,
            "host_response": payload,
            "delivered_at": utc_now(),
        }

    def create_approved_descendant_task_frame(
        self,
        *,
        bridge: dict[str, Any],
        primary_proposal: dict[str, Any],
        governance_approval: dict[str, Any],
        source_work: dict[str, Any],
        task_frame: dict[str, Any],
    ) -> dict[str, Any]:
        endpoint = self.validate()
        credential_env = _environment_name(self.credential_env)
        token = os.environ.get(credential_env)
        if not token:
            raise DispatchError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
        project_id = _project_id(bridge.get("project_id"))
        body = json.dumps(
            {
                "primary_proposal": primary_proposal,
                "governance_approval": governance_approval,
                "source_work": source_work,
                "task_frame": task_frame,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        request = Request(
            endpoint + "/v1/project-master/task-frames/approved",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=max(self.timeout_seconds, 60.0)) as response:  # nosec B310
                payload = json.loads(response.read().decode("utf-8"))
                status_code = response.status
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
                code = str(detail.get("error_code") or f"HTTP_{error.code}")
            except (UnicodeError, json.JSONDecodeError):
                code = f"HTTP_{error.code}"
            raise DispatchError("MASTER_TASK_FRAME_APPLY_" + code) from error
        except (URLError, OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DispatchError("MASTER_TASK_FRAME_APPLY_UNAVAILABLE") from error
        if not 200 <= status_code < 300 or not isinstance(payload, dict):
            raise DispatchError("MASTER_TASK_FRAME_APPLY_REJECTED")
        return {
            "schema": MASTER_BRIDGE_RECEIPT_SCHEMA,
            "status": "DELIVERED",
            "project_id": project_id,
            "proposal_id": primary_proposal.get("proposal_id", "UNKNOWN"),
            "endpoint": endpoint,
            "host_response": payload,
            "delivered_at": utc_now(),
        }


def normalize_dispatch_request(project_id: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DispatchError("dispatch request must be an object")
    allowed = {
        "idempotency_key",
        "title",
        "instruction",
        "constraints",
        "expected_output",
        "requested_mode",
        "inbox_ref",
    }
    unknown = set(value) - allowed
    if unknown:
        raise DispatchError(
            "dispatch request contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    normalized_project = _project_id(project_id)
    title = _text(value.get("title"), "title")
    instruction = _text(value.get("instruction"), "instruction")
    if len(instruction.encode("utf-8")) > MAX_INSTRUCTION_BYTES:
        raise DispatchError("dispatch instruction exceeds size limit")
    constraints = value.get("constraints", [])
    if not isinstance(constraints, list) or not all(
        isinstance(item, str) and item.strip() for item in constraints
    ):
        raise DispatchError("constraints must be an array of non-empty text")
    expected_output = value.get("expected_output", {})
    _json_bytes(expected_output, "expected_output", MAX_RESULT_BYTES)
    requested_mode = _text(
        value.get("requested_mode", "MASTER"),
        "requested_mode",
    ).upper()
    if MODE_PATTERN.fullmatch(requested_mode) is None:
        raise DispatchError("requested_mode is invalid")
    inbox_ref = _relative_inbox_ref(value.get("inbox_ref", ".ai/inbox/MASTER"))
    idempotency_key = _text(
        value.get("idempotency_key", uuid.uuid4().hex),
        "idempotency_key",
    )
    material = {
        "project_id": normalized_project,
        "idempotency_key": idempotency_key,
        "title": title,
        "instruction": instruction,
        "constraints": [item.strip() for item in constraints],
        "expected_output": expected_output,
        "requested_mode": requested_mode,
        "inbox_ref": inbox_ref,
    }
    content_digest = _digest(material)
    return {
        "schema": DISPATCH_SCHEMA,
        "dispatch_id": "dispatch_" + content_digest[:24],
        **material,
        "content_digest": content_digest,
        "status": "QUEUED",
        "created_at": utc_now(),
    }


def normalize_dispatch_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != DISPATCH_SCHEMA:
        raise DispatchError("dispatch envelope schema is unsupported")
    required = {
        "schema",
        "dispatch_id",
        "project_id",
        "idempotency_key",
        "title",
        "instruction",
        "constraints",
        "expected_output",
        "requested_mode",
        "inbox_ref",
        "content_digest",
        "status",
        "created_at",
    }
    if set(value) != required:
        raise DispatchError("dispatch envelope fields are invalid")
    if (
        DISPATCH_ID_PATTERN.fullmatch(_text(value["dispatch_id"], "dispatch_id"))
        is None
    ):
        raise DispatchError("dispatch_id is invalid")
    if value["status"] not in DISPATCH_TRANSITIONS:
        raise DispatchError("dispatch status is invalid")
    request = normalize_dispatch_request(
        _project_id(value["project_id"]),
        {
            "idempotency_key": value["idempotency_key"],
            "title": value["title"],
            "instruction": value["instruction"],
            "constraints": value["constraints"],
            "expected_output": value["expected_output"],
            "requested_mode": value["requested_mode"],
            "inbox_ref": value["inbox_ref"],
        },
    )
    if request["content_digest"] != value["content_digest"]:
        raise DispatchError("dispatch content digest is invalid")
    normalized = dict(value)
    normalized["constraints"] = request["constraints"]
    normalized["inbox_ref"] = request["inbox_ref"]
    normalized["requested_mode"] = request["requested_mode"]
    return normalized


def transition_event(
    *,
    dispatch_id: str,
    project_id: str,
    current_status: str,
    next_status: str,
    evidence_ref: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = _text(current_status, "current_status").upper()
    target = _text(next_status, "next_status").upper()
    if current not in DISPATCH_TRANSITIONS:
        raise DispatchError("current dispatch status is invalid")
    if target not in DISPATCH_TRANSITIONS[current]:
        raise DispatchError(f"dispatch transition is invalid: {current} -> {target}")
    material = {
        "dispatch_id": _dispatch_id(dispatch_id),
        "project_id": _project_id(project_id),
        "previous_status": current,
        "status": target,
        "evidence_ref": _text(evidence_ref, "evidence_ref"),
        "details": details or {},
        "observed_at": utc_now(),
    }
    _json_bytes(material["details"], "details", MAX_RESULT_BYTES)
    return {
        "schema": DISPATCH_EVENT_SCHEMA,
        "event_id": "dispatch_event_" + _digest(material)[:24],
        **material,
    }


def normalize_result_packet(
    *,
    dispatch: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    envelope = normalize_dispatch_envelope(dispatch)
    if envelope["status"] != "STARTED":
        raise DispatchError("result packet requires a STARTED dispatch")
    if not isinstance(value, dict):
        raise DispatchError("result packet must be an object")
    allowed = {"status", "summary", "evidence_refs", "outputs"}
    if set(value) != allowed:
        raise DispatchError("result packet fields are invalid")
    status = _text(value.get("status"), "status").upper()
    if status not in TERMINAL_STATUSES:
        raise DispatchError("result status must be COMPLETED or BLOCKED")
    evidence_refs = value.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not all(
        isinstance(item, str) and item.strip() for item in evidence_refs
    ):
        raise DispatchError("evidence_refs must be an array of non-empty text")
    outputs = value.get("outputs")
    _json_bytes(outputs, "outputs", MAX_RESULT_BYTES)
    material = {
        "dispatch_id": envelope["dispatch_id"],
        "project_id": envelope["project_id"],
        "status": status,
        "summary": _text(value.get("summary"), "summary"),
        "evidence_refs": [item.strip() for item in evidence_refs],
        "outputs": outputs,
        "completed_at": utc_now(),
    }
    return {
        "schema": RESULT_PACKET_SCHEMA,
        **material,
        "result_digest": _digest(material),
    }


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _project_id(value: Any) -> str:
    result = _text(value, "project_id")
    if PROJECT_ID_PATTERN.fullmatch(result) is None:
        raise DispatchError("project_id is invalid")
    return result


def _dispatch_id(value: Any) -> str:
    result = _text(value, "dispatch_id")
    if DISPATCH_ID_PATTERN.fullmatch(result) is None:
        raise DispatchError("dispatch_id is invalid")
    return result


# Canonical default remains under .ai/inbox/. Project registration may also
# use the project-owned alternate used by resident Project Master Hosts.
ALLOWED_MASTER_INBOX_PREFIXES = (".ai/inbox/",)
ALLOWED_MASTER_INBOX_EXACT = frozenset({".ai/master/inbox"})


def _relative_inbox_ref(value: Any) -> str:
    text = _text(value, "inbox_ref").replace("\\", "/").rstrip("/")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text:
        raise DispatchError("inbox_ref must remain a relative project path")
    if text in ALLOWED_MASTER_INBOX_EXACT:
        return text
    if any(text.startswith(prefix) for prefix in ALLOWED_MASTER_INBOX_PREFIXES):
        return text
    raise DispatchError(
        "inbox_ref must be under .ai/inbox/ or exactly .ai/master/inbox"
    )


def _inbox_path(root: Path, value: Any) -> Path:
    relative = _relative_inbox_ref(value)
    candidate = root / Path(relative)
    try:
        resolved = candidate.resolve(strict=True)
        project_root = root.expanduser().resolve(strict=True)
    except OSError as error:
        raise DispatchError("MASTER_INBOX_UNAVAILABLE") from error
    try:
        resolved.relative_to(project_root)
    except ValueError as error:
        raise DispatchError("MASTER_INBOX_BOUNDARY_VIOLATION") from error
    if relative in ALLOWED_MASTER_INBOX_EXACT:
        expected = (project_root / Path(relative)).resolve(strict=True)
        if resolved != expected:
            raise DispatchError("MASTER_INBOX_BOUNDARY_VIOLATION")
    else:
        try:
            allowed = (project_root / ".ai" / "inbox").resolve(strict=True)
            resolved.relative_to(allowed)
        except (OSError, ValueError) as error:
            raise DispatchError("MASTER_INBOX_BOUNDARY_VIOLATION") from error
    current = resolved
    while current != project_root:
        if current.is_symlink():
            raise DispatchError("MASTER_INBOX_SYMLINK_FORBIDDEN")
        current = current.parent
    return resolved


def _loopback_endpoint(value: Any, *, label: str = "wake") -> str:
    endpoint = _text(value, "endpoint").rstrip("/")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.hostname is None
    ):
        raise DispatchError(f"{label} endpoint must be a plain loopback HTTP origin")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise DispatchError(
            f"{label} endpoint must use a literal loopback IP"
        ) from error
    if not address.is_loopback:
        raise DispatchError(f"{label} endpoint must remain on loopback")
    return endpoint


def _create_file(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if (
                hashlib.sha256(path.read_bytes()).hexdigest()
                != hashlib.sha256(content).hexdigest()
            ):
                raise DispatchError("DISPATCH_DELIVERY_CONFLICT")
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        if temporary.exists():
            temporary.unlink()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispatchError(f"{field} must be non-empty text")
    return value.strip()


def _environment_name(value: Any) -> str:
    name = _text(value, "credential_env")
    if re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", name) is None:
        raise DispatchError("credential_env must be an uppercase environment name")
    return name


def _json_bytes(value: Any, field: str, maximum: int) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise DispatchError(f"{field} must be JSON-compatible") from error
    if len(encoded) > maximum:
        raise DispatchError(f"{field} exceeds size limit")
    return encoded


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
