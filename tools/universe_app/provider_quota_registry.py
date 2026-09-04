"""Account-level provider quota, rolled up to one snapshot per provider.

Quota is not a per-session fact: every live session of a provider draws on the
same account bucket.  Sessions report a ``universe.provider-quota-snapshot.v1``
inside their runtime observation; this registry keeps the freshest one per
provider so the terminal pane can show all three at a glance without caring
which session is focused.
"""

from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any, Mapping


PROVIDER_QUOTA_SNAPSHOT_SCHEMA = "universe.provider-quota-snapshot.v1"
PROVIDER_QUOTA_VIEW_SCHEMA = "universe.provider-quota-view.v1"
KNOWN_PROVIDERS = ("CLAUDE", "GROK", "CODEX")
_STATE_RANK = {"UNKNOWN": 0, "AVAILABLE": 1, "WARNING": 2, "EXHAUSTED": 3}


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _clean_windows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    windows: list[dict[str, Any]] = []
    for item in value[:4]:
        if not isinstance(item, Mapping):
            continue
        window: dict[str, Any] = {}
        name = item.get("name")
        window["name"] = str(name).upper() if isinstance(name, str) and name else "WINDOW"
        for key in ("used_percent", "window_minutes"):
            raw = item.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                window[key] = float(raw)
        reset = item.get("resets_at")
        if isinstance(reset, (str, int, float)) and not isinstance(reset, bool):
            window["resets_at"] = reset
        windows.append(window)
    return windows


class ProviderQuotaRegistry:
    """Thread-safe ``provider -> freshest snapshot`` map."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_provider: dict[str, dict[str, Any]] = {}

    def record(self, snapshot: Any, *, session_ref: str | None = None) -> None:
        """Store ``snapshot`` if it is a real reading for a known provider.

        An ``UNKNOWN`` snapshot with no windows never displaces a real one — a
        session that has not observed quota yet must not blank the strip.
        """

        if not isinstance(snapshot, Mapping):
            return
        provider = str(snapshot.get("provider") or "").strip().upper()
        if provider not in KNOWN_PROVIDERS:
            return
        state = str(snapshot.get("state") or "UNKNOWN").strip().upper()
        if state not in _STATE_RANK:
            state = "UNKNOWN"
        windows = _clean_windows(snapshot.get("windows"))
        if state == "UNKNOWN" and not windows:
            return
        observed_at = snapshot.get("observed_at")
        if not isinstance(observed_at, str) or not observed_at.strip():
            observed_at = _utc_now()
        entry = {
            "schema": PROVIDER_QUOTA_SNAPSHOT_SCHEMA,
            "provider": provider,
            "source": str(snapshot.get("source") or "runtime_observation"),
            "state": state,
            "windows": windows,
            "session_ref": session_ref or "",
            "observed_at": observed_at,
            "recorded_at": _utc_now(),
        }
        reached = snapshot.get("rate_limit_reached_type")
        if isinstance(reached, str) and reached:
            entry["rate_limit_reached_type"] = reached
        with self._lock:
            current = self._by_provider.get(provider)
            # Keep the freshest reading — a stale transcript sweep must not
            # overwrite a live SDK reading that landed in the same pass.
            if (
                current is not None
                and str(current.get("observed_at") or "") > observed_at
            ):
                return
            self._by_provider[provider] = entry

    def view(self) -> dict[str, Any]:
        """A stable three-row view, one row per known provider."""

        with self._lock:
            stored = {key: dict(value) for key, value in self._by_provider.items()}
        providers = []
        for provider in KNOWN_PROVIDERS:
            entry = stored.get(provider)
            if entry is None:
                providers.append(
                    {
                        "schema": PROVIDER_QUOTA_SNAPSHOT_SCHEMA,
                        "provider": provider,
                        "source": "none",
                        "state": "UNKNOWN",
                        "windows": [],
                        "session_ref": "",
                        "observed_at": None,
                    }
                )
            else:
                providers.append(entry)
        return {
            "schema": PROVIDER_QUOTA_VIEW_SCHEMA,
            "generated_at": _utc_now(),
            "providers": providers,
        }


__all__ = [
    "PROVIDER_QUOTA_SNAPSHOT_SCHEMA",
    "PROVIDER_QUOTA_VIEW_SCHEMA",
    "KNOWN_PROVIDERS",
    "ProviderQuotaRegistry",
]
