"""Transparent adapter for a caller-selected Task Frame runtime callable.

The adapter owns only transport metadata and the report for a failed Python
invocation.  On success it neither reads nor modifies the Task Frame output.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

ADAPTER_NAME = "ai-career.task-frame-thin-adapter"
ADAPTER_VERSION = "0.1.0"
INVOCATION_FAILED = "TASK_FRAME_RUNTIME_INVOCATION_FAILED"
RuntimeCallable = Callable[..., Any]


def adapter_metadata() -> dict[str, str]:
    """Return only adapter-owned transport metadata."""

    return {
        "adapter_name": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "invocation_id": uuid4().hex,
    }


def invoke_task_frame_runtime(
    *,
    runtime_callable: RuntimeCallable | None = None,
    runtime_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Invoke a caller-selected Task Frame callable without alteration.

    The caller must provide the callable and its already-selected arguments.
    This adapter does not select a model, provider, profile, turn, route,
    parent attachment, or adoption outcome. It also does not fabricate
    execution evidence, Runtime State, or a Result Packet if the Python call
    fails or no callable was selected.
    """

    metadata = adapter_metadata()
    try:
        if runtime_callable is None:
            raise TypeError("runtime_callable is required")
        runtime_output = runtime_callable(**dict(runtime_kwargs or {}))
    except Exception:
        return {
            "adapter_metadata": metadata,
            "adapter_result": {
                "status": "UNKNOWN",
                "error_code": INVOCATION_FAILED,
                "runtime_output": None,
            },
        }

    return {
        "adapter_metadata": metadata,
        "runtime_output": runtime_output,
    }
