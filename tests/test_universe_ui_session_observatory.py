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

    def test_left_rail_renders_nodes_with_fixed_modes_and_session_state(self) -> None:
        self.assertIn('id="node-mode-list"', HTML)
        self.assertIn('id="node-mode-count"', HTML)
        self.assertIn('class="section-heading legacy-rail-surface"', HTML)
        self.assertIn('class="project-list legacy-rail-surface"', HTML)
        self.assertIn("function normalizeNodeModeNode(nodeId)", APP)
        self.assertIn("function nodeModeCatalog(project)", APP)
        self.assertIn('const modes = isUniverseHome ? ["MASTER", "CONDUCTOR"] : ["MASTER"];', APP)
        self.assertIn("function nodeModeSessionIsActive(session)", APP)
        self.assertIn('if (!["BOUND", "ANCHOR_OBSERVED"].includes(binding.state)) continue;', APP)
        self.assertIn("if (coordinate.room)", APP)
        self.assertIn("node-mode-item", CSS)
        self.assertIn(".node-mode-node", CSS)
        self.assertIn('data-active="false"', CSS)

    def test_right_chat_dock_and_sliding_inspector_contract(self) -> None:
        self.assertIn('class="conductor-panel glass-panel"', HTML)
        self.assertIn('id="conversation-layer"', HTML)
        self.assertIn('id="action-inbox-button"', HTML)
        self.assertIn('id="close-inspector"', HTML)
        header_start = HTML.index('<header class="conversation-layer-header">')
        self.assertLess(
            HTML.index('id="action-inbox-button"', header_start),
            HTML.index('id="room-message-list"', header_start),
        )
        self.assertIn(
            ".app-shell.mockup-shell > .graph-workspace > .conductor-panel",
            CSS,
        )
        self.assertIn(
            "var(--inspector-track-width)",
            CSS,
        )
        self.assertIn(
            "body.inspector-open .app-shell.mockup-shell > .inspector",
            CSS,
        )
        self.assertIn('id="chat-resize-handle"', HTML)
        self.assertIn("if (coordinate.room)", APP)
        self.assertIn("openProviderChatSummary(coordinate.room)", APP)
        self.assertIn("initChatPanelResize()", APP)
        self.assertIn("--chat-panel-width: 380px", CSS)
        self.assertIn("chat-resize-handle", CSS)
        self.assertIn("--inspector-track-width: 0px", CSS)
        self.assertIn("grid-column: 3", CSS)
        self.assertIn("grid-column: 4", CSS)
        self.assertIn("grid-template-columns 220ms ease", CSS)
        self.assertIn("transform: translateX(-100%)", CSS)
        self.assertIn("transition:\n    transform 220ms ease", CSS)

    def test_project_and_universe_selection_do_not_auto_open_inspector(self) -> None:
        refresh_slice = APP[
            APP.index("async function refresh(") : APP.index("function projectDisplayName")
        ]
        self.assertIn("revealInspector: false,", refresh_slice)
        self.assertIn("syncAssets: syncSelectedProject", refresh_slice)

        project_slice = APP[
            APP.index("function projectButton") : APP.index("function renderProjects")
        ]
        self.assertIn("revealInspector: false", project_slice)

        node_mode_slice = APP[
            APP.index("function selectNodeModeNode") : APP.index(
                "function openNodeModeCoordinate"
            )
        ]
        self.assertIn("revealInspector: false", node_mode_slice)

        universe_slice = APP[
            APP.index('if (selected.kind === "universe")') : APP.index(
                'if (selected.kind === "project")'
            )
        ]
        self.assertIn("state.inspectorDismissed = true;", universe_slice)
        self.assertIn("renderDetails();", universe_slice)

        graph_node_slice = APP[
            APP.index('if (selected.kind === "project")') : APP.index(
                'if (["system", "related", "focus"].includes(selected.kind))'
            )
        ]
        self.assertIn("revealInspector: true", graph_node_slice)
        self.assertIn('showInspectorTab("details")', graph_node_slice)

        explicit_surface = APP[
            APP.index("function openInspectorSurface") : APP.index("function fitGraphView")
        ]
        self.assertIn("state.inspectorDismissed = false;", explicit_surface)
        self.assertIn('document.body.classList.add("inspector-open")', explicit_surface)

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

    def test_master_popup_opens_current_project_master_without_provider_ref(self) -> None:
        attach_slice = APP[
            APP.index("async function attachSelectedMasterSession(session)") : APP.index(
                "async function connectSessionSummaryProviderModel"
            )
        ]
        self.assertIn("await callProjectMaster(projectId", attach_slice)
        self.assertIn("provider,", attach_slice)
        self.assertIn("anchorKey: anchorSessionKey(session)", attach_slice)
        self.assertNotIn("/v1/sessions/inject", attach_slice)
        self.assertNotIn("providerSessionRef", attach_slice)
        self.assertNotIn("provider_session_ref", attach_slice)
        activate_slice = APP[
            APP.index("async function activateAnchorSession") : APP.index(
                "function sessionRailActivityLabel"
            )
        ]
        master_branch_start = activate_slice.index("if (session.mode === \"MASTER\")")
        room_branch_start = activate_slice.index("if (room)")
        master_branch = activate_slice[master_branch_start:room_branch_start]
        self.assertIn("await attachSelectedMasterSession(session)", master_branch)
        self.assertIn("await refreshSupervisorSessions()", master_branch)
        self.assertIn("expandConversationLayer();", master_branch)
        self.assertIn("return;", master_branch)
        self.assertNotIn("openProviderChatSession(room)", master_branch)
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

    def test_conductor_session_uses_the_same_lazy_prepare_attach_route(self) -> None:
        self.assertIn('async function callUniverseConductor(options = {})', APP)
        self.assertIn('"/v1/conductor-session/prepare"', APP)
        self.assertIn("async function attachSelectedConductorSession(session)", APP)
        activate_slice = APP[
            APP.index("async function activateAnchorSession") : APP.index(
                "function sessionRailActivityLabel"
            )
        ]
        conductor_branch_start = activate_slice.index(
            'if (session.mode === "CONDUCTOR")'
        )
        conductor_branch = activate_slice[conductor_branch_start:]
        self.assertIn("await attachSelectedConductorSession(session)", conductor_branch)
        self.assertIn("await refreshSupervisorSessions()", conductor_branch)
        self.assertIn("expandConversationLayer();", conductor_branch)
        self.assertIn(
            'elements.returnToConductor.addEventListener("click", async () =>',
            APP,
        )

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


    def test_provider_session_background_events_keep_selected_transcript_focused(self) -> None:
        selection_slice = APP[
            APP.index("function syncSelectedProviderSessionState") : APP.index(
                "function mergeProviderSessionMessages"
            )
        ]
        self.assertIn("requestedKey && requestedKey !== selectedKey", selection_slice)
        self.assertIn("providerSessionRoomCacheFor(selectedKey)", selection_slice)

        payload_slice = APP[
            APP.index("function applyProviderSessionPayload") : APP.index(
                "function closeProviderSessionStream"
            )
        ]
        self.assertIn("markProviderSessionActivity(key, type, envelope)", payload_slice)
        self.assertIn("PROVIDER_SESSION_DELTA", payload_slice)
        self.assertIn("PROVIDER_SESSION_PERMISSION_RESOLVED", payload_slice)

        stream_slice = APP[
            APP.index("function openProviderSessionStream") : APP.index(
                "function openProviderChatSession"
            )
        ]
        self.assertIn("if (providerSessionRoomIsSelected(key))", stream_slice)
        self.assertIn("renderRoomMessages()", stream_slice)
        self.assertIn("renderSessionRail()", stream_slice)
        self.assertNotIn(
            "state.providerSessionMessages = dedupeProviderSessionMessages",
            stream_slice,
        )
        self.assertIn("syncProviderSessionSubscriptions();", APP)
        self.assertIn("providerSessionActivityState(room)", APP)
        self.assertIn("providerSessionUnreadCount(room)", APP)

if __name__ == "__main__":
    unittest.main()
