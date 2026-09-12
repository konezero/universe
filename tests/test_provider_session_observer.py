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

    def test_complete_activity_windows_cover_large_transcript_without_coalescing_loss(self):
        from types import SimpleNamespace
        from universe_app.memory_source_window import select_source_window, apply_activity_window
        from memory_fast_extract import normalize_transient_semantic_evidence
        path = self.root / "rollout-large.jsonl"
        events = [{"type":"response_item", "id":str(i), "payload":{"type":"message", "role":"assistant",
                   "content":[{"type":"output_text", "text":str(i) + " " + "x" * 200}]}} for i in range(600)]
        self.write(path, *events)
        source = self.register("CODEX", path)
        sid = source["source_id"]
        self.store.scan(sid)
        self.store.scan(sid)
        position = {}
        facade = SimpleNamespace(list_provider_session_sources=self.store.list_sources,
            get_memory_source_position=lambda project:position,
            prepare_provider_activity_batch=self.store.build_batch_candidate,
            provider_session_observer=self.store)
        ordinals = []
        for _ in range(20):
            selected, report = select_source_window(facade, "p")
            window = report["activity_windows"][sid]
            batch = apply_activity_window(self.store.build_batch_candidate(sid), window)
            evidence = normalize_transient_semantic_evidence(self.store.build_transient_semantic_evidence(sid, batch["activity_refs"], require_complete=True))
            self.assertLessEqual(sum(len(e["text"]) for e in evidence), 32000)
            self.assertLessEqual(len(evidence), 256)
            self.assertEqual(len(batch["activity_refs"]), len(evidence))
            ordinals.extend(e["ordinal"] for e in evidence)
            position = {"next_source_id":report["next_source_id"], "selection":report}
            if not report["activity_resume"]:break
        else:self.fail("source pagination did not finish")
        self.assertEqual(list(range(1,601)), ordinals)

    def test_complete_evidence_splits_long_message_and_rejects_oversized_event(self):
        path = self.root / "rollout-long-message.jsonl"
        def event(text):return {"type":"response_item", "id":"long", "payload":{"type":"message","role":"user","content":[{"type":"input_text","text":text}]}}
        self.write(path,event("x" * 5000))
        source=self.register("CODEX",path);sid=source["source_id"];self.store.scan(sid)
        batch=self.store.build_batch_candidate(sid)
        evidence=self.store.build_transient_semantic_evidence(sid,batch["activity_refs"],require_complete=True)
        self.assertEqual(5000,sum(len(e["text"]) for e in evidence))
        self.assertEqual([2000,2000,1000],[len(e["text"]) for e in evidence])
        self.write(path,event("x" * 33000))
        self.store.scan(sid)
        with self.assertRaises(ProviderSessionObserverError) as caught:
            self.store.build_transient_semantic_evidence(sid,self.store.build_batch_candidate(sid)["activity_refs"],require_complete=True)
        self.assertEqual("SEMANTIC_EVIDENCE_WINDOW_LIMIT",caught.exception.code)

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

    def test_codex_semantic_evidence_is_transient_redacted_and_activity_bound(self) -> None:
        source_path = self.root / "rollout-semantic.jsonl"
        self.write(
            source_path,
            {
                "type": "response_item",
                "id": "message-1",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "한글 메모 😀 — Keep memory candidates review-only. "
                                "api_key=verysecretvalue"
                            ),
                        }
                    ],
                },
            },
        )
        source = self.register("CODEX", source_path)
        self.store.scan(str(source["source_id"]))
        activity = self.store.list_activities(str(source["source_id"]))[0]
        evidence = self.store.build_transient_semantic_evidence(
            str(source["source_id"]),
            [activity],
        )

        from memory_fast_extract import normalize_transient_semantic_evidence, FastExtractError
        self.assertEqual(evidence, normalize_transient_semantic_evidence(evidence))
        with self.assertRaises(FastExtractError):
            normalize_transient_semantic_evidence([{**evidence[0], "text": evidence[0]["text"] + "변조"}])
        self.assertEqual(1, len(evidence))
        self.assertIn("review-only", evidence[0]["text"])
        self.assertIn("[REDACTED]", evidence[0]["text"])
        self.assertNotIn("verysecretvalue", json.dumps(evidence))
        self.assertNotIn(
            "review-only",
            json.dumps(self.store.list_activities(str(source["source_id"]))),
        )

        forged = {**activity, "activity_digest": "a" * 64}
        with self.assertRaises(ProviderSessionObserverError) as captured:
            self.store.build_transient_semantic_evidence(
                str(source["source_id"]), [forged]
            )
        self.assertEqual("SEMANTIC_ACTIVITY_NOT_ATTESTED", captured.exception.code)

    def test_live_deltas_are_incremental_transient_and_redacted(self) -> None:
        source_path = self.root / "rollout-live-deltas.jsonl"
        self.write(
            source_path,
            {
                "type": "response_item",
                "id": "history",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "historical"}],
                },
            },
        )
        source = self.register("CODEX", source_path, start_at_end=True)
        self.store.scan(str(source["source_id"]))
        with source_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "id": "live",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "live result api_key=secretvalue",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
        scan = self.store.scan(str(source["source_id"]))
        delta = self.store.build_transient_live_deltas(
            str(source["source_id"]), added_count=scan["added"]
        )

        self.assertEqual("TRANSIENT_REDACTED", delta["delivery"])
        self.assertEqual(1, len(delta["deltas"]))
        self.assertIn("live result", delta["deltas"][0]["text"])
        self.assertIn("[REDACTED]", delta["deltas"][0]["text"])
        self.assertNotIn("secretvalue", json.dumps(delta))
        persisted = json.dumps(self.store.list_activities(str(source["source_id"])))
        self.assertNotIn("live result", persisted)
        self.assertNotIn("secretvalue", persisted)

    def test_codex_live_deltas_collapse_adjacent_telemetry_twins(self) -> None:
        source_path = self.root / "rollout-telemetry-twins.jsonl"
        self.write(
            source_path,
            {
                "type": "response_item",
                "id": "user-response",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "same question"}],
                },
            },
            {
                "type": "event_msg",
                "id": "user-event",
                "payload": {"type": "user_message", "message": "same question"},
            },
            {
                "type": "event_msg",
                "id": "assistant-event",
                "payload": {"type": "agent_message", "message": "same answer"},
            },
            {
                "type": "response_item",
                "id": "assistant-response",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same answer"}],
                },
            },
        )
        source = self.register("CODEX", source_path)
        scan = self.store.scan(str(source["source_id"]))
        delta = self.store.build_transient_live_deltas(
            str(source["source_id"]), added_count=scan["added"]
        )

        self.assertEqual("TRANSIENT_REDACTED", delta["delivery"])
        self.assertEqual(["USER", "ASSISTANT"], [item["role"] for item in delta["deltas"]])
        self.assertEqual(
            ["same question", "same answer"],
            [item["text"] for item in delta["deltas"]],
        )

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

    def test_complete_event_larger_than_scan_budget_advances_once(self) -> None:
        source_path = self.root / "rollout-oversized-budget.jsonl"
        self.write(
            source_path,
            {"type": "turn_completed", "text": "x" * 1024},
            {"type": "turn_started"},
        )
        source = self.register("CODEX", source_path)

        first = self.store.scan(
            str(source["source_id"]), max_bytes=64, max_seconds=1e-9
        )
        self.assertEqual(1, first["added"])
        self.assertEqual(1, first["bounded_read"]["events"])
        self.assertEqual(1, first["bounded_read"]["oversized_events"])
        self.assertGreater(first["bounded_read"]["bytes"], 64)

        second = self.store.scan(str(source["source_id"]), max_bytes=64)
        self.assertEqual(1, second["added"])
        self.assertEqual(0, second["bounded_read"]["oversized_events"])
        self.assertEqual(source_path.stat().st_size, second["source"]["cursor"]["offset"])

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
        history = self.store.list_activities(
            str(source["source_id"]), active_only=False
        )
        self.assertEqual(["child", "root"], [item["provider_event_id"] for item in history])
        limited = self.store.list_activities(
            str(source["source_id"]), active_only=False, limit=1
        )
        self.assertEqual(["child"], [item["provider_event_id"] for item in limited])

    def test_claude_skips_uuid_less_session_metadata(self) -> None:
        source_path = self.root / "claude-session-with-metadata.jsonl"
        self.write(
            source_path,
            {
                "type": "assistant",
                "uuid": "assistant-result",
                "message": {"role": "assistant", "content": "done"},
            },
            {"type": "file-history-snapshot", "snapshot": {}},
            {"type": "file-history-delta", "backup": {}},
            {"type": "queue-operation", "operation": "dequeue"},
            {"type": "ai-title", "aiTitle": "redacted"},
            {"type": "last-prompt", "lastPrompt": "redacted"},
            {"type": "mode", "mode": "default"},
            {"type": "permission-mode", "permissionMode": "default"},
            {"type": "atis-latch", "atis": {}},
            {"type": "bridge-session", "bridgeSessionId": "bridge-1"},
        )
        source = self.register("CLAUDE", source_path)

        result = self.store.scan(str(source["source_id"]))
        activities = self.store.list_activities(str(source["source_id"]))

        self.assertEqual("ACTIVE", result["source"]["status"])
        self.assertIsNone(result["source"]["reason"])
        self.assertEqual(1, result["added"])
        self.assertEqual("TURN_COMPLETED", activities[0]["event_kind"])

    def test_claude_unknown_uuid_less_event_still_fails_closed(self) -> None:
        source_path = self.root / "claude-unknown-event.jsonl"
        self.write(source_path, {"type": "unexpected-new-event"})
        source = self.register("CLAUDE", source_path)

        result = self.store.scan(str(source["source_id"]))

        self.assertEqual("UNKNOWN", result["source"]["status"])
        self.assertEqual("SOURCE_SCHEMA_UNSUPPORTED", result["source"]["reason"])

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

    def test_grok_method_events_scan_and_expose_visible_chunks(self) -> None:
        source_path = self.root / "updates.jsonl"
        self.write(
            source_path,
            {
                "method": "session/update",
                "params": {
                    "sessionId": "grok-live",
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"text": "hello "},
                    },
                },
            },
            {
                "method": "session/update",
                "params": {
                    "sessionId": "grok-live",
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"text": "world"},
                    },
                },
            },
            {
                "method": "session/update",
                "params": {
                    "sessionId": "grok-live",
                    "update": {
                        "sessionUpdate": "agent_thought_chunk",
                        "content": {"text": "secret thought"},
                    },
                },
            },
            {
                "method": "session/update",
                "params": {
                    "sessionId": "grok-live",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"text": "visible answer"},
                    },
                },
            },
        )
        source = self.register("GROK", source_path)
        scan = self.store.scan(str(source["source_id"]))
        self.assertEqual("ACTIVE", scan["source"]["status"])
        self.assertEqual(4, scan["added"])
        delta = self.store.build_transient_live_deltas(
            str(source["source_id"]), added_count=scan["added"]
        )
        self.assertEqual("TRANSIENT_REDACTED", delta["delivery"])
        self.assertEqual(["USER", "ASSISTANT"], [item["role"] for item in delta["deltas"]])
        self.assertEqual("hello world", delta["deltas"][0]["text"])
        self.assertEqual("visible answer", delta["deltas"][1]["text"])
        persisted = json.dumps(self.store.list_activities(str(source["source_id"])))
        self.assertNotIn("hello world", persisted)
        self.assertNotIn("secret thought", persisted)

    def test_claude_rag_uses_latest_branch_ancestry_and_rejects_abandoned_refs(self):
        path=self.root/"branch-rag.jsonl"
        events=[{"type":"user","uuid":"root","message":{"content":"root request"}},
                {"type":"assistant","uuid":"old","parentUuid":"root","message":{"content":"abandoned answer"}}]
        self.write(path,*events)
        source=self.register("CLAUDE",path);sid=str(source["source_id"])
        self.store.scan(sid)
        old=self.store.build_batch_candidate(sid)["activity_refs"]
        self.write(path,*events,{"type":"assistant","uuid":"new","parentUuid":"root","message":{"content":"current answer"}})
        self.store.scan(sid)
        refs=self.store.build_batch_candidate(sid)["activity_refs"]
        self.assertEqual([3,1],[ref["ordinal"] for ref in refs])
        evidence=self.store.build_transient_semantic_evidence(sid,refs,require_complete=True)
        self.assertEqual(["root request","current answer"],[item["text"] for item in evidence])
        with self.assertRaises(ProviderSessionObserverError) as caught:
            self.store.build_transient_semantic_evidence(sid,old,require_complete=True)
        self.assertEqual("SEMANTIC_ACTIVITY_NOT_ATTESTED",caught.exception.code)

    def test_multi_provider_rag_evidence_preserves_origin_and_excludes_tools(self) -> None:
        from memory_fast_extract import redact_activity_batch, normalize_transient_semantic_evidence, FAST_EXTRACT_PROVIDER, FastExtractError
        cases = {
            "CLAUDE": ("rag-claude.jsonl", [
                {"type":"user","uuid":"u","message":{"role":"user","content":[
                    {"type":"tool_result","content":"private tool output"},
                    {"type":"text","text":"visible request"}]}},
                {"type":"assistant","uuid":"a","parentUuid":"u","message":{"role":"assistant","content":[
                    {"type":"thinking","thinking":"hidden thought"},
                    {"type":"tool_use","input":{"text":"private tool input"}},
                    {"type":"text","text":"visible answer"}]}}
            ]),
            "GROK": ("updates.jsonl", [
                {"method":"session/update","params":{"update":{"sessionUpdate":kind,"content":{"type":"text","text":text}}}}
                for kind,text in [("user_message_chunk","visible request"),("agent_thought_chunk","hidden thought"),("agent_message_chunk","visible answer")]
            ])
        }
        for provider,(name,events) in cases.items():
            with self.subTest(provider=provider):
                path=self.root/name
                self.write(path,*events)
                source=self.register(provider,path)
                sid=str(source["source_id"])
                self.store.scan(sid)
                batch=redact_activity_batch(self.store.build_batch_candidate(sid))
                self.assertEqual(provider,batch["source"]["provider"])
                evidence=normalize_transient_semantic_evidence(self.store.build_transient_semantic_evidence(sid,batch["activity_refs"],require_complete=True))
                self.assertEqual(["visible request","visible answer"],[item["text"] for item in evidence])
                self.assertNotIn("visible answer",json.dumps(batch))
                batch["source"]["provider"]="UNSUPPORTED"
                with self.assertRaises(FastExtractError):redact_activity_batch(batch)
        self.assertEqual("CODEX",FAST_EXTRACT_PROVIDER)

    def test_visible_transcript_reads_recent_claude_and_grok_text(self) -> None:
        claude_path = self.root / "claude-chat.jsonl"
        self.write(
            claude_path,
            {
                "type": "user",
                "uuid": "claude-user",
                "message": {"role": "user", "content": [{"type": "text", "text": "ask claude"}]},
            },
            {
                "type": "assistant",
                "uuid": "claude-assistant",
                "parentUuid": "claude-user",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "claude reply"}],
                },
            },
        )
        claude = self.register("CLAUDE", claude_path)
        self.store.scan(str(claude["source_id"]))
        claude_excerpts = self.store.build_transient_visible_transcript(
            str(claude["source_id"])
        )
        self.assertEqual(["USER", "ASSISTANT"], [item["role"] for item in claude_excerpts])
        self.assertEqual("ask claude", claude_excerpts[0]["text"])
        self.assertEqual("claude reply", claude_excerpts[1]["text"])

        grok_path = self.root / "updates.jsonl"
        self.write(
            grok_path,
            {
                "method": "session/update",
                "params": {
                    "sessionId": "grok-resume",
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"text": "resume me"},
                    },
                },
            },
        )
        grok = self.register("GROK", grok_path, start_at_end=True)
        excerpts = self.store.build_transient_visible_transcript(str(grok["source_id"]))
        self.assertEqual(["USER"], [item["role"] for item in excerpts])
        self.assertEqual("resume me", excerpts[0]["text"])


if __name__ == "__main__":
    unittest.main()
