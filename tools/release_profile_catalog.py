from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


PROFILE_CATALOG_SCHEMA = "ai-career.release-profile-catalog.v1"
GOVERNANCE_CATALOG_SCHEMA = "ai-career.release-profile-catalog.v2"
CONTEXT_CATALOG_SCHEMA = "ai-career.release-profile-catalog.v3"
PROFILE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
GOVERNANCE_KIND_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{0,63}$")
SOURCE_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OVERLAY_POLICIES = frozenset({"APPEND_ONLY", "NONE"})
GOVERNANCE_SELECTOR_FIELDS = (
    "role",
    "mode",
    "operation",
    "scope",
    "risk",
    "capability",
)


class ReleaseProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ProfileSurface:
    path: str
    required: bool

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "required": self.required}


@dataclass(frozen=True)
class LoadProfile:
    profile_id: str
    description: str
    surfaces: tuple[ProfileSurface, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "description": self.description,
            "surfaces": [surface.as_dict() for surface in self.surfaces],
        }


@dataclass(frozen=True)
class SkillBinding:
    skill_id: str
    profile_id: str

    def as_dict(self) -> dict[str, str]:
        return {"skill_id": self.skill_id, "profile_id": self.profile_id}


@dataclass(frozen=True)
class ModeProfile:
    mode_profile_id: str
    overlay_policy: str
    load_profiles: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode_profile_id": self.mode_profile_id,
            "overlay_policy": self.overlay_policy,
            "load_profiles": list(self.load_profiles),
        }


@dataclass(frozen=True)
class ContextProfile:
    context_profile_id: str
    overlay_policy: str
    load_profiles: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_profile_id": self.context_profile_id,
            "overlay_policy": self.overlay_policy,
            "load_profiles": list(self.load_profiles),
        }


@dataclass(frozen=True)
class ReleaseProfileCatalog:
    owner: str
    load_profiles: tuple[LoadProfile, ...]
    skill_bindings: tuple[SkillBinding, ...]
    context_profiles: tuple[ContextProfile, ...]
    schema: str

    @property
    def mode_profiles(self) -> tuple[ModeProfile, ...]:
        """Compatibility view for v1/v2 release artifacts only."""
        return tuple(
            ModeProfile(
                mode_profile_id=profile.context_profile_id,
                overlay_policy=profile.overlay_policy,
                load_profiles=profile.load_profiles,
            )
            for profile in self.context_profiles
        )

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": self.schema,
            "owner": self.owner,
            "load_profiles": [profile.as_dict() for profile in self.load_profiles],
            "skill_bindings": [binding.as_dict() for binding in self.skill_bindings],
        }
        if self.schema in {PROFILE_CATALOG_SCHEMA, GOVERNANCE_CATALOG_SCHEMA}:
            result["mode_profiles"] = [
                profile.as_dict()
                for profile in self.mode_profiles
            ]
        elif self.schema == CONTEXT_CATALOG_SCHEMA:
            result["context_profiles"] = [
                profile.as_dict()
                for profile in self.context_profiles
            ]
        else:
            raise ReleaseProfileError("release profile catalog schema is unsupported")
        return result

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(
                self.as_dict(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class GovernanceSelector:
    role: str
    mode: str
    operation: str
    scope: str
    risk: str
    capability: str

    def as_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "mode": self.mode,
            "operation": self.operation,
            "scope": self.scope,
            "risk": self.risk,
            "capability": self.capability,
        }


@dataclass(frozen=True)
class GovernanceUnit:
    governance_id: str
    kind: str
    source_ref: str
    source_digest: str
    compact_instruction: str

    def as_dict(self) -> dict[str, str]:
        return {
            "governance_id": self.governance_id,
            "kind": self.kind,
            "source_ref": self.source_ref,
            "source_digest": self.source_digest,
            "compact_instruction": self.compact_instruction,
        }


@dataclass(frozen=True)
class GovernanceIndexEntry:
    selector: GovernanceSelector
    governance_id: str
    required: bool
    priority: int

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.selector.as_dict(),
            "governance_id": self.governance_id,
            "required": self.required,
            "priority": self.priority,
        }


