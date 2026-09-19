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

# Shared work-surface Actions. These identifiers are deliberately stable:
# HTTP, the Conductor runtime, and future adapters all use the same registry
# instead of growing separate command vocabularies.
FEATURE_CREATE_ACTION_ID = "feature.create"
FEATURE_CREATE_REQUEST_SCHEMA = "universe.feature-create-action-request.v1"
FEATURE_CREATE_RESULT_SCHEMA = "universe.feature-create-receipt.v1"

TODO_CREATE_ACTION_ID = "todo.create"
TODO_UPDATE_ACTION_ID = "todo.update"
TODO_STATE_ACTION_ID = "todo.state"
TODO_READ_ACTION_ID = "todo.read"
TODO_LIST_ACTION_ID = "todo.list"
TODO_PRIORITY_ACTION_ID = "todo.priority"
TODO_BIND_NODE_ACTION_ID = "todo.bind_node"
TODO_BIND_GOAL_ACTION_ID = "todo.bind_goal"
TODO_MOVE_PROJECT_ACTION_ID = "todo.move_project"
TODO_REORDER_ACTION_ID = "todo.reorder"
TODO_ARCHIVE_ACTION_ID = "todo.archive"
TODO_RESTORE_ACTION_ID = "todo.restore"
TODO_REDO_ACTION_ID = "todo.redo"

# The exact shape UniverseServer._handle_todo_bind_goal_action /
# UniverseStore.set_todo_goal_binding accept: additionalProperties is False
# there (via _exact_object_fields), so this schema must list every field the
# handler actually reads, nothing more - a caller relying only on the
# catalog (not the source) needs this to construct a valid call.
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
TODO_BIND_GOAL_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
        "goal_id": {
            "type": ["string", "null"],
            "pattern": _IDENTIFIER_PATTERN,
            "description": "Omit or set null to unbind; a non-null value must "
            "name an existing Goal in the same project as the Todo.",
        },
    },
}
TODO_BIND_NODE_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
        "node_ref": {
            "type": ["string", "null"],
            "pattern": _IDENTIFIER_PATTERN,
            "description": "Omit or set null to unbind to PROJECT scope; a "
            "non-null value must name an existing Feature Node in the same "
            "project as the Todo.",
        },
    },
}
TODO_PRIORITY_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision", "priority"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
        "priority": {
            "type": "string",
            "enum": ["AUTO", "P0", "P1", "P2", "P3"],
            "description": "AUTO resolves server-side via infer_todo_priority; "
            "P0..P3 are stored as the explicit user/Master choice.",
        },
    },
}
TODO_REORDER_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision", "sort_order"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
        "sort_order": {
            "type": "integer",
            "description": "Explicit list order among sibling Todos; lower "
            "values sort earlier in todo.list ORDER BY sort_order.",
        },
    },
}
TODO_MOVE_PROJECT_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision", "project_id"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
        "project_id": {
            "type": ["string", "null"],
            "pattern": _IDENTIFIER_PATTERN,
            "description": "Target project_id, or null to move into UNIVERSE "
            "scope. NODE-scoped Todos and Todos with node_ref/goal_id must be "
            "unbound first (todo.bind_node / todo.bind_goal).",
        },
    },
}
TODO_ARCHIVE_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
    },
}
TODO_RESTORE_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
    },
}
TODO_REDO_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["todo_id", "expected_revision", "request_id"],
    "properties": {
        "todo_id": {"type": "string", "pattern": _IDENTIFIER_PATTERN},
        "expected_revision": {"type": "integer", "minimum": 1},
        "request_id": {
            "type": "string",
            "pattern": "^[A-Za-z0-9_-]{8,100}$",
            "description": "Idempotency key. Replaying it returns the first result "
            "instead of copying the Todo again.",
        },
        "reason": {"type": "string", "maxLength": 500},
    },
}
TODO_DELETE_ACTION_ID = "todo.delete"

TODO_ACTION_IDS = (
    TODO_CREATE_ACTION_ID,
    TODO_UPDATE_ACTION_ID,
    TODO_READ_ACTION_ID,
    TODO_LIST_ACTION_ID,
    TODO_STATE_ACTION_ID,
    TODO_PRIORITY_ACTION_ID,
    TODO_BIND_NODE_ACTION_ID,
    TODO_BIND_GOAL_ACTION_ID,
    TODO_MOVE_PROJECT_ACTION_ID,
    TODO_REORDER_ACTION_ID,
    TODO_ARCHIVE_ACTION_ID,
    TODO_RESTORE_ACTION_ID,
    TODO_REDO_ACTION_ID,
    TODO_DELETE_ACTION_ID,
)

# TODO_ACTION_IDS is the full, stable todo work-surface vocabulary. Only the
# Actions in IMPLEMENTED_WORK_SURFACE_ACTION_IDS have a server handler and are
# registered as discoverable contracts; the rest stay unregistered (so coverage
# never reports them as available) until a handler and a receipt-aware path
# exist. See docs/action-ir-work-surface.md for the slice-3 decision.
IMPLEMENTED_WORK_SURFACE_ACTION_IDS = (
    FEATURE_CREATE_ACTION_ID,
    TODO_CREATE_ACTION_ID,
    TODO_UPDATE_ACTION_ID,
    TODO_READ_ACTION_ID,
    TODO_LIST_ACTION_ID,
    TODO_STATE_ACTION_ID,
    TODO_BIND_GOAL_ACTION_ID,
    TODO_BIND_NODE_ACTION_ID,
    TODO_PRIORITY_ACTION_ID,
    TODO_REORDER_ACTION_ID,
    TODO_MOVE_PROJECT_ACTION_ID,
    TODO_ARCHIVE_ACTION_ID,
    TODO_RESTORE_ACTION_ID,
    TODO_REDO_ACTION_ID,
)
PENDING_WORK_SURFACE_ACTION_IDS = (
    TODO_DELETE_ACTION_ID,
)

