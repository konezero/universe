from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "tools" / "universe_ui" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "universe_ui" / "styles.css").read_text(encoding="utf-8")


class SessionObservatoryUiContractTests(unittest.TestCase):
    def test_conversation_expansion_escapes_and_restores_the_dock(self) -> None:
        self.assertIn('id="conversation-expand"', HTML)
        expand_slice = APP[
            APP.index('elements.conversationExpand.addEventListener("change"') :
            APP.index("elements.goalPlanMap?.addEventListener")
        ]
        self.assertIn("document.body.append(elements.conversationLayer)", expand_slice)
        self.assertIn("parent.insertBefore(", expand_slice)
        self.assertIn('classList.toggle("expanded", expanded)', expand_slice)

    def test_goal_plan_editor_preserves_revision_and_selection(self) -> None:
        self.assertIn('class="goal-plan-toolbar"', HTML)
        self.assertIn('id="edit-selected-goal"', HTML)
        self.assertIn('state.selectedGoalId = state.goals[0]?.goal_id || null;', APP)
        self.assertIn(
            'revision: state.goals.find((goal) => goal.goal_id === goalId)?.revision',
            APP,
        )
        goal_dialog = HTML[HTML.index('id="goal-dialog"') : HTML.index('id="milestone-dialog"')]
        self.assertIn('value="ACTIVE"', goal_dialog)
        self.assertNotIn('value="IN_PROGRESS"', goal_dialog)

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
        self.assertIn('id="session-summary-provider"', HTML)
        self.assertIn('id="session-summary-model"', HTML)
        self.assertIn('id="session-summary-effort"', HTML)
        self.assertIn('id="session-summary-connect"', HTML)
        self.assertIn('id="session-summary-open"', HTML)
        self.assertIn("openProviderChatSummary(room)", APP)
        self.assertIn("renderProviderChatSummary", APP)
        rail_slice = APP[
            APP.index("function renderSessionRail") :
            APP.index("function renderSessionObservatory")
        ]
        self.assertNotIn("await activateAnchorSession(boundSession)", rail_slice)
        self.assertIn("session-summary-facts", CSS)

    def test_master_popup_attaches_exact_session_before_switching_main_chat(self) -> None:
        self.assertIn("async function attachSelectedMasterSession(session)", APP)
        self.assertIn('"/v1/sessions/inject"', APP)
        self.assertIn("provider_session_ref: providerSessionRef", APP)
        self.assertIn("provider: session.provider", APP)
        self.assertIn("expectedSessionRef", APP)
        self.assertIn("expectedModel", APP)
        self.assertIn("expectedEffort", APP)
        connect_slice = APP[
            APP.index("async function connectSessionSummaryProviderModel") : APP.index(
                "function sessionConnectionText"
            )
        ]
        self.assertLess(
            connect_slice.index("await callProjectMaster"),
            connect_slice.index("elements.sessionSummaryDialog.close()"),
        )
        self.assertIn("session-summary-connection", CSS)

    def test_provider_profiles_use_one_provider_model_effort_dialog(self) -> None:
        self.assertIn('id="provider-profile-dialog"', HTML)
        self.assertIn('id="provider-profile-provider"', HTML)
        self.assertIn('id="provider-profile-model"', HTML)
        self.assertIn('id="provider-profile-model-custom"', HTML)
        self.assertIn('id="provider-profile-effort"', HTML)
        self.assertIn("function openProviderProfileDialog", APP)
        self.assertIn("async function submitProviderProfile", APP)
        self.assertIn("body: { provider, model_ref: modelRef, effort }", APP)
        self.assertIn("provider-profile-row", CSS)
        self.assertIn("provider-profile-dialog", CSS)

    def test_hidden_view_and_bounded_tail_are_wired(self) -> None:
        self.assertIn('id="session-rail-show-hidden"', HTML)
        self.assertIn("providerChatShowHidden", APP)
        self.assertIn('/v1/session-observer/tail', APP)
        self.assertIn("window.setInterval(tailProviderSessions, 4000)", APP)
        self.assertIn("session-rail-row", CSS)

    def test_selected_anchor_session_receives_ephemeral_provider_tail(self) -> None:
        self.assertIn("providerChatRoomForSupervisorSession", APP)
        self.assertIn("providerLiveDelivery", APP)
        detail_slice = APP[
            APP.index("function renderSelectedSessionDetail") : APP.index(
                "async function api"
            )
        ]
        self.assertIn("Live provider output", detail_slice)
        self.assertIn("state.providerLiveDeltas[room.chat_key]", detail_slice)
        self.assertIn("session-detail-live", detail_slice)
        tail_slice = APP[
            APP.index("async function tailProviderSessions") : APP.index(
                "async function discoverProviderActivitySources"
            )
        ]
        self.assertIn("state.providerLiveDelivery[room.chat_key]", tail_slice)
        self.assertIn("renderSelectedSessionDetail();", tail_slice)
        self.assertIn("session-detail-live-feed", CSS)
        self.assertLess(
            HTML.index('id="session-observatory-detail"'),
            HTML.index('id="session-observatory-list"'),
        )

    def test_master_session_can_rebind_to_registered_project(self) -> None:
        self.assertIn('id="session-working-directory-project"', HTML)
        self.assertIn('id="session-working-directory-apply"', HTML)
        self.assertIn("function renderSessionWorkingDirectory", APP)
        self.assertIn("async function rebindSelectedSessionWorkingDirectory", APP)
        self.assertIn("/working-directory`,", APP)
        rebind_slice = APP[
            APP.index("async function rebindSelectedSessionWorkingDirectory") : APP.index(
                "async function api"
            )
        ]
        self.assertIn("expected_version: session.row_version", rebind_slice)
        self.assertIn("await refreshSupervisorSessions()", rebind_slice)
        self.assertIn("session?.provider_session_attached", APP)
        self.assertIn("session-working-directory", CSS)
        self.assertIn(".session-working-directory.hidden", CSS)

    def test_approval_uses_server_owned_canonical_route(self) -> None:
        self.assertIn("/v1/governance/proposals/", APP)
        decision_slice = APP[
            APP.index("async function decideGovernanceProposal") :
            APP.index("function requestedPermissionSummary")
        ]
        self.assertNotIn("commander_surface", decision_slice)
        self.assertNotIn("idempotency_key", decision_slice)
        self.assertIn('decideGovernanceProposal(proposal, "APPROVE")', APP)
        self.assertIn('decideGovernanceProposal(proposal, "CANCEL")', APP)
        self.assertIn('"proposal-cancel", "Cancel"', APP)

    def test_actions_are_separate_from_chat_and_keep_scroll_stable(self) -> None:
        self.assertIn('id="action-inbox-button"', HTML)
        self.assertIn('id="action-inbox-dialog"', HTML)
        self.assertIn('id="action-inbox-list"', HTML)
        self.assertIn("function renderActionInbox", APP)
        self.assertIn("function pendingActionItems", APP)
        self.assertIn("function finishRoomMessageRender", APP)
        self.assertIn("CANCELLATION_REQUESTED", APP)
        self.assertIn("/v1/conductor-room/delegations/", APP)
        message_slice = APP[
            APP.index("function renderRoomMessages") : APP.index(
                "function renderGovernanceProposalCard"
            )
        ]
        self.assertNotIn("renderGovernanceProposalCard(proposal)", message_slice)
        self.assertNotIn("renderPermissionCard(permission)", message_slice)
        self.assertNotIn("scrollRoomToPendingAction", APP)
        self.assertIn("action-inbox-dialog", CSS)

    def test_project_master_delivery_labels_distinguish_queue_and_acceptance(
        self,
    ) -> None:
        self.assertIn('deliveryState === "QUEUED_FOR_MASTER"', APP)
        self.assertIn('deliveryState === "ACCEPTED_BY_MASTER"', APP)
        self.assertNotIn("DELIVERED_TO_MASTER", APP)

    def test_project_graph_labels_functional_nodes_with_seed_provenance(self) -> None:
        self.assertIn("Project Seed node", HTML)
        self.assertIn('projection_origin: "PROJECT_SEED"', APP)
        self.assertIn("projection_seed_id: projection?.seed_id", APP)
        self.assertIn("projection_source_ref: projection?.source?.ref", APP)
        self.assertIn('addDetail(grid, "Origin", "Project Seed")', APP)
        self.assertIn('addDetail(grid, "Seed source", data.projection_source_ref)', APP)

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
        self.assertIn('binding.is_default === true', rail_slice)
        self.assertIn('binding.observer_currentness === "CURRENT"', rail_slice)
        self.assertNotIn("binding.anchor_temporality", rail_slice)


if __name__ == "__main__":
    unittest.main()
