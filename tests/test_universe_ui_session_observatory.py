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
        self.assertIn("function nodeModeSessionIsCurrent(session)", APP)
        self.assertIn("function vendorStreamStateForSession(session)", APP)
        self.assertIn("function startNewNodeModeSession(coordinate)", APP)
        self.assertIn("function resumeNodeModeSession(coordinate, session)", APP)
        self.assertIn('currentness === "CURRENT"', APP)
        self.assertIn('if (!["BOUND", "ANCHOR_OBSERVED"].includes(binding.state)) continue;', APP)
        self.assertIn("coordinate.sessions.push(source.session)", APP)
        self.assertIn("node-mode-item", CSS)
        self.assertIn(".node-mode-node", CSS)
        self.assertIn('data-active="false"', CSS)
        self.assertNotIn(
            ".filter((project) => !isProjectContainer(project))",
            APP[APP.index("function nodeModeCoordinates") : APP.index("function nodeModeStatusLabel")],
        )
        self.assertIn("function renderNodeModeGroup", APP)
        self.assertIn("node-mode-group-nested", APP)
        self.assertIn("childrenByParent.get(group.nodeId)", APP)
        self.assertIn(".node-mode-group-nested", CSS)

    def test_mode_click_expands_persistent_session_cards_without_auto_routing(self) -> None:
        self.assertIn("selectedModeCoordinateKey: null", APP)
        self.assertIn("function renderNodeModeSessionCards(coordinate)", APP)
        self.assertIn("node-mode-session-card", APP)
        self.assertIn("if (modeSelected)", APP)
        self.assertIn("list.append(renderNodeModeSessionCards(coordinate));", APP)
        open_slice = APP[
            APP.index("function openNodeModeCoordinate") : APP.index(
                "function renderNodeModes"
            )
        ]
        self.assertIn("state.selectedModeCoordinateKey = coordinate.key", open_slice)
        self.assertIn("state.selectedModeCoordinateKey = null", open_slice)
        self.assertNotIn("openProviderChatSummary", open_slice)
        self.assertIn("node-mode-session-cards", CSS)
        self.assertIn("node-mode-session-card", CSS)
        self.assertIn("const selectedAnchorKey = anchorSessionKey(session)", APP)
        self.assertIn("typeof options.isCurrent === \"function\"", APP)
        self.assertIn("if (!isCurrent()) return false", APP)
        self.assertIn("await resumeNodeModeSession(coordinate, session)", APP)
        self.assertIn("New session", APP)

    def test_session_graph_is_a_separate_read_only_navigation_surface(self) -> None:
        self.assertIn('data-primary-view="sessions"', HTML)
        self.assertIn('api("/v1/session-graph")', APP)
        self.assertIn("function buildSessionGraph()", APP)
        self.assertIn('state.view === "sessions"', APP)
        self.assertIn('item.kind === "session_anchor"', APP)
        self.assertIn("selectNodeModeSession(coordinate, session)", APP)
        self.assertIn("MODE_ANCHOR", APP)
        self.assertIn("SESSION_ANCHOR", APP)
        self.assertIn("TASK_FRAME", APP)
        self.assertIn('id="graph-legend"', HTML)
        self.assertIn('if (view === "sessions") fitGraphView();', APP)
        self.assertIn('state.view === "sessions" ? 0.12 : 0.5', APP)
        self.assertIn('state.selectedProject?.project_id', APP)
        self.assertIn('visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to)', APP)
        self.assertIn(".node-key.session-anchor", CSS)

    def test_chat_toggle_has_no_message_count_badge(self) -> None:
        self.assertNotIn('id="conversation-badge"', HTML)
        self.assertNotIn("conversationBadge:", APP)
        self.assertNotIn("function updateConversationBadge", APP)

    def test_project_entry_is_unified_and_planning_is_project_scoped(self) -> None:
        self.assertIn('id="add-project-rail-button"', HTML)
        self.assertNotIn('id="fresh-project-rail-button"', HTML)
        self.assertNotIn('id="import-project-rail-button"', HTML)
        self.assertIn('id="plan-project-button"', HTML)
        self.assertIn("addProjectRailButton:", APP)
        self.assertIn("planProjectButton:", APP)
        self.assertIn('/v1/project-connections/prepare', APP)
        self.assertIn('/v1/project-connections/apply', APP)
        self.assertIn("elements.projectDialog.showModal()", APP)

    def test_import_project_root_uses_native_directory_picker(self) -> None:
        self.assertIn('id="project-root-browse"', HTML)
        self.assertIn('class="project-root-picker"', HTML)
        self.assertIn("projectRootBrowse:", APP)
        self.assertIn('api("/v1/host/select-directory"', APP)
        self.assertIn('elements.projectForm.elements.namedItem("project_root")', APP)
        self.assertIn('result.status === "DIRECTORY_SELECTED"', APP)
        self.assertIn(".project-root-picker", CSS)

    def test_fresh_project_directory_uses_native_directory_picker(self) -> None:
        fresh_dialog = HTML[HTML.index('id="fresh-project-dialog"') :]
        self.assertIn('name="project_root"', fresh_dialog)
        self.assertIn('id="fresh-project-root-browse"', fresh_dialog)
        self.assertIn("freshProjectRootBrowse:", APP)
        self.assertIn("function selectFreshProjectRoot()", APP)
        self.assertIn(
            'elements.freshProjectForm.elements.namedItem("project_root")', APP
        )
        self.assertIn(
            'project_root: String(form.get("project_root") || "").trim()', APP
        )

    def test_release_import_uses_native_file_pickers(self) -> None:
        release_dialog = HTML[HTML.index('id="release-dialog"') :]
        self.assertIn('id="release-database-browse"', release_dialog)
        self.assertIn('id="release-manifest-browse"', release_dialog)
        self.assertIn('class="host-path-picker"', release_dialog)
        self.assertIn('api("/v1/host/select-file"', APP)
        self.assertIn('"RELEASE_DATABASE"', APP)
        self.assertIn('"RELEASE_MANIFEST"', APP)
        self.assertIn(".host-path-picker", CSS)

    def test_release_proposal_renders_current_lifecycle_plan_shape(self) -> None:
        start = APP.index("function showReleaseProposal(proposal)")
        end = APP.index("async function proposeProjectRelease", start)
        renderer = APP[start:end]
        self.assertIn("Array.isArray(plan.actions)", renderer)
        self.assertIn("Array.isArray(plan.collisions)", renderer)
        self.assertIn("plan.installed_runtime?.state", renderer)
        self.assertIn("plan.project_host_preflight", renderer)
        self.assertNotIn("for (const action of proposal.plan.actions)", renderer)

    def test_release_catalog_selects_target_and_exposes_apply(self) -> None:
        release_dialog = HTML[HTML.index('id="release-dialog"') :]
        self.assertIn('id="release-target-project"', release_dialog)
        catalog_start = APP.index("function renderReleaseCatalog()")
        catalog_end = APP.index("function showReleaseProposal", catalog_start)
        catalog = APP[catalog_start:catalog_end]
        self.assertIn("state.selectedReleaseTargetProjectId", catalog)
        self.assertIn("visibleProjects()", catalog)
        self.assertIn("project.project_root", catalog)
        self.assertNotIn("action.disabled = !state.selectedProject", catalog)
        self.assertNotIn("state.selectedProject?.project_id", catalog)
        proposal_start = APP.index("function showReleaseProposal(proposal)")
        proposal_end = APP.index("async function proposeProjectRelease", proposal_start)
        proposal_renderer = APP[proposal_start:proposal_end]
        self.assertIn("applyProjectRelease(proposal, applyButton)", proposal_renderer)
        self.assertIn("async function applyProjectRelease", APP)
        self.assertIn("/release-proposals/apply`,", APP)
        self.assertIn('approval: "APPROVED"', APP)
        self.assertIn("proposal.release_database_sha256", APP)

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
        self.assertIn("function renderNodeModeSessionCards(coordinate)", APP)
        self.assertIn("const opened = await openProviderChatSession(room, {", APP)
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
        self.assertIn('id="session-summary-new"', HTML)
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

    def test_session_popup_separates_resume_and_new_provider_session(self) -> None:
        connection_slice = APP[
            APP.index("function renderSessionSummaryConnection") : APP.index(
                "function renderProviderChatSummary"
            )
        ]
        self.assertIn('["MASTER", "CONDUCTOR"].includes(mode)', connection_slice)
        self.assertIn('"Continue with profile"', connection_slice)
        self.assertIn('"Start new session"', connection_slice)
        self.assertIn('sessionAction = "RESUME"', APP)
        self.assertIn('connectSessionSummaryProviderModel("NEW")', APP)
        self.assertIn("prepareBody.session_action = options.sessionAction", APP)
        self.assertIn(
            'const validSelected = selected && models.includes(selected) ? selected : ""',
            APP,
        )

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

    def test_conductor_room_uses_resident_session_before_planning_binding(self) -> None:
        self.assertIn("const residentSessionReady = Boolean(", APP)
        self.assertIn('session?.resident === true', APP)
        self.assertIn(
            'state.conductorRuntimeBinding?.status === "BOUND" || residentSessionReady',
            APP,
        )
        self.assertIn('"Provider setup required"', APP)
        self.assertIn('"LLM connected / Auto-approve " + autoApprove', APP)

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

    def test_action_inbox_excludes_governance_approval_controls(self) -> None:
        self.assertNotIn("/v1/governance/proposals/", APP)
        self.assertNotIn("async function decideGovernanceProposal", APP)
        self.assertNotIn('decideGovernanceProposal(proposal, "APPROVE")', APP)
        self.assertNotIn('decideGovernanceProposal(proposal, "CANCEL")', APP)
        self.assertNotIn('"proposal-cancel", "Cancel"', APP)

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
                "function renderComposerState"
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

    def test_semantic_project_graph_is_separate_from_session_graph(self) -> None:
        self.assertIn('/semantic-graph', APP)
        self.assertIn('function buildSemanticProjectGraph', APP)
        self.assertIn('projection only', APP)
        self.assertIn('if (state.view === "semantic")', APP)

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


    def test_provider_session_stream_requires_verified_identity(self) -> None:
        eligible_slice = APP[
            APP.index("function providerSessionRoomIsEligible") : APP.index(
                "function providerSessionRoomCacheFor"
            )
        ]
        openable_slice = APP[
            APP.index("function providerSessionRoomIsOpenable") : APP.index(
                "function providerSessionUnreadCount"
            )
        ]
        self.assertIn('identityState === "VERIFIED"', eligible_slice)
        self.assertIn('currentness === "CURRENT"', eligible_slice)
        self.assertIn('identityState === "VERIFIED"', openable_slice)

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
