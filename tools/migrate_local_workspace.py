"""Safely migrate a tracked local AI Workspace to local-only state."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "universe.local-workspace-backup.v1"
MANIFEST_NAME = "workspace-backup.json"


class WorkspaceMigrationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )


def tracked_workspace_paths(project_root: Path) -> list[str]:
    result = _git(project_root, "ls-files", "-z", "--", ".ai")
    return sorted(item for item in result.stdout.split("\0") if item)


def _workspace_files(workspace: Path) -> list[Path]:
    if not workspace.is_dir() or workspace.is_symlink():
        raise WorkspaceMigrationError("LOCAL_WORKSPACE_NOT_FOUND")
    files: list[Path] = []
    for path in workspace.rglob("*"):
        if path.is_symlink():
            raise WorkspaceMigrationError(
                f"LOCAL_WORKSPACE_LINK_UNSUPPORTED:{path.relative_to(workspace)}"
            )
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def backup_workspace(project_root: Path, backup_dir: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    workspace = root / ".ai"
    destination = backup_dir.expanduser().resolve()
    if destination == root or root in destination.parents:
        raise WorkspaceMigrationError("BACKUP_MUST_BE_OUTSIDE_PROJECT")
    if destination.exists() and any(destination.iterdir()):
        raise WorkspaceMigrationError("BACKUP_DESTINATION_NOT_EMPTY")
    destination.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for source in _workspace_files(workspace):
        relative = source.relative_to(workspace)
        target = destination / "workspace" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        digest = _sha256(source)
        if _sha256(target) != digest:
            raise WorkspaceMigrationError(f"BACKUP_DIGEST_MISMATCH:{relative.as_posix()}")
        entries.append(
            {
                "path": relative.as_posix(),
                "sha256": digest,
                "size": source.stat().st_size,
            }
        )

    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project_root": str(root),
        "source_head": head,
        "tracked_paths": tracked_workspace_paths(root),
        "files": entries,
    }
    (destination / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def untrack_workspace(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    workspace = root / ".ai"
    files_before = {
        path.relative_to(workspace).as_posix(): _sha256(path)
        for path in _workspace_files(workspace)
    }
    ignored = _git(root, "check-ignore", "-q", "--", ".ai/probe", check=False)
    if ignored.returncode != 0:
        raise WorkspaceMigrationError("LOCAL_WORKSPACE_IGNORE_RULE_MISSING")
    tracked_before = tracked_workspace_paths(root)
    if tracked_before:
        _git(root, "rm", "-r", "--cached", "--ignore-unmatch", "--", ".ai")
    if tracked_workspace_paths(root):
        raise WorkspaceMigrationError("LOCAL_WORKSPACE_INDEX_REMOVAL_FAILED")
    files_after = {
        path.relative_to(workspace).as_posix(): _sha256(path)
        for path in _workspace_files(workspace)
    }
    if files_after != files_before:
        raise WorkspaceMigrationError("LOCAL_WORKSPACE_CONTENT_CHANGED")
    return {
        "status": (
            "LOCAL_WORKSPACE_UNTRACKED"
            if tracked_before
            else "LOCAL_WORKSPACE_ALREADY_UNTRACKED"
        ),
        "tracked_paths_removed": tracked_before,
        "file_count": len(files_after),
    }


def migrate_workspace(
    project_root: Path,
    backup_dir: Path,
    *,
    quiescence_evidence_ref: str,
) -> dict[str, Any]:
    evidence_ref = str(quiescence_evidence_ref or "").strip()
    if not evidence_ref or evidence_ref.upper() == "UNKNOWN":
        raise WorkspaceMigrationError("LOCAL_WORKSPACE_QUIESCENCE_EVIDENCE_REQUIRED")
    backup = backup_workspace(project_root, backup_dir)
    migration = untrack_workspace(project_root)
    return {
        **migration,
        "backup_manifest": str(backup_dir.expanduser().resolve() / MANIFEST_NAME),
        "backup_file_count": len(backup["files"]),
        "quiescence_evidence_ref": evidence_ref,
    }


def restore_workspace(
    project_root: Path,
    backup_dir: Path,
    *,
    allow_relocated: bool = False,
) -> dict[str, Any]:
    root = project_root.expanduser().resolve(strict=True)
    backup_root = backup_dir.expanduser().resolve(strict=True)
    manifest_path = backup_root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceMigrationError("BACKUP_MANIFEST_INVALID") from error
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(
        manifest.get("files"), list
    ):
        raise WorkspaceMigrationError("BACKUP_MANIFEST_INVALID")
    manifest_project_root = manifest.get("project_root")
    if not isinstance(manifest_project_root, str) or not manifest_project_root.strip():
        raise WorkspaceMigrationError("BACKUP_MANIFEST_INVALID")
    if not allow_relocated:
        expected_root = Path(manifest_project_root).expanduser().resolve()
        if expected_root != root:
            raise WorkspaceMigrationError("BACKUP_PROJECT_ROOT_MISMATCH")

    workspace = root / ".ai"
    pending: list[tuple[Path, Path, str]] = []
    conflicts: list[str] = []
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise WorkspaceMigrationError("BACKUP_MANIFEST_INVALID")
        relative = Path(str(entry.get("path", "")))
        digest = str(entry.get("sha256", ""))
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not digest
            or len(digest) != 64
        ):
            raise WorkspaceMigrationError("BACKUP_MANIFEST_INVALID")
        source = backup_root / "workspace" / relative
        target = workspace / relative
        if not source.is_file() or _sha256(source) != digest:
            raise WorkspaceMigrationError(
                f"BACKUP_CONTENT_INVALID:{relative.as_posix()}"
            )
        if target.exists():
            if not target.is_file() or _sha256(target) != digest:
                conflicts.append(relative.as_posix())
            continue
        pending.append((source, target, digest))
    if conflicts:
        raise WorkspaceMigrationError(
            "LOCAL_WORKSPACE_RESTORE_CONFLICT:" + ",".join(sorted(conflicts))
        )
    for source, target, digest in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if _sha256(target) != digest:
            raise WorkspaceMigrationError(
                f"LOCAL_WORKSPACE_RESTORE_FAILED:{target.relative_to(workspace)}"
            )
    return {
        "status": "LOCAL_WORKSPACE_RESTORED",
        "restored_file_count": len(pending),
        "unchanged_file_count": len(manifest["files"]) - len(pending),
    }


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    subcommands = command.add_subparsers(dest="command", required=True)
    for name in ("backup", "migrate", "restore"):
        item = subcommands.add_parser(name)
        item.add_argument("--project-root", type=Path, required=True)
        item.add_argument("--backup-dir", type=Path, required=True)
        if name == "migrate":
            item.add_argument("--quiescence-evidence-ref", required=True)
        if name == "restore":
            item.add_argument("--allow-relocated", action="store_true")
    status = subcommands.add_parser("status")
    status.add_argument("--project-root", type=Path, required=True)
    return command


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "backup":
            result = backup_workspace(args.project_root, args.backup_dir)
        elif args.command == "migrate":
            result = migrate_workspace(
                args.project_root,
                args.backup_dir,
                quiescence_evidence_ref=args.quiescence_evidence_ref,
            )
        elif args.command == "restore":
            result = restore_workspace(
                args.project_root,
                args.backup_dir,
                allow_relocated=args.allow_relocated,
            )
        else:
            root = args.project_root.expanduser().resolve(strict=True)
            result = {
                "status": "LOCAL_WORKSPACE_STATUS",
                "workspace_present": (root / ".ai").is_dir(),
                "tracked_paths": tracked_workspace_paths(root),
            }
    except WorkspaceMigrationError as error:
        print(json.dumps({"status": "ERROR", "error_code": str(error)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
