from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import (  # noqa: E402
    UniverseHTTPServer,
    _public_pty_binding,
)

APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")

PROJECT = "universe"
BOUND_ANCHOR = "session_anchor_bound"
CURRENT_ANCHOR = "MASTER-CURRENT-DIFFERENT"


class _ProjectionServer:
    """Borrow the projection methods under test without a live service."""

    _pty_binding_material = UniverseHTTPServer._pty_binding_material
    _join_live_pty_bindings = UniverseHTTPServer._join_live_pty_bindings
    list_git_work_history = UniverseHTTPServer.list_git_work_history

    def __init__(self, terminals=(), sessions=None, events=()):
        self._terminals = list(terminals)
        self._sessions = dict(sessions or {})
        self._events = list(events)
        self.session_supervisor = SimpleNamespace(get_session=self._get_session)
        self.store = SimpleNamespace(list_events=self.list_events)

    def _get_session(self, session_id):
        return self._sessions[session_id]

    def _session_anchor_terminal_host(self):
        return SimpleNamespace(list_sessions=lambda: list(self._terminals))

    def list_events(self, project_id, limit=200):
        return list(self._events)


def _terminal(**overrides):
    terminal = {
        "terminal_id": "term_bound",
        "state": "LIVE",
        "project_id": PROJECT,
        "mode": "MASTER",
        "provider": "CLAUDE",
        "pid": 4242,
        "backend_owner": "RUST_RECONNECTION_HOST",
        "reconnection_host_id": "host-bound",
        "host_session_ref": "host-bound",
        "host_runtime_versions": {
            "server_version": "UniverseLocal/1",
            "supervisor_version": "UniverseSupervisor/1",
            "host_version": "UniverseSessionHost/1",
            "pty_version": "UniverseConPty/1",
        },
        "host_compatibility": "CURRENT",
        "host_reconnect_eligible": True,
        "host_protocol_state": "INITIALIZED",
        "created_at": "2026-08-25T00:00:00Z",
        "supervisor_session_id": "session_bound",
        "active_session_anchor_ref": BOUND_ANCHOR,
    }
    terminal.update(overrides)
    return terminal


def _bound_session():
    return {
        "session_bound": {
            "provider": "CLAUDE",
            "provider_session_ref": "vendor-ref",
            "last_seen_at": "2026-08-25T01:00:00Z",
            "mode": "MASTER",
        }
    }


def _anchor_record(**overrides):
    record = {
        "mode": "MASTER",
        "session_id": "codex:other",
        "session_anchor_ref": CURRENT_ANCHOR,
        "temporality": "CURRENT",
        "currentness": "CURRENT",
        "currentness_source": "MODE_CURRENT_ANCHOR",
        "state": "CURRENT",
        "active_ing": False,
        "last_seen_at": "2026-08-21T02:48:05+00:00",
        "pty_binding": dict(_public_pty_binding(None)),
    }
    record.update(overrides)
    return record


