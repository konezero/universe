from __future__ import annotations

from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "tools"))

from session_anchor_transport import (  # noqa: E402
    SessionAnchorTransport,
    SessionAnchorTransportError,
)


class FakeSupervisor:
    def __init__(self, sessions: list[dict]) -> None:
        self.sessions = sessions

    def list_sessions(self, *, include_hidden: bool) -> list[dict]:
        assert include_hidden is True
        return [dict(item) for item in self.sessions]


class FakeProviderSessions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.terminal_callbacks: dict[str, object] = {}
        self.target_reply_id = "provider-reply-target"
        self.origin_reply_id = "provider-reply-origin"

    def submit(
        self,
        chat_key: str,
        value: dict,
        *,
        on_accepted=None,
        on_terminal=None,
    ) -> dict:
        self.calls.append((chat_key, dict(value)))
        reply_id = (
            self.target_reply_id if chat_key == "chat-target" else self.origin_reply_id
        )
        if on_accepted is not None:
            on_accepted({"message_id": reply_id, "state": "STARTING"})
        if on_terminal is not None:
            self.terminal_callbacks[reply_id] = on_terminal
        return {"status": "PROVIDER_SESSION_INPUT_ACCEPTED", "reply": {"message_id": reply_id}}

    def emit_terminal(self, reply_id: str, *, body: str) -> None:
        callback = self.terminal_callbacks[reply_id]
        callback({"message_id": reply_id, "state": "COMPLETED", "body": body})

    def wait_idle(self, _chat_key: str, _timeout: float) -> bool:
        return True

    def snapshot(self, chat_key: str) -> dict:
        if chat_key != "chat-target":
            return {"messages": []}
        return {
            "messages": [
                {
                    "message_id": self.target_reply_id,
                    "state": "COMPLETED",
                    "body": "target terminal result",
                }
            ]
        }


def session(anchor: str, provider: str = "CODEX", **overrides: object) -> dict:
    return {
        "session_id": "session-" + anchor,
        "session_anchor_ref": anchor,
        "current_project_id": "GCS",
        "project_id": "GCS",
        "provider": provider,
        "provider_session_ref": provider.lower() + "-vendor-key",
        "state": "LIVE",
        "currentness": "CURRENT",
        **overrides,
    }


def catalog() -> dict:
    return {
        "rooms": [
            {
                "chat_key": "chat-origin",
                "provider": "CODEX",
                "identity_state": "VERIFIED",
                "session_kind": "CHAT",
                "binding": {
                    "session_anchor_ref": "anchor-origin",
                    "vendor_session_chat_key": "chat-origin",
                },
            },
            {
                "chat_key": "chat-target",
                "provider": "CODEX",
                "identity_state": "VERIFIED",
                "session_kind": "CHAT",
                "binding": {
                    "session_anchor_ref": "anchor-target",
                    "vendor_session_chat_key": "chat-target",
                },
            },
        ]
    }


class SessionAnchorTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "universe.sqlite3"
        self.providers = FakeProviderSessions()
        self.terminal = []
        self.transport = SessionAnchorTransport(
            database_path=self.database,
            session_supervisor=FakeSupervisor(
                [session("anchor-origin"), session("anchor-target")]
            ),
            provider_sessions=self.providers,
            provider_chat_catalog=catalog,
            terminal_callback=lambda event: self.terminal.append(dict(event)),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, **overrides: object) -> dict:
        request = {
            "delegation_id": "delegation-001",
            "project_id": "GCS",
            "request": {
                "idempotency_key": "cross-session-001",
                "summary": "Review the bounded diff.",
                "provider": "AUTO",
                "origin_session_anchor_ref": "anchor-origin",
                "target_session_anchor_ref": "anchor-target",
                "origin_session_chat_key": "chat-origin",
            },
        }
        request.update(overrides)
        return request

    def test_routes_exact_target_and_terminal_result_to_origin_without_rooms(self) -> None:
        progress = self.transport.deliver(self.request())
        self.assertEqual("WAITING_FOR_TARGET_SESSION_RESULT", progress["progress"]["step"])
        self.assertFalse(progress["progress"]["room_queue_used"])
        self.providers.emit_terminal(
            self.providers.target_reply_id,
            body="target terminal result",
        )
        self.assertEqual(["chat-target", "chat-origin"], [key for key, _ in self.providers.calls])
        self.assertIn("cross-session delegation", self.providers.calls[0][1]["body"])
        self.assertIn("terminal result", self.providers.calls[1][1]["body"])
        self.assertEqual("anchor-origin", self.terminal[0]["origin_session_anchor_ref"])
        self.assertEqual("anchor-target", self.terminal[0]["target_session_anchor_ref"])
        delivery = self.transport._require_delivery(
            progress["progress"]["session_anchor_delivery_id"]
        )
        self.assertEqual("ORIGIN_RESULT_ACCEPTED", delivery["state"])
        frame = self.transport.task_frame_lineage.get_task_frame(
            delivery["task_frame_ref"]
        )
        self.assertEqual("anchor-origin", frame["origin_session_anchor_ref"])
        self.assertEqual("anchor-target", frame["target_session_anchor_ref"])
        self.assertEqual(1, len(frame["results"]))

    def test_idempotency_persists_one_target_delivery(self) -> None:
        first = self.transport.deliver(self.request())
        self.transport.record_terminal(
            first["progress"]["session_anchor_delivery_id"],
            {
                "reply_message_id": self.providers.target_reply_id,
                "state": "COMPLETED",
                "body": "target terminal result",
            },
        )
        second = self.transport.deliver(self.request())
        self.assertEqual(
            first["progress"]["session_anchor_delivery_id"],
            second["progress"]["session_anchor_delivery_id"],
        )
        self.assertEqual(2, len(self.providers.calls))

    def test_duplicate_terminal_callback_cannot_overwrite_completed_delivery(self) -> None:
        progress = self.transport.deliver(self.request())
        self.providers.emit_terminal(
            self.providers.target_reply_id,
            body="target terminal result",
        )
        self.providers.emit_terminal(
            self.providers.target_reply_id,
            body="duplicate target terminal result",
        )
        delivery = self.transport._require_delivery(
            progress["progress"]["session_anchor_delivery_id"]
        )
        self.assertEqual("ORIGIN_RESULT_ACCEPTED", delivery["state"])
        self.assertEqual(2, len(self.providers.calls))

    def test_cancelled_central_claim_suppresses_lineage_and_origin_delivery(self) -> None:
        self.transport.terminal_claim_callback = lambda _delegation_id: False
        progress = self.transport.deliver(self.request())
        self.providers.emit_terminal(
            self.providers.target_reply_id,
            body="must not reach origin",
        )
        delivery = self.transport._require_delivery(
            progress["progress"]["session_anchor_delivery_id"]
        )
        self.assertEqual("CANCELLED", delivery["state"])
        self.assertEqual(["chat-target"], [key for key, _ in self.providers.calls])
        frame = self.transport.task_frame_lineage.get_task_frame(
            delivery["task_frame_ref"]
        )
        self.assertEqual([], frame["results"])

    def test_claimed_origin_chat_must_match_exact_anchor_transport(self) -> None:
        request = self.request()
        request["request"] = {
            **request["request"],
            "origin_session_chat_key": "chat-target",
        }
        with self.assertRaises(SessionAnchorTransportError) as mismatch:
            self.transport.deliver(request)
        self.assertEqual("ORIGIN_SESSION_CHAT_MISMATCH", mismatch.exception.code)

    def test_missing_stale_ambiguous_and_provider_mismatch_fail_closed(self) -> None:
        self.transport.session_supervisor = FakeSupervisor([session("anchor-origin")])
        with self.assertRaises(SessionAnchorTransportError) as missing:
            self.transport.deliver(self.request())
        self.assertEqual("TARGET_SESSION_ANCHOR_NOT_FOUND", missing.exception.code)

        self.transport.session_supervisor = FakeSupervisor(
            [session("anchor-origin"), session("anchor-target", currentness="STALE")]
        )
        with self.assertRaises(SessionAnchorTransportError) as stale:
            self.transport.deliver(self.request())
        self.assertEqual("TARGET_SESSION_ANCHOR_STALE", stale.exception.code)

        self.transport.session_supervisor = FakeSupervisor(
            [session("anchor-origin"), session("anchor-target")]
        )
        mismatch = self.request()
        mismatch["request"] = {**mismatch["request"], "provider": "CLAUDE"}
        with self.assertRaises(SessionAnchorTransportError) as provider:
            self.transport.deliver(mismatch)
        self.assertEqual("TARGET_SESSION_PROVIDER_MISMATCH", provider.exception.code)

        ambiguous_catalog = catalog()
        ambiguous_catalog["rooms"].append(dict(ambiguous_catalog["rooms"][1]))
        self.transport.provider_chat_catalog = lambda: ambiguous_catalog
        with self.assertRaises(SessionAnchorTransportError) as ambiguous:
            self.transport.deliver(self.request())
        self.assertEqual("TARGET_SESSION_TRANSPORT_AMBIGUOUS", ambiguous.exception.code)


if __name__ == "__main__":
    unittest.main()
