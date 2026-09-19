"""Master-side launch, direction and collection of an independent Task Frame Host.

The Master (the Parent) decides the route, the persona and the write scope; this
module turns that decision into a Boss room plus a detached Host process, posts
the Master's directives into the room, and reports a Host's liveness.  It never
runs a role itself and never judges a result.

Every check here is mechanical: the run belongs to the caller, the Todo is live
and on the run's node, and the write scope is exact CREATE/MODIFY files inside
the project.  Whether a Worker is needed at all, and how wide the scope is,
stays the Master's call.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from task_frame_host import DIRECTIVE_SCHEMA, DIRECTIVES, ROLES
from task_frame_host_main import launch as launch_host
from task_frame_host_main import pid_alive

WORKER_OPERATIONS = frozenset({"CREATE", "MODIFY"})
LIVE_TODO_STATES = frozenset({"READY", "IN_PROGRESS", "REVIEW", "PAUSED"})


class LaunchError(Exception):
    def __init__(self, code: str, detail: str, status: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def frame_id_for(run_id: str, request_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{request_id}".encode("utf-8")).hexdigest()[:20]
    return f"host_{digest}"


def host_dir(state_root: Path, frame_id: str) -> Path:
    return Path(state_root) / frame_id


def validate_write_scope(scope: Any, project_root: Path) -> dict[str, Any]:
    """The Master's write scope, or NONE.  Exact files inside the project only."""

    if scope is None:
        return {"repository_write_scope": "NONE", "mutation_scope": {"operations": [], "targets": []}}
    if not isinstance(scope, Mapping):
        raise LaunchError("TASK_FRAME_SCOPE_INVALID", "worker_write_scope must be an object")
    kind = str(scope.get("repository_write_scope") or "").upper()
    if kind == "NONE":
        return {"repository_write_scope": "NONE", "mutation_scope": {"operations": [], "targets": []}}
    if kind != "BOUNDED":
        raise LaunchError("TASK_FRAME_SCOPE_INVALID", "repository_write_scope must be NONE or BOUNDED")
    mutation = scope.get("mutation_scope")
    if not isinstance(mutation, Mapping):
        raise LaunchError("TASK_FRAME_SCOPE_INVALID", "BOUNDED needs a mutation_scope")
    operations = [str(item).upper() for item in mutation.get("operations") or []]
    targets = [str(item) for item in mutation.get("targets") or []]
    if not operations or not targets or set(operations) - WORKER_OPERATIONS:
        raise LaunchError(
            "TASK_FRAME_SCOPE_INVALID",
            "a Worker may be granted CREATE/MODIFY on exact targets only (no DELETE or MOVE)",
        )
    root = Path(project_root).resolve()
    normalized: list[str] = []
    for target in targets:
        if any(mark in target for mark in "*?[]"):
            raise LaunchError("TASK_FRAME_SCOPE_INVALID", f"targets must be exact files, not patterns: {target}")
        path = Path(target)
        if not path.is_absolute():
            raise LaunchError("TASK_FRAME_SCOPE_INVALID", f"targets must be absolute paths: {target}")
        resolved = path.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise LaunchError("TASK_FRAME_SCOPE_OUTSIDE_PROJECT", f"target is outside the project: {target}")
        if str(resolved) not in normalized:
            normalized.append(str(resolved))
    return {
        "repository_write_scope": "BOUNDED",
        "mutation_scope": {"operations": list(dict.fromkeys(operations)), "targets": normalized},
    }


def check_run_and_todo(run: Mapping[str, Any], owner_ref: str, todo: Mapping[str, Any]) -> None:
    if str(run.get("state") or "") != "RUNNING":
        raise LaunchError("TASK_FRAME_RUN_NOT_RUNNING", "a Task Frame is launched only for a RUNNING run")
    if str(run.get("session_anchor_ref") or "") != owner_ref:
        raise LaunchError("TASK_FRAME_OWNER_MISMATCH", "only the run's owning Session Anchor may launch a Task Frame")
    if todo.get("archived_at") or str(todo.get("state") or "") not in LIVE_TODO_STATES:
        raise LaunchError("TASK_FRAME_TODO_NOT_LIVE", "the Todo is finished, blocked or archived")
    if str(todo.get("project_id") or "") != str(run.get("project_id") or ""):
        raise LaunchError("TASK_FRAME_TODO_PROJECT_MISMATCH", "the Todo belongs to another project")
    node = str(run.get("node_ref") or "")
    if node and str(todo.get("node_ref") or "") != node:
        raise LaunchError("TASK_FRAME_TODO_NODE_MISMATCH", "the Todo is not on the run's node")


