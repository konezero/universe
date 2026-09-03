"""Recognize a provider CLI in a cmd.exe-owned process tree.

Ports the interpreter-stripping matcher from Orca's
``src/shared/agent-process-recognition.ts`` (MIT, stablyai/orca @ 67e22345)
onto Universe's Supervisor-owned ``cmd /k`` wrapper. The cmd layer stays the
identity anchor; this module only names the CLI running under it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

PROCESS_EXTENSION_RE = re.compile(r"\.(?:exe|cmd|bat|ps1)$", re.IGNORECASE)
INTERPRETER_SCRIPT_EXTENSION_RE = re.compile(r"\.(?:js|mjs|cjs)$", re.IGNORECASE)
PYTHON_SCRIPT_EXTENSION_RE = re.compile(r"\.(?:py|pyw)$", re.IGNORECASE)
PYTHON_PROCESS_RE = re.compile(r"^python(?:\d+(?:\.\d+)*)?$")

STATIC_INTERPRETER_PROCESS_NAMES = frozenset(
    {
        "node",
        "python",
        "python3",
        "bash",
        "zsh",
        "sh",
        "fish",
        "pwsh",
        "powershell",
        "cmd",
    }
)

PROVIDER_PROCESS_NAMES = {
    "claude": "CLAUDE",
    "codex": "CODEX",
    "grok": "GROK",
}

NODE_PACKAGE_HINTS = (
    ("node_modules/@anthropic-ai/", "CLAUDE"),
    ("node_modules/@openai/codex/", "CODEX"),
    ("node_modules/@xai/", "GROK"),
)


def normalize_process_name(
    process_name: str | None,
    *,
    strip_interpreter_script_extension: bool = False,
) -> str:
    if not process_name:
        return ""
    unquoted = str(process_name).strip().strip("\"'")
    basename = unquoted.replace("/", "\\").rsplit("\\", 1)[-1]
    without_ext = PROCESS_EXTENSION_RE.sub("", basename).lower()
    if strip_interpreter_script_extension:
        return INTERPRETER_SCRIPT_EXTENSION_RE.sub("", without_ext)
    return without_ext


def is_interpreter_process_name(normalized: str) -> bool:
    return normalized in STATIC_INTERPRETER_PROCESS_NAMES or bool(
        PYTHON_PROCESS_RE.match(normalized)
    )


def tokenize_command_line(command_line: str) -> list[str]:
    tokens: list[str] = []
    current = ""
    quote: str | None = None
    escaped = False
    length = len(command_line)
    index = 0
    while index < length:
        char = command_line[index]
        if escaped:
            current += char
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            nxt = command_line[index + 1] if index + 1 < length else ""
            if nxt and (nxt.isspace() or nxt in {'"', "'", "\\"}):
                escaped = True
                index += 1
                continue
        if char in {'"', "'"} and quote is None:
            quote = char
            index += 1
            continue
        if quote == char:
            quote = None
            index += 1
            continue
        if char.isspace() and quote is None:
            if current:
                tokens.append(current)
                current = ""
            index += 1
            continue
        current += char
        index += 1
    if current:
        tokens.append(current)
    return tokens

def provider_for_normalized_process(normalized: str) -> str | None:
    exact = PROVIDER_PROCESS_NAMES.get(normalized)
    if exact:
        return exact
    if normalized.startswith("codex-"):
        return "CODEX"
    if normalized.startswith("grok-"):
        return "GROK"
    if normalized.startswith("claude-"):
        return "CLAUDE"
    return None


def provider_for_path(path: str) -> str | None:
    comparable = str(path or "").replace("\\", "/").lower()
    if not comparable:
        return None
    name = normalize_process_name(comparable)
    found = provider_for_normalized_process(name)
    if found:
        return found
    for hint, provider in NODE_PACKAGE_HINTS:
        if hint in comparable:
            return provider
    return None


def find_interpreter_entrypoint_token(tokens: Sequence[str]) -> str | None:
    if not tokens:
        return None
    first = normalize_process_name(tokens[0])
    if not is_interpreter_process_name(first):
        return None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            continue
        if PYTHON_PROCESS_RE.match(first) and token == "-m":
            return tokens[index + 1] if index + 1 < len(tokens) else None
        if token.startswith("-"):
            index += 1
            continue
        if "/" in token or "\\" in token or PROCESS_EXTENSION_RE.search(token):
            return token
        maybe = provider_for_normalized_process(normalize_process_name(token))
        if maybe:
            return token
        index += 1
    return None


def recognize_agent_process_from_command_line(command_line: str) -> dict[str, str] | None:
    tokens = tokenize_command_line(command_line)
    if not tokens:
        return None
    first = normalize_process_name(tokens[0])
    entry = find_interpreter_entrypoint_token(tokens) or tokens[0]
    for candidate in (entry, tokens[0]):
        provider = provider_for_path(candidate) or provider_for_normalized_process(
            normalize_process_name(candidate)
        )
        if provider:
            return {
                "provider": provider,
                "process_name": normalize_process_name(candidate),
            }
    if is_interpreter_process_name(first):
        return None
    return None


def recognize_provider_process_tree(
    nodes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Name the provider CLI under a cmd /k shell.

    Each node is ``{name, image, command_line, children}``. Interpreters are
    stripped so ``cmd -> node -> claude`` still identifies CLAUDE.
    """

    def walk(items: Sequence[Mapping[str, Any]]) -> dict[str, str] | None:
        for node in items:
            command = str(node.get("command_line") or "")
            if command:
                found = recognize_agent_process_from_command_line(command)
                if found:
                    return found
            image = str(node.get("image") or "")
            name = str(node.get("name") or "")
            for candidate in (image, name):
                provider = provider_for_path(candidate) or provider_for_normalized_process(
                    normalize_process_name(candidate)
                )
                if provider:
                    return {
                        "provider": provider,
                        "process_name": normalize_process_name(candidate or name),
                    }
            nested = node.get("children")
            if isinstance(nested, Sequence) and nested:
                found = walk(nested)
                if found:
                    return found
            if is_interpreter_process_name(normalize_process_name(name)):
                continue
        return None

    recognized = walk(nodes)
    if recognized is None:
        return {"alive": False, "provider": "", "process_name": ""}
    return {
        "alive": True,
        "provider": recognized["provider"],
        "process_name": recognized["process_name"],
    }
