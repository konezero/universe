"""Deterministic ownership and source-routing primitives for Memory RAG.

The Memory tables intentionally keep the conversation/project that collected a
candidate separate from the project that may eventually own the knowledge.
This module contains only validation and projections; it does not perform
database writes and it never treats candidate text as an instruction.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


MEMORY_OWNERSHIP_SCHEMA = "universe.memory-candidate-ownership.v1"
PROJECT_OWNERSHIP_CATALOG_SCHEMA = "universe.project-ownership-catalog.v1"
UNASSIGNED = "UNASSIGNED"
OWNERSHIP_STATES = frozenset({"ASSIGNED", "PROPOSED", "UNASSIGNED", "CONFLICTED"})
JUDGMENTS = frozenset(
    {"CURRENT", "FUTURE", "PAST_HISTORY", "OUTDATED", "INCORRECT", "NOISE", "UNVERIFIED"}
)
OPEN_TODO_STATES = frozenset({"BACKLOG", "READY", "IN_PROGRESS", "BLOCKED"})
_COMPACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_NOISE_MARKERS = (
    "memory-exact-codex-cli-response",
    "test response",
    "test-only",
    "test only",
    "fixture",
    "one-shot test",
    "일회성 테스트",
    "테스트 응답",
)
_PAST_MARKERS = (
    "past history",
    "historical",
    "previously",
    "과거 이력",
    "지난 기록",
    "완료된 작업",
    "completed todo",
)


def _text(value: Any, *, maximum: int = 256) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split()).strip()
    return value[:maximum] if value else ""


def _project_id(value: Any) -> str:
    value = _text(value, maximum=128)
    return value if _COMPACT_ID.fullmatch(value) else ""


def _root(value: Any) -> str:
    text = _text(value, maximum=1024)
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        return ""


def _root_key(value: Any) -> str:
    return os.path.normcase(_root(value)).casefold()


def _metadata(project: Mapping[str, Any]) -> dict[str, Any]:
    value = project.get("metadata")
    return dict(value) if isinstance(value, Mapping) else {}


def _aliases(project: Mapping[str, Any]) -> set[str]:
    metadata = _metadata(project)
    values: list[Any] = [project.get("project_id")]
    for key in ("legacy_project_ids", "aliases"):
        raw = metadata.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            values.extend(raw)
    for key in ("alias", "migrated_from", "migrated_to_project_id"):
        values.append(metadata.get(key))
    return {
        item.casefold()
        for raw in values
        if (item := _project_id(raw))
    }


def _is_owner_project(project: Mapping[str, Any]) -> bool:
    project_id = _project_id(project.get("project_id"))
    metadata = _metadata(project)
    visibility = _text(metadata.get("visibility"), maximum=64).upper()
    network_role = _text(metadata.get("network_role"), maximum=64).upper()
    node_kind = _text(metadata.get("node_kind"), maximum=64).upper()
    return bool(
        project_id
        and visibility != "MIGRATED_LEGACY"
        and network_role not in {"NETWORK_ANCHOR", "CONTAINER"}
        and node_kind not in {"CONTAINER", "NETWORK_ANCHOR"}
        and project_id.casefold() != "universe-private"
    )


def build_project_catalog(projects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build exact id/alias/root maps from the registered project catalog."""

    entries: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    roots: dict[str, str] = {}
    for raw in projects:
        if not isinstance(raw, Mapping):
            continue
        project_id = _project_id(raw.get("project_id"))
        root = _root(raw.get("project_root"))
        if not project_id or not root:
            continue
        metadata = _metadata(raw)
        entry = {
            "project_id": project_id,
            "project_root": root,
            "metadata": metadata,
            "aliases": sorted(_aliases(raw)),
            "owner_eligible": _is_owner_project(raw),
            "visibility": _text(metadata.get("visibility"), maximum=64).upper() or "ACTIVE",
            "network_role": _text(metadata.get("network_role"), maximum=64).upper() or "UNKNOWN",
        }
        entries[project_id] = entry
    for project_id, entry in entries.items():
        target = _project_id(entry["metadata"].get("migrated_to_project_id"))
        target_entry = entries.get(target) if target else None
        root_owner = (
            target
            if target_entry is not None and target_entry["owner_eligible"]
            else project_id
        )
        roots[_root_key(entry["project_root"])] = root_owner
        for alias in entry["aliases"]:
            if target_entry is not None and target_entry["owner_eligible"]:
                aliases[alias] = target
            elif entry["owner_eligible"]:
                aliases[alias] = project_id
    return {
        "schema": PROJECT_OWNERSHIP_CATALOG_SCHEMA,
        "projects": entries,
        "aliases": aliases,
        "roots": roots,
        "owner_project_ids": sorted(
            project_id
            for project_id, entry in entries.items()
            if entry["owner_eligible"]
        ),
    }


