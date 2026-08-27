from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "universe_ui" / "styles.css").read_text(encoding="utf-8")


class ProviderSessionUiContractTests(unittest.TestCase):
    def test_goal_scheduler_uses_current_conductor_receipt_route(self) -> None:
        start = APP.index("function currentConductorMutationSession()")
        end = APP.index("function renderMeetingFeaturePanel", start)
        scheduler = APP[start:end]
        self.assertIn('session.mode || "").toUpperCase() === "CONDUCTOR"', scheduler)
        self.assertIn("nodeModeSessionIsCurrent(session)", scheduler)
        self.assertIn("/v1/goal-automation-scheduler-mutation-receipts", scheduler)
        self.assertIn("prepared.receipt.receipt_id", scheduler)
        self.assertIn("session_anchor_ref: session.session_anchor_ref", scheduler)
        self.assertNotIn("/automation/scheduler`", scheduler)

    def test_applied_goal_work_plan_uses_conductor_advance_route(self) -> None:
        self.assertIn("async function advanceGoalAutomation()", APP)
        start = APP.index("async function advanceGoalAutomation()")
        end = APP.index("function renderMeetingFeaturePanel", start)
        delivery = APP[start:end]
        self.assertIn("/automation/advance`,", delivery)
        self.assertIn('approval: "ADVANCE"', delivery)
        self.assertIn("expected_goal_revision: goal.revision", delivery)
        self.assertNotIn("/master-handoffs", delivery)
        self.assertNotIn("task-frame", delivery.lower())
        self.assertIn('"Advance Conductor"', APP)

    def test_selected_session_uses_native_provider_endpoint_not_room_queue(self) -> None:
        self.assertIn('kind: "PROVIDER_SESSION"', APP)
        self.assertIn("async function openProviderChatSession(room, options = {})", APP)
        submit_slice = APP[
            APP.index("async function submitDispatch") : APP.index(
                "async function prepareProjectSeed"
            )
        ]
        self.assertIn("/v1/provider-sessions/", submit_slice)
        self.assertIn("/messages`,", submit_slice)
        provider_branch = submit_slice[
            submit_slice.index('kind === "PROVIDER_SESSION"') : submit_slice.index(
                'kind === "UNIVERSE_CONDUCTOR"'
            )
        ]
        self.assertNotIn("/v1/conductor-room/messages", provider_branch)
        self.assertNotIn("/v1/projects/", provider_branch)
        self.assertIn("Sent directly to the selected Provider Session", provider_branch)

    def test_mode_session_selection_keeps_the_exact_provider_coordinate(self) -> None:
        self.assertIn("selectedSupervisorAnchorKeysByMode: {}", APP)
        self.assertIn("async function selectNodeModeSession(coordinate, session)", APP)
        selection_slice = APP[
            APP.index("async function selectNodeModeSession") : APP.index(
                "function renderNodeModeSessionCards"
            )
        ]
        resume_slice = APP[
            APP.index("async function bindNodeModeSessionPty") : APP.index(
                "async function startNewNodeModeSession"
            )
        ]
        self.assertIn("[coordinate.key]: anchorSessionKey(session)", selection_slice)
        self.assertIn("openNodeModeSessionActions(coordinate, session)", selection_slice)
        self.assertNotIn("await resumeNodeModeSession(coordinate, session)", selection_slice)
        self.assertIn("createTerminalTab(coordinate, session)", resume_slice)
        self.assertIn("focusTerminalForSession(coordinate, session)", resume_slice)
        self.assertNotIn("attachProviderChatRoom", resume_slice)
        open_slice = APP[
            APP.index("async function openProviderChatSession") : APP.index(
                "function closeProjectRoomStream"
            )
        ]
        self.assertIn("session_anchor_ref:", open_slice)
        self.assertIn("vendor_session_id:", open_slice)
        self.assertIn("chat_key: chatKey", open_slice)
        self.assertIn("function providerSessionRoomIsOpenable(room)", APP)

    def test_mode_selection_never_infers_chat_from_default_or_activity(self) -> None:
        selected_slice = APP[
            APP.index("function nodeModeSelectedSession") : APP.index(
                "async function selectNodeModeSession"
            )
        ]
        self.assertIn("selectedSupervisorAnchorKeysByMode", selected_slice)
        self.assertNotIn("session.is_default", selected_slice)
        self.assertNotIn("sessions[0]", selected_slice)

    def test_cross_session_delegation_has_explicit_anchor_contract_and_rejoins_origin(self) -> None:
        self.assertIn("function beginCrossSessionDelegation(targetSession)", APP)
        self.assertIn("async function rejoinDelegationOrigin(delegation)", APP)
        delegation_slice = APP[
            APP.index("async function submitDispatch") : APP.index(
                "async function prepareProjectSeed"
            )
        ]
        branch = delegation_slice[
            delegation_slice.index('kind === "SESSION_DELEGATION"') : delegation_slice.index(
                'kind === "PROVIDER_SESSION"'
            )
        ]
        self.assertIn('api("/v1/conductor/delegations"', branch)
        self.assertIn("controlToken: true", branch)
        self.assertIn("origin_session_anchor_ref: originAnchorRef", branch)
        self.assertIn("target_session_anchor_ref: targetAnchorRef", branch)
        self.assertIn("origin_session_chat_key:", branch)
        self.assertIn("project_id: projectId", branch)
        self.assertIn('provider: "AUTO"', branch)
        self.assertIn("watchSessionDelegation(delegation.delegation_id", branch)
        self.assertNotIn("/v1/projects/", branch)
        self.assertNotIn("/v1/rooms/", branch)
        self.assertIn("await rejoinDelegationOrigin(delegation)", branch)
        self.assertIn("CROSS-SESSION DELEGATION / NOT DIRECT CHAT", APP)
        self.assertIn("session-delegation-message", CSS)

    def test_provider_session_stream_is_incremental_and_opaque(self) -> None:
        self.assertIn("function openProviderSessionStream(chatKey)", APP)
        self.assertIn("PROVIDER_SESSION_DELTA", APP)
        self.assertIn("PROVIDER_SESSION_PERMISSION", APP)
        self.assertIn("PROVIDER_SESSION_WORK_STATUS", APP)
        self.assertIn("PROVIDER_SESSION_ACTION", APP)
        self.assertIn("PROVIDER_SESSION_ACTION_DELETED", APP)
        self.assertIn("function workStatusNotificationText(workStatus)", APP)
        action_slice = APP[
            APP.index("function pendingActionItems") : APP.index(
                "function finishRoomMessageRender"
            )
        ]
        self.assertNotIn("governanceProposal", action_slice)
        self.assertNotIn("renderGovernanceProposalCard", APP)
        render_slice = action_slice[action_slice.index("function renderActionInbox") :]
        self.assertNotIn("renderGitActionCard", render_slice)
        self.assertNotIn("Recent activity", render_slice)
        self.assertIn("state.providerSessionPermissions", APP)
        self.assertIn("function closeProviderSessionStream(chatKey)", APP)
        stream_slice = APP[
            APP.index("function openProviderSessionStream") : APP.index(
                "async function openProviderChatSession"
            )
        ]
        self.assertIn("state.providerSessionStreams[key]", stream_slice)
        self.assertIn("envelope.chat_key", stream_slice)
        self.assertNotIn("provider_session_ref", stream_slice)
        self.assertNotIn("source_path", stream_slice)
    def test_provider_session_streams_are_chat_key_scoped_and_backgrounded(self) -> None:
        self.assertIn("providerSessionStreams: {}", APP)
        self.assertIn("providerSessionRoomCaches: {}", APP)
        self.assertIn("providerSessionUnreadCount(room)", APP)
        self.assertIn("requestedKey && requestedKey !== selectedKey", APP)
        self.assertIn("function providerSessionRoomIsEligible(room)", APP)
        self.assertIn("function syncProviderSessionSubscriptions()", APP)
        self.assertIn("syncProviderSessionSubscriptions();", APP)
        stream_slice = APP[
            APP.index("function openProviderSessionStream") : APP.index(
                "function openProviderChatSession"
            )
        ]
        self.assertIn("state.providerSessionStreams[key]", stream_slice)
        self.assertIn("providerSessionRoomCacheFor(key)", stream_slice)
        self.assertIn("markProviderSessionActivity(key, type, envelope)", APP)
        self.assertIn("providerSessionRoomIsSelected(key)", stream_slice)
        self.assertNotIn("closeProviderSessionStream();", stream_slice)
        focus_slice = APP[
            APP.index("function returnToUniverseConductor") : APP.index(
                "async function callProjectMaster"
            )
        ]
        self.assertNotIn("closeProviderSessionStream()", focus_slice)
    def test_background_subscriptions_exclude_workers_unbound_and_past_rooms(self) -> None:
        eligibility = APP[
            APP.index("function providerSessionRoomIsEligible") : APP.index(
                "function providerSessionRoomCacheFor"
            )
        ]
        self.assertIn('sessionKind !== "WORKER"', eligibility)
        self.assertIn("function providerSessionObservedProjectId(room)", APP)
        self.assertIn('["BOUND", "ANCHOR_OBSERVED"].includes(bindingState)', eligibility)
        self.assertIn('currentness === "CURRENT"', eligibility)
        self.assertNotIn("providerSessionRoomIsSelected(chatKey)", eligibility)

        subscriptions = APP[
            APP.index("function syncProviderSessionSubscriptions") : APP.index(
                "function reconcileProviderSessionStreams"
            )
        ]
        self.assertIn(
            "eligible.has(key) || providerSessionRoomIsSelected(key)",
            subscriptions,
        )
        self.assertIn("closeProviderSessionStream(key)", subscriptions)
        self.assertIn("delete state.providerSessionRoomCaches[key]", subscriptions)
        self.assertIn("delete state.providerSessionStreamStates[key]", subscriptions)
    def test_provider_session_cancel_uses_direct_endpoint_without_room_queue(self) -> None:
        self.assertIn("async function cancelProviderSessionTurn()", APP)
        cancel_slice = APP[
            APP.index("async function cancelProviderSessionTurn()") : APP.index(
                "function renderComposerActions()"
            )
        ]
        self.assertIn("/v1/provider-sessions/${encodeURIComponent(target.chat_key)}/cancel", cancel_slice)
        self.assertNotIn("/v1/conductor-room/messages", cancel_slice)
        self.assertNotIn("/v1/projects/", cancel_slice)
        self.assertIn("Cancel reply", APP)

    def test_mobile_conversation_header_uses_two_row_layout(self) -> None:
        self.assertIn("grid-template-areas:", CSS)
        self.assertIn('"toggle title actions"', CSS)
        self.assertIn('"toggle opacity opacity"', CSS)
        self.assertIn(".conversation-layer-header .chat-dock-toggle", CSS)
        self.assertIn(".conversation-layer-header .layer-opacity", CSS)


if __name__ == "__main__":
    unittest.main()
