#!/usr/bin/env python3
"""Classify obvious Windows shell hazards without executing the command."""

from __future__ import annotations

import argparse
import json
import ntpath
from pathlib import Path
import re
from typing import Any


REQUEST_SCHEMA = "windows-shell-guard.request.v1"
RESULT_SCHEMA = "windows-shell-guard.result.v1"

HOST_VALUES = {"WINDOWS", "LINUX", "MACOS", "UNKNOWN"}
SHELL_VALUES = {"POWERSHELL", "PWSH", "CMD", "BASH", "WSL", "UNKNOWN"}

POWERSHELL_VERBS = {
    "add",
    "clear",
    "compare",
    "connect",
    "convertfrom",
    "convertto",
    "copy",
    "disable",
    "disconnect",
    "enable",
    "enter",
    "exit",
    "export",
    "foreach",
    "format",
    "get",
    "group",
    "import",
    "invoke",
    "join",
    "measure",
    "move",
    "new",
    "out",
    "pop",
    "push",
    "read",
    "receive",
    "remove",
    "rename",
    "resolve",
    "restart",
    "select",
    "set",
    "sort",
    "split",
    "start",
    "stop",
    "test",
    "wait",
    "where",
    "write",
}

REPLACEMENTS = {
    "BASH_COMMAND": "Use rg, Git, Python, or the equivalent PowerShell cmdlet.",
    "BASH_FIND_NAME": "Use: rg --files -g 'PATTERN'.",
    "BASH_RECURSIVE_DELETE": (
        "Use Remove-Item -LiteralPath PATH -Recurse -Force after path validation."
    ),
    "BASH_RECURSIVE_COPY": (
        "Use Copy-Item -LiteralPath SOURCE -Destination TARGET -Recurse."
    ),
    "BASH_MOVE": "Use Move-Item -LiteralPath SOURCE -Destination TARGET.",
    "BASH_ENVIRONMENT_ASSIGNMENT": 'Use: $env:NAME = "value".',
    "BASH_CHAINING": "Run each command as a separate checked invocation.",
    "BASH_NULL_DEVICE": "Use $null or redirect with PowerShell syntax.",
    "BASH_PROCESS_SUBSTITUTION": "Use a temporary file or an explicit pipeline.",
    "BASH_LINE_CONTINUATION": "Use PowerShell syntax or an argument array.",
    "WINDOWS_NATIVE_GLOB_UNEXPANDED": (
        "Use the CLI's selector, for example: rg PATTERN -g '*.py' ROOT."
    ),
    "INVOKE_EXPRESSION_FORBIDDEN": "Construct a cmdlet call or structured argv.",
    "START_PROCESS_ARGUMENT_LIST_FORBIDDEN": (
        "Route the executable through windows-native-cli."
    ),
    "POWERSHELL_REQUIRED": "Use PowerShell or pwsh for Windows shell operations.",
    "DESTRUCTIVE_LITERAL_PATH_REQUIRED": "Use -LiteralPath for destructive paths.",
    "DESTRUCTIVE_PATH_SCOPE_REQUIRED": (
        "Supply allowed_roots and resolved_targets before recursive delete or move."
    ),
    "DESTRUCTIVE_TARGET_OUTSIDE_SCOPE": (
        "Resolve the target and keep it below an explicitly allowed root."
    ),
}


class RequestError(ValueError):
    """Raised when a shell-guard request is malformed."""


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RequestError(f"{field} must be a non-empty string")
    if "\x00" in value:
        raise RequestError(f"{field} must not contain NUL")
    return value


def _first_token(command: str) -> str:
    text = command.lstrip()
    if text.startswith("&"):
        text = text[1:].lstrip()
    if not text:
        return ""
    if text[0] in {"'", '"'}:
        end = text.find(text[0], 1)
        return text[1:end] if end > 0 else text[1:]
    return re.split(r"[\s;|]", text, maxsplit=1)[0]


def _command_tokens(command: str) -> list[str]:
    tokens = re.findall(r"""(?:"[^"]*"|'[^']*'|[^\s;|]+)""", command)
    return [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\""
        else token
        for token in tokens
    ]


def _looks_powershell_internal(command: str) -> bool:
    stripped = command.lstrip()
    if stripped.startswith(("$", "[", "{", "(", ".")):
        return True
    first = _first_token(command)
    lowered = first.lower()
    if lowered in {"if", "foreach", "for", "while", "switch", "try", "throw"}:
        return True
    if "-" not in first:
        return False
    verb = first.split("-", 1)[0].lower()
    return verb in POWERSHELL_VERBS


