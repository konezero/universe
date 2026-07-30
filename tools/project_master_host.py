from __future__ import annotations

import argparse
import base64
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
import queue
import secrets
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol
from uuid import UUID, uuid4

from project_master_bridge import (
    ProjectMasterBridgeHost,
    ProjectMasterBridgeError,
    ProjectMasterBridgeHttpServer,
    normalize_bridge_envelope,
    post_master_reply,
    post_master_stream_event,
    utc_now,
)
from project_seed_apply import apply_project_seed_asset_proposal
from project_seed_assets import ProjectSeedAssetError
from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli


PROJECT_MASTER_HOST_SCHEMA = "universe.project-master-live-host.v1"
PROJECT_MASTER_SESSION_SCHEMA = "universe.project-master-session.v1"
SUPPORTED_PROVIDERS = frozenset({"GROK", "CODEX"})


class ProjectMasterHostError(RuntimeError):
    pass


class MasterProvider(Protocol):
    @property
    def session_ref(self) -> str: ...

    def reply(self, message: Mapping[str, Any]) -> str: ...


ReplyPoster = Callable[..., dict[str, Any]]
StreamPoster = Callable[..., dict[str, Any]]
NativeRunner = Callable[[NativeCliRequest], NativeCliResult]
BridgeRegistrar = Callable[[str, Mapping[str, Any]], tuple[dict[str, Any], bool]]
SourceCommitResolver = Callable[[Path], str]


