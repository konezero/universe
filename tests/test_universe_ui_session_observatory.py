from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "tools" / "universe_ui" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "universe_ui" / "styles.css").read_text(encoding="utf-8")


class SessionObservatoryUiContractTests(unittest.TestCase):
    def test_bound_sessions_group_by_current_universe_location(self) -> None:
        self.assertIn("binding.current_project_id", APP)
        self.assertIn("session-rail-location", APP)
        self.assertIn("Unbound vendor sessions", APP)
        self.assertIn("room.workspace_name", APP)
        self.assertLess(
            APP.index("for (const roomsAtLocation of boundGroups.values())"),
            APP.index("for (const roomsAtOrigin of unboundGroups.values())"),
        )

    def test_hidden_view_and_bounded_tail_are_wired(self) -> None:
        self.assertIn('id="session-rail-show-hidden"', HTML)
        self.assertIn("providerChatShowHidden", APP)
        self.assertIn('/v1/session-observer/tail', APP)
        self.assertIn("window.setInterval(tailProviderSessions, 4000)", APP)
        self.assertIn("session-rail-row", CSS)

    def test_approval_uses_server_owned_canonical_route(self) -> None:
        self.assertIn("/v1/governance/proposals/", APP)
        decision_slice = APP[
            APP.index("async function decideGovernanceProposal") :
            APP.index("function requestedPermissionSummary")
        ]
        self.assertNotIn("commander_surface", decision_slice)
        self.assertNotIn("idempotency_key", decision_slice)

    def test_browser_does_not_render_raw_provider_paths(self) -> None:
        activity_slice = APP[
            APP.index("function renderProviderActivitySources") :
            APP.index("function prefillsObservatoryInjectForm")
        ]
        self.assertNotIn("source.source_path", activity_slice)
        rail_slice = APP[
            APP.index("function renderSessionRail") :
            APP.index("function renderSessionObservatory")
        ]
        self.assertNotIn("provider_session_ref", rail_slice)


if __name__ == "__main__":
    unittest.main()
