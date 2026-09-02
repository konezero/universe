"""Small, server-owned registry for mutating Action contracts.

The registry deliberately keeps request routing separate from authority. A
caller can identify an Action and provide its business input, but actor and
execution context are resolved by the server that dispatches the Action.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any


ACTION_REGISTRY_SCHEMA = "universe.action-registry.v1"
ACTION_CONTRACT_VERSION = "universe.action-contract.v1"
ACTION_CONTEXT_SCHEMA = "universe.action-context.v1"

COVERED = "COVERED"
LEGACY_DIRECT = "LEGACY_DIRECT"
UNCOVERED = "UNCOVERED"
ACTION_COVERAGE_CLASSES = frozenset({COVERED, LEGACY_DIRECT, UNCOVERED})

FEATURE_GOAL_START_ACTION_ID = "feature.goal.start"
FEATURE_GOAL_START_REQUEST_SCHEMA = "universe.feature-goal-start-action-request.v1"
FEATURE_GOAL_START_RESULT_SCHEMA = "universe.feature-goal-start-receipt.v1"
FEATURE_GOAL_START_ACTION_SURFACE = "feature.goal.start"
LEGACY_FEATURE_GOAL_START_HTTP_SURFACE = (
    "/v1/feature-nodes/{feature_id}/goal-start-receipts"
)
LEGACY_FEATURE_GOAL_START_STORE_SURFACE = "UniverseStore.start_feature_goal"

RAG_ADOPT_ACTION_ID = "rag.adopt"
RAG_ADOPT_REQUEST_SCHEMA = "universe.rag-adopt-action-request.v1"
RAG_ADOPT_RESULT_SCHEMA = "universe.rag-adopt-receipt.v1"
RAG_ADOPT_ACTION_SURFACE = "rag.adopt"

RAG_RECORD_DECISION_ACTION_ID = "rag.record-decision"
RAG_RECORD_DECISION_REQUEST_SCHEMA = (
    "universe.rag-record-decision-action-request.v1"
)
RAG_RECORD_DECISION_RESULT_SCHEMA = "universe.rag-record-decision-receipt.v1"
RAG_RECORD_DECISION_ACTION_SURFACE = "rag.record-decision"

MEMORY_BATCH_RUN_ACTION_ID = "memory.batch.run"
MEMORY_BATCH_RUN_REQUEST_SCHEMA = "universe.memory-batch-run-action-request.v1"
# The action returns the existing local-service envelope and keeps the durable
# run payload under universe.memory-batch-run.v1.
MEMORY_BATCH_RUN_RESULT_SCHEMA = "universe.local-service.v1"
MEMORY_BATCH_RUN_ACTION_SURFACE = "memory.batch.run"
LEGACY_MEMORY_BATCH_RUN_HTTP_SURFACE = (
    "/v1/projects/{project_id}/memory-batches/run"
)

SESSION_NEW_ACTION_ID = "session.new"
SESSION_NEW_REQUEST_SCHEMA = "universe.session-new-action-request.v1"
SESSION_NEW_RESULT_SCHEMA = "universe.session-new-receipt.v1"
SESSION_NEW_ACTION_SURFACE = "session.new"

SESSION_RESUME_ACTION_ID = "session.resume"
SESSION_RESUME_REQUEST_SCHEMA = "universe.session-resume-action-request.v1"
SESSION_RESUME_RESULT_SCHEMA = "universe.session-resume-receipt.v1"
SESSION_RESUME_ACTION_SURFACE = "session.resume"

LEGACY_CLI_TERMINAL_HTTP_SURFACE = "/v1/terminals"
LEGACY_CONDUCTOR_SESSION_PREPARE_HTTP_SURFACE = "/v1/conductor-session/prepare"
LEGACY_PROJECT_MASTER_SESSION_PREPARE_HTTP_SURFACE = (
    "/v1/projects/{project_id}/master-session/prepare"
)

SERVER_RESOLVED_CALLER_FIELDS = frozenset(
    {
        "actor",
        "actor_id",
        "actor_ref",
        "approval",
        "approval_state",
        "assignment",
        "assignment_id",
        "authority",
        "authority_id",
        "authority_ref",
        "context",
        "execution_assignment",
        "execution_assignment_id",
        "governance",
        "mode",
        "role",
        "started_by_role",
    }
)

_ACTION_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$")


class ActionRegistryError(ValueError):
    """Base error for invalid Action contracts and registry operations."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


