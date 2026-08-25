"""Anchor-first session ownership regressions for msg_8da76d465ba819a4.

Two observed failures are pinned here:

1. A SessionStart hook with no resolvable Mode defaulted to MASTER, so a live
   CONDUCTOR PTY was observed as MASTER and the Mode Current Anchor was patched
   from that guess.
2. An explicit inject targeting one supervisor session resolved a *different*
   session through provider identity-owner lookup and relocated that session.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import universe_session_inject_hook as inject_hook  # noqa: E402
from universe_server import UniverseError, perform_session_ref_inject  # noqa: E402

HOOK_SOURCE = (ROOT / "tools" / "universe_session_inject_hook.py").read_text(
    encoding="utf-8"
)

TARGET_SESSION = "session_ec5ed568705da97bfc0d4ecb"
IDENTITY_OWNER = "session_123b6a5dac26bd0a91575526"
OWNER_ANCHOR = "session_anchor_338be0d4b127cb433c6ab20f"


class _Supervisor:
    """Minimal Supervisor whose provider identity is owned by another session."""

    def __init__(self, sessions: Mapping[str, Mapping[str, Any]], owner: str):
        self._sessions = {key: dict(value) for key, value in sessions.items()}
        self._owner = owner
        self.relocated: list[str] = []

    def get_session(self, session_id: str) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return dict(self._sessions[session_id])

    def register_session(self, value: Mapping[str, Any]):
        # Mirrors the real identity-owner redirect.
        return dict(self._sessions[self._owner]), False

    def bind_current_location(self, session_id: str, **kwargs: Any):
        self.relocated.append(session_id)
        return dict(self._sessions[session_id])

    def list_sessions(self, **kwargs: Any):
        return [dict(item) for item in self._sessions.values()]


class _Rooms:
    def get_room(self, room_id: str):
        raise AssertionError("room lookup must not run for this request")


def _sessions() -> dict[str, dict[str, Any]]:
    return {
        TARGET_SESSION: {
            "session_id": TARGET_SESSION,
            "node": "universe",
            "mode": "CONDUCTOR",
            "provider": "CODEX",
            "provider_session_ref": "",
            "session_anchor_ref": "session_anchor_026fea767aeeffa3ffe2ef12",
            "row_version": 1,
            "state": "LIVE",
            "is_default": False,
        },
        IDENTITY_OWNER: {
            "session_id": IDENTITY_OWNER,
            "node": "universe",
            "mode": "MASTER",
            "provider": "CODEX",
            "provider_session_ref": "01a03306-d73d-7e92-8e67-ade74c0ba51a",
            "session_anchor_ref": OWNER_ANCHOR,
            "row_version": 9,
            "state": "LIVE",
            "is_default": True,
        },
    }


class SessionStartModeResolutionTests(unittest.TestCase):
    """An unresolved Mode must stay unresolved, never become MASTER."""

    @staticmethod
    def _resolve(mode: Any = None, environment: Mapping[str, str] | None = None) -> str:
        return inject_hook.resolve_mode(
            args=argparse.Namespace(mode=mode),
            session_fields={},
            environment=dict(environment or {}),
        )

    def test_missing_mode_does_not_default_to_master(self) -> None:
        self.assertEqual(
            self._resolve(),
            "",
            "an external-web/missing-mode SessionStart must not claim MASTER",
        )

    def test_unknown_mode_tokens_do_not_default_to_master(self) -> None:
        for token in ("UNKNOWN", "NONE", "", "   "):
            with self.subTest(token=token):
                self.assertEqual(self._resolve(mode=token), "")

    def test_explicit_and_environment_modes_are_still_honoured(self) -> None:
        self.assertEqual(self._resolve(mode="conductor"), "CONDUCTOR")
        self.assertEqual(
            self._resolve(environment={"UNIVERSE_MODE": "conductor"}), "CONDUCTOR"
        )

    def test_hook_never_sends_a_guessed_mode(self) -> None:
        body = HOOK_SOURCE[
            HOOK_SOURCE.index("inject_body = {") : HOOK_SOURCE.index(
                'if session_ref:\n        inject_body["provider_session_ref"]'
            )
        ]
        self.assertNotIn('"mode": mode', body)
        self.assertNotIn('"node": node', body)
        guard = HOOK_SOURCE[HOOK_SOURCE.index("    if mode:") :][:400]
        self.assertIn('inject_body["mode"] = mode', guard)
        self.assertIn('inject_body["node"] = node', guard)

    def test_unresolved_mode_does_not_preempt_offline_diagnostics(self) -> None:
        # The Mode guard must not short-circuit before the Host connection
        # check, or an offline Universe reports SKIPPED instead of OFFLINE.
        guard = HOOK_SOURCE[HOOK_SOURCE.index("    if mode:") :][:400]
        self.assertNotIn("SKIPPED", guard)


class ExplicitSessionIdentityCollisionTests(unittest.TestCase):
    """An explicit supervisor coordinate must never relocate another session."""

    def _inject(self, **overrides: Any):
        supervisor = _Supervisor(_sessions(), owner=IDENTITY_OWNER)
        body = {
            "provider": "CODEX",
            "project_id": "universe",
            "supervisor_session_id": TARGET_SESSION,
            "provider_session_ref": "01a03306-d73d-7e92-8e67-ade74c0ba51a",
            "node": "universe",
            "mode": "CONDUCTOR",
        }
        body.update(overrides)
        return supervisor, body

    def test_identity_owner_collision_conflicts_instead_of_relocating(self) -> None:
        supervisor, body = self._inject()
        with self.assertRaises(UniverseError) as caught:
            perform_session_ref_inject(
                session_supervisor=supervisor,
                multi_rooms=_Rooms(),
                body=body,
                environment={},
            )
        self.assertEqual(caught.exception.code, "SESSION_IDENTITY_OWNER_CONFLICT")
        self.assertEqual(int(caught.exception.status), 409)
        self.assertEqual(
            supervisor.relocated,
            [],
            "the identity owner must not be relocated by a collision",
        )

    def test_conflict_is_raised_before_any_location_rebind(self) -> None:
        supervisor, body = self._inject()
        try:
            perform_session_ref_inject(
                session_supervisor=supervisor,
                multi_rooms=_Rooms(),
                body=body,
                environment={},
            )
        except UniverseError:
            pass
        self.assertNotIn(IDENTITY_OWNER, supervisor.relocated)
        self.assertNotIn(TARGET_SESSION, supervisor.relocated)


class ServerOwnedCoordinateTests(unittest.TestCase):
    """Server-owned coordinates win when the caller omits node/mode."""

    def test_stored_mode_is_authoritative_when_body_omits_it(self) -> None:
        source = (ROOT / "tools" / "universe_server.py").read_text(encoding="utf-8")
        slice_ = source[
            source.index("stored_session: Mapping[str, Any] = {}") : source.index(
                "session_id = explicit_session_id or supervisor_session_id_for"
            )
        ]
        self.assertIn('stored_session.get("mode")', slice_)
        self.assertIn('stored_session.get("node")', slice_)
        self.assertIn("explicit_session_id", slice_)


if __name__ == "__main__":
    unittest.main()