@dataclass(frozen=True)
class GovernanceDependency:
    governance_id: str
    requires_governance_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "governance_id": self.governance_id,
            "requires_governance_id": self.requires_governance_id,
        }


@dataclass(frozen=True)
class GovernanceOverride:
    base_governance_id: str
    overriding_governance_id: str
    applies_when: GovernanceSelector

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_governance_id": self.base_governance_id,
            "overriding_governance_id": self.overriding_governance_id,
            "applies_when": self.applies_when.as_dict(),
        }


@dataclass(frozen=True)
class GovernanceCatalog(ReleaseProfileCatalog):
    governance_units: tuple[GovernanceUnit, ...]
    governance_index: tuple[GovernanceIndexEntry, ...]
    governance_dependencies: tuple[GovernanceDependency, ...]
    governance_overrides: tuple[GovernanceOverride, ...]

    def as_dict(self) -> dict[str, Any]:
        result = super().as_dict()
        result.update(
            {
                "schema": self.schema,
                "governance_units": [
                    unit.as_dict() for unit in self.governance_units
                ],
                "governance_index": [
                    entry.as_dict() for entry in self.governance_index
                ],
                "governance_dependencies": [
                    dependency.as_dict()
                    for dependency in self.governance_dependencies
                ],
                "governance_overrides": [
                    override.as_dict()
                    for override in self.governance_overrides
                ],
            }
        )
        return result

    @property
    def digest(self) -> str:
        return _digest(self.as_dict())


@dataclass(frozen=True)
class GovernanceSelection:
    catalog_digest: str
    selector: GovernanceSelector
    matched_entries: tuple[GovernanceIndexEntry, ...]
    dependency_closure: tuple[str, ...]
    units: tuple[GovernanceUnit, ...]
    selector_digest: str

    @property
    def governance_ids(self) -> tuple[str, ...]:
        return self.dependency_closure

    def as_dict(self) -> dict[str, Any]:
        return {
            "catalog_digest": self.catalog_digest,
            "selector": self.selector.as_dict(),
            "matched_entries": [
                entry.as_dict() for entry in self.matched_entries
            ],
            "dependency_closure": list(self.dependency_closure),
            "units": [unit.as_dict() for unit in self.units],
            "selector_digest": self.selector_digest,
        }


def _parse_profile_model(
    value: Mapping[str, Any],
    *,
    packaged_paths: Iterable[str],
    profile_field: str,
) -> tuple[
    str,
    list[LoadProfile],
    list[SkillBinding],
    list[ContextProfile],
]:
    owner = _text(value.get("owner"), "owner")
    paths = frozenset(packaged_paths)
    load_profiles = _load_profiles(value.get("load_profiles"), paths)
    profile_ids = {profile.profile_id for profile in load_profiles}
    skill_bindings = _skill_bindings(value.get("skill_bindings"), profile_ids)
    if profile_field == "mode_profiles":
        legacy_profiles = _mode_profiles(value.get(profile_field), profile_ids)
        context_profiles = [
            ContextProfile(
                context_profile_id=profile.mode_profile_id,
                overlay_policy=profile.overlay_policy,
                load_profiles=profile.load_profiles,
            )
            for profile in legacy_profiles
        ]
    else:
        context_profiles = _context_profiles(value.get(profile_field), profile_ids)
    return owner, load_profiles, skill_bindings, context_profiles


def parse_release_profile_catalog(
    value: Any,
    *,
    packaged_paths: Iterable[str],
) -> ReleaseProfileCatalog:
    if not isinstance(value, dict) or value.get("schema") != PROFILE_CATALOG_SCHEMA:
        raise ReleaseProfileError("release profile catalog schema is unsupported")
    if set(value) != {
        "schema",
        "owner",
        "load_profiles",
        "skill_bindings",
        "mode_profiles",
    }:
        raise ReleaseProfileError("release profile catalog fields are invalid")
    owner, load_profiles, skill_bindings, context_profiles = _parse_profile_model(
        value,
        packaged_paths=packaged_paths,
        profile_field="mode_profiles",
    )
    return ReleaseProfileCatalog(
        owner=owner,
        load_profiles=tuple(
            sorted(load_profiles, key=lambda profile: profile.profile_id)
        ),
        skill_bindings=tuple(
            sorted(skill_bindings, key=lambda binding: binding.skill_id)
        ),
        context_profiles=tuple(
            sorted(context_profiles, key=lambda profile: profile.context_profile_id)
        ),
        schema=PROFILE_CATALOG_SCHEMA,
    )


