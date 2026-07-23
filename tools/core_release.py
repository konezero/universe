from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess  # nosec B404
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


RELEASE_SCHEMA = "universe.core-release-db.v1"
MANIFEST_SCHEMA = "universe.core-release-manifest.v1"
SOURCE_INDEX_SCHEMA = "ai-career.project-runtime-source-index.v1"
DISTRIBUTION_SCHEMA = "ai-career.project-runtime-distribution.v1"
DEFAULT_SOURCE_INDEX = (
    ".ai/distribution/context_management_runtime_pack/"
    "project_runtime_source_index.json"
)
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RELEASE_BYTES = 128 * 1024 * 1024
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE release_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE release_file (
    path TEXT PRIMARY KEY,
    git_mode TEXT NOT NULL,
    git_object_id TEXT NOT NULL,
    size INTEGER NOT NULL CHECK (size >= 0),
    sha256 TEXT NOT NULL,
    content BLOB NOT NULL
);
"""


class CoreReleaseError(ValueError):
    pass


@dataclass(frozen=True)
class GitBlob:
    path: str
    mode: str
    object_id: str
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class GitObjectReader:
    def __init__(self, repository: Path) -> None:
        self.repository = repository.expanduser().resolve(strict=True)
        git = shutil.which("git")
        if git is None:
            raise CoreReleaseError("git executable is unavailable")
        self.git = git
        if self._run("rev-parse", "--is-inside-work-tree").strip() != b"true":
            raise CoreReleaseError("source repository is not a Git work tree")

    def resolve_commit(self, source_ref: str) -> str:
        normalized = source_ref.strip()
        if not normalized or normalized.startswith("-") or "\x00" in normalized:
            raise CoreReleaseError("source_ref is invalid")
        result = self._run(
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{normalized}^{{commit}}",
        ).decode("ascii").strip()
        if COMMIT_PATTERN.fullmatch(result) is None:
            raise CoreReleaseError("source_ref did not resolve to one commit")
        return result

    def committed_at(self, commit: str) -> str:
        return self._run(
            "show",
            "-s",
            "--format=%cI",
            commit,
        ).decode("utf-8").strip()

    def read_blob(self, commit: str, path: str) -> GitBlob:
        normalized = validate_release_path(path)
        record = self._run("ls-tree", "-z", commit, "--", normalized)
        records = [item for item in record.split(b"\0") if item]
        if len(records) != 1:
            raise CoreReleaseError(f"release path is missing or ambiguous: {normalized}")
        try:
            header, returned_path = records[0].split(b"\t", 1)
            mode, object_type, object_id = header.decode("ascii").split()
            decoded_path = returned_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise CoreReleaseError(
                f"invalid Git tree record for release path: {normalized}"
            ) from error
        if decoded_path != normalized or object_type != "blob" or mode == "120000":
            raise CoreReleaseError(f"release path must be a regular Git blob: {normalized}")
        content = self._run("cat-file", "blob", object_id)
        if len(content) > MAX_FILE_BYTES:
            raise CoreReleaseError(f"release file exceeds size limit: {normalized}")
        return GitBlob(
            path=normalized,
            mode=mode,
            object_id=object_id,
            content=content,
        )

    def _run(self, *args: str) -> bytes:
        completed = subprocess.run(  # nosec B603
            [self.git, "--no-pager", "-C", str(self.repository), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise CoreReleaseError(f"Git object read failed: {detail}")
        return completed.stdout


def validate_release_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise CoreReleaseError("release path must be non-empty text")
    if "\\" in value or "\x00" in value or ":" in value:
        raise CoreReleaseError(f"release path is not canonical: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CoreReleaseError(f"release path escapes the source root: {value}")
    normalized = path.as_posix()
    if normalized != value:
        raise CoreReleaseError(f"release path is not canonical: {value}")
    return normalized


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_object(value: bytes, label: str) -> dict[str, Any]:
    try:
        result = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CoreReleaseError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(result, dict):
        raise CoreReleaseError(f"{label} must contain one JSON object")
    return result


def _source_inventory(
    reader: GitObjectReader,
    commit: str,
    source_index_path: str,
) -> tuple[dict[str, Any], dict[str, Any], list[GitBlob]]:
    source_index_blob = reader.read_blob(commit, source_index_path)
    if len(source_index_blob.content) > MAX_INDEX_BYTES:
        raise CoreReleaseError("source index exceeds size limit")
    source_index = _json_object(source_index_blob.content, "source index")
    if source_index.get("schema") != SOURCE_INDEX_SCHEMA:
        raise CoreReleaseError("source index schema is unsupported")
    raw_paths = source_index.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise CoreReleaseError("source index paths must be a non-empty array")
    if not all(isinstance(path, str) for path in raw_paths):
        raise CoreReleaseError("source index contains a non-text path")
    paths = [validate_release_path(path) for path in raw_paths]
    if len(paths) != len(set(paths)):
        raise CoreReleaseError("source index contains duplicate paths")
    if source_index_path not in paths:
        raise CoreReleaseError("source index does not include itself")

    required_refs = {
        "package_manifest_path",
        "installer_path",
        "core_registry_path",
    }
    for field in required_refs:
        path = validate_release_path(str(source_index.get(field, "")))
        if path not in paths:
            raise CoreReleaseError(f"source index reference is not packaged: {field}")

    blobs = [reader.read_blob(commit, path) for path in sorted(paths)]
    total_bytes = sum(len(blob.content) for blob in blobs)
    if total_bytes > MAX_RELEASE_BYTES:
        raise CoreReleaseError("release payload exceeds size limit")
    blob_by_path = {blob.path: blob for blob in blobs}
    distribution_path = str(source_index["package_manifest_path"])
    distribution = _json_object(
        blob_by_path[distribution_path].content,
        "distribution manifest",
    )
    if distribution.get("schema") != DISTRIBUTION_SCHEMA:
        raise CoreReleaseError("distribution manifest schema is unsupported")
    package = distribution.get("package")
    if not isinstance(package, dict) or not isinstance(package.get("name"), str):
        raise CoreReleaseError("distribution manifest package is invalid")
    if package.get("source_index_path") != source_index_path:
        raise CoreReleaseError("distribution manifest source index does not match")
    return source_index, distribution, blobs


def _payload_material(
    *,
    source_repository: str,
    source_commit: str,
    package_name: str,
    source_index_path: str,
    source_index_sha256: str,
    distribution_manifest_sha256: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": RELEASE_SCHEMA,
        "source_repository": source_repository,
        "source_commit": source_commit,
        "package_name": package_name,
        "source_index_path": source_index_path,
        "source_index_sha256": source_index_sha256,
        "distribution_manifest_sha256": distribution_manifest_sha256,
        "files": files,
    }


def build_release(
    *,
    source_repo: Path,
    source_ref: str,
    source_repository: str,
    database_path: Path,
    manifest_path: Path,
    expected_commit: str = "",
    source_index_path: str = DEFAULT_SOURCE_INDEX,
) -> dict[str, Any]:
    normalized_repository = source_repository.strip()
    if not normalized_repository:
        raise CoreReleaseError("source_repository is required")
    source_index_path = validate_release_path(source_index_path)
    reader = GitObjectReader(source_repo)
    source_commit = reader.resolve_commit(source_ref)
    if expected_commit and source_commit != expected_commit.strip().lower():
        raise CoreReleaseError("resolved source commit does not match expected_commit")

    source_index, distribution, blobs = _source_inventory(
        reader,
        source_commit,
        source_index_path,
    )
    blob_by_path = {blob.path: blob for blob in blobs}
    distribution_path = str(source_index["package_manifest_path"])
    file_material = [
        {
            "path": blob.path,
            "git_mode": blob.mode,
            "size": len(blob.content),
            "sha256": blob.sha256,
        }
        for blob in blobs
    ]
    payload = _payload_material(
        source_repository=normalized_repository,
        source_commit=source_commit,
        package_name=str(distribution["package"]["name"]),
        source_index_path=source_index_path,
        source_index_sha256=blob_by_path[source_index_path].sha256,
        distribution_manifest_sha256=blob_by_path[distribution_path].sha256,
        files=file_material,
    )
    payload_sha256 = sha256_bytes(canonical_bytes(payload))
    release_id = f"core-{source_commit[:12]}-{payload_sha256[:12]}"
    metadata = {
        "schema": RELEASE_SCHEMA,
        "release_id": release_id,
        "source_repository": normalized_repository,
        "source_ref": source_ref,
        "source_commit": source_commit,
        "source_committed_at": reader.committed_at(source_commit),
        "package_name": str(distribution["package"]["name"]),
        "source_index_path": source_index_path,
        "source_index_sha256": blob_by_path[source_index_path].sha256,
        "distribution_manifest_path": distribution_path,
        "distribution_manifest_sha256": blob_by_path[distribution_path].sha256,
        "file_count": str(len(blobs)),
        "payload_bytes": str(sum(len(blob.content) for blob in blobs)),
        "payload_sha256": payload_sha256,
        "candidate_execution": "FORBIDDEN",
        "source_access": "GIT_OBJECTS_DATA_ONLY",
    }
    _write_database(database_path, metadata, blobs)
    database_sha256 = sha256_bytes(database_path.read_bytes())
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "release_id": release_id,
        "source_repository": normalized_repository,
        "source_ref": source_ref,
        "source_commit": source_commit,
        "source_committed_at": metadata["source_committed_at"],
        "package_name": metadata["package_name"],
        "file_count": len(blobs),
        "payload_bytes": int(metadata["payload_bytes"]),
        "payload_sha256": payload_sha256,
        "database": database_path.name,
        "database_sha256": database_sha256,
        "source_access": "GIT_OBJECTS_DATA_ONLY",
        "candidate_execution": "FORBIDDEN",
    }
    _write_json_atomic(manifest_path, manifest)
    verify_release(database_path=database_path, manifest_path=manifest_path)
    return manifest


def _write_database(
    database_path: Path,
    metadata: dict[str, str],
    blobs: list[GitBlob],
) -> None:
    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{database_path.name}.",
        dir=database_path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA journal_mode = OFF")
            connection.execute("PRAGMA synchronous = OFF")
            connection.execute("PRAGMA page_size = 4096")
            connection.executescript(SCHEMA_SQL)
            with connection:
                connection.executemany(
                    "INSERT INTO release_metadata(key, value) VALUES (?, ?)",
                    sorted(metadata.items()),
                )
                connection.executemany(
                    """
                    INSERT INTO release_file(
                        path, git_mode, git_object_id, size, sha256, content
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            blob.path,
                            blob.mode,
                            blob.object_id,
                            len(blob.content),
                            blob.sha256,
                            blob.content,
                        )
                        for blob in blobs
                    ],
                )
            connection.execute("VACUUM")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise CoreReleaseError("release database integrity check failed")
        finally:
            connection.close()
        os.replace(temporary, database_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def verify_release(*, database_path: Path, manifest_path: Path) -> dict[str, Any]:
    database_path = database_path.expanduser().resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise CoreReleaseError("release manifest schema is unsupported")
    actual_database_sha = sha256_bytes(database_path.read_bytes())
    if actual_database_sha != manifest.get("database_sha256"):
        raise CoreReleaseError("release database digest does not match manifest")

    connection = sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise CoreReleaseError("release database integrity check failed")
        objects = connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table', 'view', 'trigger')
            ORDER BY type, name
            """
        ).fetchall()
        if objects != [
            ("table", "release_file"),
            ("table", "release_metadata"),
        ]:
            raise CoreReleaseError("release database contains unexpected objects")
        metadata = dict(connection.execute("SELECT key, value FROM release_metadata"))
        rows = connection.execute(
            """
            SELECT path, git_mode, size, sha256, content
            FROM release_file
            ORDER BY path
            """
        ).fetchall()
    finally:
        connection.close()

    if metadata.get("schema") != RELEASE_SCHEMA:
        raise CoreReleaseError("release database schema is unsupported")
    files: list[dict[str, Any]] = []
    payload_bytes = 0
    for path, mode, size, digest, content in rows:
        validate_release_path(path)
        if not isinstance(content, bytes):
            raise CoreReleaseError(f"release file content is invalid: {path}")
        if len(content) != size or sha256_bytes(content) != digest:
            raise CoreReleaseError(f"release file digest mismatch: {path}")
        payload_bytes += size
        files.append(
            {
                "path": path,
                "git_mode": mode,
                "size": size,
                "sha256": digest,
            }
        )
    if len(files) != int(metadata.get("file_count", "-1")):
        raise CoreReleaseError("release file count is inconsistent")
    if payload_bytes != int(metadata.get("payload_bytes", "-1")):
        raise CoreReleaseError("release payload size is inconsistent")

    payload = _payload_material(
        source_repository=metadata.get("source_repository", ""),
        source_commit=metadata.get("source_commit", ""),
        package_name=metadata.get("package_name", ""),
        source_index_path=metadata.get("source_index_path", ""),
        source_index_sha256=metadata.get("source_index_sha256", ""),
        distribution_manifest_sha256=metadata.get(
            "distribution_manifest_sha256",
            "",
        ),
        files=files,
    )
    payload_sha256 = sha256_bytes(canonical_bytes(payload))
    if payload_sha256 != metadata.get("payload_sha256"):
        raise CoreReleaseError("release payload digest is inconsistent")
    expected_manifest = {
        "release_id": metadata.get("release_id"),
        "source_repository": metadata.get("source_repository"),
        "source_commit": metadata.get("source_commit"),
        "package_name": metadata.get("package_name"),
        "file_count": len(files),
        "payload_bytes": payload_bytes,
        "payload_sha256": payload_sha256,
        "source_access": metadata.get("source_access"),
        "candidate_execution": metadata.get("candidate_execution"),
    }
    for field, value in expected_manifest.items():
        if manifest.get(field) != value:
            raise CoreReleaseError(f"release manifest field mismatch: {field}")
    if manifest.get("database") != database_path.name:
        raise CoreReleaseError("release manifest database name does not match")
    return {
        "status": "CORE_RELEASE_VERIFIED",
        "release_id": metadata["release_id"],
        "source_commit": metadata["source_commit"],
        "file_count": len(files),
        "payload_bytes": payload_bytes,
        "payload_sha256": payload_sha256,
        "database_sha256": actual_database_sha,
        "candidate_execution": metadata["candidate_execution"],
    }


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Build and verify Universe Core Release DBs")
    commands = cli.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build")
    build.add_argument("--source-repo", type=Path, required=True)
    build.add_argument("--source-ref", required=True)
    build.add_argument("--source-repository", required=True)
    build.add_argument("--expected-commit", default="")
    build.add_argument("--source-index", default=DEFAULT_SOURCE_INDEX)
    build.add_argument("--database", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)

    verify = commands.add_parser("verify")
    verify.add_argument("--database", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "build":
            result = build_release(
                source_repo=args.source_repo,
                source_ref=args.source_ref,
                source_repository=args.source_repository,
                expected_commit=args.expected_commit,
                source_index_path=args.source_index,
                database_path=args.database,
                manifest_path=args.manifest,
            )
            status = "CORE_RELEASE_BUILT"
        else:
            result = verify_release(
                database_path=args.database,
                manifest_path=args.manifest,
            )
            status = result["status"]
    except (CoreReleaseError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {
                    "status": "CORE_RELEASE_FAILED",
                    "detail": str(error),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"status": status, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
