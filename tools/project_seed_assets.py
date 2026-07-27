from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ASSET_MANIFEST_SCHEMA = "universe.project-seed-assets.v1"
FUNCTIONAL_GRAPH_SCHEMA = "universe.functional-graph.v1"
IMPLEMENTATION_GRAPH_SCHEMA = "universe.implementation-graph.v1"
BINDINGS_SCHEMA = "universe.implementation-bindings.v1"
DOCUMENT_CATALOG_SCHEMA = "universe.project-document-catalog.v1"
ASSET_ROOT = Path(".ai") / "universe"
ASSET_FILES = {
    "functional_graph": "functional-graph.json",
    "implementation_graph": "implementation-graph.json",
    "bindings": "bindings.json",
    "documents": "documents.json",
}


class ProjectSeedAssetError(ValueError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_project_seed_asset_bundle(seed: dict[str, Any]) -> dict[str, bytes]:
    """Build the canonical Project-side Seed assets without writing a Project."""

    functional = {
        "schema": FUNCTIONAL_GRAPH_SCHEMA,
        "nodes": seed["nodes"],
        "edges": seed["edges"],
    }
    implementation = {
        "schema": IMPLEMENTATION_GRAPH_SCHEMA,
        "nodes": seed.get("implementation", {}).get("nodes", []),
    }
    bindings = {
        "schema": BINDINGS_SCHEMA,
        "bindings": seed.get("implementation_bindings", []),
    }
    documents = {
        "schema": DOCUMENT_CATALOG_SCHEMA,
        "documents": seed["documents"],
    }
    payloads = {
        ASSET_FILES["functional_graph"]: canonical_json(functional) + b"\n",
        ASSET_FILES["implementation_graph"]: canonical_json(implementation) + b"\n",
        ASSET_FILES["bindings"]: canonical_json(bindings) + b"\n",
        ASSET_FILES["documents"]: canonical_json(documents) + b"\n",
    }
    manifest = {
        "schema": ASSET_MANIFEST_SCHEMA,
        "status": "PUBLISHED",
        "asset_root": ASSET_ROOT.as_posix(),
        "seed_id": seed["seed_id"],
        "source": seed["source"],
        "project": seed["project"],
        "assets": {
            key: {
                "path": filename,
                "sha256": sha256_bytes(payloads[filename]),
            }
            for key, filename in ASSET_FILES.items()
        },
    }
    payloads["manifest.json"] = canonical_json(manifest) + b"\n"
    return payloads


def materialize_project_seed_assets(project_root: Path, seed: dict[str, Any]) -> dict[str, Any]:
    """Write a completed bundle. Project Master callers own the write authorization."""

    root = project_root.resolve(strict=True)
    asset_root = root / ASSET_ROOT
    if asset_root.exists() and asset_root.is_symlink():
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_ROOT_SYMLINK")
    asset_root.mkdir(parents=True, exist_ok=True)
    if asset_root.is_symlink() or not asset_root.is_dir():
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_ROOT_INVALID")
    payloads = build_project_seed_asset_bundle(seed)
    for relative_path, content in payloads.items():
        target = asset_root / relative_path
        if target.exists() and target.is_symlink():
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_TARGET_SYMLINK")
        target.write_bytes(content)
    return {
        "schema": ASSET_MANIFEST_SCHEMA,
        "asset_root": ASSET_ROOT.as_posix(),
        "manifest_ref": (ASSET_ROOT / "manifest.json").as_posix(),
        "files": {
            relative_path: sha256_bytes(content)
            for relative_path, content in sorted(payloads.items())
        },
    }


def load_project_seed_assets(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    asset_root = root / ASSET_ROOT
    if not asset_root.is_dir() or asset_root.is_symlink():
        raise ProjectSeedAssetError("PROJECT_SEED_ASSETS_UNAVAILABLE")
    manifest_path = asset_root / "manifest.json"
    manifest = _read_json(manifest_path, "PROJECT_SEED_ASSET_MANIFEST_INVALID")
    if manifest.get("schema") != ASSET_MANIFEST_SCHEMA:
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_SCHEMA_INVALID")
    if manifest.get("status") != "PUBLISHED":
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_NOT_PUBLISHED")
    if manifest.get("asset_root") != ASSET_ROOT.as_posix():
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_ROOT_INVALID")
    assets = manifest.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(ASSET_FILES):
        raise ProjectSeedAssetError("PROJECT_SEED_ASSET_MANIFEST_INVALID")
    payloads: dict[str, dict[str, Any]] = {}
    for key, expected_filename in ASSET_FILES.items():
        descriptor = assets.get(key)
        if not isinstance(descriptor, dict) or descriptor.get("path") != expected_filename:
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_MANIFEST_INVALID")
        expected_digest = descriptor.get("sha256")
        if not isinstance(expected_digest, str) or len(expected_digest) != 64:
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_MANIFEST_INVALID")
        target = asset_root / expected_filename
        if not target.is_file() or target.is_symlink():
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_FILE_UNAVAILABLE")
        content = target.read_bytes()
        if sha256_bytes(content) != expected_digest:
            raise ProjectSeedAssetError("PROJECT_SEED_ASSET_DIGEST_MISMATCH")
        payloads[key] = _read_json(target, "PROJECT_SEED_ASSET_FILE_INVALID")

    functional = payloads["functional_graph"]
    implementation = payloads["implementation_graph"]
    bindings = payloads["bindings"]
    documents = payloads["documents"]
    if functional.get("schema") != FUNCTIONAL_GRAPH_SCHEMA:
        raise ProjectSeedAssetError("FUNCTIONAL_GRAPH_SCHEMA_INVALID")
    if implementation.get("schema") != IMPLEMENTATION_GRAPH_SCHEMA:
        raise ProjectSeedAssetError("IMPLEMENTATION_GRAPH_SCHEMA_INVALID")
    if bindings.get("schema") != BINDINGS_SCHEMA:
        raise ProjectSeedAssetError("IMPLEMENTATION_BINDINGS_SCHEMA_INVALID")
    if documents.get("schema") != DOCUMENT_CATALOG_SCHEMA:
        raise ProjectSeedAssetError("PROJECT_DOCUMENT_CATALOG_SCHEMA_INVALID")
    if not isinstance(functional.get("nodes"), list) or not isinstance(functional.get("edges"), list):
        raise ProjectSeedAssetError("FUNCTIONAL_GRAPH_CONTENT_INVALID")
    if not isinstance(implementation.get("nodes"), list):
        raise ProjectSeedAssetError("IMPLEMENTATION_GRAPH_CONTENT_INVALID")
    if not isinstance(bindings.get("bindings"), list):
        raise ProjectSeedAssetError("IMPLEMENTATION_BINDINGS_CONTENT_INVALID")
    if not isinstance(documents.get("documents"), list):
        raise ProjectSeedAssetError("PROJECT_DOCUMENT_CATALOG_CONTENT_INVALID")
    return {
        "seed_id": manifest.get("seed_id"),
        "source": manifest.get("source"),
        "project": manifest.get("project"),
        "nodes": functional["nodes"],
        "edges": functional["edges"],
        "implementation_nodes": implementation["nodes"],
        "implementation_bindings": bindings["bindings"],
        "documents": documents["documents"],
    }


def project_seed_template() -> dict[str, Any]:
    return {
        "schema": "universe.project-seed-template.v1",
        "template_id": "project-seed-v1",
        "asset_root": ASSET_ROOT.as_posix(),
        "asset_files": {key: value for key, value in ASSET_FILES.items()},
        "owner": "PROJECT_MASTER",
        "generation": "PROJECT_MASTER_READ_ONLY_DISCOVERY_THEN_PROJECT_WRITE",
        "graphs": {
            "functional": "Capabilities, flows, and external boundaries.",
            "implementation": "Packages, modules, classes, services, adapters, and endpoints.",
            "bindings": "Many-to-many functional to implementation evidence links.",
        },
    }


def _read_json(path: Path, error_code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectSeedAssetError(error_code) from error
    if not isinstance(value, dict):
        raise ProjectSeedAssetError(error_code)
    return value