def parse_release_governance_catalog(
    value: Any,
    *,
    packaged_paths: Iterable[str],
) -> GovernanceCatalog:
    schema = value.get("schema") if isinstance(value, dict) else None
    if schema not in {GOVERNANCE_CATALOG_SCHEMA, CONTEXT_CATALOG_SCHEMA}:
        raise ReleaseProfileError("governance catalog schema is unsupported")
    profile_field = (
        "mode_profiles"
        if schema == GOVERNANCE_CATALOG_SCHEMA
        else "context_profiles"
    )
    if set(value) != {
        "schema",
        "owner",
        "load_profiles",
        "skill_bindings",
        profile_field,
        "governance_units",
        "governance_index",
        "governance_dependencies",
        "governance_overrides",
    }:
        raise ReleaseProfileError("governance catalog fields are invalid")

    owner, load_profiles, skill_bindings, context_profiles = _parse_profile_model(
        value,
        packaged_paths=packaged_paths,
        profile_field=profile_field,
    )
    paths = frozenset(packaged_paths)
    units = _governance_units(value.get("governance_units"), paths)
    unit_ids = {unit.governance_id for unit in units}
    index = _governance_index(value.get("governance_index"), unit_ids)
    dependencies = _governance_dependencies(
        value.get("governance_dependencies"),
        unit_ids,
    )
    _validate_dependency_graph(dependencies)
    overrides = _governance_overrides(
        value.get("governance_overrides"),
        unit_ids,
    )
    return GovernanceCatalog(
        owner=owner,
        load_profiles=tuple(
            sorted(load_profiles, key=lambda profile: profile.profile_id)
        ),
        skill_bindings=tuple(
            sorted(skill_bindings, key=lambda binding: binding.skill_id)
        ),
        context_profiles=tuple(
            sorted(context_profiles, key=lambda profile: profile.context_profile_id)
        ),
        schema=schema,
        governance_units=tuple(
            sorted(units, key=lambda unit: unit.governance_id)
        ),
        governance_index=tuple(
            sorted(index, key=_index_sort_key)
        ),
        governance_dependencies=tuple(
            sorted(
                dependencies,
                key=lambda dependency: (
                    dependency.governance_id,
                    dependency.requires_governance_id,
                ),
            )
        ),
        governance_overrides=tuple(
            sorted(overrides, key=_override_sort_key)
        ),
    )


def parse_release_catalog(
    value: Any,
    *,
    packaged_paths: Iterable[str],
) -> ReleaseProfileCatalog | GovernanceCatalog:
    if isinstance(value, dict) and value.get("schema") == PROFILE_CATALOG_SCHEMA:
        return parse_release_profile_catalog(
            value,
            packaged_paths=packaged_paths,
        )
    if isinstance(value, dict) and value.get("schema") in {
        GOVERNANCE_CATALOG_SCHEMA,
        CONTEXT_CATALOG_SCHEMA,
    }:
        return parse_release_governance_catalog(
            value,
            packaged_paths=packaged_paths,
        )
    raise ReleaseProfileError("release profile catalog schema is unsupported")


