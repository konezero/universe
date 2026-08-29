"""Project-local incremental file index and mechanical search."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "universe.project-file-index.v1"
SEARCH_SCHEMA = "universe.project-file-search.v1"
MAX_EXCERPT_BYTES = 32_768
MAX_FILE_BYTES = 1_048_576
MAX_HITS = 50
SYNC_STATE_SCHEMA = "universe.project-file-index-sync-state.v1"
GRAPH_CANDIDATE_SCHEMA = "universe.project-file-graph-candidates.v1"
PROJECT_INDEX_IDENTITY_SCHEMA = "universe.project-file-index-identity.v1"
PROJECT_INDEX_RELATIVE_PATH = Path(
    ".ai/runtime/state/project_file_index.sqlite3"
)

IMPLEMENTATION_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".rs", ".go", ".java", ".kt",
    ".c", ".h", ".cpp", ".cs", ".sh", ".ps1", ".sql",
}

SKIP_DIR_NAMES = {
    ".artifacts",
    ".git",
    ".hg",
    ".mypy_cache",
    ".playwright-mcp",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "venv",
}

AI_TEXT_INDEX_PREFIXES = (
    ".ai/adapters/",
    ".ai/agents/",
    ".ai/core/",
    ".ai/distribution/",
    ".ai/governance/",
    ".ai/memory/",
    ".ai/skills/",
    ".ai/templates/",
    ".ai/universe/",
    ".ai/runtime/project_instance/",
    ".ai/runtime/reference_runtime/",
    ".ai/runtime/tools/",
)

SKIP_RELATIVE_PREFIXES = (
    ".ai/runtime/tmp/",
    ".ai/runtime/state/",
    ".ai/runtime/session_store/",
    ".ai/runtime/task_frames/",
    ".ai/runtime/release_db/",
)

TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".txt",
    ".json",
    ".toml",
    ".yml",
    ".yaml",
    ".js",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".sh",
    ".ps1",
    ".sql",
}

DDL = """
CREATE TABLE IF NOT EXISTS project_index_identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_root TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_file_index (
    project_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    text_excerpt TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, relative_path)
);

CREATE INDEX IF NOT EXISTS project_file_index_project_time
ON project_file_index(project_id, indexed_at, relative_path);

CREATE TABLE IF NOT EXISTS project_file_index_sync_state (
    project_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('SUCCEEDED', 'FAILED', 'BLOCKED')),
    mode TEXT NOT NULL,
    anchor_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    last_attempt_at TEXT NOT NULL,
    last_success_at TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_detail TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_file_graph_candidate (
    project_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    candidate_kind TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN ('CURRENT', 'REMOVED')),
    promotion_state TEXT NOT NULL CHECK(promotion_state IN ('USER_APPROVAL_REQUIRED')),
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    removed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, relative_path)
);

