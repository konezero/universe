"""Host State for Universe-managed vs standalone CLI processes.

MANAGED / UNMANAGED is Host State. It is not a Mode and not a session
relationship.
"""

from __future__ import annotations

from collections.abc import Mapping


def is_universe_managed_host(environment: Mapping[str, str]) -> bool:
    """True when this process was launched by a Universe-owned terminal."""

    if str(environment.get("UNIVERSE_SUPERVISOR_SESSION_ID") or "").strip():
        return True
    terminal_id = str(environment.get("UNIVERSE_TERMINAL_ID") or "").strip()
    managed_shell = str(environment.get("UNIVERSE_MANAGED_SHELL") or "").strip()
    return bool(terminal_id) and managed_shell == "1"


def host_state(environment: Mapping[str, str]) -> str:
    return "MANAGED" if is_universe_managed_host(environment) else "UNMANAGED"