class ActionContractError(ActionRegistryError):
    pass


class DuplicateActionError(ActionRegistryError):
    pass


class UnknownActionError(ActionRegistryError):
    pass


ActionHandler = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ActionContractError(
            "ACTION_REQUEST_NOT_CANONICAL",
            "Action request must contain JSON-compatible values",
        ) from error


def _forbidden_field_paths(value: Any, path: str = "request") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            child_path = f"{path}.{name}"
            if name.casefold() in SERVER_RESOLVED_CALLER_FIELDS:
                found.append(child_path)
            found.extend(_forbidden_field_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_field_paths(child, f"{path}[{index}]"))
    return found


def find_forbidden_caller_fields(value: Any) -> tuple[str, ...]:
    """Return caller-supplied server-owned fields in deterministic order."""

    return tuple(sorted(set(_forbidden_field_paths(value))))


def derive_idempotency_key(
    action_id: str,
    request: Mapping[str, Any],
    *,
    contract_version: str = ACTION_CONTRACT_VERSION,
) -> str:
    """Derive a stable key from the Action identity and canonical request."""

    if not isinstance(action_id, str) or not _ACTION_ID_RE.fullmatch(action_id):
        raise ActionContractError(
            "ACTION_ID_INVALID", "action_id must be a stable lowercase identifier"
        )
    if not isinstance(request, Mapping):
        raise ActionContractError("ACTION_REQUEST_INVALID", "request must be an object")
    material = {
        "action_id": action_id,
        "contract_version": contract_version,
        "request": dict(request),
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return f"action_idem_{digest}"


@dataclass(frozen=True)
class ActionContract:
    """Versioned declaration of one server-dispatched Action."""

    action_id: str
    request_schema_ref: str
    result_schema_ref: str
    side_effect_class: str
    contract_version: str = ACTION_CONTRACT_VERSION
    actor_context_resolution: str = "SERVER_SIDE"
    idempotency_key_derivation: str = "SHA256_CANONICAL_ACTION_AND_REQUEST"
    caller_supplied_actor_context: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "ActionContract":
        if not isinstance(self.action_id, str) or not _ACTION_ID_RE.fullmatch(
            self.action_id
        ):
            raise ActionContractError(
                "ACTION_ID_INVALID",
                "action_id must be a stable lowercase identifier",
            )
        for field_name in (
            "contract_version",
            "request_schema_ref",
            "result_schema_ref",
            "side_effect_class",
            "idempotency_key_derivation",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ActionContractError(
                    "ACTION_CONTRACT_INVALID",
                    f"{field_name} must be a non-empty string",
                )
        if self.actor_context_resolution != "SERVER_SIDE":
            raise ActionContractError(
                "ACTION_ACTOR_CONTEXT_RESOLUTION_INVALID",
                "actor and context must be resolved server-side",
            )
        if self.caller_supplied_actor_context:
            raise ActionContractError(
                "ACTION_CALLER_CONTEXT_ENABLED",
                "caller-supplied actor and context are forbidden",
            )
        if not isinstance(self.metadata, Mapping):
            raise ActionContractError(
                "ACTION_METADATA_INVALID", "metadata must be an object"
            )
        return self

    @property
    def actor_context_statement(self) -> str:
        return (
            "Actor and context are resolved server-side; caller-supplied actor "
            "and context are never accepted."
        )

    def validate_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise ActionContractError(
                "ACTION_REQUEST_INVALID", "Action request must be an object"
            )
        forbidden = find_forbidden_caller_fields(request)
        if forbidden:
            raise ActionContractError(
                "ACTION_CALLER_CONTEXT_FORBIDDEN",
                "server-owned fields are not accepted: " + ", ".join(forbidden),
            )
        _canonical_json(request)
        return dict(request)

    def idempotency_key(self, request: Mapping[str, Any]) -> str:
        return derive_idempotency_key(
            self.action_id,
            request,
            contract_version=self.contract_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "contract_version": self.contract_version,
            "request_schema_ref": self.request_schema_ref,
            "result_schema_ref": self.result_schema_ref,
            "side_effect_class": self.side_effect_class,
            "actor_context": {
                "resolution": self.actor_context_resolution,
                "caller_supplied": self.caller_supplied_actor_context,
                "statement": self.actor_context_statement,
            },
            "idempotency_key_derivation": self.idempotency_key_derivation,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RegisteredAction:
    contract: ActionContract
    handler: ActionHandler | None
    surfaces: tuple[str, ...]

    @property
    def action_id(self) -> str:
        return self.contract.action_id


class ActionRegistry:
    """Registry whose coverage report is computed from live registrations."""

    def __init__(self) -> None:
        self._actions: dict[str, RegisteredAction] = {}
        self._legacy_surfaces: set[str] = set()
        self._uncovered_surfaces: set[str] = set()

    def register(
        self,
        contract: ActionContract,
        handler: ActionHandler | None = None,
        *,
        surfaces: Sequence[str] | None = None,
    ) -> ActionContract:
        if not isinstance(contract, ActionContract):
            raise ActionContractError(
                "ACTION_CONTRACT_INVALID", "register requires an ActionContract"
            )
        contract.validate()
        if contract.action_id in self._actions:
            raise DuplicateActionError(
                "ACTION_ID_DUPLICATE",
                f"Action is already registered: {contract.action_id}",
            )
        normalized_surfaces = tuple(
            dict.fromkeys(
                surface.strip()
                for surface in (surfaces or (contract.action_id,))
                if isinstance(surface, str) and surface.strip()
            )
        )
        if not normalized_surfaces:
            raise ActionContractError(
                "ACTION_SURFACE_REQUIRED", "an Action must declare a surface"
            )
        self._actions[contract.action_id] = RegisteredAction(
            contract=contract,
            handler=handler,
            surfaces=normalized_surfaces,
        )
        self._uncovered_surfaces.difference_update(normalized_surfaces)
        return contract

    register_action = register

    def lookup(self, action_id: str) -> ActionContract:
        return self.lookup_registration(action_id).contract

    def lookup_registration(self, action_id: str) -> RegisteredAction:
        if not isinstance(action_id, str) or action_id not in self._actions:
            raise UnknownActionError(
                "ACTION_ID_UNKNOWN", f"Action is not registered: {action_id}"
            )
        return self._actions[action_id]

    def dispatch(
        self,
        action_id: str,
        request: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        registration = self.lookup_registration(action_id)
        normalized_request = registration.contract.validate_request(request)
        if registration.handler is None:
            raise ActionRegistryError(
                "ACTION_HANDLER_UNAVAILABLE",
                f"Action has no registered handler: {action_id}",
            )
        result = registration.handler(normalized_request, dict(context or {}))
        if not isinstance(result, Mapping):
            raise ActionRegistryError(
                "ACTION_RESULT_INVALID", "Action handler must return an object"
            )
        return dict(result)

    def register_legacy_surface(self, surface_id: str) -> str:
        normalized = self._normalize_surface(surface_id)
        self._legacy_surfaces.add(normalized)
        self._uncovered_surfaces.discard(normalized)
        return normalized

    declare_legacy_surface = register_legacy_surface

    def register_uncovered_surface(self, surface_id: str) -> str:
        normalized = self._normalize_surface(surface_id)
        if normalized not in self._legacy_surfaces:
            self._uncovered_surfaces.add(normalized)
        return normalized

    declare_uncovered_surface = register_uncovered_surface

    @staticmethod
    def _normalize_surface(surface_id: str) -> str:
        if not isinstance(surface_id, str) or not surface_id.strip():
            raise ActionContractError(
                "ACTION_SURFACE_INVALID", "surface_id must be a non-empty string"
            )
        return surface_id.strip()

    def known_surfaces(self) -> tuple[str, ...]:
        registered = {
            surface
            for action in self._actions.values()
            for surface in action.surfaces
        }
        return tuple(
            sorted(registered | self._legacy_surfaces | self._uncovered_surfaces)
        )

    def classify_surface(self, surface_id: str) -> str:
        normalized = self._normalize_surface(surface_id)
        if normalized in self._legacy_surfaces:
            return LEGACY_DIRECT
        if any(
            normalized in action.surfaces for action in self._actions.values()
        ):
            return COVERED
        return UNCOVERED

    def coverage_classification(
        self, surface_id: str | None = None
    ) -> str | dict[str, str]:
        """Classify one surface, or return all live surface classifications."""

        if surface_id is None:
            return self.coverage()
        return self.classify_surface(surface_id)

    def coverage(self) -> dict[str, str]:
        return {
            surface: self.classify_surface(surface)
            for surface in self.known_surfaces()
        }

    def coverage_report(self) -> dict[str, Any]:
        action_ids = sorted(self._actions)
        return {
            "schema": ACTION_REGISTRY_SCHEMA,
            "registered_action_ids": action_ids,
            "contracts": [
                self._actions[action_id].contract.to_dict()
                for action_id in action_ids
            ],
            "coverage": [
                {
                    "surface_id": surface,
                    "classification": classification,
                }
                for surface, classification in self.coverage().items()
            ],
        }


def build_default_action_registry(
    handler: ActionHandler | None = None,
    *,
    rag_adopt_handler: ActionHandler | None = None,
    rag_record_decision_handler: ActionHandler | None = None,
    memory_batch_run_handler: ActionHandler | None = None,
    session_new_handler: ActionHandler | None = None,
    session_resume_handler: ActionHandler | None = None,
) -> ActionRegistry:
    """Build the currently modeled mutating surface registry."""

    registry = ActionRegistry()
    registry.register(
        ActionContract(
            action_id=FEATURE_GOAL_START_ACTION_ID,
            request_schema_ref=FEATURE_GOAL_START_REQUEST_SCHEMA,
            result_schema_ref=FEATURE_GOAL_START_RESULT_SCHEMA,
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={"receipt_schema": "universe.feature-goal-start-receipt.v1"},
        ),
        handler,
        surfaces=(FEATURE_GOAL_START_ACTION_SURFACE,),
    )
    registry.register_legacy_surface(LEGACY_FEATURE_GOAL_START_HTTP_SURFACE)
    registry.register_legacy_surface(LEGACY_FEATURE_GOAL_START_STORE_SURFACE)
    registry.register(
        ActionContract(
            action_id=RAG_ADOPT_ACTION_ID,
            request_schema_ref=RAG_ADOPT_REQUEST_SCHEMA,
            result_schema_ref=RAG_ADOPT_RESULT_SCHEMA,
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={"receipt_schema": RAG_ADOPT_RESULT_SCHEMA},
        ),
        rag_adopt_handler,
        surfaces=(RAG_ADOPT_ACTION_SURFACE,),
    )
    registry.register(
        ActionContract(
            action_id=RAG_RECORD_DECISION_ACTION_ID,
            request_schema_ref=RAG_RECORD_DECISION_REQUEST_SCHEMA,
            result_schema_ref=RAG_RECORD_DECISION_RESULT_SCHEMA,
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={"receipt_schema": RAG_RECORD_DECISION_RESULT_SCHEMA},
        ),
        rag_record_decision_handler,
        surfaces=(RAG_RECORD_DECISION_ACTION_SURFACE,),
    )
    registry.register(
        ActionContract(
            action_id=MEMORY_BATCH_RUN_ACTION_ID,
            request_schema_ref=MEMORY_BATCH_RUN_REQUEST_SCHEMA,
            result_schema_ref=MEMORY_BATCH_RUN_RESULT_SCHEMA,
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "run_schema": "universe.memory-batch-run.v1",
                "provider_invocation": "MAY_RUN",
            },
        ),
        memory_batch_run_handler,
        surfaces=(MEMORY_BATCH_RUN_ACTION_SURFACE,),
    )
    registry.register_legacy_surface(LEGACY_MEMORY_BATCH_RUN_HTTP_SURFACE)
    registry.register(
        ActionContract(
            action_id=SESSION_NEW_ACTION_ID,
            request_schema_ref=SESSION_NEW_REQUEST_SCHEMA,
            result_schema_ref=SESSION_NEW_RESULT_SCHEMA,
            side_effect_class="SESSION_LIFECYCLE_MUTATION",
            metadata={
                "target_resolution": "SERVER_SIDE",
                "targets": ["PROJECT_MASTER", "UNIVERSE_CONDUCTOR"],
            },
        ),
        session_new_handler,
        surfaces=(SESSION_NEW_ACTION_SURFACE,),
    )
    registry.register(
        ActionContract(
            action_id=SESSION_RESUME_ACTION_ID,
            request_schema_ref=SESSION_RESUME_REQUEST_SCHEMA,
            result_schema_ref=SESSION_RESUME_RESULT_SCHEMA,
            side_effect_class="SESSION_LIFECYCLE_MUTATION",
            metadata={
                "target_resolution": "SERVER_SIDE",
                "targets": [
                    "CLI_TERMINAL",
                    "PROJECT_MASTER",
                    "UNIVERSE_CONDUCTOR",
                ],
            },
        ),
        session_resume_handler,
        surfaces=(SESSION_RESUME_ACTION_SURFACE,),
    )
    registry.register_legacy_surface(LEGACY_CLI_TERMINAL_HTTP_SURFACE)
    registry.register_legacy_surface(LEGACY_CONDUCTOR_SESSION_PREPARE_HTTP_SURFACE)
    registry.register_legacy_surface(LEGACY_PROJECT_MASTER_SESSION_PREPARE_HTTP_SURFACE)
    return registry


DEFAULT_ACTION_REGISTRY = build_default_action_registry()


__all__ = [
    "ACTION_CONTEXT_SCHEMA",
    "ACTION_CONTRACT_VERSION",
    "ACTION_COVERAGE_CLASSES",
    "ACTION_REGISTRY_SCHEMA",
    "ActionContract",
    "ActionContractError",
    "ActionRegistry",
    "ActionRegistryError",
    "COVERED",
    "DEFAULT_ACTION_REGISTRY",
    "DuplicateActionError",
    "FEATURE_GOAL_START_ACTION_ID",
    "FEATURE_GOAL_START_ACTION_SURFACE",
    "FEATURE_GOAL_START_REQUEST_SCHEMA",
    "FEATURE_GOAL_START_RESULT_SCHEMA",
    "LEGACY_DIRECT",
    "LEGACY_CLI_TERMINAL_HTTP_SURFACE",
    "LEGACY_CONDUCTOR_SESSION_PREPARE_HTTP_SURFACE",
    "LEGACY_FEATURE_GOAL_START_HTTP_SURFACE",
    "LEGACY_FEATURE_GOAL_START_STORE_SURFACE",
    "LEGACY_MEMORY_BATCH_RUN_HTTP_SURFACE",
    "LEGACY_PROJECT_MASTER_SESSION_PREPARE_HTTP_SURFACE",
    "MEMORY_BATCH_RUN_ACTION_ID",
    "MEMORY_BATCH_RUN_ACTION_SURFACE",
    "MEMORY_BATCH_RUN_REQUEST_SCHEMA",
    "MEMORY_BATCH_RUN_RESULT_SCHEMA",
    "RAG_ADOPT_ACTION_ID",
    "RAG_ADOPT_ACTION_SURFACE",
    "RAG_ADOPT_REQUEST_SCHEMA",
    "RAG_ADOPT_RESULT_SCHEMA",
    "RAG_RECORD_DECISION_ACTION_ID",
    "RAG_RECORD_DECISION_ACTION_SURFACE",
    "RAG_RECORD_DECISION_REQUEST_SCHEMA",
    "RAG_RECORD_DECISION_RESULT_SCHEMA",
    "SESSION_NEW_ACTION_ID",
    "SESSION_NEW_ACTION_SURFACE",
    "SESSION_NEW_REQUEST_SCHEMA",
    "SESSION_NEW_RESULT_SCHEMA",
    "SESSION_RESUME_ACTION_ID",
    "SESSION_RESUME_ACTION_SURFACE",
    "SESSION_RESUME_REQUEST_SCHEMA",
    "SESSION_RESUME_RESULT_SCHEMA",
    "RegisteredAction",
    "SERVER_RESOLVED_CALLER_FIELDS",
    "UNCOVERED",
    "UnknownActionError",
    "build_default_action_registry",
    "derive_idempotency_key",
    "find_forbidden_caller_fields",
]