def select_governance(
    catalog: GovernanceCatalog,
    selector: GovernanceSelector | Mapping[str, Any],
) -> GovernanceSelection:
    if not isinstance(catalog, GovernanceCatalog):
        raise ReleaseProfileError(
            "governance selector requires a governance release profile catalog"
        )
    normalized_selector = _selector(selector, "selector")
    matches = tuple(
        sorted(
            (
                entry
                for entry in catalog.governance_index
                if entry.selector == normalized_selector
            ),
            key=lambda entry: (entry.priority, entry.governance_id),
        )
    )
    selected_ids = [entry.governance_id for entry in matches]
    selected = set(selected_ids)
    for override in catalog.governance_overrides:
        if (
            override.applies_when == normalized_selector
            and override.base_governance_id in selected
        ):
            selected.remove(override.base_governance_id)
            selected.add(override.overriding_governance_id)
            if override.overriding_governance_id not in selected_ids:
                selected_ids.append(override.overriding_governance_id)

    dependencies: dict[str, list[str]] = {}
    for dependency in catalog.governance_dependencies:
        dependencies.setdefault(dependency.governance_id, []).append(
            dependency.requires_governance_id
        )
    for values in dependencies.values():
        values.sort()

    closure: list[str] = []
    visited: set[str] = set()

    def visit(governance_id: str) -> None:
        if governance_id in visited:
            return
        for dependency_id in dependencies.get(governance_id, []):
            visit(dependency_id)
        visited.add(governance_id)
        closure.append(governance_id)

    for governance_id in selected_ids:
        if governance_id in selected:
            visit(governance_id)

    units_by_id = {
        unit.governance_id: unit for unit in catalog.governance_units
    }
    units = tuple(units_by_id[governance_id] for governance_id in closure)
    selector_digest = _digest(
        {
            "catalog_digest": catalog.digest,
            "selector": normalized_selector.as_dict(),
            "matched_entries": [entry.as_dict() for entry in matches],
            "dependency_closure": closure,
            "source_digests": [unit.source_digest for unit in units],
        }
    )
    return GovernanceSelection(
        catalog_digest=catalog.digest,
        selector=normalized_selector,
        matched_entries=matches,
        dependency_closure=tuple(closure),
        units=units,
        selector_digest=selector_digest,
    )


def _governance_units(
    value: Any,
    paths: frozenset[str],
) -> list[GovernanceUnit]:
    rows = _list(value, "governance_units")
    if not rows:
        raise ReleaseProfileError("governance_units must not be empty")
    units: list[GovernanceUnit] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        context = f"governance_units[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "governance_id",
            "kind",
            "source_ref",
            "source_digest",
            "compact_instruction",
        }:
            raise ReleaseProfileError(f"{context} fields are invalid")
        governance_id = _governance_id(
            item.get("governance_id"),
            f"{context}.governance_id",
        )
        if governance_id in seen:
            raise ReleaseProfileError(
                "governance catalog has duplicate governance IDs"
            )
        seen.add(governance_id)
        kind = _governance_kind(item.get("kind"), f"{context}.kind")
        source_ref = _packaged_source_ref(
            item.get("source_ref"),
            paths,
            f"{context}.source_ref",
        )
        source_digest = _source_digest(
            item.get("source_digest"),
            f"{context}.source_digest",
        )
        units.append(
            GovernanceUnit(
                governance_id=governance_id,
                kind=kind,
                source_ref=source_ref,
                source_digest=source_digest,
                compact_instruction=_text(
                    item.get("compact_instruction"),
                    f"{context}.compact_instruction",
                ),
            )
        )
    return units


def _governance_index(
    value: Any,
    unit_ids: set[str],
) -> list[GovernanceIndexEntry]:
    rows = _list(value, "governance_index")
    entries: list[GovernanceIndexEntry] = []
    seen: set[tuple[GovernanceSelector, str]] = set()
    required_fields = {
        *GOVERNANCE_SELECTOR_FIELDS,
        "governance_id",
        "required",
        "priority",
    }
    for index, item in enumerate(rows):
        context = f"governance_index[{index}]"
        if not isinstance(item, dict) or set(item) != required_fields:
            raise ReleaseProfileError(f"{context} fields are invalid")
        selector = _selector(
            {
                key: item[key]
                for key in GOVERNANCE_SELECTOR_FIELDS
            },
            context,
        )
        governance_id = _governance_id(
            item.get("governance_id"),
            f"{context}.governance_id",
        )
        if governance_id not in unit_ids:
            raise ReleaseProfileError(
                f"{context} references an unknown governance unit"
            )
        required = item.get("required")
        if not isinstance(required, bool):
            raise ReleaseProfileError(f"{context}.required must be boolean")
        priority = item.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
            raise ReleaseProfileError(
                f"{context}.priority must be a non-negative integer"
            )
        identity = (selector, governance_id)
        if identity in seen:
            raise ReleaseProfileError(
                "governance catalog has duplicate selector entries"
            )
        seen.add(identity)
        entries.append(
            GovernanceIndexEntry(
                selector=selector,
                governance_id=governance_id,
                required=required,
                priority=priority,
            )
        )
    return entries


