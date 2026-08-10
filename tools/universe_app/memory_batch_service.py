"""Memory batch catalog and configuration orchestration."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Mapping, Protocol

from provider_model_catalog import ProviderModelCatalogError
from universe_memory import (
    MEMORY_BATCH_STAGES,
    MemoryError,
    normalize_memory_batch_config,
    resolve_memory_batch_config,
)

from .connection import UniverseError


class MemoryBatchStore(Protocol):
    def get_project(self, project_id: str) -> dict[str, Any]: ...

    def resolve_worker_binding(self, request: Mapping[str, Any]) -> dict[str, Any]: ...

    def get_memory_batch_configs(self, project_id: str) -> list[dict[str, Any]]: ...

    def upsert_memory_batch_config(
        self, project_id: str, request: Mapping[str, Any]
    ) -> dict[str, Any]: ...


class ProviderModelCatalog(Protocol):
    def snapshot(self) -> dict[str, Any]: ...


class MemoryBatchConfigurationService:
    """Resolve and persist Memory batch settings without owning HTTP routing."""

    def __init__(
        self,
        store: MemoryBatchStore,
        provider_model_catalog: ProviderModelCatalog,
        *,
        api_schema: str,
    ) -> None:
        self.store = store
        self.provider_model_catalog = provider_model_catalog
        self.api_schema = api_schema

    @staticmethod
    def config_request(value: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "stage",
            "provider",
            "model",
            "model_ref",
            "effort",
            "schedule",
            "quota",
            "budget",
            "quota_or_budget",
            "fallback",
            "enabled",
            "dry_run",
        }
        return {key: value[key] for key in allowed if key in value}

    def catalog_settings(self) -> dict[str, Any]:
        try:
            catalog = self.provider_model_catalog.snapshot()
        except ProviderModelCatalogError as error:
            raise UniverseError(error.code, str(error)) from error
        return {
            "schema": self.api_schema,
            "status": "MEMORY_BATCH_CATALOG_COLLECTED",
            "catalog": catalog,
            "stages": sorted(MEMORY_BATCH_STAGES),
            "resolution_policy": {
                "provider_model_catalog": "CURRENT_SNAPSHOT_REQUIRED",
                "invalid_model": "FAIL_CLOSED",
                "unavailable_provider": "FALLBACK_ONLY",
            },
        }

    def resolve(
        self, project_id: str, value: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        project = self.store.get_project(project_id)
        if not isinstance(value, Mapping):
            raise UniverseError(
                "MEMORY_BATCH_CONFIG_INVALID",
                "memory batch config must be an object",
            )
        request = self.config_request(value)
        stage = str(request.get("stage") or "").strip().upper()
        if stage not in MEMORY_BATCH_STAGES:
            raise UniverseError(
                "MEMORY_BATCH_STAGE_INVALID", "stage is not supported"
            )
        try:
            binding_resolution = self.store.resolve_worker_binding(
                {
                    "project_id": project["project_id"],
                    "worker_role": "ROUTINE",
                    "task_type": f"MEMORY_{stage}",
                }
            )
            normalized = normalize_memory_batch_config(
                request,
                worker_binding=binding_resolution.get("snapshot"),
            )
        except MemoryError as error:
            raise UniverseError(error.code, error.message) from error
        try:
            catalog = self.provider_model_catalog.snapshot()
        except ProviderModelCatalogError as error:
            raise UniverseError(error.code, str(error)) from error
        try:
            resolved = resolve_memory_batch_config(normalized, catalog)
        except MemoryError as error:
            if (
                error.code == "MEMORY_BATCH_PROVIDER_UNAVAILABLE"
                and normalized["fallback"] == "DETERMINISTIC"
            ):
                resolved = {
                    **normalized,
                    "resolution": {
                        "status": "FALLBACK_DETERMINISTIC",
                        "error_code": error.code,
                        "requested_provider": normalized["provider"],
                        "requested_model_ref": normalized["model_ref"],
                    },
                }
            else:
                raise UniverseError(
                    error.code, error.message, HTTPStatus.CONFLICT
                ) from error
        if (
            resolved.get("resolution", {}).get("status") == "UNAVAILABLE"
            and normalized["fallback"] == "DETERMINISTIC"
        ):
            resolved["resolution"] = {
                **resolved["resolution"],
                "status": "FALLBACK_DETERMINISTIC",
                "error_code": "MEMORY_BATCH_PROVIDER_UNAVAILABLE",
            }
        binding = binding_resolution.get("snapshot") or {}
        resolved["project_id"] = project["project_id"]
        resolved["worker_binding"] = {
            "profile_id": binding.get("profile_id"),
            "provider": binding.get("provider"),
            "model_ref": binding.get("model_ref"),
            "effort": binding.get("effort"),
            "binding_digest": binding.get("binding_digest"),
        }
        return resolved, catalog

    def list_configs(self, project_id: str) -> dict[str, Any]:
        configs: list[dict[str, Any]] = []
        for stored in self.store.get_memory_batch_configs(project_id):
            try:
                resolved, _catalog = self.resolve(project_id, stored)
                configs.append({**stored, **resolved})
            except UniverseError as error:
                configs.append(
                    {
                        **stored,
                        "resolution": {
                            "status": "BLOCKED",
                            "error_code": error.code,
                            "detail": error.detail,
                        },
                    }
                )
        return {
            "schema": self.api_schema,
            "status": "MEMORY_BATCH_CONFIGS_COLLECTED",
            "project_id": project_id,
            "configs": configs,
        }

    def save_config(self, project_id: str, value: Any) -> dict[str, Any]:
        resolved, _catalog = self.resolve(project_id, value)
        persisted = self.store.upsert_memory_batch_config(
            project_id,
            {
                **self.config_request(resolved),
                "resolution": resolved.get("resolution"),
            },
        )
        return {
            "schema": self.api_schema,
            "status": "MEMORY_BATCH_CONFIG_SAVED",
            "config": {
                **persisted,
                "resolution": resolved.get("resolution"),
                "worker_binding": resolved.get("worker_binding"),
            },
        }
