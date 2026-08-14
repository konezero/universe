from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "universe_ui" / "styles.css").read_text(encoding="utf-8")


class ProviderSessionUiContractTests(unittest.TestCase):
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
        self.assertIn("[coordinate.key]: anchorSessionKey(session)", selection_slice)
        self.assertIn("await openProviderChatSession(room, { session });", selection_slice)
        open_slice = APP[
            APP.index("async function openProviderChatSession") : APP.index(
                "function closeProjectRoomStream"
            )
        ]
        self.assertIn("session_anchor_ref:", open_slice)
        self.assertIn("vendor_session_id:", open_slice)
        self.assertIn("chat_key: chatKey", open_slice)
        self.assertIn("function providerSessionRoomIsOpenable(room)", APP)

    def test_provider_session_stream_is_incremental_and_opaque(self) -> None:
        self.assertIn("function openProviderSessionStream(chatKey)", APP)
        self.assertIn("PROVIDER_SESSION_DELTA", APP)
        self.assertIn("PROVIDER_SESSION_PERMISSION", APP)
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
        self.assertIn('"BOUND", "ANCHOR_OBSERVED"', eligibility)
        self.assertIn('currentness === "CURRENT"', eligibility)
        self.assertIn("providerSessionRoomIsEligible(room)", APP)
        self.assertIn("delete state.providerSessionRoomCaches[key]", APP)
        self.assertIn("closeProviderSessionStream(chatKey)", APP)
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
