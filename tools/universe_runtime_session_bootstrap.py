"""Project Runtime projection of a server-resolved managed Session Anchor.

Universe owns identity; this module never substitutes the current Mode's session
or a provider conversation id for the Supervisor session id.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def bootstrap_managed_session(repo_root: Path, session: Mapping[str, Any]) -> dict[str, Any]:
    identity = {k: str(session.get(k) or "").strip() for k in (
        "session_id", "session_anchor_ref", "provider", "provider_session_ref", "mode"
    )}
    identity["provider"] = identity["provider"].upper()
    identity["mode"] = identity["mode"].upper()
    if not all(identity[k] for k in ("session_id", "session_anchor_ref", "mode")):
        return {"status": "MANAGED_SESSION_IDENTITY_REQUIRED"}
    if identity["provider"] not in {"CODEX", "CLAUDE", "GROK"}:
        return {"status": "MANAGED_SESSION_PROVIDER_REQUIRED"}
    package = Path(__file__).absolute().parents[1] / ".ai" / "runtime"
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))
    from reference_runtime.anchor_session_memory_runtime import AnchorSessionMemoryRuntime
    from reference_runtime.mode_registry_runtime import (
        ModeRegistryError, load_mode_registry, mode_definition_digest, mode_registry_digest,
    )
    from reference_runtime.project_runtime_store import ProjectRuntimeStore, ProjectRuntimeStoreError

    sid, anchor, mode = (identity[k] for k in ("session_id", "session_anchor_ref", "mode"))
    path = repo_root / ".ai/runtime/session_store" / (
        "session-" + hashlib.sha256(sid.encode("utf-8")).hexdigest()[:24] + ".sqlite3"
    )
    runtime = store = None
    try:
        registry = load_mode_registry(repo_root)
        definition = registry.resolve(mode)  # Reject unknown Mode before creating a session.
        # Validate existing identity before touching the Mode's current pointer.
        previous = None
        if path.is_file():
            runtime = AnchorSessionMemoryRuntime(database_path=path)
            previous = runtime.stored_snapshot()
        snapshot = dict((previous or {}).get("snapshot") or {})
        if previous and (
            previous.get("anchor_id") != anchor
            or snapshot.get("session_id") not in (None, "", sid)
            or snapshot.get("provider") not in (None, "", identity["provider"])
        ):
            return {"status": "MANAGED_SESSION_IDENTITY_CONFLICT", "session_id": sid}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        # Preserve monotonic observation time if an existing attachment was
        # written by a Host whose clock is ahead of this hook's clock.
        prior_time = str(snapshot.get("observed_at") or "")
        if prior_time and datetime.fromisoformat(prior_time.replace("Z", "+00:00")) > datetime.fromisoformat(now.replace("Z", "+00:00")):
            now = prior_time
        store = ProjectRuntimeStore(repo_root)
        prepared = store.prepare_mode_current_anchor(
            project_id=registry.owner, registry_payload=registry.as_dict(),
            registry_revision=registry.revision, registry_digest=mode_registry_digest(registry),
            registry_source_ref=registry.path.resolve().as_uri(), mode=mode,
            mode_definition_digest=mode_definition_digest(definition),
            source_ref=registry.path.resolve().as_uri(), observed_at=now,
            host_session_ref=sid,
        )
        if prepared.get("status") not in {"MODE_CURRENT_ANCHOR_CREATED", "MODE_CURRENT_ANCHOR_OBSERVED"}:
            return {"status": "MANAGED_MODE_PREPARATION_FAILED", "cause": prepared.get("status")}
        mode_anchor = prepared["snapshot"]["anchor_id"]
        snapshot.update({
            "session_id": sid, "anchor_id": anchor,
            "frame_id": snapshot.get("frame_id") or "current",
            "state": snapshot.get("state") or "READY", "host_state": "MANAGED",
            "provider": identity["provider"], "session_anchor_ref": anchor,
            "mode": mode, "requested_mode": mode,
            "mode_anchor_ref": {"mode": mode, "anchor_id": mode_anchor},
            "observed_at": now,
        })
        if identity["provider_session_ref"]:
            prefix = {"CODEX": "codex", "CLAUDE": "claude-code", "GROK": "grok-cli"}[identity["provider"]]
            snapshot["provider_session_ref"] = identity["provider_session_ref"]
            snapshot["observer_session_ref"] = prefix + ":" + identity["provider_session_ref"]
        if runtime is None:
            runtime = AnchorSessionMemoryRuntime(database_path=path)
        recorded = runtime.record_snapshot(
            snapshot=snapshot,
            source_ref=(previous or {}).get("source_ref") or "universe://sessions/" + anchor,
        )
        if recorded.get("status") not in {"SNAPSHOT_RECORDED", "SNAPSHOT_UPDATED"}:
            return {"status": "MANAGED_SESSION_RECORD_FAILED", "cause": recorded.get("status")}
        attached = store.attach_session_anchor(
            mode=mode, session_id=sid, session_anchor_id=anchor, observed_at=now,
        )
        if attached.get("status") != "MODE_SESSION_ANCHOR_ATTACHED":
            return {"status": "MANAGED_SESSION_LINK_FAILED", "cause": attached.get("status")}
        return {"status": "MANAGED_SESSION_BOOTSTRAP_COMPLETE", "session_id": sid,
                "session_anchor_ref": anchor, "mode_anchor_id": mode_anchor,
                "session_sql_path": str(path), "provider": identity["provider"],
                "authority_created": False, "execution_assignment_created": False}
    except (ModeRegistryError, ProjectRuntimeStoreError, sqlite3.Error, OSError, ValueError) as error:
        return {"status": "MANAGED_SESSION_BOOTSTRAP_FAILED",
                "error_code": getattr(error, "error_code", type(error).__name__), "detail": str(error)}
    finally:
        if runtime is not None:
            runtime.close()
        if store is not None:
            store.close()
