from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess  # nosec B404
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from host_profile import resolve_host_tool
from release_profile_catalog import (
    GovernanceCatalog,
    ReleaseProfileCatalog,
    ReleaseProfileError,
    parse_release_catalog,
    parse_release_governance_catalog,
)

LEGACY_RELEASE_SCHEMA = "universe.core-release-db.v1"
RELEASE_SCHEMA = "universe.core-release-db.v2"
LEGACY_MANIFEST_SCHEMA = "universe.core-release-manifest.v1"
MANIFEST_SCHEMA = "universe.core-release-manifest.v2"
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

CREATE TABLE load_profile (
    profile_id TEXT PRIMARY KEY,
    description TEXT NOT NULL
);

CREATE TABLE load_profile_surface (
    profile_id TEXT NOT NULL
        REFERENCES load_profile(profile_id)
        ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    path TEXT NOT NULL
        REFERENCES release_file(path),
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    PRIMARY KEY(profile_id, ordinal),
    UNIQUE(profile_id, path)
);

CREATE TABLE skill_profile_binding (
    skill_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL
        REFERENCES load_profile(profile_id)
);

CREATE TABLE mode_profile (
    mode_profile_id TEXT PRIMARY KEY,
    overlay_policy TEXT NOT NULL
);

CREATE TABLE mode_profile_load (
    mode_profile_id TEXT NOT NULL
        REFERENCES mode_profile(mode_profile_id)
        ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    profile_id TEXT NOT NULL
        REFERENCES load_profile(profile_id),
    PRIMARY KEY(mode_profile_id, ordinal),
    UNIQUE(mode_profile_id, profile_id)
);

CREATE TABLE governance_unit (
    governance_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_ref TEXT NOT NULL
        REFERENCES release_file(path),
    source_digest TEXT NOT NULL,
    compact_instruction TEXT NOT NULL,
    release_id TEXT NOT NULL
);

CREATE TABLE governance_index (
    role TEXT NOT NULL,
    mode TEXT NOT NULL,
    operation TEXT NOT NULL,
    scope TEXT NOT NULL,
    risk TEXT NOT NULL,
    capability TEXT NOT NULL,
    governance_id TEXT NOT NULL
        REFERENCES governance_unit(governance_id),
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    priority INTEGER NOT NULL CHECK (priority >= 0),
    PRIMARY KEY(
        role, mode, operation, scope, risk, capability, governance_id
    )
);

CREATE TABLE governance_dependency (
    governance_id TEXT NOT NULL
        REFERENCES governance_unit(governance_id)
        ON DELETE CASCADE,
    requires_governance_id TEXT NOT NULL
        REFERENCES governance_unit(governance_id)
        ON DELETE CASCADE,
    PRIMARY KEY(governance_id, requires_governance_id)
);

