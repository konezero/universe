#!/usr/bin/env python3
"""At Stop, remind the agent to review relevant follow-ups before waiting."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from universe_session_inject_hook import default_state_path, load_server_connection


def hook_request(payload: Mapping[str, Any], environment: Mapping[str, str], provider: str) -> dict[str, Any] | None:
    # Grok 1.0.30 emits camelCase input even for Claude-compatible hook files.
    # Its extra session-end Stop is observe-only, not an idle turn boundary.
    event = payload.get("hook_event_name")
    session_ref = payload.get("session_id")
    if provider == "GROK":
        native_event = payload.get("hookEventName")
        if event not in {None, "Stop"} or native_event not in {None, "stop"}:
            return None
        event = event or ("Stop" if native_event == "stop" else None)
        native_ref = payload.get("sessionId")
        if session_ref and native_ref and session_ref != native_ref:
            return None
        session_ref = session_ref or native_ref
        if payload.get("reason") != "end_turn":
            return None
    if (event != "Stop"
            or any(payload.get(key) for key in ("agent_id", "agentId", "subagentType", "subagent_type"))
            or payload.get("stop_hook_active") is True or payload.get("stopHookActive") is True):
        return None
    coordinates = {"terminal_id": environment.get("UNIVERSE_TERMINAL_ID", ""),
                   "session_anchor_ref": environment.get("UNIVERSE_SESSION_ANCHOR_REF", ""),
                   "supervisor_session_id": environment.get("UNIVERSE_SUPERVISOR_SESSION_ID", ""),
                   "provider": provider, "provider_session_ref": session_ref or ""}
    # Grok also loads .claude/settings.json. Its Claude handler must skip so
    # the native Grok handler alone performs the check.
    if not all(coordinates.values()) or environment.get("UNIVERSE_PROVIDER", provider).upper() != provider:
        return None
    return {"schema": "universe.session-bus-hook.v1", **coordinates, "hook_event_name": "Stop"}


def render_hook(result: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any]:
    if not result.get("pending_count"):
        return {}
    coordinates = json.dumps({"terminal_id": request["terminal_id"],
                              "session_anchor_ref": request["session_anchor_ref"]}, ensure_ascii=False)
    reason = (
        "[대기 전 후속 메시지 확인] 작업 중간이 아니라 이번 응답을 마치고 대기하려는 시점입니다. "
        "Session Bus에 확인할 답변·조율 메시지가 있습니다. 현재 작업과 관련된 메시지, 후속 답변, "
        "기존 지시에 따라 이어서 처리할 메시지를 읽고 맥락에 따라 판단하세요.\n"
        "먼저 아래 Session Anchor의 inbox를 읽고 thread_id와 원래 지시, 처리 이력을 확인하세요. "
        "훅은 대기 여부만 확인했으며 메시지를 가져가거나 읽음·완료로 바꾸지 않았습니다.\n"
        "- 기존에 승인된 작업의 후속이면 이어서 처리하세요.\n"
        "- 이미 처리된 중복 보고나 단순 수신 확인은 같은 작업을 다시 실행하지 마세요. "
        "PROCESS_REPLY의 처리는 수신 확인이며 새로운 답장을 요청하지 않습니다.\n"
        "- 관련 없는 별도 작업은 섞어 실행하지 마세요. 아직 처리하지 않은 메시지를 완료로 기록하지 마세요.\n"
        "- 관련 후속이 없거나 여기서 더 할 일이 없으면 그대로 대기하세요. 반복 폴링하지 마세요.\n"
        "조회: GET /v1/session-bus/inbox?session_anchor_ref=" + request["session_anchor_ref"]
        + "&projection=INBOX. 원문과 이력은 같은 anchor 및 thread_id로 projection=ACTIVITY 조회.\n"
        "현재 수신 좌표: " + coordinates + ". endpoint와 인증은 "
        ".ai/skills/common/resolve_universe_endpoint.py로 해석하고 인증값은 출력하지 마세요.\n"
        "실제로 처리하기로 선택한 메시지만 기존 API 상태 전이와 /reply 계약을 사용하세요. "
        "QUEUED이면 /v1/session-bus/messages/{message_id}/state에 현재 수신 좌표와 state를 보내 "
        "ACCEPTED 다음 STARTED로 전이하고, ACCEPTED이면 STARTED로 전이하세요. "
        "이미 STARTED이면 다시 시작하지 말고 기존 맥락에서 이어가세요. "
        "처리 후 /v1/session-bus/messages/{message_id}/reply에 현재 수신 좌표, 실제 처리 요약 body_text, "
        "outcome COMPLETED 또는 실제 실패의 FAILED를 기록하세요. 메시지 처리 완료와 프로젝트 작업/TODO 완료는 별개입니다."
    )
    if request.get("provider") in {"CLAUDE", "GROK"}:
        return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": reason}}
    return {"decision": "block", "reason": reason}


def run_hook(payload: Mapping[str, Any], *, provider: str, environment: Mapping[str, str] | None = None,
             state_path: Path | None = None) -> dict[str, Any]:
    env = os.environ if environment is None else environment
    request = hook_request(payload, env, provider)
    if request is None:
        return {"status": "SKIPPED", "hook_stdout": {}}
    endpoint, token, error = load_server_connection(state_path or default_state_path())
    if not endpoint:
        return {"status": "OFFLINE", "error_code": "BUS_HOOK_ENDPOINT_UNAVAILABLE", "detail": error, "hook_stdout": {}}
    url = endpoint.rstrip("/") + "/v1/session-bus/hooks"
    try:
        req = urllib.request.Request(url, data=json.dumps(request).encode(), headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=4) as response:
            result = json.loads(response.read())
        return {**result, "hook_stdout": render_hook(result, request)}
    except urllib.error.HTTPError as exc:
        try: detail = json.loads(exc.read())
        except (ValueError, OSError): detail = {"error_code": "BUS_HOOK_HTTP_ERROR"}
        return {"status": "FAILED", "operation": "SESSION_BUS_STOP_HOOK", "endpoint": url,
                "http_status": exc.code, "error": detail, "hook_stdout": {}}
    except (OSError, ValueError) as exc:
        return {"status": "FAILED", "operation": "SESSION_BUS_STOP_HOOK", "endpoint": url,
                "error_code": type(exc).__name__, "detail": str(exc), "hook_stdout": {}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["CODEX", "CLAUDE", "GROK"])
    args = parser.parse_args()
    payload: dict[str, Any] = {}
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be an object")
        result = run_hook(payload, provider=args.provider)
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "FAILED", "error_code": type(exc).__name__, "hook_stdout": {}}
    # Best-effort Runtime diagnostics contain no prompt or bearer credentials.
    try:
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Universe" / "hook-observations"
        root.mkdir(parents=True, exist_ok=True)
        diagnostic = {k: v for k, v in result.items() if k not in {"messages", "hook_stdout"}}
        diagnostic.update(observed_at=time.time(), provider=args.provider, hook_event_name=(payload.get("hook_event_name") or payload.get("hookEventName")) if isinstance(payload, dict) else None)
        with (root / ((os.environ.get("UNIVERSE_TERMINAL_ID") or "unbound") + ".jsonl")).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(diagnostic, ensure_ascii=False) + "\n")
    except OSError:
        pass
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result.get("hook_stdout") or {}, ensure_ascii=False))
    return 0


if __name__ == "__main__": raise SystemExit(main())
