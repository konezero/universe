import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from universe_app.provider_quota_transcript import (  # noqa: E402
    claude_quota_from_transcript,
    codex_quota_from_transcript,
    grok_quota_from_billing_log,
    sweep_transcript_quota,
)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


class CodexTranscriptQuotaTests(unittest.TestCase):
    def test_reads_the_newest_rate_limits_event(self) -> None:
        with TemporaryDirectory() as tmp:
            roll = Path(tmp) / "rollout-x.jsonl"
            _write(
                roll,
                [
                    {"type": "event_msg", "payload": {"type": "agent_message"}},
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {
                                    "used_percent": 40.0,
                                    "window_minutes": 300,
                                    "resets_at": 1788000000,
                                },
                                "secondary": None,
                                "rate_limit_reached_type": None,
                            },
                        },
                    },
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {
                                    "used_percent": 92.5,
                                    "window_minutes": 10080,
                                    "resets_at": 1788747896,
                                },
                                "secondary": None,
                                "rate_limit_reached_type": None,
                            },
                        },
                    },
                ],
            )
            snapshot = codex_quota_from_transcript(roll)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot["provider"], "CODEX")
            # The newest event wins: 92.5% weekly, not the earlier 40%.
            self.assertEqual(snapshot["windows"][0]["used_percent"], 92.5)
            self.assertEqual(snapshot["windows"][0]["resets_at"], 1788747896)
            self.assertEqual(snapshot["source"], "codex-rollout-transcript")

    def test_state_thresholds(self) -> None:
        with TemporaryDirectory() as tmp:
            roll = Path(tmp) / "rollout-x.jsonl"
            _write(
                roll,
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {"used_percent": 92.5, "window_minutes": 10080},
                                "rate_limit_reached_type": None,
                            },
                        },
                    }
                ],
            )
            snapshot = codex_quota_from_transcript(roll)
            assert snapshot is not None
            self.assertEqual(snapshot["state"], "WARNING")
            self.assertEqual(snapshot["windows"][0]["used_percent"], 92.5)
            self.assertEqual(snapshot["windows"][0]["name"], "PRIMARY")

    def test_reached_type_forces_exhausted(self) -> None:
        with TemporaryDirectory() as tmp:
            roll = Path(tmp) / "rollout-x.jsonl"
            _write(
                roll,
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {"used_percent": 12.0, "window_minutes": 300},
                                "rate_limit_reached_type": "primary",
                            },
                        },
                    }
                ],
            )
            snapshot = codex_quota_from_transcript(roll)
            assert snapshot is not None
            self.assertEqual(snapshot["state"], "EXHAUSTED")
            self.assertEqual(snapshot["rate_limit_reached_type"], "primary")

    def test_a_stale_transcript_is_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            roll = Path(tmp) / "rollout-x.jsonl"
            _write(
                roll,
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {"used_percent": 50.0, "window_minutes": 300}
                            },
                        },
                    }
                ],
            )
            old = time.time() - 7200
            os.utime(roll, (old, old))
            self.assertIsNone(
                codex_quota_from_transcript(roll, max_age_seconds=3600)
            )


class ClaudeTranscriptQuotaTests(unittest.TestCase):
    def test_reads_the_approaching_limit_notice(self) -> None:
        with TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            _write(
                transcript,
                [
                    {"type": "assistant", "message": {"role": "assistant"}},
                    {
                        "type": "system",
                        "subtype": "informational",
                        "level": "notice",
                        "content": (
                            "Approaching your 5-hour usage limit — Claude will "
                            "wrap up the current step."
                        ),
                        "timestamp": _recent_iso(),
                    },
                ],
            )
            snapshot = claude_quota_from_transcript(transcript)
            assert snapshot is not None
            self.assertEqual(snapshot["provider"], "CLAUDE")
            self.assertEqual(snapshot["state"], "WARNING")
            self.assertEqual(snapshot["windows"][0]["name"], "5_HOUR")

    def test_an_old_notice_is_ignored(self) -> None:
        with TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            _write(
                transcript,
                [
                    {
                        "type": "system",
                        "content": "Approaching your 5-hour usage limit — ...",
                        "timestamp": "2020-01-01T00:00:00.000Z",
                    }
                ],
            )
            self.assertIsNone(
                claude_quota_from_transcript(transcript, max_age_seconds=3600)
            )

    def test_a_transcript_with_no_notice_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            _write(
                transcript,
                [{"type": "assistant", "message": {"role": "assistant"}}],
            )
            self.assertIsNone(claude_quota_from_transcript(transcript))