def _governance_dependencies(
    value: Any,
    unit_ids: set[str],
) -> list[GovernanceDependency]:
    rows = _list(value, "governance_dependencies")
    dependencies: list[GovernanceDependency] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(rows):
        context = f"governance_dependencies[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "governance_id",
            "requires_governance_id",
        }:
            raise ReleaseProfileError(f"{context} fields are invalid")
        governance_id = _governance_id(
            item.get("governance_id"),
            f"{context}.governance_id",
        )
        requires_id = _governance_id(
            item.get("requires_governance_id"),
            f"{context}.requires_governance_id",
        )
        if governance_id not in unit_ids or requires_id not in unit_ids:
            raise ReleaseProfileError(
                f"{context} references an unknown governance unit"
            )
        if governance_id == requires_id:
            raise ReleaseProfileError(
                f"{context} must not depend on itself"
            )
        identity = (governance_id, requires_id)
        if identity in seen:
            raise ReleaseProfileError(
                "governance catalog has duplicate dependencies"
            )
        seen.add(identity)
        dependencies.append(
            GovernanceDependency(
                governance_id=governance_id,
                requires_governance_id=requires_id,
            )
        )
    return dependencies


def _governance_overrides(
    value: Any,
    unit_ids: set[str],
) -> list[GovernanceOverride]:
    rows = _list(value, "governance_overrides")
    overrides: list[GovernanceOverride] = []
    seen: set[tuple[str, str, GovernanceSelector]] = set()
    for index, item in enumerate(rows):
        context = f"governance_overrides[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "base_governance_id",
            "overriding_governance_id",
            "applies_when",
        }:
            raise ReleaseProfileError(f"{context} fields are invalid")
        base_id = _governance_id(
            item.get("base_governance_id"),
            f"{context}.base_governance_id",
        )
        overriding_id = _governance_id(
            item.get("overriding_governance_id"),
            f"{context}.overriding_governance_id",
        )
        if base_id not in unit_ids or overriding_id not in unit_ids:
            raise ReleaseProfileError(
                f"{context} references an unknown governance unit"
            )
        if base_id == overriding_id:
            raise ReleaseProfileError(
                f"{context} must not override itself"
            )
        applies_when = _selector(item.get("applies_when"), f"{context}.applies_when")
        identity = (base_id, overriding_id, applies_when)
        if identity in seen:
            raise ReleaseProfileError(
                "governance catalog has duplicate overrides"
            )
        seen.add(identity)
        overrides.append(
            GovernanceOverride(
                base_governance_id=base_id,
                overriding_governance_id=overriding_id,
                applies_when=applies_when,
            )
        )
    return overrides


def _validate_dependency_graph(
    dependencies: list[GovernanceDependency],
) -> None:
    graph: dict[str, list[str]] = {}
    for dependency in dependencies:
        graph.setdefault(dependency.governance_id, []).append(
            dependency.requires_governance_id
        )
    for values in graph.values():
        values.sort()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(governance_id: str) -> None:
        if governance_id in visiting:
            raise ReleaseProfileError("governance dependency graph has a cycle")
        if governance_id in visited:
            return
        visiting.add(governance_id)
        for dependency_id in graph.get(governance_id, []):
            visit(dependency_id)
        visiting.remove(governance_id)
        visited.add(governance_id)

    for governance_id in sorted(graph):
        visit(governance_id)


def _selector(value: Any, field: str) -> GovernanceSelector:
    if isinstance(value, GovernanceSelector):
        return value
    if not isinstance(value, Mapping) or set(value) != set(GOVERNANCE_SELECTOR_FIELDS):
        raise ReleaseProfileError(f"{field} fields are invalid")
    values = {
        key: _selector_text(value.get(key), f"{field}.{key}")
        for key in GOVERNANCE_SELECTOR_FIELDS
    }
    return GovernanceSelector(**values)


def _selector_text(value: Any, field: str) -> str:
    result = _text(value, field).upper()
    if PROFILE_ID_PATTERN.fullmatch(result) is None:
        raise ReleaseProfileError(f"{field} is invalid")
    return result