CREATE INDEX IF NOT EXISTS project_file_graph_candidate_project_state
ON project_file_graph_candidate(project_id, lifecycle_state, relative_path);
"""


class FileIndexError(Exception):
    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        self.detail = detail
        super().__init__(detail)


class ProjectIndexReadConnection(sqlite3.Connection):
    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def project_index_path(project_root: Path) -> Path:
    return project_root.resolve(strict=True) / PROJECT_INDEX_RELATIVE_PATH


def initialize_project_index(
    connection: sqlite3.Connection, *, project_id: str, project_root: Path
) -> dict[str, str]:
    root = project_root.resolve(strict=True)
    normalized_id = project_id.strip()
    if not normalized_id:
        raise FileIndexError("PROJECT_ID_REQUIRED", "project_id is required")
    connection.row_factory = sqlite3.Row
    connection.executescript(DDL)
    row = connection.execute(
        "SELECT schema, project_id, project_root FROM project_index_identity WHERE singleton = 1"
    ).fetchone()
    now = _now()
    if row is None:
        connection.execute(
            """
            INSERT INTO project_index_identity(
                singleton, schema, project_id, project_root, created_at, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?)
            """,
            (PROJECT_INDEX_IDENTITY_SCHEMA, normalized_id, str(root), now, now),
        )
    elif (
        str(row["schema"]) != PROJECT_INDEX_IDENTITY_SCHEMA
        or str(row["project_id"]) != normalized_id
        or Path(str(row["project_root"])).resolve() != root
    ):
        raise FileIndexError(
            "PROJECT_INDEX_IDENTITY_MISMATCH",
            "project index identity does not match the requested project",
        )
    else:
        connection.execute(
            "UPDATE project_index_identity SET updated_at = ? WHERE singleton = 1",
            (now,),
        )
    return {
        "schema": PROJECT_INDEX_IDENTITY_SCHEMA,
        "project_id": normalized_id,
        "project_root": str(root),
        "database_path": str(project_index_path(root)),
    }


def open_project_index_readonly(
    *, project_id: str, project_root: Path
) -> sqlite3.Connection:
    root = project_root.resolve(strict=True)
    path = project_index_path(root)
    if not path.is_file():
        raise FileIndexError(
            "PROJECT_INDEX_MISSING",
            f"project file index is absent: {path}",
        )
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
        timeout=2,
        factory=ProjectIndexReadConnection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        row = connection.execute(
            "SELECT schema, project_id, project_root FROM project_index_identity WHERE singleton = 1"
        ).fetchone()
        if row is None or (
            str(row["schema"]) != PROJECT_INDEX_IDENTITY_SCHEMA
            or str(row["project_id"]) != project_id.strip()
            or Path(str(row["project_root"])).resolve() != root
        ):
            raise FileIndexError(
                "PROJECT_INDEX_IDENTITY_MISMATCH",
                "project index identity does not match the registered project",
            )
    except FileIndexError:
        connection.close()
        raise
    except sqlite3.Error as error:
        connection.close()
        raise FileIndexError(
            "PROJECT_INDEX_INVALID",
            f"project file index schema cannot be read: {error}",
        ) from error
    return connection


def sync_project_index_from_hook(
    *,
    project_id: str,
    project_root: Path,
    mode: str,
    anchor_id: str,
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    used = require_mode_current_anchor(root, mode=mode, anchor_id=anchor_id)
    path = project_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        with connection:
            identity = initialize_project_index(
                connection, project_id=project_id, project_root=root
            )
            index = (
                sync_index_paths(
                    connection,
                    project_id=project_id.strip(),
                    project_root=root,
                    changed_paths=changed_paths,
                )
                if changed_paths is not None
                else sync_index(
                    connection, project_id=project_id.strip(), project_root=root
                )
            )
            sync = record_sync_state(
                connection,
                project_id=project_id.strip(),
                state="SUCCEEDED",
                mode=used["mode"],
                anchor_id=used["anchor_id"],
                summary=index,
            )
    finally:
        connection.close()
    return {
        "schema": SCHEMA,
        "status": "PROJECT_INDEX_HOOK_SYNCED",
        "identity": identity,
        "index": index,
        "sync": sync,
    }


def _normalize_relative(path: Path) -> str:
    text = path.as_posix()
    if text.startswith("./"):
        return text[2:]
    return text


def should_skip(relative_path: str) -> bool:
    normalized = _normalize_relative(Path(relative_path))
    if normalized == ".ai":
        return False
    if normalized.startswith(".ai/"):
        allowed = any(
            normalized == prefix.rstrip("/") or normalized.startswith(prefix)
            for prefix in AI_TEXT_INDEX_PREFIXES
        )
        ancestor = any(
            prefix.startswith(normalized.rstrip("/") + "/")
            for prefix in AI_TEXT_INDEX_PREFIXES
        )
        if not allowed and not ancestor:
            return True
    parts = normalized.split("/")
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in SKIP_RELATIVE_PREFIXES)


def iter_project_files(project_root: Path) -> list[Path]:
    root = project_root.resolve(strict=True)
    found: list[Path] = []
    is_junction = getattr(os.path, "isjunction", lambda _path: False)
    for current, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in directory_names:
            candidate = current_path / name
            relative = _normalize_relative(candidate.relative_to(root))
            if should_skip(relative + "/"):
                continue
            if os.path.islink(candidate) or is_junction(candidate):
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories
        for name in file_names:
            path = current_path / name
            relative = _normalize_relative(path.relative_to(root))
            if should_skip(relative):
                continue
            if os.path.islink(path) or is_junction(path):
                continue
            found.append(Path(relative))
    return sorted(found)


def read_excerpt(path: Path) -> str:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return ""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size <= 0 or size > MAX_FILE_BYTES:
        return ""
    raw = path.read_bytes()[:MAX_EXCERPT_BYTES]
    if b"\x00" in raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def file_fingerprint(path: Path) -> tuple[int, int, str, str]:
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    before_mtime = getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000))
    after_mtime = getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000))
    if before.st_size != after.st_size or before_mtime != after_mtime:
        raise FileIndexError(
            "FILE_CHANGED_DURING_INDEX",
            f"file changed while it was indexed: {path}",
        )
    digest = hashlib.sha256(data).hexdigest()
    excerpt = ""
    if (
        path.suffix.lower() in TEXT_SUFFIXES
        and 0 < len(data) <= MAX_FILE_BYTES
        and b"\x00" not in data[:MAX_EXCERPT_BYTES]
    ):
        excerpt = data[:MAX_EXCERPT_BYTES].decode("utf-8", errors="replace")
    return after.st_size, after_mtime, digest, excerpt


def require_mode_current_anchor(
    project_root: Path, *, mode: str, anchor_id: str
) -> dict[str, str]:
    normalized_mode = mode.strip().upper()
    normalized_anchor = anchor_id.strip()
    if not normalized_mode or not normalized_anchor:
        raise FileIndexError(
            "MODE_CURRENT_ANCHOR_REQUIRED",
            "mode and anchor_id are required",
        )
    store = Path(project_root) / ".ai" / "runtime" / "state" / "project_runtime.sqlite3"
    if not store.is_file():
        raise FileIndexError(
            "MODE_CURRENT_ANCHOR_STORE_MISSING",
            "project_runtime.sqlite3 is absent; Mode Current Anchor cannot be used",
        )
    connection = sqlite3.connect(f"file:{store.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            """
            SELECT mode, anchor_id, state
            FROM mode_current_anchor
            WHERE mode = ?
            """,
            (normalized_mode,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise FileIndexError(
            "MODE_CURRENT_ANCHOR_MISSING",
            f"no Current Anchor exists for mode {normalized_mode}",
        )
    if str(row[1]) != normalized_anchor:
        raise FileIndexError(
            "MODE_CURRENT_ANCHOR_MISMATCH",
            f"requested {normalized_anchor} is not the Current Anchor for {normalized_mode}",
        )
    return {"mode": str(row[0]), "anchor_id": str(row[1]), "state": str(row[2])}


def resolve_mode_current_anchor(
    project_root: Path, *, preferred_mode: str = "MASTER"
) -> dict[str, str]:
    root = project_root.resolve(strict=True)
    store = root / ".ai" / "runtime" / "state" / "project_runtime.sqlite3"
    if not store.is_file():
        raise FileIndexError(
            "MODE_CURRENT_ANCHOR_STORE_MISSING",
            "project_runtime.sqlite3 is absent; automatic file indexing is blocked",
        )
    mode = preferred_mode.strip().upper()
    connection = sqlite3.connect(f"file:{store.resolve().as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT mode, anchor_id FROM mode_current_anchor WHERE mode = ?",
            (mode,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise FileIndexError(
            "MODE_CURRENT_ANCHOR_MISSING",
            f"no Current Anchor exists for automatic {mode} file indexing",
        )
    return require_mode_current_anchor(root, mode=str(row[0]), anchor_id=str(row[1]))


def record_sync_state(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    state: str,
    mode: str,
    anchor_id: str,
    summary: Mapping[str, Any] | None = None,
    error_code: str = "",
    error_detail: str = "",
) -> dict[str, Any]:
    normalized_state = state.strip().upper()
    if normalized_state not in {"SUCCEEDED", "FAILED", "BLOCKED"}:
        raise FileIndexError("FILE_INDEX_SYNC_STATE_INVALID", "sync state is invalid")
    now = _now()
    existing = connection.execute(
        "SELECT generation, last_success_at FROM project_file_index_sync_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    generation = int(existing["generation"]) if existing is not None else 0
    last_success_at = str(existing["last_success_at"]) if existing is not None else ""
    if normalized_state == "SUCCEEDED":
        generation += 1
        last_success_at = now
    summary_json = json.dumps(
        dict(summary or {}), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    connection.execute(
        """
        INSERT INTO project_file_index_sync_state(
            project_id, state, mode, anchor_id, generation, last_attempt_at,
            last_success_at, error_code, error_detail, summary_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(project_id) DO UPDATE SET
            state = excluded.state,
            mode = excluded.mode,
            anchor_id = excluded.anchor_id,
            generation = excluded.generation,
            last_attempt_at = excluded.last_attempt_at,
            last_success_at = excluded.last_success_at,
            error_code = excluded.error_code,
            error_detail = excluded.error_detail,
            summary_json = excluded.summary_json
        """,
        (
            project_id,
            normalized_state,
            mode.strip().upper(),
            anchor_id.strip(),
            generation,
            now,
            last_success_at,
            error_code.strip().upper(),
            error_detail.strip()[:2000],
            summary_json,
        ),
    )
    return {
        "schema": SYNC_STATE_SCHEMA,
        "project_id": project_id,
        "state": normalized_state,
        "mode": mode.strip().upper(),
        "anchor_id": anchor_id.strip(),
        "generation": generation,
        "last_attempt_at": now,
        "last_success_at": last_success_at,
        "error_code": error_code.strip().upper(),
        "error_detail": error_detail.strip()[:2000],
        "summary": dict(summary or {}),
    }


def reconcile_graph_candidates(
    connection: sqlite3.Connection, *, project_id: str, observed_at: str
) -> dict[str, Any]:
    current = {
        str(row["relative_path"]): str(row["sha256"])
        for row in connection.execute(
            "SELECT relative_path, sha256 FROM project_file_index WHERE project_id = ?",
            (project_id,),
        )
        if Path(str(row["relative_path"])).suffix.lower() in IMPLEMENTATION_SUFFIXES
    }
    existing = {
        str(row["relative_path"]): row
        for row in connection.execute(
            """
            SELECT relative_path, sha256, lifecycle_state
            FROM project_file_graph_candidate
            WHERE project_id = ?
            """,
            (project_id,),
        )
    }
    created = updated = unchanged = removed = 0
    for relative_path, digest in sorted(current.items()):
        previous = existing.get(relative_path)
        if previous is None:
            created += 1
        elif str(previous["sha256"]) != digest or str(previous["lifecycle_state"]) != "CURRENT":
            updated += 1
        else:
            unchanged += 1
        connection.execute(
            """
            INSERT INTO project_file_graph_candidate(
                project_id, relative_path, sha256, candidate_kind,
                lifecycle_state, promotion_state, first_observed_at,
                last_observed_at, removed_at
            ) VALUES (?, ?, ?, 'IMPLEMENTATION_MODULE', 'CURRENT',
                      'USER_APPROVAL_REQUIRED', ?, ?, '')
            ON CONFLICT(project_id, relative_path) DO UPDATE SET
                sha256 = excluded.sha256,
                candidate_kind = excluded.candidate_kind,
                lifecycle_state = 'CURRENT',
                promotion_state = 'USER_APPROVAL_REQUIRED',
                last_observed_at = excluded.last_observed_at,
                removed_at = ''
            """,
            (project_id, relative_path, digest, observed_at, observed_at),
        )
    for relative_path in sorted(set(existing) - set(current)):
        previous = existing[relative_path]
        if str(previous["lifecycle_state"]) == "REMOVED":
            continue
        connection.execute(
            """
            UPDATE project_file_graph_candidate
            SET lifecycle_state = 'REMOVED', last_observed_at = ?, removed_at = ?
            WHERE project_id = ? AND relative_path = ?
            """,
            (observed_at, observed_at, project_id, relative_path),
        )
        removed += 1
    return {
        "schema": GRAPH_CANDIDATE_SCHEMA,
        "project_id": project_id,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "current_count": len(current),
        "observed_at": observed_at,
        "projection_only": True,
        "promotion_state": "USER_APPROVAL_REQUIRED",
    }


def list_graph_candidates(
    connection: sqlite3.Connection, *, project_id: str, include_removed: bool = True
) -> list[dict[str, Any]]:
    where = "project_id = ?" if include_removed else "project_id = ? AND lifecycle_state = 'CURRENT'"
    rows = connection.execute(
        f"""
        SELECT relative_path, sha256, candidate_kind, lifecycle_state,
               promotion_state, first_observed_at, last_observed_at, removed_at
        FROM project_file_graph_candidate
        WHERE {where}
        ORDER BY relative_path
        """,
        (project_id,),
    ).fetchall()
    return [
        {
            "relative_path": str(row["relative_path"]),
            "sha256": str(row["sha256"]),
            "candidate_kind": str(row["candidate_kind"]),
            "lifecycle_state": str(row["lifecycle_state"]),
            "promotion_state": str(row["promotion_state"]),
            "first_observed_at": str(row["first_observed_at"]),
            "last_observed_at": str(row["last_observed_at"]),
            "removed_at": str(row["removed_at"]),
            "projection_only": True,
        }
        for row in rows
    ]


def sync_index(
    connection: sqlite3.Connection, *, project_id: str, project_root: Path
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    indexed_at = _now()
    existing = {
        str(row["relative_path"]): row
        for row in connection.execute(
            """
            SELECT relative_path, size_bytes, mtime_ns, sha256
            FROM project_file_index
            WHERE project_id = ?
            """,
            (project_id,),
        )
    }
    seen: set[str] = set()
    created = 0
    updated = 0
    unchanged = 0
    for relative in iter_project_files(root):
        key = _normalize_relative(relative)
        seen.add(key)
        absolute = root / relative
        try:
            stat = absolute.stat()
        except OSError:
            continue
        size = stat.st_size
        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        previous = existing.get(key)
        if (
            previous is not None
            and int(previous["size_bytes"]) == size
            and int(previous["mtime_ns"]) == mtime_ns
        ):
            unchanged += 1
            continue
        size, mtime_ns, digest, excerpt = file_fingerprint(absolute)
        connection.execute(
            """
            INSERT INTO project_file_index(
                project_id, relative_path, size_bytes, mtime_ns, sha256,
                text_excerpt, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, relative_path) DO UPDATE SET
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                sha256 = excluded.sha256,
                text_excerpt = excluded.text_excerpt,
                indexed_at = excluded.indexed_at
            """,
            (project_id, key, size, mtime_ns, digest, excerpt, indexed_at),
        )
        if previous is None:
            created += 1
        else:
            updated += 1
    removed = 0
    for stale in set(existing) - seen:
        connection.execute(
            """
            DELETE FROM project_file_index
            WHERE project_id = ? AND relative_path = ?
            """,
            (project_id, stale),
        )
        removed += 1
    graph_reconcile = reconcile_graph_candidates(
        connection, project_id=project_id, observed_at=indexed_at
    )
    return {
        "schema": SCHEMA,
        "project_id": project_id,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "indexed_at": indexed_at,
        "graph_reconcile": graph_reconcile,
    }


def sync_index_paths(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    project_root: Path,
    changed_paths: list[str],
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    indexed_at = _now()
    normalized: set[str] = set()
    for value in changed_paths:
        raw = Path(str(value))
        candidate = raw if raw.is_absolute() else root / raw
        try:
            relative = candidate.resolve(strict=False).relative_to(root)
        except ValueError as error:
            raise FileIndexError(
                "FILE_INDEX_PATH_OUTSIDE_PROJECT",
                f"changed path is outside the project root: {value}",
            ) from error
        key = _normalize_relative(relative)
        if key and not should_skip(key):
            normalized.add(key)

    created = updated = unchanged = removed = 0
    for key in sorted(normalized):
        absolute = root / Path(key)
        previous = connection.execute(
            """
            SELECT size_bytes, mtime_ns, sha256
            FROM project_file_index
            WHERE project_id = ? AND relative_path = ?
            """,
            (project_id, key),
        ).fetchone()
        if not absolute.is_file() or absolute.is_symlink():
            if previous is not None:
                connection.execute(
                    "DELETE FROM project_file_index WHERE project_id = ? AND relative_path = ?",
                    (project_id, key),
                )
                removed += 1
            continue
        stat = absolute.stat()
        mtime_ns = getattr(
            stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)
        )
        if (
            previous is not None
            and int(previous["size_bytes"]) == stat.st_size
            and int(previous["mtime_ns"]) == mtime_ns
        ):
            unchanged += 1
            continue
        size, mtime_ns, digest, excerpt = file_fingerprint(absolute)
        connection.execute(
            """
            INSERT INTO project_file_index(
                project_id, relative_path, size_bytes, mtime_ns, sha256,
                text_excerpt, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, relative_path) DO UPDATE SET
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                sha256 = excluded.sha256,
                text_excerpt = excluded.text_excerpt,
                indexed_at = excluded.indexed_at
            """,
            (project_id, key, size, mtime_ns, digest, excerpt, indexed_at),
        )
        if previous is None:
            created += 1
        else:
            updated += 1
    graph_reconcile = reconcile_graph_candidates(
        connection, project_id=project_id, observed_at=indexed_at
    )
    return {
        "schema": SCHEMA,
        "project_id": project_id,
        "scope": "HOOK_CHANGED_PATHS",
        "changed_path_count": len(normalized),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "indexed_at": indexed_at,
        "graph_reconcile": graph_reconcile,
    }


def search_index(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    query: str,
    limit: int = 20,
) -> dict[str, Any]:
    needle = query.strip().lower()
    if not needle:
        raise FileIndexError("SEARCH_QUERY_REQUIRED", "query must be a non-empty string")
    tokens = [part for part in needle.replace("/", " ").replace("\\", " ").split() if len(part) >= 2]
    capped = max(1, min(int(limit), MAX_HITS))
    hits: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT relative_path, size_bytes, sha256, text_excerpt, indexed_at
        FROM project_file_index
        WHERE project_id = ?
        ORDER BY relative_path
        """,
        (project_id,),
    ):
        path = str(row["relative_path"])
        excerpt = str(row["text_excerpt"] or "")
        haystack = f"{path}\n{excerpt}".lower()
        path_hit = needle in path.lower()
        excerpt_hit = needle in excerpt.lower()
        token_hits = [token for token in tokens if token in haystack]
        if not path_hit and not excerpt_hit and not token_hits:
            continue
        hits.append(
            {
                "relative_path": path,
                "size_bytes": int(row["size_bytes"]),
                "sha256": str(row["sha256"]),
                "indexed_at": str(row["indexed_at"]),
                "excerpt": excerpt[:2000],
                "match": {
                    "path": path_hit,
                    "excerpt": excerpt_hit,
                    "tokens": token_hits[:20],
                },
            }
        )
    hits.sort(
        key=lambda item: (
            -int(item["match"]["path"]),
            -int(item["match"]["excerpt"]),
            -len(item["match"]["tokens"]),
            item["relative_path"],
        )
    )
    return {
        "schema": SEARCH_SCHEMA,
        "project_id": project_id,
        "query": query,
        "hits": hits[:capped],
        "hit_count": min(len(hits), capped),
        "scanned": True,
    }


