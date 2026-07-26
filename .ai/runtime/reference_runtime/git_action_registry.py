"""Shared bounded Git action registry for proposal, Guard, and execution."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_BRANCH_REF = re.compile(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$")
LOCAL_GIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class GitAction:
    """One exact registry-resolved Git action."""

    name: str
    command_argv: tuple[str, ...]
    execution_argvs: tuple[tuple[str, ...], ...]


def command_payload_sha256(command_argv: Sequence[str]) -> str:
    """Return the canonical command digest used by the execution receipt."""

    return hashlib.sha256(
        json.dumps(list(command_argv), ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def resolve_git_action(
    command_argv: Any, *, repository_root: Path
) -> GitAction | None:
    """Resolve one supported Git argv against repository-local context.

    `REBASE_CURRENT_BRANCH` is one explicit pull/rebase form. It cannot select
    another branch or upstream, force-push, or grant a later PUSH action. The
    normal one-time Guard receipt is still required to execute it.
    """

    argv = _argv(command_argv)
    if argv is None or len(argv) < 2 or argv[0] != "git":
        return None
    if not _is_git_work_tree(repository_root):
        return None

    command = argv[1]
    if command == "add":
        if len(argv) < 4 or argv[2] != "--":
            return None
        if not all(_safe_relative_path(path) for path in argv[3:]):
            return None
        return GitAction("ADD", tuple(argv), (tuple(argv),))

    if command == "commit":
        if len(argv) != 4 or argv[2] != "-m" or not argv[3].strip():
            return None
        return GitAction("COMMIT", tuple(argv), (tuple(argv),))

    current_branch = _current_branch(repository_root)
    if current_branch is None:
        return None

    if command == "push":
        if len(argv) != 4 or argv[2] != "origin":
            return None
        refspec = argv[3]
        if (
            not refspec.startswith("HEAD:")
            or not _safe_branch_ref(refspec[5:])
            or refspec[5:] != f"refs/heads/{current_branch}"
        ):
            return None
        return GitAction("PUSH_CURRENT_BRANCH", tuple(argv), (tuple(argv),))

    if command == "pull":
        expected = ["git", "pull", "--rebase", "origin", current_branch]
        if argv != expected:
            return None
        return GitAction(
            "REBASE_CURRENT_BRANCH",
            tuple(argv),
            (
                (
                    "git",
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"refs/heads/{current_branch}:refs/remotes/origin/{current_branch}",
                ),
                ("git", "rebase", "--no-verify", f"origin/{current_branch}"),
            ),
        )

    return None


def _argv(value: Any) -> list[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    argv = list(value)
    if not argv or any(
        not isinstance(item, str) or not item or "\x00" in item for item in argv
    ):
        return None
    return argv


def _current_branch(repository_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=repository_root,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LOCAL_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    branch = result.stdout.strip()
    return branch if result.returncode == 0 and branch else None


def _is_git_work_tree(repository_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repository_root,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LOCAL_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and not value.startswith(":")
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and (not path.parts or path.parts[0].casefold() != ".git")
    )


def _safe_branch_ref(value: str) -> bool:
    if not _BRANCH_REF.fullmatch(value):
        return False
    branch = value.removeprefix("refs/heads/")
    forbidden = ("..", "@{", "//", "~", "^", ":", "?", "*", "[", "\\", " ")
    return (
        bool(branch)
        and not branch.startswith(".")
        and not branch.endswith(".")
        and not any(token in branch for token in forbidden)
        and all(
            part and part not in {".", ".."} and not part.endswith(".lock")
            for part in branch.split("/")
        )
    )
