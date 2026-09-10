"""Contract checks for protocol selection outside the terminal surface."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SessionBusProtocolUiTests(unittest.TestCase):
    def test_compose_selects_protocol_and_ui_only_notification(self):
        html = (ROOT / "tools/universe_ui/index.html").read_text(encoding="utf-8")
        app = (ROOT / "tools/universe_ui/app.js").read_text(encoding="utf-8")
        self.assertIn('id="session-bus-protocol"', html)
        for protocol in ("WORK", "CONVERSATION", "NOTICE"):
            self.assertIn(f'value="{protocol}"', html)
        compose = app.split("async function sendSessionBusCompose(event)", 1)[1].split("\n}", 1)[0]
        self.assertIn('protocol: elements.sessionBusProtocol?.value || "WORK"', compose)
        self.assertIn('notify: "UI"', compose)
        self.assertNotIn('notify: "HEADER"', compose)
        self.assertIn('message.handling?.action', app)


if __name__ == "__main__":
    unittest.main()
