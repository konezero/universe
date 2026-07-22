"""Source-backed Mode Registry validation and mutation planning."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODE_REGISTRY_SCHEMA = "ai-career.mode-registry.v1"
MODE_REGISTRY_RELATIVE_PATH = Path(
    ".ai/runtime/project_instance/mode_registry.json"
)
INSTALLATION_MANIFEST_RELATIVE_PATH = Path(
    ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
)
INSTALLATION_MANIFEST_SCHEMA = "ai-career.project-runtime-installation.v1"
MODE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{0,63}$")
REGISTRY_POLICIES = frozenset({"IMMUTABLE", "MASTER_MANAGED"})
REPOSITORY_KINDS = frozenset({"AI_CAREER", "PROJECT"})
MODE_PROFILES = frozenset({"GOVERNANCE_ONLY", "EXECUTABLE_PROOF_REQUIRED"})
MUTATION_OPERATIONS = frozenset({"ADD", "MODIFY", "DELETE"})
AI_CAREER_MODES = frozenset({"CONDUCTOR", "CARRIER"})
AI_CAREER_MODE_DEFINITIONS = {
    "CONDUCTOR": {
        "role": "CONDUCTOR",
        "scope": "governance",
        "mode_profile": "GOVERNANCE_ONLY",
    },
    "CARRIER": {
        "role": "CARRIER",
        "scope": "candidate-transfer",
        "mode_profile": "GOVERNANCE_ONLY",
    },
}


@dataclass(frozen=True)
class ModeRegistryError(Exception):
    """A deterministic Mode Registry contract failure."""

    error_code: str
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True)
class ModeDefinition:
    mode: str
    role: str
    scope: str
    mode_profile: str

    def as_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "scope": self.scope,
            "mode_profile": self.mode_profile,
        }


@dataclass(frozen=True)
class ModeRegistry:
    path: Path
    owner: str
    repository_kind: str
    policy: str
    root_mode: str | None
    revision: int
    modes: Mapping[str, ModeDefinition]

    def resolve(self, mode: str) -> ModeDefinition:
        normalized = normalize_mode_id(mode)
        definition = self.modes.get(normalized)
        if definition is None:
            raise ModeRegistryError(
                "MODE_NOT_REGISTERED",
                f"Mode is not registered: {normalized}",
            )
        return definition

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": MODE_REGISTRY_SCHEMA,
            "owner": self.owner,
            "repository_kind": self.repository_kind,
            "policy": self.policy,
            "root_mode": self.root_mode,
            "revision": self.revision,
            "modes": {
                mode: self.modes[mode].as_dict()
                for mode in sorted(self.modes)
            },
        }


def normalize_mode_id(value: str) -> str:
    if not isinstance(value, str):
        raise ModeRegistryError("MODE_ID_INVALID", "Mode ID must be a string")
    stripped = value.strip()
    if not stripped.isascii():
        raise ModeRegistryError(
            "MODE_ID_INVALID",
            "Mode ID must contain ASCII characters only",
        )
    normalized = stripped.upper()
    if not MODE_ID_PATTERN.fullmatch(normalized):
        raise ModeRegistryError(
            "MODE_ID_INVALID",
            "Mode ID must match ^[A-Z][A-Z0-9_-]{0,63}$",
        )
    return normalized


def load_mode_registry(repository_root: Path) -> ModeRegistry:
    root = repository_root.expanduser().resolve()
    path = root / MODE_REGISTRY_RELATIVE_PATH
    try:
        payload = _strict_json_loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise ModeRegistryError(
            "MODE_REGISTRY_UNAVAILABLE",
            f"Mode Registry is missing: {path}",
        ) from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ModeRegistryError(
            "MODE_REGISTRY_INVALID",
            f"Mode Registry cannot be loaded: {error}",
        ) from error
    registry = validate_mode_registry(payload, path=path)
    if registry.repository_kind == "PROJECT":
        _validate_project_installation_identity(root, registry)
    return registry


def _validate_project_installation_identity(
    repository_root: Path, registry: ModeRegistry
) -> None:
    path = repository_root / INSTALLATION_MANIFEST_RELATIVE_PATH
    try:
        payload = _strict_json_loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise ModeRegistryError(
            "PROJECT_INSTALLATION_IDENTITY_UNAVAILABLE",
            f"project installation manifest is missing: {path}",
        ) from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ModeRegistryError(
            "PROJECT_INSTALLATION_IDENTITY_INVALID",
            f"project installation manifest cannot be loaded: {error}",
        ) from error
    if not isinstance(payload, Mapping) or payload.get("schema") != INSTALLATION_MANIFEST_SCHEMA:
        raise ModeRegistryError(
            "PROJECT_INSTALLATION_IDENTITY_INVALID",
            "project installation manifest schema is invalid",
        )
    installation = payload.get("installation")
    if not isinstance(installation, Mapping):
        raise ModeRegistryError(
            "PROJECT_INSTALLATION_IDENTITY_INVALID",
            "project installation coordinates are unavailable",
        )
    if installation.get("project") != registry.owner:
        raise ModeRegistryError(
            "PROJECT_MODE_REGISTRY_OWNER_MISMATCH",
            "project Registry owner does not match the installed project identity",
        )


def validate_mode_registry(payload: Any, *, path: Path) -> ModeRegistry:
    if not isinstance(payload, Mapping):
        raise ModeRegistryError(
            "MODE_REGISTRY_INVALID", "Mode Registry root must be an object"
        )
    _exact_fields(
        payload,
        {
            "schema",
            "owner",
            "repository_kind",
            "policy",
            "root_mode",
            "revision",
            "modes",
        },
        "registry",
    )
    if payload.get("schema") != MODE_REGISTRY_SCHEMA:
        raise ModeRegistryError(
            "MODE_REGISTRY_SCHEMA_INVALID",
            f"Mode Registry schema must be {MODE_REGISTRY_SCHEMA}",
        )
    owner = _required_text(payload, "owner", "registry")
    repository_kind = _required_text(
        payload, "repository_kind", "registry"
    ).upper()
    if repository_kind not in REPOSITORY_KINDS:
        raise ModeRegistryError(
            "MODE_REGISTRY_KIND_INVALID",
            f"unsupported repository kind: {repository_kind}",
        )
    policy = _required_text(payload, "policy", "registry").upper()
    if policy not in REGISTRY_POLICIES:
        raise ModeRegistryError(
            "MODE_REGISTRY_POLICY_INVALID",
            f"unsupported Mode Registry policy: {policy}",
        )
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ModeRegistryError(
            "MODE_REGISTRY_REVISION_INVALID",
            "registry.revision must be a positive integer",
        )
    raw_root_mode = payload.get("root_mode")
    if raw_root_mode is not None and not isinstance(raw_root_mode, str):
        raise ModeRegistryError(
            "MODE_REGISTRY_ROOT_INVALID",
            "registry.root_mode must be a Mode ID or null",
        )
    root_mode = (
        None if raw_root_mode is None else normalize_mode_id(raw_root_mode)
    )
    raw_modes = payload.get("modes")
    if not isinstance(raw_modes, Mapping) or not raw_modes:
        raise ModeRegistryError(
            "MODE_REGISTRY_EMPTY", "registry.modes must be a non-empty object"
        )
    modes: dict[str, ModeDefinition] = {}
    for raw_mode, raw_definition in raw_modes.items():
        mode = normalize_mode_id(raw_mode)
        if raw_mode != mode:
            raise ModeRegistryError(
                "MODE_ID_INVALID",
                f"Registry Mode ID must use canonical uppercase ASCII: {raw_mode}",
            )
        if mode in modes:
            raise ModeRegistryError(
                "MODE_REGISTRY_DUPLICATE",
                f"duplicate normalized Mode ID: {mode}",
            )
        definition = _mode_definition(mode, raw_definition)
        if raw_definition.get("role") != definition.role:
            raise ModeRegistryError(
                "MODE_ID_INVALID",
                f"Registry role must use canonical uppercase ASCII: {mode}",
            )
        modes[mode] = definition

    if repository_kind == "AI_CAREER":
        if owner != "ai-career":
            raise ModeRegistryError(
                "AI_CAREER_MODE_REGISTRY_INVALID",
                "ai-career Registry owner must be ai-career",
            )
        if policy != "IMMUTABLE" or root_mode is not None:
            raise ModeRegistryError(
                "AI_CAREER_MODE_REGISTRY_INVALID",
                "ai-career Registry must be IMMUTABLE with no root Mode",
            )
        if frozenset(modes) != AI_CAREER_MODES:
            raise ModeRegistryError(
                "AI_CAREER_MODE_SET_INVALID",
                "ai-career Registry must contain exactly CONDUCTOR and CARRIER",
            )
        actual_definitions = {
            mode: modes[mode].as_dict() for mode in sorted(modes)
        }
        if actual_definitions != {
            mode: AI_CAREER_MODE_DEFINITIONS[mode] for mode in sorted(AI_CAREER_MODES)
        }:
            raise ModeRegistryError(
                "AI_CAREER_MODE_DEFINITION_INVALID",
                "ai-career CONDUCTOR/CARRIER definitions are immutable",
            )
    if repository_kind == "PROJECT":
        if policy != "MASTER_MANAGED" or root_mode != "MASTER":
            raise ModeRegistryError(
                "PROJECT_MODE_REGISTRY_INVALID",
                "project Registry must be MASTER_MANAGED with root Mode MASTER",
            )
        if "MASTER" not in modes:
            raise ModeRegistryError(
                "PROJECT_MASTER_MODE_REQUIRED",
                "project Registry must contain MASTER",
            )
        if modes["MASTER"].role != "MASTER":
            raise ModeRegistryError(
                "PROJECT_MASTER_ROLE_INVALID",
                "project MASTER Mode must retain the MASTER role",
            )

    return ModeRegistry(
        path=path,
        owner=owner,
        repository_kind=repository_kind,
        policy=policy,
        root_mode=root_mode,
        revision=revision,
        modes=modes,
    )


def plan_mode_registry_mutation(
    repository_root: Path,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an exact postimage plan; never write the Registry."""

    _exact_fields(
        request,
        {"operation", "actor_mode", "mode", "active_mode", "definition"},
        "request",
    )
    registry = load_mode_registry(repository_root)
    operation = _required_text(request, "operation", "request").upper()
    if operation not in MUTATION_OPERATIONS:
        raise ModeRegistryError(
            "MODE_REGISTRY_OPERATION_INVALID",
            f"unsupported Registry operation: {operation}",
        )
    if registry.policy == "IMMUTABLE":
        raise ModeRegistryError(
            "MODE_REGISTRY_IMMUTABLE",
            "the ai-career CONDUCTOR/CARRIER Registry is immutable",
        )
    actor_mode = normalize_mode_id(
        _required_text(request, "actor_mode", "request")
    )
    if actor_mode != "MASTER":
        raise ModeRegistryError(
            "MASTER_MODE_REQUIRED",
            "project Mode Registry mutation requires MASTER",
        )
    mode = normalize_mode_id(_required_text(request, "mode", "request"))
    active_mode_value = request.get("active_mode", "UNKNOWN")
    if not isinstance(active_mode_value, str):
        raise ModeRegistryError(
            "ACTIVE_MODE_INVALID", "request.active_mode must be a string"
        )
    active_mode = (
        "UNKNOWN"
        if active_mode_value.strip().upper() == "UNKNOWN"
        else normalize_mode_id(active_mode_value)
    )
    if operation == "DELETE" and active_mode == "UNKNOWN":
        raise ModeRegistryError(
            "ACTIVE_MODE_REQUIRED",
            "DELETE requires the source-backed active Mode",
        )
    modes = dict(registry.modes)

    if operation == "ADD":
        if mode in modes:
            raise ModeRegistryError(
                "MODE_ALREADY_REGISTERED", f"Mode is already registered: {mode}"
            )
        modes[mode] = _mode_definition(mode, request.get("definition"))
    elif operation == "MODIFY":
        if mode not in modes:
            raise ModeRegistryError(
                "MODE_NOT_REGISTERED", f"Mode is not registered: {mode}"
            )
        definition = _mode_definition(mode, request.get("definition"))
        if mode == registry.root_mode and definition.role != registry.root_mode:
            raise ModeRegistryError(
                "ROOT_MODE_ROLE_CHANGE_FORBIDDEN",
                "MASTER root Mode must retain the MASTER role",
            )
        modes[mode] = definition
    else:
        if request.get("definition") is not None:
            raise ModeRegistryError(
                "MODE_REGISTRY_REQUEST_INVALID",
                "DELETE must not include a definition",
            )
        if mode == registry.root_mode:
            raise ModeRegistryError(
                "ROOT_MODE_DELETION_FORBIDDEN",
                "MASTER cannot delete itself",
            )
        registry.resolve(active_mode)
        if mode == active_mode:
            raise ModeRegistryError(
                "ACTIVE_MODE_DELETION_FORBIDDEN",
                f"active Mode must be changed before deletion: {mode}",
            )
        if mode not in modes:
            raise ModeRegistryError(
                "MODE_NOT_REGISTERED", f"Mode is not registered: {mode}"
            )
        del modes[mode]

    updated = ModeRegistry(
        path=registry.path,
        owner=registry.owner,
        repository_kind=registry.repository_kind,
        policy=registry.policy,
        root_mode=registry.root_mode,
        revision=registry.revision + 1,
        modes=modes,
    )
    preimage = _canonical_json(registry.as_dict())
    postimage = _canonical_json(updated.as_dict())
    return {
        "schema": "ai-career.mode-registry-mutation-plan.v1",
        "status": "MODE_REGISTRY_MUTATION_PLANNED",
        "operation": operation,
        "actor_mode": actor_mode,
        "mode": mode,
        "target": str(registry.path),
        "preimage_sha256": _sha256(preimage),
        "postimage_sha256": _sha256(postimage),
        "postimage_text": postimage.decode("utf-8"),
        "revision_before": registry.revision,
        "revision_after": updated.revision,
        "approval_required": True,
        "execution_guard_required": True,
        "repository_write": False,
    }


