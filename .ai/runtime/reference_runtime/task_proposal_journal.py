"""Durable task proposal history without executable Git behavior.

The journal preserves what the user approved and which immutable commits later
implemented that work.  It never stages, commits, pushes, inspects, or invokes
Git.  Git remains the source-control system; its SHA is attached only after a
Host has completed ordinary source-control work.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROPOSAL_SCHEMA = "ai-career.task-proposal.v1"
APPROVAL_SCHEMA = "ai-career.task-proposal-approval.v1"
RESULT_SCHEMA = "ai-career.task-proposal-result.v1"
JOURNAL_SCHEMA = "ai-career.task-proposal-journal.v1"
DEFAULT_DATABASE_RELATIVE_PATH = Path(".ai/runtime/task_frames/task-proposals.sqlite3")
LEGACY_GIT_DATABASE_RELATIVE_PATH = Path(".ai/runtime/task_frames/git-proposals.sqlite3")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal (
    proposal_id TEXT PRIMARY KEY,
    proposal_digest TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('PROPOSED', 'APPROVED', 'COMPLETED', 'FAILED')),
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approval_json TEXT,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES proposal(proposal_id)
);
CREATE TABLE IF NOT EXISTS proposal_result_receipt (
    result_receipt_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES proposal(proposal_id)
);
CREATE TABLE IF NOT EXISTS proposal_commit_evidence (
    proposal_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    repository_ref TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    result_receipt_id TEXT NOT NULL,
    PRIMARY KEY (proposal_id, commit_sha),
    UNIQUE (proposal_id, sequence),
    FOREIGN KEY (proposal_id) REFERENCES proposal(proposal_id),
    FOREIGN KEY (result_receipt_id) REFERENCES proposal_result_receipt(result_receipt_id)
);
CREATE INDEX IF NOT EXISTS proposal_commit_evidence_by_receipt
    ON proposal_commit_evidence(result_receipt_id, sequence);
"""


@dataclass(frozen=True)
class TaskProposalError(Exception):
    error_code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