def _governance_id(value: Any, field: str) -> str:
    result = _text(value, field).upper()
    if PROFILE_ID_PATTERN.fullmatch(result) is None:
        raise ReleaseProfileError(f"{field} is invalid")
    return result


def _governance_kind(value: Any, field: str) -> str:
    result = _text(value, field).upper()
    if GOVERNANCE_KIND_PATTERN.fullmatch(result) is None:
        raise ReleaseProfileError(f"{field} is invalid")
    return result


def _source_digest(value: Any, field: str) -> str:
    result = _text(value, field)
    if SOURCE_DIGEST_PATTERN.fullmatch(result) is None:
        raise ReleaseProfileError(f"{field} is invalid")
    return result


def _packaged_source_ref(
    value: Any,
    paths: frozenset[str],
    field: str,
) -> str:
    result = _text(value, field)
    path = PurePosixPath(result)
    if (
        "\\" in result
        or "\x00" in result
        or ":" in result
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != result
    ):
        raise ReleaseProfileError(f"{field} is not a canonical source path")
    if result not in paths:
        raise ReleaseProfileError(f"{field} is not packaged: {result}")
    return result


def _index_sort_key(entry: GovernanceIndexEntry) -> tuple[Any, ...]:
    return (
        *tuple(entry.selector.as_dict()[field] for field in GOVERNANCE_SELECTOR_FIELDS),
        entry.priority,
        entry.governance_id,
    )


def _override_sort_key(override: GovernanceOverride) -> tuple[Any, ...]:
    return (
        override.base_governance_id,
        override.overriding_governance_id,
        *tuple(
            override.applies_when.as_dict()[field]
            for field in GOVERNANCE_SELECTOR_FIELDS
        ),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _load_profiles(value: Any, paths: frozenset[str]) -> list[LoadProfile]:
    rows = _list(value, "load_profiles")
    profiles: list[LoadProfile] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        context = f"load_profiles[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "profile_id",
            "description",
            "surfaces",
        }:
            raise ReleaseProfileError(f"{context} fields are invalid")
        profile_id = _profile_id(item.get("profile_id"), f"{context}.profile_id")
        if profile_id in seen:
            raise ReleaseProfileError("release profile catalog has duplicate profile IDs")
        seen.add(profile_id)
        surface_rows = _list(item.get("surfaces"), f"{context}.surfaces")
        if not surface_rows:
            raise ReleaseProfileError(f"{context}.surfaces must not be empty")
        surfaces: list[ProfileSurface] = []
        surface_paths: set[str] = set()
        for ordinal, surface in enumerate(surface_rows):
            surface_context = f"{context}.surfaces[{ordinal}]"
            if not isinstance(surface, dict) or set(surface) != {"path", "required"}:
                raise ReleaseProfileError(f"{surface_context} fields are invalid")
            path = _text(surface.get("path"), f"{surface_context}.path")
            required = surface.get("required")
            if not isinstance(required, bool):
                raise ReleaseProfileError(f"{surface_context}.required must be boolean")
            if path not in paths:
                raise ReleaseProfileError(
                    f"release profile surface is not packaged: {path}"
                )
            if path in surface_paths:
                raise ReleaseProfileError(
                    f"release profile contains duplicate surface: {path}"
                )
            surface_paths.add(path)
            surfaces.append(ProfileSurface(path=path, required=required))
        profiles.append(
            LoadProfile(
                profile_id=profile_id,
                description=_text(item.get("description"), f"{context}.description"),
                surfaces=tuple(surfaces),
            )
        )
    return profiles


def _skill_bindings(
    value: Any,
    profile_ids: set[str],
) -> list[SkillBinding]:
    rows = _list(value, "skill_bindings")
    bindings: list[SkillBinding] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        context = f"skill_bindings[{index}]"
        if not isinstance(item, dict) or set(item) != {"skill_id", "profile_id"}:
            raise ReleaseProfileError(f"{context} fields are invalid")
        skill_id = _text(item.get("skill_id"), f"{context}.skill_id")
        if SKILL_ID_PATTERN.fullmatch(skill_id) is None:
            raise ReleaseProfileError(f"{context}.skill_id is invalid")
        profile_id = _profile_id(item.get("profile_id"), f"{context}.profile_id")
        if profile_id not in profile_ids:
            raise ReleaseProfileError(f"{context} references an unknown load profile")
        if skill_id in seen:
            raise ReleaseProfileError("release profile catalog has duplicate skill IDs")
        seen.add(skill_id)
        bindings.append(SkillBinding(skill_id=skill_id, profile_id=profile_id))
    return bindings


