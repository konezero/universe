#!/usr/bin/env python3
"""Run a native executable from a structured request without a shell."""

from __future__ import annotations

import argparse
import codecs
import json
import os
from pathlib import Path
import shutil

# Native process creation is this Skill's purpose; caller authority stays external.
import subprocess  # nosec B404
import time
from typing import Any


REQUEST_SCHEMA = "windows-native-cli.request.v1"
RESULT_SCHEMA = "windows-native-cli.result.v1"
DEFAULT_MAX_OUTPUT_CHARS = 200_000


class RequestError(ValueError):
    """Raised when a native CLI request is malformed."""


def _require_string(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise RequestError(f"{field} must be a string")
    if "\x00" in value:
        raise RequestError(f"{field} must not contain NUL")
    return value


def _require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RequestError(f"{field} must be an array of strings")
    if any("\x00" in item for item in value):
        raise RequestError(f"{field} must not contain NUL")
    return list(value)


def _resolve_executable(raw: str, allow_path_lookup: bool) -> str:
    suffix = Path(raw).suffix.lower()
    if suffix in {".bat", ".cmd"}:
        raise RequestError("batch files are shell boundaries and are not supported")

    candidate = Path(raw)
    if candidate.is_absolute():
        if not candidate.is_file():
            raise RequestError("executable does not exist")
        return str(candidate)

    if not allow_path_lookup:
        raise RequestError(
            "executable must be absolute unless allow_path_lookup is true"
        )

    resolved = shutil.which(raw)
    if not resolved:
        raise RequestError("executable was not found on PATH")
    if Path(resolved).suffix.lower() in {".bat", ".cmd"}:
        raise RequestError("PATH resolved to a batch file, which is not supported")
    return resolved


def _build_environment(spec: Any) -> dict[str, str]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise RequestError("environment must be an object")

    inherit = spec.get("inherit", True)
    if not isinstance(inherit, bool):
        raise RequestError("environment.inherit must be boolean")
    environment = dict(os.environ) if inherit else {}

    set_values = spec.get("set", {})
    if not isinstance(set_values, dict):
        raise RequestError("environment.set must be an object")
    for key, value in set_values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RequestError("environment.set keys and values must be strings")
        if not key or "\x00" in key or "\x00" in value:
            raise RequestError("environment.set contains an invalid key or value")
        environment[key] = value

    for key in _require_string_list(spec.get("remove", []), "environment.remove"):
        environment.pop(key, None)
    return environment


def _open_stdin(spec: Any) -> tuple[Any, Any]:
    if spec is None:
        spec = {"kind": "NONE"}
    if not isinstance(spec, dict):
        raise RequestError("stdin must be an object")

    kind = spec.get("kind", "NONE")
    if kind == "NONE":
        return subprocess.DEVNULL, None
    if kind != "FILE":
        raise RequestError("stdin.kind must be NONE or FILE")

    path = Path(_require_string(spec.get("path"), "stdin.path"))
    if not path.is_file():
        raise RequestError("stdin file does not exist")
    handle = path.open("rb")
    return handle, handle


def _bounded(text: str, maximum: int) -> tuple[str, bool, int]:
    original = len(text)
    if original <= maximum:
        return text, False, original
    return text[:maximum], True, original


def _base_result(status: str, started: float) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": status,
        "return_code": None,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "stdout": "",
        "stderr": "",
        "stdout_truncated": False,
        "stderr_truncated": False,
        "stdout_original_chars": 0,
        "stderr_original_chars": 0,
        "argument_values_reported": False,
        "descendant_termination_proven": False,
    }


def execute(request: dict[str, Any]) -> tuple[dict[str, Any], int]:
    started = time.monotonic()
    if request.get("schema") != REQUEST_SCHEMA:
        raise RequestError(f"schema must be {REQUEST_SCHEMA}")

    allow_path_lookup = request.get("allow_path_lookup", False)
    if not isinstance(allow_path_lookup, bool):
        raise RequestError("allow_path_lookup must be boolean")
    executable = _resolve_executable(
        _require_string(request.get("executable"), "executable"),
        allow_path_lookup,
    )
    arguments = _require_string_list(request.get("args", []), "args")

    cwd_value = request.get("cwd")
    cwd = None
    if cwd_value is not None:
        cwd_path = Path(_require_string(cwd_value, "cwd"))
        if not cwd_path.is_dir():
            raise RequestError("cwd does not exist or is not a directory")
        cwd = str(cwd_path)

    timeout = request.get("timeout_seconds")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise RequestError("timeout_seconds must be a positive number or null")

    encoding = _require_string(
        request.get("output_encoding", "utf-8"), "output_encoding"
    )
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        raise RequestError("output_encoding is not recognized") from exc
    max_output = request.get("max_output_chars", DEFAULT_MAX_OUTPUT_CHARS)
    if (
        isinstance(max_output, bool)
        or not isinstance(max_output, int)
        or max_output < 1
    ):
        raise RequestError("max_output_chars must be a positive integer")

    environment = _build_environment(request.get("environment"))
    stdin_target, stdin_handle = _open_stdin(request.get("stdin"))
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(  # nosec B603
            [executable, *arguments],
            cwd=cwd,
            env=environment,
            stdin=stdin_target,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        try:
            stdout_bytes, stderr_bytes = process.communicate(timeout=timeout)
            status = "COMPLETED" if process.returncode == 0 else "FAILED"
            exit_code = 0 if process.returncode == 0 else 1
        except subprocess.TimeoutExpired:
            process.kill()
            stdout_bytes, stderr_bytes = process.communicate()
            status = "TIMED_OUT"
            exit_code = 124
    except OSError as exc:
        result = _base_result("LAUNCH_FAILED", started)
        result["stderr"] = f"{type(exc).__name__}: {exc}"
        result["stderr_original_chars"] = len(result["stderr"])
        return result, 1
    finally:
        if stdin_handle is not None:
            stdin_handle.close()

    stdout = stdout_bytes.decode(encoding, errors="replace")
    stderr = stderr_bytes.decode(encoding, errors="replace")
    stdout, stdout_truncated, stdout_original = _bounded(stdout, max_output)
    stderr, stderr_truncated, stderr_original = _bounded(stderr, max_output)

    result = _base_result(status, started)
    result.update(
        {
            "return_code": process.returncode if process is not None else None,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "stdout_original_chars": stdout_original,
            "stderr_original_chars": stderr_original,
        }
    )
    return result, exit_code


def _write_result(path: Path | None, result: dict[str, Any]) -> None:
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if path is None:
        print(rendered)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path)
    arguments = parser.parse_args()

    started = time.monotonic()
    try:
        request = json.loads(arguments.request.read_text(encoding="utf-8-sig"))
        if not isinstance(request, dict):
            raise RequestError("request root must be an object")
        result, exit_code = execute(request)
    except (OSError, json.JSONDecodeError, UnicodeError, RequestError) as exc:
        result = _base_result("REQUEST_INVALID", started)
        result["stderr"] = f"{type(exc).__name__}: {exc}"
        result["stderr_original_chars"] = len(result["stderr"])
        exit_code = 2

    _write_result(arguments.result, result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
