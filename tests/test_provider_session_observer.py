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

    def register(
        self, provider: str, path: Path, *, start_at_end: bool = False
    ) -> dict[str, object]:
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
                "start_at_end": start_at_end,
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

    def test_start_at_end_skips_history_and_tails_only_new_events(self) -> None:
        source_path = self.root / "rollout-live-tail.jsonl"
        self.write(
            source_path,
            {"type": "turn_started", "message": "historical private prompt"},
        )
        source = self.register("CODEX", source_path, start_at_end=True)

        first = self.store.scan(str(source["source_id"]))
        self.assertEqual(0, first["added"])
        self.assertEqual([], self.store.list_activities(str(source["source_id"])))

        with source_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"type": "turn_completed", "text": "new private result"})
                + "\n"
            )
        second = self.store.scan(str(source["source_id"]))
        activities = self.store.list_activities(str(source["source_id"]))
        self.assertEqual(1, second["added"])
        self.assertEqual("TURN_COMPLETED", activities[0]["event_kind"])
        self.assertNotIn("private", json.dumps(activities))

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

    def test_all_provider_scans_redact_payloads_and_are_idempotent(self) -> None:
        fixtures = {
            "CODEX": ("rollout-redaction.jsonl", {"type": "turn_completed", "text": "private-codex"}),
            "CLAUDE": ("claude-redaction.jsonl", {"type": "message", "uuid": "claude-redaction", "text": "private-claude"}),
            "GROK": ("updates.jsonl", {"type": "rate_limit_event", "message": "private-grok"}),
        }
        for provider, (filename, event) in fixtures.items():
            with self.subTest(provider=provider):
                source_path = self.root / provider.lower() / filename
                source_path.parent.mkdir(parents=True, exist_ok=True)
                self.write(source_path, event)
                source = self.register(provider, source_path)
                first = self.store.scan(str(source["source_id"]))
                second = self.store.scan(str(source["source_id"]))
                rendered = json.dumps(
                    {
                        "source": first,
                        "activities": self.store.list_activities(str(source["source_id"])),
                    }
                )
                self.assertEqual(1, first["added"])
                self.assertEqual(0, second["added"])
                self.assertNotIn("private-", rendered)

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

    def test_discovery_reads_only_known_provider_paths(self) -> None:
        codex_home = self.root / "codex"
        rollout = codex_home / "archived_sessions" / "rollout-20260808.jsonl"
        rollout.parent.mkdir(parents=True)
        self.write(rollout, {"type": "turn_started", "message": "private"})
        discovered = self.store.discover_sources("CODEX", home=codex_home)
        self.assertEqual(1, len(discovered))
        self.assertIsNone(discovered[0]["provider_session_id"])
        self.assertEqual("UNKNOWN", discovered[0]["identity_state"])
        self.assertNotIn("private", json.dumps(discovered))

    def test_discovery_extracts_only_safe_provider_chat_metadata(self) -> None:
        codex_home = self.root / "codex"
        codex_path = codex_home / "sessions" / "2026" / "rollout-safe.jsonl"
        codex_path.parent.mkdir(parents=True)
        self.write(
            codex_path,
            {
                "type": "session_meta",
                "payload": {
                    "id": "codex-chat-001",
                    "cwd": r"C:\workspace\universe",
                    "source": {
                        "subagent": {
                            "thread_spawn": {"parent_thread_id": "codex-parent-001"}
                        }
                    },
                    "private_prompt": "must not escape",
                },
            },
        )
        codex = self.store.discover_sources("CODEX", home=codex_home)[0]
        self.assertEqual("codex-chat-001", codex["provider_session_id"])
        self.assertEqual(r"C:\workspace\universe", codex["workspace"])
        self.assertEqual("WORKER", codex["session_kind"])
        self.assertEqual("codex-parent-001", codex["parent_provider_session_id"])
        self.assertNotIn("private_prompt", json.dumps(codex))
        self.assertNotIn("must not escape", json.dumps(codex))

        claude_home = self.root / "claude"
        claude_path = claude_home / "projects" / "C--workspace-GCS" / "chat.jsonl"
        claude_path.parent.mkdir(parents=True)
        self.write(
            claude_path,
            {
                "type": "queue-operation",
                "sessionId": "claude-chat-001",
                "cwd": r"C:\workspace\GCS",
                "slug": "review-current-anchor",
                "isSidechain": False,
                "message": "private claude text",
            },
        )
        claude = self.store.discover_sources("CLAUDE", home=claude_home)[0]
        self.assertEqual("claude-chat-001", claude["provider_session_id"])
        self.assertEqual("review-current-anchor", claude["display_name"])
        self.assertEqual("CHAT", claude["session_kind"])
        self.assertNotIn("private claude text", json.dumps(claude))

        grok_home = self.root / "grok"
        grok_path = (
            grok_home
            / "sessions"
            / "C%3A%5Cworkspace%5Cuniverse-rendezvous"
            / "grok-chat-folder"
            / "updates.jsonl"
        )
        grok_path.parent.mkdir(parents=True)
        self.write(
            grok_path,
            {
                "method": "session/update",
                "params": {
                    "sessionId": "grok-chat-001",
                    "private": "must stay provider-owned",
                },
            },
        )
        grok = self.store.discover_sources("GROK", home=grok_home)[0]
        self.assertEqual("grok-chat-001", grok["provider_session_id"])
        self.assertEqual(r"C:\workspace\universe-rendezvous", grok["workspace"])
        self.assertNotIn("must stay provider-owned", json.dumps(grok))

    def test_public_adapter_contract_is_bounded_and_redacted(self) -> None:
        path = self.root / "rollout-public.jsonl"
        self.write(
            path,
            *(
                {"type": "turn_started", "id": f"event-{index}"}
                for index in range(5)
            ),
        )
        source = self.store.register_source(
            {
                "provider": "CODEX",
                "provider_session_id": "codex-public",
                "source_path": str(path),
                "source_kind": "CODEX_ROLLOUT_JSONL",
                "source_version": "v1",
            }
        )
        result = self.store.read_bounded(str(source["source_id"]), max_events=2)
        self.assertEqual(2, result["bounded_read"]["events"])
        rendered = json.dumps(result)
        self.assertNotIn(str(path), rendered)
        self.assertNotIn("codex-public", rendered)
        self.assertNotIn("source_path", rendered)
        self.assertEqual(
            2,
            self.store.open_cursor(str(source["source_id"]))["cursor"]["ordinal"],
        )


if __name__ == "__main__":
    unittest.main()