class TaskProposalJournal:
    """Persist user-facing task proposals and completed-work evidence."""

    def __init__(
        self, repository_root: Path, database_path: Path | str | None = None
    ) -> None:
        self.repository_root = repository_root.expanduser().resolve(strict=True)
        self.database_path = self._database_path(database_path)
        self.conn = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def propose(self, request: Mapping[str, Any], *, observed_at: str) -> dict[str, Any]:
        created_at = _timestamp(observed_at)
        material = {
            "repository_ref": _text(
                request.get("repository_ref") or str(self.repository_root),
                "repository_ref",
            ),
            "task_summary": _text(request.get("task_summary"), "task_summary"),
            "boundary": _text(request.get("boundary"), "boundary"),
            "request_ref": _text(request.get("request_ref"), "request_ref"),
            "scope": _json_value(request.get("scope", {}), "scope"),
            "source_ref": _optional_text(request.get("source_ref")),
            "created_at": created_at,
        }
        digest = _digest(material)
        proposal_id = "task_proposal_" + digest[:24]
        proposal = {
            "schema": PROPOSAL_SCHEMA,
            "status": "TASK_PROPOSAL_CREATED",
            "proposal_id": proposal_id,
            "proposal_digest": digest,
            **material,
            "approval_required": True,
            "authority_created": False,
            "repository_write": False,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            existing = self.conn.execute(
                "SELECT state, proposal_json FROM proposal WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                self.conn.execute("COMMIT")
                return {
                    **json.loads(str(existing["proposal_json"])),
                    "status": "TASK_PROPOSAL_REUSED",
                    "proposal_state": str(existing["state"]),
                }
            self.conn.execute(
                """
                INSERT INTO proposal(proposal_id, proposal_digest, proposal_json, state, created_at)
                VALUES (?, ?, ?, 'PROPOSED', ?)
                """,
                (proposal_id, digest, _canonical_json(proposal), created_at),
            )
            self._append_event(proposal_id, "PROPOSED", created_at, {"proposal_digest": digest})
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return proposal

    def approve(self, request: Mapping[str, Any], *, observed_at: str) -> dict[str, Any]:
        proposal_id = _text(request.get("proposal_id"), "proposal_id")
        row = self._proposal_row(proposal_id)
        if row["state"] != "PROPOSED":
            raise TaskProposalError(
                "TASK_PROPOSAL_STATE_INVALID",
                f"proposal is not awaiting approval: {row['state']}",
            )
        proposal = json.loads(str(row["proposal_json"]))
        digest = _text(request.get("proposal_digest"), "proposal_digest")
        if digest != row["proposal_digest"]:
            raise TaskProposalError(
                "TASK_PROPOSAL_APPROVAL_MISMATCH",
                "approval does not bind the stored proposal digest",
            )
        evidence_ref = _text(request.get("evidence_ref"), "evidence_ref")
        if evidence_ref == proposal["request_ref"]:
            raise TaskProposalError(
                "TASK_PROPOSAL_DISTINCT_APPROVAL_REQUIRED",
                "proposal creation and approval require distinct user input evidence",
            )
        approved_at = _timestamp(observed_at)
        approval = {
            "schema": APPROVAL_SCHEMA,
            "status": "APPROVED",
            "proposal_id": proposal_id,
            "proposal_digest": digest,
            "evidence_ref": evidence_ref,
            "approved_at": approved_at,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            updated = self.conn.execute(
                """
                UPDATE proposal SET state = 'APPROVED', approved_at = ?, approval_json = ?
                WHERE proposal_id = ? AND state = 'PROPOSED'
                """,
                (approved_at, _canonical_json(approval), proposal_id),
            )
            if updated.rowcount != 1:
                raise TaskProposalError(
                    "TASK_PROPOSAL_STATE_CONFLICT",
                    "proposal approval state changed concurrently",
                )
            self._append_event(proposal_id, "APPROVED", approved_at, {"proposal_digest": digest})
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return {
            "schema": APPROVAL_SCHEMA,
            "status": "TASK_PROPOSAL_APPROVED",
            "proposal": proposal,
            "approval": approval,
            "repository_write": False,
        }

    def record_result(self, request: Mapping[str, Any], *, observed_at: str) -> dict[str, Any]:
        proposal_id = _text(request.get("proposal_id"), "proposal_id")
        row = self._proposal_row(proposal_id)
        if row["state"] != "APPROVED":
            raise TaskProposalError(
                "TASK_PROPOSAL_STATE_INVALID",
                f"proposal cannot accept a result from state: {row['state']}",
            )
        result_status = _text(request.get("result_status"), "result_status").upper()
        if result_status not in {"PASS", "FAIL"}:
            raise TaskProposalError("TASK_RESULT_STATUS_INVALID", "result_status must be PASS or FAIL")
        recorded_at = _timestamp(observed_at)
        commits = _commit_evidence(request.get("commit_evidence", []))
        receipt_material = {
            "proposal_id": proposal_id,
            "result_status": result_status,
            "summary": _text(request.get("summary"), "summary"),
            "evidence_ref": _text(request.get("evidence_ref"), "evidence_ref"),
            "validation": _json_value(request.get("validation", {}), "validation"),
            "recorded_at": recorded_at,
        }
        receipt_id = "result_receipt_" + _digest(receipt_material)[:24]
        receipt = {
            "schema": RESULT_SCHEMA,
            "status": "TASK_RESULT_RECORDED",
            "result_receipt_id": receipt_id,
            **receipt_material,
            "commit_evidence": commits,
        }
        next_state = "COMPLETED" if result_status == "PASS" else "FAILED"
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                INSERT INTO proposal_result_receipt(result_receipt_id, proposal_id, receipt_json, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (receipt_id, proposal_id, _canonical_json(receipt), recorded_at),
            )
            for sequence, item in enumerate(commits, start=1):
                self.conn.execute(
                    """
                    INSERT INTO proposal_commit_evidence(
                        proposal_id, commit_sha, repository_ref, sequence, recorded_at, result_receipt_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_id,
                        item["commit_sha"],
                        item["repository_ref"],
                        sequence,
                        recorded_at,
                        receipt_id,
                    ),
                )
            self.conn.execute(
                "UPDATE proposal SET state = ?, completed_at = ? WHERE proposal_id = ?",
                (next_state, recorded_at, proposal_id),
            )
            self._append_event(
                proposal_id,
                f"RESULT_{result_status}",
                recorded_at,
                {"result_receipt_id": receipt_id, "commit_count": len(commits)},
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return {**receipt, "proposal_state": next_state, "repository_write": False}

    def status(self, proposal_id: str) -> dict[str, Any]:
        row = self._proposal_row(_text(proposal_id, "proposal_id"))
        events = [
            {
                "event_type": item["event_type"],
                "observed_at": item["observed_at"],
                "details": json.loads(str(item["details_json"])),
            }
            for item in self.conn.execute(
                "SELECT event_type, observed_at, details_json FROM proposal_event WHERE proposal_id = ? ORDER BY event_id",
                (row["proposal_id"],),
            )
        ]
        results = [
            json.loads(str(item["receipt_json"]))
            for item in self.conn.execute(
                "SELECT receipt_json FROM proposal_result_receipt WHERE proposal_id = ? ORDER BY recorded_at",
                (row["proposal_id"],),
            )
        ]
        commits = [
            {
                "commit_sha": item["commit_sha"],
                "repository_ref": item["repository_ref"],
                "sequence": item["sequence"],
                "recorded_at": item["recorded_at"],
                "result_receipt_id": item["result_receipt_id"],
            }
            for item in self.conn.execute(
                """
                SELECT commit_sha, repository_ref, sequence, recorded_at, result_receipt_id
                FROM proposal_commit_evidence WHERE proposal_id = ? ORDER BY sequence
                """,
                (row["proposal_id"],),
            )
        ]
        legacy_path = self.repository_root / LEGACY_GIT_DATABASE_RELATIVE_PATH
        return {
            "schema": JOURNAL_SCHEMA,
            "status": "TASK_PROPOSAL_STATUS",
            "proposal_state": row["state"],
            "proposal": json.loads(str(row["proposal_json"])),
            "approval": None if row["approval_json"] is None else json.loads(str(row["approval_json"])),
            "results": results,
            "commit_evidence": commits,
            "events": events,
            "database_path": str(self.database_path),
            "legacy_git_proposal_journal": (
                "INERT_LEGACY_PRESENT" if legacy_path.is_file() else "ABSENT"
            ),
            "repository_write": False,
        }

    def _proposal_row(self, proposal_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM proposal WHERE proposal_id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise TaskProposalError("TASK_PROPOSAL_UNKNOWN", f"proposal not found: {proposal_id}")
        return row

    def _append_event(self, proposal_id: str, event_type: str, observed_at: str, details: Mapping[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO proposal_event(proposal_id, event_type, observed_at, details_json) VALUES (?, ?, ?, ?)",
            (proposal_id, event_type, observed_at, _canonical_json(details)),
        )

    def _database_path(self, value: Path | str | None) -> Path:
        candidate = (
            self.repository_root / DEFAULT_DATABASE_RELATIVE_PATH
            if value is None
            else Path(value).expanduser()
        )
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate.resolve()


def _commit_evidence(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TaskProposalError("TASK_COMMIT_EVIDENCE_INVALID", "commit_evidence must be a list")
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, Mapping):
            raise TaskProposalError("TASK_COMMIT_EVIDENCE_INVALID", f"commit_evidence[{index}] must be an object")
        sha = _text(raw.get("commit_sha"), f"commit_evidence[{index}].commit_sha").lower()
        if not COMMIT_PATTERN.fullmatch(sha):
            raise TaskProposalError("TASK_COMMIT_EVIDENCE_INVALID", f"commit_evidence[{index}] has invalid commit_sha")
        if sha in seen:
            raise TaskProposalError("TASK_COMMIT_EVIDENCE_INVALID", "commit evidence must not repeat a SHA")
        seen.add(sha)
        items.append(
            {
                "commit_sha": sha,
                "repository_ref": _text(raw.get("repository_ref"), f"commit_evidence[{index}].repository_ref"),
            }
        )
    return items


def _json_value(value: Any, field: str) -> Any:
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as error:
        raise TaskProposalError("TASK_PROPOSAL_INVALID", f"{field} must be JSON serializable") from error


def _text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TaskProposalError("TASK_PROPOSAL_INVALID", f"{field} is required")
    return text


def _optional_text(value: Any) -> str:
    return str(value or "").strip() or "UNKNOWN"


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TaskProposalError("TASK_PROPOSAL_INVALID", "observed_at must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise TaskProposalError("TASK_PROPOSAL_INVALID", "observed_at must include timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
