"""Privacy-bounded provider session activity observer.

Provider transcript files remain the canonical source.  This module persists
only file/cursor identity and reduced operational activity; it never copies a
prompt, response, tool command, or provider message body into Universe.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import unquote


OBSERVER_SCHEMA = "universe.provider-session-observer.v1"
SOURCE_SCHEMA = "universe.provider-session-source.v1"
ACTIVITY_SCHEMA = "universe.provider-session-activity.v1"
PROVIDERS = frozenset({"CODEX", "CLAUDE", "GROK"})
SOURCE_KINDS = {
    "CODEX": "CODEX_ROLLOUT_JSONL",
    "CLAUDE": "CLAUDE_SESSION_JSONL",
    "GROK": "GROK_UPDATES_JSONL",
}
METADATA_READ_LIMIT = 256 * 1024
METADATA_LINE_LIMIT = 192
DEFAULT_SCAN_BYTE_LIMIT = 256 * 1024
MAX_SINGLE_EVENT_BYTE_LIMIT = 4 * 1024 * 1024
DEFAULT_SCAN_EVENT_LIMIT = 512
DEFAULT_SCAN_TIME_LIMIT_SECONDS = 0.25
SEMANTIC_EXCERPT_LIMIT = 256
SEMANTIC_EXCERPT_CHAR_LIMIT = 2000
SEMANTIC_TOTAL_CHAR_LIMIT = 32000
SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|xai|ghp|github_pat)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*\S{8,}",
        re.IGNORECASE,
    ),
)


class ProviderSessionObserverError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderSessionObserverError("SOURCE_REQUEST_INVALID", f"{field} is required")
    return value.strip()


def _identifier(value: Any, field: str) -> str:
    text = _text(value, field)
    if len(text) > 256 or any(char.isspace() for char in text):
        raise ProviderSessionObserverError(
            "SOURCE_REQUEST_INVALID", f"{field} must be a compact identifier"
        )
    return text


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _redact_semantic_text(value: str) -> str:
    redacted = value.replace("\x00", " ").strip()
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return " ".join(redacted.split())[:SEMANTIC_EXCERPT_CHAR_LIMIT]


def _codex_semantic_messages(event: Mapping[str, Any]) -> list[tuple[str, str]]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return []
    payload_type = str(payload.get("type") or "").strip().lower()
    role = str(payload.get("role") or "").strip().upper()
    messages: list[tuple[str, str]] = []
    if payload_type == "message" and role in {"USER", "ASSISTANT"}:
        content = payload.get("content")
        if isinstance(content, list):
            chunks = []
            for item in content:
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("type") or "").lower() not in {
                    "input_text",
                    "output_text",
                    "text",
                }:
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text)
            if chunks:
                messages.append((role, "\n".join(chunks)))
    elif payload_type in {"user_message", "agent_message", "assistant_message"}:
        inferred_role = "USER" if payload_type == "user_message" else "ASSISTANT"
        text = payload.get("message") or payload.get("text")
        if isinstance(text, str) and text.strip():
            messages.append((inferred_role, text))
    return messages


def _file_identity(path: Path) -> str:
    stat = path.stat()
    # Do not include ctime: Unix updates it when a transcript is appended.
    # st_ino is available on NTFS but may be zero on some filesystem drivers.
    return f"{stat.st_dev}:{stat.st_ino}"


def _safe_event_kind(event_type: str) -> tuple[str, str]:
    normalized = event_type.strip().upper().replace("-", "_").replace(" ", "_")
    if "QUOTA" in normalized or "RATE_LIMIT" in normalized:
        return "QUOTA_STOP", "WAITING"
    if "PERMISSION" in normalized or "APPROVAL" in normalized:
        return "APPROVAL_WAIT", "WAITING"
    if "ERROR" in normalized or "FAIL" in normalized:
        return "ERROR", "FAILED"
    if "TOOL" in normalized or "COMMAND" in normalized:
        return "TOOL_PHASE", "ACTIVE"
    if "COMPLETE" in normalized or "RESULT" in normalized or "FINISH" in normalized:
        return "TURN_COMPLETED", "COMPLETED"
    if "START" in normalized or "PROMPT" in normalized or "MESSAGE" in normalized:
        return "TURN_STARTED", "ACTIVE"
    return "ACTIVITY", "OBSERVED"


def _event_type(event: Mapping[str, Any]) -> str:
    for key in ("type", "event_type", "event", "kind"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ProviderSessionObserverError(
        "SOURCE_SCHEMA_UNSUPPORTED", "event has no supported type field"
    )


def _event_time(event: Mapping[str, Any]) -> str:
    for key in ("timestamp", "created_at", "updated_at", "time"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    return _now()


def _event_id(event: Mapping[str, Any], fallback: str) -> str:
    for key in ("uuid", "id", "event_id", "message_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
    return fallback


def _safe_metadata_text(value: Any, *, limit: int = 512) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:limit] if text else None


def _workspace_fallback(provider: str, path: Path) -> str | None:
    if provider == "GROK" and len(path.parents) >= 2:
        return unquote(path.parents[1].name)
    if provider == "CLAUDE":
        encoded = unquote(path.parent.name)
        if len(encoded) >= 3 and encoded[1:3] == "--":
            # Claude's project directory is only a fallback. A cwd field read
            # from bounded metadata wins because '-' is ambiguous here.
            decoded_path = encoded[3:].replace("-", "\\")
            return f"{encoded[0]}:\\{decoded_path}"
    return None


def _bounded_session_metadata(provider: str, path: Path) -> dict[str, Any]:
    """Read only provider identity metadata from a bounded JSONL prefix.

    Prompt, response, tool input, command, and message bodies are deliberately
    ignored. Discovery remains advisory, but identity never falls back to a
    path or filename. Ambiguous sources stay unbound.
    """
    metadata: dict[str, Any] = {
        "provider_session_id": None,
        "workspace": _workspace_fallback(provider, path),
        "display_name": None,
        "session_kind": "CHAT",
        "parent_provider_session_id": None,
        "identity_state": "UNKNOWN",
    }
    consumed = 0
    try:
        with path.open("rb") as handle:
            for index, raw_line in enumerate(handle):
                consumed += len(raw_line)
                if index >= METADATA_LINE_LIMIT or consumed > METADATA_READ_LIMIT:
                    break
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(event, Mapping):
                    continue
                if provider == "CODEX" and event.get("type") == "session_meta":
                    payload = event.get("payload")
                    if not isinstance(payload, Mapping):
                        continue
                    metadata["provider_session_id"] = (
                        _safe_metadata_text(payload.get("id"), limit=256)
                        or _safe_metadata_text(payload.get("session_id"), limit=256)
                    )
                    if metadata["provider_session_id"]:
                        metadata["identity_state"] = "VERIFIED"
                    metadata["workspace"] = (
                        _safe_metadata_text(payload.get("cwd")) or metadata["workspace"]
                    )
                    metadata["display_name"] = _safe_metadata_text(
                        payload.get("title") or payload.get("slug"), limit=160
                    )
                    source = payload.get("source")
                    if isinstance(source, Mapping) and isinstance(
                        source.get("subagent"), Mapping
                    ):
                        metadata["session_kind"] = "WORKER"
                        subagent = source["subagent"]
                        spawn = subagent.get("thread_spawn")
                        if isinstance(spawn, Mapping):
                            metadata["parent_provider_session_id"] = _safe_metadata_text(
                                spawn.get("parent_thread_id"), limit=256
                            )
                    break
                if provider == "CLAUDE":
                    metadata["provider_session_id"] = (
                        _safe_metadata_text(event.get("sessionId"), limit=256)
                    )
                    if metadata["provider_session_id"]:
                        metadata["identity_state"] = "VERIFIED"
                    metadata["workspace"] = (
                        _safe_metadata_text(event.get("cwd")) or metadata["workspace"]
                    )
                    metadata["display_name"] = (
                        _safe_metadata_text(event.get("slug"), limit=160)
                        or metadata["display_name"]
                    )
                    if event.get("isSidechain") is True or _safe_metadata_text(
                        event.get("agentId"), limit=256
                    ):
                        metadata["session_kind"] = "WORKER"
                    if (
                        metadata["workspace"]
                        and metadata["display_name"]
                        and metadata["provider_session_id"]
                    ):
                        break
                if provider == "GROK":
                    params = event.get("params")
                    if isinstance(params, Mapping):
                        metadata["provider_session_id"] = (
                            _safe_metadata_text(params.get("sessionId"), limit=256)
                        )
                        if metadata["provider_session_id"]:
                            metadata["identity_state"] = "VERIFIED"
                            break
    except OSError:
        pass
    workspace = _safe_metadata_text(metadata.get("workspace"))
    metadata["workspace"] = workspace
    metadata["workspace_name"] = Path(workspace).name if workspace else "Unknown workspace"
    metadata["display_name"] = (
        _safe_metadata_text(metadata.get("display_name"), limit=160)
        or f"{provider.title()} session"
    )
    return metadata


class ProviderSessionObserverStore:
    """Durable cursor store with provider-specific fail-closed reducers."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS provider_session_source (
                    source_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    provider_session_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    file_identity TEXT,
                    cursor_offset INTEGER NOT NULL DEFAULT 0,
                    cursor_ordinal INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    reason TEXT,
                    last_seen_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(provider, provider_session_id, source_path)
                );

                CREATE TABLE IF NOT EXISTS provider_session_activity (
                    activity_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL
                        REFERENCES provider_session_source(source_id)
                        ON DELETE CASCADE,
                    provider_event_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    event_kind TEXT NOT NULL,
                    activity_state TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    activity_digest TEXT NOT NULL,
                    byte_offset INTEGER,
                    branch_parent_id TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(source_id, provider_event_id, activity_digest)
                );

                CREATE INDEX IF NOT EXISTS provider_session_activity_source_time
                ON provider_session_activity(source_id, active, ordinal, activity_id);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(provider_session_activity)"
                ).fetchall()
            }
            if "byte_offset" not in columns:
                connection.execute(
                    "ALTER TABLE provider_session_activity ADD COLUMN byte_offset INTEGER"
                )

    def register_source(self, value: Mapping[str, Any]) -> dict[str, Any]:
        provider = _text(value.get("provider"), "provider").upper()
        if provider not in PROVIDERS:
            raise ProviderSessionObserverError("SOURCE_PROVIDER_UNSUPPORTED", provider)
        source_kind = _text(value.get("source_kind"), "source_kind").upper()
        if source_kind != SOURCE_KINDS[provider]:
            raise ProviderSessionObserverError(
                "SOURCE_KIND_INVALID", f"{provider} requires {SOURCE_KINDS[provider]}"
            )
        source_path = Path(_text(value.get("source_path"), "source_path")).expanduser()
        if not source_path.is_absolute():
            raise ProviderSessionObserverError(
                "SOURCE_PATH_INVALID", "source_path must be absolute"
            )
        if provider == "GROK" and source_path.name != "updates.jsonl":
            raise ProviderSessionObserverError(
                "SOURCE_PATH_FORBIDDEN", "Grok observer accepts updates.jsonl only"
            )
        if (
            provider == "CODEX"
            and (
                not source_path.name.startswith("rollout-")
                or source_path.suffix.lower() != ".jsonl"
            )
        ):
            raise ProviderSessionObserverError(
                "SOURCE_PATH_INVALID", "Codex observer requires rollout-*.jsonl"
            )
        if provider == "CLAUDE" and source_path.suffix.lower() != ".jsonl":
            raise ProviderSessionObserverError(
                "SOURCE_PATH_INVALID", "Claude observer requires a .jsonl session source"
            )
        source_id = str(value.get("source_id") or "source_" + uuid.uuid4().hex)
        provider_session_id = _identifier(value.get("provider_session_id"), "provider_session_id")
        source_version = _text(value.get("source_version", "v1"), "source_version")
        start_at_end = value.get("start_at_end") is True
        initial_offset = 0
        initial_identity = None
        initial_status = "REGISTERED"
        if start_at_end and source_path.is_file():
            initial_offset = source_path.stat().st_size
            initial_identity = _file_identity(source_path)
            initial_status = "ACTIVE"
        now = _now()
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT source_id FROM provider_session_source
                WHERE provider = ? AND provider_session_id = ? AND source_path = ?
                """,
                (provider, provider_session_id, str(source_path)),
            ).fetchone()
            if existing is not None:
                return self._source_row(
                    connection.execute(
                        "SELECT * FROM provider_session_source WHERE source_id = ?",
                        (existing["source_id"],),
                    ).fetchone()
                )
            connection.execute(
                """
                INSERT INTO provider_session_source(
                    source_id, provider, provider_session_id, source_path, source_kind,
                    source_version, file_identity, cursor_offset, status,
                    last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    provider,
                    provider_session_id,
                    str(source_path),
                    source_kind,
                    source_version,
                    initial_identity,
                    initial_offset,
                    initial_status,
                    now if start_at_end else None,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
        return self._source_row(row)

    def discover_sources(
        self, provider: str, *, home: Path | None = None
    ) -> list[dict[str, Any]]:
        """Return redacted local chat metadata from known provider paths."""
        normalized_provider = _text(provider, "provider").upper()
        if normalized_provider not in PROVIDERS:
            raise ProviderSessionObserverError(
                "SOURCE_PROVIDER_UNSUPPORTED", normalized_provider
            )
        root = home or self._default_provider_home(normalized_provider)
        if not root.is_dir():
            return []
        if normalized_provider == "CODEX":
            paths = list((root / "archived_sessions").glob("rollout-*.jsonl"))
            paths.extend((root / "sessions").glob("**/rollout-*.jsonl"))
        elif normalized_provider == "CLAUDE":
            paths = list((root / "projects").glob("**/*.jsonl"))
        else:
            paths = list((root / "sessions").glob("**/updates.jsonl"))
        file_paths: list[tuple[Path, float]] = []
        for path in paths:
            try:
                if path.is_file():
                    file_paths.append((path, path.stat().st_mtime))
            except OSError:
                # Discovery is advisory. A rotating provider directory must not
                # turn a local UI refresh into a transcript read or a failure.
                continue
        candidates: list[dict[str, Any]] = []
        for path, modified_at in sorted(
            file_paths, key=lambda item: item[1], reverse=True
        )[:200]:
            metadata = _bounded_session_metadata(normalized_provider, path)
            candidates.append(
                {
                    "schema": SOURCE_SCHEMA,
                    "status": "DISCOVERED",
                    "provider": normalized_provider,
                    "provider_session_id": metadata["provider_session_id"],
                    "source_path": str(path.resolve()),
                    "source_kind": SOURCE_KINDS[normalized_provider],
                    "source_version": "v1",
                    "last_modified_at": datetime.fromtimestamp(modified_at, tz=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "workspace": metadata["workspace"],
                    "workspace_name": metadata["workspace_name"],
                    "display_name": metadata["display_name"],
                    "session_kind": metadata["session_kind"],
                    "parent_provider_session_id": metadata[
                        "parent_provider_session_id"
                    ],
                    "identity_state": metadata["identity_state"],
                    "transcript_content": "EXCLUDED",
                }
            )
        return candidates

    def list_sources(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_session_source ORDER BY updated_at DESC, source_id"
            ).fetchall()
        return [self._source_row(row) for row in rows]

    def scan_registered_sources(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            source_ids = [
                str(row["source_id"])
                for row in connection.execute(
                    "SELECT source_id FROM provider_session_source WHERE enabled = 1"
                ).fetchall()
            ]
        return [self.scan(source_id) for source_id in source_ids]

    def list_activities(self, source_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
            if exists is None:
                raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
            query = "SELECT * FROM provider_session_activity WHERE source_id = ?"
            if active_only:
                query += " AND active = 1"
            query += " ORDER BY ordinal DESC, activity_id DESC"
            rows = connection.execute(query, (source_id,)).fetchall()
        return [self._activity_row(row) for row in rows]

    def build_batch_candidate(self, source_id: str) -> dict[str, Any]:
        """Prepare, but never publish, one bounded activity-to-memory candidate."""
        with self._connection() as connection:
            source = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
        activities = [
            activity
            for activity in self.list_activities(source_id)
            if activity["event_kind"]
            in {"TURN_COMPLETED", "ERROR", "QUOTA_STOP", "APPROVAL_WAIT"}
        ]
        source_view = self._source_row(source)
        material = {
            "source_id": source_id,
            "cursor": source_view["cursor"],
            "activity_digests": [item["activity_digest"] for item in activities],
        }
        return {
            "schema": "universe.provider-activity-batch-candidate.v1",
            "candidate_id": "activitybatch_" + _sha256(_canonical_json(material))[:24],
            "status": "REVIEW_REQUIRED" if activities else "ACTIVITY_BATCH_EMPTY",
            "source": {
                "provider": source_view["provider"],
                "provider_session_id": source_view["provider_session_id"],
                "source_id": source_id,
                "cursor": source_view["cursor"],
            },
            "activity_refs": [
                {
                    "activity_id": item["activity_id"],
                    "activity_digest": item["activity_digest"],
                    "ordinal": item["ordinal"],
                    "event_kind": item["event_kind"],
                    "activity_state": item["activity_state"],
                }
                for item in activities
            ],
            "memory": {"state": "REVIEW_REQUIRED", "publication": "NOT_REQUESTED"},
            "bench": {"state": "NOT_RECORDED", "reason": "SKILL_RUN_EVIDENCE_REQUIRED"},
            "future": {"state": "NOT_PROJECTED", "reason": "CASE_OR_PATTERN_REQUIRED"},
            "raw_transcript": "EXCLUDED",
        }

    def build_transient_semantic_evidence(
        self,
        source_id: str,
        activity_refs: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Read exact selected Codex message events without persisting text."""

        if not activity_refs or len(activity_refs) > 512:
            raise ProviderSessionObserverError(
                "SEMANTIC_EVIDENCE_INVALID",
                "activity_refs must contain 1..512 items",
            )
        with self._connection() as connection:
            source = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
            if str(source["provider"]) != "CODEX":
                raise ProviderSessionObserverError(
                    "SEMANTIC_PROVIDER_UNSUPPORTED", "Codex source required"
                )
            path = Path(str(source["source_path"]))
            if not path.is_file() or _file_identity(path) != str(source["file_identity"]):
                raise ProviderSessionObserverError(
                    "SEMANTIC_SOURCE_NOT_CURRENT", source_id
                )
            selected: list[tuple[sqlite3.Row, Mapping[str, Any]]] = []
            for ref in activity_refs:
                activity_id = _identifier(ref.get("activity_id"), "activity_id")
                activity_digest = _identifier(
                    ref.get("activity_digest"), "activity_digest"
                )
                ordinal = ref.get("ordinal")
                if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
                    raise ProviderSessionObserverError(
                        "SEMANTIC_EVIDENCE_INVALID", "ordinal must be positive"
                    )
                row = connection.execute(
                    """
                    SELECT * FROM provider_session_activity
                    WHERE source_id = ? AND activity_id = ? AND activity_digest = ?
                      AND ordinal = ? AND active = 1
                    """,
                    (source_id, activity_id, activity_digest, ordinal),
                ).fetchone()
                if row is None or row["byte_offset"] is None:
                    raise ProviderSessionObserverError(
                        "SEMANTIC_ACTIVITY_NOT_ATTESTED", activity_id
                    )
                selected.append((row, ref))

        selected.sort(key=lambda item: int(item[0]["ordinal"]))
        excerpts: list[dict[str, Any]] = []
        total_chars = 0
        previous_semantic_key: tuple[str, str] | None = None
        previous_ordinal: int | None = None
        with path.open("rb") as handle:
            for row, _ref in selected:
                byte_offset = int(row["byte_offset"])
                handle.seek(byte_offset)
                raw_line = handle.readline()
                if not raw_line.endswith(b"\n"):
                    raise ProviderSessionObserverError(
                        "SEMANTIC_SOURCE_NOT_CURRENT", str(row["activity_id"])
                    )
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ProviderSessionObserverError(
                        "SEMANTIC_SOURCE_NOT_CURRENT", type(error).__name__
                    ) from error
                if not isinstance(event, Mapping):
                    raise ProviderSessionObserverError(
                        "SEMANTIC_SOURCE_NOT_CURRENT", "event is not an object"
                    )
                safe = {
                    "source_id": source_id,
                    "provider_event_id": _event_id(event, f"offset-{byte_offset}"),
                    "ordinal": int(row["ordinal"]),
                    "event_type": _event_type(event)[:96],
                    "parent_id": None,
                }
                if _sha256(_canonical_json(safe)) != str(row["activity_digest"]):
                    raise ProviderSessionObserverError(
                        "SEMANTIC_SOURCE_NOT_CURRENT", str(row["activity_id"])
                    )
                for role, raw_text in _codex_semantic_messages(event):
                    text = _redact_semantic_text(raw_text)
                    if not text:
                        continue
                    remaining = SEMANTIC_TOTAL_CHAR_LIMIT - total_chars
                    if remaining <= 0 or len(excerpts) >= SEMANTIC_EXCERPT_LIMIT:
                        break
                    text = text[:remaining]
                    text_digest = _sha256(_canonical_json(text))
                    ordinal = int(row["ordinal"])
                    semantic_key = (role, text_digest)
                    is_adjacent_telemetry_twin = (
                        semantic_key == previous_semantic_key
                        and previous_ordinal is not None
                        and ordinal == previous_ordinal + 1
                    )
                    previous_semantic_key = semantic_key
                    previous_ordinal = ordinal
                    if is_adjacent_telemetry_twin:
                        continue
                    total_chars += len(text)
                    excerpts.append(
                        {
                            "excerpt_id": "semantic_" + text_digest[:24],
                            "activity_digest": str(row["activity_digest"]),
                            "ordinal": ordinal,
                            "role": role,
                            "text": text,
                            "text_digest": text_digest,
                        }
                    )
        if not excerpts:
            raise ProviderSessionObserverError(
                "SEMANTIC_EVIDENCE_EMPTY",
                "selected Activity has no bounded user or assistant text",
            )
        return excerpts

    def build_transient_live_deltas(
        self,
        source_id: str,
        *,
        added_count: int,
    ) -> dict[str, Any]:
        """Return only this scan's redacted message deltas in process memory.

        Activity rows remain metadata-only.  The caller receives no source path
        or provider-session identifier, and the extracted text is never written
        back to this store.
        """

        if isinstance(added_count, bool) or not isinstance(added_count, int):
            raise ProviderSessionObserverError(
                "SEMANTIC_EVIDENCE_INVALID", "added_count must be an integer"
            )
        with self._connection() as connection:
            source = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if source is None:
            raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
        public_source = self._public_source_row(source)
        if added_count <= 0:
            return {
                "schema": "universe.provider-live-delta.v1",
                "source": public_source,
                "delivery": "NO_NEW_ACTIVITY",
                "deltas": [],
            }
        if str(source["provider"]) != "CODEX":
            return {
                "schema": "universe.provider-live-delta.v1",
                "source": public_source,
                "delivery": "ACTIVITY_ONLY",
                "deltas": [],
            }
        activities = self.list_activities(source_id)[: min(added_count, SEMANTIC_EXCERPT_LIMIT)]
        activity_refs = [
            {
                "activity_id": activity["activity_id"],
                "activity_digest": activity["activity_digest"],
                "ordinal": activity["ordinal"],
            }
            for activity in reversed(activities)
        ]
        try:
            excerpts = self.build_transient_semantic_evidence(source_id, activity_refs)
        except ProviderSessionObserverError as error:
            if error.code != "SEMANTIC_EVIDENCE_EMPTY":
                raise
            excerpts = []
        return {
            "schema": "universe.provider-live-delta.v1",
            "source": public_source,
            "delivery": "TRANSIENT_REDACTED" if excerpts else "ACTIVITY_ONLY",
            "deltas": excerpts,
        }

    def scan(
        self,
        source_id: str,
        *,
        max_bytes: int = DEFAULT_SCAN_BYTE_LIMIT,
        max_events: int = DEFAULT_SCAN_EVENT_LIMIT,
        max_seconds: float = DEFAULT_SCAN_TIME_LIMIT_SECONDS,
    ) -> dict[str, Any]:
        if max_bytes <= 0 or max_events <= 0 or max_seconds <= 0:
            raise ProviderSessionObserverError(
                "SOURCE_REQUEST_INVALID", "scan limits must be positive"
            )
        max_bytes = min(max_bytes, MAX_SINGLE_EVENT_BYTE_LIMIT)
        max_events = min(max_events, 4096)
        max_seconds = min(max_seconds, 2.0)
        started = time.monotonic()
        with self._connection() as connection:
            source = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
            path = Path(source["source_path"])
            if not path.is_file():
                return self._mark_unknown(connection, source, "SOURCE_MISSING")
            identity = _file_identity(path)
            offset = int(source["cursor_offset"])
            ordinal = int(source["cursor_ordinal"])
            if source["file_identity"] and source["file_identity"] != identity:
                return self._mark_unknown(connection, source, "SOURCE_ROTATED")
            size = path.stat().st_size
            if size < offset:
                return self._mark_unknown(connection, source, "SOURCE_TRUNCATED")
            if not source["file_identity"]:
                connection.execute(
                    "UPDATE provider_session_source SET file_identity = ? WHERE source_id = ?",
                    (identity, source_id),
                )
            added = 0
            scanned_bytes = 0
            scanned_events = 0
            oversized_events = 0
            next_offset = offset
            try:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    while True:
                        start = handle.tell()
                        if scanned_events >= max_events or (
                            scanned_events and time.monotonic() - started >= max_seconds
                        ):
                            break
                        raw_line = handle.readline(MAX_SINGLE_EVENT_BYTE_LIMIT + 1)
                        if len(raw_line) > MAX_SINGLE_EVENT_BYTE_LIMIT:
                            raise ProviderSessionObserverError(
                                "SOURCE_EVENT_TOO_LARGE",
                                "JSONL event exceeds the single-event byte limit",
                            )
                        if not raw_line or not raw_line.endswith(b"\n"):
                            break
                        if scanned_bytes + len(raw_line) > max_bytes:
                            if scanned_events:
                                handle.seek(start)
                                break
                            oversized_events += 1
                        next_offset = handle.tell()
                        scanned_bytes += len(raw_line)
                        scanned_events += 1
                        try:
                            event = json.loads(raw_line.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError) as error:
                            raise ProviderSessionObserverError(
                                "SOURCE_SCHEMA_UNSUPPORTED", type(error).__name__
                            ) from error
                        if not isinstance(event, Mapping):
                            raise ProviderSessionObserverError(
                                "SOURCE_SCHEMA_UNSUPPORTED", "JSONL event must be an object"
                            )
                        ordinal += 1
                        if self._reduce_event(connection, source, event, ordinal, start):
                            added += 1
            except ProviderSessionObserverError as error:
                return self._mark_unknown(connection, source, error.code)
            now = _now()
            connection.execute(
                """
                UPDATE provider_session_source
                SET cursor_offset = ?, cursor_ordinal = ?, status = 'ACTIVE', reason = NULL,
                    last_seen_at = ?, updated_at = ?
                WHERE source_id = ?
                """,
                (next_offset, ordinal, now, now, source_id),
            )
            current = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?", (source_id,)
            ).fetchone()
        return {
            "schema": OBSERVER_SCHEMA,
            "source": self._source_row(current),
            "added": added,
            "bounded_read": {
                "bytes": scanned_bytes,
                "events": scanned_events,
                "oversized_events": oversized_events,
                "byte_limit": max_bytes,
                "event_limit": max_events,
            },
        }

    def discover_sessions(
        self, provider: str, *, home: Path | None = None
    ) -> list[dict[str, Any]]:
        return [
            self._public_discovery(item)
            for item in self.discover_sources(provider, home=home)
        ]

    def identify_session(self, source_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_session_source WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise ProviderSessionObserverError("SOURCE_NOT_FOUND", source_id)
        return self._public_source_row(row)

    def open_cursor(self, source_id: str) -> dict[str, Any]:
        source = self.identify_session(source_id)
        return {
            "source_id": source_id,
            "cursor": source["cursor"],
            "status": source["status"],
        }

    def read_bounded(self, source_id: str, **limits: Any) -> dict[str, Any]:
        result = self.scan(source_id, **limits)
        return {
            **result,
            "source": self._public_source_row_from_mapping(result["source"]),
        }

    def project_activity(self, source_id: str) -> dict[str, Any]:
        activities = self.list_activities(source_id)
        latest = activities[0] if activities else None
        return {
            "source_id": source_id,
            "activity_state": (latest or {}).get("activity_state") or "UNKNOWN",
            "last_activity_at": (latest or {}).get("observed_at"),
            "evidence_state": "OBSERVED" if latest else "UNKNOWN",
        }

    def reduce(self, source_id: str) -> dict[str, Any]:
        return self.project_activity(source_id)

    def _reduce_event(
        self,
        connection: sqlite3.Connection,
        source: sqlite3.Row,
        event: Mapping[str, Any],
        ordinal: int,
        byte_offset: int,
    ) -> bool:
        provider = str(source["provider"])
        event_type = _event_type(event)
        event_kind, activity_state = _safe_event_kind(event_type)
        if provider == "CODEX" and _codex_semantic_messages(event):
            event_kind, activity_state = "TURN_COMPLETED", "COMPLETED"
        event_id = _event_id(event, f"offset-{byte_offset}")
        parent_id: str | None = None
        if provider == "CLAUDE":
            if not isinstance(event.get("uuid"), str) or not str(event["uuid"]).strip():
                raise ProviderSessionObserverError(
                    "SOURCE_SCHEMA_UNSUPPORTED", "Claude event requires uuid"
                )
            parent = event.get("parentUuid")
            if parent is not None and not isinstance(parent, str):
                raise ProviderSessionObserverError(
                    "SOURCE_SCHEMA_UNSUPPORTED", "Claude parentUuid must be a string"
                )
            parent_id = parent.strip() if isinstance(parent, str) and parent.strip() else None
            if parent_id is not None:
                connection.execute(
                    """
                    UPDATE provider_session_activity SET active = 0
                    WHERE source_id = ? AND provider_event_id = ?
                    """,
                    (source["source_id"], parent_id),
                )
        # Deliberately hash only source identity and public reducer metadata.
        # The raw JSON event, transcript text, command text, and prompt are never
        # persisted or fed into this digest.
        safe = {
            "source_id": source["source_id"],
            "provider_event_id": event_id,
            "ordinal": ordinal,
            "event_type": event_type[:96],
            "parent_id": parent_id,
        }
        digest = _sha256(_canonical_json(safe))
        activity_id = "activity_" + digest[:24]
        inserted = connection.execute(
            """
                INSERT OR IGNORE INTO provider_session_activity(
                    activity_id, source_id, provider_event_id, ordinal, event_kind,
                    activity_state, observed_at, activity_digest, byte_offset,
                    branch_parent_id, active, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                activity_id,
                source["source_id"],
                event_id,
                ordinal,
                event_kind,
                activity_state,
                _event_time(event),
                digest,
                byte_offset,
                parent_id,
                _now(),
            ),
        )
        return inserted.rowcount == 1

    def _mark_unknown(
        self, connection: sqlite3.Connection, source: sqlite3.Row, reason: str
    ) -> dict[str, Any]:
        now = _now()
        connection.execute(
            """
            UPDATE provider_session_source
            SET status = 'UNKNOWN', reason = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (reason, now, source["source_id"]),
        )
        row = connection.execute(
            "SELECT * FROM provider_session_source WHERE source_id = ?",
            (source["source_id"],),
        ).fetchone()
        return {"schema": OBSERVER_SCHEMA, "source": self._source_row(row), "added": 0}

    @staticmethod
    def _source_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": SOURCE_SCHEMA,
            "source_id": row["source_id"],
            "provider": row["provider"],
            "provider_session_id": row["provider_session_id"],
            "source_path": row["source_path"],
            "source_kind": row["source_kind"],
            "source_version": row["source_version"],
            "enabled": bool(row["enabled"]),
            "file_identity": row["file_identity"],
            "cursor": {"offset": row["cursor_offset"], "ordinal": row["cursor_ordinal"]},
            "status": row["status"],
            "reason": row["reason"],
            "last_seen_at": row["last_seen_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _public_source_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": SOURCE_SCHEMA,
            "source_id": row["source_id"],
            "provider": row["provider"],
            "source_kind": row["source_kind"],
            "enabled": bool(row["enabled"]),
            "cursor": {
                "offset": row["cursor_offset"],
                "ordinal": row["cursor_ordinal"],
            },
            "status": row["status"],
            "reason": row["reason"],
            "last_seen_at": row["last_seen_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _public_source_row_from_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
        allowed = (
            "schema",
            "source_id",
            "provider",
            "source_kind",
            "enabled",
            "cursor",
            "status",
            "reason",
            "last_seen_at",
            "updated_at",
        )
        return {key: row.get(key) for key in allowed}

    @staticmethod
    def _public_discovery(value: Mapping[str, Any]) -> dict[str, Any]:
        identity_state = str(value.get("identity_state") or "UNKNOWN")
        key_material = "\0".join(
            (
                str(value.get("provider") or "UNKNOWN"),
                str(value.get("source_path") or ""),
            )
        )
        return {
            "schema": SOURCE_SCHEMA,
            "discovery_key": "discovery_" + _sha256(key_material)[:24],
            "provider": value.get("provider"),
            "source_kind": value.get("source_kind"),
            "last_modified_at": value.get("last_modified_at"),
            "display_name": value.get("display_name"),
            "workspace_name": value.get("workspace_name"),
            "session_kind": value.get("session_kind"),
            "identity_state": identity_state,
            "binding_state": (
                "ELIGIBLE" if identity_state == "VERIFIED" else "UNBOUND"
            ),
            "transcript_content": "EXCLUDED",
        }

    @staticmethod
    def _default_provider_home(provider: str) -> Path:
        if provider == "CODEX":
            return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        if provider == "CLAUDE":
            return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        return Path(os.environ.get("GROK_HOME") or Path.home() / ".grok")

    @staticmethod
    def _activity_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema": ACTIVITY_SCHEMA,
            "activity_id": row["activity_id"],
            "source_id": row["source_id"],
            "provider_event_id": row["provider_event_id"],
            "ordinal": row["ordinal"],
            "event_kind": row["event_kind"],
            "activity_state": row["activity_state"],
            "observed_at": row["observed_at"],
            "activity_digest": row["activity_digest"],
            "branch_parent_id": row["branch_parent_id"],
            "active": bool(row["active"]),
            "recorded_at": row["recorded_at"],
        }
