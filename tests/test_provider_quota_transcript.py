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
                home_by_provider={"CODEX": codex_home, "CLAUDE": Path(tmp) / "nope"}
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