def _obvious_bash_reasons(command: str) -> list[str]:
    reasons: list[str] = []
    command_boundary = r"(?:^|[;\n|])\s*"

    if re.search(
        command_boundary + r"(?:grep|sed|awk|head|tail|cat|chmod|export|bash)\b",
        command,
        flags=re.IGNORECASE,
    ):
        reasons.append("BASH_COMMAND")
    if re.search(
        command_boundary + r"find\b[^\r\n;|]*\s-name\b",
        command,
        flags=re.IGNORECASE,
    ):
        reasons.append("BASH_FIND_NAME")
    if re.search(
        command_boundary + r"rm\b[^\r\n;|]*\s-(?:rf|fr)\b",
        command,
        flags=re.IGNORECASE,
    ):
        reasons.append("BASH_RECURSIVE_DELETE")
    if re.search(
        command_boundary + r"cp\b[^\r\n;|]*\s-(?:r|R)\b",
        command,
        flags=re.IGNORECASE,
    ):
        reasons.append("BASH_RECURSIVE_COPY")
    if re.search(command_boundary + r"mv\b", command, flags=re.IGNORECASE):
        reasons.append("BASH_MOVE")
    if re.search(
        r"(?:^|[;\n])\s*[A-Za-z_][A-Za-z0-9_]*=[^\s;]+\s+\S+",
        command,
    ):
        reasons.append("BASH_ENVIRONMENT_ASSIGNMENT")
    if "&&" in command or "||" in command:
        reasons.append("BASH_CHAINING")
    if "/dev/null" in command:
        reasons.append("BASH_NULL_DEVICE")
    if re.search(r"<\([^)]*\)", command):
        reasons.append("BASH_PROCESS_SUBSTITUTION")
    if re.search(r"\\\s*(?:\r?\n|$)", command):
        reasons.append("BASH_LINE_CONTINUATION")
    return reasons


def _has_unexpanded_rg_path_glob(command: str) -> bool:
    tokens = _command_tokens(command)
    if tokens and tokens[0] == "&":
        tokens = tokens[1:]
    if not tokens:
        return False
    first = ntpath.basename(tokens[0]).lower()
    if first not in {"rg", "rg.exe"}:
        return False

    value_options = {
        "-g",
        "--glob",
        "--iglob",
        "--type",
        "--type-add",
        "--engine",
        "--encoding",
    }
    pattern_options = {"-e", "--regexp", "-f", "--file"}
    option_value_kind: str | None = None
    pattern_supplied = False
    files_mode = False
    for token in tokens[1:]:
        if option_value_kind is not None:
            if option_value_kind == "pattern":
                pattern_supplied = True
            option_value_kind = None
            continue
        # Bandit treats this ripgrep option as a password-like assignment.
        if token == "--files":  # nosec B105
            files_mode = True
            continue
        if token in pattern_options:
            option_value_kind = "pattern"
            continue
        if token in value_options:
            option_value_kind = "value"
            continue
        if token.startswith(("-e", "--regexp=", "-f", "--file=")):
            pattern_supplied = True
            continue
        if token.startswith(
            (
                "-g",
                "--glob=",
                "--iglob=",
                "--type=",
                "--type-add=",
                "--engine=",
                "--encoding=",
            )
        ):
            continue
        if token.startswith("-"):
            continue
        if not files_mode and not pattern_supplied:
            pattern_supplied = True
            continue
        if ("*" in token or "?" in token) and ("/" in token or "\\" in token):
            return True
    return False


def _destructive_operation(command: str) -> bool:
    lowered = command.lower()
    recursive_delete = "remove-item" in lowered and "-recurse" in lowered
    return recursive_delete or "move-item" in lowered


def _normalize_windows_path(raw: str) -> str:
    if not ntpath.isabs(raw):
        raise RequestError("path scope entries must be absolute Windows paths")
    return ntpath.normcase(ntpath.normpath(raw))


