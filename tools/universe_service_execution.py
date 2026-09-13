"""Host-owned strict lifecycle bindings and one-time receipts for service.restart.

The installed Runtime's source Work Receipt never authorizes this operation.
The Host must separately attest the user's exact lifecycle instruction through
bind(); execute() rechecks the current Anchor and consumes a short-lived permit
immediately before dispatching the fixed, authenticated Action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class LifecycleError(ValueError):
    def __init__(self, code, detail):
        self.code, self.detail = code, detail
        super().__init__(detail)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def host_state_path():
    return Path(os.environ.get("UNIVERSE_STATE_FILE") or Path(os.environ["LOCALAPPDATA"]) / "Universe/server.json").resolve()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ServiceExecution:
    """Local Host adapter. Never exposed as a remotely writable HTTP endpoint."""
    def __init__(self, repo_root, session_id, *, state_path=None):
        self.repo = Path(repo_root).resolve(strict=True)
        self.session_id = session_id
        self.state_path = Path(state_path or host_state_path()).resolve()
        self.ledger = self.repo / ".ai/runtime/state/service_execution.sqlite3"

    def _anchor(self):
        runtime = str(self.repo / ".ai/runtime")
        if runtime not in sys.path:
            sys.path.insert(0, runtime)
        from reference_runtime.project_runtime_store import ProjectRuntimeStore
        from reference_runtime.mode_registry_runtime import validate_mode_registry
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in self.session_id)
        bundle = json.loads((self.repo / ".ai/runtime/state/anchor_work" / (safe + ".json")).read_text(encoding="utf-8"))
        snapshot = bundle["snapshot"]
        store = ProjectRuntimeStore(self.repo)
        try:
            registry = store.mode_registry_snapshot()
            validate_mode_registry(registry["registry"], path=self.repo / ".ai/runtime/state/project_runtime.sqlite3").resolve(bundle["mode"])
            current = store.mode_current_anchor(bundle["mode"])
        finally:
            store.close()
        if snapshot.get("session_id") != self.session_id or not current or current.get("anchor_id") != snapshot.get("anchor_id") or str(current.get("frame_id") or "current") != snapshot.get("frame_id"):
            raise LifecycleError("LIFECYCLE_ANCHOR_CHANGED", "current Session Anchor does not match")
        return {key: snapshot[key] for key in ("session_id", "frame_id", "anchor_id", "source_commit")}

    def _state(self):
        raw = self.state_path.read_bytes()
        state = json.loads(raw.decode("utf-8-sig"))
        url = urlsplit(state.get("endpoint", ""))
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise LifecycleError("LIFECYCLE_ENDPOINT_INVALID", "Host state must name the local service")
        if type(state.get("pid")) is not int or state["pid"] <= 0 or not state.get("token"):
            raise LifecycleError("LIFECYCLE_STATE_INVALID", "Host service PID and control token are required")
        return state, hashlib.sha256(raw).hexdigest()

    def prepare(self):
        state, preimage = self._state()
        proposal = {"schema": "universe.service-execution-proposal.v1", "repo_root": str(self.repo), **self._anchor(),
                    "operation": "COMMAND", "action_id": "service.restart", "target": str(self.state_path),
                    "endpoint": state["endpoint"].rstrip("/"), "target_preimage_sha256": preimage,
                    "request": {"request_id": "restart-" + uuid.uuid4().hex, "expected_pid": state["pid"]}}
        return {**proposal, "proposal_id": "lifecycle_" + digest(proposal)[:24]}

    def _validate(self, proposal):
        if set(proposal) != {"schema", "repo_root", "session_id", "frame_id", "anchor_id", "source_commit", "operation", "action_id", "target", "endpoint", "target_preimage_sha256", "request", "proposal_id"}:
            raise LifecycleError("LIFECYCLE_PROPOSAL_INVALID", "unexpected proposal fields")
        material = {k: v for k, v in proposal.items() if k != "proposal_id"}
        if proposal["proposal_id"] != "lifecycle_" + digest(material)[:24] or proposal["schema"] != "universe.service-execution-proposal.v1" or proposal["operation"] != "COMMAND" or proposal["action_id"] != "service.restart":
            raise LifecycleError("LIFECYCLE_PROPOSAL_INVALID", "proposal content mismatch")
        if proposal["repo_root"] != str(self.repo) or proposal["target"] != str(self.state_path):
            raise LifecycleError("LIFECYCLE_TARGET_CHANGED", "Host target changed")
        if any(proposal.get(k) != v for k, v in self._anchor().items()):
            raise LifecycleError("LIFECYCLE_ANCHOR_CHANGED", "Session Anchor changed")
        state, preimage = self._state()
        import re
        request = proposal["request"]
        if not isinstance(request, dict) or set(request) != {"request_id", "expected_pid"} or not isinstance(request["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request["request_id"]):
            raise LifecycleError("LIFECYCLE_PROPOSAL_INVALID", "invalid Action request")
        if type(request["expected_pid"]) is not int or request["expected_pid"] != state["pid"] or proposal["endpoint"] != state["endpoint"].rstrip("/") or proposal["target_preimage_sha256"] != preimage:
            raise LifecycleError("LIFECYCLE_INSTANCE_CHANGED", "service instance changed; prepare again")
        return state

    def _connect(self):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.ledger, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.executescript("CREATE TABLE IF NOT EXISTS binding (id TEXT PRIMARY KEY, proposal TEXT NOT NULL, approval TEXT NOT NULL); CREATE TABLE IF NOT EXISTS permit (id TEXT PRIMARY KEY, binding_id TEXT NOT NULL, expires REAL NOT NULL, consumed REAL);")
        return connection

    def bind(self, proposal, approval):
        self._validate(proposal)
        expected = {"status": "APPROVED", "proposal_id": proposal["proposal_id"], "action_id": "service.restart", "target": proposal["target"], "expected_pid": proposal["request"]["expected_pid"]}
        if set(approval) != set(expected) | {"instruction_ref"} or any(approval.get(k) != v for k, v in expected.items()) or not isinstance(approval.get("instruction_ref"), str) or not approval["instruction_ref"].strip() or approval["instruction_ref"] in {"UNKNOWN", "UNASSIGNED"}:
            raise LifecycleError("LIFECYCLE_APPROVAL_REQUIRED", "Host-attested approval must match this exact restart proposal")
        connection = self._connect()
        try:
            with connection:
                row = connection.execute("SELECT proposal, approval FROM binding WHERE id=?", (proposal["proposal_id"],)).fetchone()
                if row and (json.loads(row["proposal"]) != proposal or json.loads(row["approval"]) != approval):
                    raise LifecycleError("LIFECYCLE_BINDING_CONFLICT", "binding already has different approval")
                connection.execute("INSERT OR IGNORE INTO binding VALUES (?, ?, ?)", (proposal["proposal_id"], json.dumps(proposal), json.dumps(approval)))
        finally:
            connection.close()
        return {"status": "EXECUTION_BINDING_APPLIED", "binding_id": proposal["proposal_id"], "instruction_ref": approval["instruction_ref"]}

    def check(self, binding_id):
        connection = self._connect()
        try:
            with connection:
                row = connection.execute("SELECT proposal FROM binding WHERE id=?", (binding_id,)).fetchone()
                if not row:
                    raise LifecycleError("LIFECYCLE_BINDING_REQUIRED", "exact lifecycle binding is required")
                self._validate(json.loads(row["proposal"]))
                receipt_id, expires = "permit_" + uuid.uuid4().hex, time.time() + 30
                connection.execute("INSERT INTO permit VALUES (?, ?, ?, NULL)", (receipt_id, binding_id, expires))
                return {"status": "EXECUTION_GUARD_PERMITTED", "receipt_id": receipt_id, "binding_id": binding_id, "expires_at": expires, "one_time": True}
        finally:
            connection.close()

    def execute(self, receipt_id):
        connection = self._connect()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT permit.*, binding.proposal FROM permit JOIN binding ON binding.id=permit.binding_id WHERE permit.id=?", (receipt_id,)).fetchone()
                if not row or row["consumed"] is not None or row["expires"] < time.time():
                    raise LifecycleError("LIFECYCLE_PERMIT_INVALID", "permit is absent, consumed, or expired")
                proposal = json.loads(row["proposal"])
                state = self._validate(proposal)
                connection.execute("UPDATE permit SET consumed=? WHERE id=? AND consumed IS NULL", (time.time(), receipt_id))
            # The fixed Host hook dispatches only the consumed proposal. No caller URL,
            # shell, executable, actor, token, or request substitution is accepted.
            return self._dispatch(proposal, state["token"], receipt_id)
        finally:
            connection.close()

    def _dispatch(self, proposal, token, receipt_id):
        body = {"action_id": "service.restart", "request": proposal["request"]}
        request = Request(proposal["endpoint"] + "/v1/actions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
        try:
            with build_opener(NoRedirect).open(request, timeout=15) as response:
                result = json.load(response)
        except Exception as error:
            # A lost response can follow acceptance. Query this operation_id; never
            # manufacture another request to compensate for transport uncertainty.
            raise LifecycleError("LIFECYCLE_DISPATCH_UNCERTAIN", "service.restart /v1/actions: " + type(error).__name__ + "; query operation_id=" + proposal["request"]["request_id"]) from error
        return {"status": "LIFECYCLE_ACTION_DISPATCHED", "receipt_id": receipt_id, "operation_id": proposal["request"]["request_id"], "result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "bind", "check", "execute"))
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--session-id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--request", help="UTF-8 JSON file for bind/check/execute")
    args = parser.parse_args()
    if not args.session_id:
        parser.error("current Host session-id is required")
    host = ServiceExecution(args.repo_root, args.session_id)
    payload = json.loads(Path(args.request).read_text(encoding="utf-8-sig")) if args.request else {}
    try:
        if args.operation == "prepare":
            result = host.prepare()
        elif args.operation == "bind":
            result = host.bind(payload["proposal"], payload["approval"])
        elif args.operation == "check":
            result = host.check(payload["binding_id"])
        else:
            result = host.execute(payload["receipt_id"])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except LifecycleError as error:
        print(json.dumps({"status": "LIFECYCLE_EXECUTION_BLOCKED", "error": {"code": error.code, "operation": "service.restart", "detail": error.detail}}))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
