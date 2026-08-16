"""Project-local incremental file index and mechanical search."""

from __future__ import annotations

import hashlib
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

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

SKIP_RELATIVE_PREFIXES = (
    ".ai/runtime/tmp/",
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
CREATE TABLE IF NOT EXISTS project_file_index (
    project_id TEXT NOT NULL
        REFERENCES project_connection(project_id)
        ON DELETE CASCADE,
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
"""


class FileIndexError(Exception):
    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        self.detail = detail
        super().__init__(detail)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_relative(path: Path) -> str:
    text = path.as_posix()
    if text.startswith("./"):
        return text[2:]
    return text


def should_skip(relative_path: str) -> bool:
    normalized = _normalize_relative(Path(relative_path))
    parts = normalized.split("/")
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in SKIP_RELATIVE_PREFIXES)


def iter_project_files(project_root: Path) -> list[Path]:
    root = project_root.resolve(strict=True)
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = _normalize_relative(path.relative_to(root))
        if should_skip(relative):
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
    stat = path.stat()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    excerpt = read_excerpt(path)
    return stat.st_size, getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)), digest, excerpt


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
    return {
        "schema": SCHEMA,
        "project_id": project_id,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "indexed_at": indexed_at,
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
    return {
        "schema": SCHEMA,
        "project_id": project_id,
        "file_count": int(row["file_count"] if row is not None else 0),
        "indexed_at": str(row["indexed_at"] or ""),
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
