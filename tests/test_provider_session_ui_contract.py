from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "universe_ui" / "styles.css").read_text(encoding="utf-8")


class ProviderSessionUiContractTests(unittest.TestCase):
    def test_selected_session_uses_native_provider_endpoint_not_room_queue(self) -> None:
        self.assertIn('kind: "PROVIDER_SESSION"', APP)
        self.assertIn("async function openProviderChatSession(room)", APP)
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

    def test_provider_session_stream_is_incremental_and_opaque(self) -> None:
        self.assertIn("function openProviderSessionStream(chatKey)", APP)
        self.assertIn("PROVIDER_SESSION_DELTA", APP)
        self.assertIn("PROVIDER_SESSION_PERMISSION", APP)
        self.assertIn("closeProviderSessionStream()", APP)
        stream_slice = APP[
            APP.index("function openProviderSessionStream") : APP.index(
                "function closeProjectRoomStream"
            )
        ]
        self.assertNotIn("provider_session_ref", stream_slice)
        self.assertNotIn("source_path", stream_slice)

    def test_mobile_conversation_header_uses_two_row_layout(self) -> None:
        self.assertIn("grid-template-areas:", CSS)
        self.assertIn('"toggle title actions"', CSS)
        self.assertIn('"toggle opacity opacity"', CSS)
        self.assertIn(".conversation-layer-header .chat-dock-toggle", CSS)
        self.assertIn(".conversation-layer-header .layer-opacity", CSS)


if __name__ == "__main__":
    unittest.main()