def resolve_registered_project(value: Any, catalog: Mapping[str, Any]) -> str | None:
    """Resolve only an exact registered id, alias, or canonical absolute root."""

    if isinstance(value, Mapping):
        for key in ("project_id", "owner_project_id", "origin_project_id", "workspace", "project_root"):
            resolved = resolve_registered_project(value.get(key), catalog)
            if resolved is not None:
                return resolved
        return None
    text = _text(value, maximum=1024)
    if not text:
        return None
    entries = catalog.get("projects") if isinstance(catalog, Mapping) else {}
    if not isinstance(entries, Mapping):
        return None
    aliases = catalog.get("aliases") if isinstance(catalog, Mapping) else {}
    if isinstance(aliases, Mapping):
        resolved = aliases.get(text.casefold())
        if isinstance(resolved, str) and resolved in entries:
            return resolved
    for project_id in entries:
        if str(project_id).casefold() == text.casefold():
            return str(project_id)
    root = _root_key(text)
    roots = catalog.get("roots") if isinstance(catalog, Mapping) else {}
    resolved = roots.get(root) if isinstance(roots, Mapping) else None
    return str(resolved) if isinstance(resolved, str) and resolved in entries else None


def _owner_eligible(catalog: Mapping[str, Any], project_id: str | None) -> str | None:
    if not project_id:
        return None
    entries = catalog.get("projects")
    entry = entries.get(project_id) if isinstance(entries, Mapping) else None
    return project_id if isinstance(entry, Mapping) and entry.get("owner_eligible") else None


def canonical_source_roots(
    projects: Sequence[Mapping[str, Any]], requested_project_id: str
) -> list[dict[str, Any]]:
    """Return the primary project plus the registered canonical Runtime source.

    The Career source is special because it is the installed Runtime/policy
    source.  Its ``.ai`` tree is eligible; ``.ai`` trees in product projects
    remain excluded by the source-review collector.  Migrated legacy projects
    and the Universe network container are never added as source roots.
    """

    catalog = build_project_catalog(projects)
    requested = resolve_registered_project(requested_project_id, catalog)
    if requested is None:
        requested = _project_id(requested_project_id) or requested_project_id
    entries = catalog.get("projects") or {}
    primary = entries.get(requested)
    roots: list[dict[str, Any]] = []
    if isinstance(primary, Mapping):
        roots.append(
            {
                "project_id": primary["project_id"],
                "root": primary["project_root"],
                "role": "PRIMARY_PROJECT",
                "include_runtime_policy": primary["project_id"].casefold() == "career"
                or primary.get("network_role") == "CAREER_SOURCE",
            }
        )
    for project_id in sorted(entries):
        entry = entries[project_id]
        if not entry.get("owner_eligible") or project_id == requested:
            continue
        is_runtime_source = (
            project_id.casefold() == "career"
            or entry.get("network_role") == "CAREER_SOURCE"
        )
        if is_runtime_source:
            roots.append(
                {
                    "project_id": project_id,
                    "root": entry["project_root"],
                    "role": "CANONICAL_RUNTIME_SOURCE",
                    "include_runtime_policy": True,
                }
            )
    return roots


