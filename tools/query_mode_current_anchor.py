#!/usr/bin/env python3
"""Query the Mode Current Anchor and the bound session SQL path."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = "ai-career.mode-current-anchor-query.request.v1"
RESULT_SCHEMA = "ai-career.mode-current-anchor-query.result.v1"


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("request root must be an object")
    return payload


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result(status: str, **fields: Any) -> dict[str, Any]:
    payload = {"schema": RESULT_SCHEMA, "status": status}
    payload.update(fields)
    return payload


def query(repo_root: Path, request: dict[str, Any]) -> dict[str, Any]:
    mode = str(request.get("mode") or "").strip().upper()
    expected = str(request.get("anchor_id") or "").strip()
    session_id = str(request.get("session_id") or "").strip()
    if not mode:
        return _result("MODE_REQUIRED", reasons=["MODE_REQUIRED"])
    store = repo_root / ".ai" / "runtime" / "state" / "project_runtime.sqlite3"
    if not store.is_file():
        return _result(
            "MODE_CURRENT_ANCHOR_STORE_MISSING",
            mode=mode,
            reasons=["MODE_CURRENT_ANCHOR_STORE_MISSING"],
        )
    connection = sqlite3.connect(f"file:{store.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """
            SELECT mode, frame_id, anchor_id, state
            FROM mode_current_anchor
            WHERE mode = ?
            """,
            (mode,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return _result(
            "MODE_CURRENT_ANCHOR_MISSING",
            mode=mode,
            reasons=["MODE_CURRENT_ANCHOR_MISSING"],
        )
    stored_anchor = str(row[2])
    if expected and expected != stored_anchor:
        return _result(
            "MODE_CURRENT_ANCHOR_MISMATCH",
            mode=mode,
            requested_anchor_id=expected,
            anchor_id=stored_anchor,
            reasons=["MODE_CURRENT_ANCHOR_MISMATCH"],
        )
    session_sql = ""
    session_sql_present = False
    if session_id:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
        session_path = repo_root / ".ai" / "runtime" / "session_store" / f"session-{digest}.sqlite3"
        session_sql = str(session_path)
        session_sql_present = session_path.is_file()
    return _result(
        "MODE_CURRENT_ANCHOR_READY",
        mode=str(row[0]),
        frame_id=str(row[1]),
        anchor_id=stored_anchor,
        state=str(row[3]),
        session_id=session_id,
        session_sql_path=session_sql,
        session_sql_present=session_sql_present,
        companions_are_refs=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Query Mode Current Anchor")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    result = query(Path(args.repo_root), _load(Path(args.request)))
    _write(Path(args.result), result)
    print(json.dumps({"status": result["status"], "anchor_id": result.get("anchor_id")}))
    return 0 if result["status"] == "MODE_CURRENT_ANCHOR_READY" else 4


if __name__ == "__main__":
    sys.exit(main())
