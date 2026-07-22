"""File-backed push proposal and approval journal.

Proposal creation and approval do not require a live Session Runtime. Actual
push mutation still imports one approved action into the process-local
Execution Binding and Guard path.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .git_command_gateway import (
        LOCAL_GIT_TIMEOUT_SECONDS,
        REMOTE_GIT_TIMEOUT_SECONDS,
    )
else:
    from git_command_gateway import (
        LOCAL_GIT_TIMEOUT_SECONDS,
        REMOTE_GIT_TIMEOUT_SECONDS,
    )


PROPOSAL_SCHEMA = "ai-career.git-proposal.v2"
APPROVAL_SCHEMA = "ai-career.git-proposal-approval.v1"
RESULT_SCHEMA = "ai-career.git-proposal-result.v1"
JOURNAL_SCHEMA = "ai-career.git-proposal-journal.v1"
DEFAULT_DATABASE_RELATIVE_PATH = Path(
    ".ai/runtime/task_frames/git-proposals.sqlite3"
)
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal (
    proposal_id TEXT PRIMARY KEY,
    proposal_kind TEXT NOT NULL CHECK (proposal_kind = 'PUSH'),
    proposal_digest TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('PROPOSED', 'APPROVED', 'EXECUTING', 'COMPLETED', 'FAILED')
    ),
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approval_json TEXT,
    completed_at TEXT,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS proposal_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposal_scope (
    proposal_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    FOREIGN KEY (proposal_id) REFERENCES proposal(proposal_id)
);
CREATE INDEX IF NOT EXISTS proposal_scope_latest
    ON proposal_scope(session_id, mode, proposal_sequence DESC);
"""


@dataclass(frozen=True)
class GitProposalError(Exception):
    error_code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


