from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable, Mapping

from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli


PROFILE_SCHEMA = "ai-career.host-profile.v1"
PROFILE_ENVIRONMENT = "AI_CAREER_HOST_PROFILE"
SUPPORTED_TOOLS = ("python", "git", "codex", "grok")
REQUIRED_TOOLS = frozenset({"python", "git"})
FORBIDDEN_SUFFIXES = frozenset({".bat", ".cmd", ".ps1"})
EXECUTABLE_OVERRIDES = {
    tool: f"AI_CAREER_{tool.upper()}_EXECUTABLE" for tool in SUPPORTED_TOOLS
}
PROFILE_FIELDS = frozenset(
    {"schema", "revision", "created_at", "updated_at", "tools"}
)
TOOL_FIELDS = frozenset(
    {
        "status",
        "executable",
        "version",
        "verified_at",
        "discovery_source",
        "environment",
        "evidence_ref",
        "reason",
    }
)
ALLOWED_TOOL_ENVIRONMENT = frozenset({"GROK_HOME"})


class HostProfileError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HostToolResolution:
    tool: str
    executable: Path
    version: str
    environment: Mapping[str, str]
    evidence_ref: str


NativeRunner = Callable[[NativeCliRequest], NativeCliResult]
PathLookup = Callable[[str], str | None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_host_profile_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    values = environment if environment is not None else os.environ
    configured = values.get(PROFILE_ENVIRONMENT)
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = values.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).expanduser().resolve() / "ai-career" / "host.json"
    return Path.home() / ".local" / "share" / "ai-career" / "host.json"


class HostProfileStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        path_lookup: PathLookup = shutil.which,
        native_runner: NativeRunner = run_native_cli,
        current_python: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.environment = dict(environment if environment is not None else os.environ)
        self.path = (
            path.expanduser().resolve()
            if path is not None
            else default_host_profile_path(self.environment)
        )
        self.path_lookup = path_lookup
        self.native_runner = native_runner
        self.current_python = Path(current_python or sys.executable).resolve()
        self.home = Path(home or Path.home()).expanduser().resolve()

    def snapshot(self) -> dict[str, Any]:
        profile = self._load()
        return {
            **profile,
            "profile_path": str(self.path),
            "required_tools": sorted(REQUIRED_TOOLS),
            "status": self._profile_status(profile),
        }

    def ensure_initialized(self) -> dict[str, Any]:
        if self.path.is_file():
            return self.snapshot()
        return self.discover()

    def discover(self) -> dict[str, Any]:
        previous = self._load(allow_absent=True)
        tools: dict[str, dict[str, Any]] = {}
        for tool in SUPPORTED_TOOLS:
            record = self._discover_tool(tool)
            if record["status"] != "AVAILABLE":
                existing = previous["tools"].get(tool)
                if isinstance(existing, Mapping):
                    record = self._verify_record(tool, existing)
            tools[tool] = record
        profile = self._profile(tools, previous.get("created_at"))
        self._save(profile)
        return self.snapshot()

    def set_tool(self, tool: str, executable: str) -> dict[str, Any]:
        normalized = self._tool_name(tool)
        profile = self._load(allow_absent=True)
        profile["tools"][normalized] = self._candidate_record(
            normalized,
            Path(executable).expanduser(),
            "USER_SELECTED",
        )
        updated = self._profile(profile["tools"], profile.get("created_at"))
        self._save(updated)
        return self.snapshot()

    def verify_tool(self, tool: str) -> dict[str, Any]:
        normalized = self._tool_name(tool)
        profile = self._load()
        current = profile["tools"].get(normalized)
        if not isinstance(current, Mapping):
            raise HostProfileError(
                "HOST_TOOL_NOT_CONFIGURED",
                f"{normalized} is not configured",
            )
        profile["tools"][normalized] = self._verify_record(normalized, current)
        updated = self._profile(profile["tools"], profile.get("created_at"))
        self._save(updated)
        return self.snapshot()

    def resolve(self, tool: str) -> HostToolResolution | None:
        normalized = self._tool_name(tool)
        profile = self._load(allow_absent=True)
        record = profile["tools"].get(normalized)
        if not isinstance(record, Mapping) or record.get("status") != "AVAILABLE":
            return None
        executable = self._native_path(record.get("executable"))
        if executable is None:
            return None
        environment = record.get("environment")
        if not isinstance(environment, Mapping):
            environment = {}
        safe_environment = {
            str(key): str(value)
            for key, value in environment.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        return HostToolResolution(
            tool=normalized,
            executable=executable,
            version=str(record.get("version") or "UNKNOWN"),
            environment=safe_environment,
            evidence_ref=str(record.get("evidence_ref") or "UNKNOWN"),
        )

    def _load(self, *, allow_absent: bool = False) -> dict[str, Any]:
        if not self.path.is_file():
            if allow_absent:
                return self._profile({}, None)
            raise HostProfileError(
                "HOST_PROFILE_UNAVAILABLE",
                f"Host Profile does not exist: {self.path}",
            )
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise HostProfileError(
                "HOST_PROFILE_INVALID",
                f"Host Profile is not valid UTF-8 JSON: {self.path}",
            ) from error
        if not isinstance(value, Mapping) or value.get("schema") != PROFILE_SCHEMA:
            raise HostProfileError(
                "HOST_PROFILE_INVALID",
                "Host Profile schema is invalid",
            )
        unknown_profile_fields = set(value) - PROFILE_FIELDS
        if unknown_profile_fields:
            raise HostProfileError(
                "HOST_PROFILE_INVALID",
                "Host Profile contains unsupported fields: "
                + ", ".join(sorted(unknown_profile_fields)),
            )
        tools = value.get("tools")
        if not isinstance(tools, Mapping):
            raise HostProfileError(
                "HOST_PROFILE_INVALID",
                "Host Profile tools must be an object",
            )
        unknown = set(tools) - set(SUPPORTED_TOOLS)
        if unknown:
            raise HostProfileError(
                "HOST_PROFILE_INVALID",
                f"Host Profile contains unsupported tools: {', '.join(sorted(unknown))}",
            )
        normalized_tools: dict[str, dict[str, Any]] = {}
        for key, item in tools.items():
            if not isinstance(item, Mapping):
                raise HostProfileError(
                    "HOST_PROFILE_INVALID",
                    f"Host Profile tool {key} must be an object",
                )
            unknown_tool_fields = set(item) - TOOL_FIELDS
            if unknown_tool_fields:
                raise HostProfileError(
                    "HOST_PROFILE_INVALID",
                    f"Host Profile tool {key} contains unsupported fields: "
                    + ", ".join(sorted(unknown_tool_fields)),
                )
            environment = item.get("environment", {})
            if not isinstance(environment, Mapping) or (
                set(environment) - ALLOWED_TOOL_ENVIRONMENT
            ):
                raise HostProfileError(
                    "HOST_PROFILE_INVALID",
                    f"Host Profile tool {key} environment is invalid",
                )
            normalized_tools[str(key)] = dict(item)
        return {
            "schema": PROFILE_SCHEMA,
            "revision": int(value.get("revision") or 1),
            "created_at": str(value.get("created_at") or utc_now()),
            "updated_at": str(value.get("updated_at") or utc_now()),
            "tools": normalized_tools,
        }

    def _profile(
        self,
        tools: Mapping[str, Mapping[str, Any]],
        created_at: Any,
    ) -> dict[str, Any]:
        now = utc_now()
        return {
            "schema": PROFILE_SCHEMA,
            "revision": 1,
            "created_at": str(created_at or now),
            "updated_at": now,
            "tools": {
                tool: dict(tools.get(tool) or self._unavailable_record(tool))
                for tool in SUPPORTED_TOOLS
            },
        }

    def _save(self, profile: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            dict(profile),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        descriptor, temporary = tempfile.mkstemp(
            prefix="host-profile-",
            suffix=".json",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _discover_tool(self, tool: str) -> dict[str, Any]:
        last_failure = self._unavailable_record(tool)
        for candidate, source in self._candidates(tool):
            record = self._candidate_record(tool, candidate, source)
            if record["status"] == "AVAILABLE":
                return record
            last_failure = record
        return last_failure

    def _candidates(self, tool: str) -> list[tuple[Path, str]]:
        values: list[tuple[Path, str]] = []
        override = self.environment.get(EXECUTABLE_OVERRIDES[tool])
        if override:
            values.append((Path(override).expanduser(), "ENVIRONMENT_OVERRIDE"))
        if tool == "codex":
            legacy = self.environment.get("CODEX_CLI_PATH")
            if legacy:
                values.append((Path(legacy).expanduser(), "LEGACY_ENVIRONMENT"))
            values.append(
                (
                    self.home / ".codex" / ".sandbox-bin" / "codex.exe",
                    "KNOWN_LOCATION",
                )
            )
        elif tool == "grok":
            grok_home = self.environment.get("GROK_HOME")
            if grok_home:
                values.append(
                    (Path(grok_home).expanduser() / "bin" / "grok.exe", "LEGACY_ENVIRONMENT")
                )
            values.append((self.home / ".grok" / "bin" / "grok.exe", "KNOWN_LOCATION"))
        elif tool == "python":
            values.append((self.current_python, "CURRENT_PROCESS"))
        names = {
            "python": ("python.exe", "python"),
            "git": ("git.exe", "git"),
            "codex": ("codex.exe", "codex"),
            "grok": ("grok.exe", "grok"),
        }[tool]
        for name in names:
            resolved = self.path_lookup(name)
            if resolved:
                values.append((Path(resolved), "PATH"))
        deduplicated: list[tuple[Path, str]] = []
        seen: set[str] = set()
        for path, source in values:
            resolved_path = path.expanduser().resolve()
            key = os.path.normcase(str(resolved_path))
            if key not in seen:
                seen.add(key)
                deduplicated.append((resolved_path, source))
        return deduplicated

    def _candidate_record(
        self,
        tool: str,
        candidate: Path,
        source: str,
    ) -> dict[str, Any]:
        executable = self._native_path(candidate)
        if executable is None:
            return self._unavailable_record(tool, "EXECUTABLE_INVALID", source)
        environment: dict[str, str] = {}
        if tool == "grok" and executable.parent.name.lower() == "bin":
            environment["GROK_HOME"] = str(executable.parent.parent)
        try:
            result = self.native_runner(
                NativeCliRequest(
                    executable=executable,
                    arguments=("--version",),
                    timeout_seconds=20,
                    environment=environment,
                )
            )
        except (OSError, ValueError):
            return self._unavailable_record(tool, "VERSION_CHECK_FAILED", source)
        version = (result.stdout or result.stderr).strip().splitlines()
        if result.status != "COMPLETED" or result.return_code != 0 or not version:
            return self._unavailable_record(tool, "VERSION_CHECK_FAILED", source)
        verified_at = utc_now()
        return {
            "status": "AVAILABLE",
            "executable": str(executable),
            "version": version[0][:512],
            "verified_at": verified_at,
            "discovery_source": source,
            "environment": environment,
            "evidence_ref": (
                f"host-tool://{tool}/{executable.name}?verified_at={verified_at}"
            ),
        }

    def _verify_record(
        self,
        tool: str,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        executable = record.get("executable")
        if not isinstance(executable, str) or not executable:
            return self._unavailable_record(tool)
        return self._candidate_record(
            tool,
            Path(executable),
            str(record.get("discovery_source") or "HOST_PROFILE"),
        )

    @staticmethod
    def _native_path(value: Any) -> Path | None:
        if isinstance(value, Path):
            candidate = value
        elif isinstance(value, str) and value.strip():
            candidate = Path(value.strip())
        else:
            return None
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except OSError:
            return None
        if not resolved.is_file() or resolved.suffix.lower() in FORBIDDEN_SUFFIXES:
            return None
        if os.name == "nt" and resolved.suffix.lower() != ".exe":
            return None
        return resolved

    @staticmethod
    def _unavailable_record(
        tool: str,
        reason: str = "NOT_DISCOVERED",
        source: str = "NONE",
    ) -> dict[str, Any]:
        return {
            "status": "UNAVAILABLE",
            "executable": "UNKNOWN",
            "version": "UNKNOWN",
            "verified_at": "UNKNOWN",
            "discovery_source": source,
            "environment": {},
            "evidence_ref": "UNKNOWN",
            "reason": f"{tool.upper()}_{reason}",
        }

    @staticmethod
    def _profile_status(profile: Mapping[str, Any]) -> str:
        tools = profile.get("tools")
        if not isinstance(tools, Mapping):
            return "HOST_PROFILE_INVALID"
        missing_required = [
            tool
            for tool in REQUIRED_TOOLS
            if not isinstance(tools.get(tool), Mapping)
            or tools[tool].get("status") != "AVAILABLE"
        ]
        return (
            "HOST_PROFILE_INCOMPLETE"
            if missing_required
            else "HOST_PROFILE_READY"
        )

    @staticmethod
    def _tool_name(tool: str) -> str:
        normalized = str(tool).strip().lower()
        if normalized not in SUPPORTED_TOOLS:
            raise HostProfileError(
                "HOST_TOOL_UNSUPPORTED",
                f"unsupported Host tool: {tool}",
            )
        return normalized


def default_host_profile() -> HostProfileStore:
    return HostProfileStore()


def resolve_host_tool(tool: str) -> HostToolResolution | None:
    return default_host_profile().resolve(tool)
