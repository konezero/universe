"""Execute an authorized local service Action and record evidence, without permits.

User/automation authorization belongs to the invoking Host. This adapter
records its instruction reference; that reference and the resulting audit id
are not credentials or permission grants. The service authenticates the Host
and revalidates the target process at the actual lifecycle boundary.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class LifecycleError(ValueError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


def host_state_path():
    return Path(os.environ.get("UNIVERSE_STATE_FILE") or Path(os.environ["LOCALAPPDATA"]) / "Universe/server.json").resolve()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ServiceExecution:
    """Fixed local Action transport with an append-only execution history."""
    def __init__(self, repo_root, session_id=None, *, state_path=None):
        self.repo = Path(repo_root).resolve(strict=True)
        self.session_id = session_id
        self.state_path = Path(state_path or host_state_path()).resolve()
        self.ledger = self.repo / ".ai/runtime/state/service_execution.sqlite3"

    def _state(self):
        raw = self.state_path.read_bytes()
        state = json.loads(raw.decode("utf-8-sig"))
        url = urlsplit(state.get("endpoint", ""))
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise LifecycleError("LIFECYCLE_ENDPOINT_INVALID", "Host state must name the local service")
        if type(state.get("pid")) is not int or state["pid"] <= 0 or not state.get("token"):
            raise LifecycleError("LIFECYCLE_STATE_INVALID", "Host service PID and control token are required")
        return state, hashlib.sha256(raw).hexdigest()

    def _record(self, *, operation_id, phase, instruction_ref=None, expected_pid=None, state_digest=None, error_code=None):
        record = {"schema": "universe.service-execution-audit.v1", "event_id": "event_" + uuid.uuid4().hex,
                  "operation_id": operation_id, "phase": phase, "observed_at": datetime.now(timezone.utc).isoformat(),
                  "action_id": "service.restart", "target": str(self.state_path), "session_id": self.session_id,
                  "instruction_ref": instruction_ref, "expected_pid": expected_pid, "state_sha256": state_digest,
                  "error_code": error_code, "authority_created": False}
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.ledger, timeout=15)
        try:
            with connection:
                # Old binding/permit rows remain historical data and are never read.
                connection.execute("CREATE TABLE IF NOT EXISTS execution_event (event_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, phase TEXT NOT NULL, record_json TEXT NOT NULL)")
                connection.execute("INSERT INTO execution_event VALUES (?, ?, ?, ?)", (record["event_id"], operation_id, phase, json.dumps(record)))
        finally:
            connection.close()
        return {"status": "EXECUTION_AUDIT_RECORDED", "event_id": record["event_id"], "operation_id": operation_id, "phase": phase}

    def execute(self, request, *, instruction_ref):
        if not isinstance(request, dict) or set(request) != {"request_id", "expected_pid"} or not isinstance(request.get("request_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request["request_id"]):
            raise LifecycleError("LIFECYCLE_REQUEST_INVALID", "request_id and expected_pid are required")
        if type(request["expected_pid"]) is not int or request["expected_pid"] <= 0:
            raise LifecycleError("LIFECYCLE_REQUEST_INVALID", "expected_pid must be a positive integer")
        if not isinstance(instruction_ref, str) or not instruction_ref.strip() or len(instruction_ref) > 1024:
            raise LifecycleError("LIFECYCLE_EVIDENCE_REQUIRED", "record the existing user instruction or automation run reference")
        evidence = {"operation_id": request["request_id"], "instruction_ref": instruction_ref, "expected_pid": request["expected_pid"]}
        try:
            self._record(**evidence, phase="ATTEMPTED")
        except (OSError, sqlite3.Error) as error:
            raise LifecycleError("EXECUTION_AUDIT_UNAVAILABLE", "could not record the execution attempt") from error
        try:
            state, state_digest = self._state()
            if state["pid"] != request["expected_pid"]:
                raise LifecycleError("LIFECYCLE_INSTANCE_CHANGED", "service instance changed; observe service.status")
            evidence["state_digest"] = state_digest
            self._record(**evidence, phase="VALIDATED")
            result = self._dispatch(state, {"action_id": "service.restart", "request": request})
        except (LifecycleError, OSError, ValueError, sqlite3.Error) as error:
            code = getattr(error, "code", "LIFECYCLE_HOST_ERROR")
            phase = "UNCONFIRMED" if code == "LIFECYCLE_DISPATCH_UNCERTAIN" else "REJECTED"
            try:
                self._record(**evidence, phase=phase, error_code=code)
            except (OSError, sqlite3.Error):
                pass  # Original attempt exists; preserve the actual dispatch error.
            if isinstance(error, LifecycleError):
                raise
            raise LifecycleError(code, type(error).__name__) from error
        # ACCEPTED is not completion. The service operation ledger is the owner of
        # RUNNING/COMPLETED/FAILED and is queried with this same operation_id.
        try:
            audit = self._record(**evidence, phase="DISPATCHED")
        except (OSError, sqlite3.Error):
            audit = {"status": "EXECUTION_AUDIT_FAILED", "operation_id": request["request_id"]}
        return {"status": "LIFECYCLE_ACTION_DISPATCHED", "operation_id": request["request_id"], "result": result, "audit": audit}

    def _dispatch(self, state, body):
        request = Request(state["endpoint"].rstrip("/") + "/v1/actions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + state["token"]}, method="POST")
        try:
            with build_opener(NoRedirect).open(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            raise LifecycleError("LIFECYCLE_HTTP_REJECTED", "service.restart /v1/actions HTTP " + str(error.code)) from error
        except Exception as error:
            raise LifecycleError("LIFECYCLE_DISPATCH_UNCERTAIN", "service.restart /v1/actions: " + type(error).__name__ + "; query operation_id=" + str(body["request"].get("request_id", ""))) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("execute",))
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--session-id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--request", required=True, help="JSON with request_id and expected_pid")
    parser.add_argument("--instruction-ref", required=True, help="existing user instruction or authorized automation run reference; never a credential")
    args = parser.parse_args()
    try:
        payload = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
        result = ServiceExecution(args.repo_root, args.session_id).execute(payload, instruction_ref=args.instruction_ref)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (LifecycleError, OSError, ValueError) as error:
        print(json.dumps({"status": "LIFECYCLE_EXECUTION_FAILED", "error": {"code": getattr(error, "code", "LIFECYCLE_REQUEST_INVALID"), "operation": "service.restart", "detail": str(error) if isinstance(error, LifecycleError) else type(error).__name__}}))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