def _mode_profiles(
    value: Any,
    profile_ids: set[str],
) -> list[ModeProfile]:
    rows = _list(value, "mode_profiles")
    profiles: list[ModeProfile] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        context = f"mode_profiles[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "mode_profile_id",
            "overlay_policy",
            "load_profiles",
        }:
            raise ReleaseProfileError(f"{context} fields are invalid")
        mode_profile_id = _profile_id(
            item.get("mode_profile_id"),
            f"{context}.mode_profile_id",
        )
        if mode_profile_id in seen:
            raise ReleaseProfileError("release profile catalog has duplicate Mode profiles")
        seen.add(mode_profile_id)
        overlay_policy = _text(
            item.get("overlay_policy"),
            f"{context}.overlay_policy",
        ).upper()
        if overlay_policy not in OVERLAY_POLICIES:
            raise ReleaseProfileError(f"{context}.overlay_policy is unsupported")
        load_profile_ids = [
            _profile_id(candidate, f"{context}.load_profiles[]")
            for candidate in _list(item.get("load_profiles"), f"{context}.load_profiles")
        ]
        if not load_profile_ids:
            raise ReleaseProfileError(f"{context}.load_profiles must not be empty")
        if len(load_profile_ids) != len(set(load_profile_ids)):
            raise ReleaseProfileError(f"{context}.load_profiles contains duplicates")
        if not set(load_profile_ids).issubset(profile_ids):
            raise ReleaseProfileError(f"{context} references an unknown load profile")
        profiles.append(
            ModeProfile(
                mode_profile_id=mode_profile_id,
                overlay_policy=overlay_policy,
                load_profiles=tuple(load_profile_ids),
            )
        )
    return profiles


def _context_profiles(
    value: Any,
    profile_ids: set[str],
) -> list[ContextProfile]:
    rows = _list(value, "context_profiles")
    profiles: list[ContextProfile] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        context = f"context_profiles[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "context_profile_id",
            "overlay_policy",
            "load_profiles",
        }:
            raise ReleaseProfileError(f"{context} fields are invalid")
        context_profile_id = _profile_id(
            item.get("context_profile_id"),
            f"{context}.context_profile_id",
        )
        if context_profile_id in seen:
            raise ReleaseProfileError(
                "release profile catalog has duplicate context profiles"
            )
        seen.add(context_profile_id)
        overlay_policy = _text(
            item.get("overlay_policy"),
            f"{context}.overlay_policy",
        ).upper()
        if overlay_policy not in OVERLAY_POLICIES:
            raise ReleaseProfileError(f"{context}.overlay_policy is unsupported")
        load_profile_ids = [
            _profile_id(candidate, f"{context}.load_profiles[]")
            for candidate in _list(
                item.get("load_profiles"), f"{context}.load_profiles"
            )
        ]
        if not load_profile_ids:
            raise ReleaseProfileError(f"{context}.load_profiles must not be empty")
        if len(load_profile_ids) != len(set(load_profile_ids)):
            raise ReleaseProfileError(f"{context}.load_profiles contains duplicates")
        if not set(load_profile_ids).issubset(profile_ids):
            raise ReleaseProfileError(
                f"{context} references an unknown load profile"
            )
        profiles.append(
            ContextProfile(
                context_profile_id=context_profile_id,
                overlay_policy=overlay_policy,
                load_profiles=tuple(load_profile_ids),
            )
        )
    return profiles


def _profile_id(value: Any, field: str) -> str:
    result = _text(value, field).upper()
    if PROFILE_ID_PATTERN.fullmatch(result) is None:
        raise ReleaseProfileError(f"{field} is invalid")
    return result


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseProfileError(f"{field} must be non-empty text")
    return value.strip()


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReleaseProfileError(f"{field} must be an array")
    return value