class AnchorPtyProjectionTests(unittest.TestCase):
    """A verified binding is one record; liveness never implies authority."""

    def test_verified_binding_attaches_observation_without_promoting_state(self) -> None:
        server = _ProjectionServer(
            terminals=[_terminal()], sessions=_bound_session()
        )
        record = _anchor_record(session_anchor_ref=BOUND_ANCHOR)
        joined = server._join_live_pty_bindings(PROJECT, [record])

        self.assertEqual(len(joined), 1, "one binding must not render two cards")
        only = joined[0]
        self.assertEqual(only["currentness"], "CURRENT")
        self.assertEqual(only["state"], "CURRENT")
        self.assertFalse(only["active_ing"])
        self.assertEqual(only["pty_binding"]["status"], "BOUND")
        self.assertEqual(only["pty_binding"]["terminal_id"], "term_bound")
        self.assertEqual(only["pty_binding"]["pid"], 4242)
        self.assertEqual(
            only["pty_binding"]["backend_owner"], "RUST_RECONNECTION_HOST"
        )
        self.assertEqual(only["pty_binding"]["reconnection_host_id"], "host-bound")
        self.assertEqual(only["pty_binding"]["host_compatibility"], "CURRENT")
        self.assertTrue(only["pty_binding"]["host_reconnect_eligible"])

    def test_live_pty_without_anchor_cannot_create_a_session(self) -> None:
        server = _ProjectionServer(
            terminals=[
                _terminal(
                    terminal_id="term_pending",
                    supervisor_session_id="",
                    active_session_anchor_ref=None,
                )
            ]
        )
        joined = server._join_live_pty_bindings(PROJECT, [])

        self.assertEqual(joined, [])

    def test_verified_provider_identity_joins_current_anchor_to_live_pty(self) -> None:
        server = _ProjectionServer(
            terminals=[_terminal()], sessions=_bound_session()
        )
        current = _anchor_record(
            observer_session_ref="claude-code:vendor-ref"
        )

        joined = server._join_live_pty_bindings(PROJECT, [current])

        self.assertEqual(len(joined), 1, "one provider session must render one card")
        only = joined[0]
        self.assertEqual(only["session_anchor_ref"], CURRENT_ANCHOR)
        self.assertEqual(only["currentness"], "CURRENT")
        self.assertEqual(only["state"], "CURRENT")
        self.assertEqual(only["pty_binding"]["terminal_id"], "term_bound")

    def test_shared_provider_identity_only_joins_the_current_record(self) -> None:
        """A PAST/BEYOND record must not fan out onto the same live PTY."""

        server = _ProjectionServer(
            terminals=[_terminal()], sessions=_bound_session()
        )
        current = _anchor_record(
            observer_session_ref="claude-code:vendor-ref"
        )
        past = _anchor_record(
            session_id="claude-code-past",
            session_anchor_ref="session_anchor_past",
            temporality="PAST",
            currentness="PAST",
            currentness_source="SESSION_STORE",
            state="READY",
            observer_session_ref="claude-code:vendor-ref",
        )

        joined = server._join_live_pty_bindings(PROJECT, [current, past])

        self.assertEqual(len(joined), 2)
        bound_records = [
            item for item in joined if item["pty_binding"]["status"] == "BOUND"
        ]
        self.assertEqual(
            len(bound_records),
            1,
            "only the CURRENT record may receive the PTY observation",
        )
        self.assertEqual(bound_records[0]["session_anchor_ref"], CURRENT_ANCHOR)
        stale = next(item for item in joined if item["session_anchor_ref"] == "session_anchor_past")
        self.assertEqual(stale["state"], "READY")
        self.assertEqual(stale["pty_binding"]["status"], "UNBOUND")

    def test_live_binding_does_not_borrow_a_different_current_anchor(self) -> None:
        server = _ProjectionServer(
            terminals=[_terminal()], sessions=_bound_session()
        )
        joined = server._join_live_pty_bindings(PROJECT, [_anchor_record()])

        self.assertEqual(len(joined), 1, "PTY liveness cannot synthesize another Anchor")
        current = joined[0]

        self.assertEqual(current["currentness"], "CURRENT")
        self.assertEqual(
            current["pty_binding"]["status"],
            "UNBOUND",
            "the Mode Current Anchor must not claim another terminal's PTY",
        )

    def test_records_without_a_live_pty_stay_unbound(self) -> None:
        server = _ProjectionServer(terminals=[])
        joined = server._join_live_pty_bindings(PROJECT, [_anchor_record()])
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0]["pty_binding"]["status"], "UNBOUND")

    def test_only_live_terminals_in_this_project_are_joined(self) -> None:
        server = _ProjectionServer(
            terminals=[
                _terminal(terminal_id="term_exited", state="EXITED"),
                _terminal(terminal_id="term_other", project_id="other-project"),
            ],
            sessions=_bound_session(),
        )
        joined = server._join_live_pty_bindings(
            PROJECT, [_anchor_record(session_anchor_ref=BOUND_ANCHOR)]
        )
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0]["pty_binding"]["status"], "UNBOUND")


