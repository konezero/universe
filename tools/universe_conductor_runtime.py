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
SourceCommitResolver = Callable[[Path], str]


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
        native_runner: NativeRunner = run_native_cli,
        source_commit_resolver: SourceCommitResolver | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
        startup_timeout: float = 30,
        session_supervisor: SessionSupervisorStore | None = None,
    ) -> None:
        self.repository_root = repository_root.expanduser().resolve(strict=True)
        self.native_runner = native_runner
        self.source_commit_resolver = source_commit_resolver or self._git_head
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

    def start(self) -> Mapping[str, Any]:
        if self._binding is not None and self._process is not None:
            if self._process.poll() is None:
                return dict(self._binding)
            if self._runtime_job is not None:
                self._runtime_job.close()
                self._runtime_job = None
            self._binding = None
            self._process = None

        definition = self._conductor_definition()
        source_commit = self.source_commit_resolver(self.repository_root)
        host_session_ref = f"universe://local-service/conductor/{uuid4().hex}"
        prepared = self._invoke(
            ("prepare-session", "--repo-root", str(self.repository_root)),
            {
                "command": "BOOT",
                "source_state": "SOURCE_READY",
                "source_ref": (
                    f"git-object-database://universe@{source_commit}"
                ),
                "source_commit": source_commit,
                "source_repository": str(self.repository_root),
                "mode": "CONDUCTOR",
                "role": definition["role"],
                "scope": definition["scope"],
                "host_session_ref": host_session_ref,
                "anchor_snapshot_ref": "UNKNOWN",
                "host_executable_capability": "AVAILABLE",
                "mode_profile": definition["mode_profile"],
                "task_requirement": "NONE",
                "evidence_profile": "NONE",
            },
        )
        anchor_id = self._prepared_anchor_id(prepared)
        mode_boot_binding = self._prepared_mode_boot_binding(
            prepared,
            anchor_id=anchor_id,
        )
        session_id = f"universe-conductor-{uuid4().hex}"
        frame_id = mode_boot_binding["frame_id"]
        token = secrets.token_urlsafe(32)
        command = [
            str(_required_host_executable("python")),
            str(self.runtime_cli),
            "session-boot",
            "serve",
            "--repo-root",
            str(self.repository_root),
            "--session-id",
            session_id,
            "--frame-id",
            frame_id,
            "--anchor-id",
            anchor_id,
            "--boot-binding-id",
            mode_boot_binding["binding_id"],
            "--host-action",
            "UNIVERSE_LOCAL_SERVICE",
            "--session-location",
            "UNIVERSE_LOCAL_SERVICE",
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
                startup.get("status") != "SESSION_BOOT_IMAGE_CREATED"
                or not isinstance(host_adapter, Mapping)
                or not isinstance(runtime_state, Mapping)
                or runtime_state.get("anchor_id") != anchor_id
                or runtime_state.get("mode") != "CONDUCTOR"
                or runtime_state.get("role") != "CONDUCTOR"
                or runtime_state.get("executable_runtime_currentness") != "CURRENT"
                or not isinstance(startup.get("mode_boot_binding"), Mapping)
                or startup["mode_boot_binding"].get("binding_id")
                != mode_boot_binding["binding_id"]
                or startup["mode_boot_binding"].get("status") != "ACTIVE"
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
                "runtime_currentness_observation": str(
                    runtime_state["executable_runtime_currentness"]
                ),
                "parent_actor_ref": "universe-conductor",
                "parent_evidence_ref": host_session_ref,
                "binding_evidence_ref": (
                    f"process-local://universe/conductor-runtime/{session_id}"
                ),
            }
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

    def observe(self, message_id: str) -> Mapping[str, Any]:
        normalized_id = _text(message_id, "message_id")
        result = self._invoke(
            (
                "mode-anchor",
                "observe-commander-input",
                "--repo-root",
                str(self.repository_root),
            ),
            {
                "mode": "CONDUCTOR",
                "commander_surface": "UNIVERSE_UI",
                "evidence_ref": (
                    f"universe://conductor-room/messages/{normalized_id}"
                ),
            },
        )
        if result.get("status") != "COMMANDER_INPUT_OBSERVED":
            raise UniverseConductorRuntimeError(
                "UNIVERSE_COMMANDER_SURFACE_OBSERVATION_FAILED"
            )
        return result

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
            "node": "universe",
            "mode": "CONDUCTOR",
            "session_id": str(self._binding["session_id"]),
            "frame_id": str(self._binding["origin_frame_id"]),
            "anchor_id": str(self._binding["origin_anchor_ref"]),
            "currentness": str(
                self._binding["runtime_currentness_observation"]
            ),
            "source_ref": (
                "git-object-database://universe@"
                + self.source_commit_resolver(self.repository_root)
            ),
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
            node="CONDUCTOR", mode="CONDUCTOR"
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

    def _conductor_definition(self) -> Mapping[str, str]:
        registry_path = (
            self.repository_root
            / ".ai"
            / "runtime"
            / "project_instance"
            / "mode_registry.json"
        )
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            definition = registry["modes"]["CONDUCTOR"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise UniverseConductorRuntimeError(
                "CONDUCTOR_MODE_UNAVAILABLE"
            ) from error
        if not isinstance(definition, Mapping):
            raise UniverseConductorRuntimeError("CONDUCTOR_MODE_UNAVAILABLE")
        role = _text(definition.get("role"), "CONDUCTOR.role")
        if role != "CONDUCTOR":
            raise UniverseConductorRuntimeError("CONDUCTOR_MODE_ROLE_MISMATCH")
        return {
            "role": role,
            "scope": _text(definition.get("scope"), "CONDUCTOR.scope"),
            "mode_profile": _text(
                definition.get("mode_profile"), "CONDUCTOR.mode_profile"
            ),
        }

    @staticmethod
    def _prepared_anchor_id(prepared: Mapping[str, Any]) -> str:
        anchor = prepared.get("mode_current_anchor")
        snapshot = anchor.get("snapshot") if isinstance(anchor, Mapping) else None
        payload = snapshot.get("snapshot") if isinstance(snapshot, Mapping) else None
        anchor_id = payload.get("anchor_id") if isinstance(payload, Mapping) else None
        if (
            prepared.get("status") != "SESSION_PREPARED"
            or not isinstance(anchor, Mapping)
            or anchor.get("status")
            not in {"MODE_CURRENT_ANCHOR_CREATED", "MODE_CURRENT_ANCHOR_OBSERVED"}
        ):
            raise UniverseConductorRuntimeError(
                "UNIVERSE_SESSION_PREPARATION_FAILED"
            )
        return _text(anchor_id, "mode_current_anchor.anchor_id")

    @staticmethod
    def _prepared_mode_boot_binding(
        prepared: Mapping[str, Any],
        *,
        anchor_id: str,
    ) -> dict[str, str]:
        binding = prepared.get("mode_boot_binding")
        if not isinstance(binding, Mapping) or binding.get("status") != "PREPARED":
            raise UniverseConductorRuntimeError(
                "UNIVERSE_MODE_BOOT_BINDING_UNAVAILABLE"
            )
        normalized = {
            field: _text(binding.get(field), f"mode_boot_binding.{field}")
            for field in ("binding_id", "mode", "role", "frame_id", "anchor_id")
        }
        if (
            normalized["mode"] != "CONDUCTOR"
            or normalized["role"] != "CONDUCTOR"
            or normalized["anchor_id"] != anchor_id
        ):
            raise UniverseConductorRuntimeError(
                "UNIVERSE_MODE_BOOT_BINDING_MISMATCH"
            )
        return normalized

    def _git_head(self, repository_root: Path) -> str:
        result = self.native_runner(
            NativeCliRequest(
                executable=_required_host_executable("git"),
                arguments=("rev-parse", "HEAD"),
                cwd=repository_root,
                timeout_seconds=15,
            )
        )
        source_commit = result.stdout.strip()
        if (
            result.status != "COMPLETED"
            or result.return_code != 0
            or len(source_commit) != 40
            or any(
                character not in "0123456789abcdefABCDEF"
                for character in source_commit
            )
        ):
            raise UniverseConductorRuntimeError(
                "UNIVERSE_SOURCE_COMMIT_UNAVAILABLE"
            )
        return source_commit.lower()


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
