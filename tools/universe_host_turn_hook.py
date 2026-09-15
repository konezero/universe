#!/usr/bin/env python3
"""Report CLI lifecycle only to its exact surviving Host. Never read the Bus."""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping
from universe_app.reconnection_host import ReconnectionHostRegistry


def normalize_event(payload: Mapping[str, Any], provider: str, environment: Mapping[str, str]) -> dict[str, Any] | None:
    if environment.get("UNIVERSE_PROVIDER", provider).upper() != provider:
        return None
    if any(payload.get(k) for k in ("agent_id", "agentId", "subagent_type", "subagentType")):
        return None
    if payload.get("session_id") and payload.get("sessionId") and payload["session_id"] != payload["sessionId"]:
        return None
    if payload.get("hook_event_name") and payload.get("hookEventName"):
        canonical=lambda value: re.sub(r"[^a-z]", "", str(value).lower())
        if canonical(payload["hook_event_name"]) != canonical(payload["hookEventName"]): return None
    event = payload.get("hook_event_name") or payload.get("hookEventName") or payload.get("type")
    ref = payload.get("session_id") or payload.get("sessionId") or payload.get("thread-id")
    if not ref: return None
    native = re.sub(r"[^a-z]", "", str(event).lower())
    notification = payload.get("notification_type") or payload.get("notificationType")
    kind = {"userpromptsubmit":"PROMPT_SUBMITTED", "beforeagent":"PROMPT_SUBMITTED",
            "pretooluse":"WORKING", "beforetool":"WORKING", "permissionrequest":"WAITING_USER",
            "stop":"STOPPING", "afteragent":"STOPPING", "sessionend":"SESSION_ENDED",
            "interrupt":"INTERRUPTED", "stopcancelled":"INTERRUPTED", "stopfailure":"INTERRUPTED"}.get(native)
    # Stop is before other hooks may request continuation. It is never idle.
    if native == "agentturncomplete" and provider == "CODEX": kind = "IDLE"
    if native == "notification":
        if notification == "idle_prompt" and provider in {"CLAUDE", "GROK"}: kind = "IDLE"
        elif notification in {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog"}: kind = "WAITING_USER"
        # task_complete/agent_completed may refer to a background task, not this CLI turn.
    if not kind: return None
    prompt = str(payload.get("prompt") or "")
    mid = re.search(r"(?:^|\s)instruction_ref:\s*session-bus:(msg_[a-zA-Z0-9]+)(?=\s|$)", prompt)
    persona_mid = re.search(
        r"(?:^|\n)Persona delivery message id:\s*(persona-[A-Za-z0-9_-]+)(?=\s|$)",
        prompt,
    )
    message_id = (
        mid.group(1)
        if mid
        else persona_mid.group(1)
        if persona_mid
        else ""
    )
    return {"schema":"universe.host-turn-event.v1", "provider":provider,
            "provider_session_ref":str(ref), "event":kind,
            "turn_id":str(payload.get("turn_id") or payload.get("turn-id") or payload.get("turnId") or payload.get("promptId") or ""),
            "observed_at_ms":time.time_ns() // 1_000_000,
            "message_id":message_id if kind == "PROMPT_SUBMITTED" else ""}


def run_hook(payload: Mapping[str, Any], *, provider: str, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environment is None else environment
    event = normalize_event(payload, provider, env)
    host_id = env.get("UNIVERSE_SESSION_HOST_ID", "")
    if event is None or not host_id: return {"status":"SKIPPED", "hook_stdout":{}}
    registry = Path(env.get("UNIVERSE_RECONNECTION_HOST_REGISTRY") or str(Path(env.get("LOCALAPPDATA", ""))/"Universe"/"reconnection-hosts"))
    binary = Path(env.get("UNIVERSE_RECONNECTION_HOST_BINARY") or str(Path(__file__).parent/"session_host"/"target"/"release"/"universe-session-host-v2.exe"))
    try:
        client = ReconnectionHostRegistry(registry, binary).discover_by_host_id(host_id)
        host = client.request("turn_observe", channel=event)["host"]
        return {"status":"HOST_TURN_OBSERVED", "host_id":host_id, "event":event["event"],
                "turn_state":host["turn_delivery"]["state"], "hook_stdout":{}}
    except Exception as error:
        return {"status":"FAILED", "operation":"HOST_TURN_OBSERVE", "host_id":host_id,
                "error_code":getattr(error,"code","HOST_TURN_OBSERVATION_FAILED"), "detail":str(error), "hook_stdout":{}}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=["CODEX","CLAUDE","GROK","GEMINI"])
    parser.add_argument("--notify-json", default=None)
    args=parser.parse_args()
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        payload=json.loads(args.notify_json) if args.notify_json is not None else json.load(sys.stdin)
        if not isinstance(payload, dict): raise ValueError("hook payload must be an object")
        result=run_hook(payload,provider=args.provider)
    except (ValueError,OSError) as error:
        result={"status":"FAILED","operation":"HOST_TURN_OBSERVE","error_code":"HOOK_INPUT_INVALID","detail":str(error)}
    try:
        directory=Path(os.environ.get("LOCALAPPDATA", str(Path.home())))/"Universe"/"hook-observations"
        directory.mkdir(parents=True,exist_ok=True)
        with (directory/((os.environ.get("UNIVERSE_TERMINAL_ID") or "unbound")+".jsonl")).open("a",encoding="utf-8") as stream:
            stream.write(json.dumps({**result,"observed_at":time.time()},ensure_ascii=False)+"\n")
    except OSError: pass
    # Reporting never becomes a model prompt or Stop continuation.
    print("{}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
