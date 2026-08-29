"""Provider model catalog: CLI discover + presets + user edits.

Stored under %LOCALAPPDATA%\\Universe\\provider-models.json (machine-local).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tomllib
from typing import Any, Callable, Mapping

from windows_native_cli import NativeCliRequest, NativeCliResult, run_native_cli


CATALOG_SCHEMA = "universe.provider-model-catalog.v1"
PROVIDERS = ("GROK", "CODEX", "CLAUDE")
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

# Document / product presets when CLI list is weak or offline.
DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "GROK": {
        "default": "grok-4.5",
        "models": ["grok-4.5", "grok-build"],
    },
    "CODEX": {
        "default": "default",
        "models": [
            "default",
            "gpt-5-codex",
            "gpt-5.3-codex-spark",
            "codex-mini-latest",
            "o3",
            "o4-mini",
        ],
    },
    "CLAUDE": {
        "default": "claude-sonnet-5",
        "models": [
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "default",
            "sonnet",
            "opus",
            "haiku",
        ],
    },
}

PROVIDER_TO_TOOL = {
    "GROK": "grok",
    "CODEX": "codex",
    "CLAUDE": "claude",
}


class ProviderModelCatalogError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_catalog_path(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    configured = env.get("UNIVERSE_PROVIDER_MODELS")
    if configured:
        return Path(configured).expanduser().resolve()
    local = env.get("LOCALAPPDATA")
    if local:
        return Path(local).expanduser().resolve() / "Universe" / "provider-models.json"
    return Path.home() / ".local" / "share" / "Universe" / "provider-models.json"


def _normalize_model_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or not MODEL_ID.fullmatch(text):
        return None
    return text


def _unique_models(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for item in group:
            mid = _normalize_model_id(item)
            if mid and mid not in seen:
                seen.add(mid)
                out.append(mid)
    return out


def empty_catalog() -> dict[str, Any]:
    providers: dict[str, Any] = {}
    for name in PROVIDERS:
        preset = DEFAULT_PRESETS[name]
        providers[name] = {
            "status": "UNKNOWN",
            "default": preset["default"],
            "models": list(preset["models"]),
            "user_models": [],
            "source": "PRESET",
            "cli_models": [],
            "note": "",
        }
    return {
        "schema": CATALOG_SCHEMA,
        "updated_at": None,
        "discovered_at": None,
        "catalog_path": None,
        "providers": providers,
    }


class ProviderModelCatalogStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        host_profile: Any | None = None,
        native_runner: Callable[[NativeCliRequest], NativeCliResult] = run_native_cli,
        home: Path | None = None,
    ) -> None:
        self.environment = dict(environment if environment is not None else os.environ)
        self.path = (
            path.expanduser().resolve()
            if path is not None
            else default_catalog_path(self.environment)
        )
        self.host_profile = host_profile
        self.native_runner = native_runner
        self.home = Path(home or Path.home()).expanduser().resolve()

    def snapshot(self) -> dict[str, Any]:
        catalog = self._load(allow_absent=True)
        catalog["catalog_path"] = str(self.path)
        return catalog

    def ensure_initialized(self) -> dict[str, Any]:
        if self.path.is_file():
            return self.snapshot()
        return self.discover(persist=True)

    def discover(self, *, persist: bool = True) -> dict[str, Any]:
        previous = self._load(allow_absent=True)
        tools = self._tool_snapshot()
        providers: dict[str, Any] = {}
        for name in PROVIDERS:
            preset = DEFAULT_PRESETS[name]
            prev = previous["providers"].get(name) or {}
            user_models = [
                m
                for m in (prev.get("user_models") or [])
                if _normalize_model_id(m)
            ]
            tool_name = PROVIDER_TO_TOOL[name]
            tool = tools.get(tool_name) or {}
            tool_status = str(tool.get("status") or "UNAVAILABLE")
            cli_models: list[str] = []
            note = ""
            source = "PRESET"
            default = str(preset["default"])
            if tool_status == "AVAILABLE":
                executable = tool.get("executable")
                try:
                    cli_default, cli_models, note = self._discover_provider(
                        name, executable
                    )
                    if cli_default:
                        default = cli_default
                    if cli_models:
                        source = "CLI+PRESET"
                    else:
                        source = "PRESET+HOST"
                        note = note or "CLI list empty; using presets"
                except Exception as error:  # noqa: BLE001 — catalog must not break host boot
                    note = f"discover failed: {error}"
                    source = "PRESET"
            else:
                note = str(tool.get("reason") or "provider CLI unavailable")
                source = "PRESET"

            host_model = _normalize_model_id(tool.get("model"))
            models = _unique_models(
                [default],
                [host_model] if host_model else [],
                cli_models,
                list(preset["models"]),
                user_models,
            )
            if default not in models and _normalize_model_id(default):
                models.insert(0, default)
            providers[name] = {
                "status": "AVAILABLE" if tool_status == "AVAILABLE" else "UNAVAILABLE",
                "default": default,
                "models": models,
                "user_models": user_models,
                "cli_models": cli_models,
                "source": source,
                "note": note,
            }

        now = utc_now()
        catalog = {
            "schema": CATALOG_SCHEMA,
            "updated_at": now,
            "discovered_at": now,
            "catalog_path": str(self.path),
            "providers": providers,
        }
        if persist:
            self._save(catalog)
        return catalog

    def save_user_edits(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ProviderModelCatalogError(
                "PROVIDER_MODELS_INVALID", "body must be an object"
            )
        raw_providers = value.get("providers")
        if not isinstance(raw_providers, dict):
            raise ProviderModelCatalogError(
                "PROVIDER_MODELS_INVALID", "providers must be an object"
            )
        current = self._load(allow_absent=True)
        for name in PROVIDERS:
            patch = raw_providers.get(name)
            if not isinstance(patch, dict):
                continue
            entry = current["providers"].setdefault(
                name,
                {
                    "status": "UNKNOWN",
                    "default": DEFAULT_PRESETS[name]["default"],
                    "models": list(DEFAULT_PRESETS[name]["models"]),
                    "user_models": [],
                    "cli_models": [],
                    "source": "PRESET",
                    "note": "",
                },
            )
            if "user_models" in patch:
                raw = patch["user_models"]
                if not isinstance(raw, list):
                    raise ProviderModelCatalogError(
                        "PROVIDER_MODELS_INVALID",
                        f"{name}.user_models must be an array",
                    )
                user_models = [
                    mid
                    for mid in (_normalize_model_id(item) for item in raw)
                    if mid
                ]
                entry["user_models"] = user_models
            if "default" in patch:
                default = _normalize_model_id(patch["default"])
                if default:
                    entry["default"] = default
            # Rebuild models from pieces
            entry["models"] = _unique_models(
                [entry.get("default") or ""],
                list(entry.get("cli_models") or []),
                list(DEFAULT_PRESETS[name]["models"]),
                list(entry.get("user_models") or []),
            )
        current["updated_at"] = utc_now()
        current["catalog_path"] = str(self.path)
        current["schema"] = CATALOG_SCHEMA
        self._save(current)
        return self.snapshot()

    def _tool_snapshot(self) -> dict[str, Any]:
        if self.host_profile is None:
            return {}
        try:
            snap = self.host_profile.snapshot()
        except Exception:  # noqa: BLE001
            return {}
        tools = snap.get("tools")
        return tools if isinstance(tools, dict) else {}

    def _discover_provider(
        self, provider: str, executable: Any
    ) -> tuple[str | None, list[str], str]:
        path = Path(str(executable or "")).expanduser()
        if not path.is_file():
            return None, [], "executable missing"
        if provider == "GROK":
            return self._discover_grok(path)
        if provider == "CODEX":
            return self._discover_codex(path)
        if provider == "CLAUDE":
            return self._discover_claude(path)
        return None, [], "unknown provider"

    def _discover_grok(self, executable: Path) -> tuple[str | None, list[str], str]:
        environment: dict[str, str] = {}
        if executable.parent.name.lower() == "bin":
            environment["GROK_HOME"] = str(executable.parent.parent)
        result = self.native_runner(
            NativeCliRequest(
                executable=executable,
                arguments=("models",),
                timeout_seconds=25,
                environment=environment,
            )
        )
        text = f"{result.stdout or ''}\n{result.stderr or ''}"
        default: str | None = None
        models: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            lower = stripped.lower()
            if lower.startswith("default model:"):
                default = _normalize_model_id(stripped.split(":", 1)[-1])
            if stripped.startswith("*"):
                body = stripped.lstrip("*").strip()
                body = body.replace("(default)", "").strip()
                mid = _normalize_model_id(body.split()[0] if body else "")
                if mid:
                    models.append(mid)
            else:
                # plain id lines
                mid = _normalize_model_id(stripped)
                if mid and " " not in stripped and ":" not in stripped[:12]:
                    # skip prose lines
                    if mid.startswith("grok") or mid in DEFAULT_PRESETS["GROK"]["models"]:
                        models.append(mid)
        models = _unique_models(models)
        note = "from `grok models`" if models else "grok models returned no ids"
        return default, models, note

    def _discover_codex(self, executable: Path) -> tuple[str | None, list[str], str]:
        # Prefer ~/.codex/config.toml current model; CLI has no stable list command.
        config_path = self.home / ".codex" / "config.toml"
        default: str | None = None
        models: list[str] = []
        if config_path.is_file():
            try:
                data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError):
                data = {}
            if isinstance(data, dict):
                default = _normalize_model_id(data.get("model"))
                agents = data.get("agents")
                if isinstance(agents, dict):
                    sub = _normalize_model_id(agents.get("default_subagent_model"))
                    if sub:
                        models.append(sub)
                if default:
                    models.append(default)
        # version probe only (native)
        self.native_runner(
            NativeCliRequest(
                executable=executable,
                arguments=("--version",),
                timeout_seconds=15,
            )
        )
        note = (
            "from ~/.codex/config.toml + presets"
            if default or models
            else "codex has no stable models list; presets only"
        )
        return default, _unique_models(models), note

    def _discover_claude(self, executable: Path) -> tuple[str | None, list[str], str]:
        # No stable non-interactive model list; confirm binary with --version.
        self.native_runner(
            NativeCliRequest(
                executable=executable,
                arguments=("--version",),
                timeout_seconds=15,
            )
        )
        return "claude-sonnet-5", [
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "default",
            "sonnet",
            "opus",
            "haiku",
        ], "claude CLI aliases + versioned model IDs"

    def _load(self, *, allow_absent: bool = False) -> dict[str, Any]:
        if not self.path.is_file():
            if allow_absent:
                return empty_catalog()
            raise ProviderModelCatalogError(
                "PROVIDER_MODELS_MISSING", f"catalog not found: {self.path}"
            )
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            if allow_absent:
                return empty_catalog()
            raise ProviderModelCatalogError(
                "PROVIDER_MODELS_INVALID", str(error)
            ) from error
        if not isinstance(raw, dict):
            return empty_catalog()
        base = empty_catalog()
        providers_in = raw.get("providers")
        if isinstance(providers_in, dict):
            for name in PROVIDERS:
                entry = providers_in.get(name)
                if not isinstance(entry, dict):
                    continue
                target = base["providers"][name]
                for key in (
                    "status",
                    "default",
                    "source",
                    "note",
                ):
                    if key in entry and entry[key] is not None:
                        target[key] = entry[key]
                for list_key in ("models", "user_models", "cli_models"):
                    if isinstance(entry.get(list_key), list):
                        target[list_key] = [
                            m
                            for m in (
                                _normalize_model_id(item) for item in entry[list_key]
                            )
                            if m
                        ]
                target["models"] = _unique_models(
                    [str(target.get("default") or "")],
                    target.get("cli_models") or [],
                    DEFAULT_PRESETS[name]["models"],
                    target.get("user_models") or [],
                    target.get("models") or [],
                )
        base["updated_at"] = raw.get("updated_at")
        base["discovered_at"] = raw.get("discovered_at")
        base["schema"] = CATALOG_SCHEMA
        return base

    def _save(self, catalog: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": CATALOG_SCHEMA,
            "updated_at": catalog.get("updated_at") or utc_now(),
            "discovered_at": catalog.get("discovered_at"),
            "providers": catalog.get("providers") or {},
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