CREATE TABLE governance_override (
    base_governance_id TEXT NOT NULL
        REFERENCES governance_unit(governance_id)
        ON DELETE CASCADE,
    overriding_governance_id TEXT NOT NULL
        REFERENCES governance_unit(governance_id)
        ON DELETE CASCADE,
    applies_when_json TEXT NOT NULL,
    PRIMARY KEY(
        base_governance_id, overriding_governance_id, applies_when_json
    )
);
"""


class CoreReleaseError(ValueError):
    pass


ReleaseCatalog = ReleaseProfileCatalog | GovernanceCatalog


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
        git = resolve_host_tool("git")
        if git is None:
            raise CoreReleaseError("git executable is unavailable")
        self.git = str(git.executable)
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
        try:
            completed = subprocess.run(  # nosec B603
                [self.git, "--no-pager", "-C", str(self.repository), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
                timeout=60,
            )
        except subprocess.TimeoutExpired as error:
            raise CoreReleaseError("Git object read timed out") from error
        except OSError as error:
            raise CoreReleaseError(f"Git object reader unavailable: {error}") from error
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
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[GitBlob],
    ReleaseCatalog | None,
    str,
]:
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
    catalog_path = "NONE"
    catalog: ReleaseCatalog | None = None
    raw_catalog_path = source_index.get("release_profile_catalog_path")
    if raw_catalog_path is not None:
        catalog_path = validate_release_path(str(raw_catalog_path))
        if catalog_path not in paths:
            raise CoreReleaseError(
                "source index reference is not packaged: "
                "release_profile_catalog_path"
            )
        try:
            catalog = parse_release_catalog(
                _json_object(
                    blob_by_path[catalog_path].content,
                    "release profile catalog",
                ),
                packaged_paths=paths,
            )
        except ReleaseProfileError as error:
            raise CoreReleaseError(str(error)) from error
    return source_index, distribution, blobs, catalog, catalog_path


def _profile_material(
    *,
    catalog: ReleaseCatalog | None,
    catalog_path: str,
    blob_by_path: dict[str, GitBlob],
) -> dict[str, Any]:
    if not isinstance(catalog, ReleaseProfileCatalog):
        return {
            "status": "ABSENT",
            "path": "NONE",
            "sha256": "NONE",
            "catalog_digest": "NONE",
            "load_profile_count": 0,
            "skill_binding_count": 0,
            "mode_profile_count": 0,
        }
    return {
        "status": "PRESENT",
        "path": catalog_path,
        "sha256": blob_by_path[catalog_path].sha256,
        "catalog_digest": catalog.digest,
        "load_profile_count": len(catalog.load_profiles),
        "skill_binding_count": len(catalog.skill_bindings),
        "mode_profile_count": len(catalog.mode_profiles),
    }


def _governance_material(
    *,
    catalog: ReleaseCatalog | None,
    catalog_path: str,
    blob_by_path: dict[str, GitBlob],
) -> dict[str, Any]:
    if not isinstance(catalog, GovernanceCatalog):
        return {
            "status": "ABSENT",
            "path": "NONE",
            "sha256": "NONE",
            "catalog_digest": "NONE",
            "governance_unit_count": 0,
            "governance_index_count": 0,
            "governance_dependency_count": 0,
            "governance_override_count": 0,
        }
    for unit in catalog.governance_units:
        if blob_by_path[unit.source_ref].sha256 != unit.source_digest:
            raise CoreReleaseError(
                "governance source digest does not match packaged file: "
                + unit.source_ref
            )
    return {
        "status": "PRESENT",
        "path": catalog_path,
        "sha256": blob_by_path[catalog_path].sha256,
        "catalog_digest": catalog.digest,
        "governance_unit_count": len(catalog.governance_units),
        "governance_index_count": len(catalog.governance_index),
        "governance_dependency_count": len(catalog.governance_dependencies),
        "governance_override_count": len(catalog.governance_overrides),
    }


def _payload_material(
    *,
    source_repository: str,
    source_commit: str,
    package_name: str,
    source_index_path: str,
    source_index_sha256: str,
    distribution_manifest_sha256: str,
    files: list[dict[str, Any]],
    schema: str = RELEASE_SCHEMA,
    profile_catalog: dict[str, Any] | None = None,
    governance_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": schema,
        "source_repository": source_repository,
        "source_commit": source_commit,
        "package_name": package_name,
        "source_index_path": source_index_path,
        "source_index_sha256": source_index_sha256,
        "distribution_manifest_sha256": distribution_manifest_sha256,
        "files": files,
    }
    if profile_catalog is not None:
        result["profile_catalog"] = profile_catalog
    if governance_catalog is not None:
        result["governance_catalog"] = governance_catalog
    return result


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

    source_index, distribution, blobs, profile_catalog, profile_catalog_path = (
        _source_inventory(
        reader,
        source_commit,
        source_index_path,
        )
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
    profile_material = _profile_material(
        catalog=profile_catalog,
        catalog_path=profile_catalog_path,
        blob_by_path=blob_by_path,
    )
    governance_material = _governance_material(
        catalog=profile_catalog,
        catalog_path=profile_catalog_path,
        blob_by_path=blob_by_path,
    )
    payload = _payload_material(
        source_repository=normalized_repository,
        source_commit=source_commit,
        package_name=str(distribution["package"]["name"]),
        source_index_path=source_index_path,
        source_index_sha256=blob_by_path[source_index_path].sha256,
        distribution_manifest_sha256=blob_by_path[distribution_path].sha256,
        files=file_material,
        profile_catalog=profile_material,
        governance_catalog=(
            governance_material
            if isinstance(profile_catalog, GovernanceCatalog)
            else None
        ),
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
        "profile_catalog_status": str(profile_material["status"]),
        "profile_catalog_path": str(profile_material["path"]),
        "profile_catalog_sha256": str(profile_material["sha256"]),
        "profile_catalog_digest": str(profile_material["catalog_digest"]),
        "profile_catalog_owner": (
            profile_catalog.owner
            if isinstance(profile_catalog, ReleaseProfileCatalog)
            else "NONE"
        ),
        "load_profile_count": str(profile_material["load_profile_count"]),
        "skill_binding_count": str(profile_material["skill_binding_count"]),
        "mode_profile_count": str(profile_material["mode_profile_count"]),
        "governance_catalog_status": str(governance_material["status"]),
        "governance_catalog_path": str(governance_material["path"]),
        "governance_catalog_sha256": str(governance_material["sha256"]),
        "governance_catalog_digest": str(
            governance_material["catalog_digest"]
        ),
        "governance_catalog_owner": (
            profile_catalog.owner
            if isinstance(profile_catalog, GovernanceCatalog)
            else "NONE"
        ),
        "governance_unit_count": str(
            governance_material["governance_unit_count"]
        ),
        "governance_index_count": str(
            governance_material["governance_index_count"]
        ),
        "governance_dependency_count": str(
            governance_material["governance_dependency_count"]
        ),
        "governance_override_count": str(
            governance_material["governance_override_count"]
        ),
    }
    _write_database(database_path, metadata, blobs, catalog=profile_catalog)
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
        "profile_catalog": profile_material,
    }
    if isinstance(profile_catalog, GovernanceCatalog):
        manifest["governance_catalog"] = governance_material
    _write_json_atomic(manifest_path, manifest)
    verify_release(database_path=database_path, manifest_path=manifest_path)
    return manifest


def _write_database(
    database_path: Path,
    metadata: dict[str, str],
    blobs: list[GitBlob],
    *,
    catalog: ReleaseCatalog | None,
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
                if isinstance(catalog, ReleaseProfileCatalog):
                    connection.executemany(
                        """
                        INSERT INTO load_profile(profile_id, description)
                        VALUES (?, ?)
                        """,
                        [
                            (profile.profile_id, profile.description)
                            for profile in catalog.load_profiles
                        ],
                    )
                    connection.executemany(
                        """
                        INSERT INTO load_profile_surface(
                            profile_id, ordinal, path, required
                        ) VALUES (?, ?, ?, ?)
                        """,
                        [
                            (
                                profile.profile_id,
                                ordinal,
                                surface.path,
                                int(surface.required),
                            )
                            for profile in catalog.load_profiles
                            for ordinal, surface in enumerate(profile.surfaces)
                        ],
                    )
                    connection.executemany(
                        """
                        INSERT INTO skill_profile_binding(skill_id, profile_id)
                        VALUES (?, ?)
                        """,
                        [
                            (binding.skill_id, binding.profile_id)
                            for binding in catalog.skill_bindings
                        ],
                    )
                    connection.executemany(
                        """
                        INSERT INTO mode_profile(mode_profile_id, overlay_policy)
                        VALUES (?, ?)
                        """,
                        [
                            (profile.mode_profile_id, profile.overlay_policy)
                            for profile in catalog.mode_profiles
                        ],
                    )
                    connection.executemany(
                        """
                        INSERT INTO mode_profile_load(
                            mode_profile_id, ordinal, profile_id
                        ) VALUES (?, ?, ?)
                        """,
                        [
                            (
                                profile.mode_profile_id,
                                ordinal,
                                load_profile_id,
                            )
                            for profile in catalog.mode_profiles
                            for ordinal, load_profile_id in enumerate(
                                profile.load_profiles
                            )
                        ],
                    )
                if isinstance(catalog, GovernanceCatalog):
                    connection.executemany(
                        """
                        INSERT INTO governance_unit(
                            governance_id, kind, source_ref, source_digest,
                            compact_instruction, release_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                unit.governance_id,
                                unit.kind,
                                unit.source_ref,
                                unit.source_digest,
                                unit.compact_instruction,
                                metadata["release_id"],
                            )
                            for unit in catalog.governance_units
                        ],
                    )
                    connection.executemany(
                        """
                        INSERT INTO governance_index(
                            role, mode, operation, scope, risk, capability,
                            governance_id, required, priority
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            (
                                entry.selector.role,
                                entry.selector.mode,
                                entry.selector.operation,
                                entry.selector.scope,
                                entry.selector.risk,
                                entry.selector.capability,
                                entry.governance_id,
                                int(entry.required),
                                entry.priority,
                            )
                            for entry in catalog.governance_index
                        ],
                    )
                    connection.executemany(
                        """
                        INSERT INTO governance_dependency(
                            governance_id, requires_governance_id
                        ) VALUES (?, ?)
                        """,
                        [
                            (
                                dependency.governance_id,
                                dependency.requires_governance_id,
                            )
                            for dependency in catalog.governance_dependencies
                        ],
                    )
                    connection.executemany(
                        """
                        INSERT INTO governance_override(
                            base_governance_id, overriding_governance_id,
                            applies_when_json
                        ) VALUES (?, ?, ?)
                        """,
                        [
                            (
                                override.base_governance_id,
                                override.overriding_governance_id,
                                canonical_bytes(
                                    override.applies_when.as_dict()
                                ).decode("ascii"),
                            )
                            for override in catalog.governance_overrides
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
    if not isinstance(manifest, dict) or manifest.get("schema") not in {
        MANIFEST_SCHEMA,
        LEGACY_MANIFEST_SCHEMA,
    }:
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
        metadata = dict(connection.execute("SELECT key, value FROM release_metadata"))
        schema = metadata.get("schema")
        legacy_v2_objects = [
            ("table", "load_profile"),
            ("table", "load_profile_surface"),
            ("table", "mode_profile"),
            ("table", "mode_profile_load"),
            ("table", "release_file"),
            ("table", "release_metadata"),
            ("table", "skill_profile_binding"),
        ]
        current_v2_objects = [
            ("table", "governance_dependency"),
            ("table", "governance_index"),
            ("table", "governance_override"),
            ("table", "governance_unit"),
            *legacy_v2_objects,
        ]
        expected_objects = (
            {tuple(legacy_v2_objects), tuple(current_v2_objects)}
            if schema == RELEASE_SCHEMA
            else {
                (
                    ("table", "release_file"),
                    ("table", "release_metadata"),
                )
            }
        )
        objects = connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table', 'view', 'trigger')
            ORDER BY type, name
            """
        ).fetchall()
        if tuple(objects) not in expected_objects:
            raise CoreReleaseError("release database contains unexpected objects")
        if schema == RELEASE_SCHEMA:
            foreign_key_issues = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_issues:
                raise CoreReleaseError(
                    "release profile catalog violates foreign key constraints"
                )
        rows = connection.execute(
            """
            SELECT path, git_mode, size, sha256, content
            FROM release_file
            ORDER BY path
            """
        ).fetchall()
        profile_rows = (
            _read_profile_rows(connection)
            if schema == RELEASE_SCHEMA
            else None
        )
        governance_rows = (
            _read_governance_rows(connection)
            if schema == RELEASE_SCHEMA
            and tuple(objects) == tuple(current_v2_objects)
            else None
        )
    finally:
        connection.close()

    if metadata.get("schema") not in {RELEASE_SCHEMA, LEGACY_RELEASE_SCHEMA}:
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

    profile_material = None
    governance_material = None
    if metadata["schema"] == RELEASE_SCHEMA:
        profile_material = _verified_profile_material(
            metadata=metadata,
            rows=profile_rows,
            governance_rows=governance_rows,
            files={item["path"]: item["sha256"] for item in files},
        )
        governance_material = _verified_governance_material(
            metadata=metadata,
            profile_rows=profile_rows,
            rows=governance_rows,
            files={item["path"]: item["sha256"] for item in files},
        )
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
        schema=metadata["schema"],
        profile_catalog=profile_material,
        governance_catalog=(
            governance_material
            if governance_material is not None
            and governance_material["status"] == "PRESENT"
            else None
        ),
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
    if profile_material is not None:
        expected_manifest["profile_catalog"] = profile_material
    if governance_material is not None and governance_material["status"] == "PRESENT":
        expected_manifest["governance_catalog"] = governance_material
    for field, value in expected_manifest.items():
        if manifest.get(field) != value:
            raise CoreReleaseError(f"release manifest field mismatch: {field}")
    if manifest.get("database") != database_path.name:
        raise CoreReleaseError("release manifest database name does not match")
    result = {
        "status": "CORE_RELEASE_VERIFIED",
        "release_id": metadata["release_id"],
        "source_commit": metadata["source_commit"],
        "file_count": len(files),
        "payload_bytes": payload_bytes,
        "payload_sha256": payload_sha256,
        "database_sha256": actual_database_sha,
        "candidate_execution": metadata["candidate_execution"],
        "profile_catalog": (
            profile_material
            if profile_material is not None
            else {
                "status": "LEGACY",
                "path": "UNKNOWN",
                "sha256": "UNKNOWN",
                "catalog_digest": "UNKNOWN",
                "load_profile_count": 0,
                "skill_binding_count": 0,
                "mode_profile_count": 0,
            }
        ),
    }
    if governance_material is not None and governance_material["status"] == "PRESENT":
        result["governance_catalog"] = governance_material
    return result