def _path_scope_reasons(request: dict[str, Any]) -> tuple[list[str], bool]:
    scope = request.get("path_scope")
    if not isinstance(scope, dict):
        return ["DESTRUCTIVE_PATH_SCOPE_REQUIRED"], False

    roots = scope.get("allowed_roots")
    targets = scope.get("resolved_targets")
    if (
        not isinstance(roots, list)
        or not roots
        or not all(isinstance(item, str) and item for item in roots)
        or not isinstance(targets, list)
        or not targets
        or not all(isinstance(item, str) and item for item in targets)
    ):
        return ["DESTRUCTIVE_PATH_SCOPE_REQUIRED"], False

    try:
        normalized_roots = [_normalize_windows_path(item) for item in roots]
        normalized_targets = [_normalize_windows_path(item) for item in targets]
    except (RequestError, ValueError):
        return ["DESTRUCTIVE_PATH_SCOPE_REQUIRED"], False

    for target in normalized_targets:
        permitted = False
        for root in normalized_roots:
            try:
                common = ntpath.commonpath([root, target])
            except ValueError:
                continue
            if common == root and target != root:
                permitted = True
                break
        if not permitted:
            return ["DESTRUCTIVE_TARGET_OUTSIDE_SCOPE"], False
    return [], True


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("schema") != REQUEST_SCHEMA:
        raise RequestError(f"schema must be {REQUEST_SCHEMA}")

    host_os = _required_string(request.get("host_os"), "host_os").upper()
    active_shell = _required_string(request.get("active_shell"), "active_shell").upper()
    command = _required_string(request.get("command"), "command")

    if host_os not in HOST_VALUES:
        raise RequestError(f"host_os must be one of {sorted(HOST_VALUES)}")
    if active_shell not in SHELL_VALUES:
        raise RequestError(f"active_shell must be one of {sorted(SHELL_VALUES)}")

    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "status": "UNKNOWN",
        "reason_codes": [],
        "replacement_hint": None,
        "execution_route": "UNKNOWN",
        "path_scope_checked": False,
        "command_executed": False,
    }

    if host_os == "UNKNOWN" or active_shell == "UNKNOWN":
        result["reason_codes"] = ["HOST_OR_SHELL_UNKNOWN"]
        return result
    if host_os != "WINDOWS":
        result["status"] = "NOT_APPLICABLE"
        result["reason_codes"] = ["NON_WINDOWS_HOST"]
        return result
    if active_shell in {"BASH", "WSL"}:
        result["status"] = "PERMITTED"
        result["reason_codes"] = ["EXPLICIT_BASH_SHELL"]
        result["execution_route"] = "EXPLICIT_BASH"
        return result
    if active_shell == "CMD":
        result["status"] = "BLOCKED"
        result["reason_codes"] = ["POWERSHELL_REQUIRED"]
        result["replacement_hint"] = REPLACEMENTS["POWERSHELL_REQUIRED"]
        return result

    reasons = _obvious_bash_reasons(command)
    if _has_unexpanded_rg_path_glob(command):
        reasons.append("WINDOWS_NATIVE_GLOB_UNEXPANDED")
    lowered = command.lower()
    if re.search(r"\binvoke-expression\b", lowered):
        reasons.append("INVOKE_EXPRESSION_FORBIDDEN")
    if "start-process" in lowered and "-argumentlist" in lowered:
        reasons.append("START_PROCESS_ARGUMENT_LIST_FORBIDDEN")

    if _destructive_operation(command):
        if "-literalpath" not in lowered:
            reasons.append("DESTRUCTIVE_LITERAL_PATH_REQUIRED")
        path_reasons, checked = _path_scope_reasons(request)
        reasons.extend(path_reasons)
        result["path_scope_checked"] = checked

    reasons = _deduplicate(reasons)
    if reasons:
        result["status"] = "BLOCKED"
        result["reason_codes"] = reasons
        result["replacement_hint"] = REPLACEMENTS.get(reasons[0])
        return result

    result["status"] = "PERMITTED"
    result["execution_route"] = (
        "POWERSHELL_INTERNAL"
        if _looks_powershell_internal(command)
        else "WINDOWS_NATIVE_CLI"
    )
    return result


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

    try:
        request = json.loads(arguments.request.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise RequestError("request root must be an object")
        result = evaluate(request)
        exit_code = 0 if result["status"] in {"PERMITTED", "NOT_APPLICABLE"} else 2
    except (OSError, UnicodeError, json.JSONDecodeError, RequestError) as exc:
        result = {
            "schema": RESULT_SCHEMA,
            "status": "REQUEST_INVALID",
            "reason_codes": ["REQUEST_INVALID"],
            "replacement_hint": None,
            "execution_route": "UNKNOWN",
            "path_scope_checked": False,
            "command_executed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 2

    _write_result(arguments.result, result)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
