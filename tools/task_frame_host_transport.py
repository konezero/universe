"""HTTP transport for the Task Frame Host ports.

The Host lifecycle (``task_frame_host``) only sees small ports; this module
binds them to the running Universe server over loopback HTTP.  The server does
not track the Host: it is an independent process that is handed a base URL, a
room id and the Master's Session Bus address.  Loopback is the only credential
the routes used here require.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence


class TransportError(RuntimeError):
    """A server call failed; the Host keeps the notice and retries."""


class _Http:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            error.close()
            raise TransportError(f"{method} {path} -> HTTP {error.code}") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise TransportError(f"{method} {path} failed: {error}") from error
        if not isinstance(payload, Mapping):
            raise TransportError(f"{method} {path} returned a non-object body")
        return payload


class HttpRoomPort:
    """Boss room: reports in, Master directives out, room state."""

    def __init__(self, base_url: str, room_id: str, *, author_binding_id: str | None = None) -> None:
        self._http = _Http(base_url)
        self._room = f"/v1/rooms/{room_id}"
        self._binding = author_binding_id

    def post_report(self, *, body_text: str, severity: str, idempotency_key: str) -> None:
        self._http.request(
            "POST",
            f"{self._room}/worker-report",
            {
                "body_text": body_text,
                "severity": severity,
                "idempotency_key": idempotency_key,
                "author_binding_id": self._binding,
            },
        )

    def call_master(self, *, reason: str) -> None:
        self._http.request("POST", f"{self._room}/call-master", {"reason": reason})

    def events_after(self, sequence: int) -> Sequence[Mapping[str, Any]]:
        payload = self._http.request("GET", f"{self._room}/events?after_sequence={int(sequence)}&limit=200")
        events = payload.get("events")
        return [event for event in events if isinstance(event, Mapping)] if isinstance(events, list) else []

    def is_open(self) -> bool:
        payload = self._http.request("GET", self._room)
        room = payload.get("room") if isinstance(payload.get("room"), Mapping) else payload
        return str(room.get("state") or "").upper() == "OPEN"


class HttpBusPort:
    """Wake the Master over the Session Bus (one INSTRUCTION-free notice)."""

    def __init__(self, base_url: str, *, to: Mapping[str, Any], sender: Mapping[str, Any], thread_id: str) -> None:
        self._http = _Http(base_url)
        self._to = dict(to)
        self._sender = dict(sender)
        self._thread_id = thread_id

    def notify(self, *, idempotency_key: str, body_text: str, payload: Mapping[str, Any]) -> None:
        self._http.request(
            "POST",
            "/v1/session-bus/messages",
            {
                "to": self._to,
                "from": self._sender,
                "kind": "INSTRUCTION",
                "protocol": "WORK",
                "notify": "NONE",
                "thread_id": self._thread_id,
                "idempotency_key": idempotency_key,
                "body_text": body_text + "\n" + json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
            },
        )


class HttpTodoPort:
    """Todo lifetime.  The server lists non-archived Todos only, so an absent
    Todo is reported as archived (the Host's terminal condition)."""

    def __init__(self, base_url: str, todo_id: str) -> None:
        self._http = _Http(base_url)
        self._todo_id = todo_id

    def state(self) -> tuple[str, bool]:
        payload = self._http.request("GET", "/v1/todos")
        todos = payload.get("todos")
        if not isinstance(todos, list):
            raise TransportError("GET /v1/todos returned no todo list")
        for todo in todos:
            if isinstance(todo, Mapping) and todo.get("todo_id") == self._todo_id:
                return str(todo.get("state") or ""), bool(todo.get("archived_at"))
        return "", True


def fetch_runtime_binding(base_url: str, run_id: str, task_frame_id: str) -> Mapping[str, Any]:
    """The server's current frame runtime binding for this Host's own frame."""

    payload = _Http(base_url, timeout=30.0).request(
        "POST",
        "/v1/actions",
        {
            "action_id": "persona.automation.host-binding",
            "request": {"run_id": run_id, "task_frame_id": task_frame_id},
        },
    )
    binding = payload.get("runtime_binding")
    if not isinstance(binding, Mapping):
        raise TransportError("host-binding returned no runtime_binding")
    return binding