def verify_mode_request(
    registry: ModeRegistry,
    *,
    mode: str,
    role: str,
    scope: str,
    mode_profile: str,
) -> ModeDefinition:
    definition = registry.resolve(mode)
    requested = {
        "role": role.strip().upper(),
        "scope": scope.strip(),
        "mode_profile": mode_profile.strip().upper(),
    }
    if requested != definition.as_dict():
        raise ModeRegistryError(
            "MODE_REGISTRY_PROFILE_MISMATCH",
            f"requested Mode definition does not match Registry entry: {definition.mode}",
        )
    return definition


def mode_definition_digest(definition: ModeDefinition) -> str:
    return _sha256(_canonical_json(definition.as_dict()))


def mode_registry_digest(registry: ModeRegistry) -> str:
    return _sha256(_canonical_json(registry.as_dict()))


def _mode_definition(mode: str, payload: Any) -> ModeDefinition:
    if not isinstance(payload, Mapping):
        raise ModeRegistryError(
            "MODE_DEFINITION_INVALID", f"definition for {mode} must be an object"
        )
    _exact_fields(payload, {"role", "scope", "mode_profile"}, f"modes.{mode}")
    role = normalize_mode_id(_required_text(payload, "role", f"modes.{mode}"))
    scope = _required_text(payload, "scope", f"modes.{mode}")
    mode_profile = _required_text(
        payload, "mode_profile", f"modes.{mode}"
    ).upper()
    if mode_profile not in MODE_PROFILES:
        raise ModeRegistryError(
            "MODE_PROFILE_INVALID",
            f"unsupported Mode Profile: {mode_profile}",
        )
    return ModeDefinition(
        mode=mode,
        role=role,
        scope=scope,
        mode_profile=mode_profile,
    )


def _exact_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
    context: str,
) -> None:
    extra = sorted(set(payload).difference(allowed))
    if extra:
        raise ModeRegistryError(
            "MODE_REGISTRY_FIELD_UNSUPPORTED",
            f"{context} contains unsupported field: {extra[0]}",
        )


def _required_text(
    payload: Mapping[str, Any],
    field: str,
    context: str,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ModeRegistryError(
            "MODE_REGISTRY_FIELD_INVALID",
            f"{context}.{field} must be a non-empty string",
        )
    return value.strip()


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json_loads(text: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=reject_duplicate_keys)
