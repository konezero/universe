#!/usr/bin/env python3
"""Query the local Universe service for retrieval and mechanical file search."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from universe_project_index_hook import REQUEST_SCHEMA as INDEX_HOOK_SCHEMA
from universe_project_index_hook import run as run_project_index_hook

REQUEST_SCHEMA = "universe.local-query.request.v1"
RESULT_SCHEMA = "universe.local-query.result.v1"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("request root must be an object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _call(endpoint: str, token: str, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        endpoint.rstrip("/") + path,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise SystemExit("Universe response root must be an object")
            return response.status, payload
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except Exception:
            payload = {"status": "TRANSPORT_FAILED", "detail": str(error)}
        if not isinstance(payload, dict):
            payload = {"status": "TRANSPORT_FAILED", "detail": str(error)}
        return error.code, payload


def run(request: dict[str, Any]) -> dict[str, Any]:
    endpoint = str(request.get("endpoint") or "").strip()
    token = str(request.get("token") or "").strip()
    project_id = str(request.get("project_id") or "").strip()
    operation = str(request.get("operation") or "").strip().lower()
    mode = str(request.get("mode") or "").strip()
    anchor_id = str(request.get("anchor_id") or "").strip()
    if not project_id or not operation:
        return {
            "schema": RESULT_SCHEMA,
            "status": "QUERY_BLOCKED",
            "reasons": ["REQUEST_FIELDS_REQUIRED"],
        }
    if not mode or not anchor_id:
        return {
            "schema": RESULT_SCHEMA,
            "status": "QUERY_BLOCKED",
            "reasons": ["MODE_CURRENT_ANCHOR_REQUIRED"],
            "detail": "Use the Mode Current Anchor. session.md is a ref only.",
        }
    body = {
        "mode": mode,
        "anchor_id": anchor_id,
        "session_id": str(request.get("session_id") or ""),
        "query": str(request.get("query") or ""),
        "limit": request.get("limit", 20),
        "node_ids": request.get("node_ids") or [],
    }
    if operation == "sync-index":
        project_root = str(request.get("project_root") or "").strip()
        if not project_root:
            return {
                "schema": RESULT_SCHEMA,
                "status": "QUERY_BLOCKED",
                "reasons": ["PROJECT_ROOT_REQUIRED"],
            }
        payload = run_project_index_hook(
            {
                "schema": INDEX_HOOK_SCHEMA,
                "project_id": project_id,
                "project_root": project_root,
                "mode": mode,
                "anchor_id": anchor_id,
                "changed_paths": request.get("changed_paths"),
            }
        )
        status = 200
    elif not endpoint or not token:
        return {
            "schema": RESULT_SCHEMA,
            "status": "QUERY_BLOCKED",
            "reasons": ["UNIVERSE_ENDPOINT_FIELDS_REQUIRED"],
        }
    elif operation == "search":
        status, payload = _call(
            endpoint, token, "POST", f"/v1/projects/{project_id}/file-index/search", body
        )
    elif operation == "retrieval":
        status, payload = _call(
            endpoint, token, "POST", f"/v1/projects/{project_id}/retrieval-context", body
        )
    else:
        return {
            "schema": RESULT_SCHEMA,
            "status": "QUERY_BLOCKED",
            "reasons": ["OPERATION_UNSUPPORTED"],
        }
    return {
        "schema": RESULT_SCHEMA,
        "status": "QUERY_COMPLETED" if status < 400 else "QUERY_BLOCKED",
        "http_status": status,
        "operation": operation,
        "mode": mode,
        "anchor_id": anchor_id,
        "result": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Query local Universe retrieval and file index")
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request = _load_json(Path(args.request))
    result = run(request)
    _write_json(Path(args.result), result)
    print(json.dumps({"status": result.get("status"), "http_status": result.get("http_status")}))
    return 0 if result.get("status") == "QUERY_COMPLETED" else 4


if __name__ == "__main__":
    sys.exit(main())