class GitProposalJournal:
    """Persist exact push proposals outside Session memory."""

    def __init__(
        self,
        repository_root: Path,
        database_path: Path | str | None = None,
    ) -> None:
        self.repository_root = repository_root.expanduser().resolve(strict=True)
        if not (self.repository_root / ".git").exists():
            raise GitProposalError(
                "GIT_REPOSITORY_REQUIRED",
                f"repository root is not a Git work tree: {self.repository_root}",
            )
        self.database_path = self._database_path(database_path)
        self.conn = sqlite3.connect(
            self.database_path,
            timeout=5,
            isolation_level=None,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def propose_push(
        self,
        request: Mapping[str, Any],
        *,
        observed_at: str,
    ) -> dict[str, Any]:
        session_id = _required_text(request.get("session_id"), "session_id")
        mode = _scope_mode(request.get("mode"))
        commit_sha = str(request.get("commit_sha", "")).strip().lower() or self._head()
        if not COMMIT_PATTERN.fullmatch(commit_sha) or not self._commit_exists(commit_sha):
            raise GitProposalError(
                "GIT_PUSH_COMMIT_INVALID",
                "commit_sha must identify an existing immutable local commit",
            )
        branch = _required_text(
            request.get("target_branch") or self._branch(),
            "target_branch",
        )
        if branch != self._branch() or commit_sha != self._head():
            raise GitProposalError(
                "GIT_PUSH_HEAD_MISMATCH",
                "push proposal must bind the current branch HEAD",
            )
        remote = _required_text(request.get("remote") or "origin", "remote")
        if remote != "origin":
            raise GitProposalError(
                "GIT_PUSH_REMOTE_UNSUPPORTED",
                "only origin is supported by the bounded Git gateway",
            )
        expected_remote_head = self._remote_head(remote, branch)
        if expected_remote_head != "ABSENT" and not self._is_ancestor(
            expected_remote_head, commit_sha
        ):
            raise GitProposalError(
                "GIT_PUSH_NON_FAST_FORWARD",
                "local commit is not a descendant of the observed remote head",
            )
        material = {
            "proposal_kind": "PUSH",
            "repository_root": str(self.repository_root),
            "session_id": session_id,
            "mode": mode,
            "commit_sha": commit_sha,
            "branch": self._branch(),
            "remote": remote,
            "target_branch": branch,
            "expected_remote_head": expected_remote_head,
            "task_summary": _required_text(
                request.get("task_summary"), "task_summary"
            ),
            "request_ref": _evidence_ref(request.get("request_ref"), "request_ref"),
            "local_commit": False,
            "push": True,
            "actions": [
                {
                    "action": "PUSH",
                    "command_argv": [
                        "git",
                        "push",
                        remote,
                        f"HEAD:refs/heads/{branch}",
                    ],
                }
            ],
            "created_at": _timestamp(observed_at),
        }
        existing = self._matching_active_scoped_proposal(
            session_id=session_id,
            mode=mode,
            commit_sha=commit_sha,
            branch=material["branch"],
            remote=remote,
            target_branch=branch,
            expected_remote_head=expected_remote_head,
        )
        if existing is not None:
            proposal = json.loads(str(existing["proposal_json"]))
            return {
                **proposal,
                "status": "GIT_PROPOSAL_REUSED",
                "proposal_state": existing["state"],
            }
        return self._insert_proposal(material)

    def approve(
        self,
        request: Mapping[str, Any],
        *,
        observed_at: str,
    ) -> dict[str, Any]:
        proposal_id = _required_text(request.get("proposal_id"), "proposal_id")
        row = self._proposal_row(proposal_id)
        if row["state"] != "PROPOSED":
            raise GitProposalError(
                "GIT_PROPOSAL_STATE_INVALID",
                f"proposal is not awaiting approval: {row['state']}",
            )
        proposal = json.loads(str(row["proposal_json"]))
        if proposal.get("proposal_kind") != "PUSH":
            raise GitProposalError(
                "GIT_PROPOSAL_KIND_UNSUPPORTED",
                "only PUSH proposals can be approved",
            )
        digest = _required_text(request.get("proposal_digest"), "proposal_digest")
        if digest != row["proposal_digest"] or digest != _digest(
            _proposal_material(proposal)
        ):
            raise GitProposalError(
                "GIT_PROPOSAL_APPROVAL_MISMATCH",
                "approval does not bind the stored proposal digest",
            )
        approval_evidence_ref = _evidence_ref(
            request.get("evidence_ref"), "evidence_ref"
        )
        if approval_evidence_ref == proposal["request_ref"]:
            raise GitProposalError(
                "GIT_PROPOSAL_DISTINCT_APPROVAL_REQUIRED",
                "push proposal creation and approval require distinct user input evidence",
            )
        approved_at = _timestamp(observed_at)
        approval = {
            "schema": APPROVAL_SCHEMA,
            "status": "APPROVED",
            "proposal_id": proposal_id,
            "proposal_digest": digest,
            "evidence_ref": approval_evidence_ref,
            "authority_source_ref": _evidence_ref(
                request.get("authority_source_ref"), "authority_source_ref"
            ),
            "approved_at": approved_at,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            updated = self.conn.execute(
                """
                UPDATE proposal
                SET state = 'APPROVED', approved_at = ?, approval_json = ?
                WHERE proposal_id = ? AND state = 'PROPOSED'
                """,
                (approved_at, _canonical_json(approval), proposal_id),
            )
            if updated.rowcount != 1:
                raise GitProposalError(
                    "GIT_PROPOSAL_STATE_CONFLICT",
                    "proposal approval state changed concurrently",
                )
            self._append_event(
                proposal_id,
                "APPROVED",
                approved_at,
                {"proposal_digest": digest},
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return {
            "schema": APPROVAL_SCHEMA,
            "status": "GIT_PROPOSAL_APPROVED",
            "proposal": proposal,
            "approval": approval,
            "repository_write": False,
        }

    def approved_action(
        self,
        *,
        proposal_id: str,
        action: str,
    ) -> dict[str, Any]:
        row = self._proposal_row(proposal_id)
        return self._approved_action_from_row(row, action=action)

    def latest_scoped_proposal(
        self,
        *,
        session_id: str,
        mode: str,
    ) -> dict[str, Any]:
        row = self._latest_scoped_row(session_id=session_id, mode=mode)
        if row is None:
            raise GitProposalError(
                "GIT_PROPOSAL_SCOPE_UNKNOWN",
                "no push proposal exists for the current session and Mode",
            )
        return {
            "schema": JOURNAL_SCHEMA,
            "status": "GIT_PROPOSAL_SCOPE_CURRENT",
            "proposal_state": row["state"],
            "proposal": json.loads(str(row["proposal_json"])),
            "repository_write": False,
        }

    def approved_scoped_action(
        self,
        *,
        session_id: str,
        mode: str,
        action: str,
        proposal_id: str = "",
    ) -> dict[str, Any]:
        normalized_session_id = _required_text(session_id, "session_id")
        normalized_mode = _scope_mode(mode)
        explicit_proposal_id = proposal_id.strip()
        if explicit_proposal_id:
            row = self._proposal_row(explicit_proposal_id)
            proposal_scope = self._proposal_scope(row["proposal_id"])
            if proposal_scope != (normalized_session_id, normalized_mode):
                raise GitProposalError(
                    "GIT_PROPOSAL_SCOPE_MISMATCH",
                    "push proposal does not belong to the current session and Mode",
                )
        else:
            row = self._latest_scoped_row(
                session_id=normalized_session_id,
                mode=normalized_mode,
            )
            if row is None:
                raise GitProposalError(
                    "GIT_PROPOSAL_SCOPE_UNKNOWN",
                    "no push proposal exists for the current session and Mode",
                )
        return self._approved_action_from_row(row, action=action)

    def _approved_action_from_row(
        self,
        row: sqlite3.Row,
        *,
        action: str,
    ) -> dict[str, Any]:
        if row["state"] not in {"APPROVED", "EXECUTING"}:
            raise GitProposalError(
                "GIT_PROPOSAL_NOT_APPROVED",
                f"proposal cannot be imported from state: {row['state']}",
            )
        proposal = json.loads(str(row["proposal_json"]))
        approval = json.loads(str(row["approval_json"]))
        if proposal.get("proposal_kind") != "PUSH":
            raise GitProposalError(
                "GIT_PROPOSAL_KIND_UNSUPPORTED",
                "only PUSH proposals can be imported",
            )
        normalized_action = _required_text(action, "action").upper()
        actions = {
            str(item.get("action", "")).upper(): item
            for item in proposal["actions"]
            if isinstance(item, Mapping)
        }
        if normalized_action not in actions:
            raise GitProposalError(
                "GIT_PROPOSAL_ACTION_INVALID",
                f"action is not present in proposal: {normalized_action}",
            )
        self._verify_execution_preconditions(proposal, normalized_action)
        return {
            "schema": JOURNAL_SCHEMA,
            "status": "GIT_PROPOSAL_ACTION_APPROVED",
            "proposal": proposal,
            "approval": approval,
            "action": {
                "action": normalized_action,
                "command_argv": list(actions[normalized_action]["command_argv"]),
            },
            "repository_write": False,
        }

    def record_result(
        self,
        *,
        proposal_id: str,
        action: str,
        result: Mapping[str, Any],
        observed_at: str,
    ) -> dict[str, Any]:
        row = self._proposal_row(proposal_id)
        if row["state"] not in {"APPROVED", "EXECUTING"}:
            raise GitProposalError(
                "GIT_PROPOSAL_STATE_INVALID",
                f"proposal cannot accept a result from state: {row['state']}",
            )
        proposal = json.loads(str(row["proposal_json"]))
        if proposal.get("proposal_kind") != "PUSH":
            raise GitProposalError(
                "GIT_PROPOSAL_KIND_UNSUPPORTED",
                "only PUSH proposals can record results",
            )
        normalized_action = _required_text(action, "action").upper()
        actions = {
            str(item.get("action", "")).upper()
            for item in proposal["actions"]
            if isinstance(item, Mapping)
        }
        if normalized_action not in actions:
            raise GitProposalError(
                "GIT_PROPOSAL_ACTION_INVALID",
                f"action is not present in proposal: {normalized_action}",
            )
        if self._action_completed(proposal_id, normalized_action):
            raise GitProposalError(
                "GIT_PROPOSAL_ACTION_ALREADY_COMPLETED",
                f"action already completed: {normalized_action}",
            )
        successful = (
            result.get("status") == "GIT_COMMAND_APPLIED"
            and result.get("returncode") == 0
        )
        final_action = normalized_action == "PUSH"
        next_state = "COMPLETED" if successful and final_action else (
            "EXECUTING" if successful else "FAILED"
        )
        completed_at = _timestamp(observed_at)
        result_record = {
            "schema": RESULT_SCHEMA,
            "proposal_id": proposal_id,
            "proposal_kind": proposal["proposal_kind"],
            "action": normalized_action,
            "status": "PASS" if successful else "FAIL",
            "commit_sha": self._head() if successful else "UNKNOWN",
            "observed_at": completed_at,
            "gateway_status": str(result.get("status", "UNKNOWN")),
            "returncode": result.get("returncode"),
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                UPDATE proposal
                SET state = ?, completed_at = ?, result_json = ?
                WHERE proposal_id = ?
                """,
                (
                    next_state,
                    completed_at if final_action else None,
                    _canonical_json(result_record),
                    proposal_id,
                ),
            )
            self._append_event(
                proposal_id,
                f"{normalized_action}_{'PASS' if successful else 'FAIL'}",
                completed_at,
                result_record,
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return {
            "schema": RESULT_SCHEMA,
            "status": "GIT_PROPOSAL_RESULT_RECORDED",
            "proposal_state": next_state,
            "result": result_record,
            "repository_write": False,
        }

    def status(self, proposal_id: str) -> dict[str, Any]:
        row = self._proposal_row(proposal_id)
        events = [
            {
                "event_type": item["event_type"],
                "observed_at": item["observed_at"],
                "details": json.loads(str(item["details_json"])),
            }
            for item in self.conn.execute(
                """
                SELECT event_type, observed_at, details_json
                FROM proposal_event
                WHERE proposal_id = ?
                ORDER BY event_id
                """,
                (proposal_id,),
            )
        ]
        return {
            "schema": JOURNAL_SCHEMA,
            "status": "GIT_PROPOSAL_STATUS",
            "proposal_state": row["state"],
            "proposal": json.loads(str(row["proposal_json"])),
            "approval": (
                None
                if row["approval_json"] is None
                else json.loads(str(row["approval_json"]))
            ),
            "result": (
                None
                if row["result_json"] is None
                else json.loads(str(row["result_json"]))
            ),
            "events": events,
            "database_path": str(self.database_path),
            "repository_write": False,
        }

    def _insert_proposal(self, material: Mapping[str, Any]) -> dict[str, Any]:
        proposal_digest = _digest(material)
        proposal_id = "git_proposal_" + proposal_digest[:24]
        proposal = {
            "schema": PROPOSAL_SCHEMA,
            "status": "GIT_PROPOSAL_CREATED",
            "proposal_id": proposal_id,
            "proposal_digest": proposal_digest,
            **material,
            "approval_required": True,
            "authority_created": False,
            "repository_write": False,
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                INSERT INTO proposal(
                    proposal_id, proposal_kind, proposal_digest, proposal_json,
                    state, created_at
                )
                VALUES (?, ?, ?, ?, 'PROPOSED', ?)
                """,
                (
                    proposal_id,
                    proposal["proposal_kind"],
                    proposal_digest,
                    _canonical_json(proposal),
                    proposal["created_at"],
                ),
            )
            self._append_event(
                proposal_id,
                "PROPOSED",
                proposal["created_at"],
                {"proposal_digest": proposal_digest},
            )
            self.conn.execute(
                """
                INSERT INTO proposal_scope(proposal_id, session_id, mode)
                VALUES (?, ?, ?)
                """,
                (
                    proposal_id,
                    _required_text(material.get("session_id"), "session_id"),
                    _scope_mode(material.get("mode")),
                ),
            )
            self.conn.execute("COMMIT")
        except sqlite3.IntegrityError as error:
            self.conn.execute("ROLLBACK")
            raise GitProposalError(
                "GIT_PROPOSAL_ALREADY_EXISTS",
                f"proposal already exists: {proposal_id}",
            ) from error
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return proposal

    def _verify_execution_preconditions(
        self,
        proposal: Mapping[str, Any],
        action: str,
    ) -> None:
        if proposal["branch"] != self._branch():
            raise GitProposalError(
                "GIT_PROPOSAL_STALE",
                "current branch no longer matches the approved proposal",
            )
        if proposal.get("proposal_kind") != "PUSH" or action != "PUSH":
            raise GitProposalError(
                "GIT_PROPOSAL_KIND_UNSUPPORTED",
                "only an approved PUSH action can execute",
            )
        if proposal["commit_sha"] != self._head():
            raise GitProposalError(
                "GIT_PROPOSAL_STALE",
                "current HEAD no longer matches the approved push commit",
            )
        if self._remote_head(
            proposal["remote"], proposal["target_branch"]
        ) != proposal["expected_remote_head"]:
            raise GitProposalError(
                "GIT_PUSH_REMOTE_CHANGED",
                "remote head changed after proposal creation",
            )

    def _database_path(self, value: Path | str | None) -> Path:
        candidate = (
            self.repository_root / DEFAULT_DATABASE_RELATIVE_PATH
            if value is None
            else Path(value).expanduser()
        )
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        resolved_parent = candidate.parent.resolve(strict=False)
        try:
            resolved_parent.relative_to(self.repository_root)
        except ValueError as error:
            raise GitProposalError(
                "GIT_PROPOSAL_DATABASE_OUTSIDE_REPOSITORY",
                "proposal database must remain inside the repository",
            ) from error
        current = self.repository_root
        relative_parent = resolved_parent.relative_to(self.repository_root)
        for part in relative_parent.parts:
            current /= part
            if not current.exists():
                continue
            if current.is_symlink() or (
                getattr(current.lstat(), "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise GitProposalError(
                    "GIT_PROPOSAL_DATABASE_REPARSE_POINT",
                    "proposal database path traverses a link or reparse point",
                )
        resolved_parent.mkdir(parents=True, exist_ok=True)
        if candidate.exists():
            stat_result = candidate.lstat()
            if candidate.is_symlink() or (
                getattr(stat_result, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise GitProposalError(
                    "GIT_PROPOSAL_DATABASE_REPARSE_POINT",
                    "proposal database file cannot be a link or reparse point",
                )
        return resolved_parent / candidate.name

    def _proposal_row(self, proposal_id: str) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT * FROM proposal WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise GitProposalError(
                "GIT_PROPOSAL_UNKNOWN",
                f"proposal does not exist: {proposal_id}",
            )
        return row

    def _latest_scoped_row(
        self,
        *,
        session_id: str,
        mode: str,
    ) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT proposal.*
            FROM proposal_scope
            JOIN proposal ON proposal.proposal_id = proposal_scope.proposal_id
            WHERE proposal_scope.session_id = ? AND proposal_scope.mode = ?
            ORDER BY proposal_scope.proposal_sequence DESC
            LIMIT 1
            """,
            (_required_text(session_id, "session_id"), _scope_mode(mode)),
        ).fetchone()

    def _proposal_scope(self, proposal_id: str) -> tuple[str, str] | None:
        row = self.conn.execute(
            """
            SELECT session_id, mode
            FROM proposal_scope
            WHERE proposal_id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return (str(row["session_id"]), str(row["mode"]))

    def _matching_active_scoped_proposal(
        self,
        *,
        session_id: str,
        mode: str,
        commit_sha: str,
        branch: str,
        remote: str,
        target_branch: str,
        expected_remote_head: str,
    ) -> sqlite3.Row | None:
        row = self._latest_scoped_row(session_id=session_id, mode=mode)
        if row is None or row["state"] not in {"PROPOSED", "APPROVED", "EXECUTING"}:
            return None
        proposal = json.loads(str(row["proposal_json"]))
        matching_fields = {
            "commit_sha": commit_sha,
            "branch": branch,
            "remote": remote,
            "target_branch": target_branch,
            "expected_remote_head": expected_remote_head,
        }
        if all(proposal.get(key) == value for key, value in matching_fields.items()):
            return row
        return None

    def _append_event(
        self,
        proposal_id: str,
        event_type: str,
        observed_at: str,
        details: Mapping[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO proposal_event(
                proposal_id, event_type, observed_at, details_json
            )
            VALUES (?, ?, ?, ?)
            """,
            (proposal_id, event_type, observed_at, _canonical_json(details)),
        )

    def _head(self) -> str:
        return self._git(["rev-parse", "HEAD"])

    def _branch(self) -> str:
        branch = self._git(["symbolic-ref", "--quiet", "--short", "HEAD"])
        if not branch:
            raise GitProposalError(
                "GIT_BRANCH_REQUIRED",
                "detached HEAD cannot create a Git proposal",
            )
        return branch

    def _commit_exists(self, commit_sha: str) -> bool:
        completed = self._run_git(["cat-file", "-e", f"{commit_sha}^{{commit}}"])
        return completed.returncode == 0

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        completed = self._run_git(["merge-base", "--is-ancestor", ancestor, descendant])
        return completed.returncode == 0

    def _action_completed(self, proposal_id: str, action: str) -> bool:
        event_type = f"{action.upper()}_PASS"
        row = self.conn.execute(
            """
            SELECT 1
            FROM proposal_event
            WHERE proposal_id = ? AND event_type = ?
            LIMIT 1
            """,
            (proposal_id, event_type),
        ).fetchone()
        return row is not None

    def _remote_head(self, remote: str, branch: str) -> str:
        completed = self._run_git(
            ["ls-remote", "--heads", remote, f"refs/heads/{branch}"],
            timeout=REMOTE_GIT_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            raise GitProposalError(
                "GIT_REMOTE_OBSERVATION_FAILED",
                completed.stderr.strip() or "unable to observe remote head",
            )
        line = completed.stdout.strip()
        if not line:
            return "ABSENT"
        commit_sha = line.split()[0].lower()
        if not COMMIT_PATTERN.fullmatch(commit_sha):
            raise GitProposalError(
                "GIT_REMOTE_OBSERVATION_INVALID",
                "remote head did not return an immutable commit",
            )
        return commit_sha

    def _git(self, args: Sequence[str]) -> str:
        completed = self._run_git(args)
        if completed.returncode != 0:
            raise GitProposalError(
                "GIT_OBSERVATION_FAILED",
                completed.stderr.strip() or f"git {' '.join(args)} failed",
            )
        return completed.stdout.strip()

    def _run_git(
        self,
        args: Sequence[str],
        *,
        timeout: int = LOCAL_GIT_TIMEOUT_SECONDS,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.repository_root,
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitProposalError(
                "GIT_OBSERVATION_FAILED",
                str(error),
            ) from error


def _proposal_material(proposal: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "schema",
        "status",
        "proposal_id",
        "proposal_digest",
        "approval_required",
        "authority_created",
        "repository_write",
    }
    return {key: value for key, value in proposal.items() if key not in excluded}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GitProposalError(
            "GIT_PROPOSAL_REQUEST_INVALID",
            f"{field} must be a non-empty string",
        )
    return value.strip()


def _evidence_ref(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if text.upper() == "UNKNOWN":
        raise GitProposalError(
            "GIT_PROPOSAL_EVIDENCE_REQUIRED",
            f"{field} must identify concrete evidence",
        )
    return text


def _scope_mode(value: Any) -> str:
    return _required_text(value, "mode").upper()


def _timestamp(value: Any) -> str:
    text = _required_text(value, "observed_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise GitProposalError(
            "GIT_PROPOSAL_TIMESTAMP_INVALID",
            "observed_at must be ISO-8601",
        ) from error
    if parsed.tzinfo is None:
        raise GitProposalError(
            "GIT_PROPOSAL_TIMESTAMP_INVALID",
            "observed_at must include a timezone",
        )
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