# credential_handling contract: Actions never accept inline secrets. A caller may
# pass an opaque ``credential_ref`` the server resolves out of band; any field
# that would carry the secret value itself is rejected.
ACTION_CREDENTIAL_REF_FIELD = "credential_ref"
SENSITIVE_CREDENTIAL_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "client_secret",
        "credential",
        "credential_value",
        "password",
        "secret",
        "token",
    }
)

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

# A prepared memory-sync bundle is passive until the user explicitly selects
# it for canonical persistence.  This Action is the only mutating bridge: it
# accepts the prepared evidence itself and never shells out to memory-sync.
MEMORY_SYNC_PERSIST_SELECTED_ACTION_ID = "memory.sync.persist-selected"
MEMORY_SYNC_PERSIST_SELECTED_REQUEST_SCHEMA = (
    "universe.memory-sync-persist-selected-action-request.v1"
)
MEMORY_SYNC_PERSIST_SELECTED_RESULT_SCHEMA = (
    "universe.memory-sync-persist-selected-receipt.v1"
)
MEMORY_SYNC_PERSIST_SELECTED_ACTION_SURFACE = "memory.sync.persist-selected"

SESSION_NEW_ACTION_ID = "session.new"
SESSION_NEW_REQUEST_SCHEMA = "universe.session-new-action-request.v1"
SESSION_NEW_RESULT_SCHEMA = "universe.session-new-receipt.v1"
SESSION_NEW_ACTION_SURFACE = "session.new"

SESSION_RESUME_ACTION_ID = "session.resume"
SESSION_RESUME_REQUEST_SCHEMA = "universe.session-resume-action-request.v1"
SESSION_RESUME_RESULT_SCHEMA = "universe.session-resume-receipt.v1"
SESSION_RESUME_ACTION_SURFACE = "session.resume"

# Provider-neutral delivery Actions. The compatibility HTTP routes front the
# same typed contracts and durable domain gateways; they do not create a
# second receipt store or an independent lifecycle.
MASTER_COMPLETE_ACTION_ID = "master.complete"
MASTER_COMPLETE_REQUEST_SCHEMA = "universe.master-complete-action-request.v1"
MASTER_COMPLETE_RESULT_SCHEMA = "universe.master-complete-action-result.v1"

SESSION_BUS_REPLY_ACTION_ID = "session-bus.reply"
SESSION_BUS_REPLY_REQUEST_SCHEMA = "universe.session-bus-reply-action-request.v1"
SESSION_BUS_REPLY_RESULT_SCHEMA = "universe.session-bus-reply-action-result.v1"

# Persona Conductor automation is a bounded, durable work loop.  State and
# queue Actions remain local mutations; the judge Action is the one explicit
# provider boundary and must use the server-owned Task Frame Host.
PERSONA_AUTOMATION_ACTION_IDS = (
    "persona.automation.start",
    "persona.automation.pause",
    "persona.automation.resume",
    "persona.automation.stop",
    "persona.automation.status",
    "persona.automation.kick",
    "persona.automation.repair-reviewer",
    "persona.automation.tick",
    "persona.automation.plan",
    "persona.automation.judge",
    "persona.automation.decide",
    "persona.automation.dispatch",
    "persona.automation.master-result",
    "persona.automation.worker-result",
    "persona.automation.reviewer-verdict",
    "persona.automation.review",
    "persona.automation.complete",
    "persona.automation.launch-frame",
    "persona.automation.host-directive",
    "persona.automation.host-status",
    "persona.automation.collect-frame",
    "persona.automation.host-permission",
    "persona.automation.host-binding",
)

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


