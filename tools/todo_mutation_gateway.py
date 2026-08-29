from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TodoMutationGatewayError(RuntimeError):
    def __init__(self, code: str, detail: str, status: int = 0):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def default_service_state_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Universe" / "server.json"
    return Path.home() / ".universe" / "server.json"


class TodoMutationGateway:
    """Fail-closed Todo mutation transport shared by all supervised sessions.

    Standalone and Universe-attached callers supply the same provider session
    and Session Anchor coordinates. The service, not Mode or Todo source_kind,
    decides whether that exact supervised session is current.
    """

    def __init__(self, endpoint: str, token: str | None = None, *, timeout: float = 10.0):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_service_state(cls, path: Path | None = None) -> "TodoMutationGateway":
        state_path = (path or default_service_state_path()).expanduser().resolve()
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise TodoMutationGatewayError(
                "TODO_GATEWAY_UNAVAILABLE",
                f"Universe service state is unavailable: {state_path}",
            ) from error
        endpoint = str(state.get("endpoint") or "").strip()
        token = str(state.get("token") or "").strip() or None
        if not endpoint:
            raise TodoMutationGatewayError(
                "TODO_GATEWAY_UNAVAILABLE",
                "Universe service state has no endpoint",
            )
        return cls(endpoint, token)

    @staticmethod
    def mutation_request(
        *,
        provider: str,
        provider_session_ref: str,
        session_id: str,
        session_anchor_ref: str,
        instruction_ref: str,
        todo: Mapping[str, Any],
        ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        return {
            "schema": "universe.todo-mutation-request.v1",
            "provider": provider,
            "provider_session_ref": provider_session_ref,
            "session_id": session_id,
            "session_anchor_ref": session_anchor_ref,
            "instruction_ref": instruction_ref,
            "todo": dict(todo),
            "ttl_seconds": ttl_seconds,
        }

    def prepare(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._post("/v1/todo-mutation-receipts", request)

    def consume(self, receipt_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._post(
            f"/v1/todo-mutation-receipts/{receipt_id}/consume",
            request,
        )

    def create_todo(self, **coordinates: Any) -> dict[str, Any]:
        request = self.mutation_request(**coordinates)
        prepared = self.prepare(request)
        receipt_id = str(prepared["receipt"]["receipt_id"])
        return self.consume(receipt_id, request)


    @staticmethod
    def action_mutation_request(
        *,
        provider: str,
        provider_session_ref: str,
        session_id: str,
        session_anchor_ref: str,
        instruction_ref: str,
        todo_id: str,
        action: Mapping[str, Any],
        ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        return {
            "schema": "universe.todo-action-mutation-request.v1",
            "provider": provider,
            "provider_session_ref": provider_session_ref,
            "session_id": session_id,
            "session_anchor_ref": session_anchor_ref,
            "instruction_ref": instruction_ref,
            "todo_id": todo_id,
            "action": dict(action),
            "ttl_seconds": ttl_seconds,
        }

    def prepare_action(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._post("/v1/todo-action-mutation-receipts", request)

    def consume_action(
        self, receipt_id: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._post(
            f"/v1/todo-action-mutation-receipts/{receipt_id}/consume",
            request,
        )

    def apply_action(self, **coordinates: Any) -> dict[str, Any]:
        request = self.action_mutation_request(**coordinates)
        prepared = self.prepare_action(request)
        receipt_id = str(prepared["receipt"]["receipt_id"])
        return self.consume_action(receipt_id, request)

    @staticmethod
    def scheduler_mutation_request(
        *,
        provider: str,
        provider_session_ref: str,
        session_id: str,
        session_anchor_ref: str,
        instruction_ref: str,
        goal_id: str,
        action: str,
        expected_goal_revision: int,
        interval_seconds: int = 5,
        ttl_seconds: int = 120,
    ) -> dict[str, Any]:
        return {
            "schema": "universe.goal-automation-scheduler-mutation-request.v1",
            "provider": provider,
            "provider_session_ref": provider_session_ref,
            "session_id": session_id,
            "session_anchor_ref": session_anchor_ref,
            "instruction_ref": instruction_ref,
            "goal_id": goal_id,
            "scheduler": {
                "action": action,
                "expected_goal_revision": expected_goal_revision,
                "interval_seconds": interval_seconds,
            },
            "ttl_seconds": ttl_seconds,
        }

    def prepare_scheduler(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._post(
            "/v1/goal-automation-scheduler-mutation-receipts", request
        )

    def consume_scheduler(
        self, receipt_id: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._post(
            f"/v1/goal-automation-scheduler-mutation-receipts/{receipt_id}/consume",
            request,
        )

    def configure_scheduler(self, **coordinates: Any) -> dict[str, Any]:
        request = self.scheduler_mutation_request(**coordinates)
        prepared = self.prepare_scheduler(request)
        receipt_id = str(prepared["receipt"]["receipt_id"])
        return self.consume_scheduler(receipt_id, request)

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            self.endpoint + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                try:
                    body = json.loads(error.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = {}
                raise TodoMutationGatewayError(
                    str(body.get("error_code") or "TODO_GATEWAY_HTTP_ERROR"),
                    str(body.get("detail") or body.get("reason") or error.reason),
                    error.code,
                ) from error
            finally:
                error.close()
        except (OSError, URLError) as error:
            raise TodoMutationGatewayError(
                "TODO_GATEWAY_UNAVAILABLE",
                "Universe mutation service is unavailable; raw mutation fallback is forbidden",
            ) from error
