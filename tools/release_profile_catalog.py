from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


PROFILE_CATALOG_SCHEMA = "ai-career.release-profile-catalog.v1"
PROFILE_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
OVERLAY_POLICIES = frozenset({"APPEND_ONLY", "NONE"})


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
class ReleaseProfileCatalog:
    owner: str
    load_profiles: tuple[LoadProfile, ...]
    skill_bindings: tuple[SkillBinding, ...]
    mode_profiles: tuple[ModeProfile, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": PROFILE_CATALOG_SCHEMA,
            "owner": self.owner,
            "load_profiles": [profile.as_dict() for profile in self.load_profiles],
            "skill_bindings": [binding.as_dict() for binding in self.skill_bindings],
            "mode_profiles": [profile.as_dict() for profile in self.mode_profiles],
        }

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
    owner = _text(value.get("owner"), "owner")
    paths = frozenset(packaged_paths)
    load_profiles = _load_profiles(value.get("load_profiles"), paths)
    profile_ids = {profile.profile_id for profile in load_profiles}
    skill_bindings = _skill_bindings(value.get("skill_bindings"), profile_ids)
    mode_profiles = _mode_profiles(value.get("mode_profiles"), profile_ids)
    return ReleaseProfileCatalog(
        owner=owner,
        load_profiles=tuple(
            sorted(load_profiles, key=lambda profile: profile.profile_id)
        ),
        skill_bindings=tuple(
            sorted(skill_bindings, key=lambda binding: binding.skill_id)
        ),
        mode_profiles=tuple(
            sorted(mode_profiles, key=lambda profile: profile.mode_profile_id)
        ),
    )


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
