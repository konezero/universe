"""Receipt-aware, bounded Git mutation gateway for one repository root."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .execution_guard_runtime import ExecutionGuardError, ExecutionGuardRuntime


_BRANCH_REF = re.compile(r"refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]*$")
LOCAL_GIT_TIMEOUT_SECONDS = 30
REMOTE_GIT_TIMEOUT_SECONDS = 120
GIT_MUTATION_TIMEOUT_SECONDS = 300


class GitCommandGateway:
    """Run a small Git mutation allow-list after consuming a matching receipt."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve(strict=True)

    @staticmethod
    def command_payload_sha256(command_argv: Sequence[str]) -> str:
        """Return the canonical command payload digest required by the Guard."""

        return hashlib.sha256(
            json.dumps(list(command_argv), ensure_ascii=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    @classmethod
    def validate_command(cls, command_argv: Any, *, repository_root: Path) -> list[str] | None:
        """Accept only non-interactive, shell-free bounded Git commands."""

        if not isinstance(command_argv, Sequence) or isinstance(command_argv, (str, bytes)):
            return None
        argv = list(command_argv)
        if not argv or any(not isinstance(item, str) or not item or "\x00" in item for item in argv):
            return None
        if len(argv) < 2 or argv[0] != "git":
            return None

        command = argv[1]
        if command == "add":
            if len(argv) < 4 or argv[2] != "--":
                return None
            if not all(cls._safe_relative_path(path) for path in argv[3:]):
                return None
            return argv

        if command == "commit":
            if len(argv) != 4 or argv[2] != "-m" or not argv[3].strip():
                return None
            return argv

        if command == "push":
            if len(argv) != 4 or argv[2] != "origin":
                return None
            refspec = argv[3]
            if not refspec.startswith("HEAD:") or not cls._safe_branch_ref(refspec[5:]):
                return None
            current_branch = cls._current_branch(repository_root)
            if current_branch is None or refspec[5:] != f"refs/heads/{current_branch}":
                return None
            return argv

        return None

    def apply(
        self,
        *,
        guard: ExecutionGuardRuntime,
        snapshot: Mapping[str, Any],
        payload: Mapping[str, Any],
        task_frame_lineage_verification: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = payload.get("request")
        if not isinstance(request, Mapping) or str(request.get("operation", "")).upper() != "COMMAND":
            return _blocked("GIT_COMMAND_OPERATION_REQUIRED")
        if not self._matches_repository_root(request.get("target")):
            return _blocked("GIT_COMMAND_TARGET_MISMATCH")
        argv = self.validate_command(
            request.get("command_argv"), repository_root=self.repository_root
        )
        if argv is None:
            return _blocked("GIT_COMMAND_NOT_ALLOWLISTED")
        if str(request.get("payload_sha256", "")) != self.command_payload_sha256(argv):
            return _blocked("GIT_COMMAND_PAYLOAD_MISMATCH")
        if not self._is_git_work_tree():
            return _blocked("GIT_REPOSITORY_REQUIRED")

        try:
            consumed = guard.consume(
                receipt_id=str(payload.get("receipt_id", "")),
                snapshot=snapshot,
                request=request,
                observed_at=str(payload.get("observed_at", "")),
                task_frame_lineage_verification=task_frame_lineage_verification,
            )
        except ExecutionGuardError as error:
            return _blocked(error.error_code, detail=error.detail)
        if consumed.get("status") != "PERMIT_RECEIPT_CONSUMED":
            return _blocked("PERMIT_RECEIPT_REJECTED", receipt_result=consumed)

        environment = dict(os.environ)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        execution_argv = argv
        hardening: dict[str, Any] = {
            "shell": False,
            "terminal_prompt": False,
        }
        try:
            if argv[1] == "commit":
                with tempfile.TemporaryDirectory(prefix="ai-career-git-hooks-") as hooks:
                    execution_argv = [
                        "git",
                        "-c",
                        f"core.hooksPath={hooks}",
                        "-c",
                        "commit.gpgSign=false",
                        *argv[1:],
                    ]
                    hardening.update(
                        {
                            "repository_hooks": "DISABLED",
                            "commit_signing": "DISABLED",
                        }
                    )
                    result = self._run(
                        execution_argv,
                        environment=environment,
                    )
            else:
                result = self._run(execution_argv, environment=environment)
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "status": "GIT_COMMAND_FAILED",
                "decision": "BLOCKED",
                "repository_write": False,
                "reasons": ["HOST_GIT_COMMAND_FAILED"],
                "detail": str(error),
                "receipt_id": consumed["receipt_id"],
            }

        return {
            "status": "GIT_COMMAND_APPLIED" if result.returncode == 0 else "GIT_COMMAND_FAILED",
            "decision": "APPLIED" if result.returncode == 0 else "BLOCKED",
            "repository_write": result.returncode == 0,
            "command_argv": argv,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "receipt_id": consumed["receipt_id"],
            "execution_hardening": hardening,
        }

    def _run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=self.repository_root,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_MUTATION_TIMEOUT_SECONDS,
            env=dict(environment),
        )

    def _matches_repository_root(self, target: Any) -> bool:
        if not isinstance(target, str) or not target.strip():
            return False
        try:
            return Path(target).resolve(strict=True) == self.repository_root
        except OSError:
            return False

    def _is_git_work_tree(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.repository_root,
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

    @staticmethod
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

    @staticmethod
    def _safe_relative_path(value: str) -> bool:
        path = Path(value)
        return (
            bool(value)
            and not value.startswith(":")
            and not path.is_absolute()
            and all(part not in {"", ".", ".."} for part in path.parts)
            and (not path.parts or path.parts[0].casefold() != ".git")
        )

    @staticmethod
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


def _blocked(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "GIT_COMMAND_BLOCKED",
        "decision": "BLOCKED",
        "repository_write": False,
        "reasons": [reason],
        **extra,
    }