def index_status(connection: sqlite3.Connection, *, project_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS file_count, MAX(indexed_at) AS indexed_at
        FROM project_file_index
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    candidate = connection.execute(
        """
        SELECT
            SUM(CASE WHEN lifecycle_state = 'CURRENT' THEN 1 ELSE 0 END) AS current_count,
            SUM(CASE WHEN lifecycle_state = 'REMOVED' THEN 1 ELSE 0 END) AS removed_count
        FROM project_file_graph_candidate
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    sync = connection.execute(
        """
        SELECT state, mode, anchor_id, generation, last_attempt_at,
               last_success_at, error_code, error_detail, summary_json
        FROM project_file_index_sync_state
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    sync_state = None
    if sync is not None:
        sync_state = {
            "schema": SYNC_STATE_SCHEMA,
            "state": str(sync["state"]),
            "mode": str(sync["mode"]),
            "anchor_id": str(sync["anchor_id"]),
            "generation": int(sync["generation"]),
            "last_attempt_at": str(sync["last_attempt_at"]),
            "last_success_at": str(sync["last_success_at"]),
            "error_code": str(sync["error_code"]),
            "error_detail": str(sync["error_detail"]),
            "summary": json.loads(str(sync["summary_json"] or "{}")),
        }
    return {
        "schema": SCHEMA,
        "project_id": project_id,
        "file_count": int(row["file_count"] if row is not None else 0),
        "indexed_at": str(row["indexed_at"] or ""),
        "graph_candidates": {
            "schema": GRAPH_CANDIDATE_SCHEMA,
            "current_count": int(candidate["current_count"] or 0),
            "removed_count": int(candidate["removed_count"] or 0),
            "projection_only": True,
        },
        "sync": sync_state,
    }


def coordinate_from_request(payload: Mapping[str, Any]) -> tuple[str, str]:
    mode = str(payload.get("mode") or "").strip()
    anchor_id = str(payload.get("anchor_id") or "").strip()
    if not mode or not anchor_id:
        raise FileIndexError(
            "MODE_CURRENT_ANCHOR_REQUIRED",
            "mode and anchor_id must name the Mode Current Anchor",
        )
    return mode, anchor_id
