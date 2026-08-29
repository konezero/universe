#!/usr/bin/env python3
"""Provider-neutral CLI for one durable Session Anchor inbox.

The CLI discovers the local Universe service from its Host-owned state file.
Message bodies and replies use JSON/file transport; secrets are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from resolve_universe_endpoint import resolve


RESULT_SCHEMA = "universe.session-inbox-cli.result.v1"
CLI_LIFECYCLE_STATES = (
    "READ",
    "WORKING",
    "QUEUED",
    "ACCEPTED",
    "STARTED",
    "COMPLETED",
    "FAILED",
    "REPLIED",
    "CANCELLED",
    "DONE",
)


def _http(
    endpoint: str,
    token: str,
    method: str,
    path: str,
    body: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        endpoint.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload if isinstance(payload, dict) else {}
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except Exception:
            payload = {"status": "ERROR", "detail": str(error)}
        return error.code, payload if isinstance(payload, dict) else {}


def _coordinate(args: argparse.Namespace) -> tuple[str, str]:
    anchor = str(
        args.anchor
        or os.environ.get("UNIVERSE_SESSION_ANCHOR_REF")
        or os.environ.get("UNIVERSE_ANCHOR_REF")
        or ""
    ).strip()
    terminal = str(
        args.terminal
        or os.environ.get("UNIVERSE_TERMINAL_ID")
        or ""
    ).strip()
    if not anchor and not terminal:
        raise ValueError(
            "Session Anchor or terminal handle is required; set "
            "UNIVERSE_SESSION_ANCHOR_REF or UNIVERSE_TERMINAL_ID"
        )
    return anchor, terminal


def _query_path(
    *,
    anchor: str,
    terminal: str,
    projection: str,
    headers_only: bool,
) -> str:
    query = {
        "projection": projection.upper(),
        "headers": "1" if headers_only else "0",
    }
    if anchor:
        query["session_anchor_ref"] = anchor
    else:
        query["terminal_id"] = terminal
    return "/v1/session-bus/inbox?" + urllib.parse.urlencode(query)


def _list(
    endpoint: str,
    token: str,
    *,
    anchor: str,
    terminal: str,
    projection: str,
    headers_only: bool,
) -> tuple[int, dict[str, Any]]:
    return _http(
        endpoint,
        token,
        "GET",
        _query_path(
            anchor=anchor,
            terminal=terminal,
            projection=projection,
            headers_only=headers_only,
        ),
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    state = resolve(Path(args.state_file) if args.state_file else None)
    if state.get("status") != "UNIVERSE_LOCAL_ENDPOINT_READY":
        return {
            "schema": RESULT_SCHEMA,
            "status": "INBOX_UNAVAILABLE",
            "reasons": list(state.get("reasons") or [state.get("status")]),
        }
    try:
        anchor, terminal = _coordinate(args)
    except ValueError as error:
        return {
            "schema": RESULT_SCHEMA,
            "status": "INBOX_BLOCKED",
            "reasons": ["SESSION_COORDINATE_REQUIRED"],
            "detail": str(error),
        }
    endpoint = str(state["endpoint"])
    token = str(state["token"])
    command = str(args.command)
    http_status = 0
    payload: dict[str, Any]

    if command == "list":
        http_status, payload = _list(
            endpoint,
            token,
            anchor=anchor,
            terminal=terminal,
            projection=args.projection,
            headers_only=not args.with_body,
        )
    elif command == "read":
        http_status, listing = _list(
            endpoint,
            token,
            anchor=anchor,
            terminal=terminal,
            projection="ACTIVITY",
            headers_only=False,
        )
        message = next(
            (
                item
                for item in listing.get("messages") or []
                if str(item.get("message_id") or "") == args.message_id
            ),
            None,
        )
        payload = (
            {"status": "OK", "message": message}
            if message is not None
            else {"status": "ERROR", "error_code": "BUS_MESSAGE_NOT_FOUND"}
        )
        if message is None and http_status < 400:
            http_status = 404
    elif command == "state":
        lifecycle_state = {
            "READ": "ACCEPTED",
            "WORKING": "STARTED",
        }.get(args.lifecycle_state, args.lifecycle_state)
        http_status, payload = _http(
            endpoint,
            token,
            "POST",
            f"/v1/session-bus/messages/{urllib.parse.quote(args.message_id)}/state",
            {
                "state": lifecycle_state,
                "session_anchor_ref": anchor,
                "terminal_id": terminal,
                "result_ref": args.result_ref,
                "error_code": args.error_code,
            },
        )
    elif command == "reply":
        body = Path(args.body_file).read_text(encoding="utf-8")
        http_status, payload = _http(
            endpoint,
            token,
            "POST",
            f"/v1/session-bus/messages/{urllib.parse.quote(args.message_id)}/reply",
            {
                "body_text": body,
                "session_anchor_ref": anchor,
                "terminal_id": terminal,
                "result_ref": args.result_ref,
                "outcome": args.outcome,
            },
        )
    else:
        deadline = time.monotonic() + max(0.0, float(args.timeout))
        payload = {"messages": []}
        while True:
            http_status, payload = _list(
                endpoint,
                token,
                anchor=anchor,
                terminal=terminal,
                projection="INBOX",
                headers_only=True,
            )
            if http_status >= 400 or payload.get("messages"):
                break
            if time.monotonic() >= deadline:
                payload = {"status": "WAIT_TIMEOUT", "messages": []}
                break
            time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))

    return {
        "schema": RESULT_SCHEMA,
        "status": "INBOX_COMPLETED" if http_status < 400 else "INBOX_BLOCKED",
        "http_status": http_status,
        "command": command,
        "session_anchor_ref": anchor,
        "terminal_id": terminal,
        "result": payload,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and update a Universe Session Inbox")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--anchor", default="")
    parser.add_argument("--terminal", default="")
    parser.add_argument("--result", default="")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("list")
    listing.add_argument("--projection", choices=("INBOX", "ACTIVITY", "RESULTS"), default="INBOX")
    listing.add_argument("--with-body", action="store_true")

    reading = subparsers.add_parser("read")
    reading.add_argument("message_id")

    state = subparsers.add_parser("state")
    state.add_argument("message_id")
    state.add_argument("lifecycle_state", choices=CLI_LIFECYCLE_STATES)
    state.add_argument("--result-ref", default="")
    state.add_argument("--error-code", default="")

    reply = subparsers.add_parser("reply")
    reply.add_argument("message_id")
    reply.add_argument("--body-file", required=True)
    reply.add_argument("--result-ref", default="")
    reply.add_argument("--outcome", choices=("COMPLETED", "FAILED"), default="COMPLETED")

    waiting = subparsers.add_parser("wait")
    waiting.add_argument("--timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = run(args)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.result:
        Path(args.result).write_text(encoded, encoding="utf-8")
    else:
        print(encoded)
    return 0 if result.get("status") == "INBOX_COMPLETED" else 4


if __name__ == "__main__":
    sys.exit(main())
