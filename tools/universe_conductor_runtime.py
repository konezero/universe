from __future__ import annotations

from collections import deque
import json
import os
import queue
import secrets
import subprocess  # nosec B404
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TextIO
from uuid import uuid4

from host_profile import resolve_host_tool
from process_identity import WindowsKillOnCloseJob, launched_process_identity
from session_supervisor import SessionSupervisorError, SessionSupervisorStore
from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli


PLANNING_RUNTIME_BINDING_SCHEMA = "universe.planning-runtime-binding.v1"


class UniverseConductorRuntimeError(RuntimeError):
    pass


NativeRunner = Callable[[NativeCliRequest], NativeCliResult]
SourceBindingResolver = Callable[[Path], Mapping[str, Any]]


class RuntimeProcess(Protocol):
    pid: int
    stdout: TextIO | None
    stderr: TextIO | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[..., RuntimeProcess]


class UniverseConductorRuntime:
    """Own the process-local Runtime binding for the Universe Conductor."""

    def __init__(
        self,
        repository_root: Path,
        *,
        session_node: str = "CONDUCTOR",
        requested_mode: str = "CONDUCTOR",
        exact_session_id: str = "",
        session_location: str = "UNIVERSE_LOCAL_SERVICE",
        parent_actor_ref: str = "universe-conductor",
        register_process_lease: bool = True,
        native_runner: NativeRunner = run_native_cli,
        source_binding_resolver: SourceBindingResolver | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
        startup_timeout: float = 30,
        session_supervisor: SessionSupervisorStore | None = None,
    ) -> None:
        self.repository_root = repository_root.expanduser().resolve(strict=True)
        self.session_node = _text(session_node, "session_node")
        self.requested_mode = _text(requested_mode, "requested_mode").upper()
        self.exact_session_id = str(exact_session_id or "").strip()
        self.session_location = _text(session_location, "session_location")
        self.parent_actor_ref = _text(parent_actor_ref, "parent_actor_ref")
        self.register_process_lease = bool(register_process_lease)
        self._mode_role: str | None = None
        self.native_runner = native_runner
        self.source_binding_resolver = source_binding_resolver
        self.process_factory = process_factory
        self.startup_timeout = startup_timeout
        self.session_supervisor = session_supervisor
        self.runtime_cli = (
            self.repository_root
            / ".ai"
            / "runtime"
            / "reference_runtime"
            / "cli.py"
        )
        if not self.runtime_cli.is_file():
            raise UniverseConductorRuntimeError("UNIVERSE_RUNTIME_CLI_UNAVAILABLE")
        self._process: RuntimeProcess | None = None
        self._runtime_job: WindowsKillOnCloseJob | None = None
        self._binding: dict[str, Any] | None = None
        self._stderr: deque[str] = deque(maxlen=40)
        self._supervisor_session_id: str | None = None
        self._lease_token: str | None = None
        self._lease_version: int | None = None
        self._process_identity: dict[str, Any] | None = None
        self._source_binding: dict[str, str] | None = None

    def start(self) -> Mapping[str, Any]:
        if self._binding is not None and self._process is not None:
            if self._process.poll() is None:
                return dict(self._binding)
            if self._runtime_job is not None:
                self._runtime_job.close()
                self._runtime_job = None
            self._binding = None
            self._process = None

        session = self._anchor_graph_session()
        if session is None:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_CONDUCTOR_SESSION_ANCHOR_UNAVAILABLE"
            )
        source = self._resolved_source_binding()
        anchor_id = _text(session.get("session_anchor_ref"), "session_anchor_ref")
        session_id = _text(session.get("session_id"), "session_id")
        frame_id = "current"
        self._mode_role = "UNASSIGNED"
        token = secrets.token_urlsafe(32)
        command = [
            str(_required_host_executable("python")),
            str(self.runtime_cli),
            "project-runtime",
            "serve",
            "--repo-root",
            str(self.repository_root),
            "--session-id",
            session_id,
            "--frame-id",
            frame_id,
            "--anchor-id",
            anchor_id,
            "--mode",
            self.requested_mode,
            "--host-action",
            "PERSISTENT_SESSION_ATTACH",
            "--session-location",
            self.session_location,
            "--commander-surface",
            "UNIVERSE_UI",
            "--execution-surface",
            "LOCAL_RUNTIME",
            "--repository-location",
            str(self.repository_root),
            "--port",
            "0",
            "--token",
            token,
        ]
        options: dict[str, Any] = {
            "cwd": str(self.repository_root),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": False,
        }
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            process = self.process_factory(command, **options)
        except OSError as error:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_START_FAILED"
            ) from error
        self._process = process
        try:
            runtime_job = WindowsKillOnCloseJob(process)
            self._runtime_job = runtime_job
            startup = self._read_startup(process)
            host_adapter = startup.get("host_adapter")
            runtime_state = startup.get("runtime_state")
            if (
                startup.get("status") != "PERSISTENT_SESSION_ATTACHED"
                or not isinstance(host_adapter, Mapping)
                or not isinstance(runtime_state, Mapping)
                or runtime_state.get("anchor_id") != anchor_id
                or runtime_state.get("mode") != self.requested_mode
                or runtime_state.get("role") != "UNASSIGNED"
                or runtime_state.get("executable_runtime_currentness") != "CURRENT"
                or startup.get("attachment_path") != "ANCHOR_GRAPH"
                or "mode_boot_binding" in startup
            ):
                raise UniverseConductorRuntimeError(
                    "UNIVERSE_RUNTIME_START_RESULT_INVALID:"
                    f"status={startup.get('status')};"
                    f"anchor={runtime_state.get('anchor_id') if isinstance(runtime_state, Mapping) else 'ABSENT'};"
                    "currentness="
                    f"{runtime_state.get('executable_runtime_currentness') if isinstance(runtime_state, Mapping) else 'ABSENT'}"
                )
            endpoint = _text(host_adapter.get("endpoint"), "host_adapter.endpoint")
            returned_token = _text(host_adapter.get("token"), "host_adapter.token")
            if returned_token != token:
                raise UniverseConductorRuntimeError(
                    "UNIVERSE_RUNTIME_TOKEN_MISMATCH"
                )
            self._binding = {
                "schema": PLANNING_RUNTIME_BINDING_SCHEMA,
                "endpoint": endpoint,
                "token": token,
                "session_id": session_id,
                "origin_anchor_ref": anchor_id,
                "origin_frame_id": frame_id,
                "source_ref": source["source_ref"],
                "source_commit": source["source_commit"],
                "source_repository": source["source_repository"],
                "runtime_currentness_observation": str(
                    runtime_state["executable_runtime_currentness"]
                ),
                "attachment_path": "ANCHOR_GRAPH",
                "parent_actor_ref": self.parent_actor_ref,
                "parent_evidence_ref": (
                    f"session-anchor://{self.session_node}/{self.requested_mode}/{anchor_id}"
                ),
                "binding_evidence_ref": (
                    "process-local://"
                    f"{self.session_node}/{self.requested_mode.lower()}-runtime/{session_id}"
                ),
            }
            if self.register_process_lease:
                self._register_process_lease(
                    process=process,
                    command=command,
                    endpoint=endpoint,
                    token=token,
                )
            return dict(self._binding)
        except Exception:
            self.stop()
            raise

    def _anchor_graph_session(self) -> Mapping[str, Any] | None:
        """Resolve the one default supervised session for this Universe Mode."""

        if self.session_supervisor is None:
            return None
        if self.exact_session_id:
            try:
                session = self.session_supervisor.get_session(self.exact_session_id)
            except SessionSupervisorError:
                return None
            if (
                str(session.get("node") or "") != self.session_node
                or str(session.get("mode") or "").upper() != self.requested_mode
            ):
                return None
            return session
        candidates = [
            item
            for item in self.session_supervisor.list_sessions(
                node=self.session_node,
                mode=self.requested_mode,
                include_hidden=True,
            )
            if bool(item.get("is_default"))
        ]
        if len(candidates) != 1:
            return None
        return candidates[0]

    def observe(self, message_id: str) -> Mapping[str, Any]:
        normalized_id = _text(message_id, "message_id")
        session = self._anchor_graph_session()
        if session is None:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_CONDUCTOR_SESSION_ANCHOR_UNAVAILABLE"
            )
        anchor_id = _text(session.get("session_anchor_ref"), "session_anchor_ref")
        return {
            "schema": "universe.anchor-graph-commander-observation.v1",
            "status": "COMMANDER_INPUT_OBSERVED",
            "mode": self.requested_mode,
            "session_id": _text(session.get("session_id"), "session_id"),
            "session_anchor_ref": anchor_id,
            "evidence_ref": f"universe://conductor-room/messages/{normalized_id}",
        }

    def stop(self) -> None:
        process = self._process
        job = self._runtime_job
        if process is None or process.poll() is not None:
            self._process = None
            self._runtime_job = None
            self._binding = None
            if job is not None:
                job.close()
            self._mark_stale_if_owned("PROCESS_NOT_RUNNING_AT_STOP")
            return
        stop_receipt = self._authorize_supervised_stop()
        self._process = None
        self._runtime_job = None
        self._binding = None
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if job is not None:
            job.close()
        self._complete_supervised_stop(stop_receipt)

    def reconcile(self) -> str:
        process = self._process
        if process is None:
            return "NOT_STARTED"
        if process.poll() is None:
            return "LIVE"
        self._process = None
        if self._runtime_job is not None:
            self._runtime_job.close()
            self._runtime_job = None
        self._binding = None
        self._mark_stale_if_owned("PROCESS_EXITED_UNEXPECTEDLY")
        return "EXITED"

    def continuity_coordinate(self) -> Mapping[str, str] | None:
        if self._binding is None or self._process is None:
            return None
        if self._process.poll() is not None:
            return None
        return {
            "node": self.session_node,
            "mode": self.requested_mode,
            "session_id": str(self._binding["session_id"]),
            "frame_id": str(self._binding["origin_frame_id"]),
            "anchor_id": str(self._binding["origin_anchor_ref"]),
            "currentness": str(
                self._binding["runtime_currentness_observation"]
            ),
            "source_ref": self._resolved_source_binding()["source_ref"],
        }

    def _register_process_lease(
        self,
        *,
        process: RuntimeProcess,
        command: list[str],
        endpoint: str,
        token: str,
    ) -> None:
        if self.session_supervisor is None:
            return
        sessions = self.session_supervisor.list_sessions(
            node=self.session_node, mode=self.requested_mode
        )
        session = next((item for item in sessions if item["is_default"]), None)
        if session is None:
            raise UniverseConductorRuntimeError(
                "SUPERVISOR_CONDUCTOR_SESSION_UNAVAILABLE"
            )
        identity = launched_process_identity(
            process,
            executable=Path(command[0]),
            command=command,
            endpoint=endpoint,
            handshake_token=token,
        )
        existing = session.get("process_lease")
        expected_version = (
            0 if existing is None else int(existing.get("lease_version", 0))
        )
        acquired = self.session_supervisor.acquire_lease(
            session["session_id"],
            identity,
            expected_lease_version=expected_version,
            stop_capability=token,
        )
        self._supervisor_session_id = str(session["session_id"])
        self._lease_token = str(acquired["lease_token"])
        self._lease_version = int(acquired["lease"]["lease_version"])
        self._process_identity = identity

    def _authorize_supervised_stop(self) -> Mapping[str, Any] | None:
        if (
            self.session_supervisor is None
            or self._supervisor_session_id is None
            or self._lease_token is None
            or self._lease_version is None
            or self._process_identity is None
        ):
            return None
        try:
            receipt = self.session_supervisor.authorize_stop(
                self._supervisor_session_id,
                self._process_identity,
                lease_token=self._lease_token,
                expected_lease_version=self._lease_version,
            )
        except SessionSupervisorError:
            current = self.session_supervisor.get_session(
                self._supervisor_session_id
            ).get("process_lease")
            if isinstance(current, Mapping):
                self._lease_version = int(current["lease_version"])
            raise
        self._lease_version = int(receipt["lease_version"])
        return receipt

    def _complete_supervised_stop(self, receipt: Mapping[str, Any] | None) -> None:
        if (
            receipt is None
            or self.session_supervisor is None
            or self._supervisor_session_id is None
            or self._lease_token is None
            or self._lease_version is None
        ):
            return
        self.session_supervisor.complete_stop(
            self._supervisor_session_id,
            lease_token=self._lease_token,
            expected_lease_version=self._lease_version,
        )
        self._supervisor_session_id = None
        self._lease_token = None
        self._lease_version = None
        self._process_identity = None

    def _mark_stale_if_owned(self, reason: str) -> None:
        if (
            self.session_supervisor is None
            or self._supervisor_session_id is None
            or self._lease_token is None
            or self._lease_version is None
            or self._process_identity is None
        ):
            return
        self.session_supervisor.mark_lease_stale(
            self._supervisor_session_id,
            self._process_identity,
            lease_token=self._lease_token,
            expected_lease_version=self._lease_version,
            reason=reason,
        )
        self._supervisor_session_id = None
        self._lease_token = None
        self._lease_version = None
        self._process_identity = None

    def _read_startup(self, process: RuntimeProcess) -> Mapping[str, Any]:
        if process.stdout is None:
            raise UniverseConductorRuntimeError("UNIVERSE_RUNTIME_STDOUT_UNAVAILABLE")
        output: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_line() -> None:
            try:
                output.put(process.stdout.readline())
            except (OSError, UnicodeError):
                output.put("")

        threading.Thread(target=read_line, daemon=True).start()
        if process.stderr is not None:
            threading.Thread(
                target=self._drain_stderr,
                args=(process.stderr,),
                daemon=True,
            ).start()
        try:
            raw = output.get(timeout=self.startup_timeout)
        except queue.Empty as error:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_START_TIMEOUT"
            ) from error
        if not raw:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_START_RESULT_UNAVAILABLE"
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_START_RESULT_INVALID"
            ) from error
        if not isinstance(payload, Mapping):
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_START_RESULT_INVALID"
            )
        return payload

    def _drain_stderr(self, stream: TextIO) -> None:
        try:
            for line in stream:
                self._stderr.append(line.rstrip())
        except (OSError, UnicodeError):
            return

    def _invoke(
        self,
        arguments: tuple[str, ...],
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        request_path = (
            self.repository_root
            / ".ai"
            / "runtime"
            / "tmp"
            / f"universe-conductor-runtime-{uuid4().hex}.json"
        )
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(
            json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=_required_host_executable("python"),
                    arguments=(
                        str(self.runtime_cli),
                        *arguments,
                        "--request",
                        str(request_path),
                    ),
                    cwd=self.repository_root,
                    timeout_seconds=30,
                )
            )
        finally:
            request_path.unlink(missing_ok=True)
        if result.status != "COMPLETED" or result.return_code != 0:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_COMMAND_FAILED"
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_RESULT_INVALID"
            ) from error
        if not isinstance(payload, Mapping):
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RUNTIME_RESULT_INVALID"
            )
        return payload

    def _resolved_source_binding(self) -> dict[str, str]:
        if self._source_binding is not None:
            return dict(self._source_binding)
        if self.source_binding_resolver is None:
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RELEASE_SELECTION_REQUIRED"
            )
        candidate = self.source_binding_resolver(self.repository_root)
        if not isinstance(candidate, Mapping):
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RELEASE_SELECTION_UNAVAILABLE"
            )
        if str(candidate.get("status") or "").upper() != "SELECTED":
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RELEASE_SELECTION_REQUIRED"
            )
        release_id = _text(candidate.get("release_id"), "release_id")
        source_repository = _text(
            candidate.get("source_repository"), "source_repository"
        )
        source_commit = _text(candidate.get("source_commit"), "source_commit").lower()
        database_sha256 = _text(
            candidate.get("database_sha256"), "database_sha256"
        ).lower()
        if (
            len(source_commit) != 40
            or any(character not in "0123456789abcdef" for character in source_commit)
            or len(database_sha256) != 64
            or any(character not in "0123456789abcdef" for character in database_sha256)
        ):
            raise UniverseConductorRuntimeError(
                "UNIVERSE_RELEASE_SELECTION_INVALID"
            )
        binding = {
            "source_ref": f"universe-release-db://{release_id}@{database_sha256}",
            "source_commit": source_commit,
            "source_repository": source_repository,
        }
        self._source_binding = binding
        return dict(binding)

def _required_host_executable(tool: str) -> Path:
    resolved = resolve_host_tool(tool)
    if resolved is None:
        raise UniverseConductorRuntimeError(
            f"{tool.upper()}_HOST_TOOL_UNAVAILABLE"
        )
    return resolved.executable


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UniverseConductorRuntimeError(f"{field.upper()}_REQUIRED")
    return value.strip()
