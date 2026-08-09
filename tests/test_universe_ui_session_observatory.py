from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "tools" / "universe_ui" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "universe_ui" / "styles.css").read_text(encoding="utf-8")


class SessionObservatoryUiContractTests(unittest.TestCase):
    def test_sessions_group_into_collapsible_project_tree(self) -> None:
        self.assertIn("binding.current_project_id", APP)
        self.assertIn("sessionRailProjectIdentity", APP)
        self.assertIn("providerChatExpandedProjects", APP)
        self.assertIn("providerChatExpandedBranches", APP)
        self.assertIn("session-project-tree", APP)
        self.assertIn("session-project-toggle", APP)
        self.assertIn("session-project-branch-toggle", APP)
        self.assertIn('key: "unassigned"', APP)
        self.assertIn('label: "Unassigned sessions"', APP)
        self.assertIn('appendBranch("Current", group.current)', APP)
        self.assertIn('appendBranch("Past", group.past)', APP)
        self.assertIn('appendBranch("Unbound", group.unbound)', APP)
        self.assertIn("room.workspace_name", APP)
        self.assertLess(
            APP.index('appendBranch("Current", group.current)'),
            APP.index('appendBranch("Past", group.past)'),
        )

    def test_session_click_opens_summary_before_activation(self) -> None:
        self.assertIn('id="session-summary-dialog"', HTML)
        self.assertIn('id="session-summary-facts"', HTML)
        self.assertIn('id="session-summary-open"', HTML)
        self.assertIn("openProviderChatSummary(room)", APP)
        self.assertIn("renderProviderChatSummary", APP)
        rail_slice = APP[
            APP.index("function renderSessionRail") :
            APP.index("function renderSessionObservatory")
        ]
        self.assertNotIn("await activateAnchorSession(boundSession)", rail_slice)
        self.assertIn("session-summary-facts", CSS)

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
        self.assertIn("sessionRailActivityLabel(room)", rail_slice)
        self.assertIn("room.last_activity_at", APP)
        self.assertIn('["BOUND", "ANCHOR_OBSERVED"]', rail_slice)
        self.assertIn('binding.observer_currentness === "CURRENT"', rail_slice)
        self.assertNotIn("binding.anchor_temporality", rail_slice)


if __name__ == "__main__":
    unittest.main()
