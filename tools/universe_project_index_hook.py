#!/usr/bin/env python3
"""Update one project-owned file index from a bounded file-change hook."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from universe_file_index import FileIndexError, sync_project_index_from_hook


REQUEST_SCHEMA = "universe.project-index-hook.request.v1"
RESULT_SCHEMA = "universe.project-index-hook.result.v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FileIndexError("PROJECT_INDEX_HOOK_REQUEST_INVALID", "request must be an object")
    return value


def run(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema") != REQUEST_SCHEMA:
        raise FileIndexError(
            "PROJECT_INDEX_HOOK_REQUEST_INVALID", "request schema is invalid"
        )
    changed = value.get("changed_paths")
    if changed is not None and (
        not isinstance(changed, list)
        or any(not isinstance(item, str) or not item.strip() for item in changed)
    ):
        raise FileIndexError(
            "PROJECT_INDEX_HOOK_REQUEST_INVALID",
            "changed_paths must be an array of non-empty strings",
        )
    project_id = str(value.get("project_id") or "").strip()
    project_root = str(value.get("project_root") or "").strip()
    if not project_id or not project_root:
        raise FileIndexError(
            "PROJECT_INDEX_HOOK_REQUEST_INVALID",
            "project_id and project_root are required",
        )
    result = sync_project_index_from_hook(
        project_id=project_id,
        project_root=Path(project_root),
        mode=str(value.get("mode") or ""),
        anchor_id=str(value.get("anchor_id") or ""),
        changed_paths=changed,
    )
    return {
        "schema": RESULT_SCHEMA,
        "status": result["status"],
        "project_index": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run(_load(args.request))
        exit_code = 0
    except (FileIndexError, OSError, ValueError) as error:
        result = {
            "schema": RESULT_SCHEMA,
            "status": "PROJECT_INDEX_HOOK_BLOCKED",
            "error_code": getattr(error, "error_code", "PROJECT_INDEX_HOOK_FAILED"),
            "detail": getattr(error, "detail", str(error)),
        }
        exit_code = 4
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": result["status"]}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
