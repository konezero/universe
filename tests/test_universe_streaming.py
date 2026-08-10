from __future__ import annotations

import sys
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from universe_app.streaming import (  # noqa: E402
    ConductorRoomEventHub,
    ProjectRoomEventHub,
)
from universe_server import (  # noqa: E402
    ConductorRoomEventHub as LegacyConductorRoomEventHub,
    ProjectRoomEventHub as LegacyProjectRoomEventHub,
)


class UniverseStreamingContractTests(unittest.TestCase):
    def test_legacy_entrypoint_reexports_event_hubs(self) -> None:
        self.assertIs(LegacyProjectRoomEventHub, ProjectRoomEventHub)
        self.assertIs(LegacyConductorRoomEventHub, ConductorRoomEventHub)

    def test_project_events_are_isolated_and_cursor_ordered(self) -> None:
        hub = ProjectRoomEventHub(retained_events=32)
        first = hub.publish("GCS", {"delta": "one"})
        second = hub.publish("Universe", {"delta": "two"})
        third = hub.publish("GCS", {"delta": "three"})
        self.assertEqual(1, first["event_id"])
        self.assertEqual(2, second["event_id"])
        self.assertEqual(3, third["event_id"])
        self.assertEqual([third], hub.wait("GCS", after_event_id=1, timeout_seconds=0.1))
        self.assertEqual([], hub.wait("Universe", after_event_id=2, timeout_seconds=0.1))

    def test_conductor_events_are_incremental(self) -> None:
        hub = ConductorRoomEventHub()
        first = hub.publish({"delta": "one"})
        second = hub.publish({"delta": "two"})
        self.assertEqual([second], hub.wait(after_event_id=first["event_id"], timeout_seconds=0.1))


if __name__ == "__main__":
    unittest.main()