class GitWorkHistoryTests(unittest.TestCase):
    """COMMIT/PUSH history carries exact Anchor and terminal attribution."""

    def _events(self):
        return [
            {
                "event_id": "git_work_exact",
                "event_type": "GIT_WORK_STATUS",
                "created_at": "2026-08-25T01:10:00Z",
                "payload": {
                    "operation": "COMMIT",
                    "state": "COMPLETED",
                    "exit_code": 0,
                    "short_sha": "abc1234",
                    "branch": "main",
                    "session_anchor_ref": BOUND_ANCHOR,
                    "terminal_id": "term_bound",
                },
            },
            {
                "event_id": "git_work_unattributed",
                "event_type": "GIT_WORK_STATUS",
                "created_at": "2026-08-25T01:05:00Z",
                "payload": {
                    "operation": "PUSH",
                    "state": "FAILED",
                    "exit_code": 1,
                    "remote": "origin",
                },
            },
            {
                "event_id": "unrelated",
                "event_type": "TEST_WORK_STATUS",
                "created_at": "2026-08-25T01:00:00Z",
                "payload": {"tier": "fast"},
            },
        ]

    def test_history_reports_exact_and_unattributed_entries(self) -> None:
        server = _ProjectionServer(events=self._events())
        entries = server.list_git_work_history(PROJECT)["entries"]

        self.assertEqual(len(entries), 2, "only COMMIT/PUSH milestones are history")
        exact = entries[0]
        self.assertEqual(exact["operation"], "COMMIT")
        self.assertEqual(exact["attribution"], "EXACT")
        self.assertEqual(exact["session_anchor_ref"], BOUND_ANCHOR)
        self.assertEqual(exact["terminal_id"], "term_bound")

        unattributed = entries[1]
        self.assertEqual(unattributed["operation"], "PUSH")
        self.assertEqual(unattributed["attribution"], "UNATTRIBUTED")
        self.assertIsNone(unattributed["session_anchor_ref"])
        self.assertIsNone(unattributed["terminal_id"])

    def test_history_filters_to_one_session_anchor(self) -> None:
        server = _ProjectionServer(events=self._events())
        result = server.list_git_work_history(
            PROJECT, session_anchor_ref=BOUND_ANCHOR
        )
        self.assertEqual(
            [item["event_id"] for item in result["entries"]], ["git_work_exact"]
        )
        self.assertEqual(result["session_anchor_ref"], BOUND_ANCHOR)


class ActionInboxUiContractTests(unittest.TestCase):
    """PTY-header Actions separates Active / History / Approvals."""

    def test_actions_dialog_renders_three_sections(self) -> None:
        render = APP[
            APP.index("function renderActionInbox()") : APP.index(
                "async function openActionInbox()"
            )
        ]
        labels = re.findall(r"appendSection\(\s*\"([A-Za-z ]+)\"", render)
        self.assertEqual(labels, ["Active work", "History", "Approvals"])

    def test_actions_title_never_interpolates_a_missing_coordinate(self) -> None:
        self.assertNotIn(
            "${state.conversationTarget.alias || state.conversationTarget.projectId}"
            " actions",
            APP,
        )
        title = APP[
            APP.index("function actionInboxTitle()") : APP.index(
                "function actionInboxApprovals()"
            )
        ]
        self.assertIn('value !== "null"', title)
        self.assertIn('value !== "undefined"', title)
        self.assertIn('return label ? `${label} actions` : "Actions";', title)

    def test_history_is_read_from_the_authoritative_git_endpoint(self) -> None:
        loader = APP[
            APP.index("async function loadActionInboxHistory()") : APP.index(
                "function renderGitHistoryActionCard("
            )
        ]
        self.assertIn("/git-work-history", loader)
        self.assertIn("session_anchor_ref=", loader)
        card = APP[
            APP.index("function renderGitHistoryActionCard(") : APP.index(
                "function renderApprovalActionCard("
            )
        ]
        self.assertIn('entry.attribution === "EXACT"', card)
        self.assertIn("entry.terminal_id", card)
        self.assertIn("UNATTRIBUTED", card)

    def test_ui_never_synthesizes_sessions_or_currentness_from_pty(self) -> None:
        self.assertNotIn("function sessionFromPtyTerminal", APP)
        coordinates = APP[
            APP.index("function nodeModeCoordinates()") : APP.index(
                "function nodeModeStatusLabel(coordinate)"
            )
        ]
        self.assertNotIn("state.supervisorTerminals", coordinates)
        rail = APP[
            APP.index("function renderSessionRail()") : APP.index(
                "function renderSessionObservatory()"
            )
        ]
        self.assertNotIn("state.supervisorTerminals", rail)


if __name__ == "__main__":
    unittest.main()
