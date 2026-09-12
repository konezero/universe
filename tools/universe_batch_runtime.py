"""Host-owned Runtime lifecycle for a single RAG batch, independent of CLI sessions."""
from contextlib import contextmanager
import hmac
import sys
import threading
from uuid import uuid4

from universe_conductor_runtime import UniverseConductorRuntime
from universe_app.connection import UniverseError
from universe_app.session_runtime_credentials import runtime_credential_ref


class UniverseBatchRuntime(UniverseConductorRuntime):
    """Allocate a job coordinate; the installed Runtime owns its durable journal.

    This is an execution context, never a Supervisor provider session. Starting
    it does not register a vendor CLI or change the user's Mode Current Anchor.
    The existing project-runtime adapter opens the installed MASTER lineage.
    """
    def __init__(self, repository_root, *, project_id, **kwargs):
        self.batch_session_id = "batch-runtime-" + uuid4().hex
        self.batch_anchor_id = "batch-anchor-" + uuid4().hex
        super().__init__(
            repository_root, session_node=project_id, requested_mode="MASTER",
            session_location="UNIVERSE_BATCH_HOST",
            parent_actor_ref="universe-batch-host:" + self.batch_session_id,
            register_process_lease=False, **kwargs,
        )

    def _anchor_graph_session(self):
        return {"session_id": self.batch_session_id,
                "session_anchor_ref": self.batch_anchor_id,
                "node": self.session_node, "mode": self.requested_mode}


class MemoryBatchRuntimePool:
    """Own private bindings only for the lifetime of an executing batch."""
    def __init__(self, factory):
        self.factory = factory
        self._lock = threading.RLock()
        self._entries = {}
        self._stopping = False

    @contextmanager
    def execution(self, project_id, stage):
        runtime = None
        sid = None
        key = (project_id, stage)
        try:
            with self._lock:
                if self._stopping:
                    raise UniverseError("MEMORY_BATCH_RUNTIME_STOPPING", "Batch Host is stopping", 409)
                if any(e["key"] == key for e in self._entries.values()):
                    raise UniverseError("MEMORY_BATCH_RUNTIME_BUSY", "This project stage is already executing", 409)
                runtime = self.factory(project_id)
                binding = dict(runtime.start())
                sid = binding["session_id"]
                if not sid.startswith("batch-runtime-") or binding.get("runtime_currentness_observation") != "CURRENT":
                    raise UniverseError("MEMORY_BATCH_RUNTIME_START_INVALID", "Batch Runtime startup was not attested", 409)
                public = {"session_id": sid, "session_anchor_ref": binding["origin_anchor_ref"],
                          "credential_ref": runtime_credential_ref(binding)}
                self._entries[sid] = {"key": key, "runtime": runtime, "binding": binding,
                                      "public": public, "frame": None}
            yield binding, public
        finally:
            if runtime is not None:
                primary = sys.exc_info()[1]
                try:
                    with self._lock:
                        entry = self._entries.pop(sid, None) if sid else None
                        if entry is not None or sid is None:
                            runtime.stop()
                        elif not self._stopping:
                            runtime.stop()
                except Exception as cleanup:
                    if primary is None:
                        raise
                    primary.add_note("Batch Runtime cleanup failed: " + type(cleanup).__name__)

    def bind_frame(self, project_id, request):
        with self._lock:
            entry = self._entries.get(request["session_id"])
            if entry is None or entry["key"][0] != project_id:
                raise UniverseError("MEMORY_BATCH_RUNTIME_UNAVAILABLE", "Batch execution has ended", 409)
            if entry["frame"] is not None:
                raise UniverseError("MEMORY_BATCH_FRAME_ALREADY_BOUND", "Batch already owns a frame", 409)
            entry["frame"] = dict(request)

    def resolve(self, project_id, request):
        sid = str(request.get("session_id") or "")
        if not sid.startswith("batch-runtime-"):
            return None
        with self._lock:
            entry = self._entries.get(sid)
            if entry is None:
                raise UniverseError("MEMORY_BATCH_RUNTIME_UNAVAILABLE", "Batch execution is no longer active", 409)
            if entry["key"][0] != project_id:
                raise UniverseError("RUNTIME_CREDENTIAL_PROJECT_MISMATCH", "Batch belongs to another project", 409)
            expected = entry["frame"]
            if expected is None or any(request.get(k) != expected.get(k) for k in
                    ("session_id", "session_anchor_ref", "task_frame_ref", "frame_id", "turn_id", "invoker_actor_ref")):
                raise UniverseError("MEMORY_BATCH_FRAME_MISMATCH", "Request does not identify this batch frame", 409)
            credential = request.get("credential_ref")
            if not isinstance(credential, str) or not hmac.compare_digest(credential.encode(), expected["credential_ref"].encode()):
                raise UniverseError("RUNTIME_CREDENTIAL_REF_INVALID", "Batch credential does not match", 409)
            if entry["runtime"].reconcile() != "LIVE":
                raise UniverseError("MEMORY_BATCH_RUNTIME_EXITED", "Batch Runtime exited", 409)
            return {k: entry["binding"][k] for k in ("endpoint", "token")}

    def close(self):
        failures = []
        with self._lock:
            self._stopping = True
            for sid, entry in list(self._entries.items()):
                try:
                    entry["runtime"].stop()
                    self._entries.pop(sid, None)
                except Exception as error:
                    failures.append(type(error).__name__)
        if failures:
            raise UniverseError("MEMORY_BATCH_RUNTIME_CLOSE_FAILED", ", ".join(failures), 500)
