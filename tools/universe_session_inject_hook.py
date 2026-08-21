#!/usr/bin/env python3
"""Best-effort MODE_CHANGE / SessionStart → Universe session-ref inject.

Designed for external harness hooks (Claude SessionStart, Codex boot, etc.).

Contract:
- Does not grant authority, Execution Assignment, or write scope.
- Never blocks harness boot: default exit 0 for SKIPPED / OFFLINE / errors.
- Injects only when provider + session_ref + project/room target resolve.
- Optional local observation record under .ai/runtime/tmp (not session.md).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

HOOK_SCHEMA = "universe.session-inject-hook.v1"
PROVIDERS = frozenset({"CLAUDE", "CODEX", "GROK"})

# Prefix used when writing observer_session_ref into mode_current_anchor snapshot.
# Must match _vendor_identity_from_observer prefixes in universe_server.py.
PROVIDER_OBS_PREFIX: dict[str, str] = {
    "CLAUDE": "claude-code",
    "CODEX": "codex",
    "GROK": "grok-cli",
}

# Env keys scanned in order per provider (first non-empty wins).
PROVIDER_ENV_REFS: dict[str, tuple[str, ...]] = {
    "CLAUDE": (
        "CLAUDE_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CONVERSATION_ID",
    ),
    "CODEX": ("CODEX_THREAD_ID", "CODEX_SESSION_ID"),
    "GROK": ("GROK_SESSION_ID", "XAI_SESSION_ID", "GROK_CONVERSATION_ID"),
}

PROVIDER_HINT_ENV = (
    "UNIVERSE_INJECT_PROVIDER",
    "UNIVERSE_PROVIDER",
    "AI_PROVIDER",
    "PROVIDER",
)

# Truthy values that mark this process as a Grok Build / Grok TUI agent.
_GROK_AGENT_TRUTHY = frozenset({"1", "true", "yes", "on", "GROK", "GROK_AGENT"})


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def default_state_path() -> Path:
    override = os.environ.get("UNIVERSE_STATE_FILE")
    if override:
        return Path(override).expanduser()
    portable = os.environ.get("UNIVERSE_DATA_DIR")
    if portable:
        return Path(portable).expanduser() / "server.json"
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe" / "server.json"
    return Path.home() / ".local" / "share" / "universe" / "server.json"


def _result(
    status: str,
    *,
    detail: str = "",
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": HOOK_SCHEMA,
        "status": status,
        "observed_at": utc_now(),
        "authority": "UNASSIGNED",
        "execution_assignment": "UNASSIGNED",
    }
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    return payload


def _read_stdin_json() -> dict[str, Any] | None:
    if sys.stdin is None or sys.stdin.isatty():
        return None
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _paths_equal(left: str, right: str) -> bool:
    try:
        a = Path(left).expanduser().resolve()
        b = Path(right).expanduser().resolve()
    except OSError:
        return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
            os.path.normpath(right)
        )
    if os.name == "nt":
        return os.path.normcase(str(a)) == os.path.normcase(str(b))
    return a == b


def _grok_home(environment: Mapping[str, str]) -> Path:
    raw = str(environment.get("GROK_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".grok"


def _grok_agent_active(environment: Mapping[str, str]) -> bool:
    # Grok sets these on every hook subprocess, including Claude-compat
    # SessionStart commands loaded from .claude/settings.json.
    if str(environment.get("GROK_HOOK_EVENT") or "").strip():
        return True
    if str(environment.get("GROK_HOOK_NAME") or "").strip():
        return True
    value = str(environment.get("GROK_AGENT") or "").strip()
    if value.upper() in _GROK_AGENT_TRUTHY or value in _GROK_AGENT_TRUTHY:
        return True
    # Some hosts only set GROK_HOME while the agent process is live.
    if str(environment.get("GROK_HOME") or "").strip() and str(
        environment.get("GROK_SESSION_ID") or ""
    ).strip():
        return True
    return False


def _stdin_session_ref(stdin_payload: Mapping[str, Any] | None) -> tuple[str | None, str]:
    if not stdin_payload:
        return None, ""
    for key in (
        "session_id",
        "sessionId",
        "conversation_id",
        "conversationId",
        "thread_id",
        "threadId",
    ):
        raw = stdin_payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip(), f"STDIN.{key}"
    nested = stdin_payload.get("session")
    if isinstance(nested, Mapping):
        for key in ("id", "session_id", "sessionId"):
            raw = nested.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip(), f"STDIN.session.{key}"
    return None, ""


def _grok_session_dir_exists(
    repo_root: Path | None,
    environment: Mapping[str, str],
    session_id: str,
) -> bool:
    if not session_id or repo_root is None:
        return False
    home = _grok_home(environment)
    try:
        sessions_root = home / "sessions" / _encode_grok_workspace_dir(Path(repo_root))
    except OSError:
        return False
    return (sessions_root / session_id).is_dir()


def _encode_grok_workspace_dir(repo_root: Path) -> str:
    """Match Grok TUI session folder naming: URL-quote of absolute path."""
    return quote(str(repo_root.resolve()), safe="")


def _grok_session_activity_key(
    sessions_root: Path, session_id: str
) -> tuple[str, float, int]:
    """Sort key for Grok sessions (higher / later is better).

    Uses summary.json last_active_at, chat_history mtime, and message count so
    stale or short-lived active_sessions rows lose to the real conversation.
    """
    session_dir = sessions_root / session_id
    last_active = ""
    mtime = 0.0
    num_messages = 0
    if not session_dir.is_dir():
        return ("", 0.0, 0)
    try:
        mtime = float(session_dir.stat().st_mtime)
    except OSError:
        mtime = 0.0
    summary_path = session_dir / "summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            summary = None
        if isinstance(summary, Mapping):
            last_active = str(
                summary.get("last_active_at") or summary.get("updated_at") or ""
            ).strip()
            for key in ("num_chat_messages", "num_messages"):
                raw = summary.get(key)
                if isinstance(raw, int) and raw >= 0:
                    num_messages = raw
                    break
                if isinstance(raw, str) and raw.isdigit():
                    num_messages = int(raw)
                    break
    for name in ("chat_history.jsonl", "updates.jsonl", "summary.json"):
        path = session_dir / name
        if not path.is_file():
            continue
        try:
            mtime = max(mtime, float(path.stat().st_mtime))
        except OSError:
            continue
    return (last_active, mtime, num_messages)


def resolve_grok_local_session_ref(
    repo_root: Path | None,
    environment: Mapping[str, str],
) -> tuple[str | None, str]:
    """Resolve Grok session_ref from local TUI state (no network).

    Priority:
    1. Among ``$GROK_HOME/active_sessions.json`` rows for this repo cwd, pick the
       session with the highest local activity (summary last_active / mtime),
       not merely the newest ``opened_at`` (can be a short duplicate agent).
    2. Else newest active session folder under
       ``$GROK_HOME/sessions/<encoded-cwd>/`` by the same activity key.
    """
    home = _grok_home(environment)
    if repo_root is not None:
        try:
            repo_resolved = str(repo_root.expanduser().resolve())
        except OSError:
            repo_resolved = str(repo_root)

        sessions_root = home / "sessions" / _encode_grok_workspace_dir(
            Path(repo_resolved)
        )

        active_path = home / "active_sessions.json"
        if active_path.is_file():
            try:
                payload = json.loads(active_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, list):
                matches: list[dict[str, Any]] = []
                for item in payload:
                    if not isinstance(item, Mapping):
                        continue
                    session_id = str(item.get("session_id") or "").strip()
                    cwd = str(item.get("cwd") or "").strip()
                    if not session_id or not cwd:
                        continue
                    if _paths_equal(cwd, repo_resolved):
                        matches.append(dict(item))
                if matches:
                    matches.sort(
                        key=lambda row: (
                            *_grok_session_activity_key(
                                sessions_root, str(row.get("session_id") or "")
                            ),
                            str(row.get("opened_at") or ""),
                        ),
                        reverse=True,
                    )
                    chosen = str(matches[0].get("session_id") or "").strip()
                    if chosen:
                        source = "GROK.active_sessions.json"
                        if len(matches) > 1:
                            source = "GROK.active_sessions.activity"
                        return chosen, source

        if sessions_root.is_dir():
            candidates = [
                path
                for path in sessions_root.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ]
            if candidates:
                latest = max(
                    candidates,
                    key=lambda path: _grok_session_activity_key(
                        sessions_root, path.name
                    ),
                )
                name = latest.name.strip()
                if name:
                    return name, "GROK.sessions_activity"

    return None, "GROK.local_unresolved"


def resolve_provider_and_ref(
    *,
    args: argparse.Namespace,
    stdin_payload: Mapping[str, Any] | None,
    session_fields: Mapping[str, str],
    environment: Mapping[str, str],
    repo_root: Path | None = None,
) -> tuple[str | None, str | None, str]:
    """Return (provider, session_ref, source_label)."""
    if args.provider and (args.session_ref or args.provider_session_ref):
        provider = str(args.provider).strip().upper()
        ref = str(args.session_ref or args.provider_session_ref).strip()
        if provider in PROVIDERS and ref:
            return provider, ref, "CLI"

    stdin_ref, stdin_source = _stdin_session_ref(stdin_payload)
    # Grok TUI also runs project .claude/settings.json SessionStart hooks.
    # Those historically omitted --provider and defaulted to CLAUDE, which
    # overwrote Mode Current Anchor observer with claude-code:<grok-id>.
    if _grok_agent_active(environment) or _grok_session_dir_exists(
        repo_root, environment, stdin_ref or ""
    ):
        for env_key in PROVIDER_ENV_REFS["GROK"]:
            env_ref = str(environment.get(env_key) or "").strip()
            if env_ref:
                return "GROK", env_ref, env_key
        ref = stdin_ref
        source = stdin_source
        if not ref:
            ref, source = resolve_grok_local_session_ref(repo_root, environment)
        if ref:
            return "GROK", ref, source or "GROK.local"

    if stdin_payload:
        # Explicit provider in stdin wins over Claude-shaped keys.
        provider_hint = stdin_payload.get("provider")
        ref_hint = (
            stdin_payload.get("session_ref")
            or stdin_payload.get("provider_session_ref")
            or stdin_payload.get("thread_id")
        )
        if (
            isinstance(provider_hint, str)
            and provider_hint.strip().upper() in PROVIDERS
            and isinstance(ref_hint, str)
            and ref_hint.strip()
        ):
            return (
                provider_hint.strip().upper(),
                ref_hint.strip(),
                "STDIN.explicit",
            )

        # Require an explicit provider. Do not default stdin sessionId to CLAUDE;
        # Grok compat runs the same Claude SessionStart command.
        stdin_provider = (
            str(args.provider).strip().upper()
            if args.provider and str(args.provider).strip().upper() in PROVIDERS
            else ""
        )
        if stdin_provider:
            for key in (
                "session_id",
                "sessionId",
                "conversation_id",
                "conversationId",
                "thread_id",
                "threadId",
            ):
                raw = stdin_payload.get(key)
                if isinstance(raw, str) and raw.strip():
                    return stdin_provider, raw.strip(), f"STDIN.{key}"

            nested = stdin_payload.get("session")
            if isinstance(nested, Mapping):
                for key in ("id", "session_id", "sessionId"):
                    raw = nested.get(key)
                    if isinstance(raw, str) and raw.strip():
                        return stdin_provider, raw.strip(), f"STDIN.session.{key}"

    # Explicit provider env + matching ref env.
    for key in PROVIDER_HINT_ENV:
        hint = str(environment.get(key) or "").strip().upper()
        if hint in PROVIDERS:
            for env_key in PROVIDER_ENV_REFS[hint]:
                ref = str(environment.get(env_key) or "").strip()
                if ref:
                    return hint, ref, env_key
            if hint == "GROK":
                grok_ref, grok_source = resolve_grok_local_session_ref(
                    repo_root, environment
                )
                if grok_ref:
                    return "GROK", grok_ref, grok_source

    # Scan all known provider env refs.
    for provider, keys in PROVIDER_ENV_REFS.items():
        for env_key in keys:
            ref = str(environment.get(env_key) or "").strip()
            if ref:
                return provider, ref, env_key

    # CLI provider with env ref only.
    if args.provider:
        provider = str(args.provider).strip().upper()
        if provider in PROVIDERS:
            for env_key in PROVIDER_ENV_REFS[provider]:
                ref = str(environment.get(env_key) or "").strip()
                if ref:
                    return provider, ref, env_key
            if provider == "GROK":
                grok_ref, grok_source = resolve_grok_local_session_ref(
                    repo_root, environment
                )
                if grok_ref:
                    return "GROK", grok_ref, grok_source

    # Grok TUI local state (active_sessions / sessions dir) when this process
    # is a Grok agent or the user asked for GROK without a ref env.
    want_grok_local = False
    if args.provider and str(args.provider).strip().upper() == "GROK":
        want_grok_local = True
    elif _grok_agent_active(environment):
        want_grok_local = True
    if want_grok_local:
        grok_ref, grok_source = resolve_grok_local_session_ref(repo_root, environment)
        if grok_ref:
            return "GROK", grok_ref, grok_source

    if args.provider:
        return str(args.provider).strip().upper(), None, "CLI.provider_only"
    return None, None, "UNRESOLVED"


def resolve_project_id(
    *,
    args: argparse.Namespace,
    session_fields: Mapping[str, str],
    environment: Mapping[str, str],
    repo_root: Path | None,
) -> str | None:
    for candidate in (
        args.project_id,
        environment.get("UNIVERSE_PROJECT_ID"),
        environment.get("UNIVERSE_NODE"),
    ):
        text = str(candidate or "").strip()
        if text and text.upper() not in {"UNKNOWN", "NONE"}:
            return text
    if repo_root is not None:
        name = repo_root.resolve().name.strip()
        if name:
            return name
    return None


def resolve_mode(
    *,
    args: argparse.Namespace,
    session_fields: Mapping[str, str],
    environment: Mapping[str, str],
) -> str:
    for candidate in (
        args.mode,
        environment.get("UNIVERSE_MODE"),
        environment.get("REQUESTED_MODE"),
    ):
        text = str(candidate or "").strip().upper()
        if text and text not in {"UNKNOWN", "NONE"}:
            return text
    return "MASTER"


def load_server_connection(
    state_path: Path,
) -> tuple[str | None, str | None, str | None]:
    if not state_path.is_file():
        return None, None, "SERVER_STATE_ABSENT"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, None, f"SERVER_STATE_UNREADABLE:{error}"
    if not isinstance(data, dict):
        return None, None, "SERVER_STATE_INVALID"
    endpoint = str(data.get("endpoint") or "").strip()
    token = str(data.get("token") or "").strip()
    if not endpoint:
        return None, None, "SERVER_ENDPOINT_MISSING"
    return endpoint, token or None, None


def endpoint_reachable(endpoint: str, *, timeout: float = 0.35) -> bool:
    parsed = urlsplit(endpoint)
    try:
        port = parsed.port
    except ValueError:
        return False
    host = parsed.hostname
    if host not in {"127.0.0.1", "::1", "localhost"} or port is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def post_inject(
    *,
    endpoint: str,
    token: str | None,
    payload: Mapping[str, Any],
    timeout: float = 3.0,
) -> tuple[int, dict[str, Any] | None, str | None]:
    body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
    url = endpoint.rstrip("/") + "/v1/sessions/inject"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            status = int(response.status)
    except urllib.error.HTTPError as error:
        try:
            raw = error.read().decode("utf-8")
        except OSError:
            raw = ""
        status = int(error.code)
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        detail = None
        if isinstance(parsed, dict):
            detail = str(parsed.get("detail") or parsed.get("error_code") or raw[:300])
        else:
            detail = raw[:300] or str(error)
        return status, parsed if isinstance(parsed, dict) else None, detail
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, None, str(error)

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return status, None, "RESPONSE_NOT_JSON"
    if not isinstance(parsed, dict):
        return status, None, "RESPONSE_NOT_OBJECT"
    return status, parsed, None


def write_observation(
    repo_root: Path,
    observation: Mapping[str, Any],
) -> str | None:
    """Write non-authoritative provider observation under runtime tmp."""
    tmp = repo_root / ".ai" / "runtime" / "tmp"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        path = tmp / "last_provider_session_observation.json"
        path.write_text(
            json.dumps(dict(observation), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return str(path)
    except OSError:
        return None


def _make_observer_session_ref(provider: str, session_ref: str) -> str:
    prefix = PROVIDER_OBS_PREFIX.get(provider.upper(), provider.lower())
    return f"{prefix}:{session_ref}"


def patch_mode_current_anchor(
    repo_root: Path,
    *,
    provider: str,
    session_ref: str,
    mode: str,
) -> dict[str, Any]:
    """Patch observer_session_ref into mode_current_anchor.snapshot_json.

    Operates directly on project_runtime.sqlite3 — no Universe server needed.
    Best-effort: returns a status dict, never raises.
    """
    store = repo_root / ".ai" / "runtime" / "state" / "project_runtime.sqlite3"
    if not store.is_file():
        return {"status": "ANCHOR_STORE_ABSENT"}
    observer_ref = _make_observer_session_ref(provider, session_ref)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(store.resolve()), timeout=2.0)
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "mode_current_anchor" not in tables:
            return {"status": "ANCHOR_TABLE_ABSENT"}
        row = connection.execute(
            "SELECT snapshot_json FROM mode_current_anchor WHERE mode = ?",
            (mode,),
        ).fetchone()
        if row is None:
            return {"status": "ANCHOR_ROW_ABSENT", "mode": mode}
        try:
            payload = json.loads(str(row["snapshot_json"] or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        inner = payload.get("snapshot")
        if isinstance(inner, dict):
            inner["observer_session_ref"] = observer_ref
            payload["snapshot"] = inner
        else:
            payload["observer_session_ref"] = observer_ref
        connection.execute(
            "UPDATE mode_current_anchor SET snapshot_json = ? WHERE mode = ?",
            (json.dumps(payload, separators=(",", ":")), mode),
        )
        connection.commit()
        return {
            "status": "ANCHOR_PATCHED",
            "mode": mode,
            "observer_session_ref": observer_ref,
        }
    except (OSError, sqlite3.Error) as error:
        return {"status": "ANCHOR_PATCH_FAILED", "detail": str(error)}
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Best-effort Universe session-ref inject after MODE_CHANGE / SessionStart"
        )
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--project-id", default="")
    parser.add_argument("--mode", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--session-ref", default="")
    parser.add_argument("--provider-session-ref", default="")
    parser.add_argument("--room-type", default="PROJECT")
    parser.add_argument("--slot-role", default="MASTER")
    parser.add_argument("--node", default="")
    parser.add_argument(
        "--make-default",
        dest="make_default",
        action="store_true",
        default=None,
    )
    parser.add_argument(
        "--no-make-default",
        dest="make_default",
        action="store_false",
    )
    parser.add_argument("--state-file", type=Path, default=None)
    parser.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read Claude/Codex hook JSON from stdin for session_id",
    )
    parser.add_argument(
        "--trigger",
        default="manual",
        help="Label for logs: mode_change | session_start | manual",
    )
    parser.add_argument(
        "--update-session-md",
        action="store_true",
        help="Deprecated compatibility flag; returns DEPRECATED_ANCHOR_DB_ONLY",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Non-zero exit when inject cannot run (default: always 0)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout for lifecycle hosts that validate hook output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve inputs and print plan without HTTP inject",
    )
    return parser


def run_hook(
    args: argparse.Namespace,
    *,
    environment: Mapping[str, str] | None = None,
    stdin_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    env = os.environ if environment is None else environment
    repo_root = args.repo_root.expanduser().resolve()
    # Anchor databases own live session coordinates. The Markdown companions
    # are compatibility notices only and are never read by this hook.
    session_fields: dict[str, str] = {}

    if stdin_payload is None and args.from_stdin:
        stdin_payload = _read_stdin_json()

    provider, session_ref, ref_source = resolve_provider_and_ref(
        args=args,
        stdin_payload=stdin_payload,
        session_fields=session_fields,
        environment=env,
        repo_root=repo_root,
    )
    project_id = resolve_project_id(
        args=args,
        session_fields=session_fields,
        environment=env,
        repo_root=repo_root,
    )
    if args.node:
        node = str(args.node).strip()
    else:
        node = project_id or "CONDUCTOR"
    mode = resolve_mode(args=args, session_fields=session_fields, environment=env)
    room_type = str(args.room_type or "PROJECT").strip().upper() or "PROJECT"
    slot_role = str(args.slot_role or "MASTER").strip().upper() or "MASTER"
    if args.make_default is None:
        make_default = slot_role == "MASTER" and room_type == "PROJECT"
    else:
        make_default = bool(args.make_default)

    base = {
        "trigger": args.trigger,
        "repo_root": str(repo_root),
        "provider": provider,
        "session_ref": session_ref,
        "ref_source": ref_source,
        "project_id": project_id,
        "node": node,
        "mode": mode,
        "room_type": room_type,
        "slot_role": slot_role,
        "make_default": make_default,
    }

    if not provider or provider not in PROVIDERS:
        return _result(
            "SKIPPED",
            detail="provider not resolved",
            **base,
        )
    if not session_ref:
        return _result(
            "SKIPPED",
            detail="session_ref not resolved",
            **base,
        )
    if not project_id:
        return _result(
            "SKIPPED",
            detail="project_id not resolved",
            **base,
        )

    supervisor_session_id = str(
        env.get("UNIVERSE_SUPERVISOR_SESSION_ID") or ""
    ).strip()
    observation = {
        "schema": "universe.provider-session-observation.v1",
        "observed_at": utc_now(),
        "provider": provider,
        "provider_session_ref": session_ref,
        "ref_source": ref_source,
        "project_id": project_id,
        "node": node,
        "mode": mode,
        "trigger": args.trigger,
        "authority": "UNASSIGNED",
        "execution_assignment": "UNASSIGNED",
    }
    observation_path = write_observation(repo_root, observation)
    session_md_status = (
        "DEPRECATED_ANCHOR_DB_ONLY" if args.update_session_md else "NOT_REQUESTED"
    )

    # Patch mode_current_anchor.snapshot_json with observer_session_ref.
    # Local SQLite write — runs even when Universe is offline.
    anchor_patch = patch_mode_current_anchor(
        repo_root,
        provider=provider,
        session_ref=session_ref,
        mode=mode,
    )

    inject_body = {
        "project_id": project_id,
        "node": node,
        "mode": mode,
        "room_type": room_type,
        "slot_role": slot_role,
        "provider": provider,
        "provider_session_ref": session_ref,
        "make_default": make_default,
        "bounded_summary": f"Hook inject ({args.trigger})",
        # Product label for PROJECT room MASTER slot (UI Project Master room).
        "display_name": (
            "Project Master"
            if room_type == "PROJECT" and slot_role == "MASTER"
            else ""
        ),
    }
    if supervisor_session_id:
        inject_body["supervisor_session_id"] = supervisor_session_id

    if args.dry_run:
        return _result(
            "DRY_RUN",
            detail="resolved inject payload; HTTP not called",
            observation_path=observation_path,
            session_md_status=session_md_status,
            anchor_patch=anchor_patch,
            inject_body=inject_body,
            **base,
        )

    state_path = (
        args.state_file.expanduser()
        if args.state_file is not None
        else default_state_path()
    )
    endpoint, token, state_error = load_server_connection(state_path)
    if endpoint is None:
        return _result(
            "OFFLINE",
            detail=state_error or "Universe server state unavailable",
            state_file=str(state_path),
            observation_path=observation_path,
            session_md_status=session_md_status,
            anchor_patch=anchor_patch,
            **base,
        )
    if not endpoint_reachable(endpoint):
        return _result(
            "OFFLINE",
            detail="Universe loopback endpoint not reachable",
            endpoint=endpoint,
            state_file=str(state_path),
            observation_path=observation_path,
            session_md_status=session_md_status,
            anchor_patch=anchor_patch,
            **base,
        )

    status_code, response, error_detail = post_inject(
        endpoint=endpoint,
        token=token,
        payload=inject_body,
    )
    if status_code and 200 <= status_code < 300 and isinstance(response, dict):
        return _result(
            "INJECTED",
            detail="session ref injected into Universe",
            endpoint=endpoint,
            http_status=status_code,
            observation_path=observation_path,
            session_md_status=session_md_status,
            anchor_patch=anchor_patch,
            inject_response={
                "status": response.get("status"),
                "supervisor_session_created": response.get(
                    "supervisor_session_created"
                ),
                "make_default": response.get("make_default"),
                "resident_runtime_reload": response.get("resident_runtime_reload"),
                "binding_id": (response.get("binding") or {}).get("binding_id")
                if isinstance(response.get("binding"), dict)
                else None,
                "room_id": (response.get("room") or {}).get("room_id")
                if isinstance(response.get("room"), dict)
                else None,
                "bridge_line": response.get("bridge_line"),
            },
            **base,
        )

    return _result(
        "INJECT_FAILED",
        detail=error_detail or f"HTTP {status_code}",
        endpoint=endpoint,
        http_status=status_code or None,
        observation_path=observation_path,
        session_md_status=session_md_status,
        anchor_patch=anchor_patch,
        inject_body=inject_body,
        **base,
    )


def _toml_array(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def _codex_hook_block(python_exe: str, script_path: str) -> str:
    import subprocess as _sp
    cmd_str = _sp.list2cmdline([python_exe, script_path, "--repo-root", ".", "--provider", "CODEX", "--from-stdin", "--trigger", "session_start", "--quiet"])
    return (
        "\n[[hooks.SessionStart]]\n"
        'matcher = "*"\n'
        "[[hooks.SessionStart.hooks]]\n"
        'type = "command"\n'
        f"command = {json.dumps(cmd_str)}\n"
    )


def _grok_hook_payload(python_exe: str, script_path: str) -> dict[str, Any]:
    cmd = " ".join([
        python_exe, script_path,
        "--repo-root", ".",
        "--provider", "GROK",
        "--from-stdin",
        "--trigger", "session_start",
    ])
    return {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": cmd}]}
            ]
        }
    }


def _claude_settings_hook(script_path: str) -> dict[str, Any]:
    return {
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python {script_path} --repo-root . --provider CLAUDE --from-stdin --trigger session_start",
                        }
                    ]
                }
            ]
        }
    }


def _merge_claude_settings(path: Path, hook_entry: dict[str, Any]) -> str:
    """Merge SessionStart hook into .claude/settings.json; return status string."""
    import json as _json
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = {}
    hooks = existing.setdefault("hooks", {})
    starts = hooks.setdefault("SessionStart", [])
    new_cmd = hook_entry["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    script_marker = "universe_session_inject_hook.py"
    updated = False
    for block in starts:
        for hook in block.get("hooks") or []:
            command = str(hook.get("command") or "")
            if script_marker not in command:
                continue
            if command != new_cmd:
                hook["command"] = new_cmd
                updated = True
    if updated:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return "UPDATED"
    if any(
        script_marker in (h.get("command") or "")
        for block in starts
        for h in (block.get("hooks") or [])
    ):
        return "CURRENT"
    starts.append(hook_entry["hooks"]["SessionStart"][0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return "WRITTEN"


def _write_grok_session_hook(hooks_dir: Path, python_exe: str, script_path: str) -> dict[str, Any]:
    hook_path = hooks_dir / "session-start.json"
    try:
        existing_json: dict[str, Any] = {}
        if hook_path.is_file():
            try:
                existing_json = json.loads(hook_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                existing_json = {}
        marker = "universe_session_inject_hook.py"
        payload = _grok_hook_payload(python_exe, script_path)
        desired_cmd = payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        found = False
        updated = False
        starts = existing_json.setdefault("hooks", {}).setdefault("SessionStart", [])
        for block in starts:
            for item in block.get("hooks") or []:
                command = str(item.get("command") or "")
                if marker not in command:
                    continue
                found = True
                if command != desired_cmd:
                    item["command"] = desired_cmd
                    updated = True
        if found:
            if updated:
                hooks_dir.mkdir(parents=True, exist_ok=True)
                hook_path.write_text(json.dumps(existing_json, indent=2) + "\n", encoding="utf-8")
                return {"status": "UPDATED", "path": str(hook_path)}
            return {"status": "CURRENT", "path": str(hook_path)}
        starts.extend(payload["hooks"]["SessionStart"])
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(json.dumps(existing_json, indent=2) + "\n", encoding="utf-8")
        return {"status": "WRITTEN", "path": str(hook_path)}
    except OSError as error:
        return {"status": "ERROR", "detail": str(error), "path": str(hook_path)}


def repair_claude_compat_observer_stamps(
    repo_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Rewrite claude-code:<id> observers that belong to a Grok session."""
    env = os.environ if environment is None else environment
    store = Path(repo_root) / ".ai" / "runtime" / "state" / "project_runtime.sqlite3"
    repairs: list[dict[str, Any]] = []
    if not store.is_file():
        return [{"status": "ANCHOR_STORE_ABSENT"}]
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(store.resolve()), timeout=2.0)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT mode, snapshot_json FROM mode_current_anchor"
        ).fetchall()
    except (OSError, sqlite3.Error) as error:
        return [{"status": "ANCHOR_READ_FAILED", "detail": str(error)}]
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass
    for row in rows:
        mode = str(row["mode"] or "").strip().upper()
        try:
            payload = json.loads(str(row["snapshot_json"] or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        inner = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
        observer = str((inner or {}).get("observer_session_ref") or "").strip()
        if not observer.lower().startswith("claude-code:"):
            continue
        session_ref = observer.split(":", 1)[1].strip()
        if not session_ref:
            continue
        if not _grok_session_dir_exists(Path(repo_root), env, session_ref):
            repairs.append(
                {
                    "status": "SKIPPED_NO_GROK_SESSION",
                    "mode": mode,
                    "observer_session_ref": observer,
                }
            )
            continue
        patched = patch_mode_current_anchor(
            Path(repo_root),
            provider="GROK",
            session_ref=session_ref,
            mode=mode,
        )
        repairs.append(
            {
                "status": "REPAIRED" if patched.get("status") == "ANCHOR_PATCHED" else patched.get("status"),
                "mode": mode,
                "from": observer,
                "to": f"grok-cli:{session_ref}",
                "session_ref": session_ref,
                "anchor_patch": patched,
            }
        )
    return repairs


def setup_provider_hooks(
    repo_root: Path,
    *,
    global_: bool = False,
    providers: list[str] | None = None,
    python_exe: str | None = None,
    script_path: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Write SessionStart hook configs for Codex and/or Grok (and Claude).

    Returns a dict with per-provider status strings.
    """
    import sys as _sys
    py = python_exe or _sys.executable
    sp = script_path or str(Path(__file__).resolve())
    targets = [p.upper() for p in (providers or ["CODEX", "GROK", "CLAUDE"])]
    results: dict[str, Any] = {}

    home = Path.home()

    if "CODEX" in targets:
        config_dir = home / ".codex" if global_ else repo_root / ".codex"
        config_path = config_dir / "config.toml"
        try:
            existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
            marker = "universe_session_inject_hook.py"
            if marker in existing:
                results["CODEX"] = {"status": "ALREADY_PRESENT", "path": str(config_path)}
            else:
                config_dir.mkdir(parents=True, exist_ok=True)
                with config_path.open("a", encoding="utf-8") as f:
                    f.write(_codex_hook_block(py, sp))
                results["CODEX"] = {"status": "WRITTEN", "path": str(config_path)}
        except OSError as e:
            results["CODEX"] = {"status": "ERROR", "detail": str(e)}

    if "GROK" in targets:
        if global_:
            results["GROK"] = _write_grok_session_hook(home / ".grok" / "hooks", py, sp)
        results["GROK_PROJECT"] = _write_grok_session_hook(
            repo_root / ".grok" / "hooks", py, sp
        )
    if "CLAUDE" in targets:
        try:
            results["CLAUDE_PROJECT"] = {
                "status": _merge_claude_settings(
                    repo_root / ".claude" / "settings.json",
                    _claude_settings_hook(sp),
                ),
                "path": str((repo_root / ".claude" / "settings.json")),
            }
        except OSError as error:
            results["CLAUDE_PROJECT"] = {"status": "ERROR", "detail": str(error)}
        if global_:
            settings_path = home / ".claude" / "settings.json"
            try:
                results["CLAUDE"] = {
                    "status": _merge_claude_settings(
                        settings_path, _claude_settings_hook(sp)
                    ),
                    "path": str(settings_path),
                }
            except OSError as error:
                results["CLAUDE"] = {"status": "ERROR", "detail": str(error)}

    repairs = repair_claude_compat_observer_stamps(repo_root, environment=environment)
    return {
        "status": "SETUP_HOOKS_DONE",
        "global": global_,
        "providers": results,
        "repairs": repairs,
    }


def main(argv: list[str] | None = None) -> int:
    # Subcommand dispatch: first positional arg "setup-hooks" branches early.
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] == "setup-hooks":
        sub = argparse.ArgumentParser(description="Write SessionStart hook configs for Codex/Grok/Claude")
        sub.add_argument("--repo-root", type=Path, default=Path.cwd())
        sub.add_argument("--global", dest="global_", action="store_true", help="Write to global home config dirs")
        sub.add_argument("--providers", nargs="+", default=["CODEX", "GROK", "CLAUDE"], metavar="PROVIDER")
        sub.add_argument("--python", dest="python_exe", default="")
        sub.add_argument("--script", dest="script_path", default="")
        args = sub.parse_args(raw[1:])
        result = setup_provider_hooks(
            args.repo_root.expanduser().resolve(),
            global_=args.global_,
            providers=args.providers,
            python_exe=args.python_exe or None,
            script_path=args.script_path or None,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    parser = build_parser()
    args = parser.parse_args(raw)
    result = run_hook(args)
    codex_session_start = (
        str(args.provider or "").strip().upper() == "CODEX"
        and args.from_stdin
        and str(args.trigger or "").strip().lower() == "session_start"
    )
    if not (args.quiet or codex_session_start):
        print(json.dumps(result, indent=2, sort_keys=True))
    if args.strict and result.get("status") not in {"INJECTED", "DRY_RUN"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
