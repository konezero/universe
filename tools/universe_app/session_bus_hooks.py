"""Compatibility endpoint: CLI lifecycle belongs to the Host, not the Bus."""
from typing import Any, Mapping

def handle_hook(bus: Any, host: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {"status":"HOST_LIFECYCLE_REQUIRED", "pending_count":0, "messages":[]}
