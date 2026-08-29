#!/usr/bin/env python3
"""Install and run project-owned Git events for incremental file indexing."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from universe_file_index import (
    FileIndexError,
    project_index_path,
    resolve_mode_current_anchor,
    sync_project_index_from_hook,
)


HOOK_EVENTS = ("post-commit", "post-checkout", "post-merge", "post-rewrite")
HOOK_MARKER = "universe-project-index-git-hook-v1"
RESULT_SCHEMA = "universe.project-index-git-hook.result.v1"


def _git(project_root: Path, *args: str) -> bytes:
    executable = shutil.which("git")
    if not executable:
        raise FileIndexError("GIT_UNAVAILABLE", "git executable is unavailable")
    completed = subprocess.run(
        [executable, *args],
        cwd=project_root,
        shell=False,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FileIndexError("GIT_INDEX_DIFF_FAILED", detail or "git diff failed")
    return completed.stdout


def _paths(value: bytes) -> list[str]:
    return sorted(
        {
            item.decode("utf-8", errors="surrogateescape")
            for item in value.split(b"\0")
            if item
        }
    )


def changed_paths_for_event(
    *,
    project_root: Path,
    event: str,
    hook_args: Iterable[str] = (),
    rewrite_input: str = "",
) -> list[str]:
    normalized = event.strip().lower()
    args = list(hook_args)
    if normalized == "post-commit":
        return _paths(
            _git(
                project_root,
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                "HEAD",
            )
        )
    if normalized == "post-checkout":
        if len(args) < 3 or args[2] != "1":
            return []
        return _paths(
            _git(project_root, "diff", "--name-only", "-z", args[0], args[1])
        )
    if normalized == "post-merge":
        return _paths(
            _git(project_root, "diff", "--name-only", "-z", "ORIG_HEAD", "HEAD")
        )
    if normalized == "post-rewrite":
        changed: set[str] = set()
        for line in rewrite_input.splitlines():
            pair = line.split()
            if len(pair) < 2:
                continue
            changed.update(
                _paths(
                    _git(
                        project_root,
                        "diff",
                        "--name-only",
                        "-z",
                        pair[0],
                        pair[1],
                    )
                )
            )
        return sorted(changed)
    raise FileIndexError("GIT_INDEX_EVENT_INVALID", f"unsupported Git hook: {event}")


def run_git_event(
    *,
    project_id: str,
    project_root: Path,
    mode: str,
    event: str,
    hook_args: Iterable[str] = (),
    rewrite_input: str = "",
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    anchor = resolve_mode_current_anchor(root, preferred_mode=mode)
    index_missing = not project_index_path(root).is_file()
    changed_paths = changed_paths_for_event(
        project_root=root,
        event=event,
        hook_args=hook_args,
        rewrite_input=rewrite_input,
    )
    if not index_missing and not changed_paths:
        return {
            "schema": RESULT_SCHEMA,
            "status": "PROJECT_INDEX_GIT_EVENT_NO_CHANGES",
            "event": event,
            "project_id": project_id,
        }
    indexed = sync_project_index_from_hook(
        project_id=project_id,
        project_root=root,
        mode=anchor["mode"],
        anchor_id=anchor["anchor_id"],
        changed_paths=None if index_missing else changed_paths,
    )
    return {
        "schema": RESULT_SCHEMA,
        "status": "PROJECT_INDEX_GIT_EVENT_SYNCED",
        "event": event,
        "project_id": project_id,
        "bootstrap": index_missing,
        "changed_paths": changed_paths,
        "project_index": indexed,
    }


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def hook_body(
    *,
    python_exe: Path,
    script_path: Path,
    project_id: str,
    project_root: Path,
    mode: str,
    event: str,
) -> str:
    command = " ".join(
        [
            _shell_quote(python_exe.resolve().as_posix()),
            _shell_quote(script_path.resolve().as_posix()),
            "run",
            "--event",
            _shell_quote(event),
            "--project-id",
            _shell_quote(project_id),
            "--project-root",
            _shell_quote(project_root.resolve().as_posix()),
            "--mode",
            _shell_quote(mode.strip().upper()),
            '"$@"',
        ]
    )
    return (
        "#!/bin/sh\n"
        f"# {HOOK_MARKER}\n"
        f"{command} >/dev/null\n"
        "status=$?\n"
        "if [ $status -ne 0 ]; then\n"
        "  echo \"Universe project index hook failed; Git operation remains complete.\" >&2\n"
        "fi\n"
        "exit 0\n"
    )


def install_git_hooks(
    *,
    project_id: str,
    project_root: Path,
    mode: str = "MASTER",
    python_exe: Path | None = None,
    script_path: Path | None = None,
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    git_dir = root / ".git"
    if not git_dir.is_dir():
        raise FileIndexError("GIT_DIRECTORY_MISSING", f"Git directory is absent: {git_dir}")
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    executable = (python_exe or Path(sys.executable)).resolve(strict=True)
    script = (script_path or Path(__file__)).resolve(strict=True)
    results: list[dict[str, str]] = []
    for event in HOOK_EVENTS:
        target = hooks_dir / event
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        if existing and HOOK_MARKER not in existing:
            results.append(
                {"event": event, "status": "CONFLICT", "path": str(target)}
            )
            continue
        body = hook_body(
            python_exe=executable,
            script_path=script,
            project_id=project_id,
            project_root=root,
            mode=mode,
            event=event,
        )
        if existing == body:
            status = "CURRENT"
        else:
            target.write_text(body, encoding="utf-8", newline="\n")
            target.chmod(0o755)
            status = "UPDATED" if existing else "WRITTEN"
        results.append({"event": event, "status": status, "path": str(target)})
    return {
        "schema": RESULT_SCHEMA,
        "status": "PROJECT_INDEX_GIT_HOOKS_INSTALLED",
        "project_id": project_id,
        "project_root": str(root),
        "mode": mode.strip().upper(),
        "hooks": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    run = sub.add_parser("run")
    for item in (install, run):
        item.add_argument("--project-id", required=True)
        item.add_argument("--project-root", required=True, type=Path)
        item.add_argument("--mode", default="MASTER")
    install.add_argument("--python-exe", type=Path, default=None)
    run.add_argument("--event", required=True, choices=HOOK_EVENTS)
    run.add_argument("hook_args", nargs="*")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install_git_hooks(
                project_id=args.project_id,
                project_root=args.project_root,
                mode=args.mode,
                python_exe=args.python_exe,
            )
        else:
            result = run_git_event(
                project_id=args.project_id,
                project_root=args.project_root,
                mode=args.mode,
                event=args.event,
                hook_args=args.hook_args,
                rewrite_input=sys.stdin.read() if args.event == "post-rewrite" else "",
            )
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (FileIndexError, OSError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {
                    "schema": RESULT_SCHEMA,
                    "status": "PROJECT_INDEX_GIT_HOOK_BLOCKED",
                    "error_code": getattr(error, "error_code", "PROJECT_INDEX_GIT_HOOK_FAILED"),
                    "detail": getattr(error, "detail", str(error)),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