def resolve_source_ownership(
    source: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    project_id: Any = None,
) -> dict[str, Any]:
    """Resolve a provider source from explicit scope or exact workspace evidence."""

    context = resolve_registered_project(project_id, catalog)
    context_owner = _owner_eligible(catalog, context)
    origin = resolve_registered_project(
        source.get("origin_project_id")
        or source.get("conversation_project_id")
        or source.get("project_id"),
        catalog,
    )
    if origin is None and context is not None:
        origin = context
    workspace = source.get("workspace") or source.get("workspace_path")
    workspace_project = resolve_registered_project(workspace, catalog)
    workspace_owner = _owner_eligible(catalog, workspace_project)
    if origin is None and workspace_project is not None:
        origin = workspace_project
    explicit_owner = source.get("owner_project_id")
    explicit_owner_project = (
        resolve_registered_project(explicit_owner, catalog) if explicit_owner else None
    )
    explicit_owner_resolved = _owner_eligible(catalog, explicit_owner_project)
    evidence: list[dict[str, Any]] = []
    if context is not None:
        evidence.append({"kind": "EXPLICIT_REGISTERED_PROJECT_SCOPE", "project_id": context})
    if workspace_owner is not None:
        evidence.append(
            {
                "kind": "CANONICAL_WORKSPACE_ROOT_MATCH",
                "project_id": workspace_owner,
            }
        )
    if explicit_owner and explicit_owner_resolved is None:
        evidence.append({"kind": "OWNER_PROJECT_UNREGISTERED", "value": _text(explicit_owner)})
    conflict = False
    if explicit_owner_resolved and workspace_owner and explicit_owner_resolved != workspace_owner:
        conflict = True
    elif (
        not explicit_owner
        and context_owner
        and workspace_owner
        and context_owner != workspace_owner
    ):
        conflict = True
    if conflict:
        evidence.append(
            {
                "kind": "OWNERSHIP_EVIDENCE_CONFLICT",
                "scope_project_id": context_owner or context,
                "workspace_project_id": workspace_owner,
                "explicit_owner_project_id": explicit_owner_resolved,
            }
        )
        owner = None
        state = "CONFLICTED"
        proposed = workspace_owner or explicit_owner_resolved or UNASSIGNED
    else:
        owner = explicit_owner_resolved or context_owner or workspace_owner
        if owner is not None:
            state = "ASSIGNED"
            proposed = owner
        elif origin is not None and _owner_eligible(catalog, origin):
            state = "PROPOSED"
            proposed = origin
        else:
            state = "UNASSIGNED"
            proposed = UNASSIGNED
    if owner is not None:
        state = "ASSIGNED"
        proposed = owner
    return {
        "origin_project_id": origin or UNASSIGNED,
        "proposed_owner_project_id": proposed,
        "owner_project_id": owner or UNASSIGNED,
        "ownership_state": state,
        "ownership_evidence": {"schema": MEMORY_OWNERSHIP_SCHEMA, "items": evidence},
        "reason": (
            "source is assigned from an exact registered project scope or canonical workspace root"
            if owner
            else "source has conflicting registered scope and canonical workspace evidence; held for routing"
            if state == "CONFLICTED"
            else "source ownership is not attested; kept in the common holding queue"
        ),
    }


