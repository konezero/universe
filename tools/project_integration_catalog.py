"""Verified Universe-owned project-integration template catalog."""

from __future__ import annotations

import hashlib
import json
import base64
from pathlib import Path
import re
from typing import Any, Mapping


CATALOG_SCHEMA = "universe.project-integration-catalog.v1"
CATALOG_STATUS = "PROJECT_INTEGRATION_CATALOG_READY"
PROPOSAL_SCHEMA = "universe.project-integration-proposal.v1"
CATALOG_RELATIVE_ROOT = Path("templates") / "project-integration"
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
TEMPLATE_SPECS: Mapping[str, tuple[str, str]] = {
    "project_binding": ("project-binding.example.json", ".universe/project.json"),
    "install_binding": (
        "install-binding.example.json",
        ".ai/universe/install_binding.json",
    ),
    "todo_policy": ("TODO_TRACKING_POLICY.md", ".ai/universe/TODO_TRACKING_POLICY.md"),
    "connection": ("universe-connection.md", ".ai/universe/connection.md"),
    "node_memory": ("node-memory.md", ".ai/memory/universe_nodes/README.md"),
}


class ProjectIntegrationCatalogError(ValueError):
    pass


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _catalog_root(root: Path | None) -> Path:
    repository_root = (
        root.expanduser().resolve(strict=True)
        if root is not None
        else Path(__file__).resolve().parents[1]
    )
    catalog_root = repository_root / CATALOG_RELATIVE_ROOT
    if not catalog_root.is_dir() or catalog_root.is_symlink():
        raise ProjectIntegrationCatalogError("PROJECT_INTEGRATION_CATALOG_UNAVAILABLE")
    return catalog_root


def _read_template(catalog_root: Path, filename: str) -> bytes:
    path = catalog_root / filename
    if not path.is_file() or path.is_symlink():
        raise ProjectIntegrationCatalogError("PROJECT_INTEGRATION_TEMPLATE_UNAVAILABLE")
    return path.read_bytes()


