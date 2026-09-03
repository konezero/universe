"""Verify that a prompt written into a ConPTY actually started a turn.

Ports the poll loop from Orca's
``src/main/runtime/agent-prompt-submission-verification.ts``
(MIT, stablyai/orca @ 67e22345). Hook counters are preferred; PTY output
sequence is the fallback when a turn-start edge cannot be observed.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

AGENT_PROMPT_EFFECT_POLL_MS = 50
AGENT_PROMPT_EFFECT_TIMEOUT_MS = 30_000
AGENT_PROMPT_STALLED = "stalled"
AGENT_PROMPT_DELIVERED = "delivered"
AGENT_PROMPT_PENDING = "pending"


def prompt_activity(
    *,
    generation: int = 0,
    permission_sequence: int = 0,
    working_sequence: int = 0,
    explicit_working_started_at: float | None = None,
    output_sequence: int = 0,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "generation": int(generation),
        "permission_sequence": int(permission_sequence),
        "working_sequence": int(working_sequence),
        "explicit_working_started_at": explicit_working_started_at,
        "output_sequence": int(output_sequence),
        "status": status,
    }


def observed_hook_working_after_baseline(
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    started = current.get("explicit_working_started_at")
    if started is None:
        return False
    previous = baseline.get("explicit_working_started_at") or 0
    return float(started) > float(previous)


def observed_delivery_evidence(
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    if str(baseline.get("status") or "") != "working":
        return False
    return int(current.get("output_sequence") or 0) > int(
        baseline.get("output_sequence") or 0
    )


def agent_prompt_effect_observed(
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> bool:
    return (
        int(current.get("working_sequence") or 0)
        > int(baseline.get("working_sequence") or 0)
        or observed_hook_working_after_baseline(baseline, current)
        or observed_delivery_evidence(baseline, current)
    )


def prompt_blocked(baseline: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    if str(current.get("status") or "") == "permission":
        return True
    return int(current.get("permission_sequence") or 0) > int(
        baseline.get("permission_sequence") or 0
    )


def verify_agent_prompt_submission(
    *,
    baseline: Mapping[str, Any],
    read_activity: Callable[[], Mapping[str, Any]],
    timeout_ms: int = AGENT_PROMPT_EFFECT_TIMEOUT_MS,
    poll_ms: int = AGENT_PROMPT_EFFECT_POLL_MS,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> str:
    """Poll until a turn-start edge or timeout.

    Returns ``delivered``, ``stalled``, or ``blocked``.
    """

    if prompt_blocked(baseline, baseline):
        return "blocked"
    deadline = now() + (timeout_ms / 1000.0)
    while now() < deadline:
        current = read_activity()
        if int(current.get("generation") or 0) != int(baseline.get("generation") or 0):
            return "stale"
        if prompt_blocked(baseline, current):
            return "blocked"
        if agent_prompt_effect_observed(baseline, current):
            return AGENT_PROMPT_DELIVERED
        sleep(max(0.0, poll_ms / 1000.0))
    current = read_activity()
    if int(current.get("generation") or 0) != int(baseline.get("generation") or 0):
        return "stale"
    if prompt_blocked(baseline, current):
        return "blocked"
    if agent_prompt_effect_observed(baseline, current):
        return AGENT_PROMPT_DELIVERED
    return AGENT_PROMPT_STALLED