def host_status(state_root: Path, frame_id: str) -> dict[str, Any]:
    folder = host_dir(state_root, frame_id)
    heartbeat: Any = None
    for name in ("heartbeat.json", "launched.json"):
        try:
            heartbeat = json.loads((folder / name).read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    if not isinstance(heartbeat, Mapping):
        return {"task_frame_id": frame_id, "known": False, "alive": False}
    pid = int(heartbeat.get("pid") or 0)
    return {
        "task_frame_id": frame_id,
        "known": True,
        "alive": pid_alive(pid),
        "pid": pid,
        "phase": heartbeat.get("phase"),
        "exit_reason": heartbeat.get("exit_reason"),
        "room_id": heartbeat.get("room_id"),
        "updated_at": heartbeat.get("updated_at"),
    }


def launch_frame(
    *,
    run: Mapping[str, Any],
    value: Mapping[str, Any],
    todo: Mapping[str, Any],
    project_root: Path,
    repository_root: Path,
    persona_text: str,
    runtime_binding: Mapping[str, Any],
    base_url: str,
    rooms: Any,
    bus_to: Mapping[str, Any],
    bus_from: Mapping[str, Any],
    state_root: Path,
    launcher: Callable[..., int] = launch_host,
) -> dict[str, Any]:
    run_id = str(run["run_id"])
    check_run_and_todo(run, str(value["owner_ref"]), todo)
    scope = validate_write_scope(value.get("worker_write_scope"), project_root)
    first_role = str(value.get("first_role") or "WORKER").upper()
    if first_role not in ROLES:
        raise LaunchError("TASK_FRAME_ROLE_INVALID", "first_role must be WORKER or REVIEWER")
    frame_id = frame_id_for(run_id, str(value["request_id"]))
    existing = host_status(state_root, frame_id)
    if existing["known"]:
        # A replay never starts a second Host or re-runs directives; a new
        # request_id is a new frame.
        return {"status": "TASK_FRAME_HOST_REPLAYED", **existing}
    room = rooms.create_boss_room(
        project_id=str(run["project_id"]),
        task_frame_id=frame_id,
        title=f"Task Frame / {todo.get('title') or todo.get('todo_id')}",
    )
    spec = {
        "base_url": base_url,
        "room_id": room["room_id"],
        "task_frame_id": frame_id,
        "todo_id": todo["todo_id"],
        "first_role": first_role,
        "bus_to": dict(bus_to),
        "bus_from": dict(bus_from),
        "thread_id": f"persona-{run_id}-host-{frame_id}",
        "runner": "task_frame_host_runner:make",
        "repository_root": str(repository_root),
        "provider": str(value["provider"]).upper(),
        "runtime_binding": dict(runtime_binding),
        "source_ref": f"universe://todo/{todo['todo_id']}",
        "todo": {"title": todo.get("title"), "detail": todo.get("detail")},
        "persona_text": persona_text,
        "worker_write_scope": scope,
    }
    if value.get("idle_timeout_seconds"):
        spec["idle_timeout_seconds"] = float(value["idle_timeout_seconds"])
    folder = host_dir(state_root, frame_id)
    folder.mkdir(parents=True, exist_ok=True)
    pid = launcher(spec, folder / "spec.json", folder / "heartbeat.json")
    # Written at once so a replay before the first heartbeat is still a replay.
    (folder / "launched.json").write_text(
        json.dumps({"pid": pid, "room_id": room["room_id"], "task_frame_id": frame_id}), encoding="utf-8"
    )
    return {
        "status": "TASK_FRAME_HOST_LAUNCHED",
        "task_frame_id": frame_id,
        "room_id": room["room_id"],
        "pid": pid,
        "first_role": first_role,
        "repository_write_scope": scope["repository_write_scope"],
        "mutation_scope": scope["mutation_scope"],
    }


def post_directive(
    *, rooms: Any, state_root: Path, task_frame_id: str, value: Mapping[str, Any]
) -> dict[str, Any]:
    directive = str(value.get("directive") or "").upper()
    if directive not in DIRECTIVES:
        raise LaunchError("TASK_FRAME_DIRECTIVE_INVALID", "directive must be RUN_ROLE, REWORK or DONE")
    role = str(value.get("target_role") or value.get("role") or "").upper() or None
    if directive != "DONE" and role not in ROLES:
        raise LaunchError("TASK_FRAME_ROLE_INVALID", "RUN_ROLE and REWORK need target_role WORKER or REVIEWER")
    status = host_status(state_root, task_frame_id)
    if not status["known"] or not status.get("room_id"):
        raise LaunchError("TASK_FRAME_HOST_UNKNOWN", "no Host was launched for this Task Frame", 404)
    body: dict[str, Any] = {"schema": DIRECTIVE_SCHEMA, "directive": directive}
    if role:
        body["role"] = role
    if isinstance(value.get("feedback"), str) and value["feedback"].strip():
        body["feedback"] = value["feedback"]
    message = rooms.post_message(
        str(status["room_id"]),
        {
            "author_role": "MASTER",
            "body_text": json.dumps(body, ensure_ascii=False),
            "idempotency_key": f"directive:{value['request_id']}",
        },
    )
    return {
        "status": "TASK_FRAME_DIRECTIVE_POSTED",
        "task_frame_id": task_frame_id,
        "message_id": message.get("message_id"),
        "host_alive": status["alive"],
    }
