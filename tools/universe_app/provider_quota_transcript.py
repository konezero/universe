"""Read account-level quota out of a provider CLI's own transcript file.

The terminal (ConPTY) path gives us no SDK stream, so a live ``claude`` /
``codex`` TUI never hands Universe a ``rate_limit_event``.  But every provider
CLI writes a transcript, and some of them record their rate-limit state there:

* Codex ``~/.codex/sessions/**/rollout-*.jsonl`` -- a ``token_count`` event
  carries ``rate_limits.primary`` / ``.secondary`` with ``used_percent``,
  ``window_minutes`` and an epoch ``resets_at``.  Full data.
* Claude ``~/.claude/projects/**/*.jsonl`` -- system warnings and structured
  assistant API errors with an explicit session reset clock; no percentage.
* Grok ``~/.grok/logs/unified.jsonl`` -- the CLI logs a "billing: fetched
  credits config" line every ~30 s with ``creditUsagePercent`` and the weekly
  ``currentPeriod``. The freshest of the three.

The account display uses the newest transcript. Work recovery must instead
correlate the exact Supervisor provider session to its transcript.  Reads are tail-only (last ~96 KiB) so a 40 MB transcript costs
nothing.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROVIDER_QUOTA_SNAPSHOT_SCHEMA = "universe.provider-quota-snapshot.v1"
_TAIL_BYTES = 96 * 1024
_CLAUDE_LIMIT_NOTICE = re.compile(
    r"(?P<window>\d+\s*-?\s*hour|weekly|5-?hour)\s+usage limit", re.IGNORECASE
)


def _provider_home(provider: str) -> Path:
    provider = provider.upper()
    if provider == "CODEX":
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if provider == "CLAUDE":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return Path(os.environ.get("GROK_HOME") or Path.home() / ".grok")


def _newest_transcript(provider: str, home: Path | None = None) -> Path | None:
    root = home or _provider_home(provider)
    if not root.is_dir():
        return None
    provider = provider.upper()
    if provider == "CODEX":
        globs: Iterable[Path] = (
            *(root / "sessions").glob("**/rollout-*.jsonl"),
            *(root / "archived_sessions").glob("rollout-*.jsonl"),
        )
    elif provider == "CLAUDE":
        globs = (
            path
            for path in (root / "projects").glob("**/*.jsonl")
            if "subagents" not in path.parts
        )
    elif provider == "GROK":
        billing_log = root / "logs" / "unified.jsonl"
        return billing_log if billing_log.is_file() else None
    else:
        return None
    newest: Path | None = None
    newest_mtime = -1.0
    for path in globs:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest, newest_mtime = path, mtime
    return newest


def _tail_lines(path: Path, *, tail_bytes: int = _TAIL_BYTES) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > tail_bytes:
                handle.seek(-tail_bytes, os.SEEK_END)
                handle.readline()  # drop the partial first line
            data = handle.read()
    except OSError:
        return []
    return data.decode("utf-8", "replace").splitlines()


def _stale(path: Path, *, max_age_seconds: float) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) > max_age_seconds
    except OSError:
        return True


def _observed_at(path: Path) -> str | None:
    """The transcript's own last-write time — the true age of the reading."""

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return (
        datetime.fromtimestamp(mtime, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _codex_window(raw: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percent")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    window: dict[str, Any] = {"name": name.upper(), "used_percent": float(used)}
    minutes = raw.get("window_minutes")
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool):
        window["window_minutes"] = float(minutes)
    resets = raw.get("resets_at")
    if isinstance(resets, (int, float)) and not isinstance(resets, bool):
        window["resets_at"] = int(resets)
    return window


def _quota_state(windows: list[dict[str, Any]], reached: Any) -> str:
    if isinstance(reached, str) and reached:
        return "EXHAUSTED"
    percents = [
        w["used_percent"] for w in windows if isinstance(w.get("used_percent"), float)
    ]
    if not percents:
        return "UNKNOWN"
    if any(p >= 100 for p in percents):
        return "EXHAUSTED"
    if any(p >= 80 for p in percents):
        return "WARNING"
    return "AVAILABLE"


def codex_quota_from_transcript(
    path: Path, *, max_age_seconds: float = 3600.0
) -> dict[str, Any] | None:
    if _stale(path, max_age_seconds=max_age_seconds):
        return None
    for line in reversed(_tail_lines(path)):
        if '"rate_limits"' not in line:
            continue
        try:
            payload = json.loads(line).get("payload", {})
        except (json.JSONDecodeError, AttributeError):
            continue
        rate_limits = payload.get("rate_limits")
        if not isinstance(rate_limits, dict):
            continue
        windows: list[dict[str, Any]] = []
        for name in ("primary", "secondary"):
            window = _codex_window(rate_limits.get(name), name)
            if window is not None:
                windows.append(window)
        if not windows:
            continue
        reached = rate_limits.get("rate_limit_reached_type")
        snapshot = {
            "schema": PROVIDER_QUOTA_SNAPSHOT_SCHEMA,
            "provider": "CODEX",
            "source": "codex-rollout-transcript",
            "state": _quota_state(windows, reached),
            "windows": windows,
            "observed_at": _observed_at(path),
        }
        if isinstance(reached, str) and reached:
            snapshot["rate_limit_reached_type"] = reached
        return snapshot
    return None


def _claude_reset_epoch(content: str, observed_at: str) -> int | None:
    """Parse an explicit CLI clock + IANA zone; never guess the host timezone.

    A clock-only reset is supported only for a session limit. Weekly/date
    formats remain observable but cannot authorize a guessed retry time.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    match = re.search(r"session limit.*?resets (\d{1,2})(?::(\d{2}))?\s*(am|pm) \(([^)]+)\)\s*$", content, re.I)
    if not match:
        return None
    try:
        hour, minute = int(match[1]), int(match[2] or 0)
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            return None
        stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        local = stamp.astimezone(ZoneInfo(match[4]))
        reset = local.replace(hour=hour % 12 + (12 if match[3].lower() == "pm" else 0), minute=minute, second=0, microsecond=0)
        if reset <= local:
            reset += timedelta(days=1)
        return int(reset.timestamp())
    except (ValueError, ZoneInfoNotFoundError):
        return None


def claude_quota_from_transcript(
    path: Path, *, max_age_seconds: float = 3600.0
) -> dict[str, Any] | None:
    """Read provider error records, not arbitrary assistant/user discussion."""
    import hashlib
    if _stale(path, max_age_seconds=max_age_seconds):
        return None
    for line in reversed(_tail_lines(path)):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        api_error = entry.get("type") == "assistant" and entry.get("isApiErrorMessage") is True
        if api_error:
            message = entry.get("message") or {}
            parts = message.get("content", []) if isinstance(message, dict) else []
            content = " ".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("type") == "text") if isinstance(parts, list) else ""
            if not re.search(r"you(?:'|’)?ve hit your (?:session|weekly|usage) limit", content, re.I):
                continue
        elif entry.get("type") == "system":
            content = str(entry.get("content") or "")
            if "usage limit" not in content.lower():
                continue
        else:
            continue
        stamp = entry.get("timestamp")
        try:
            observed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if observed.tzinfo is None or not -60 <= time.time() - observed.timestamp() <= max_age_seconds:
                continue
        except ValueError:
            continue
        reached = api_error or "reached" in content.lower() or "hit" in content.lower()
        reset = _claude_reset_epoch(content, str(stamp)) if api_error else None
        window = {"name": "SESSION" if "session limit" in content.lower() else "5_HOUR" if "5-hour" in content.lower() else "USAGE_LIMIT"}
        if reset is not None:
            window["resets_at"] = reset
        return {
            "schema": PROVIDER_QUOTA_SNAPSHOT_SCHEMA, "provider": "CLAUDE",
            "source": "claude-transcript-api-error" if api_error else "claude-transcript-notice",
            "state": "EXHAUSTED" if reached else "WARNING", "windows": [window],
            "notice": content[:200], "observed_at": stamp,
            "provider_session_ref": path.stem,
            "evidence_id": hashlib.sha256((path.stem + str(stamp) + content).encode("utf-8")).hexdigest(),
        }
    return None


def claude_session_quota(provider_session_ref: str) -> dict[str, Any] | None:
    # Exact session correlation for work recovery; newest account transcript
    # alone must never suspend a different session's in-flight operation.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", provider_session_ref):
        return None
    paths = list((_provider_home("CLAUDE") / "projects").glob("*/" + provider_session_ref + ".jsonl"))
    if len(paths) != 1:
        return None
    return claude_quota_from_transcript(paths[0], max_age_seconds=24 * 3600)


def grok_quota_from_billing_log(
    path: Path, *, max_age_seconds: float = 3600.0
) -> dict[str, Any] | None:
    """Grok CLI logs its weekly credit usage to ``~/.grok/logs/unified.jsonl``."""

    if _stale(path, max_age_seconds=max_age_seconds):
        return None
    for line in reversed(_tail_lines(path)):
        if "billing: fetched credits config" not in line:
            continue
        try:
            config = json.loads(line).get("ctx", {}).get("config", {})
        except (json.JSONDecodeError, AttributeError):
            continue
        used = config.get("creditUsagePercent")
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            continue
        window: dict[str, Any] = {"name": "CREDITS", "used_percent": float(used)}
        period = config.get("currentPeriod")
        if isinstance(period, dict):
            period_type = period.get("type")
            if isinstance(period_type, str) and period_type:
                window["name"] = (
                    period_type.replace("USAGE_PERIOD_TYPE_", "").upper() or "CREDITS"
                )
            end = period.get("end")
            if isinstance(end, str) and end:
                window["resets_at"] = end
        if "resets_at" not in window:
            end = config.get("billingPeriodEnd")
            if isinstance(end, str) and end:
                window["resets_at"] = end
        state = "AVAILABLE"
        if used >= 100:
            state = "EXHAUSTED"
        elif used >= 80:
            state = "WARNING"
        return {
            "schema": PROVIDER_QUOTA_SNAPSHOT_SCHEMA,
            "provider": "GROK",
            "source": "grok-cli-billing-log",
            "state": state,
            "windows": [window],
            "observed_at": _observed_at(path),
        }
    return None


def sweep_transcript_quota(
    *, home_by_provider: dict[str, Path] | None = None, max_age_seconds: float = 3600.0
) -> list[dict[str, Any]]:
    """Best-effort quota snapshots from each provider CLI's own local files."""

    homes = home_by_provider or {}
    readers = {
        "CODEX": codex_quota_from_transcript,
        "CLAUDE": claude_quota_from_transcript,
        "GROK": grok_quota_from_billing_log,
    }
    snapshots: list[dict[str, Any]] = []
    for provider, reader in readers.items():
        path = _newest_transcript(provider, homes.get(provider))
        if path is None:
            continue
        try:
            snapshot = reader(path, max_age_seconds=max_age_seconds)
        except Exception:  # noqa: BLE001 - a transcript read must never raise upward
            snapshot = None
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


__all__ = [
    "codex_quota_from_transcript",
    "claude_quota_from_transcript",
    "grok_quota_from_billing_log",
    "sweep_transcript_quota",
]
