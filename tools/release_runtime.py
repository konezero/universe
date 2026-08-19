from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from core_release import CoreReleaseError, canonical_bytes, verify_release


INSTALL_STATE_SCHEMA = "universe.project-release-install.v1"
INSTALL_STATE_PATH = (
    ".ai/runtime/project_instance/UNIVERSE_RELEASE_INSTALL.json"
)
PLAN_SCHEMA = "universe.project-release-plan.v1"
PROFILE_CATALOG_REQUIRED = "PROFILE_CATALOG_REQUIRED"
GOVERNANCE_CONTEXT_SCHEMA = "universe.release-governance-context.v1"
GOVERNANCE_SELECTOR_FIELDS = (
    "role",
    "mode",
    "operation",
    "scope",
    "risk",
    "capability",
)
MAX_GOVERNANCE_CONTEXT_BYTES = 128 * 1024


class ReleaseRuntimeError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    git_object_id: str
    size: int
    sha256: str
    content: bytes


class ReleaseRuntime:
    """Read an immutable Release DB without executing packaged source."""

    def __init__(self, *, database_path: Path, manifest_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve(strict=True)
        self.manifest_path = manifest_path.expanduser().resolve(strict=True)
        self.verification = verify_release(
            database_path=self.database_path,
            manifest_path=self.manifest_path,
        )
        self._connection = sqlite3.connect(
            f"{self.database_path.as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only = ON")
        self.metadata = dict(
            self._connection.execute(
                "SELECT key, value FROM release_metadata"
            ).fetchall()
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ReleaseRuntime:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def release_id(self) -> str:
        return self.metadata["release_id"]

    @property
    def profile_catalog_status(self) -> str:
        return self.metadata.get("profile_catalog_status", "LEGACY")

    @property
    def governance_catalog_status(self) -> str:
        return self.metadata.get("governance_catalog_status", "ABSENT")

    def select_governance_context(self, selector: Mapping[str, Any]) -> dict[str, Any]:
        """Return only the immutable governance units selected for one role."""

        if self.governance_catalog_status != "PRESENT":
            return {
                "schema": GOVERNANCE_CONTEXT_SCHEMA,
                "status": "ABSENT",
                "release_id": self.release_id,
                "reason": "GOVERNANCE_CATALOG_ABSENT",
            }
        normalized = _governance_selector(selector)
        rows = self._connection.execute(
            """
            SELECT governance_id, required, priority
            FROM governance_index
            WHERE role = ? AND mode = ? AND operation = ? AND scope = ?
              AND risk = ? AND capability = ?
            ORDER BY priority, governance_id
            """,
            tuple(normalized[field] for field in GOVERNANCE_SELECTOR_FIELDS),
        ).fetchall()
        matched_entries = [
            {
                **normalized,
                "governance_id": str(row["governance_id"]),
                "required": bool(row["required"]),
                "priority": int(row["priority"]),
            }
            for row in rows
        ]
        selected_ids = [entry["governance_id"] for entry in matched_entries]
        selected = set(selected_ids)
        selector_json = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        for row in self._connection.execute(
            """
            SELECT base_governance_id, overriding_governance_id
            FROM governance_override
            WHERE applies_when_json = ?
            ORDER BY base_governance_id, overriding_governance_id
            """,
            (selector_json,),
        ):
            base = str(row["base_governance_id"])
            override = str(row["overriding_governance_id"])
            if base in selected:
                selected.remove(base)
                selected.add(override)
                if override not in selected_ids:
                    selected_ids.append(override)
        if not selected:
            raise ReleaseRuntimeError("governance selector has no matching units")

        dependencies = {
            str(row["governance_id"]): [
                str(item["requires_governance_id"])
                for item in self._connection.execute(
                    """SELECT requires_governance_id FROM governance_dependency
                       WHERE governance_id = ? ORDER BY requires_governance_id""",
                    (row["governance_id"],),
                )
            ]
            for row in self._connection.execute(
                "SELECT DISTINCT governance_id FROM governance_dependency"
            )
        }
        closure: list[str] = []
        visited: set[str] = set()

        def visit(governance_id: str) -> None:
            if governance_id in visited:
                return
            for dependency in dependencies.get(governance_id, []):
                visit(dependency)
            visited.add(governance_id)
            closure.append(governance_id)

        for governance_id in selected_ids:
            if governance_id in selected:
                visit(governance_id)

        units: list[dict[str, Any]] = []
        content_size = 0
        for governance_id in closure:
            row = self._connection.execute(
                """
                SELECT u.governance_id, u.kind, u.source_ref, u.source_digest,
                       u.compact_instruction, f.content
                FROM governance_unit AS u
                JOIN release_file AS f ON f.path = u.source_ref
                WHERE u.governance_id = ?
                """,
                (governance_id,),
            ).fetchone()
            if row is None:
                raise ReleaseRuntimeError("governance unit is missing from release")
            content = row["content"]
            if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != row["source_digest"]:
                raise ReleaseRuntimeError("governance unit digest does not match release")
            content_size += len(content)
            if content_size > MAX_GOVERNANCE_CONTEXT_BYTES:
                raise ReleaseRuntimeError("selected governance context exceeds size limit")
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ReleaseRuntimeError("governance unit is not UTF-8 text") from error
            units.append({
                "governance_id": str(row["governance_id"]),
                "kind": str(row["kind"]),
                "source_ref": str(row["source_ref"]),
                "source_digest": str(row["source_digest"]),
                "compact_instruction": str(row["compact_instruction"]),
                "content": text,
            })
        catalog_digest = _required_text(
            self.metadata.get("governance_catalog_digest"),
            "governance_catalog_digest",
        )
        selector_digest = _digest(
            {
                "catalog_digest": catalog_digest,
                "selector": normalized,
                "matched_entries": matched_entries,
                "dependency_closure": closure,
                "source_digests": [unit["source_digest"] for unit in units],
            }
        )
        material = {
            "schema": GOVERNANCE_CONTEXT_SCHEMA,
            "status": "SELECTED",
            "release_id": self.release_id,
            "source_commit": self.metadata["source_commit"],
            "catalog_digest": catalog_digest,
            "selector": normalized,
            "dependency_closure": closure,
            "units": units,
            "selector_digest": selector_digest,
        }
        return material

    def list_profiles(self) -> dict[str, Any]:
        self._require_profiles()
        load_profiles = [
            {
                "profile_id": row["profile_id"],
                "description": row["description"],
                "surface_count": row["surface_count"],
            }
            for row in self._connection.execute(
                """
                SELECT p.profile_id, p.description, COUNT(s.path) AS surface_count
                FROM load_profile AS p
                LEFT JOIN load_profile_surface AS s
                  ON s.profile_id = p.profile_id
                GROUP BY p.profile_id, p.description
                ORDER BY p.profile_id
                """
            )
        ]
        skills = [
            dict(row)
            for row in self._connection.execute(
                """
                SELECT skill_id, profile_id
                FROM skill_profile_binding
                ORDER BY skill_id
                """
            )
        ]
        modes = [
            self.resolve_mode_profile(row["mode_profile_id"])
            for row in self._connection.execute(
                """
                SELECT mode_profile_id
                FROM mode_profile
                ORDER BY mode_profile_id
                """
            )
        ]
        return {
            "release_id": self.release_id,
            "catalog_status": self.profile_catalog_status,
            "load_profiles": load_profiles,
            "skill_bindings": skills,
            "mode_profiles": modes,
        }

    def resolve_skill(self, skill_id: str) -> dict[str, Any]:
        self._require_profiles()
        normalized = _required_text(skill_id, "skill_id")
        row = self._connection.execute(
            """
            SELECT profile_id
            FROM skill_profile_binding
            WHERE skill_id = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            raise ReleaseRuntimeError(f"skill profile is not registered: {normalized}")
        result = self.load_profile(row["profile_id"])
        result["skill_id"] = normalized
        return result

    def load_profile(self, profile_id: str) -> dict[str, Any]:
        self._require_profiles()
        normalized = _required_text(profile_id, "profile_id").upper()
        profile = self._connection.execute(
            """
            SELECT profile_id, description
            FROM load_profile
            WHERE profile_id = ?
            """,
            (normalized,),
        ).fetchone()
        if profile is None:
            raise ReleaseRuntimeError(
                f"load profile is not registered: {normalized}"
            )
        rows = self._connection.execute(
            """
            SELECT s.ordinal, s.path, s.required, f.size, f.sha256
            FROM load_profile_surface AS s
            JOIN release_file AS f ON f.path = s.path
            WHERE s.profile_id = ?
            ORDER BY s.ordinal
            """,
            (normalized,),
        ).fetchall()
        return {
            "profile_id": profile["profile_id"],
            "description": profile["description"],
            "surfaces": [
                {
                    "ordinal": row["ordinal"],
                    "path": row["path"],
                    "required": bool(row["required"]),
                    "size": row["size"],
                    "sha256": row["sha256"],
                }
                for row in rows
            ],
        }

    def resolve_mode_profile(self, mode_profile_id: str) -> dict[str, Any]:
        self._require_profiles()
        normalized = _required_text(
            mode_profile_id,
            "mode_profile_id",
        ).upper()
        mode = self._connection.execute(
            """
            SELECT mode_profile_id, overlay_policy
            FROM mode_profile
            WHERE mode_profile_id = ?
            """,
            (normalized,),
        ).fetchone()
        if mode is None:
            raise ReleaseRuntimeError(
                f"Mode profile is not registered: {normalized}"
            )
        profiles = [
            row["profile_id"]
            for row in self._connection.execute(
                """
                SELECT profile_id
                FROM mode_profile_load
                WHERE mode_profile_id = ?
                ORDER BY ordinal
                """,
                (normalized,),
            )
        ]
        return {
            "mode_profile_id": mode["mode_profile_id"],
            "overlay_policy": mode["overlay_policy"],
            "load_profiles": profiles,
        }

    def profile_content(self, profile_id: str) -> list[tuple[str, bytes]]:
        profile = self.load_profile(profile_id)
        return [
            (surface["path"], self.release_file(surface["path"]).content)
            for surface in profile["surfaces"]
        ]

    def release_file(self, path: str) -> ReleaseFile:
        normalized = _release_path(path)
        row = self._connection.execute(
            """
            SELECT path, git_object_id, size, sha256, content
            FROM release_file
            WHERE path = ?
            """,
            (normalized,),
        ).fetchone()
        if row is None:
            raise ReleaseRuntimeError(f"release file is not present: {normalized}")
        content = row["content"]
        if not isinstance(content, bytes):
            raise ReleaseRuntimeError(f"release file content is invalid: {normalized}")
        return ReleaseFile(
            path=row["path"],
            git_object_id=row["git_object_id"],
            size=row["size"],
            sha256=row["sha256"],
            content=content,
        )

    def iter_release_files(self) -> Iterable[ReleaseFile]:
        for row in self._connection.execute(
            """
            SELECT path, git_object_id, size, sha256, content
            FROM release_file
            ORDER BY path
            """
        ):
            content = row["content"]
            if not isinstance(content, bytes):
                raise ReleaseRuntimeError(
                    f"release file content is invalid: {row['path']}"
                )
            yield ReleaseFile(
                path=row["path"],
                git_object_id=row["git_object_id"],
                size=row["size"],
                sha256=row["sha256"],
                content=content,
            )

    def materialize_source_bundle(self, bundle_root: Path) -> dict[str, Any]:
        """Rebuild the installer's canonical provider-attested source bundle."""

        root = bundle_root.expanduser().resolve()
        if root.exists():
            if not root.is_dir() or root.is_symlink() or any(root.iterdir()):
                raise ReleaseRuntimeError("source bundle target must be an empty real directory")
        else:
            root.mkdir(parents=True)
        object_root = root / "objects" / "sha256"
        object_root.mkdir(parents=True)

        rows: list[dict[str, Any]] = []
        for item in self.iter_release_files():
            object_path = object_root / item.sha256
            if object_path.exists():
                if (
                    not object_path.is_file()
                    or object_path.is_symlink()
                    or hashlib.sha256(object_path.read_bytes()).hexdigest()
                    != item.sha256
                ):
                    raise ReleaseRuntimeError("source bundle object collision")
            else:
                _replace_file(object_path, item.content)
            rows.append(
                {
                    "path": item.path,
                    "blob_oid": item.git_object_id,
                    "sha256": item.sha256,
                    "size": item.size,
                }
            )

        committed_at = _required_text(
            self.metadata.get("source_committed_at"),
            "source_committed_at",
        )
        commit_date = committed_at[:10]
        if len(commit_date) != 10:
            raise ReleaseRuntimeError("source_committed_at is invalid")
        capability_ref = (
            "universe-release-db://"
            + self.release_id
            + "@"
            + self.verification["database_sha256"]
        )
        source_commit = _required_text(
            self.metadata.get("source_commit"),
            "source_commit",
        )
        manifest = {
            "schema": "ai-career.project-runtime-source-bundle.v1",
            "source": {
                "provider": "universe-release-db",
                "repository": _required_text(
                    self.metadata.get("source_repository"),
                    "source_repository",
                ),
                "requested_ref": _required_text(
                    self.metadata.get("source_ref"),
                    "source_ref",
                ),
                "resolved_commit": source_commit,
                "commit_date": commit_date,
                "source_binding": "provider-attested",
                "capability_evidence_ref": capability_ref,
            },
            "files": rows,
        }
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        manifest_path = root / "SOURCE_BUNDLE.json"
        _replace_file(manifest_path, manifest_bytes)
        return {
            "schema": manifest["schema"],
            "provider": "universe-release-db",
            "binding": "provider-attested",
            "release_id": self.release_id,
            "source_commit": source_commit,
            "bundle_root": str(root),
            "bundle_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "file_count": len(rows),
            "capability_evidence_ref": capability_ref,
        }

    def plan_project_install(self, target_root: Path) -> dict[str, Any]:
        root = _project_root(target_root)
        previous = _load_install_state(root)
        previous_inventory = (
            previous.get("inventory", {}) if previous is not None else {}
        )
        if not isinstance(previous_inventory, dict):
            raise ReleaseRuntimeError("project release inventory is invalid")
        desired = {item.path: item for item in self.iter_release_files()}
        actions: list[dict[str, Any]] = []
        collisions: list[dict[str, str]] = []

        for path, item in desired.items():
            target = _target_path(root, path)
            actual = _file_state(target)
            prior_digest = previous_inventory.get(path)
            if actual["status"] == "ABSENT":
                action = "CREATE"
            elif actual["sha256"] == item.sha256:
                action = "NOOP"
            elif isinstance(prior_digest, str) and actual["sha256"] == prior_digest:
                action = "UPDATE"
            else:
                action = "COLLISION"
                collisions.append(
                    {
                        "path": path,
                        "actual_sha256": actual["sha256"],
                        "release_sha256": item.sha256,
                    }
                )
            actions.append(
                {
                    "action": action,
                    "path": path,
                    "size": item.size,
                    "release_sha256": item.sha256,
                    "actual": actual,
                }
            )

        for path, prior_digest in sorted(previous_inventory.items()):
            if path in desired:
                continue
            target = _target_path(root, path)
            actual = _file_state(target)
            if actual["status"] == "ABSENT":
                action = "NOOP"
            elif actual["sha256"] == prior_digest:
                action = "DELETE"
            else:
                action = "COLLISION"
                collisions.append(
                    {
                        "path": path,
                        "actual_sha256": actual["sha256"],
                        "release_sha256": "REMOVED",
                    }
                )
            actions.append(
                {
                    "action": action,
                    "path": path,
                    "size": 0,
                    "release_sha256": "REMOVED",
                    "actual": actual,
                }
            )

        material = {
            "schema": PLAN_SCHEMA,
            "operation": "UPDATE" if previous is not None else "FRESH_INSTALL",
            "target_root": str(root),
            "release_id": self.release_id,
            "source_commit": self.metadata["source_commit"],
            "previous_release_id": (
                previous.get("release_id", "NONE")
                if previous is not None
                else "NONE"
            ),
            "actions": actions,
            "collisions": collisions,
            "candidate_execution": "FORBIDDEN",
        }
        material["plan_digest"] = _digest(material)
        material["status"] = (
            "PROJECT_RELEASE_PLAN_BLOCKED"
            if collisions
            else "PROJECT_RELEASE_PLAN_READY"
        )
        return material

    def apply_project_install(
        self,
        *,
        target_root: Path,
        approved_plan_digest: str,
    ) -> dict[str, Any]:
        plan = self.plan_project_install(target_root)
        if plan["plan_digest"] != _required_text(
            approved_plan_digest,
            "approved_plan_digest",
        ):
            raise ReleaseRuntimeError("project release plan approval is stale")
        if plan["collisions"]:
            raise ReleaseRuntimeError("project release plan has unmanaged collisions")
        root = Path(plan["target_root"])
        root.mkdir(parents=True, exist_ok=True)
        desired = {item.path: item for item in self.iter_release_files()}
        changed: list[dict[str, str]] = []
        for action in plan["actions"]:
            operation = action["action"]
            path = action["path"]
            target = _target_path(root, path)
            if operation in {"CREATE", "UPDATE"}:
                _replace_file(target, desired[path].content)
                changed.append({"operation": operation, "path": path})
            elif operation == "DELETE":
                target.unlink()
                changed.append({"operation": operation, "path": path})

        state = {
            "schema": INSTALL_STATE_SCHEMA,
            "release_id": self.release_id,
            "source_commit": self.metadata["source_commit"],
            "payload_sha256": self.metadata["payload_sha256"],
            "database_sha256": self.verification["database_sha256"],
            "installed_at": _utc_now(),
            "inventory": {
                path: item.sha256 for path, item in sorted(desired.items())
            },
        }
        state_path = _target_path(root, INSTALL_STATE_PATH)
        _replace_file(
            state_path,
            (json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        return {
            "status": "PROJECT_RELEASE_APPLIED",
            "operation": plan["operation"],
            "target_root": str(root),
            "release_id": self.release_id,
            "changed": changed,
            "changed_count": len(changed),
            "state_path": INSTALL_STATE_PATH,
            "candidate_execution": "FORBIDDEN",
        }

    def _require_profiles(self) -> None:
        if self.profile_catalog_status != "PRESENT":
            raise ReleaseRuntimeError(PROFILE_CATALOG_REQUIRED)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseRuntimeError(f"{field} must be non-empty text")
    return value.strip()


def _governance_selector(value: Mapping[str, Any]) -> dict[str, str]:
    if set(value) != set(GOVERNANCE_SELECTOR_FIELDS):
        raise ReleaseRuntimeError("governance selector fields are invalid")
    return {field: _required_text(value.get(field), f"selector.{field}").upper()
            for field in GOVERNANCE_SELECTOR_FIELDS}


def _release_path(value: str) -> str:
    path = PurePosixPath(_required_text(value, "path"))
    if (
        path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ReleaseRuntimeError(f"release path is invalid: {value}")
    return value


def _project_root(value: Path) -> Path:
    root = value.expanduser().resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ReleaseRuntimeError("project target must be an existing real directory")
    return root


def _target_path(root: Path, relative: str) -> Path:
    normalized = _release_path(relative)
    target = root.joinpath(*PurePosixPath(normalized).parts)
    current = target.parent
    while not current.exists() and current != root:
        current = current.parent
    if current.exists() and current.is_symlink():
        raise ReleaseRuntimeError(f"release target crosses a symlink: {relative}")
    resolved_parent = current.resolve(strict=True) if current.exists() else root
    try:
        common = os.path.commonpath(
            [os.path.normcase(str(resolved_parent)), os.path.normcase(str(root))]
        )
    except ValueError as error:
        raise ReleaseRuntimeError("release target escapes project root") from error
    if common != os.path.normcase(str(root)):
        raise ReleaseRuntimeError("release target escapes project root")
    return target


def _file_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"status": "ABSENT", "sha256": "NONE"}
    if not path.is_file() or path.is_symlink():
        return {"status": "UNSUPPORTED", "sha256": "NONE"}
    return {
        "status": "PRESENT",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


_LEGACY_DIST_MANIFEST_PATH = (
    ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
)
_LEGACY_DIST_MANIFEST_SCHEMA = "ai-career.project-runtime-installation.v1"


def _load_install_state(root: Path) -> dict[str, Any] | None:
    path = _target_path(root, INSTALL_STATE_PATH)
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ReleaseRuntimeError("project release state is invalid") from error
        if not isinstance(state, dict) or state.get("schema") != INSTALL_STATE_SCHEMA:
            raise ReleaseRuntimeError("project release state schema is unsupported")
        return state
    # Fall back to legacy DISTRIBUTION_MANIFEST.json produced by older installs.
    # Build a synthetic inventory from managed_paths so existing managed files are
    # treated as UPDATE candidates instead of COLLISION during a runtime update.
    legacy_path = _target_path(root, _LEGACY_DIST_MANIFEST_PATH)
    if not legacy_path.exists():
        return None
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(legacy, dict) or legacy.get("schema") != _LEGACY_DIST_MANIFEST_SCHEMA:
        return None
    managed_paths = legacy.get("managed_paths")
    if not isinstance(managed_paths, list):
        return None
    inventory: dict[str, str] = {}
    for entry in managed_paths:
        if isinstance(entry, dict):
            target = entry.get("target_path")
            sha = entry.get("local_sha256")
            if isinstance(target, str) and target and isinstance(sha, str) and sha:
                inventory[target] = sha
    return {
        "schema": INSTALL_STATE_SCHEMA,
        "release_id": str(legacy.get("release_id") or "LEGACY"),
        "inventory": inventory,
    }


def _replace_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Use an immutable Universe Release DB")
    cli.add_argument("--database", type=Path, required=True)
    cli.add_argument("--manifest", type=Path, required=True)
    commands = cli.add_subparsers(dest="command", required=True)

    commands.add_parser("profiles")
    skill = commands.add_parser("resolve-skill")
    skill.add_argument("--skill", required=True)
    mode = commands.add_parser("resolve-mode")
    mode.add_argument("--mode-profile", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--target", type=Path, required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--target", type=Path, required=True)
    apply.add_argument("--approved-plan-digest", required=True)
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        with ReleaseRuntime(
            database_path=args.database,
            manifest_path=args.manifest,
        ) as runtime:
            if args.command == "profiles":
                result = runtime.list_profiles()
            elif args.command == "resolve-skill":
                result = runtime.resolve_skill(args.skill)
            elif args.command == "resolve-mode":
                result = runtime.resolve_mode_profile(args.mode_profile)
            elif args.command == "plan":
                result = runtime.plan_project_install(args.target)
            else:
                result = runtime.apply_project_install(
                    target_root=args.target,
                    approved_plan_digest=args.approved_plan_digest,
                )
    except (
        CoreReleaseError,
        ReleaseRuntimeError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as error:
        print(
            json.dumps(
                {"status": "RELEASE_RUNTIME_FAILED", "detail": str(error)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
