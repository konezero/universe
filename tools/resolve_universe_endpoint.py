#!/usr/bin/env python3
"""Resolve the local Universe service endpoint from Host state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

RESULT_SCHEMA = "universe.local-endpoint.result.v1"
STATE_SCHEMA = "universe.local-service-state.v1"


def default_state_path() -> Path:
    override = os.environ.get("UNIVERSE_STATE_FILE")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe" / "server.json"
    return Path.home() / ".local" / "share" / "universe" / "server.json"


def resolve(state_path: Path | None = None) -> dict[str, Any]:
    path = state_path or default_state_path()
    if not path.is_file():
        return {
            "schema": RESULT_SCHEMA,
            "status": "UNIVERSE_LOCAL_ENDPOINT_MISSING",
            "state_path": str(path),
            "reasons": ["UNIVERSE_LOCAL_ENDPOINT_MISSING"],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema": RESULT_SCHEMA,
            "status": "UNIVERSE_LOCAL_ENDPOINT_INVALID",
            "state_path": str(path),
            "reasons": ["UNIVERSE_LOCAL_ENDPOINT_INVALID"],
        }
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        return {
            "schema": RESULT_SCHEMA,
            "status": "UNIVERSE_LOCAL_ENDPOINT_INVALID",
            "state_path": str(path),
            "reasons": ["UNIVERSE_LOCAL_ENDPOINT_SCHEMA_INVALID"],
        }
    endpoint = str(payload.get("endpoint") or "").strip()
    token = str(payload.get("token") or "").strip()
    if not endpoint or not token:
        return {
            "schema": RESULT_SCHEMA,
            "status": "UNIVERSE_LOCAL_ENDPOINT_INVALID",
            "state_path": str(path),
            "reasons": ["UNIVERSE_LOCAL_ENDPOINT_FIELDS_MISSING"],
        }
    return {
        "schema": RESULT_SCHEMA,
        "status": "UNIVERSE_LOCAL_ENDPOINT_READY",
        "state_path": str(path),
        "endpoint": endpoint,
        "token": token,
        "database": str(payload.get("database") or ""),
        "pid": payload.get("pid"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve local Universe endpoint")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    result = resolve(Path(args.state_file) if args.state_file else None)
    Path(args.result).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": result["status"], "endpoint": result.get("endpoint")}))
    return 0 if result["status"] == "UNIVERSE_LOCAL_ENDPOINT_READY" else 4


if __name__ == "__main__":
    sys.exit(main())
