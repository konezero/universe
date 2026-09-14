from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The runner uses an explicit argv sequence and disables shell execution.
import subprocess  # nosec B404
import time
from typing import Callable, Mapping


NATIVE_CLI_CONTRACT = "universe.windows-native-cli.v1"
DEFAULT_MAX_OUTPUT_CHARS = 200_000

# These coordinates identify the currently attached Universe Host.  A provider
# process launched directly by a resident adapter is a new child session; it
# must not inherit the parent's Host/terminal identity through os.environ.  A
# PTY-backed TerminalHost supplies a fresh, explicit set of these values after
# this boundary, so removing them here only affects direct native provider
# children.
INHERITED_UNIVERSE_COORDINATE_ENV = frozenset(
    {
        "UNIVERSE_SUPERVISOR_SESSION_ID",
        "UNIVERSE_SESSION_ANCHOR_REF",
        "UNIVERSE_SESSION_ANCHOR",
        "UNIVERSE_ANCHOR_REF",
        "UNIVERSE_TERMINAL_ID",
        "UNIVERSE_HOST_ID",
        "UNIVERSE_SESSION_HOST_ID",
        "UNIVERSE_MANAGED_SHELL",
        "UNIVERSE_MANAGED_SHELL_IDENTITY_FILE",
        "UNIVERSE_SESSION_INBOX_CLI",
    }
)


class NativeCliError(ValueError):
    pass


@dataclass(frozen=True)
class NativeCliRequest:
    executable: Path
    arguments: tuple[str, ...] = ()
    cwd: Path | None = None
    timeout_seconds: float = 120
    output_encoding: str = "utf-8"
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    environment: Mapping[str, str] | None = None
    stdin_path: Path | None = None


@dataclass(frozen=True)
class NativeCliResult:
    contract: str
    status: str
    return_code: int | None
    duration_ms: float
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    descendant_termination_proven: bool = False


Runner = Callable[..., subprocess.CompletedProcess[bytes]]
ProcessOpener = Callable[..., subprocess.Popen[str]]


def _validated_request(request: NativeCliRequest) -> NativeCliRequest:
    executable = request.executable.resolve()
    if not executable.is_file():
        raise NativeCliError("native CLI executable does not exist")
    if executable.suffix.lower() in {".bat", ".cmd", ".ps1"}:
        raise NativeCliError(
            "shell and batch entrypoints are not native CLI executables"
        )
    if any(not isinstance(argument, str) for argument in request.arguments):
        raise NativeCliError("native CLI arguments must be strings")
    if request.cwd is not None and not request.cwd.resolve().is_dir():
        raise NativeCliError("native CLI cwd does not exist")
    if request.timeout_seconds <= 0:
        raise NativeCliError("native CLI timeout must be positive")
    if request.max_output_chars < 1:
        raise NativeCliError("native CLI output bound must be positive")
    if request.stdin_path is not None and not request.stdin_path.resolve().is_file():
        raise NativeCliError("native CLI stdin file does not exist")
    return NativeCliRequest(
        executable=executable,
        arguments=tuple(request.arguments),
        cwd=request.cwd.resolve() if request.cwd is not None else None,
        timeout_seconds=request.timeout_seconds,
        output_encoding=request.output_encoding,
        max_output_chars=request.max_output_chars,
        environment=request.environment,
        stdin_path=(
            request.stdin_path.resolve() if request.stdin_path is not None else None
        ),
    )


def _bounded(value: str, maximum: int) -> tuple[str, bool]:
    if len(value) <= maximum:
        return value, False
    return value[:maximum], True


def _child_environment(request: NativeCliRequest) -> dict[str, str]:
    """Build an environment that cannot inherit a parent Host identity.

    Both one-shot and persistent native provider adapters create a fresh
    process.  Passing the resident terminal's ``UNIVERSE_*`` coordinates into
    either process lets a SessionStart hook register the child as the parent.
    PTY-backed children do not use this module and receive an explicit child
    coordinate from ``TerminalHost`` instead.
    """

    environment = dict(os.environ)
    if request.environment is not None:
        environment.update(request.environment)
    for key in INHERITED_UNIVERSE_COORDINATE_ENV:
        environment.pop(key, None)
    return environment


def run_native_cli(
    request: NativeCliRequest,
    *,
    runner: Runner = subprocess.run,
) -> NativeCliResult:
    normalized = _validated_request(request)
    environment = _child_environment(normalized)
    command = [str(normalized.executable), *normalized.arguments]
    stdin_handle = None
    started = time.monotonic()
    try:
        if normalized.stdin_path is not None:
            stdin_handle = normalized.stdin_path.open("rb")
        try:
            completed = runner(
                command,
                cwd=(str(normalized.cwd) if normalized.cwd is not None else None),
                env=environment,
                stdin=stdin_handle if stdin_handle is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                timeout=normalized.timeout_seconds,
                check=False,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except subprocess.TimeoutExpired as error:
            stdout_raw = error.stdout if isinstance(error.stdout, bytes) else b""
            stderr_raw = error.stderr if isinstance(error.stderr, bytes) else b""
            stdout, stdout_truncated = _bounded(
                stdout_raw.decode(normalized.output_encoding, errors="replace"),
                normalized.max_output_chars,
            )
            stderr, stderr_truncated = _bounded(
                stderr_raw.decode(normalized.output_encoding, errors="replace"),
                normalized.max_output_chars,
            )
            return NativeCliResult(
                contract=NATIVE_CLI_CONTRACT,
                status="TIMED_OUT",
                return_code=None,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                stdout=stdout,
                stderr=stderr,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        except OSError as error:
            return NativeCliResult(
                contract=NATIVE_CLI_CONTRACT,
                status="LAUNCH_FAILED",
                return_code=None,
                duration_ms=round((time.monotonic() - started) * 1000, 3),
                stdout="",
                stderr=f"{type(error).__name__}: {error}",
                stdout_truncated=False,
                stderr_truncated=False,
            )
    finally:
        if stdin_handle is not None:
            stdin_handle.close()

    stdout, stdout_truncated = _bounded(
        completed.stdout.decode(normalized.output_encoding, errors="replace"),
        normalized.max_output_chars,
    )
    stderr, stderr_truncated = _bounded(
        completed.stderr.decode(normalized.output_encoding, errors="replace"),
        normalized.max_output_chars,
    )
    return NativeCliResult(
        contract=NATIVE_CLI_CONTRACT,
        status="COMPLETED" if completed.returncode == 0 else "FAILED",
        return_code=completed.returncode,
        duration_ms=round((time.monotonic() - started) * 1000, 3),
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def open_native_cli(
    request: NativeCliRequest,
    *,
    opener: ProcessOpener = subprocess.Popen,
) -> subprocess.Popen[str]:
    normalized = _validated_request(request)
    environment = _child_environment(normalized)
    command = [str(normalized.executable), *normalized.arguments]
    return opener(  # nosec B603
        command,
        cwd=str(normalized.cwd) if normalized.cwd is not None else None,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        text=True,
        encoding=normalized.output_encoding,
        errors="replace",
        bufsize=1,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