def canonical_value_sha256(value: Any) -> str:
    """Hash one canonical JSON value as UTF-8 for an Action receipt."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def utf8_sha256(value: str) -> str:
    """Hash the exact text bytes received at an Action boundary."""

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


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


def _sensitive_credential_field_paths(value: Any, path: str = "request") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            child_path = f"{path}.{name}"
            if name.casefold() in SENSITIVE_CREDENTIAL_FIELDS:
                found.append(child_path)
            found.extend(_sensitive_credential_field_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(
                _sensitive_credential_field_paths(child, f"{path}[{index}]")
            )
    return found


def find_forbidden_credential_fields(value: Any) -> tuple[str, ...]:
    """Return secret-bearing request fields; ``credential_ref`` stays opaque."""

    return tuple(sorted(set(_sensitive_credential_field_paths(value))))


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
    credential_handling: str = "CREDENTIAL_REF_ONLY"
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
        if self.credential_handling != "CREDENTIAL_REF_ONLY":
            raise ActionContractError(
                "ACTION_CREDENTIAL_HANDLING_INVALID",
                "Actions must use opaque credential_ref values only",
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
        credential_fields = find_forbidden_credential_fields(request)
        if credential_fields:
            raise ActionContractError(
                "ACTION_CREDENTIAL_REF_ONLY",
                "secret-bearing fields are not accepted: "
                + ", ".join(credential_fields),
            )
        if ACTION_CREDENTIAL_REF_FIELD in request:
            credential_ref = request[ACTION_CREDENTIAL_REF_FIELD]
            if not isinstance(credential_ref, str) or not credential_ref.strip():
                raise ActionContractError(
                    "ACTION_CREDENTIAL_REF_INVALID",
                    "credential_ref must be a non-empty opaque reference",
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
            "credential_handling": self.credential_handling,
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

    def bind_handler(
        self, action_id: str, handler: ActionHandler
    ) -> ActionContract:
        """Attach a server-owned handler after the registry is constructed."""

        if not callable(handler):
            raise ActionContractError(
                "ACTION_HANDLER_INVALID", "handler must be callable"
            )
        registration = self.lookup_registration(action_id)
        self._actions[action_id] = RegisteredAction(
            contract=registration.contract,
            handler=handler,
            surfaces=registration.surfaces,
        )
        return registration.contract

    set_handler = bind_handler

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
    rag_archive_candidate_handler: ActionHandler | None = None,
    rag_memory_retention_handler: ActionHandler | None = None,
    rag_memory_relate_handler: ActionHandler | None = None,
    persona_create_handler: ActionHandler | None = None,
    persona_read_handler: ActionHandler | None = None,
    persona_list_handler: ActionHandler | None = None,
    persona_update_handler: ActionHandler | None = None,
    persona_archive_handler: ActionHandler | None = None,
    persona_restore_handler: ActionHandler | None = None,
    persona_assign_handler: ActionHandler | None = None,
    persona_assignment_read_handler: ActionHandler | None = None,
    persona_assignments_list_handler: ActionHandler | None = None,
    persona_unassign_handler: ActionHandler | None = None,
    fleet_worker_assign_handler: ActionHandler | None = None,
    fleet_worker_unassign_handler: ActionHandler | None = None,
    fleet_worker_assignments_list_handler: ActionHandler | None = None,
    fleet_worker_session_start_handler: ActionHandler | None = None,
    persona_coordination_handler: ActionHandler | None = None,
    master_message_orphan_handler: ActionHandler | None = None,
    memory_sync_persist_selected_handler: ActionHandler | None = None,
    session_new_handler: ActionHandler | None = None,
    session_resume_handler: ActionHandler | None = None,
    persona_automation_handlers: Mapping[str, ActionHandler] | None = None,
    work_surface_handlers: Mapping[str, ActionHandler] | None = None,
) -> ActionRegistry:
    """Build the currently modeled mutating surface registry.

    ``work_surface_handlers`` optionally binds handlers for the shared
    feature/todo work-surface Actions by action_id; unbound Actions are still
    registered so their contracts are discoverable.
    """

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
        ActionContract(action_id="rag.memory-retention", request_schema_ref="universe.rag-memory-retention-request.v1",
                       result_schema_ref="universe.rag-memory-retention-result.v1", side_effect_class="GOVERNED_KNOWLEDGE_WRITE"),
        rag_memory_retention_handler, surfaces=("rag.memory-retention",),
    )
    registry.register(
        ActionContract(
            action_id="rag.memory-relate",
            request_schema_ref="universe.rag-memory-relate-request.v1",
            result_schema_ref="universe.rag-memory-relate-result.v1",
            side_effect_class="GOVERNED_KNOWLEDGE_WRITE",
            metadata={
                "statement": (
                    "Project-scoped, typed memory topic/relation storage — replaces "
                    "hardcoded UI topic/relation tables with server-owned data any "
                    "project's memories can use without a source change."
                )
            },
        ),
        rag_memory_relate_handler,
        surfaces=("rag.memory-relate",),
    )
    _PERSONA_STATEMENT = (
        "Natural-language persona: no fixed role enum, a stable id with a "
        "free-text title/responsibility body a human or an LLM can write the "
        "same way, revisioned, assignable to one exact Session Anchor at a "
        "time. Assignment never grants authority; it only shapes that "
        "session's own launch framing."
    )
    registry.register(
        ActionContract(
            action_id="persona.create",
            request_schema_ref="universe.persona-create-request.v1",
            result_schema_ref="universe.persona-create-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={"statement": _PERSONA_STATEMENT},
        ),
        persona_create_handler, surfaces=("persona.create",),
    )
    registry.register(
        ActionContract(
            action_id="persona.read",
            request_schema_ref="universe.persona-read-request.v1",
            result_schema_ref="universe.persona-read-result.v1",
            side_effect_class="READ_ONLY",
        ),
        persona_read_handler, surfaces=("persona.read",),
    )
    registry.register(
        ActionContract(
            action_id="persona.list",
            request_schema_ref="universe.persona-list-request.v1",
            result_schema_ref="universe.persona-list-result.v1",
            side_effect_class="READ_ONLY",
        ),
        persona_list_handler, surfaces=("persona.list",),
    )
    registry.register(
        ActionContract(
            action_id="persona.update",
            request_schema_ref="universe.persona-update-request.v1",
            result_schema_ref="universe.persona-update-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
        ),
        persona_update_handler, surfaces=("persona.update",),
    )
    registry.register(
        ActionContract(
            action_id="persona.archive",
            request_schema_ref="universe.persona-archive-request.v1",
            result_schema_ref="universe.persona-state-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
        ),
        persona_archive_handler, surfaces=("persona.archive",),
    )
    registry.register(
        ActionContract(
            action_id="persona.restore",
            request_schema_ref="universe.persona-restore-request.v1",
            result_schema_ref="universe.persona-state-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
        ),
        persona_restore_handler, surfaces=("persona.restore",),
    )
    registry.register(
        ActionContract(
            action_id="persona.assign",
            request_schema_ref="universe.persona-assign-request.v1",
            result_schema_ref="universe.persona-assign-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={"statement": _PERSONA_STATEMENT},
        ),
        persona_assign_handler, surfaces=("persona.assign",),
    )
    registry.register(
        ActionContract(
            action_id="persona.assignment-read",
            request_schema_ref="universe.persona-assignment-read-request.v1",
            result_schema_ref="universe.persona-assignment-read-result.v1",
            side_effect_class="READ_ONLY",
        ),
        persona_assignment_read_handler, surfaces=("persona.assignment-read",),
    )
    registry.register(
        ActionContract(
            action_id="persona.assignments-list",
            request_schema_ref="universe.persona-assignments-list-request.v1",
            result_schema_ref="universe.persona-assignments-list-result.v1",
            side_effect_class="READ_ONLY",
            metadata={
                "request_schema": {
                    "type": "object", "additionalProperties": False,
                    "required": ["project_id"], "properties": {"project_id": {"type": "string", "minLength": 1}},
                },
                "statement": (
                    "Every durable session_persona_assignment row for a project "
                    "(ACTIVE and UNASSIGNED, live or offline Anchor) -- the "
                    "authoritative source for node ownership, not a list derived "
                    "by probing only currently-live terminals (2026-09-15)."
                ),
            },
        ),
        persona_assignments_list_handler, surfaces=("persona.assignments-list",),
    )
    registry.register(
        ActionContract(
            action_id="persona.unassign",
            request_schema_ref="universe.persona-unassign-request.v1",
            result_schema_ref="universe.persona-unassign-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
        ),
        persona_unassign_handler, surfaces=("persona.unassign",),
    )
    _FLEET_WORKER_STATEMENT = (
        "A node Master's durable Worker (IMPLEMENTER) or Reviewer binding to "
        "an exact Todo/Task Frame -- append-only per assignment_id so "
        "multiple concurrent workers and retry/ended history are both real, "
        "never overwritten state (2026-09-16 Fleet execution visibility)."
    )
    registry.register(
        ActionContract(
            action_id="fleet.worker-assign",
            request_schema_ref="universe.fleet-worker-assign-request.v1",
            result_schema_ref="universe.task-worker-assign-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={"statement": _FLEET_WORKER_STATEMENT},
        ),
        fleet_worker_assign_handler, surfaces=("fleet.worker-assign",),
    )
    registry.register(
        ActionContract(
            action_id="fleet.worker-unassign",
            request_schema_ref="universe.fleet-worker-unassign-request.v1",
            result_schema_ref="universe.task-worker-end-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={"statement": _FLEET_WORKER_STATEMENT},
        ),
        fleet_worker_unassign_handler, surfaces=("fleet.worker-unassign",),
    )
    registry.register(
        ActionContract(
            action_id="fleet.worker-assignments-list",
            request_schema_ref="universe.fleet-worker-assignments-list-request.v1",
            result_schema_ref="universe.fleet-worker-assignments-list-result.v1",
            side_effect_class="READ_ONLY",
            metadata={"statement": _FLEET_WORKER_STATEMENT},
        ),
        fleet_worker_assignments_list_handler, surfaces=("fleet.worker-assignments-list",),
    )
    registry.register(
        ActionContract(
            action_id="fleet.worker-session-start",
            request_schema_ref="universe.fleet-worker-session-start-request.v1",
            result_schema_ref="universe.fleet-worker-session-start-result.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "statement": (
                    "Creates an explicit long-lived Fleet Worker/Reviewer "
                    "terminal through the supported Host-launch route, then "
                    "records the durable binding only after the exact Host "
                    "receipt exists. This is the FLEET_SESSION observation "
                    "surface: Worker/Reviewer are assignment roles, not a "
                    "new authority or automatic node Runtime Mode. Persona "
                    "automation uses its separate TASK_FRAME route under the "
                    "node Master and must not call this Action."
                ),
            },
        ),
        fleet_worker_session_start_handler, surfaces=("fleet.worker-session-start",),
    )
    # Persona collaboration is a typed Session Bus/Task Frame coordination
    # projection.  It records exact scope and participant anchors but never
    # grants authority or mutates assignments.
    coordination_specs = {
        "persona.collaboration.open": ("LOCAL_DATABASE_MUTATION", "open", ["project_id", "node_ref", "participant_anchors", "initiator_anchor_ref", "file_scope", "evidence", "proposal", "request_id"], ["todo_id", "task_frame_id"]),
        "persona.collaboration.propose": ("LOCAL_DATABASE_MUTATION", "propose", ["coordination_id", "proposer_anchor_ref", "expected_proposal_version", "proposal", "evidence", "request_id"], []),
        "persona.collaboration.respond": ("LOCAL_DATABASE_MUTATION", "respond", ["coordination_id", "responder_anchor_ref", "expected_proposal_version", "outcome", "evidence", "request_id"], []),
        "persona.collaboration.read": ("READ_ONLY", "read", [], ["coordination_id", "project_id", "node_ref"]),
    }
    for action_id, (side_effect, operation, required_fields, optional_fields) in coordination_specs.items():
        registry.register(
            ActionContract(
                action_id=action_id,
                request_schema_ref=f"universe.{action_id.replace('.', '-')}-request.v1",
                result_schema_ref="universe.persona-coordination.v1",
                side_effect_class=side_effect,
                metadata={
                    "operation": operation,
                    "transport": "SESSION_BUS_COORDINATION",
                    "authority": "COORDINATION_ONLY",
                    "request_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": required_fields,
                        "properties": {field: {} for field in (*required_fields, *optional_fields)},
                    },
                },
            ),
            persona_coordination_handler,
            surfaces=(action_id,),
        )
    orphan_specs = {
        "master-message.orphan-cancel": ("cancel", ["project_id", "message_id", "actor_anchor_ref", "expected_owner_session_anchor_ref", "expected_owner_assignment_revision", "request_id", "reason"], []),
        "master-message.orphan-reissue": ("reissue", ["project_id", "message_id", "actor_anchor_ref", "expected_owner_session_anchor_ref", "expected_owner_assignment_revision", "current_owner_session_anchor_ref", "current_owner_assignment_revision", "request_id", "reason"], ["idempotency_key"]),
    }
    for action_id, (operation, required_fields, optional_fields) in orphan_specs.items():
        registry.register(
            ActionContract(
                action_id=action_id,
                request_schema_ref=f"universe.{action_id.replace('.', '-')}-request.v1",
                result_schema_ref="universe.project-master-message.v1",
                side_effect_class="LOCAL_DATABASE_MUTATION",
                metadata={
                    "operation": operation,
                    "gateway": "UniverseStore",
                    "cas": "QUEUED + stamped owner anchor/revision + current owner revision",
                    "authority": "NODE_MASTER_OR_CONDUCTOR",
                    "request_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": required_fields,
                        "properties": {field: {} for field in (*required_fields, *optional_fields)},
                    },
                },
            ),
            master_message_orphan_handler,
            surfaces=(action_id,),
        )
    registry.register(
        ActionContract(
            action_id="rag.archive-candidate",
            request_schema_ref="universe.rag-archive-candidate-request.v1",
            result_schema_ref="universe.rag-archive-candidate-result.v1",
            side_effect_class="GOVERNED_KNOWLEDGE_WRITE",
        ),
        rag_archive_candidate_handler,
        surfaces=("rag.archive-candidate",),
    )
    registry.register(
        ActionContract(
            action_id=MEMORY_SYNC_PERSIST_SELECTED_ACTION_ID,
            request_schema_ref=MEMORY_SYNC_PERSIST_SELECTED_REQUEST_SCHEMA,
            result_schema_ref=MEMORY_SYNC_PERSIST_SELECTED_RESULT_SCHEMA,
            side_effect_class="GOVERNED_KNOWLEDGE_WRITE",
            metadata={
                "prepared_bundle_required": True,
                "actor_context_resolution": "SERVER_SIDE",
                "idempotency": "CANDIDATE_DIGEST_AND_PROJECT",
                "provider_invocation": "FORBIDDEN",
            },
        ),
        memory_sync_persist_selected_handler,
        surfaces=(MEMORY_SYNC_PERSIST_SELECTED_ACTION_SURFACE,),
    )
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

    automation_handlers = dict(persona_automation_handlers or {})
    automation_specs = {
        "persona.automation.start": ("LOCAL_DATABASE_MUTATION", "start"),
        "persona.automation.pause": ("LOCAL_DATABASE_MUTATION", "pause"),
        "persona.automation.resume": ("LOCAL_DATABASE_MUTATION", "resume"),
        "persona.automation.stop": ("LOCAL_DATABASE_MUTATION", "stop"),
        "persona.automation.status": ("READ_ONLY", "status"),
        "persona.automation.kick": ("LOCAL_DATABASE_MUTATION", "kick"),
        "persona.automation.repair-reviewer": ("LOCAL_DATABASE_MUTATION", "repair_reviewer"),
        "persona.automation.tick": ("LOCAL_DATABASE_MUTATION", "tick"),
        "persona.automation.plan": ("LOCAL_DATABASE_MUTATION", "plan"),
        "persona.automation.judge": ("LOCAL_DATABASE_MUTATION", "judge"),
        "persona.automation.decide": ("LOCAL_DATABASE_MUTATION", "decision"),
        "persona.automation.dispatch": ("LOCAL_DATABASE_MUTATION", "dispatch"),
        "persona.automation.master-result": ("LOCAL_DATABASE_MUTATION", "master_result"),
        "persona.automation.worker-result": ("LOCAL_DATABASE_MUTATION", "worker_result"),
        "persona.automation.reviewer-verdict": ("LOCAL_DATABASE_MUTATION", "reviewer_verdict"),
        "persona.automation.review": ("LOCAL_DATABASE_MUTATION", "review"),
        "persona.automation.complete": ("LOCAL_DATABASE_MUTATION", "complete"),
        "persona.automation.launch-frame": ("LOCAL_DATABASE_MUTATION", "launch_frame"),
        "persona.automation.host-directive": ("LOCAL_DATABASE_MUTATION", "host_directive"),
        "persona.automation.host-status": ("READ_ONLY", "host_status"),
        "persona.automation.collect-frame": ("LOCAL_DATABASE_MUTATION", "collect_frame"),
        "persona.automation.host-permission": ("LOCAL_DATABASE_MUTATION", "host_permission"),
        "persona.automation.host-binding": ("READ_ONLY", "host_binding"),
    }
    for action_id in PERSONA_AUTOMATION_ACTION_IDS:
        side_effect, operation = automation_specs[action_id]
        registry.register(
            ActionContract(
                action_id=action_id,
                request_schema_ref=f"universe.{action_id.replace('.', '-')}-request.v1",
                result_schema_ref="universe.persona-automation.v1",
                side_effect_class=side_effect,
                metadata={
                    "operation": operation,
                    "provider_invocation": (
                        "TASK_FRAME_RUNTIME_CODEX_LUNA"
                        if operation == "judge"
                        else "FORBIDDEN"
                    ),
                    "queue_gateway": "MASTER_QUEUE" if operation == "dispatch" else "NONE",
                    "durable": True,
                },
            ),
            automation_handlers.get(action_id),
            surfaces=(action_id,),
        )

    supplied_handlers = dict(work_surface_handlers or {})
    registry.register(
        ActionContract(
            action_id=MASTER_COMPLETE_ACTION_ID,
            request_schema_ref=MASTER_COMPLETE_REQUEST_SCHEMA,
            result_schema_ref=MASTER_COMPLETE_RESULT_SCHEMA,
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "gateway": "UniverseStore.complete_master_message",
                "receipt_schema": MASTER_COMPLETE_RESULT_SCHEMA,
                "authentication": "CLAIMED_MASTER_OWNER",
                "replay": "EXACT_MESSAGE_PROVIDER_AND_BODY_DIGEST",
                "request_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["message_id", "provider"],
                    "properties": {
                        "message_id": {"type": "string"},
                        "provider": {"type": "string"},
                        "body_text": {"type": "string"},
                        "result_ref": {"type": "string"},
                        "terminal_id": {"type": "string"},
                        "session_anchor_ref": {"type": "string"},
                    },
                },
            },
        ),
        supplied_handlers.get(MASTER_COMPLETE_ACTION_ID),
        surfaces=(MASTER_COMPLETE_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=SESSION_BUS_REPLY_ACTION_ID,
            request_schema_ref=SESSION_BUS_REPLY_REQUEST_SCHEMA,
            result_schema_ref=SESSION_BUS_REPLY_RESULT_SCHEMA,
            side_effect_class="SESSION_LIFECYCLE_MUTATION",
            metadata={
                "gateway": "SessionBus.reply",
                "receipt_schema": SESSION_BUS_REPLY_RESULT_SCHEMA,
                "authentication": "RECIPIENT_SESSION_ANCHOR",
                "replay": "EXACT_MESSAGE_RECIPIENT_AND_BODY_DIGEST",
                "processing_action": "PROCESS_REPLY_IS_CONSUMPTION_ONLY",
                "request_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "message_id",
                        "session_anchor_ref",
                        "body_text",
                    ],
                    "properties": {
                        "message_id": {"type": "string"},
                        "terminal_id": {"type": "string"},
                        "session_anchor_ref": {"type": "string"},
                        "body_text": {"type": "string"},
                        "result_ref": {"type": "string"},
                        "outcome": {"enum": ["COMPLETED", "FAILED"]},
                    },
                },
            },
        ),
        supplied_handlers.get(SESSION_BUS_REPLY_ACTION_ID),
        surfaces=(SESSION_BUS_REPLY_ACTION_ID,),
    )
    registry.register_legacy_surface(
        "/v1/master-messages/{message_id}/complete"
    )
    registry.register_legacy_surface(
        "/v1/session-bus/messages/{message_id}/reply"
    )
    for action_id in ("service.status", "service.restart"):
        if action_id in supplied_handlers:
            registry.register(
                ActionContract(
                    action_id=action_id,
                    request_schema_ref=f"universe.{action_id}-request.v1",
                    result_schema_ref="universe.service-status.v1" if action_id == "service.status" else "universe.service-restart.v1",
                    side_effect_class="READ_ONLY" if action_id == "service.status" else "SERVICE_LIFECYCLE_MUTATION",
                    metadata={"target_resolution": "SERVER_SIDE", "preserves_pty_supervisor": True,
                              "request_fields": ["operation_id?"] if action_id == "service.status" else ["request_id", "expected_pid"]},
                ), supplied_handlers[action_id], surfaces=(action_id,),
            )
    for action_id in ("project.draft.read", "project.draft.list", "project.draft.save"):
        if action_id in supplied_handlers:
            registry.register(ActionContract(
                action_id=action_id,
                request_schema_ref=f"universe.{action_id}-request.v1",
                result_schema_ref="universe.project-draft-action.v1",
                side_effect_class="LOCAL_DATABASE_MUTATION" if action_id.endswith("save") else "READ_ONLY",
                metadata={"ownership": "PROJECT_AUTHORING", "revision_control": "EXPECTED_REVISION",
                          "fields": ["title", "domain", "description", "goal", "target_users", "scenarios", "structure", "capabilities", "validation", "constraints", "project_root"]},
            ), supplied_handlers[action_id], surfaces=(action_id,))
    # Only handler-backed work-surface Actions are registered as discoverable
    # contracts. The remaining pending todo.* Actions are intentionally left
    # unregistered (coverage reports them UNCOVERED, never available) until
    # they have both a handler and a receipt-aware path where one is needed
    # - see docs/action-ir-work-surface.md. todo.bind_goal (2026-09-15),
    # todo.bind_node (2026-09-16), todo.priority (2026-09-16),
    # todo.reorder (2026-09-16), todo.move_project (2026-09-17),
    # todo.archive (2026-09-17), and todo.restore (2026-09-17) moved out of
    # that pending set: like todo.update, they are metadata edits (goal_id /
    # node_ref / priority / sort_order / project_id / archived_at only,
    # CAS-guarded), not lifecycle transitions, so they need no receipt path
    # of their own.
    implemented_specs = (
        (
            FEATURE_CREATE_ACTION_ID,
            FEATURE_CREATE_REQUEST_SCHEMA,
            FEATURE_CREATE_RESULT_SCHEMA,
        ),
        (
            TODO_CREATE_ACTION_ID,
            "universe.todo-create-action-request.v1",
            "universe.todo-create-receipt.v1",
        ),
        (
            TODO_UPDATE_ACTION_ID,
            "universe.todo-update-action-request.v1",
            "universe.todo-update-receipt.v1",
        ),
    )
    for action_id, request_schema, result_schema in implemented_specs:
        registry.register(
            ActionContract(
                action_id=action_id,
                request_schema_ref=request_schema,
                result_schema_ref=result_schema,
                side_effect_class="LOCAL_DATABASE_MUTATION",
                metadata={"credential_handling": "CREDENTIAL_REF_ONLY"},
            ),
            supplied_handlers.get(action_id),
            surfaces=(action_id,),
        )
    registry.register(
        ActionContract(
            action_id=TODO_BIND_GOAL_ACTION_ID,
            request_schema_ref="universe.todo-bind-goal-action-request.v1",
            result_schema_ref="universe.todo-bind-goal-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_BIND_GOAL_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                # A lost response cannot be safely resolved by literal request
                # replay: this is a narrow CAS write (expected_revision), so
                # repeating the exact same call after it already applied
                # returns 409 TODO_REVISION_CONFLICT, not a replayed success.
                # Recover by calling todo.read and comparing goal_id/revision
                # to the intended target state before deciding whether to
                # retry with a fresh expected_revision.
                "replay": "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_GOAL_BINDING_REQUEST_INVALID",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                    "GOAL_NOT_FOUND",
                    "TODO_GOAL_BINDING_PROJECT_MISMATCH",
                ],
            },
        ),
        supplied_handlers.get(TODO_BIND_GOAL_ACTION_ID),
        surfaces=(TODO_BIND_GOAL_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=TODO_BIND_NODE_ACTION_ID,
            request_schema_ref="universe.todo-bind-node-action-request.v1",
            result_schema_ref="universe.todo-bind-node-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_BIND_NODE_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                "replay": "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_NODE_BINDING_REQUEST_INVALID",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                    "FEATURE_NODE_NOT_FOUND",
                    "TODO_NODE_BINDING_PROJECT_MISMATCH",
                    "TODO_NODE_BINDING_SCOPE_INVALID",
                    "TODO_NODE_BINDING_GOAL_SCOPE_CONFLICT",
                ],
            },
        ),
        supplied_handlers.get(TODO_BIND_NODE_ACTION_ID),
        surfaces=(TODO_BIND_NODE_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=TODO_PRIORITY_ACTION_ID,
            request_schema_ref="universe.todo-priority-action-request.v1",
            result_schema_ref="universe.todo-priority-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_PRIORITY_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                "replay": "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_PRIORITY_REQUEST_INVALID",
                    "TODO_PRIORITY_INVALID",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                ],
            },
        ),
        supplied_handlers.get(TODO_PRIORITY_ACTION_ID),
        surfaces=(TODO_PRIORITY_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=TODO_REORDER_ACTION_ID,
            request_schema_ref="universe.todo-reorder-action-request.v1",
            result_schema_ref="universe.todo-reorder-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_REORDER_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                "replay": "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_REORDER_REQUEST_INVALID",
                    "TODO_SORT_ORDER_INVALID",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                ],
            },
        ),
        supplied_handlers.get(TODO_REORDER_ACTION_ID),
        surfaces=(TODO_REORDER_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=TODO_MOVE_PROJECT_ACTION_ID,
            request_schema_ref="universe.todo-move-project-action-request.v1",
            result_schema_ref="universe.todo-move-project-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_MOVE_PROJECT_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                "replay": "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_MOVE_PROJECT_REQUEST_INVALID",
                    "TODO_MOVE_PROJECT_SCOPE_CONFLICT",
                    "TODO_MOVE_PROJECT_SAME_TARGET",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                    "PROJECT_NOT_FOUND",
                ],
            },
        ),
        supplied_handlers.get(TODO_MOVE_PROJECT_ACTION_ID),
        surfaces=(TODO_MOVE_PROJECT_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=TODO_ARCHIVE_ACTION_ID,
            request_schema_ref="universe.todo-archive-action-request.v1",
            result_schema_ref="universe.todo-archive-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_ARCHIVE_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                "replay": "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_ARCHIVE_REQUEST_INVALID",
                    "TODO_ALREADY_ARCHIVED",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                ],
            },
        ),
        supplied_handlers.get(TODO_ARCHIVE_ACTION_ID),
        surfaces=(TODO_ARCHIVE_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=TODO_RESTORE_ACTION_ID,
            request_schema_ref="universe.todo-restore-action-request.v1",
            result_schema_ref="universe.todo-restore-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_RESTORE_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                "replay": "NOT_IDEMPOTENT_CAS_RECOVER_VIA_TODO_READ",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_RESTORE_REQUEST_INVALID",
                    "TODO_NOT_ARCHIVED",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                ],
            },
        ),
        supplied_handlers.get(TODO_RESTORE_ACTION_ID),
        surfaces=(TODO_RESTORE_ACTION_ID,),
    )
    registry.register(
        ActionContract(
            action_id=TODO_REDO_ACTION_ID,
            request_schema_ref="universe.todo-redo-action-request.v1",
            result_schema_ref="universe.todo-redo-receipt.v1",
            side_effect_class="LOCAL_DATABASE_MUTATION",
            metadata={
                "request_schema": TODO_REDO_REQUEST_SCHEMA,
                "credential_handling": "CREDENTIAL_REF_ONLY",
                "session_selection": "NOT_REQUIRED",
                "authentication": "LOCAL_READ",
                "replay": "IDEMPOTENT_BY_REQUEST_ID",
                "description": "Start a Todo over: copy it as a fresh READY Todo and "
                "archive the original (history is kept on the archived row).",
                "errors": [
                    "REQUEST_INVALID",
                    "IDENTIFIER_INVALID",
                    "TODO_REDO_REQUEST_INVALID",
                    "TODO_REDO_REQUEST_CONFLICT",
                    "TODO_REDO_DONE_NOT_ALLOWED",
                    "TODO_REDO_ACTIVE_RUN",
                    "TODO_ALREADY_ARCHIVED",
                    "TODO_REVISION_INVALID",
                    "TODO_REVISION_CONFLICT",
                    "TODO_NOT_FOUND",
                ],
            },
        ),
        supplied_handlers.get(TODO_REDO_ACTION_ID),
        surfaces=(TODO_REDO_ACTION_ID,),
    )
    from universe_todo_actions import REQUEST_SCHEMAS
    for action_id in (TODO_READ_ACTION_ID, TODO_LIST_ACTION_ID, TODO_STATE_ACTION_ID):
        registry.register(
            ActionContract(
                action_id=action_id,
                request_schema_ref=f"universe.{action_id}-request.v1",
                result_schema_ref=f"universe.todo-{action_id.split('.')[1]}-action.v1",
                side_effect_class="LOCAL_DATABASE_MUTATION" if action_id == TODO_STATE_ACTION_ID else "READ_ONLY",
                metadata={
                    "request_schema": REQUEST_SCHEMAS[action_id],
                    "session_selection": "NOT_REQUIRED",
                    "authentication": "OPERATOR_TRANSPORT" if action_id == TODO_STATE_ACTION_ID else "LOCAL_READ",
                    "replay": "SAME_REQUEST_ID_AND_CONTENT" if action_id == TODO_STATE_ACTION_ID else "READ_ONLY",
                    "errors": ["TODO_ACTION_REQUEST_INVALID", "TODO_NOT_FOUND", "TODO_SCOPE_CONFLICT", "TODO_REVISION_CONFLICT", "TODO_STATE_REQUEST_CONFLICT", "TODO_COMPLETION_VALIDATION_REQUIRED", "TODO_OPERATOR_REQUIRED"],
                },
            ), supplied_handlers.get(action_id), surfaces=(action_id,),
        )
    # The HTTP surfaces these Actions front remain first-class legacy routes.
    registry.register_legacy_surface("/v1/todos")
    registry.register_legacy_surface("/v1/todos/{todo_id}")
    registry.register_legacy_surface("/v1/projects/{project_id}/feature-nodes")
    # Existing supervised automation still uses its Anchor-aware domain gateway.
    registry.register_legacy_surface("/v1/todo-action-mutation-receipts")
    registry.register_legacy_surface("/v1/todos/{todo_id}/actions")
    return registry


DEFAULT_ACTION_REGISTRY = build_default_action_registry()


__all__ = [
    "ACTION_CONTEXT_SCHEMA",
    "ACTION_CONTRACT_VERSION",
    "ACTION_COVERAGE_CLASSES",
    "ACTION_CREDENTIAL_REF_FIELD",
    "ACTION_REGISTRY_SCHEMA",
    "ActionContract",
    "ActionContractError",
    "ActionRegistry",
    "ActionRegistryError",
    "COVERED",
    "DEFAULT_ACTION_REGISTRY",
    "DuplicateActionError",
    "FEATURE_CREATE_ACTION_ID",
    "FEATURE_CREATE_REQUEST_SCHEMA",
    "FEATURE_CREATE_RESULT_SCHEMA",
    "FEATURE_GOAL_START_ACTION_ID",
    "FEATURE_GOAL_START_ACTION_SURFACE",
    "IMPLEMENTED_WORK_SURFACE_ACTION_IDS",
    "PENDING_WORK_SURFACE_ACTION_IDS",
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
    "MEMORY_SYNC_PERSIST_SELECTED_ACTION_ID",
    "MEMORY_SYNC_PERSIST_SELECTED_ACTION_SURFACE",
    "MEMORY_SYNC_PERSIST_SELECTED_REQUEST_SCHEMA",
    "MEMORY_SYNC_PERSIST_SELECTED_RESULT_SCHEMA",
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
    "MASTER_COMPLETE_ACTION_ID",
    "MASTER_COMPLETE_REQUEST_SCHEMA",
    "MASTER_COMPLETE_RESULT_SCHEMA",
    "SESSION_BUS_REPLY_ACTION_ID",
    "SESSION_BUS_REPLY_REQUEST_SCHEMA",
    "SESSION_BUS_REPLY_RESULT_SCHEMA",
    "PERSONA_AUTOMATION_ACTION_IDS",
    "RegisteredAction",
    "SENSITIVE_CREDENTIAL_FIELDS",
    "SERVER_RESOLVED_CALLER_FIELDS",
    "TODO_ACTION_IDS",
    "TODO_ARCHIVE_ACTION_ID",
    "TODO_ARCHIVE_REQUEST_SCHEMA",
    "TODO_RESTORE_REQUEST_SCHEMA",
    "TODO_REDO_ACTION_ID",
    "TODO_REDO_REQUEST_SCHEMA",
    "TODO_BIND_GOAL_ACTION_ID",
    "TODO_BIND_GOAL_REQUEST_SCHEMA",
    "TODO_BIND_NODE_ACTION_ID",
    "TODO_BIND_NODE_REQUEST_SCHEMA",
    "TODO_CREATE_ACTION_ID",
    "TODO_DELETE_ACTION_ID",
    "TODO_MOVE_PROJECT_ACTION_ID",
    "TODO_PRIORITY_ACTION_ID",
    "TODO_PRIORITY_REQUEST_SCHEMA",
    "TODO_REORDER_ACTION_ID",
    "TODO_REORDER_REQUEST_SCHEMA",
    "TODO_MOVE_PROJECT_REQUEST_SCHEMA",
    "TODO_RESTORE_ACTION_ID",
    "TODO_STATE_ACTION_ID",
    "TODO_READ_ACTION_ID",
    "TODO_LIST_ACTION_ID",
    "TODO_UPDATE_ACTION_ID",
    "UNCOVERED",
    "UnknownActionError",
    "build_default_action_registry",
    "canonical_value_sha256",
    "derive_idempotency_key",
    "find_forbidden_caller_fields",
    "find_forbidden_credential_fields",
    "utf8_sha256",
]
