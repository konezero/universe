from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from universe_app.connection import UniverseError
from universe_app.session_runtime_credentials import (
    runtime_credential_ref,
    resolve_session_runtime_connection,
)


class SessionRuntimeCredentialTests(unittest.TestCase):
    def setUp(self):
        self.session = {
            "session_id": "session-one",
            "session_anchor_ref": "anchor-one",
            "current_project_id": "TEST",
            "state": "LIVE",
            "currentness": "CURRENT",
        }
        self.binding = {
            "session_id": "session-one",
            "origin_anchor_ref": "anchor-one",
            "binding_evidence_ref": "host://attachment-one",
            "endpoint": "http://127.0.0.1:17777",
            "token": "transport-only-secret",
            "runtime_currentness_observation": "CURRENT",
        }
        self.ref = runtime_credential_ref(self.binding)

    def resolve(self, **changes):
        request = {
            "project_id": "TEST",
            "session": self.session,
            "binding": self.binding,
            "session_anchor_ref": "anchor-one",
            "credential_ref": self.ref,
        }
        return resolve_session_runtime_connection(**{**request, **changes})

    def test_reference_is_opaque_and_exact_connection_is_host_only(self):
        self.assertNotIn(self.binding["token"], self.ref)
        self.assertNotIn(self.binding["endpoint"], self.ref)
        self.assertEqual(
            {key: self.binding[key] for key in ("endpoint", "token")}, self.resolve()
        )

    def test_cross_project_or_session_never_resolves(self):
        for changes in (
            {"project_id": "OTHER"},
            {"session_anchor_ref": "other"},
            {"binding": {**self.binding, "session_id": "other"}},
        ):
            with self.assertRaises(UniverseError):
                self.resolve(**changes)

    def test_stopped_stale_missing_and_rotated_binding_fail_closed(self):
        variants = [
            {"session": {**self.session, "state": "STOPPED"}},
            {"session": {**self.session, "currentness": "STALE"}},
            {"binding": None},
            {"binding": {**self.binding, "runtime_currentness_observation": "STALE"}},
            {"binding": {**self.binding, "token": "rotated-secret"}},
            {"credential_ref": "오래된-ref"},
        ]
        for changes in variants:
            with self.assertRaises(UniverseError) as caught:
                self.resolve(**changes)
            self.assertNotIn(self.binding["token"], str(caught.exception))


if __name__ == "__main__":
    unittest.main()
