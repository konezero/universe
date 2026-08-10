"""In-memory SSE event hubs with bounded retention and cursor delivery."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Mapping

from .connection import UniverseError


PROJECT_ROOM_STREAM_SCHEMA = "universe.project-room-stream.v1"
CONDUCTOR_ROOM_STREAM_SCHEMA = "universe.conductor-room-stream.v1"
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _project_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UniverseError("REQUEST_INVALID", "project_id must be non-empty text")
    project_id = value.strip()
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise UniverseError(
            "PROJECT_ID_INVALID",
            "project_id must use letters, digits, dot, underscore, or hyphen",
        )
    return project_id


class ProjectRoomEventHub:
    def __init__(self, *, retained_events: int = 512) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._retained_events = max(32, int(retained_events))
        self._events: dict[str, deque[dict[str, Any]]] = {}

    def cursor(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized_project = _project_id(project_id)
        with self._condition:
            self._sequence += 1
            event = {
                "schema": PROJECT_ROOM_STREAM_SCHEMA,
                "event_id": self._sequence,
                "project_id": normalized_project,
                "emitted_at": _utc_now(),
                "payload": dict(payload),
            }
            events = self._events.setdefault(
                normalized_project,
                deque(maxlen=self._retained_events),
            )
            events.append(event)
            self._condition.notify_all()
            return dict(event)

    def wait(
        self,
        project_id: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        normalized_project = _project_id(project_id)
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._condition:
            while True:
                events = [
                    dict(event)
                    for event in self._events.get(normalized_project, ())
                    if int(event["event_id"]) > after_event_id
                ]
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


class ConductorRoomEventHub:
    """Transient provider stream events for the single Conductor room."""

    def __init__(self, *, retained_events: int = 512) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._events: deque[dict[str, Any]] = deque(
            maxlen=max(32, int(retained_events))
        )

    def cursor(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._condition:
            self._sequence += 1
            event = {
                "schema": CONDUCTOR_ROOM_STREAM_SCHEMA,
                "event_id": self._sequence,
                "emitted_at": _utc_now(),
                "payload": dict(payload),
            }
            self._events.append(event)
            self._condition.notify_all()
            return dict(event)

    def wait(
        self,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._condition:
            while True:
                events = [
                    dict(event)
                    for event in self._events
                    if int(event["event_id"]) > after_event_id
                ]
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


__all__ = ["ConductorRoomEventHub", "ProjectRoomEventHub"]