def _validate_project_binding(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectIntegrationCatalogError(
            "PROJECT_INTEGRATION_BINDING_INVALID"
        ) from error
    if not isinstance(value, dict):
        raise ProjectIntegrationCatalogError("PROJECT_INTEGRATION_BINDING_INVALID")
    expected = {
        "schema": "universe.project-binding.v1",
        "project_id": "<PROJECT_ID>",
        "workspace_path": ".ai",
        "workspace_tracking": "LOCAL_ONLY",
        "runtime_owner": "universe",
        "standalone_install": "SUPPORTED",
    }
    if value != expected:
        raise ProjectIntegrationCatalogError("PROJECT_INTEGRATION_BINDING_INVALID")
    return value


def _validate_install_binding(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectIntegrationCatalogError(
            "PROJECT_INTEGRATION_INSTALL_BINDING_INVALID"
        ) from error
    expected = {
        "schema": "universe.install-binding.v1",
        "install_mode": "UNIVERSE_ATTACHED",
        "prefer_boot": "HOST",
        "project_id": "<PROJECT_ID>",
        "project_root": ".",
        "runtime_pin": {
            "kind": "CAREER_RELEASE_OR_HOST",
            "release_id": None,
            "manifest_digest": None,
            "note": "Attached mode follows the installed Career Runtime and Universe host.",
        },
        "career_source": {"project_id": "ai-career", "role": "CAREER_SOURCE"},
        "universe_host": {
            "discovery": "LOCAL_SERVER_JSON",
            "state_file_hint": "%LOCALAPPDATA%\\Universe\\server.json",
        },
        "standalone": {
            "enabled": False,
            "embed_runtime": False,
            "boot_entry": None,
        },
    }
    if value != expected:
        raise ProjectIntegrationCatalogError(
            "PROJECT_INTEGRATION_INSTALL_BINDING_INVALID"
        )
    return value


def load_project_integration_catalog(root: Path | None = None) -> dict[str, Any]:
    """Load the Universe-owned catalog without materializing Project files."""

    catalog_root = _catalog_root(root)
    templates: list[dict[str, Any]] = []
    binding: dict[str, Any] | None = None
    install_binding: dict[str, Any] | None = None
    digest_material: list[dict[str, str]] = []
    for template_id, (filename, target_path) in TEMPLATE_SPECS.items():
        content = _read_template(catalog_root, filename)
        digest = _sha256(content)
        templates.append(
            {
                "template_id": template_id,
                "source_path": (CATALOG_RELATIVE_ROOT / filename).as_posix(),
                "target_path": target_path,
                "sha256": digest,
                "size": len(content),
            }
        )
        digest_material.append({"template_id": template_id, "sha256": digest})
        if template_id == "project_binding":
            binding = _validate_project_binding(content)
        if template_id == "install_binding":
            install_binding = _validate_install_binding(content)
    if binding is None or install_binding is None:
        raise ProjectIntegrationCatalogError("PROJECT_INTEGRATION_BINDING_INVALID")
    catalog_digest = _sha256(
        json.dumps(
            digest_material,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return {
        "schema": CATALOG_SCHEMA,
        "status": CATALOG_STATUS,
        "catalog_root": CATALOG_RELATIVE_ROOT.as_posix(),
        "catalog_digest": catalog_digest,
        "project_binding": binding,
        "install_binding": install_binding,
        "templates": templates,
        "effects": {
            "project_source_write": "NONE",
            "project_runtime_state_write": "NONE",
            "career_release_write": "NONE",
        },
    }


def build_project_integration_proposal(
    project_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build exact template assets without writing a Project."""

    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ProjectIntegrationCatalogError("PROJECT_INTEGRATION_PROJECT_ID_INVALID")
    catalog_root = _catalog_root(root)
    catalog = load_project_integration_catalog(root)
    binding = dict(catalog["project_binding"])
    binding["project_id"] = project_id
    binding_content = (
        json.dumps(binding, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    install_binding = dict(catalog["install_binding"])
    install_binding["project_id"] = project_id
    install_binding_content = (
        json.dumps(install_binding, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    payloads = {
        ".universe/project.json": binding_content,
        ".ai/universe/install_binding.json": install_binding_content,
        ".ai/universe/TODO_TRACKING_POLICY.md": _read_template(
            catalog_root, "TODO_TRACKING_POLICY.md"
        ),
        ".ai/universe/connection.md": _read_template(
            catalog_root, "universe-connection.md"
        ),
        ".ai/memory/universe_nodes/README.md": _read_template(
            catalog_root, "node-memory.md"
        ),
    }
    assets = []
    for target_path, content in sorted(payloads.items()):
        scope = "PROJECT_SOURCE" if target_path.startswith(".universe/") else "LOCAL_RUNTIME"
        assets.append(
            {
                "target_path": target_path,
                "scope": scope,
                "operation": "CREATE_OR_REPLACE",
                "sha256": _sha256(content),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    material = {
        "schema": PROPOSAL_SCHEMA,
        "project_id": project_id,
        "catalog_digest": catalog["catalog_digest"],
        "assets": [
            {
                "target_path": asset["target_path"],
                "scope": asset["scope"],
                "operation": asset["operation"],
                "sha256": asset["sha256"],
            }
            for asset in assets
        ],
    }
    proposal_digest = _sha256(
        json.dumps(material, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return {
        **material,
        "proposal_id": "project_integration_" + proposal_digest[:24],
        "proposal_digest": proposal_digest,
        "assets": assets,
        "effects": {
            "project_source_write": "PROPOSED",
            "project_runtime_state_write": "PROPOSED",
            "career_release_write": "NONE",
        },
        "apply_contract": {
            "owner": "UNIVERSE_PROJECT_LIFECYCLE_HOST",
            "project_binding": "PROJECT_SOURCE_APPROVAL_REQUIRED",
            "local_runtime": "INSTALLED_CAREER_RUNTIME_REQUIRED",
            "execution": "NOT_STARTED",
        },
    }