class CommanderSurfaceObserver(Protocol):
    def prepare(self) -> Mapping[str, Any]: ...

    def observe(self, message: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ProjectModeCoordinator:
    """Invoke the installed project Runtime for Mode and surface ownership."""

    def __init__(
        self,
        project_root: Path,
        project_id: str,
        host_session_ref: str,
        *,
        native_runner: NativeRunner = run_native_cli,
        source_commit_resolver: SourceCommitResolver | None = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.host_session_ref = _text(host_session_ref, "host_session_ref")
        self.native_runner = native_runner
        self.source_commit_resolver = source_commit_resolver or self._git_head
        self.runtime_cli = (
            self.project_root
            / ".ai"
            / "runtime"
            / "reference_runtime"
            / "cli.py"
        )
        if not self.runtime_cli.is_file():
            raise ProjectMasterHostError("PROJECT_RUNTIME_CLI_UNAVAILABLE")
        self._prepared: dict[str, Any] | None = None
        self._runtime_process: subprocess.Popen[str] | None = None
        self._runtime_binding: dict[str, str] | None = None
        self._runtime_stderr: deque[str] = deque(maxlen=40)
        self._runtime_lock = threading.RLock()

    def prepare(self) -> Mapping[str, Any]:
        definition = self._master_definition()
        source_commit = self.source_commit_resolver(self.project_root)
        request = {
            "command": "BOOT",
            "source_state": "SOURCE_READY",
            "source_ref": (
                f"git-object-database://{self.project_id}@{source_commit}"
            ),
            "source_commit": source_commit,
            "source_repository": str(self.project_root),
            "mode": "MASTER",
            "role": definition["role"],
            "scope": definition["scope"],
            "host_session_ref": self.host_session_ref,
            "anchor_snapshot_ref": "UNKNOWN",
            "host_executable_capability": "AVAILABLE",
            "mode_profile": definition["mode_profile"],
            "task_requirement": "NONE",
            "evidence_profile": "NONE",
        }
        result = self._invoke(
            ("prepare-session", "--repo-root", str(self.project_root)),
            request,
        )
        anchor = result.get("mode_current_anchor")
        anchor_status = anchor.get("status") if isinstance(anchor, Mapping) else None
        if result.get("status") != "SESSION_PREPARED" or anchor_status not in {
            "MODE_CURRENT_ANCHOR_CREATED",
            "MODE_CURRENT_ANCHOR_OBSERVED",
        }:
            raise ProjectMasterHostError("PROJECT_MASTER_SESSION_PREPARATION_FAILED")
        self._prepared = dict(result)
        return result

    def observe(self, message: Mapping[str, Any]) -> Mapping[str, Any]:
        message_id = _text(message.get("message_id"), "message.message_id")
        result = self._invoke(
            (
                "mode-anchor",
                "observe-commander-input",
                "--repo-root",
                str(self.project_root),
            ),
            {
                "mode": "MASTER",
                "commander_surface": "UNIVERSE_UI",
                "evidence_ref": (
                    f"universe://project-room/messages/{message_id}"
                ),
            },
        )
        if result.get("status") != "COMMANDER_INPUT_OBSERVED":
            raise ProjectMasterHostError("PROJECT_COMMANDER_SURFACE_OBSERVATION_FAILED")
        return result

    def apply_file(
        self,
        *,
        target: Path,
        content: bytes,
        operation: str,
        boundary: str,
        approval_evidence_ref: str,
        request_ref: str,
    ) -> Mapping[str, Any]:
        binding = self._ensure_runtime()
        normalized_operation = _text(operation, "operation").upper()
        normalized_target = target.expanduser().resolve(strict=target.exists())
        target_preimage = (
            {
                "status": "PRESENT",
                "sha256": hashlib.sha256(normalized_target.read_bytes()).hexdigest(),
            }
            if normalized_target.exists()
            else {"status": "ABSENT", "sha256": "NONE"}
        )
        proposal = self._invoke(
            (
                "execution-binding",
                "propose",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "request": {
                    "operation": normalized_operation,
                    "target": str(normalized_target),
                    "boundary": boundary,
                    "write_roots": [str(self.project_root / ".ai" / "universe")],
                    "write_operations": ["CREATE", "MODIFY"],
                    "task_summary": "Apply one approved Universe Project Seed asset",
                    "request_ref": request_ref,
                },
            },
        )
        if proposal.get("status") != "EXECUTION_ASSIGNMENT_PROPOSED":
            raise ProjectMasterHostError("PROJECT_SEED_ASSIGNMENT_PROPOSAL_FAILED")
        approval = {
            "status": "APPROVED",
            "proposal_id": proposal["proposal_id"],
            "commander_surface": "UNIVERSE_UI",
            "operation": normalized_operation,
            "target": str(normalized_target),
            "boundary": boundary,
            "evidence_ref": approval_evidence_ref,
            "authority_source_ref": approval_evidence_ref,
        }
        applied_binding = self._invoke(
            (
                "execution-binding",
                "apply",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "proposal": proposal,
                "approval": approval,
            },
        )
        if applied_binding.get("status") != "EXECUTION_BINDING_APPLIED":
            raise ProjectMasterHostError("PROJECT_SEED_EXECUTION_BINDING_FAILED")
        guard_request = {
            "session_id": binding["session_id"],
            "frame_id": binding["frame_id"],
            "anchor_id": binding["anchor_id"],
            "operation": normalized_operation,
            "target": str(normalized_target),
            "boundary": boundary,
            "source_commit": proposal["source_commit"],
            "validation_ref": proposal["validation_ref"],
            "payload_sha256": hashlib.sha256(content).hexdigest(),
            "target_preimage": target_preimage,
            "host_capability": {
                "filesystem_write": "AVAILABLE",
                "pre_write_hook": "AVAILABLE",
                "evidence_ref": (
                    "project-master://"
                    + self.project_id
                    + "/receipt-aware-mutation-gateway"
                ),
            },
            "approval": approval,
        }
        permit = self._invoke(
            (
                "execution-guard",
                "check",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "observed_at": utc_now(),
                "request": guard_request,
            },
        )
        if permit.get("status") != "EXECUTION_GUARD_PERMITTED":
            return permit
        result = self._invoke(
            (
                "mutation-gateway",
                "apply-file",
                "--endpoint",
                binding["endpoint"],
                "--token",
                binding["token"],
            ),
            {
                "session_id": binding["session_id"],
                "observed_at": utc_now(),
                "request": guard_request,
                "receipt_id": permit["permit_receipt"]["receipt_id"],
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
        )
        return result

    def close(self) -> None:
        with self._runtime_lock:
            process = self._runtime_process
            self._runtime_process = None
            self._runtime_binding = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _ensure_runtime(self) -> dict[str, str]:
        with self._runtime_lock:
            if (
                self._runtime_binding is not None
                and self._runtime_process is not None
                and self._runtime_process.poll() is None
            ):
                return dict(self._runtime_binding)
            prepared = self._prepared or dict(self.prepare())
            anchor = prepared.get("mode_current_anchor")
            snapshot = anchor.get("snapshot") if isinstance(anchor, Mapping) else None
            payload = snapshot.get("snapshot") if isinstance(snapshot, Mapping) else None
            anchor_id = (
                _text(payload.get("anchor_id"), "mode_current_anchor.anchor_id")
                if isinstance(payload, Mapping)
                else ""
            )
            if not anchor_id:
                raise ProjectMasterHostError("PROJECT_MASTER_ANCHOR_UNAVAILABLE")
            session_id = f"project-master-{uuid4().hex}"
            frame_id = "master"
            token = secrets.token_urlsafe(32)
            command = [
                sys.executable,
                str(self.runtime_cli),
                "session-boot",
                "serve",
                "--repo-root",
                str(self.project_root),
                "--session-id",
                session_id,
                "--frame-id",
                frame_id,
                "--anchor-id",
                anchor_id,
                "--host-action",
                "PROJECT_MASTER_SEED_APPLY",
                "--session-location",
                "PROJECT_MASTER_HOST",
                "--commander-surface",
                "UNIVERSE_UI",
                "--execution-surface",
                "LOCAL_RUNTIME",
                "--repository-location",
                str(self.project_root),
                "--port",
                "0",
                "--token",
                token,
            ]
            options: dict[str, Any] = {
                "cwd": str(self.project_root),
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
                process = subprocess.Popen(command, **options)  # nosec B603
                startup = self._read_runtime_startup(process)
                host_adapter = startup.get("host_adapter")
                runtime_state = startup.get("runtime_state")
                if (
                    startup.get("status") != "SESSION_BOOT_IMAGE_CREATED"
                    or not isinstance(host_adapter, Mapping)
                    or not isinstance(runtime_state, Mapping)
                    or runtime_state.get("anchor_id") != anchor_id
                    or runtime_state.get("executable_runtime_currentness")
                    != "CURRENT"
                ):
                    raise ProjectMasterHostError(
                        "PROJECT_MASTER_RUNTIME_START_RESULT_INVALID"
                    )
                endpoint = _text(
                    host_adapter.get("endpoint"), "host_adapter.endpoint"
                )
                if _text(host_adapter.get("token"), "host_adapter.token") != token:
                    raise ProjectMasterHostError(
                        "PROJECT_MASTER_RUNTIME_TOKEN_MISMATCH"
                    )
            except Exception:
                if "process" in locals() and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                raise
            self._runtime_process = process
            self._runtime_binding = {
                "endpoint": endpoint,
                "token": token,
                "session_id": session_id,
                "frame_id": frame_id,
                "anchor_id": anchor_id,
            }
            return dict(self._runtime_binding)

    def _read_runtime_startup(
        self, process: subprocess.Popen[str]
    ) -> Mapping[str, Any]:
        if process.stdout is None:
            raise ProjectMasterHostError("PROJECT_MASTER_RUNTIME_STDOUT_UNAVAILABLE")
        output: queue.Queue[str] = queue.Queue(maxsize=1)

        def read_line() -> None:
            try:
                output.put(process.stdout.readline())
            except (OSError, UnicodeError):
                output.put("")

        threading.Thread(target=read_line, daemon=True).start()
        if process.stderr is not None:
            threading.Thread(
                target=self._drain_runtime_stderr,
                args=(process.stderr,),
                daemon=True,
            ).start()
        try:
            raw = output.get(timeout=30)
        except queue.Empty as error:
            raise ProjectMasterHostError(
                "PROJECT_MASTER_RUNTIME_START_TIMEOUT"
            ) from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError(
                "PROJECT_MASTER_RUNTIME_START_RESULT_INVALID"
            ) from error
        if not isinstance(payload, Mapping):
            raise ProjectMasterHostError(
                "PROJECT_MASTER_RUNTIME_START_RESULT_INVALID"
            )
        return payload

    def _drain_runtime_stderr(self, stream: Any) -> None:
        try:
            for line in stream:
                self._runtime_stderr.append(line.rstrip())
        except (OSError, UnicodeError):
            return

    def _master_definition(self) -> Mapping[str, str]:
        registry_path = (
            self.project_root
            / ".ai"
            / "runtime"
            / "project_instance"
            / "mode_registry.json"
        )
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            definition = registry["modes"]["MASTER"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ProjectMasterHostError("PROJECT_MASTER_MODE_UNAVAILABLE") from error
        if not isinstance(definition, Mapping):
            raise ProjectMasterHostError("PROJECT_MASTER_MODE_UNAVAILABLE")
        return {
            "role": _text(definition.get("role"), "MASTER.role"),
            "scope": _text(definition.get("scope"), "MASTER.scope"),
            "mode_profile": _text(
                definition.get("mode_profile"), "MASTER.mode_profile"
            ),
        }

    def _invoke(
        self,
        arguments: tuple[str, ...],
        request: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        request_path = _runtime_tmp() / f"project-runtime-{uuid4().hex}.json"
        request_path.write_text(
            json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=Path(sys.executable),
                    arguments=(
                        str(self.runtime_cli),
                        *arguments,
                        "--request",
                        str(request_path),
                    ),
                    cwd=self.project_root,
                    timeout_seconds=30,
                )
            )
        finally:
            request_path.unlink(missing_ok=True)
        if result.status != "COMPLETED" or result.return_code != 0:
            raise ProjectMasterHostError("PROJECT_RUNTIME_COMMAND_FAILED")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError("PROJECT_RUNTIME_RESULT_INVALID") from error
        if not isinstance(payload, Mapping):
            raise ProjectMasterHostError("PROJECT_RUNTIME_RESULT_INVALID")
        return payload

    def _git_head(self, project_root: Path) -> str:
        executable = shutil.which("git")
        if executable is None:
            raise ProjectMasterHostError("PROJECT_GIT_UNAVAILABLE")
        result = self.native_runner(
            NativeCliRequest(
                executable=Path(executable),
                arguments=("rev-parse", "HEAD"),
                cwd=project_root,
                timeout_seconds=15,
            )
        )
        source_commit = result.stdout.strip()
        if (
            result.status != "COMPLETED"
            or result.return_code != 0
            or len(source_commit) != 40
            or any(character not in "0123456789abcdefABCDEF" for character in source_commit)
        ):
            raise ProjectMasterHostError("PROJECT_SOURCE_COMMIT_UNAVAILABLE")
        return source_commit.lower()


class ProjectMasterSessionStore:
    def __init__(self, database_path: Path, project_id: str) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.project_id = _text(project_id, "project_id")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def provider_session_id(
        self,
        provider: str = "GROK",
        *,
        create: bool = True,
    ) -> str | None:
        normalized_provider = _provider(provider)
        key = f"provider_session_id:{normalized_provider}"
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM host_metadata WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None and normalized_provider == "GROK":
                row = connection.execute(
                    "SELECT value FROM host_metadata WHERE key = 'provider_session_id'"
                ).fetchone()
            if row is not None:
                return str(row["value"])
            if not create:
                return None
            session_id = str(uuid4())
            connection.execute(
                "INSERT INTO host_metadata(key, value) VALUES(?, ?)",
                (key, session_id),
            )
            return session_id

    def set_provider_session_id(self, provider: str, session_id: str) -> None:
        normalized_provider = _provider(provider)
        normalized_session = _text(session_id, "session_id")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO host_metadata(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (
                    f"provider_session_id:{normalized_provider}",
                    normalized_session,
                ),
            )

    def provider_session_initialized(self, provider: str = "GROK") -> bool:
        normalized_provider = _provider(provider)
        key = f"provider_session_initialized:{normalized_provider}"
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM host_metadata WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None and normalized_provider == "GROK":
                row = connection.execute(
                    "SELECT value FROM host_metadata "
                    "WHERE key = 'provider_session_initialized'"
                ).fetchone()
        return row is not None and str(row["value"]) == "true"

    def mark_provider_session_initialized(self, provider: str = "GROK") -> None:
        normalized_provider = _provider(provider)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO host_metadata(key, value)
                VALUES(?, 'true')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (f"provider_session_initialized:{normalized_provider}",),
            )

    def register(self, envelope: Mapping[str, Any]) -> bool:
        normalized = normalize_bridge_envelope(envelope)
        message_id = normalized["message"]["message_id"]
        envelope_json = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state FROM inbox_message WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row is not None and row["state"] in {
                "PENDING",
                "PROCESSING",
                "COMPLETE",
            }:
                return False
            connection.execute(
                """
                INSERT INTO inbox_message(
                    message_id, envelope_json, state, attempts, last_error, updated_at
                ) VALUES(?, ?, 'PENDING', 0, '', ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    envelope_json = excluded.envelope_json,
                    state = 'PENDING',
                    last_error = '',
                    updated_at = excluded.updated_at
                """,
                (message_id, envelope_json, utc_now()),
            )
        return True

    def recover(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE inbox_message
                SET state = 'PENDING', updated_at = ?
                WHERE state = 'PROCESSING'
                """,
                (utc_now(),),
            )
            rows = connection.execute(
                """
                SELECT envelope_json
                FROM inbox_message
                WHERE state IN ('PENDING', 'FAILED')
                ORDER BY updated_at, message_id
                """
            ).fetchall()
        return [json.loads(str(row["envelope_json"])) for row in rows]

    def claim(self, message_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE inbox_message
                SET state = 'PROCESSING', attempts = attempts + 1,
                    last_error = '', updated_at = ?
                WHERE message_id = ? AND state IN ('PENDING', 'FAILED')
                """,
                (utc_now(), message_id),
            )
        return cursor.rowcount == 1

    def complete(self, message_id: str) -> None:
        self._transition(message_id, "COMPLETE", "")

    def fail(self, message_id: str, error: str) -> None:
        self._transition(message_id, "FAILED", error[:1000])

    def state(self, message_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state FROM inbox_message WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return str(row["state"]) if row is not None else "UNKNOWN"

    def _transition(self, message_id: str, state: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE inbox_message
                SET state = ?, last_error = ?, updated_at = ?
                WHERE message_id = ?
                """,
                (state, error, utc_now(), message_id),
            )

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS host_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inbox_message (
                    message_id TEXT PRIMARY KEY,
                    envelope_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


class GrokProjectMasterRuntime:
    def __init__(
        self,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
        *,
        native_runner: NativeRunner = run_native_cli,
        model: str = "",
        max_turns: int = 8,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.store = store
        self.native_runner = native_runner
        self.model = model.strip()
        self.max_turns = max(1, int(max_turns))
        self.session_id = store.provider_session_id("GROK")
        if self.session_id is None:
            raise ProjectMasterHostError("GROK_SESSION_ID_UNAVAILABLE")
        UUID(self.session_id)

    @property
    def session_ref(self) -> str:
        return f"grok-cli:{self.session_id}"

    def reply(self, message: Mapping[str, Any]) -> str:
        executable, environment = _resolve_grok()
        if executable is None:
            raise ProjectMasterHostError("GROK_CLI_UNAVAILABLE")
        prompt_path = _runtime_tmp() / f"project-master-{uuid4().hex}.txt"
        prompt_path.write_text(self._prompt(message), encoding="utf-8")
        initialized = self.store.provider_session_initialized("GROK")
        arguments = [
            "--no-auto-update",
            "--no-subagents",
            "--no-memory",
            "--disable-web-search",
            "--permission-mode",
            "plan",
            "--sandbox",
            "read-only",
            "--max-turns",
            str(self.max_turns),
            "--cwd",
            str(self.project_root),
            "--system-prompt-override",
            self._system_prompt(),
        ]
        if self.model:
            arguments.extend(["--model", self.model])
        if initialized:
            arguments.extend(["--resume", self.session_id])
        else:
            arguments.extend(["--session-id", self.session_id])
        arguments.extend(
            [
                "--prompt-file",
                str(prompt_path),
                "--output-format",
                "json",
            ]
        )
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=executable,
                    arguments=tuple(arguments),
                    cwd=self.project_root,
                    timeout_seconds=300,
                    environment=environment,
                )
            )
        finally:
            prompt_path.unlink(missing_ok=True)
        if result.status != "COMPLETED":
            raise ProjectMasterHostError(f"GROK_CLI_{result.status}")
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ProjectMasterHostError("GROK_RESULT_JSON_INVALID") from error
        if not isinstance(response, Mapping):
            raise ProjectMasterHostError("GROK_RESULT_OBJECT_REQUIRED")
        response_session = _text(response.get("sessionId"), "response.sessionId")
        if response_session != self.session_id:
            raise ProjectMasterHostError("GROK_SESSION_ID_MISMATCH")
        text = _text(response.get("text"), "response.text")
        if response.get("stopReason") != "EndTurn":
            raise ProjectMasterHostError("GROK_TURN_INCOMPLETE")
        self.store.mark_provider_session_initialized("GROK")
        return text

    def reply_stream(
        self,
        message: Mapping[str, Any],
        on_delta: Callable[[str], None],
    ) -> str:
        executable, environment = _resolve_grok()
        if executable is None:
            raise ProjectMasterHostError("GROK_CLI_UNAVAILABLE")
        prompt_path = _runtime_tmp() / f"project-master-{uuid4().hex}.txt"
        prompt_path.write_text(self._prompt(message), encoding="utf-8")
        initialized = self.store.provider_session_initialized("GROK")
        arguments = [
            "--no-auto-update",
            "--no-subagents",
            "--no-memory",
            "--disable-web-search",
            "--permission-mode",
            "plan",
            "--sandbox",
            "read-only",
            "--max-turns",
            str(self.max_turns),
            "--cwd",
            str(self.project_root),
            "--system-prompt-override",
            self._system_prompt(),
        ]
        if self.model:
            arguments.extend(["--model", self.model])
        if initialized:
            arguments.extend(["--resume", self.session_id])
        else:
            arguments.extend(["--session-id", self.session_id])
        arguments.extend(
            [
                "--prompt-file",
                str(prompt_path),
                "--output-format",
                "streaming-json",
            ]
        )
        try:
            result = _run_streaming_json(
                executable=executable,
                arguments=arguments,
                cwd=self.project_root,
                environment=environment,
                timeout_seconds=300,
                on_delta=on_delta,
            )
        finally:
            prompt_path.unlink(missing_ok=True)
        if result.session_id != self.session_id:
            raise ProjectMasterHostError("GROK_SESSION_ID_MISMATCH")
        if result.stop_reason != "EndTurn":
            raise ProjectMasterHostError("GROK_TURN_INCOMPLETE")
        self.store.mark_provider_session_initialized("GROK")
        return result.text

    def _system_prompt(self) -> str:
        return (
            f"You are the Project Master for {self.project_id}. "
            "This is a persistent, read-only conversation Host connected to the "
            "Universe Conductor. Work from the repository at the configured cwd. "
            "At the start of the session, follow the repository entry order and "
            "prepare MASTER Mode from source-backed evidence. Never claim that "
            "Mode, Role, BOOT, or a chat message grants mutation authority. "
            "You may inspect source, review, explain, audit, and propose bounded "
            "work. Do not create, edit, delete, move, commit, push, execute project "
            "code, start subagents, or invoke network tools. For implementation "
            "requests, return a concise proposal suitable for a separate guarded "
            "execution queue. Each Project Room message includes a Host-observed "
            "Project Runtime context. Use that context for current Mode Anchor and "
            "Commander Surface answers; static state files may be older. Mark "
            "unavailable facts UNKNOWN. Reply in the user's language and keep "
            "ordinary conversation direct."
        )

    def _prompt(self, message: Mapping[str, Any]) -> str:
        runtime_context = message.get("runtime_context")
        context_text = (
            json.dumps(
                dict(runtime_context),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if isinstance(runtime_context, Mapping)
            else "{}"
        )
        return (
            "Universe Project Room message\n"
            f"message_id: {_text(message.get('message_id'), 'message.message_id')}\n"
            f"kind: {_text(message.get('kind'), 'message.kind')}\n"
            f"sender: {_text(message.get('sender'), 'message.sender')}\n"
            f"project_runtime_context: {context_text}\n\n"
            f"{_text(message.get('body'), 'message.body')}"
        )


class CodexProjectMasterRuntime:
    def __init__(
        self,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
        *,
        native_runner: NativeRunner = run_native_cli,
    ) -> None:
        self.project_root = project_root.expanduser().resolve(strict=True)
        self.project_id = _text(project_id, "project_id")
        self.store = store
        self.native_runner = native_runner
        self.session_id = store.provider_session_id("CODEX", create=False)

    @property
    def session_ref(self) -> str:
        return (
            f"codex-cli:{self.session_id}"
            if self.session_id
            else f"codex-cli:pending:{self.project_id}"
        )

    def reply(self, message: Mapping[str, Any]) -> str:
        executable, environment = _resolve_codex()
        if executable is None:
            raise ProjectMasterHostError("CODEX_CLI_UNAVAILABLE")
        prompt = f"{self._system_prompt()}\n\n{self._prompt(message)}"
        if self.session_id:
            arguments = (
                "exec",
                "resume",
                "--json",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "-C",
                str(self.project_root),
                self.session_id,
                prompt,
            )
        else:
            arguments = (
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "-C",
                str(self.project_root),
                prompt,
            )
        result = self.native_runner(
            NativeCliRequest(
                executable=executable,
                arguments=arguments,
                cwd=self.project_root,
                timeout_seconds=300,
                environment=environment,
            )
        )
        if result.status != "COMPLETED" or result.return_code != 0:
            raise ProjectMasterHostError(f"CODEX_CLI_{result.status}")
        messages: list[str] = []
        observed_session = self.session_id
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, Mapping):
                continue
            if event.get("type") in {"thread.started", "session.started"}:
                candidate = event.get("thread_id") or event.get("session_id")
                if isinstance(candidate, str) and candidate.strip():
                    observed_session = candidate.strip()
            item = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, Mapping)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
                and item["text"].strip()
            ):
                messages.append(item["text"].strip())
            elif (
                event.get("type") == "agent_message"
                and isinstance(event.get("text"), str)
                and event["text"].strip()
            ):
                messages.append(event["text"].strip())
        if not messages:
            raise ProjectMasterHostError("CODEX_RESULT_MESSAGE_MISSING")
        if observed_session:
            self.session_id = observed_session
            self.store.set_provider_session_id("CODEX", observed_session)
            self.store.mark_provider_session_initialized("CODEX")
        return "\n".join(messages)

    def _system_prompt(self) -> str:
        return (
            f"You are the Project Master for {self.project_id}. "
            "This is a persistent, read-only conversation Host connected to the "
            "Universe Conductor. Follow the repository entry order and prepare "
            "MASTER Mode from source-backed evidence. Do not create, edit, delete, "
            "move, commit, push, execute project code, invoke subagents, or use "
            "network tools. Mode and Role do not create mutation authority. "
            "Inspect, review, explain, audit, and propose bounded work only. "
            "Reply in the user's language."
        )

    @staticmethod
    def _prompt(message: Mapping[str, Any]) -> str:
        runtime_context = message.get("runtime_context")
        context_text = (
            json.dumps(
                dict(runtime_context),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if isinstance(runtime_context, Mapping)
            else "{}"
        )
        return (
            "Universe Project Room message\n"
            f"message_id: {_text(message.get('message_id'), 'message.message_id')}\n"
            f"kind: {_text(message.get('kind'), 'message.kind')}\n"
            f"sender: {_text(message.get('sender'), 'message.sender')}\n"
            f"project_runtime_context: {context_text}\n\n"
            f"{_text(message.get('body'), 'message.body')}"
        )


class ProjectMasterConversationWorker:
    def __init__(
        self,
        *,
        provider: MasterProvider,
        store: ProjectMasterSessionStore,
        universe_endpoint: str,
        project_id: str,
        bridge_token: str,
        surface_observer: CommanderSurfaceObserver,
        reply_poster: ReplyPoster = post_master_reply,
        stream_poster: StreamPoster = post_master_stream_event,
    ) -> None:
        self.provider = provider
        self.store = store
        self.universe_endpoint = universe_endpoint
        self.project_id = _text(project_id, "project_id")
        self.bridge_token = _text(bridge_token, "bridge_token")
        self.surface_observer = surface_observer
        self.reply_poster = reply_poster
        self.stream_poster = stream_poster
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name=f"project-master-{self.project_id}",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        for envelope in self.store.recover():
            self._queue.put(envelope)

    def submit(self, envelope: Mapping[str, Any]) -> bool:
        normalized = normalize_bridge_envelope(envelope)
        if not self.store.register(normalized):
            return False
        self._queue.put(normalized)
        return True

    def wait_idle(self, timeout_seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def close(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join(timeout=10)

    def _run(self) -> None:
        while True:
            envelope = self._queue.get()
            try:
                if envelope is None:
                    return
                self._process(envelope)
            finally:
                self._queue.task_done()

    def _process(self, envelope: Mapping[str, Any]) -> None:
        message = envelope["message"]
        message_id = _text(message["message_id"], "message_id")
        if not self.store.claim(message_id):
            return
        bridge_id = _text(envelope["bridge_id"], "bridge_id")
        sequence = 0

        def emit(event: str, *, delta: str = "", detail: str = "") -> None:
            nonlocal sequence
            sequence += 1
            try:
                self.stream_poster(
                    universe_endpoint=self.universe_endpoint,
                    project_id=self.project_id,
                    bridge_id=bridge_id,
                    in_reply_to=message_id,
                    event=event,
                    sequence=sequence,
                    delta=delta,
                    detail=detail,
                    bridge_token=self.bridge_token,
                    timeout_seconds=5.0,
                )
            except Exception:
                # Streaming is process-local UX evidence. Durable final delivery
                # remains authoritative when a client disconnects.
                pass

        try:
            emit("STARTED")
            surface_observation = self.surface_observer.observe(message)
            provider_message = dict(message)
            provider_message["runtime_context"] = _runtime_context(
                surface_observation
            )
            stream_reply = getattr(self.provider, "reply_stream", None)
            if callable(stream_reply):
                body = stream_reply(
                    provider_message,
                    lambda delta: emit("DELTA", delta=delta),
                )
            else:
                body = self.provider.reply(provider_message)
            self.reply_poster(
                universe_endpoint=self.universe_endpoint,
                project_id=self.project_id,
                bridge_id=bridge_id,
                in_reply_to=message_id,
                kind="RESULT",
                body=body,
                idempotency_key=f"project-master-live-{message_id}",
                bridge_token=self.bridge_token,
                timeout_seconds=10.0,
            )
        except Exception as error:
            emit("FAILED", detail=f"{type(error).__name__}: {error}")
            self.store.fail(message_id, f"{type(error).__name__}: {error}")
            return
        emit("COMPLETED")
        self.store.complete(message_id)


class LiveProjectMasterBridgeHost(ProjectMasterBridgeHost):
    _worker: ProjectMasterConversationWorker
    _coordinator: CommanderSurfaceObserver

    def __init__(
        self,
        project_root: Path,
        token: str,
        inbox_ref: str,
        worker: ProjectMasterConversationWorker,
        coordinator: CommanderSurfaceObserver,
    ) -> None:
        super().__init__(project_root, token, inbox_ref)
        object.__setattr__(self, "_worker", worker)
        object.__setattr__(self, "_coordinator", coordinator)

    def record(self, envelope: Any) -> dict[str, Any]:
        normalized = normalize_bridge_envelope(envelope)
        receipt = super().record(normalized)
        self._worker.submit(normalized)
        return receipt

    def apply_seed_assets(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, Mapping) or set(request) != {
            "project_id",
            "proposal",
            "approval",
        }:
            raise ProjectMasterBridgeError(
                "PROJECT_SEED_ASSET_APPLY_REQUEST_INVALID"
            )
        gateway = self._coordinator
        if not callable(getattr(gateway, "apply_file", None)):
            raise ProjectMasterBridgeError(
                "PROJECT_SEED_ASSET_MUTATION_GATEWAY_UNAVAILABLE"
            )
        try:
            return apply_project_seed_asset_proposal(
                project_root=self.project_root,
                project_id=_text(request.get("project_id"), "project_id"),
                proposal=request.get("proposal"),
                approval=request.get("approval"),
                mutation_gateway=gateway,
            )
        except (ProjectSeedAssetError, ProjectMasterHostError) as error:
            raise ProjectMasterBridgeError(str(error)) from error


@dataclass
class ResidentProjectMasterHandle:
    project_id: str
    provider: str
    endpoint: str
    credential_env: str
    bridge_server: ProjectMasterBridgeHttpServer
    worker: ProjectMasterConversationWorker
    coordinator: CommanderSurfaceObserver
    thread: threading.Thread

    def close(self) -> None:
        self.bridge_server.shutdown()
        self.bridge_server.server_close()
        self.worker.close()
        close_coordinator = getattr(self.coordinator, "close", None)
        if callable(close_coordinator):
            close_coordinator()
        self.thread.join(timeout=5)
        os.environ.pop(self.credential_env, None)


class ResidentProjectMasterHostManager:
    def __init__(
        self,
        *,
        universe_endpoint: str,
        bridge_registrar: BridgeRegistrar,
        provider_factory: Callable[
            [Path, str, ProjectMasterSessionStore], MasterProvider
        ]
        | None = None,
        provider_resolver: Callable[[str], str] | None = None,
        coordinator_factory: Callable[
            [Path, str, str], CommanderSurfaceObserver
        ]
        | None = None,
    ) -> None:
        self.universe_endpoint = universe_endpoint.rstrip("/")
        self.bridge_registrar = bridge_registrar
        self.provider_factory = provider_factory
        self.provider_resolver = provider_resolver or (lambda _project_id: "GROK")
        self.coordinator_factory = coordinator_factory or self._default_coordinator
        self._handles: dict[str, ResidentProjectMasterHandle] = {}
        self._lock = threading.RLock()

    def ensure(self, project: Mapping[str, Any]) -> dict[str, Any]:
        project_id = _text(project.get("project_id"), "project.project_id")
        project_root = (
            Path(_text(project.get("project_root"), "project.project_root"))
            .expanduser()
            .resolve(strict=True)
        )
        selected_provider = _provider(self.provider_resolver(project_id))
        with self._lock:
            handle = self._handles.get(project_id)
            if (
                handle is not None
                and handle.thread.is_alive()
                and handle.provider == selected_provider
            ):
                return {
                    "status": "RESIDENT",
                    "project_id": project_id,
                    "provider": selected_provider,
                    "endpoint": handle.endpoint,
                }
            if handle is not None:
                handle.close()
                self._handles.pop(project_id, None)

            inbox_ref = _resolve_master_inbox(project_root)
            credential_env = _managed_credential_env(project_id)
            os.environ[credential_env] = secrets.token_urlsafe(32)
            store = ProjectMasterSessionStore(
                _default_state_db(project_id),
                project_id,
            )
            provider = (
                self.provider_factory(project_root, project_id, store)
                if self.provider_factory is not None
                else self._default_provider(
                    selected_provider,
                    project_root,
                    project_id,
                    store,
                )
            )
            try:
                coordinator = self.coordinator_factory(
                    project_root,
                    project_id,
                    provider.session_ref,
                )
                preparation = coordinator.prepare()
            except Exception:
                os.environ.pop(credential_env, None)
                raise
            worker = ProjectMasterConversationWorker(
                provider=provider,
                store=store,
                universe_endpoint=self.universe_endpoint,
                project_id=project_id,
                bridge_token=os.environ[credential_env],
                surface_observer=coordinator,
            )
            host = LiveProjectMasterBridgeHost(
                project_root,
                os.environ[credential_env],
                inbox_ref,
                worker,
                coordinator,
            )
            server = ProjectMasterBridgeHttpServer(("127.0.0.1", 0), host)
            endpoint = f"http://127.0.0.1:{server.server_port}"
            thread = threading.Thread(
                target=server.serve_forever,
                name=f"resident-project-master-{project_id}",
                daemon=True,
            )
            worker.start()
            thread.start()
            handle = ResidentProjectMasterHandle(
                project_id=project_id,
                provider=selected_provider,
                endpoint=endpoint,
                credential_env=credential_env,
                bridge_server=server,
                worker=worker,
                coordinator=coordinator,
                thread=thread,
            )
            self._handles[project_id] = handle
            try:
                bridge, _ = self.bridge_registrar(
                    project_id,
                    {
                        "endpoint": endpoint,
                        "credential_env": credential_env,
                        "master_session_ref": provider.session_ref,
                        "binding_evidence_ref": (
                            f"universe://resident-project-master/{project_id}/"
                            f"{provider.session_ref}"
                        ),
                    },
                )
            except Exception:
                self._handles.pop(project_id, None)
                handle.close()
                raise
            return {
                "status": "STARTED",
                "project_id": project_id,
                "provider": selected_provider,
                "endpoint": endpoint,
                "bridge": bridge,
                "session_preparation": preparation,
            }

    def is_resident(self, project_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(_text(project_id, "project_id"))
            return handle is not None and handle.thread.is_alive()

    def invalidate(self, project_id: str) -> None:
        normalized = _text(project_id, "project_id")
        with self._lock:
            handle = self._handles.pop(normalized, None)
        if handle is not None:
            handle.close()

    def close(self) -> None:
        with self._lock:
            handles = list(self._handles.values())
            self._handles.clear()
        for handle in handles:
            handle.close()

    @staticmethod
    def _default_provider(
        provider: str,
        project_root: Path,
        project_id: str,
        store: ProjectMasterSessionStore,
    ) -> MasterProvider:
        if provider == "GROK":
            return GrokProjectMasterRuntime(project_root, project_id, store)
        if provider == "CODEX":
            return CodexProjectMasterRuntime(project_root, project_id, store)
        raise ProjectMasterHostError("PROJECT_MASTER_PROVIDER_UNSUPPORTED")

    @staticmethod
    def _default_coordinator(
        project_root: Path,
        project_id: str,
        host_session_ref: str,
    ) -> CommanderSurfaceObserver:
        return ProjectModeCoordinator(
            project_root,
            project_id,
            host_session_ref,
        )


@dataclass(frozen=True)
class StreamingTurnResult:
    text: str
    session_id: str
    stop_reason: str


def _run_streaming_json(
    *,
    executable: Path,
    arguments: list[str],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    on_delta: Callable[[str], None],
) -> StreamingTurnResult:
    process_environment = os.environ.copy()
    process_environment.update(
        {str(key): str(value) for key, value in environment.items()}
    )
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(  # nosec B603
        [str(executable), *arguments],
        cwd=str(cwd),
        env=process_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation_flags,
    )
    lines: queue.Queue[str | None] = queue.Queue()
    stderr_parts: list[str] = []

    def read_stdout() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    def read_stderr() -> None:
        assert process.stderr is not None
        for part in process.stderr:
            if sum(len(item) for item in stderr_parts) < 20000:
                stderr_parts.append(part)

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    text_parts: list[str] = []
    session_id = ""
    stop_reason = ""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                raise ProjectMasterHostError("GROK_CLI_TIMED_OUT")
            try:
                line = lines.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if process.poll() is not None and not stdout_thread.is_alive():
                    break
                continue
            if line is None:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ProjectMasterHostError("GROK_STREAM_JSON_INVALID") from error
            if not isinstance(event, Mapping):
                raise ProjectMasterHostError("GROK_STREAM_EVENT_INVALID")
            event_type = event.get("type")
            if event_type == "text":
                delta = str(event.get("data") or "")
                if delta:
                    text_parts.append(delta)
                    on_delta(delta)
            elif event_type == "end":
                session_id = _text(event.get("sessionId"), "event.sessionId")
                stop_reason = _text(event.get("stopReason"), "event.stopReason")
        return_code = process.wait(timeout=max(1.0, deadline - time.monotonic()))
        if return_code != 0:
            detail = "".join(stderr_parts).strip()
            raise ProjectMasterHostError(
                f"GROK_CLI_FAILED{': ' + detail[:500] if detail else ''}"
            )
    finally:
        if process.poll() is None:
            process.kill()
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
    if not session_id:
        raise ProjectMasterHostError("GROK_STREAM_END_MISSING")
    return StreamingTurnResult(
        text="".join(text_parts),
        session_id=session_id,
        stop_reason=stop_reason,
    )


def _resolve_grok() -> tuple[Path | None, dict[str, str]]:
    grok_home = Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok")).resolve()
    executable = grok_home / "bin" / "grok.exe"
    return (executable if executable.is_file() else None), {"GROK_HOME": str(grok_home)}


def _resolve_codex() -> tuple[Path | None, dict[str, str]]:
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return (candidate if candidate.is_file() else None), {}
    resolved = shutil.which("codex.exe") or shutil.which("codex")
    if not resolved:
        return None, {}
    candidate = Path(resolved).resolve()
    return (candidate if candidate.suffix.lower() == ".exe" else None), {}


def _runtime_tmp() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    root = Path(base) / "Universe" / "runtime-tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _default_state_db(project_id: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "Universe" / "project-master-host" / f"{project_id}.sqlite"


def _resolve_master_inbox(project_root: Path) -> str:
    for relative in (".ai/inbox/MASTER", ".ai/master/inbox"):
        candidate = project_root / relative
        if candidate.is_dir() and not candidate.is_symlink():
            return relative
    raise ProjectMasterHostError("MASTER_INBOX_UNAVAILABLE")


def _managed_credential_env(project_id: str) -> str:
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16].upper()
    return f"UNIVERSE_MANAGED_MASTER_{digest}_TOKEN"


def _read_token(environment_name: str) -> str:
    token = os.environ.get(_text(environment_name, "token_env"))
    if not token:
        raise ProjectMasterHostError("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE")
    return token


def _runtime_context(observation: Mapping[str, Any]) -> dict[str, str]:
    stored = observation.get("snapshot")
    snapshot = (
        stored.get("snapshot")
        if isinstance(stored, Mapping)
        and isinstance(stored.get("snapshot"), Mapping)
        else {}
    )
    coordinates = (
        snapshot.get("coordinates")
        if isinstance(snapshot.get("coordinates"), Mapping)
        else {}
    )
    return {
        "surface_observation_status": str(
            observation.get("status", "UNKNOWN")
        ),
        "mode": str(
            observation.get("anchor_mode", coordinates.get("mode", "UNKNOWN"))
        ),
        "mode_current_anchor": str(
            stored.get("anchor_id", "UNKNOWN")
            if isinstance(stored, Mapping)
            else "UNKNOWN"
        ),
        "commander_surface": str(
            coordinates.get("commander_surface", "UNKNOWN")
        ),
        "observed_at": str(
            stored.get("observed_at", "UNKNOWN")
            if isinstance(stored, Mapping)
            else "UNKNOWN"
        ),
    }


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectMasterHostError(f"{field} must be non-empty text")
    return value.strip()


def _provider(value: Any) -> str:
    normalized = _text(value, "provider").upper()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ProjectMasterHostError("PROJECT_MASTER_PROVIDER_UNSUPPORTED")
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Live local Project Master Host")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--universe-endpoint", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--inbox-ref", default=".ai/inbox/MASTER")
    parser.add_argument(
        "--provider", default="GROK", choices=sorted(SUPPORTED_PROVIDERS)
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--max-turns", default=8, type=int)
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--port", default=0, type=int)
    args = parser.parse_args()

    token = _read_token(args.token_env)
    state_db = args.state_db or _default_state_db(args.project_id)
    store = ProjectMasterSessionStore(state_db, args.project_id)
    provider = (
        GrokProjectMasterRuntime(
            args.project_root,
            args.project_id,
            store,
            model=args.model,
            max_turns=args.max_turns,
        )
        if args.provider == "GROK"
        else CodexProjectMasterRuntime(
            args.project_root,
            args.project_id,
            store,
        )
    )
    coordinator = ProjectModeCoordinator(
        args.project_root,
        args.project_id,
        provider.session_ref,
    )
    preparation = coordinator.prepare()
    worker = ProjectMasterConversationWorker(
        provider=provider,
        store=store,
        universe_endpoint=args.universe_endpoint,
        project_id=args.project_id,
        bridge_token=token,
        surface_observer=coordinator,
    )
    host = LiveProjectMasterBridgeHost(
        args.project_root,
        token,
        args.inbox_ref,
        worker,
        coordinator,
    )
    server = ProjectMasterBridgeHttpServer(("127.0.0.1", args.port), host)
    worker.start()
    print(
        json.dumps(
            {
                "schema": PROJECT_MASTER_HOST_SCHEMA,
                "endpoint": f"http://127.0.0.1:{server.server_port}",
                "master_session_ref": provider.session_ref,
                "project_id": args.project_id,
                "provider": args.provider,
                "state_db": str(state_db),
                "session_preparation": preparation["status"],
                "status": "LISTENING",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        worker.close()
        coordinator.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
