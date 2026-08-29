from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol

from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli


AUTO_CONTINUITY_SCHEMA = "universe.auto-continuity.v1"
CONTINUITY_TARGET = "sqlite://.ai/runtime/continuity/continuity.sqlite"
AUTO_SAVE_TRIGGERS = frozenset(
    {
        "TASK_COMPLETED",
        "NORMAL_STOP",
        "PROVIDER_SWITCH",
        "SESSION_SELECTION_CHANGED",
        "MODE_SWITCH",
        "PROVIDER_QUOTA",
        "IDLE",
    }
)


class ContinuityCoordinatorError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ContinuityBackend(Protocol):
    def save(
        self,
        *,
        project_root: Path,
        checkpoint_request: Mapping[str, Any],
        resume_request: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContinuityCoordinatorError(
            "AUTO_CONTINUITY_REQUEST_INVALID", f"{field} must be non-empty text"
        )
    return value.strip()


class RuntimeContinuityBackend:
    """Invoke one project's installed continuity runtime with exact argv boundaries."""

    def __init__(
        self,
        python_executable: Path,
        *,
        native_runner: Callable[[NativeCliRequest], NativeCliResult] = run_native_cli,
    ) -> None:
        self.python_executable = python_executable.expanduser().resolve()
        self.native_runner = native_runner

    def save(
        self,
        *,
        project_root: Path,
        checkpoint_request: Mapping[str, Any],
        resume_request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        root = project_root.expanduser().resolve(strict=True)
        cli = root / ".ai" / "runtime" / "reference_runtime" / "cli.py"
        if not cli.is_file() or cli.is_symlink():
            raise ContinuityCoordinatorError(
                "PROJECT_CONTINUITY_RUNTIME_UNAVAILABLE",
                "installed continuity CLI is unavailable",
            )
        with tempfile.TemporaryDirectory(prefix="universe-continuity-") as directory:
            temporary = Path(directory)
            prepared_checkpoint = self._invoke(
                root,
                cli,
                temporary / "checkpoint-prepare.json",
                ("checkpoint", "prepare"),
                checkpoint_request,
            )
            checkpoint_result = self._result(prepared_checkpoint)
            saved_checkpoint = self._invoke(
                root,
                cli,
                temporary / "checkpoint-save.json",
                ("checkpoint", "save"),
                {
                    "candidate_id": checkpoint_result["candidate_id"],
                    "candidate": checkpoint_result["candidate"],
                },
            )
            checkpoint_id = self._result(saved_checkpoint)["record_id"]
            resume_material = dict(resume_request)
            resume_material["checkpoint_ref"] = (
                f"sqlite://.ai/runtime/continuity/continuity.sqlite#{checkpoint_id}"
            )
            prepared_resume = self._invoke(
                root,
                cli,
                temporary / "resume-prepare.json",
                ("resume-save", "prepare"),
                resume_material,
            )
            resume_result = self._result(prepared_resume)
            saved_resume = self._invoke(
                root,
                cli,
                temporary / "resume-save.json",
                ("resume-save", "save"),
                {
                    "candidate_id": resume_result["candidate_id"],
                    "candidate": resume_result["candidate"],
                },
            )
        return {
            "checkpoint": self._result(saved_checkpoint),
            "resume": self._result(saved_resume),
        }

    def _invoke(
        self,
        root: Path,
        cli: Path,
        request_path: Path,
        operation: tuple[str, str],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_path.write_text(
            json.dumps(dict(request), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        result = self.native_runner(
            NativeCliRequest(
                executable=self.python_executable,
                arguments=(
                    str(cli),
                    operation[0],
                    operation[1],
                    "--repo-root",
                    str(root),
                    "--request",
                    str(request_path),
                ),
                cwd=root,
                timeout_seconds=60,
                max_output_chars=200_000,
            )
        )
        if result.status != "COMPLETED" or result.return_code != 0:
            raise ContinuityCoordinatorError(
                "PROJECT_CONTINUITY_COMMAND_FAILED",
                result.stderr.strip() or result.status,
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ContinuityCoordinatorError(
                "PROJECT_CONTINUITY_RESULT_INVALID",
                "continuity CLI result is not JSON",
            ) from error
        if not isinstance(payload, dict):
            raise ContinuityCoordinatorError(
                "PROJECT_CONTINUITY_RESULT_INVALID",
                "continuity CLI result must be an object",
            )
        return payload

    @staticmethod
    def _result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise ContinuityCoordinatorError(
                "PROJECT_CONTINUITY_RESULT_INVALID",
                "continuity CLI result payload is absent",
            )
        return result


class ProjectContinuityCoordinator:
    def __init__(
        self,
        database_path: Path,
        backend: ContinuityBackend,
        *,
        clock: Callable[[], str] = utc_now,
        coordinate_resolver: Callable[[Path], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.clock = clock
        self.coordinate_resolver = coordinate_resolver
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS continuity_save_state (
                    project_root TEXT PRIMARY KEY,
                    last_payload_digest TEXT,
                    last_checkpoint_id TEXT,
                    last_resume_id TEXT,
                    last_trigger TEXT,
                    last_status TEXT NOT NULL,
                    last_saved_at TEXT,
                    dirty_end INTEGER NOT NULL DEFAULT 0,
                    dirty_end_reason TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS continuity_save_event (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_root TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_digest TEXT,
                    checkpoint_id TEXT,
                    resume_id TEXT,
                    reason TEXT,
                    occurred_at TEXT NOT NULL
                );
                """
            )

    def save(
        self,
        *,
        project_root: Path,
        trigger: str,
        compressed_context: str,
        summary: str = "",
        source_refs: list[str] | None = None,
        runtime_coordinate: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        root = project_root.expanduser().resolve(strict=True)
        normalized_trigger = _required_text(trigger, "trigger").upper()
        if normalized_trigger not in AUTO_SAVE_TRIGGERS:
            raise ContinuityCoordinatorError(
                "AUTO_CONTINUITY_TRIGGER_INVALID",
                f"unsupported automatic save trigger: {normalized_trigger}",
            )
        context = _required_text(compressed_context, "compressed_context")
        coordinate = (
            self._normalize_runtime_coordinate(runtime_coordinate)
            if runtime_coordinate is not None
            else self._normalize_runtime_coordinate(self.coordinate_resolver(root))
            if self.coordinate_resolver is not None
            else None
        )
        if coordinate is None:
            return self._record_skip(
                root,
                normalized_trigger,
                "SESSION_COORDINATES_UNAVAILABLE",
            )
        observed_at = self.clock()
        refs = list(source_refs or [coordinate["source_ref"]])
        if not refs or any(not isinstance(item, str) or not item.strip() for item in refs):
            raise ContinuityCoordinatorError(
                "AUTO_CONTINUITY_REQUEST_INVALID",
                "source_refs must contain non-empty text",
            )
        snapshot = {
            "node": coordinate["node"],
            "mode": coordinate["mode"],
            "compressed_context": context,
            "trigger": normalized_trigger,
            "currentness": coordinate["currentness"],
        }
        checkpoint_request = {
            "session_id": coordinate["session_id"],
            "frame_id": coordinate["frame_id"],
            "anchor_id": coordinate["anchor_id"],
            "snapshot": snapshot,
            "summary": summary.strip(),
            "source_refs": refs,
            "observed_at": observed_at,
            "target_ref": CONTINUITY_TARGET,
        }
        resume_request = {
            "node": coordinate["node"],
            "mode": coordinate["mode"],
            "session_id": coordinate["session_id"],
            "frame_id": coordinate["frame_id"],
            "anchor_id": coordinate["anchor_id"],
            "checkpoint_ref": "PENDING_CHECKPOINT",
            "snapshot": snapshot,
            "summary": summary.strip(),
            "source_refs": refs,
            "source_ref": refs[0],
            "observed_at": observed_at,
            "target_ref": CONTINUITY_TARGET,
        }
        # Trigger describes why the observation was flushed, not new project
        # content. Excluding it prevents TASK_COMPLETED -> IDLE from creating
        # duplicate durable records for the same bounded state.
        digest_snapshot = dict(snapshot)
        digest_snapshot.pop("trigger", None)
        digest = hashlib.sha256(
            json.dumps(
                {
                    "coordinate": coordinate,
                    "snapshot": digest_snapshot,
                    "summary": summary.strip(),
                    "source_refs": refs,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            state = self.status(root)
            if (
                state is not None
                and state["last_payload_digest"] == digest
                and state["last_status"] == "SAVED"
                and state["last_checkpoint_id"]
                and state["last_resume_id"]
            ):
                return {
                    "schema": AUTO_CONTINUITY_SCHEMA,
                    "status": "AUTO_CONTINUITY_ALREADY_SAVED",
                    "trigger": normalized_trigger,
                    "project_root": str(root),
                    "payload_digest": digest,
                    "checkpoint_id": state["last_checkpoint_id"],
                    "resume_id": state["last_resume_id"],
                    "git_publication": "NOT_PERFORMED",
                }
            try:
                saved = self.backend.save(
                    project_root=root,
                    checkpoint_request=checkpoint_request,
                    resume_request=resume_request,
                )
                checkpoint_id = _required_text(
                    saved.get("checkpoint", {}).get("record_id"), "checkpoint.record_id"
                )
                resume_id = _required_text(
                    saved.get("resume", {}).get("record_id"), "resume.record_id"
                )
            except ContinuityCoordinatorError as error:
                self._record_failure(root, normalized_trigger, digest, error.code)
                raise
            now = self.clock()
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO continuity_save_state(
                        project_root, last_payload_digest, last_checkpoint_id,
                        last_resume_id, last_trigger, last_status, last_saved_at,
                        dirty_end, dirty_end_reason, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'SAVED', ?, 0, NULL, ?)
                    ON CONFLICT(project_root) DO UPDATE SET
                        last_payload_digest = excluded.last_payload_digest,
                        last_checkpoint_id = excluded.last_checkpoint_id,
                        last_resume_id = excluded.last_resume_id,
                        last_trigger = excluded.last_trigger,
                        last_status = excluded.last_status,
                        last_saved_at = excluded.last_saved_at,
                        dirty_end = 0,
                        dirty_end_reason = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(root),
                        digest,
                        checkpoint_id,
                        resume_id,
                        normalized_trigger,
                        now,
                        now,
                    ),
                )
                self._event(
                    connection,
                    root=root,
                    trigger=normalized_trigger,
                    status="SAVED",
                    digest=digest,
                    checkpoint_id=checkpoint_id,
                    resume_id=resume_id,
                )
            return {
                "schema": AUTO_CONTINUITY_SCHEMA,
                "status": "AUTO_CONTINUITY_SAVED",
                "trigger": normalized_trigger,
                "project_root": str(root),
                "payload_digest": digest,
                "checkpoint_id": checkpoint_id,
                "resume_id": resume_id,
                "git_publication": "NOT_PERFORMED",
            }

    def mark_dirty_end(self, project_root: Path, reason: str) -> dict[str, Any]:
        root = project_root.expanduser().resolve(strict=True)
        normalized_reason = _required_text(reason, "reason")
        now = self.clock()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO continuity_save_state(
                    project_root, last_status, dirty_end, dirty_end_reason, updated_at
                ) VALUES (?, 'DIRTY_END', 1, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    last_status = 'DIRTY_END',
                    dirty_end = 1,
                    dirty_end_reason = excluded.dirty_end_reason,
                    updated_at = excluded.updated_at
                """,
                (str(root), normalized_reason, now),
            )
            self._event(
                connection,
                root=root,
                trigger="CRASH",
                status="DIRTY_END",
                reason=normalized_reason,
            )
        state = self.status(root)
        if state is None:
            raise ContinuityCoordinatorError(
                "AUTO_CONTINUITY_INVARIANT_FAILED",
                "dirty-end state was not observable after commit",
            )
        return state

    def status(self, project_root: Path) -> dict[str, Any] | None:
        root = project_root.expanduser().resolve()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM continuity_save_state WHERE project_root = ?",
                (str(root),),
            ).fetchone()
        return None if row is None else dict(row)

    def prepare_archive_publication(
        self,
        *,
        project_root: Path,
        explicit_command: bool,
        approved: bool,
    ) -> dict[str, Any]:
        root = project_root.expanduser().resolve(strict=True)
        if not explicit_command or not approved:
            raise ContinuityCoordinatorError(
                "ARCHIVE_PUBLICATION_APPROVAL_REQUIRED",
                "Git archive publication requires an explicit command and separate approval",
            )
        state = self.status(root)
        if state is None or not state.get("last_resume_id"):
            raise ContinuityCoordinatorError(
                "ARCHIVE_PUBLICATION_SOURCE_UNAVAILABLE",
                "no durable local Resume record is available",
            )
        return {
            "schema": AUTO_CONTINUITY_SCHEMA,
            "status": "ARCHIVE_PUBLICATION_PREPARED",
            "project_root": str(root),
            "resume_id": state["last_resume_id"],
            "git_publication": "NOT_PERFORMED",
            "separate_git_approval_required": True,
        }

    @staticmethod
    def _normalize_runtime_coordinate(
        value: Mapping[str, Any] | None,
    ) -> dict[str, str] | None:
        if value is None:
            return None
        required = (
            "node",
            "mode",
            "session_id",
            "frame_id",
            "anchor_id",
            "currentness",
            "source_ref",
        )
        normalized: dict[str, str] = {}
        for field in required:
            item = value.get(field)
            if not isinstance(item, str) or not item.strip():
                return None
            normalized[field] = item.strip()
        normalized["mode"] = normalized["mode"].upper()
        if any(
            item.upper() == "UNKNOWN"
            for field, item in normalized.items()
            if field != "currentness"
        ):
            return None
        return normalized

    def _record_skip(self, root: Path, trigger: str, reason: str) -> dict[str, Any]:
        with self._connection() as connection:
            self._event(
                connection,
                root=root,
                trigger=trigger,
                status="SKIPPED",
                reason=reason,
            )
        return {
            "schema": AUTO_CONTINUITY_SCHEMA,
            "status": "AUTO_CONTINUITY_SKIPPED",
            "trigger": trigger,
            "project_root": str(root),
            "reason": reason,
            "git_publication": "NOT_PERFORMED",
        }

    def _record_failure(
        self, root: Path, trigger: str, digest: str, reason: str
    ) -> None:
        now = self.clock()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO continuity_save_state(
                    project_root, last_payload_digest, last_trigger,
                    last_status, dirty_end, dirty_end_reason, updated_at
                ) VALUES (?, ?, ?, 'FAILED', 1, ?, ?)
                ON CONFLICT(project_root) DO UPDATE SET
                    last_payload_digest = excluded.last_payload_digest,
                    last_trigger = excluded.last_trigger,
                    last_status = 'FAILED',
                    dirty_end = 1,
                    dirty_end_reason = excluded.dirty_end_reason,
                    updated_at = excluded.updated_at
                """,
                (str(root), digest, trigger, reason, now),
            )
            self._event(
                connection,
                root=root,
                trigger=trigger,
                status="FAILED",
                digest=digest,
                reason=reason,
            )

    def _event(
        self,
        connection: sqlite3.Connection,
        *,
        root: Path,
        trigger: str,
        status: str,
        digest: str | None = None,
        checkpoint_id: str | None = None,
        resume_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO continuity_save_event(
                project_root, trigger, status, payload_digest,
                checkpoint_id, resume_id, reason, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(root),
                trigger,
                status,
                digest,
                checkpoint_id,
                resume_id,
                reason,
                self.clock(),
            ),
        )