def _read_profile_rows(connection: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    connection.row_factory = sqlite3.Row
    return {
        "profiles": connection.execute(
            "SELECT profile_id, description FROM load_profile ORDER BY profile_id"
        ).fetchall(),
        "surfaces": connection.execute(
            """
            SELECT profile_id, ordinal, path, required
            FROM load_profile_surface
            ORDER BY profile_id, ordinal
            """
        ).fetchall(),
        "skills": connection.execute(
            """
            SELECT skill_id, profile_id
            FROM skill_profile_binding
            ORDER BY skill_id
            """
        ).fetchall(),
        "modes": connection.execute(
            """
            SELECT mode_profile_id, overlay_policy
            FROM mode_profile
            ORDER BY mode_profile_id
            """
        ).fetchall(),
        "mode_loads": connection.execute(
            """
            SELECT mode_profile_id, ordinal, profile_id
            FROM mode_profile_load
            ORDER BY mode_profile_id, ordinal
            """
        ).fetchall(),
    }


def _read_governance_rows(
    connection: sqlite3.Connection,
) -> dict[str, list[sqlite3.Row]]:
    connection.row_factory = sqlite3.Row
    return {
        "units": connection.execute(
            """
            SELECT governance_id, kind, source_ref, source_digest,
                   compact_instruction
            FROM governance_unit
            ORDER BY governance_id
            """
        ).fetchall(),
        "index": connection.execute(
            """
            SELECT role, mode, operation, scope, risk, capability,
                   governance_id, required, priority
            FROM governance_index
            ORDER BY role, mode, operation, scope, risk, capability,
                     priority, governance_id
            """
        ).fetchall(),
        "dependencies": connection.execute(
            """
            SELECT governance_id, requires_governance_id
            FROM governance_dependency
            ORDER BY governance_id, requires_governance_id
            """
        ).fetchall(),
        "overrides": connection.execute(
            """
            SELECT base_governance_id, overriding_governance_id,
                   applies_when_json
            FROM governance_override
            ORDER BY base_governance_id, overriding_governance_id,
                     applies_when_json
            """
        ).fetchall(),
    }


def _profile_catalog_raw(
    *,
    metadata: dict[str, str],
    rows: dict[str, list[sqlite3.Row]],
) -> dict[str, Any]:
    surfaces_by_profile: dict[str, list[dict[str, Any]]] = {}
    for row in rows["surfaces"]:
        surfaces_by_profile.setdefault(row["profile_id"], []).append(
            {"path": row["path"], "required": bool(row["required"])}
        )
    loads_by_mode: dict[str, list[str]] = {}
    for row in rows["mode_loads"]:
        loads_by_mode.setdefault(row["mode_profile_id"], []).append(
            row["profile_id"]
        )
    return {
        "schema": "ai-career.release-profile-catalog.v1",
        "owner": metadata.get("profile_catalog_owner", ""),
        "load_profiles": [
            {
                "profile_id": row["profile_id"],
                "description": row["description"],
                "surfaces": surfaces_by_profile.get(row["profile_id"], []),
            }
            for row in rows["profiles"]
        ],
        "skill_bindings": [
            {"skill_id": row["skill_id"], "profile_id": row["profile_id"]}
            for row in rows["skills"]
        ],
        "mode_profiles": [
            {
                "mode_profile_id": row["mode_profile_id"],
                "overlay_policy": row["overlay_policy"],
                "load_profiles": loads_by_mode.get(row["mode_profile_id"], []),
            }
            for row in rows["modes"]
        ],
    }


def _governance_catalog_raw(
    *,
    metadata: dict[str, str],
    profile_rows: dict[str, list[sqlite3.Row]] | None,
    governance_rows: dict[str, list[sqlite3.Row]] | None,
) -> dict[str, Any]:
    if profile_rows is None or governance_rows is None:
        raise CoreReleaseError(
            "v2 governance catalog requires profile and governance tables"
        )
    raw = _profile_catalog_raw(metadata=metadata, rows=profile_rows)
    overrides: list[dict[str, Any]] = []
    for row in governance_rows["overrides"]:
        try:
            applies_when = json.loads(row["applies_when_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise CoreReleaseError(
                "governance override selector is invalid"
            ) from error
        overrides.append(
            {
                "base_governance_id": row["base_governance_id"],
                "overriding_governance_id": row["overriding_governance_id"],
                "applies_when": applies_when,
            }
        )
    raw.update(
        {
            "schema": "ai-career.release-profile-catalog.v2",
            "governance_units": [
                {
                    "governance_id": row["governance_id"],
                    "kind": row["kind"],
                    "source_ref": row["source_ref"],
                    "source_digest": row["source_digest"],
                    "compact_instruction": row["compact_instruction"],
                }
                for row in governance_rows["units"]
            ],
            "governance_index": [
                {
                    "role": row["role"],
                    "mode": row["mode"],
                    "operation": row["operation"],
                    "scope": row["scope"],
                    "risk": row["risk"],
                    "capability": row["capability"],
                    "governance_id": row["governance_id"],
                    "required": bool(row["required"]),
                    "priority": row["priority"],
                }
                for row in governance_rows["index"]
            ],
            "governance_dependencies": [
                {
                    "governance_id": row["governance_id"],
                    "requires_governance_id": row["requires_governance_id"],
                }
                for row in governance_rows["dependencies"]
            ],
            "governance_overrides": overrides,
        }
    )
    return raw


def _verified_governance_material(
    *,
    metadata: dict[str, str],
    profile_rows: dict[str, list[sqlite3.Row]] | None,
    rows: dict[str, list[sqlite3.Row]] | None,
    files: dict[str, str],
) -> dict[str, Any]:
    status = metadata.get("governance_catalog_status", "ABSENT")
    absent = {
        "status": "ABSENT",
        "path": "NONE",
        "sha256": "NONE",
        "catalog_digest": "NONE",
        "governance_unit_count": 0,
        "governance_index_count": 0,
        "governance_dependency_count": 0,
        "governance_override_count": 0,
    }
    if rows is None:
        if status != "ABSENT":
            raise CoreReleaseError(
                "governance catalog is present without governance tables"
            )
        return absent
    if status == "ABSENT":
        if any(rows.values()):
            raise CoreReleaseError(
                "absent governance catalog has stored rows"
            )
        return absent
    if status != "PRESENT":
        raise CoreReleaseError("governance catalog status is invalid")

    raw = _governance_catalog_raw(
        metadata=metadata,
        profile_rows=profile_rows,
        governance_rows=rows,
    )
    try:
        catalog = parse_release_governance_catalog(
            raw,
            packaged_paths=files,
        )
    except ReleaseProfileError as error:
        raise CoreReleaseError(str(error)) from error
    for unit in catalog.governance_units:
        if files.get(unit.source_ref) != unit.source_digest:
            raise CoreReleaseError(
                "governance source digest is inconsistent: "
                + unit.source_ref
            )
    expected_counts = {
        "governance_unit_count": len(catalog.governance_units),
        "governance_index_count": len(catalog.governance_index),
        "governance_dependency_count": len(catalog.governance_dependencies),
        "governance_override_count": len(catalog.governance_overrides),
    }
    for key, value in expected_counts.items():
        if metadata.get(key) != str(value):
            raise CoreReleaseError(f"governance count mismatch: {key}")
    if metadata.get("governance_catalog_digest") != catalog.digest:
        raise CoreReleaseError("governance catalog digest is inconsistent")
    catalog_path = metadata.get("governance_catalog_path", "")
    if catalog_path not in files:
        raise CoreReleaseError("governance catalog path is not packaged")
    if metadata.get("governance_catalog_sha256") != files[catalog_path]:
        raise CoreReleaseError(
            "governance catalog file digest is inconsistent"
        )
    return {
        "status": "PRESENT",
        "path": catalog_path,
        "sha256": metadata.get("governance_catalog_sha256"),
        "catalog_digest": catalog.digest,
        **expected_counts,
    }


def _verified_profile_material(
    *,
    metadata: dict[str, str],
    rows: dict[str, list[sqlite3.Row]] | None,
    governance_rows: dict[str, list[sqlite3.Row]] | None,
    files: dict[str, str],
) -> dict[str, Any]:
    if rows is None:
        raise CoreReleaseError("release profile catalog tables are unavailable")
    catalog_status = metadata.get("profile_catalog_status")
    if catalog_status == "ABSENT":
        if any(rows.values()):
            raise CoreReleaseError("absent release profile catalog has stored rows")
        return {
            "status": "ABSENT",
            "path": "NONE",
            "sha256": "NONE",
            "catalog_digest": "NONE",
            "load_profile_count": 0,
            "skill_binding_count": 0,
            "mode_profile_count": 0,
        }
    if catalog_status != "PRESENT":
        raise CoreReleaseError("release profile catalog status is invalid")

    raw = (
        _governance_catalog_raw(
            metadata=metadata,
            profile_rows=rows,
            governance_rows=governance_rows,
        )
        if metadata.get("governance_catalog_status") == "PRESENT"
        else _profile_catalog_raw(metadata=metadata, rows=rows)
    )
    try:
        catalog = parse_release_catalog(raw, packaged_paths=files)
    except ReleaseProfileError as error:
        raise CoreReleaseError(str(error)) from error
    expected_counts = {
        "load_profile_count": len(catalog.load_profiles),
        "skill_binding_count": len(catalog.skill_bindings),
        "mode_profile_count": len(catalog.mode_profiles),
    }
    for key, value in expected_counts.items():
        if metadata.get(key) != str(value):
            raise CoreReleaseError(f"release profile count mismatch: {key}")
    if metadata.get("profile_catalog_digest") != catalog.digest:
        raise CoreReleaseError("release profile catalog digest is inconsistent")
    catalog_path = metadata.get("profile_catalog_path", "")
    if catalog_path not in files:
        raise CoreReleaseError("release profile catalog path is not packaged")
    if metadata.get("profile_catalog_sha256") != files[catalog_path]:
        raise CoreReleaseError("release profile catalog file digest is inconsistent")
    return {
        "status": "PRESENT",
        "path": catalog_path,
        "sha256": metadata.get("profile_catalog_sha256"),
        "catalog_digest": catalog.digest,
        **expected_counts,
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
