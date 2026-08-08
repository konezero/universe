from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from provider_session_observer import (  # noqa: E402
    ProviderSessionObserverError,
    ProviderSessionObserverStore,
)


class ProviderSessionObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ProviderSessionObserverStore(self.root / "universe.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def register(self, provider: str, path: Path) -> dict[str, object]:
        return self.store.register_source(
            {
                "provider": provider,
                "provider_session_id": f"{provider.lower()}-session-1",
                "source_path": str(path),
                "source_kind": {
                    "CODEX": "CODEX_ROLLOUT_JSONL",
                    "CLAUDE": "CLAUDE_SESSION_JSONL",
                    "GROK": "GROK_UPDATES_JSONL",
                }[provider],
                "source_version": "v1",
            }
        )

    @staticmethod
    def write(path: Path, *events: dict[str, object]) -> None:
        path.write_text(
            "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
        )

    def test_codex_incremental_cursor_never_persists_raw_message_text(self) -> None:
        source_path = self.root / "rollout-20260808.jsonl"
        self.write(
            source_path,
            {"type": "turn_started", "message": "private prompt"},
            {"type": "tool_call", "command": "private command"},
        )
        source = self.register("CODEX", source_path)
        first = self.store.scan(str(source["source_id"]))
        self.assertEqual(2, first["added"])
        activities = self.store.list_activities(str(source["source_id"]))
        rendered = json.dumps({"source": first, "activities": activities})
        self.assertNotIn("private prompt", rendered)
        self.assertNotIn("private command", rendered)
        second = self.store.scan(str(source["source_id"]))
        self.assertEqual(0, second["added"])
        with source_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "turn_completed", "text": "private"}) + "\n")
        third = self.store.scan(str(source["source_id"]))
        self.assertEqual(1, third["added"])
        self.assertEqual(3, len(self.store.list_activities(str(source["source_id"]))))

    def test_append_keeps_file_identity_and_cursor_progresses(self) -> None:
        source_path = self.root / "rollout-append.jsonl"
        self.write(source_path, {"type": "turn_started"})
        source = self.register("CODEX", source_path)
        first = self.store.scan(str(source["source_id"]))
        first_cursor = first["source"]["cursor"]["offset"]
        with source_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "turn_completed"}) + "\n")
        second = self.store.scan(str(source["source_id"]))
        self.assertEqual("ACTIVE", second["source"]["status"])
        self.assertGreater(second["source"]["cursor"]["offset"], first_cursor)
        self.assertEqual(1, second["added"])

    def test_rotation_and_schema_errors_fail_closed(self) -> None:
        source_path = self.root / "rollout-rotate.jsonl"
        self.write(source_path, {"type": "turn_started"})
        source = self.register("CODEX", source_path)
        self.store.scan(str(source["source_id"]))
        replacement = self.root / "replacement.jsonl"
        self.write(replacement, {"type": "turn_completed"})
        source_path.unlink()
        replacement.rename(source_path)
        rotated = self.store.scan(str(source["source_id"]))
        self.assertEqual("UNKNOWN", rotated["source"]["status"])
        self.assertEqual("SOURCE_ROTATED", rotated["source"]["reason"])

        broken_path = self.root / "rollout-broken.jsonl"
        broken_path.write_text("not-json\n", encoding="utf-8")
        broken = self.register("CODEX", broken_path)
        result = self.store.scan(str(broken["source_id"]))
        self.assertEqual("UNKNOWN", result["source"]["status"])
        self.assertEqual("SOURCE_SCHEMA_UNSUPPORTED", result["source"]["reason"])

    def test_claude_reduces_parent_branch_to_active_leaf(self) -> None:
        source_path = self.root / "claude-session.jsonl"
        self.write(
            source_path,
            {"type": "message", "uuid": "root"},
            {"type": "tool_call", "uuid": "child", "parentUuid": "root"},
        )
        source = self.register("CLAUDE", source_path)
        self.store.scan(str(source["source_id"]))
        active = self.store.list_activities(str(source["source_id"]))
        self.assertEqual(1, len(active))
        self.assertEqual("child", active[0]["provider_event_id"])
        self.assertEqual("TOOL_PHASE", active[0]["event_kind"])

    def test_grok_accepts_updates_only(self) -> None:
        with self.assertRaisesRegex(ProviderSessionObserverError, "updates.jsonl only"):
            self.register("GROK", self.root / "chat_history.jsonl")
        source_path = self.root / "updates.jsonl"
        self.write(source_path, {"type": "rate_limit_event", "message": "private"})
        source = self.register("GROK", source_path)
        result = self.store.scan(str(source["source_id"]))
        self.assertEqual(1, result["added"])
        activity = self.store.list_activities(str(source["source_id"]))[0]
        self.assertEqual("QUOTA_STOP", activity["event_kind"])

    def test_batch_candidate_is_redacted_and_does_not_publish_memory_or_bench(self) -> None:
        source_path = self.root / "rollout-batch.jsonl"
        self.write(source_path, {"type": "turn_completed", "text": "private"})
        source = self.register("CODEX", source_path)
        self.store.scan(str(source["source_id"]))
        candidate = self.store.build_batch_candidate(str(source["source_id"]))
        self.assertEqual("REVIEW_REQUIRED", candidate["status"])
        self.assertEqual("NOT_REQUESTED", candidate["memory"]["publication"])
        self.assertEqual("NOT_RECORDED", candidate["bench"]["state"])
        self.assertEqual("NOT_PROJECTED", candidate["future"]["state"])
        self.assertNotIn("private", json.dumps(candidate))


if __name__ == "__main__":
    unittest.main()
