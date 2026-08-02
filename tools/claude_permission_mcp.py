"""Minimal stdio MCP server for Claude's --permission-prompt-tool.

Claude Code spawns this process and calls the ``approve`` tool whenever a tool
request is not already resolved by a rule or mode. This server owns **no**
permission policy: it forwards the request to the Universe loopback broker and
returns whatever decision Universe made.

Transport contract:

* JSON-RPC 2.0 over stdio, one message per line;
* the broker endpoint and capability token arrive through the environment
  (``UNIVERSE_CLAUDE_PERMISSION_ENDPOINT`` / ``UNIVERSE_CLAUDE_PERMISSION_TOKEN``)
  so no secret appears in argv;
* nothing is ever written to stdout except JSON-RPC responses, and the token is
  never written to stdout or stderr;
* any transport failure denies.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Mapping


PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "universe_permission"
TOOL_NAME = "approve"
ENDPOINT_ENVIRONMENT = "UNIVERSE_CLAUDE_PERMISSION_ENDPOINT"
TOKEN_ENVIRONMENT = "UNIVERSE_CLAUDE_PERMISSION_TOKEN"
TOKEN_HEADER = "X-Universe-Claude-Permission-Token"
REQUEST_TIMEOUT_SECONDS = 300.0

TOOL_DEFINITION = {
    "name": TOOL_NAME,
    "description": (
        "Ask the Universe operator to approve or reject a Claude tool call. "
        "Universe owns the decision; this tool only carries the request."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "input": {"type": "object"},
            "tool_use_id": {"type": "string"},
        },
        "required": ["tool_name", "input"],
    },
}


def _deny(message: str) -> dict[str, Any]:
    return {"behavior": "deny", "message": message}


def ask_universe(
    arguments: Mapping[str, Any],
    *,
    opener=urllib.request.urlopen,
) -> dict[str, Any]:
    """Forward one prompt to the loopback broker. Any failure denies."""

    endpoint = os.environ.get(ENDPOINT_ENVIRONMENT, "").strip()
    token = os.environ.get(TOKEN_ENVIRONMENT, "").strip()
    if not endpoint or not token:
        return _deny("CLAUDE_PERMISSION_TRANSPORT_UNCONFIGURED")
    if not endpoint.startswith(("http://127.0.0.1:", "http://localhost:")):
        # Loopback only; never send a prompt off-box.
        return _deny("CLAUDE_PERMISSION_ENDPOINT_NOT_LOOPBACK")
    body = json.dumps(dict(arguments), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", TOKEN_HEADER: token},
    )
    try:
        with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        return _deny("CLAUDE_PERMISSION_BROKER_UNREACHABLE")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _deny("CLAUDE_PERMISSION_BROKER_RESPONSE_INVALID")
    if not isinstance(decoded, Mapping) or decoded.get("behavior") not in {
        "allow",
        "deny",
    }:
        return _deny("CLAUDE_PERMISSION_BROKER_RESPONSE_INVALID")
    return dict(decoded)


def handle_message(message: Mapping[str, Any], *, asker=ask_universe) -> dict[str, Any] | None:
    """Return one JSON-RPC response, or ``None`` for a notification."""

    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL_DEFINITION]}
    elif method == "tools/call":
        params = message.get("params")
        params = params if isinstance(params, Mapping) else {}
        if str(params.get("name") or "") != TOOL_NAME:
            decision = _deny("CLAUDE_PERMISSION_TOOL_UNKNOWN")
        else:
            arguments = params.get("arguments")
            decision = asker(arguments if isinstance(arguments, Mapping) else {})
        result = {
            "content": [
                {"type": "text", "text": json.dumps(decision, ensure_ascii=False)}
            ]
        }
    elif method == "ping":
        result = {}
    else:
        if message_id is None:
            # Notification (e.g. notifications/initialized): nothing to answer.
            return None
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": -32601, "message": "method not found"},
        }
    if message_id is None:
        return None
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, Mapping):
            continue
        response = handle_message(message)
        if response is None:
            continue
        sys.stdout.write(
            json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