class GrokBillingLogQuotaTests(unittest.TestCase):
    def test_reads_credit_usage_and_weekly_period(self) -> None:
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "unified.jsonl"
            _write(
                log,
                [
                    {"msg": "something else"},
                    {
                        "ts": "2026-09-04T09:32:16.330Z",
                        "msg": "billing: fetched credits config",
                        "ctx": {
                            "config": {
                                "creditUsagePercent": 100.0,
                                "currentPeriod": {
                                    "type": "USAGE_PERIOD_TYPE_WEEKLY",
                                    "end": "2026-09-08T14:47:02.288989+00:00",
                                },
                            }
                        },
                    },
                ],
            )
            snapshot = grok_quota_from_billing_log(log)
            assert snapshot is not None
            self.assertEqual(snapshot["provider"], "GROK")
            self.assertEqual(snapshot["state"], "EXHAUSTED")
            self.assertEqual(snapshot["windows"][0]["name"], "WEEKLY")
            self.assertEqual(snapshot["windows"][0]["used_percent"], 100.0)
            self.assertEqual(
                snapshot["windows"][0]["resets_at"],
                "2026-09-08T14:47:02.288989+00:00",
            )

    def test_available_below_threshold(self) -> None:
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "unified.jsonl"
            _write(
                log,
                [
                    {
                        "msg": "billing: fetched credits config",
                        "ctx": {"config": {"creditUsagePercent": 12.0}},
                    }
                ],
            )
            snapshot = grok_quota_from_billing_log(log)
            assert snapshot is not None
            self.assertEqual(snapshot["state"], "AVAILABLE")

    def test_no_billing_line_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "unified.jsonl"
            _write(log, [{"msg": "session: started"}, {"msg": "turn: completed"}])
            self.assertIsNone(grok_quota_from_billing_log(log))


class SweepTests(unittest.TestCase):
    def test_sweep_picks_the_newest_transcript_per_provider(self) -> None:
        with TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            old = codex_home / "sessions" / "2026" / "01" / "01" / "rollout-old.jsonl"
            new = codex_home / "sessions" / "2026" / "09" / "04" / "rollout-new.jsonl"
            _write(
                old,
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {"used_percent": 10.0, "window_minutes": 300}
                            },
                        },
                    }
                ],
            )
            _write(
                new,
                [
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {"used_percent": 77.0, "window_minutes": 300}
                            },
                        },
                    }
                ],
            )
            now = time.time()
            os.utime(old, (now - 5000, now - 5000))
            os.utime(new, (now - 10, now - 10))
            snapshots = sweep_transcript_quota(
                home_by_provider={
                    "CODEX": codex_home,
                    "CLAUDE": Path(tmp) / "nope",
                    "GROK": Path(tmp) / "nope",
                }
            )
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["windows"][0]["used_percent"], 77.0)


def _recent_iso() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


if __name__ == "__main__":
    unittest.main()


class ClaudeApiErrorQuotaTests(unittest.TestCase):
    def parse(self, *, text="You've hit your session limit \u00b7 resets 4:40pm (Asia/Seoul)", flagged=True):
        from unittest.mock import patch
        from datetime import datetime
        now = datetime.fromisoformat("2026-09-15T05:44:06+00:00").timestamp()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "exact-session.jsonl"
            _write(path, [{"type": "assistant", "isApiErrorMessage": flagged,
                          "timestamp": "2026-09-15T05:44:06Z", "message": {"content": [{"type": "text", "text": text}]}}])
            with patch("universe_app.provider_quota_transcript.time.time", return_value=now):
                return claude_quota_from_transcript(path)

    def test_actual_cli_error_and_explicit_reset(self):
        from datetime import datetime
        value = self.parse()
        self.assertEqual(value["state"], "EXHAUSTED")
        self.assertEqual(value["provider_session_ref"], "exact-session")
        self.assertEqual(value["windows"][0]["resets_at"], datetime.fromisoformat("2026-09-15T16:40:00+09:00").timestamp())
        self.assertNotIn("used_percent", value["windows"][0])

    def test_chat_text_is_not_provider_error(self):
        self.assertIsNone(self.parse(flagged=False))

    def test_unknown_zone_and_weekly_clock_do_not_guess(self):
        for text in ["You've hit your session limit - resets 4:40pm (Unknown/Zone)", "You've hit your weekly limit - resets 4:40pm (Asia/Seoul)"]:
            self.assertNotIn("resets_at", self.parse(text=text)["windows"][0])
