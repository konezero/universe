"""Direct, provider-native one-to-one session conversations.

The browser addresses a session only by an opaque chat key. Provider session
references, transcript paths, and repository paths stay behind the resolver.
Room queues are deliberately not used by this service.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import threading
import time
from typing import Any, Callable, Mapping, Protocol
import uuid

from agent_session_gateway import AgentSessionError, normalize_permission_request


PROVIDER_SESSION_STREAM_SCHEMA = "universe.provider-session-stream.v1"
PROVIDER_SESSION_MESSAGE_SCHEMA = "universe.provider-session-message.v1"
PROVIDER_SESSION_SNAPSHOT_SCHEMA = "universe.provider-session-snapshot.v1"
WORK_STATUS_SCHEMA = "universe.work-status-notification.v1"
PROVIDER_ACTION_SCHEMA = "universe.provider-session-action.v1"
CHAT_KEY_PATTERN = re.compile(r"^provider_chat_[0-9a-f]{24}$")
ACTION_ID_PATTERN = re.compile(r"^provider_action_[0-9a-f]{24}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderSessionError(
            "PROVIDER_SESSION_REQUEST_INVALID",
            f"{field} must be non-empty text",
            400,
        )
    return value.strip()


def _chat_key(value: Any) -> str:
    key = _text(value, "chat_key")
    if not CHAT_KEY_PATTERN.fullmatch(key):
        raise ProviderSessionError(
            "PROVIDER_SESSION_CHAT_KEY_INVALID",
            "chat_key is not a valid opaque Provider Session key",
            400,
        )
    return key


def _action_id(value: Any) -> str:
    action_id = _text(value, "action_id")
    if not ACTION_ID_PATTERN.fullmatch(action_id):
        raise ProviderSessionError(
            "PROVIDER_SESSION_ACTION_ID_INVALID",
            "action_id is not a valid Provider Session Action identifier",
            400,
        )
    return action_id


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


class ProviderSessionError(RuntimeError):
    def __init__(self, code: str, detail: str, status: int = 409) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


class ProviderSessionHost(Protocol):
    store: Any

    def prepare(self, provider: str) -> Mapping[str, Any]: ...

    def status(self) -> Mapping[str, Any]: ...

    def reply_stream(
        self,
        provider: str,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> Mapping[str, Any]: ...

    def set_permission_requester(
        self, requester: Callable[[Mapping[str, Any]], str | None]
    ) -> None: ...

    def close(self) -> None: ...


class ProviderActionStore(Protocol):
    def record_provider_session_action(
        self, chat_key: str, action: Mapping[str, Any], *, retain: int
    ) -> Mapping[str, Any]: ...

    def list_provider_session_actions(
        self, chat_key: str, *, limit: int
    ) -> list[dict[str, Any]]: ...

    def delete_provider_session_action(
        self, chat_key: str, action_id: str
    ) -> Mapping[str, Any] | None: ...


class ProviderSessionEventHub:
    """Bounded, process-local SSE history keyed by opaque chat key."""

    def __init__(self, *, retained_events: int = 512) -> None:
        self._condition = threading.Condition()
        self._sequence = 0
        self._retained_events = max(32, int(retained_events))
        self._events: dict[str, deque[dict[str, Any]]] = {}

    def cursor(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, chat_key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        key = _chat_key(chat_key)
        with self._condition:
            self._sequence += 1
            event = {
                "schema": PROVIDER_SESSION_STREAM_SCHEMA,
                "event_id": self._sequence,
                "chat_key": key,
                "emitted_at": _utc_now(),
                "payload": dict(payload),
            }
            self._events.setdefault(
                key, deque(maxlen=self._retained_events)
            ).append(event)
            self._condition.notify_all()
            return _json_copy(event)

    def wait(
        self,
        chat_key: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> list[dict[str, Any]]:
        key = _chat_key(chat_key)
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        with self._condition:
            while True:
                events = [
                    _json_copy(event)
                    for event in self._events.get(key, ())
                    if int(event["event_id"]) > int(after_event_id)
                ]
                if events:
                    return events
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)


@dataclass
class _HostHandle:
    descriptor: dict[str, Any]
    host: ProviderSessionHost
    connection: dict[str, Any]


class ProviderSessionService:
    """Own resident provider handles without copying Room or transcript state."""

    def __init__(
        self,
        *,
        resolver: Callable[[str], Mapping[str, Any]],
        host_factory: Callable[
            [Mapping[str, Any], Callable[[Mapping[str, Any]], str | None]],
            ProviderSessionHost,
        ],
        retained_messages: int = 200,
        retained_idempotency: int | None = None,
        retained_permissions: int | None = None,
        permission_timeout_seconds: float = 600.0,
        action_store: ProviderActionStore | None = None,
    ) -> None:
        self.resolver = resolver
        self.host_factory = host_factory
        self.action_store = action_store
        self.events = ProviderSessionEventHub()
        self.retained_messages = max(16, int(retained_messages))
        self.retained_idempotency = max(
            1,
            int(
                retained_idempotency
                if retained_idempotency is not None
                else self.retained_messages * 4
            ),
        )
        self.retained_permissions = max(
            1,
            int(
                retained_permissions
                if retained_permissions is not None
                else self.retained_messages
            ),
        )
        self.permission_timeout_seconds = max(0.1, float(permission_timeout_seconds))
        self._lock = threading.RLock()
        self._handles: dict[str, _HostHandle] = {}
        self._messages: dict[str, deque[dict[str, Any]]] = {}
        self._permissions: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._permission_waiters: dict[str, dict[str, Any]] = {}
        self._work_statuses: dict[str, dict[str, Any]] = {}
        self._actions: dict[str, deque[dict[str, Any]]] = {}
        self._active_threads: dict[str, threading.Thread] = {}
        self._active_message_ids: dict[str, str] = {}
        self._cancelled_message_ids: set[str] = set()
        self._idempotency: OrderedDict[
            tuple[str, str], dict[str, Any]
        ] = OrderedDict()
        self._closed = threading.Event()

    def snapshot(self, chat_key: str) -> dict[str, Any]:
        key = _chat_key(chat_key)
        descriptor = self._resolve(key)
        with self._lock:
            handle = self._handles.get(key)
            connection = _json_copy(
                handle.connection
                if handle is not None
                else self._public_connection(None)
            )
            messages = list(self._messages.get(key, ()))
            permissions = [
                item
                for item in self._permissions.values()
                if item["chat_key"] == key
            ]
            active_message_id = self._active_message_ids.get(key)
            work_status = _json_copy(self._work_statuses.get(key))
            actions = _json_copy(
                self.action_store.list_provider_session_actions(
                    key, limit=self.retained_messages
                )
                if self.action_store is not None
                else list(self._actions.get(key, ()))
            )
            state = (
                "CANCELLATION_REQUESTED"
                if active_message_id in self._cancelled_message_ids
                else "ACTIVE" if active_message_id else "READY"
            )
        return {
            "schema": PROVIDER_SESSION_SNAPSHOT_SCHEMA,
            "status": "PROVIDER_SESSION_SNAPSHOT_COLLECTED",
            "state": state,
            "target": self._public_target(descriptor),
            "messages": _json_copy(messages),
            "permissions": _json_copy(permissions),
            "work_status": work_status,
            "actions": actions,
            "connection": connection,
            "transcript_owner": "PROVIDER",
            "room_queue_used": False,
        }

    def submit(
        self,
        chat_key: str,
        value: Mapping[str, Any],
        *,
        on_accepted: Callable[[Mapping[str, Any]], None] | None = None,
        on_terminal: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self._closed.is_set():
            raise ProviderSessionError(
                "PROVIDER_SESSION_SERVICE_CLOSED",
                "Provider Session service is closed",
                503,
            )
        key = _chat_key(chat_key)
        if not isinstance(value, Mapping):
            raise ProviderSessionError(
                "PROVIDER_SESSION_REQUEST_INVALID",
                "request body must be an object",
                400,
            )
        body = _text(value.get("body"), "body")
        if len(body) > 12000:
            raise ProviderSessionError(
                "PROVIDER_SESSION_MESSAGE_TOO_LARGE",
                "body exceeds the 12000 character limit",
                413,
            )
        idempotency_key = _text(value.get("idempotency_key"), "idempotency_key")
        descriptor = self._resolve(key)
        handle = self._ensure_handle(key, descriptor)
        with self._lock:
            duplicate = self._idempotency.get((key, idempotency_key))
            if duplicate is not None:
                self._idempotency.move_to_end((key, idempotency_key))
                return _json_copy(duplicate)
            if key in self._active_message_ids:
                raise ProviderSessionError(
                    "PROVIDER_SESSION_BUSY",
                    "The selected Provider Session is already processing a turn",
                    409,
                )
            user_message = self._message(key, "USER", body, "ACCEPTED")
            reply_message = self._message(key, "ASSISTANT", "", "STARTING")
            self._append_message(key, user_message)
            self._append_message(key, reply_message)
            self._active_message_ids[key] = reply_message["message_id"]
            worker = threading.Thread(
                target=self._run_turn,
                args=(
                    key,
                    descriptor,
                    handle,
                    user_message,
                    reply_message,
                    on_terminal,
                ),
                name=f"provider-session-{key[-8:]}",
                daemon=True,
            )
            self._active_threads[key] = worker
            result = {
                "schema": PROVIDER_SESSION_SNAPSHOT_SCHEMA,
                "status": "PROVIDER_SESSION_INPUT_ACCEPTED",
                "target": self._public_target(descriptor),
                "message": _json_copy(user_message),
                "reply": _json_copy(reply_message),
                "room_queue_used": False,
            }
            self._idempotency[(key, idempotency_key)] = result
            self._idempotency.move_to_end((key, idempotency_key))
            self._prune_idempotency_locked()
        if on_accepted is not None:
            try:
                on_accepted(_json_copy(reply_message))
            except Exception as error:  # internal transport boundary
                with self._lock:
                    self._active_message_ids.pop(key, None)
                    self._active_threads.pop(key, None)
                    reply_message["state"] = "FAILED"
                    reply_message["error_code"] = "PROVIDER_SESSION_ACCEPTED_CALLBACK_FAILED"
                    reply_message["updated_at"] = _utc_now()
                raise ProviderSessionError(
                    "PROVIDER_SESSION_ACCEPTED_CALLBACK_FAILED",
                    "Provider Session accepted callback failed",
                    503,
                ) from error
        self.events.publish(key, {"type": "PROVIDER_SESSION_MESSAGE", "message": user_message})
        self.events.publish(key, {"type": "PROVIDER_SESSION_MESSAGE", "message": reply_message})
        self._publish_work_status(key, reply_message["message_id"], "STARTED")
        try:
            worker.start()
        except RuntimeError as error:
            with self._lock:
                self._active_message_ids.pop(key, None)
                self._active_threads.pop(key, None)
                reply_message["state"] = "FAILED"
                reply_message["error_code"] = "PROVIDER_SESSION_THREAD_START_FAILED"
                reply_message["updated_at"] = _utc_now()
            self.events.publish(
                key,
                {"type": "PROVIDER_SESSION_MESSAGE", "message": reply_message},
            )
            self._publish_work_status(
                key,
                reply_message["message_id"],
                "FAILED",
                error_code="PROVIDER_SESSION_THREAD_START_FAILED",
            )
            if on_terminal is not None:
                try:
                    on_terminal(_json_copy(reply_message))
                except Exception:
                    pass
            raise ProviderSessionError(
                "PROVIDER_SESSION_THREAD_START_FAILED",
                "Provider Session turn could not start",
                503,
            ) from error
        return _json_copy(result)

    def cancel(self, chat_key: str) -> dict[str, Any]:
        """Suppress adoption for the active native Provider turn without claiming a stop."""

        key = _chat_key(chat_key)
        cancelled_permissions: list[dict[str, Any]] = []
        with self._lock:
            message_id = self._active_message_ids.get(key)
            if message_id is None:
                return {
                    "schema": PROVIDER_SESSION_SNAPSHOT_SCHEMA,
                    "status": "PROVIDER_SESSION_CANCEL_NOT_REQUIRED",
                    "target": self._public_target(self._resolve(key)),
                    "room_queue_used": False,
                }
            messages = self._messages.get(key, ())
            reply = next(
                (item for item in reversed(messages) if item.get("message_id") == message_id),
                None,
            )
            if reply is None:
                raise ProviderSessionError(
                    "PROVIDER_SESSION_ACTIVE_REPLY_MISSING",
                    "active Provider Session reply is unavailable",
                    409,
                )
            if reply.get("state") in {"COMPLETED", "FAILED", "CANCELLED"}:
                return {
                    "schema": PROVIDER_SESSION_SNAPSHOT_SCHEMA,
                    "status": "PROVIDER_SESSION_CANCEL_NOT_REQUIRED",
                    "message": _json_copy(reply),
                    "room_queue_used": False,
                }
            if message_id in self._cancelled_message_ids:
                return {
                    "schema": PROVIDER_SESSION_SNAPSHOT_SCHEMA,
                    "status": "PROVIDER_SESSION_CANCELLATION_ALREADY_REQUESTED",
                    "message": _json_copy(reply),
                    "room_queue_used": False,
                }
            self._cancelled_message_ids.add(message_id)
            reply["state"] = "CANCELLATION_REQUESTED"
            reply["updated_at"] = _utc_now()
            for request_id, record in self._permissions.items():
                if (
                    record.get("chat_key") == key
                    and record.get("message_id") == message_id
                    and record.get("state") == "PENDING"
                ):
                    record["state"] = "CANCELLED"
                    record["resolved_at"] = _utc_now()
                    waiter = self._permission_waiters.get(request_id)
                    if waiter is not None:
                        waiter["event"].set()
                    cancelled_permissions.append(_json_copy(record))
            public_reply = _json_copy(reply)
        self.events.publish(
            key, {"type": "PROVIDER_SESSION_MESSAGE", "message": public_reply}
        )
        for permission in cancelled_permissions:
            self.events.publish(
                key,
                {
                    "type": "PROVIDER_SESSION_PERMISSION_RESOLVED",
                    "permission": permission,
                },
            )
        return {
            "schema": PROVIDER_SESSION_SNAPSHOT_SCHEMA,
            "status": "PROVIDER_SESSION_CANCELLATION_REQUESTED",
            "message": public_reply,
            "room_queue_used": False,
        }

    def resolve_permission(
        self,
        chat_key: str,
        request_id: str,
        option_id: str,
    ) -> tuple[dict[str, Any], bool]:
        key = _chat_key(chat_key)
        normalized_request = _text(request_id, "request_id")
        normalized_option = _text(option_id, "option_id")
        with self._lock:
            record = self._permissions.get(normalized_request)
            if record is None:
                raise ProviderSessionError(
                    "AGENT_PERMISSION_NOT_FOUND",
                    "Provider Session permission request does not exist",
                    404,
                )
            if record["chat_key"] != key:
                raise ProviderSessionError(
                    "AGENT_PERMISSION_SCOPE_MISMATCH",
                    "permission request belongs to another Provider Session",
                    409,
                )
            offered = {option["optionId"] for option in record["options"]}
            if normalized_option not in offered:
                raise ProviderSessionError(
                    "AGENT_PERMISSION_OPTION_UNKNOWN",
                    "selected option is not offered by this request",
                    409,
                )
            if record["state"] != "PENDING":
                if record.get("selected_option_id") == normalized_option:
                    return _json_copy(record), False
                raise ProviderSessionError(
                    "AGENT_PERMISSION_ALREADY_RESOLVED",
                    "permission request already has another decision",
                    409,
                )
            waiter = self._permission_waiters.get(normalized_request)
            if waiter is None:
                raise ProviderSessionError(
                    "AGENT_PERMISSION_SESSION_UNAVAILABLE",
                    "Provider Session cannot accept this decision",
                    409,
                )
            record["state"] = "RESOLVED"
            record["selected_option_id"] = normalized_option
            record["resolved_at"] = _utc_now()
            waiter["option_id"] = normalized_option
            waiter["event"].set()
            public = _json_copy(record)
            self._prune_permissions_locked()
        self.events.publish(
            key,
            {"type": "PROVIDER_SESSION_PERMISSION_RESOLVED", "permission": public},
        )
        return public, True

    def wait_idle(self, chat_key: str, timeout_seconds: float = 10.0) -> bool:
        key = _chat_key(chat_key)
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        while time.monotonic() < deadline:
            with self._lock:
                if key not in self._active_message_ids:
                    return True
            time.sleep(0.01)
        return False

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            for request_id, waiter in list(self._permission_waiters.items()):
                record = self._permissions.get(request_id)
                if record is not None and record["state"] == "PENDING":
                    record["state"] = "CANCELLED"
                    record["resolved_at"] = _utc_now()
                waiter["event"].set()
            self._permission_waiters.clear()
            handles = list(self._handles.values())
            workers = list(self._active_threads.values())
            self._handles.clear()
        for handle in handles:
            handle.host.close()
        for worker in workers:
            worker.join(timeout=5)

    def _resolve(self, chat_key: str) -> dict[str, Any]:
        try:
            raw = self.resolver(chat_key)
        except ProviderSessionError:
            raise
        except Exception as error:
            code = getattr(error, "code", "PROVIDER_SESSION_TARGET_UNAVAILABLE")
            status = int(getattr(error, "status", 409))
            raise ProviderSessionError(str(code), str(code), status) from error
        if not isinstance(raw, Mapping):
            raise ProviderSessionError(
                "PROVIDER_SESSION_TARGET_INVALID",
                "Provider Session resolver returned no target",
                409,
            )
        descriptor = dict(raw)
        descriptor["chat_key"] = _chat_key(descriptor.get("chat_key"))
        if descriptor["chat_key"] != chat_key:
            raise ProviderSessionError(
                "PROVIDER_SESSION_TARGET_MISMATCH",
                "Provider Session target does not match the requested chat key",
                409,
            )
        for field in (
            "provider",
            "provider_session_ref",
            "project_id",
            "node",
            "mode",
            "repository_root",
        ):
            descriptor[field] = _text(descriptor.get(field), field)
        descriptor["provider"] = descriptor["provider"].upper()
        descriptor["mode"] = descriptor["mode"].upper()
        if descriptor.get("session_kind", "CHAT").upper() == "WORKER":
            raise ProviderSessionError(
                "PROVIDER_SESSION_WORKER_FORBIDDEN",
                "Task workers cannot be opened as resident user sessions",
                409,
            )
        if str(descriptor.get("identity_state") or "UNKNOWN").upper() != "VERIFIED":
            raise ProviderSessionError(
                "PROVIDER_SESSION_IDENTITY_UNVERIFIED",
                "Provider Session identity is not verified",
                409,
            )
        return descriptor

    def _ensure_handle(
        self, chat_key: str, descriptor: Mapping[str, Any]
    ) -> _HostHandle:
        fingerprint = self._handle_fingerprint(descriptor)
        stale: _HostHandle | None = None
        with self._lock:
            if self._closed.is_set():
                raise ProviderSessionError(
                    "PROVIDER_SESSION_SERVICE_CLOSED",
                    "Provider Session service is closed",
                    503,
                )
            existing = self._handles.get(chat_key)
            if existing is not None:
                existing_fingerprint = self._handle_fingerprint(
                    existing.descriptor
                )
                if existing_fingerprint == fingerprint:
                    return existing
                if chat_key in self._active_message_ids:
                    raise ProviderSessionError(
                        "PROVIDER_SESSION_BUSY",
                        "The selected Provider Session is already processing a turn",
                        409,
                    )
                self._handles.pop(chat_key, None)
                stale = existing
        if stale is not None:
            stale.host.close()

        requester = lambda request: self._request_permission(chat_key, request)
        host = self.host_factory(descriptor, requester)
        observe = getattr(getattr(host, "store", None), "observe_provider_session", None)
        if not callable(observe):
            host.close()
            raise ProviderSessionError(
                "PROVIDER_SESSION_HOST_INVALID",
                "Provider Session host cannot select an exact provider session",
                500,
            )
        observe(
            str(descriptor["provider"]),
            str(descriptor["provider_session_ref"]),
        )
        setter = getattr(host, "set_permission_requester", None)
        if callable(setter):
            setter(requester)
        handle = _HostHandle(
            descriptor=dict(descriptor),
            host=host,
            connection=self._public_connection(None),
        )

        winner: _HostHandle | None = None
        error: ProviderSessionError | None = None
        with self._lock:
            if self._closed.is_set():
                error = ProviderSessionError(
                    "PROVIDER_SESSION_SERVICE_CLOSED",
                    "Provider Session service is closed",
                    503,
                )
            else:
                current = self._handles.get(chat_key)
                if current is None:
                    self._handles[chat_key] = handle
                    return handle
                if self._handle_fingerprint(current.descriptor) == fingerprint:
                    winner = current
                else:
                    error = ProviderSessionError(
                        "PROVIDER_SESSION_TARGET_CHANGED",
                        "Provider Session target changed while its host was prepared",
                        409,
                    )
        host.close()
        if error is not None:
            raise error
        assert winner is not None
        return winner

    @staticmethod
    def _handle_fingerprint(descriptor: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(
            str(descriptor.get(field) or "")
            for field in (
                "provider",
                "provider_session_ref",
                "project_id",
                "node",
                "mode",
                "repository_root",
                "model_ref",
            )
        )

    def delete_action(self, chat_key: str, action_id: str) -> dict[str, Any]:
        key = _chat_key(chat_key)
        normalized_action_id = _action_id(action_id)
        if self.action_store is not None:
            removed = self.action_store.delete_provider_session_action(
                key, normalized_action_id
            )
        else:
            self._resolve(key)
            with self._lock:
                actions = self._actions.get(key)
                removed = next(
                    (
                        action
                        for action in actions or ()
                        if action["action_id"] == normalized_action_id
                    ),
                    None,
                )
                if removed is not None:
                    self._actions[key] = deque(
                        (
                            action
                            for action in actions or ()
                            if action["action_id"] != normalized_action_id
                        ),
                        maxlen=self.retained_messages,
                    )
        if removed is None:
            raise ProviderSessionError(
                "PROVIDER_SESSION_ACTION_NOT_FOUND",
                "Provider Session Action does not exist",
                404,
            )
        self.events.publish(
            key,
            {
                "type": "PROVIDER_SESSION_ACTION_DELETED",
                "action_id": normalized_action_id,
            },
        )
        return {
            "schema": PROVIDER_ACTION_SCHEMA,
            "status": "PROVIDER_SESSION_ACTION_DELETED",
            "action": _json_copy(removed),
        }

    def _prune_idempotency_locked(self) -> None:
        while len(self._idempotency) > self.retained_idempotency:
            self._idempotency.popitem(last=False)

    def _prune_permissions_locked(self) -> None:
        while len(self._permissions) > self.retained_permissions:
            removable = next(
                (
                    request_id
                    for request_id, record in self._permissions.items()
                    if request_id not in self._permission_waiters
                    and record.get("state") != "PENDING"
                ),
                None,
            )
            if removable is None:
                return
            self._permissions.pop(removable, None)

    def _publish_work_status(
        self,
        chat_key: str,
        message_id: str,
        state: str,
        *,
        operation: str = "PROVIDER_TURN",
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated_at = _utc_now()
        safe_details = {
            key: details[key]
            for key in (
                "source",
                "exit_code",
                "commit_sha",
                "short_sha",
                "commit_message",
                "branch",
                "remote",
                "changed_files",
            )
            if details is not None and key in details
        }
        status = {
            "schema": WORK_STATUS_SCHEMA,
            "operation": operation,
            "state": state,
            "message_id": message_id,
            "error_code": error_code,
            "updated_at": updated_at,
        }
        if safe_details:
            status["details"] = safe_details
        action = None
        if operation in {"COMMIT", "PUSH"} and state in {"COMPLETED", "FAILED"}:
            short_sha = str(safe_details.get("short_sha") or "").strip()
            commit_message = str(safe_details.get("commit_message") or "").strip()
            branch = str(safe_details.get("branch") or "").strip()
            remote = str(safe_details.get("remote") or "").strip()
            changed_files = safe_details.get("changed_files")
            if operation == "COMMIT" and state == "COMPLETED":
                summary_parts = [part for part in (short_sha, commit_message) if part]
                if isinstance(changed_files, int):
                    summary_parts.append(f"{changed_files} files")
            elif operation == "PUSH" and state == "COMPLETED":
                destination = f"{branch} -> {remote}" if branch and remote else branch
                summary_parts = [part for part in (destination, short_sha) if part]
            else:
                exit_code = safe_details.get("exit_code")
                summary_parts = [
                    f"Git exit {exit_code}" if isinstance(exit_code, int) else "Git operation failed"
                ]
            action = {
                "schema": PROVIDER_ACTION_SCHEMA,
                "action_id": "provider_action_" + uuid.uuid4().hex[:24],
                "kind": "INFORMATIONAL",
                "category": "GIT",
                "operation": operation,
                "state": state,
                "title": f"{operation.title()} {state.lower()}",
                "summary": " · ".join(summary_parts),
                "details": safe_details,
                "message_id": message_id,
                "created_at": updated_at,
            }
        if action is not None and self.action_store is not None:
            action = dict(
                self.action_store.record_provider_session_action(
                    chat_key, action, retain=self.retained_messages
                )
            )
        with self._lock:
            self._work_statuses[chat_key] = status
            if action is not None and self.action_store is None:
                actions = self._actions.setdefault(
                    chat_key, deque(maxlen=self.retained_messages)
                )
                actions.append(action)
        self.events.publish(
            chat_key,
            {"type": "PROVIDER_SESSION_WORK_STATUS", "work_status": status},
        )
        if action is not None:
            self.events.publish(
                chat_key,
                {"type": "PROVIDER_SESSION_ACTION", "action": action},
            )
        return _json_copy(status)

    def _run_turn(
        self,
        chat_key: str,
        descriptor: Mapping[str, Any],
        handle: _HostHandle,
        user_message: Mapping[str, Any],
        reply_message: dict[str, Any],
        on_terminal: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        try:
            prepared = handle.host.prepare(str(descriptor["provider"]))
            with self._lock:
                handle.connection = self._public_connection(prepared)
            request = {
                "kind": "QUESTION",
                "sender": "USER",
                "body": user_message["body"],
                "message_id": user_message["message_id"],
                "runtime_context": {
                    "requested_mode": descriptor["mode"],
                    "node": descriptor["node"],
                    "current_anchor_ref": descriptor.get("current_anchor_ref")
                    or "UNKNOWN",
                    "commander_surface": "UNIVERSE_UI",
                    "conversation_transport": "PROVIDER_NATIVE_DIRECT",
                },
            }
            result = handle.host.reply_stream(
                str(descriptor["provider"]),
                request,
                lambda delta: self._append_delta(chat_key, reply_message, delta),
            )
            text = str(result.get("text") or "").strip()
            with self._lock:
                cancelled = reply_message["message_id"] in self._cancelled_message_ids
                if not cancelled and text:
                    reply_message["body"] = text[:12000]
                reply_message["state"] = "CANCELLED" if cancelled else "COMPLETED"
                reply_message["updated_at"] = _utc_now()
            self.events.publish(
                chat_key,
                {"type": "PROVIDER_SESSION_MESSAGE", "message": reply_message},
            )
            self._publish_work_status(
                chat_key,
                reply_message["message_id"],
                "CANCELLED" if cancelled else "COMPLETED",
            )
        except Exception as error:  # noqa: BLE001 - provider boundary is normalized
            code = str(getattr(error, "code", type(error).__name__)).upper()
            with self._lock:
                cancelled = reply_message["message_id"] in self._cancelled_message_ids
                reply_message["state"] = "CANCELLED" if cancelled else "FAILED"
                if not cancelled:
                    reply_message["error_code"] = code[:120]
                reply_message["updated_at"] = _utc_now()
            self.events.publish(
                chat_key,
                {"type": "PROVIDER_SESSION_MESSAGE", "message": reply_message},
            )
            self._publish_work_status(
                chat_key,
                reply_message["message_id"],
                "CANCELLED" if cancelled else "FAILED",
                error_code=None if cancelled else code[:120],
            )
        finally:
            try:
                reader = getattr(handle.host, "drain_work_statuses", None)
                milestones = reader() if callable(reader) else []
                for milestone in milestones:
                    operation = str(milestone.get("operation") or "").upper()
                    state = str(milestone.get("state") or "").upper()
                    if operation not in {"COMMIT", "PUSH"} or state not in {
                        "COMPLETED",
                        "FAILED",
                    }:
                        continue
                    exit_code = milestone.get("exit_code")
                    self._publish_work_status(
                        chat_key,
                        reply_message["message_id"],
                        state,
                        operation=operation,
                        error_code=(
                            f"GIT_EXIT_{exit_code}"
                            if state == "FAILED" and isinstance(exit_code, int)
                            else None
                        ),
                        details=milestone,
                    )
            except Exception:
                pass
            try:
                refreshed_connection = self._public_connection(handle.host.status())
            except Exception:  # noqa: BLE001 - retain the last safe snapshot
                refreshed_connection = None
            with self._lock:
                current_handle = self._handles.get(chat_key)
                if current_handle is handle and refreshed_connection is not None:
                    handle.connection = refreshed_connection
                self._active_message_ids.pop(chat_key, None)
                self._cancelled_message_ids.discard(reply_message["message_id"])
                self._active_threads.pop(chat_key, None)
            if on_terminal is not None:
                try:
                    on_terminal(_json_copy(reply_message))
                except Exception:
                    # A transport observer cannot corrupt the provider-native turn.
                    pass

    def _append_delta(
        self, chat_key: str, reply_message: dict[str, Any], value: Any
    ) -> None:
        delta = str(value or "")
        if not delta:
            return
        with self._lock:
            if reply_message["message_id"] in self._cancelled_message_ids:
                return
            current = str(reply_message.get("body") or "")
            remaining = max(0, 12000 - len(current))
            accepted = delta[:remaining]
            if not accepted:
                return
            reply_message["body"] = current + accepted
            reply_message["state"] = "STREAMING"
            reply_message["updated_at"] = _utc_now()
        self.events.publish(
            chat_key,
            {
                "type": "PROVIDER_SESSION_DELTA",
                "message_id": reply_message["message_id"],
                "delta": accepted,
            },
        )

    def _request_permission(
        self, chat_key: str, value: Mapping[str, Any]
    ) -> str | None:
        try:
            normalized = normalize_permission_request(value)
        except AgentSessionError:
            return None
        request_id = str(normalized["request_id"])
        with self._lock:
            if self._closed.is_set():
                return None
            existing = self._permissions.get(request_id)
            if existing is not None:
                return None
            record = {
                "request_id": request_id,
                "provider": str(normalized.get("provider") or "UNKNOWN"),
                "tool_call": _json_copy(normalized.get("tool_call") or {}),
                "options": _json_copy(normalized.get("options") or []),
                "scope_kind": "PROVIDER_SESSION",
                "chat_key": chat_key,
                "message_id": self._active_message_ids.get(chat_key),
                "state": "PENDING",
                "selected_option_id": None,
                "requested_at": _utc_now(),
                "resolved_at": None,
                "persistence": "PROCESS_LOCAL",
            }
            waiter = {"event": threading.Event(), "option_id": None}
            self._permissions[request_id] = record
            self._permissions.move_to_end(request_id)
            self._permission_waiters[request_id] = waiter
            self._prune_permissions_locked()
            public = _json_copy(record)
        self.events.publish(
            chat_key,
            {"type": "PROVIDER_SESSION_PERMISSION", "permission": public},
        )
        waiter["event"].wait(self.permission_timeout_seconds)
        with self._lock:
            current = self._permissions.get(request_id)
            selected = waiter.get("option_id")
            self._permission_waiters.pop(request_id, None)
            if current is not None and current["state"] == "PENDING":
                current["state"] = "EXPIRED"
                current["resolved_at"] = _utc_now()
                public = _json_copy(current)
            else:
                public = None
            self._prune_permissions_locked()
        if public is not None:
            self.events.publish(
                chat_key,
                {"type": "PROVIDER_SESSION_PERMISSION_RESOLVED", "permission": public},
            )
        return str(selected) if selected else None

    def _append_message(self, chat_key: str, message: Mapping[str, Any]) -> None:
        self._messages.setdefault(
            chat_key, deque(maxlen=self.retained_messages)
        ).append(message if isinstance(message, dict) else dict(message))

    @staticmethod
    def _message(chat_key: str, role: str, body: str, state: str) -> dict[str, Any]:
        now = _utc_now()
        return {
            "schema": PROVIDER_SESSION_MESSAGE_SCHEMA,
            "message_id": "provider_message_" + uuid.uuid4().hex,
            "chat_key": chat_key,
            "role": role,
            "body": body,
            "state": state,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _public_target(descriptor: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "chat_key": descriptor["chat_key"],
            "provider": descriptor["provider"],
            "project_id": descriptor["project_id"],
            "node": descriptor["node"],
            "mode": descriptor["mode"],
            "alias": descriptor.get("alias")
            or f"{descriptor['project_id']} {descriptor['mode']}",
            "current_anchor_ref": descriptor.get("current_anchor_ref") or "UNKNOWN",
            "model_ref": descriptor.get("model_ref") or "UNKNOWN",
        }

    @staticmethod
    def _public_connection(value: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {
                "connection_state": "NOT_OPENED",
                "resident": False,
                "requested_mode": "UNKNOWN",
                "last_provider": "UNKNOWN",
                "model_ref": "UNKNOWN",
            }
        allowed = {
            "schema",
            "requested_mode",
            "last_provider",
            "model_ref",
            "connection_state",
            "session_persistence",
            "resident",
        }
        public = {key: value[key] for key in allowed if key in value}
        observation = value.get("runtime_observation")
        if isinstance(observation, Mapping):
            public_observation = {
                key: observation[key]
                for key in (
                    "schema",
                    "provider",
                    "state",
                    "quota_state",
                    "rate_limit_status",
                )
                if key in observation
            }
            usage = observation.get("usage")
            if isinstance(usage, Mapping):
                public_observation["usage"] = {
                    str(key): item
                    for key, item in usage.items()
                    if isinstance(item, (int, float)) and not isinstance(item, bool)
                }
            public["runtime_observation"] = public_observation
        return _json_copy(public)


__all__ = [
    "PROVIDER_SESSION_SNAPSHOT_SCHEMA",
    "PROVIDER_SESSION_STREAM_SCHEMA",
    "ProviderSessionError",
    "ProviderSessionEventHub",
    "ProviderSessionService",
]
