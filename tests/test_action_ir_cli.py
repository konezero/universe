from __future__ import annotations

import io
import json
import sys
import urllib.error
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = ROOT / ".ai" / "skills" / "common" / "action-ir" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import action_ir_cli  # noqa: E402


class _Response:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


def _envelope() -> dict[str, Any]:
    return {
        "schema": action_ir_cli.REQUEST_SCHEMA,
        "action_id": "session.new",
        "request": {
            "target": "PROJECT_MASTER",
            "project_id": "GCS",
            "provider": "CODEX",
        },
    }


class ActionIrCliTests(unittest.TestCase):
    def _ready_endpoint(self) -> dict[str, str]:
        return {
            "status": "UNIVERSE_LOCAL_ENDPOINT_READY",
            "endpoint": "http://127.0.0.1:8765",
            "token": "transient-test-token",
        }

    def test_success_posts_only_server_action_envelope(self) -> None:
        opener = Mock(
            return_value=_Response(
                201,
                {
                    "status": "SESSION_NEW_COMPLETED",
                    "echo": "transient-test-token",
                },
            )
        )
        result = action_ir_cli.run(
            _envelope(),
            endpoint_resolver=lambda _path: self._ready_endpoint(),
            opener=opener,
        )

        self.assertEqual(action_ir_cli.COMPLETED, result["status"])
        self.assertEqual(201, result["http_status"])
        request = opener.call_args.args[0]
        self.assertEqual("http://127.0.0.1:8765/v1/actions", request.full_url)
        self.assertEqual(
            {
                "action_id": "session.new",
                "request": _envelope()["request"],
            },
            json.loads(request.data.decode("utf-8")),
        )
        self.assertNotIn("transient-test-token", json.dumps(result))

    def test_structured_server_error_is_preserved_and_blocked(self) -> None:
        opener = Mock(
            side_effect=urllib.error.HTTPError(
                "http://127.0.0.1:8765/v1/actions",
                409,
                "conflict",
                None,
                io.BytesIO(
                    json.dumps(
                        {
                            "error_code": "SESSION_RESUME_SELECTOR_REQUIRED",
                            "detail": "selector required",
                        }
                    ).encode("utf-8")
                ),
            )
        )
        result = action_ir_cli.run(
            _envelope(),
            endpoint_resolver=lambda _path: self._ready_endpoint(),
            opener=opener,
        )

        self.assertEqual(action_ir_cli.BLOCKED, result["status"])
        self.assertEqual(409, result["http_status"])
        self.assertEqual("SESSION_RESUME_SELECTOR_REQUIRED", result["error_code"])
        self.assertEqual("selector required", result["detail"])
        self.assertNotIn("transient-test-token", json.dumps(result))

    def test_transport_failure_is_explicit(self) -> None:
        opener = Mock(side_effect=urllib.error.URLError("connection refused"))
        result = action_ir_cli.run(
            _envelope(),
            endpoint_resolver=lambda _path: self._ready_endpoint(),
            opener=opener,
        )

        self.assertEqual(action_ir_cli.TRANSPORT_FAILED, result["status"])
        self.assertEqual("ACTION_IR_TRANSPORT_UNAVAILABLE", result["error_code"])
        self.assertEqual("URLError", result["error_type"])

    def test_invalid_endpoint_is_a_structured_transport_failure(self) -> None:
        result = action_ir_cli.run(
            _envelope(),
            endpoint_resolver=lambda _path: {
                "status": "UNIVERSE_LOCAL_ENDPOINT_READY",
                "endpoint": "http://\ninvalid",
                "token": "transient-test-token",
            },
        )

        self.assertEqual(action_ir_cli.TRANSPORT_FAILED, result["status"])
        self.assertEqual("ACTION_IR_TRANSPORT_UNAVAILABLE", result["error_code"])
        self.assertEqual("InvalidURL", result["error_type"])

    def test_invalid_envelope_is_not_sent(self) -> None:
        opener = Mock()
        result = action_ir_cli.run(
            {"schema": action_ir_cli.REQUEST_SCHEMA, "action_id": "session.new"},
            endpoint_resolver=lambda _path: self._ready_endpoint(),
            opener=opener,
        )

        self.assertEqual(action_ir_cli.REQUEST_INVALID, result["status"])
        self.assertEqual("ACTION_REQUEST_INVALID", result["error_code"])
        opener.assert_not_called()

    def test_missing_endpoint_is_structured_without_network_call(self) -> None:
        opener = Mock()
        result = action_ir_cli.run(
            _envelope(),
            endpoint_resolver=lambda _path: {
                "status": "UNIVERSE_LOCAL_ENDPOINT_MISSING"
            },
            opener=opener,
        )

        self.assertEqual(action_ir_cli.BLOCKED, result["status"])
        self.assertEqual("UNIVERSE_LOCAL_ENDPOINT_MISSING", result["error_code"])
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