def _candidate_provenance(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = candidate.get("provenance")
    return value if isinstance(value, Mapping) else {}


def _quality_judgment(candidate: Mapping[str, Any]) -> str:
    summary = _text(candidate.get("summary") or candidate.get("title"), maximum=1600).casefold()
    if any(marker in summary for marker in _NOISE_MARKERS):
        return "NOISE"
    provenance = _candidate_provenance(candidate)
    temporality = _text(candidate.get("temporality") or provenance.get("temporality"), maximum=64).upper()
    if temporality in JUDGMENTS:
        return temporality
    assessment = candidate.get("source_review")
    status = _text(assessment.get("status") if isinstance(assessment, Mapping) else "", maximum=64).upper()
    if status in {"CURRENT", "FUTURE", "PAST_HISTORY", "OUTDATED", "INCORRECT", "NOISE"}:
        return status
    if any(marker in summary for marker in _PAST_MARKERS):
        return "PAST_HISTORY"
    return "UNVERIFIED"


def classify_candidate_ownership(
    candidate: Mapping[str, Any], catalog: Mapping[str, Any]
) -> dict[str, Any]:
    """Produce a non-mutating ownership/quality record for one candidate."""

    conversation = resolve_registered_project(candidate.get("project_id"), catalog)
    provenance = _candidate_provenance(candidate)
    origin = resolve_registered_project(
        candidate.get("origin_project_id")
        or provenance.get("origin_project_id")
        or candidate.get("project_id"),
        catalog,
    )
    explicit_owner_value = candidate.get("owner_project_id") or provenance.get("owner_project_id")
    explicit_owner_project = (
        resolve_registered_project(explicit_owner_value, catalog)
        if explicit_owner_value
        else None
    )
    owner = _owner_eligible(catalog, explicit_owner_project)
    proposed = None
    source_evidence_owner = False
    if owner is None:
        assessment = candidate.get("source_review")
        evidence_items = assessment.get("evidence") if isinstance(assessment, Mapping) else []
        if isinstance(evidence_items, Sequence):
            for item in evidence_items:
                if not isinstance(item, Mapping):
                    continue
                proposed = _owner_eligible(
                    catalog,
                    resolve_registered_project(
                        item.get("root_project_id") or item.get("project_id"), catalog
                    ),
                )
                if proposed is not None:
                    break
    if proposed is not None and conversation == proposed:
        # A source-review record that cites the same registered project is an
        # exact ownership attestation.  Evidence rooted in another project is
        # deliberately left as a proposal for that project's Master instead.
        owner = proposed
        source_evidence_owner = True
    if owner is not None:
        state = "ASSIGNED"
        proposed_owner = owner
    elif proposed is not None:
        state = "PROPOSED"
        proposed_owner = proposed
    else:
        state = "UNASSIGNED"
        proposed_owner = UNASSIGNED
    judgment = _quality_judgment(candidate)
    candidate_id = _text(candidate.get("candidate_id"), maximum=256) or "UNKNOWN"
    candidate_digest = _text(candidate.get("candidate_digest"), maximum=128) or "UNKNOWN"
    try:
        revision = int(candidate.get("revision") or 1)
    except (TypeError, ValueError):
        revision = 1
    evidence: list[dict[str, Any]] = [
        {
            "kind": "CANDIDATE_IDENTITY",
            "candidate_id": candidate_id,
            "candidate_digest": candidate_digest,
            "revision": revision,
        }
    ]
    if conversation:
        evidence.append({"kind": "CONVERSATION_PROJECT", "project_id": conversation})
    if origin:
        evidence.append({"kind": "ORIGIN_PROJECT", "project_id": origin})
    source_id = _text(provenance.get("source_id"), maximum=256)
    if source_id:
        evidence.append({"kind": "PROVIDER_SOURCE", "source_id": source_id})
    source_ref = _text(provenance.get("source_ref"), maximum=512)
    if source_ref:
        evidence.append({"kind": "SOURCE_REF", "source_ref": source_ref})
    assessment = candidate.get("source_review")
    if isinstance(assessment, Mapping):
        for key in ("source_commit", "source_digest"):
            value = _text(assessment.get(key), maximum=128)
            if value:
                evidence.append({"kind": key.upper(), key: value})
    if state == "UNASSIGNED":
        evidence.append(
            {
                "kind": "MISSING_OWNERSHIP_ATTESTATION",
                "detail": "registered owner project or canonical source evidence is required",
            }
        )
    reason_parts = []
    if state == "ASSIGNED":
        reason_parts.append(
            "owner is attested by registered project scope or matching canonical source evidence"
        )
    elif state == "PROPOSED":
        reason_parts.append("source evidence suggests an owner, but adoption remains with that project's Master")
    else:
        reason_parts.append("owner is not verified; candidate remains in the common holding queue")
    if judgment == "NOISE":
        reason_parts.append("summary matches a one-shot/test-noise marker")
    elif judgment == "UNVERIFIED":
        reason_parts.append("current source or open-TODO evidence is not attested")
    elif judgment == "PAST_HISTORY":
        reason_parts.append("record is classified as past history, not an open plan")
    if source_evidence_owner:
        evidence.append(
            {
                "kind": "SOURCE_EVIDENCE_MATCHES_CONVERSATION_PROJECT",
                "project_id": conversation,
            }
        )
    return {
        "schema": MEMORY_OWNERSHIP_SCHEMA,
        "candidate_id": candidate_id,
        "candidate_digest": candidate_digest,
        "candidate_revision": revision,
        "conversation_project_id": conversation or UNASSIGNED,
        "origin_project_id": origin or UNASSIGNED,
        "proposed_owner_project_id": proposed_owner,
        "owner_project_id": owner or UNASSIGNED,
        "ownership_state": state,
        "judgment": judgment,
        "reason": "; ".join(reason_parts)[:1200],
        "evidence": evidence,
        "auto_adoption": False,
    }


def candidate_owned_by(candidate: Mapping[str, Any], project_id: str) -> bool:
    ownership = candidate.get("ownership")
    if not isinstance(ownership, Mapping):
        return False
    return (
        str(ownership.get("ownership_state") or "").upper() == "ASSIGNED"
        and str(ownership.get("owner_project_id") or "") == str(project_id)
    )
