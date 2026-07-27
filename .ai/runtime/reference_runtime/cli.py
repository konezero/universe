"""JSON CLI for bounded ai-career reference runtime operations."""

from __future__ import annotations

import codecs
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import quote


if __package__:
    from .anchor_session_memory_adapter import (
        AnchorSessionMemoryHostAdapter,
        AnchorSessionMemoryHostServer,
        call_host_adapter,
    )
    from .execution_guard_adapter import invoke_execution_guard
    from .task_proposal_journal import TaskProposalError, TaskProposalJournal
    from .mode_registry_runtime import (
        ModeRegistryError,
        load_mode_registry,
        plan_mode_registry_mutation,
        resolve_mode_inbox,
    )
    from .os_status_runtime import (
        OsStatusError,
        evaluate_source_only_os_status,
    )
    from .continuity_runtime import (
        ContinuityCommandError,
        load_continuity_profile,
        run_continuity_command,
    )
    from .continuity_store_runtime import ContinuityStore, ContinuityStoreError
    from .session_boot_adapter import prepare_session_boot_server
    from .source_review_runtime import SourceReviewError, evaluate_source_review
    from .session_boot_runtime import (
        SessionPreparationRequest,
        SessionBootCoordinates,
        SessionBootError,
        evaluate_session_preparation,
    )
    from .task_frame_runtime import (
        ParentObservation,
        TaskFrameRuntime,
        TaskTurn,
        build_task_frame_execution_proposal,
        load_profile,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from reference_runtime.anchor_session_memory_adapter import (
        AnchorSessionMemoryHostAdapter,
        AnchorSessionMemoryHostServer,
        call_host_adapter,
    )
    from reference_runtime.execution_guard_adapter import invoke_execution_guard
    from reference_runtime.task_proposal_journal import (
        TaskProposalError,
        TaskProposalJournal,
    )
    from reference_runtime.mode_registry_runtime import (
        ModeRegistryError,
        load_mode_registry,
        plan_mode_registry_mutation,
        resolve_mode_inbox,
    )
    from reference_runtime.os_status_runtime import (
        OsStatusError,
        evaluate_source_only_os_status,
    )
    from reference_runtime.continuity_runtime import (
        ContinuityCommandError,
        load_continuity_profile,
        run_continuity_command,
    )
    from reference_runtime.continuity_store_runtime import (
        ContinuityStore,
        ContinuityStoreError,
    )
    from reference_runtime.session_boot_adapter import prepare_session_boot_server
    from reference_runtime.source_review_runtime import (
        SourceReviewError,
        evaluate_source_review,
    )
    from reference_runtime.session_boot_runtime import (
        SessionPreparationRequest,
        SessionBootCoordinates,
        SessionBootError,
        evaluate_session_preparation,
    )
    from reference_runtime.task_frame_runtime import (
        ParentObservation,
        TaskFrameRuntime,
        TaskTurn,
        build_task_frame_execution_proposal,
        load_profile,
    )


CAPABILITIES_PATH = Path(__file__).with_name("capabilities.json")
MAX_REQUEST_BYTES = 1024 * 1024


def _physical_time() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class CliFailure(Exception):
    error_code: str
    detail: str
    exit_code: int = 2
    context: Mapping[str, Any] | None = None


def _emit(payload: Mapping[str, Any]) -> None:
    print(
        json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        flush=True,
    )


def _failure_payload(error: CliFailure) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "UNKNOWN",
        "error_code": error.error_code,
        "detail": error.detail,
    }
    if error.context:
        payload.update(error.context)
    return payload


def _read_capabilities() -> dict[str, Any]:
    try:
        payload = json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CliFailure("CAPABILITIES_UNAVAILABLE", str(error), 3) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("capabilities"), dict):
        raise CliFailure("CAPABILITIES_INVALID", "capabilities.json has an invalid shape", 3)
    return payload


def _capabilities(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    payload = _read_capabilities()
    if not args:
        return 0, payload
    if len(args) == 2 and args[0] == "--capability":
        name = args[1]
    elif len(args) == 1:
        name = args[0]
    else:
        raise CliFailure(
            "CLI_USAGE_ERROR",
            "capabilities accepts no arguments or one capability name",
        )
    value = payload["capabilities"].get(name)
    if value is None:
        return 2, {
            "capability": name,
            "status": "UNKNOWN",
            "reason": "UNSUPPORTED_CAPABILITY",
        }
    return 0, {"capability": name, "status": value}


def _parse_options(
    args: Sequence[str],
    *,
    required: frozenset[str] = frozenset(),
    defaults: Mapping[str, str] | None = None,
) -> dict[str, str]:
    default_values = dict(defaults or {})
    values = dict(default_values)
    allowed = set(required).union(default_values)
    seen: set[str] = set()
    index = 0
    while index < len(args):
        key = args[index]
        if not key.startswith("--") or key not in allowed:
            raise CliFailure("CLI_USAGE_ERROR", f"unsupported option: {key}")
        if index + 1 >= len(args):
            raise CliFailure("CLI_USAGE_ERROR", f"missing value for {key}")
        if key in seen:
            raise CliFailure("CLI_USAGE_ERROR", f"duplicate option: {key}")
        seen.add(key)
        values[key] = args[index + 1]
        index += 2
    missing = sorted(required.difference(values))
    if missing:
        raise CliFailure("CLI_USAGE_ERROR", f"missing required option: {missing[0]}")
    return values


def _decode_request_bytes(raw: bytes) -> str:
    """Decode one JSON envelope without trusting the Host text wrapper."""

    try:
        if raw.startswith(codecs.BOM_UTF8):
            return raw.decode("utf-8-sig")
        if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return raw.decode("utf-16")
        if b"\x00" in raw[:64]:
            for encoding in ("utf-16-le", "utf-16-be"):
                try:
                    candidate = raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
                if candidate.lstrip().startswith(("{", "[")):
                    return candidate
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CliFailure(
            "REQUEST_ENCODING_UNSUPPORTED",
            "request must be UTF-8 or UTF-16 JSON",
        ) from error


def _load_request(source: str) -> dict[str, Any]:
    try:
        if source == "-":
            raw_bytes = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            if len(raw_bytes) > MAX_REQUEST_BYTES:
                raise CliFailure("REQUEST_TOO_LARGE", "request exceeds 1 MiB")
            raw = _decode_request_bytes(raw_bytes)
        else:
            path = Path(source)
            if path.stat().st_size > MAX_REQUEST_BYTES:
                raise CliFailure("REQUEST_TOO_LARGE", "request exceeds 1 MiB")
            raw = _decode_request_bytes(path.read_bytes())
    except CliFailure:
        raise
    except OSError as error:
        raise CliFailure("REQUEST_UNAVAILABLE", str(error)) from error
    if source != "-" and len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise CliFailure("REQUEST_TOO_LARGE", "request exceeds 1 MiB")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CliFailure("REQUEST_JSON_INVALID", str(error)) from error
    if not isinstance(payload, dict):
        raise CliFailure("REQUEST_SHAPE_INVALID", "request root must be an object")
    return payload


def _mapping(value: Any, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CliFailure("REQUEST_SHAPE_INVALID", f"{context} must be an object")
    return value


def _sequence(value: Any, context: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise CliFailure("REQUEST_SHAPE_INVALID", f"{context} must be an array")
    return value


def _exact_fields(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extra = sorted(set(value).difference(allowed))
    if extra:
        raise CliFailure(
            "REQUEST_FIELD_UNSUPPORTED",
            f"{context} contains unsupported field: {extra[0]}",
        )


def _required_text(value: Mapping[str, Any], field: str, context: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise CliFailure("REQUEST_FIELD_INVALID", f"{context}.{field} must be a non-empty string")
    return item


def _optional_text(
    value: Mapping[str, Any], field: str, context: str, *, default: str = ""
) -> str:
    item = value.get(field, default)
    if not isinstance(item, str):
        raise CliFailure("REQUEST_FIELD_INVALID", f"{context}.{field} must be a string")
    return item


def _parent_observation(value: Any, context: str) -> ParentObservation:
    payload = _mapping(value, context)
    _exact_fields(payload, {"status", "evidence_ref"}, context)
    return ParentObservation(
        status=_required_text(payload, "status", context),
        evidence_ref=_required_text(payload, "evidence_ref", context),
    )


def _task_turn(value: Any, context: str) -> TaskTurn:
    payload = _mapping(value, context)
    _exact_fields(
        payload,
        {
            "turn_id",
            "role",
            "input_turn_ids",
            "accept_turn_id",
            "return_turn_id",
            "terminal_on_accept",
        },
        context,
    )
    input_turn_ids = payload.get("input_turn_ids", [])
    if not isinstance(input_turn_ids, list) or not all(isinstance(item, str) for item in input_turn_ids):
        raise CliFailure("REQUEST_FIELD_INVALID", f"{context}.input_turn_ids must be a string array")
    terminal = payload.get("terminal_on_accept", False)
    if not isinstance(terminal, bool):
        raise CliFailure("REQUEST_FIELD_INVALID", f"{context}.terminal_on_accept must be boolean")
    return TaskTurn(
        turn_id=_required_text(payload, "turn_id", context),
        role=_required_text(payload, "role", context),
        input_turn_ids=tuple(input_turn_ids),
        accept_turn_id=_optional_text(payload, "accept_turn_id", context),
        return_turn_id=_optional_text(payload, "return_turn_id", context),
        terminal_on_accept=terminal,
    )


def _task_operation(runtime: TaskFrameRuntime, value: Any, index: int) -> Any:
    context = f"operations[{index}]"
    payload = _mapping(value, context)
    operation = _required_text(payload, "operation", context)

    if operation == "observe_parent":
        _exact_fields(payload, {"operation", "parent_observation", "observed_at"}, context)
        return runtime.observe_parent(
            parent_observation=_parent_observation(payload.get("parent_observation"), f"{context}.parent_observation"),
            observed_at=_required_text(payload, "observed_at", context),
        )
    if operation == "record_parent_instruction":
        _exact_fields(
            payload,
            {"operation", "instruction", "observed_at"},
            context,
        )
        return runtime.record_parent_instruction(
            instruction=_mapping(
                payload.get("instruction"), f"{context}.instruction"
            ),
            observed_at=_required_text(payload, "observed_at", context),
        )
    if operation == "declare_turns":
        _exact_fields(payload, {"operation", "turns", "observed_at"}, context)
        turns = _sequence(payload.get("turns"), f"{context}.turns")
        return runtime.declare_turns(
            turns=tuple(_task_turn(turn, f"{context}.turns[{turn_index}]") for turn_index, turn in enumerate(turns)),
            observed_at=_required_text(payload, "observed_at", context),
        )
    if operation == "worker_invocation_plan":
        _exact_fields(
            payload,
            {
                "operation",
                "turn_id",
                "host_capability_status",
                "capability_evidence_ref",
                "invoker_actor_ref",
                "observed_at",
            },
            context,
        )
        return runtime.worker_invocation_plan(
            turn_id=_required_text(payload, "turn_id", context),
            host_capability_status=_required_text(payload, "host_capability_status", context),
            capability_evidence_ref=_optional_text(
                payload, "capability_evidence_ref", context
            ),
            invoker_actor_ref=_optional_text(
                payload, "invoker_actor_ref", context
            ),
            observed_at=_required_text(payload, "observed_at", context),
        )
    if operation == "claim_turn":
        _exact_fields(
            payload,
            {
                "operation",
                "turn_id",
                "worker_id",
                "host_invocation_receipt_ref",
                "capability_evidence_ref",
                "invoker_actor_ref",
                "observed_at",
            },
            context,
        )
        return runtime.claim_turn(
            turn_id=_required_text(payload, "turn_id", context),
            worker_id=_required_text(payload, "worker_id", context),
            host_invocation_receipt_ref=_optional_text(
                payload, "host_invocation_receipt_ref", context
            ),
            capability_evidence_ref=_optional_text(
                payload, "capability_evidence_ref", context
            ),
            invoker_actor_ref=_optional_text(
                payload, "invoker_actor_ref", context
            ),
            observed_at=_required_text(payload, "observed_at", context),
        )
    if operation == "submit_boss_allocations":
        _exact_fields(
            payload,
            {
                "operation",
                "boss_turn_id",
                "boss_worker_id",
                "host_invocation_receipt_ref",
                "instruction_digests",
                "worker_allocations",
                "observed_at",
            },
            context,
        )
        instruction_digests = _sequence(
            payload.get("instruction_digests"),
            f"{context}.instruction_digests",
        )
        allocations = _sequence(
            payload.get("worker_allocations"),
            f"{context}.worker_allocations",
        )
        if not all(isinstance(item, str) for item in instruction_digests):
            raise CliFailure(
                "REQUEST_FIELD_INVALID",
                f"{context}.instruction_digests must be a string array",
            )
        return runtime.submit_boss_allocations(
            boss_turn_id=_required_text(payload, "boss_turn_id", context),
            boss_worker_id=_required_text(payload, "boss_worker_id", context),
            host_invocation_receipt_ref=_required_text(
                payload, "host_invocation_receipt_ref", context
            ),
            instruction_digests=instruction_digests,
            worker_allocations=tuple(
                _mapping(item, f"{context}.worker_allocations[{index}]")
                for index, item in enumerate(allocations)
            ),
            observed_at=_required_text(payload, "observed_at", context),
        )
    if operation == "boss_result_bundle":
        _exact_fields(
            payload,
            {
                "operation",
                "boss_turn_id",
                "boss_worker_id",
                "host_invocation_receipt_ref",
            },
            context,
        )
        return runtime.boss_result_bundle(
            boss_turn_id=_required_text(payload, "boss_turn_id", context),
            boss_worker_id=_required_text(payload, "boss_worker_id", context),
            host_invocation_receipt_ref=_required_text(
                payload, "host_invocation_receipt_ref", context
            ),
        )
    if operation == "complete_turn":
        _exact_fields(
            payload,
            {
                "operation",
                "turn_id",
                "worker_id",
                "result",
                "evidence_refs",
                "observed_at",
                "review_decision",
                "host_invocation_receipt_ref",
            },
            context,
        )
        result = _mapping(payload.get("result"), f"{context}.result")
        evidence_refs = _sequence(payload.get("evidence_refs"), f"{context}.evidence_refs")
        if not all(isinstance(item, str) for item in evidence_refs):
            raise CliFailure("REQUEST_FIELD_INVALID", f"{context}.evidence_refs must be a string array")
        review_decision = payload.get("review_decision", "")
        if not isinstance(review_decision, str):
            raise CliFailure("REQUEST_FIELD_INVALID", f"{context}.review_decision must be a string")
        return runtime.complete_turn(
            turn_id=_required_text(payload, "turn_id", context),
            worker_id=_required_text(payload, "worker_id", context),
            result=result,
            evidence_refs=tuple(evidence_refs),
            observed_at=_required_text(payload, "observed_at", context),
            review_decision=review_decision,
            host_invocation_receipt_ref=_optional_text(
                payload, "host_invocation_receipt_ref", context
            ),
        )
    if operation == "input_bundle":
        _exact_fields(payload, {"operation", "turn_id"}, context)
        return runtime.input_bundle(turn_id=_required_text(payload, "turn_id", context))
    if operation == "build_result_packet":
        _exact_fields(payload, {"operation"}, context)
        return runtime.build_result_packet()
    if operation == "task_frame_snapshot":
        _exact_fields(payload, {"operation"}, context)
        return runtime.task_frame_snapshot()
    if operation == "parent_observation_snapshot":
        _exact_fields(payload, {"operation"}, context)
        return runtime.parent_observation_snapshot()
    if operation == "parent_observations":
        _exact_fields(payload, {"operation"}, context)
        return runtime.parent_observations()
    if operation == "instruction_bundle":
        _exact_fields(payload, {"operation"}, context)
        return runtime.instruction_bundle()
    if operation == "instructions":
        _exact_fields(payload, {"operation"}, context)
        return runtime.instructions()
    if operation == "allocations":
        _exact_fields(payload, {"operation"}, context)
        return runtime.allocations()
    if operation == "turn_snapshot":
        _exact_fields(payload, {"operation", "turn_id"}, context)
        return runtime.turn_snapshot(_required_text(payload, "turn_id", context))
    if operation == "turns":
        _exact_fields(payload, {"operation"}, context)
        return runtime.turns()
    if operation == "journal":
        _exact_fields(payload, {"operation"}, context)
        return runtime.journal()
    if operation == "execution_evidence":
        _exact_fields(payload, {"operation"}, context)
        return runtime.execution_evidence()
    if operation == "runtime_state":
        _exact_fields(payload, {"operation"}, context)
        return runtime.runtime_state()
    if operation == "database_paths":
        _exact_fields(payload, {"operation"}, context)
        return runtime.database_paths()
    raise CliFailure(
        "TASK_FRAME_OPERATION_UNSUPPORTED",
        f"unsupported Task Frame operation: {operation}",
        context={"operation": operation},
    )


def _task_frame(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    task_frame_operation = "run"
    if args and args[0] in {"propose", "run", "continue", "status"}:
        task_frame_operation = args[0]
        args = args[1:]
    required_options = {"--repo-root", "--profile"}
    if task_frame_operation != "status":
        required_options.add("--request")
    options = _parse_options(
        args,
        required=frozenset(required_options),
        defaults={"--database": ""},
    )
    request = (
        _load_request(options["--request"])
        if task_frame_operation != "status"
        else None
    )
    if task_frame_operation == "propose":
        assert request is not None
        _exact_fields(request, {"execution_plan"}, "request")
        execution_plan = _mapping(request.get("execution_plan"), "execution_plan")
        try:
            profile = load_profile(
                Path(options["--repo-root"]), Path(options["--profile"])
            )
            proposal = build_task_frame_execution_proposal(execution_plan)
        except Exception as error:
            raise CliFailure("TASK_FRAME_PROPOSAL_INVALID", str(error), 3) from error
        if proposal["execution_plan"]["profile_id"] != profile.profile_id:
            raise CliFailure(
                "TASK_FRAME_PROPOSAL_INVALID",
                "execution_plan.profile_id does not match the selected profile",
                3,
            )
        return 0, {
            "capability": "task_frame_ledger",
            "profile_id": profile.profile_id,
            "execution_proposal": proposal,
        }

    if task_frame_operation == "status":
        if not options["--database"]:
            raise CliFailure("TASK_FRAME_DATABASE_REQUIRED", "--database is required for status")
        try:
            profile = load_profile(Path(options["--repo-root"]), Path(options["--profile"]))
            runtime = TaskFrameRuntime.open_existing(
                profile=profile,
                database_path=options["--database"],
            )
        except Exception as error:
            raise CliFailure("TASK_FRAME_INITIALIZATION_FAILED", str(error), 3) from error
        try:
            return 0, {
                "capability": "task_frame_ledger",
                "execution_evidence": runtime.execution_evidence(),
                "runtime_state": runtime.runtime_state(),
                "database_paths": runtime.database_paths(),
            }
        finally:
            runtime.close()

    assert request is not None
    if task_frame_operation == "continue":
        _exact_fields(request, {"operations"}, "request")
        if not options["--database"]:
            raise CliFailure("TASK_FRAME_DATABASE_REQUIRED", "--database is required for continue")
        try:
            profile = load_profile(Path(options["--repo-root"]), Path(options["--profile"]))
            runtime = TaskFrameRuntime.open_existing(
                profile=profile,
                database_path=options["--database"],
            )
        except Exception as error:
            raise CliFailure("TASK_FRAME_INITIALIZATION_FAILED", str(error), 3) from error
        operations = _sequence(request.get("operations"), "operations")
        try:
            operation_results = [
                {
                    "operation": _required_text(_mapping(item, f"operations[{index}]"), "operation", f"operations[{index}]"),
                    "output": _task_operation(runtime, item, index),
                }
                for index, item in enumerate(operations)
            ]
            return 0, {
                "capability": "task_frame_ledger",
                "operation_results": operation_results,
                "execution_evidence": runtime.execution_evidence(),
                "runtime_state": runtime.runtime_state(),
                "database_paths": runtime.database_paths(),
            }
        finally:
            runtime.close()

    _exact_fields(request, {"frame", "operations"}, "request")
    frame = _mapping(request.get("frame"), "frame")
    _exact_fields(
        frame,
        {
            "frame_id",
            "origin_anchor_ref",
            "origin_session_id",
            "origin_frame_id",
            "origin_governance_session_ref",
            "task_summary_ref",
            "source_ref",
            "execution_assignment_ref",
            "task_frame_execution_proposal",
            "task_frame_execution_approval",
            "parent_instruction",
            "dispatch_topology",
            "parent_observation",
            "observed_at",
        },
        "frame",
    )
    operations = _sequence(request.get("operations"), "operations")
    execution_assignment_ref = _optional_text(
        frame,
        "execution_assignment_ref",
        "frame",
        default="UNASSIGNED",
    )
    if not execution_assignment_ref.strip():
        raise CliFailure(
            "REQUEST_FIELD_INVALID",
            "frame.execution_assignment_ref must be a non-empty string",
        )
    execution_proposal = frame.get("task_frame_execution_proposal")
    if execution_proposal is not None:
        execution_proposal = _mapping(
            execution_proposal, "frame.task_frame_execution_proposal"
        )
    execution_approval = frame.get("task_frame_execution_approval")
    if execution_approval is not None:
        execution_approval = _mapping(
            execution_approval, "frame.task_frame_execution_approval"
        )
    parent_instruction = frame.get("parent_instruction")
    if parent_instruction is not None:
        parent_instruction = _mapping(
            parent_instruction, "frame.parent_instruction"
        )
    try:
        profile = load_profile(Path(options["--repo-root"]), Path(options["--profile"]))
        runtime = TaskFrameRuntime(
            profile=profile,
            frame_id=_required_text(frame, "frame_id", "frame"),
            origin_anchor_ref=_required_text(frame, "origin_anchor_ref", "frame"),
            origin_session_id=_required_text(frame, "origin_session_id", "frame"),
            origin_frame_id=_required_text(frame, "origin_frame_id", "frame"),
            task_summary_ref=_required_text(frame, "task_summary_ref", "frame"),
            source_ref=_required_text(frame, "source_ref", "frame"),
            origin_governance_session_ref=_optional_text(
                frame,
                "origin_governance_session_ref",
                "frame",
                default="UNKNOWN",
            ),
            execution_assignment_ref=execution_assignment_ref,
            task_frame_execution_proposal=execution_proposal,
            task_frame_execution_approval=execution_approval,
            parent_instruction=parent_instruction,
            dispatch_topology=(
                _mapping(frame.get("dispatch_topology"), "frame.dispatch_topology")
                if frame.get("dispatch_topology") is not None
                else None
            ),
            database_path=options["--database"] or None,
            parent_observation=_parent_observation(frame.get("parent_observation"), "frame.parent_observation"),
            observed_at=_required_text(frame, "observed_at", "frame"),
        )
    except CliFailure:
        raise
    except Exception as error:
        raise CliFailure("TASK_FRAME_INITIALIZATION_FAILED", str(error), 3) from error

    try:
        operation_results = [
            {
                "operation": _required_text(_mapping(item, f"operations[{index}]"), "operation", f"operations[{index}]"),
                "output": _task_operation(runtime, item, index),
            }
            for index, item in enumerate(operations)
        ]
        return 0, {
            "capability": "task_frame_ledger",
            "operation_results": operation_results,
            "execution_evidence": runtime.execution_evidence(),
            "runtime_state": runtime.runtime_state(),
            "database_paths": runtime.database_paths(),
        }
    finally:
        runtime.close()


def _anchor_operation(
    adapter: AnchorSessionMemoryHostAdapter, value: Any, index: int
) -> Any:
    context = f"operations[{index}]"
    payload = _mapping(value, context)
    operation = _required_text(payload, "operation", context)
    if operation == "activate":
        _exact_fields(payload, {"operation", "payload"}, context)
        return adapter.activate(_mapping(payload.get("payload"), f"{context}.payload"))
    if operation == "record_observation":
        _exact_fields(payload, {"operation", "payload"}, context)
        return adapter.record_observation(_mapping(payload.get("payload"), f"{context}.payload"))
    if operation in {"status", "evidence", "stop"}:
        _exact_fields(payload, {"operation", "session_id"}, context)
        session_id = _required_text(payload, "session_id", context)
        return getattr(adapter, operation)(session_id=session_id)
    raise CliFailure(
        "ANCHOR_MEMORY_OPERATION_UNSUPPORTED",
        f"unsupported Anchor Session Memory operation: {operation}",
        context={"operation": operation},
    )


def _anchor_batch(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    options = _parse_options(args, required=frozenset({"--request"}))
    request = _load_request(options["--request"])
    _exact_fields(request, {"operations"}, "request")
    operations = _sequence(request.get("operations"), "operations")
    adapter = AnchorSessionMemoryHostAdapter()
    try:
        return 0, {
            "capability": "anchor_session_memory",
            "operation_results": [
                {
                    "operation": _required_text(_mapping(item, f"operations[{index}]"), "operation", f"operations[{index}]"),
                    "output": _anchor_operation(adapter, item, index),
                }
                for index, item in enumerate(operations)
            ],
        }
    finally:
        adapter.close()


def _anchor_serve(args: Sequence[str]) -> int:
    options = _parse_options(
        args,
        defaults={"--port": "8765", "--token": ""},
    )
    try:
        port = int(options["--port"])
    except ValueError as error:
        raise CliFailure("CLI_USAGE_ERROR", "--port must be an integer") from error
    server = AnchorSessionMemoryHostServer(port=port, token=options["--token"])
    _emit(server.metadata())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


def _session_boot_serve(args: Sequence[str]) -> int:
    options = _parse_options(
        args,
        required=frozenset(
            {
                "--repo-root",
                "--session-id",
                "--anchor-id",
                "--host-action",
                "--session-location",
                "--commander-surface",
                "--execution-surface",
                "--repository-location",
            }
        ),
        defaults={
            "--frame-id": "current",
            "--port": "0",
            "--token": "",
        },
    )
    try:
        port = int(options["--port"])
    except ValueError as error:
        raise CliFailure("CLI_USAGE_ERROR", "--port must be an integer") from error
    try:
        prepared = prepare_session_boot_server(
            repo_root=Path(options["--repo-root"]),
            coordinates=SessionBootCoordinates(
                session_id=options["--session-id"],
                frame_id=options["--frame-id"],
                anchor_id=options["--anchor-id"],
                host_action=options["--host-action"],
                session_location=options["--session-location"],
                commander_surface=options["--commander-surface"],
                execution_surface=options["--execution-surface"],
                repository_location=options["--repository-location"],
            ),
            port=port,
            token=options["--token"],
        )
    except SessionBootError as error:
        raise CliFailure(error.error_code, error.detail, 3) from error

    _emit(prepared.result)
    try:
        Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        prepared.server.stop()
    return 0


def _prepare_session(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    options = _parse_options(
        args,
        required=frozenset({"--request"}),
        defaults={"--repo-root": "", "--registry-root": ""},
    )
    request = _load_request(options["--request"])
    _exact_fields(
        request,
        {
            "command",
            "source_state",
            "source_ref",
            "source_commit",
            "source_repository",
            "mode",
            "role",
            "scope",
            "host_session_ref",
            "anchor_snapshot_ref",
            "host_executable_capability",
            "mode_profile",
            "task_requirement",
            "evidence_profile",
        },
        "request",
    )
    mode_profile = _optional_text(
        request, "mode_profile", "request", default="GOVERNANCE_ONLY"
    )
    repository_root = options["--repo-root"].strip()
    registry_root = repository_root or options["--registry-root"].strip()
    requested_mode = _required_text(request, "mode", "request")
    try:
        registry = None
        if requested_mode != "UNKNOWN":
            if not registry_root:
                raise ModeRegistryError(
                    "MODE_REGISTRY_UNAVAILABLE",
                    "selected Mode requires --repo-root or --registry-root",
                )
            registry = load_mode_registry(Path(registry_root))
        result = evaluate_session_preparation(
            SessionPreparationRequest(
                command=_required_text(request, "command", "request"),
                source_state=_required_text(request, "source_state", "request"),
                source_ref=_required_text(request, "source_ref", "request"),
                source_commit=_required_text(request, "source_commit", "request"),
                source_repository=_optional_text(
                    request,
                    "source_repository",
                    "request",
                    default="UNKNOWN",
                ),
                mode=requested_mode,
                role=_required_text(request, "role", "request"),
                scope=_required_text(request, "scope", "request"),
                host_session_ref=_optional_text(
                    request, "host_session_ref", "request", default="UNKNOWN"
                ),
                anchor_snapshot_ref=_optional_text(
                    request, "anchor_snapshot_ref", "request", default="UNKNOWN"
                ),
                host_executable_capability=_optional_text(
                    request,
                    "host_executable_capability",
                    "request",
                    default="UNKNOWN",
                ),
                mode_profile=mode_profile,
                task_requirement=_optional_text(
                    request, "task_requirement", "request", default="NONE"
                ),
                evidence_profile=_optional_text(
                    request, "evidence_profile", "request", default="NONE"
                ),
            ),
            mode_registry=registry,
        )
    except ModeRegistryError as error:
        raise CliFailure(error.error_code, error.detail, 4) from error
    except SessionBootError as error:
        raise CliFailure(error.error_code, error.detail, 3) from error
    if result["status"] in {"SESSION_PREPARED", "GOVERNANCE_REALIGNED"}:
        if repository_root:
            adapter = AnchorSessionMemoryHostAdapter(
                repository_root=Path(repository_root)
            )
            try:
                result["mode_current_anchor"] = adapter.prepare_mode_current_anchor(
                    {
                        "mode": result["mode"]["requested"],
                        "source_ref": request["source_ref"],
                        "host_session_ref": request.get("host_session_ref", "UNKNOWN"),
                    }
                )
            finally:
                adapter.close()
        else:
            result["mode_current_anchor"] = {
                "status": "MODE_ANCHOR_STORE_UNBOUND"
            }
    return 0, result


def _mode_registry(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    if not args or args[0] not in {"list", "show", "plan"}:
        raise CliFailure(
            "CLI_USAGE_ERROR", "mode-registry requires list, show, or plan"
        )
    operation = args[0]
    required = {"--repo-root"}
    if operation == "show":
        required.add("--mode")
    if operation == "plan":
        required.add("--request")
    options = _parse_options(args[1:], required=frozenset(required))
    repository_root = Path(options["--repo-root"])
    try:
        registry = load_mode_registry(repository_root)
        if operation == "list":
            return 0, {
                "schema": "ai-career.mode-registry-result.v1",
                "status": "MODE_REGISTRY_LOADED",
                "owner": registry.owner,
                "repository_kind": registry.repository_kind,
                "policy": registry.policy,
                "root_mode": registry.root_mode,
                "revision": registry.revision,
                "modes": sorted(registry.modes),
                "repository_write": False,
            }
        if operation == "show":
            definition = registry.resolve(options["--mode"])
            return 0, {
                "schema": "ai-career.mode-registry-result.v1",
                "status": "MODE_REGISTRY_ENTRY_RESOLVED",
                "mode": definition.mode,
                "definition": definition.as_dict(),
                "inbox": resolve_mode_inbox(
                    repository_root, definition.mode
                ),
                "revision": registry.revision,
                "repository_write": False,
            }
        request = _load_request(options["--request"])
        return 0, plan_mode_registry_mutation(repository_root, request)
    except ModeRegistryError as error:
        raise CliFailure(error.error_code, error.detail, 4) from error


def _execution_binding(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    if not args or args[0] not in {
        "propose",
        "apply",
        "begin-work",
    }:
        raise CliFailure(
            "CLI_USAGE_ERROR",
            "execution-binding requires propose, apply, or begin-work",
        )
    operation = args[0]
    options = _parse_options(
        args[1:],
        required=frozenset({"--endpoint", "--token", "--request"}),
    )
    request = _load_request(options["--request"])
    _status, payload = call_host_adapter(
        endpoint=options["--endpoint"],
        token=options["--token"],
        method="POST",
        path=f"/v1/execution-binding/{operation}",
        payload=request,
    )
    expected = {
        "propose": "EXECUTION_ASSIGNMENT_PROPOSED",
        "apply": "EXECUTION_BINDING_APPLIED",
        "begin-work": "WORK_RECEIPT_ACTIVATED",
    }
    return (0 if payload.get("status") == expected[operation] else 4), payload


def _task_proposal(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    operations = {"create", "approve", "record-result", "status"}
    if not args or args[0] not in operations:
        raise CliFailure(
            "CLI_USAGE_ERROR",
            "task-proposal requires create, approve, record-result, or status",
        )
    operation = args[0]
    required = {"--repo-root"}
    if operation == "status":
        required.add("--proposal-id")
    else:
        required.add("--request")
    options = _parse_options(args[1:], required=frozenset(required))
    try:
        journal = TaskProposalJournal(Path(options["--repo-root"]))
        try:
            if operation == "status":
                return 0, journal.status(options["--proposal-id"])
            request = _load_request(options["--request"])
            observed_at = _physical_time()
            if operation == "create":
                result = journal.propose(request, observed_at=observed_at)
            elif operation == "approve":
                result = journal.approve(request, observed_at=observed_at)
            else:
                result = journal.record_result(request, observed_at=observed_at)
            return 0, result
        finally:
            journal.close()
    except TaskProposalError as error:
        raise CliFailure(error.error_code, error.detail, 4) from error


def _runtime_status(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    options = _parse_options(
        args,
        required=frozenset(
            {"--repo-root", "--endpoint", "--token", "--session-id"}
        ),
    )
    repo_root = Path(options["--repo-root"]).expanduser().resolve()
    installer = repo_root / ".ai" / "runtime" / "tools" / "project_runtime_installer.py"
    if installer.is_file():
        completed = subprocess.run(
            [sys.executable, str(installer), "status", "--target", str(repo_root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            cwd=repo_root,
        )
        try:
            repository_status: Mapping[str, Any] = json.loads(completed.stdout)
        except json.JSONDecodeError:
            repository_status = {
                "result": "UNKNOWN",
                "error_code": "INSTALLED_STATUS_OUTPUT_INVALID",
                "exit_code": completed.returncode,
            }
    else:
        repository_status = {
            "result": "UNKNOWN",
            "error_code": "INSTALLED_STATUS_TOOL_MISSING",
        }
    _http_status, session_status = call_host_adapter(
        endpoint=options["--endpoint"],
        token=options["--token"],
        method="GET",
        path=(
            "/v1/anchor-session-memory/status?session_id="
            + quote(options["--session-id"], safe="")
            + "&mode="
            + quote(str(repository_status.get("mode", "")), safe="")
        ),
    )
    repository_collected = repository_status.get("command") == "status"
    session_collected = session_status.get("status") == "HOST_SESSION_MEMORY_ACTIVE"
    result_status = (
        "RUNTIME_STATUS_COLLECTED"
        if repository_collected and session_collected
        else "RUNTIME_STATUS_PARTIAL"
    )
    return (
        0 if result_status == "RUNTIME_STATUS_COLLECTED" else 4,
        {
            "schema": "ai-career.runtime-status-collection.v1",
            "status": result_status,
            "repository_status": repository_status,
            "session_status": session_status,
            "semantic_merge": False,
            "authority_created": False,
            "repository_write": False,
        },
    )


def _os_status(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    if not args or args[0] != "source-only":
        raise CliFailure(
            "CLI_USAGE_ERROR", "os-status requires the source-only operation"
        )
    options = _parse_options(
        args[1:],
        required=frozenset({"--request"}),
    )
    request = _load_request(options["--request"])
    try:
        result = evaluate_source_only_os_status(request)
    except OsStatusError as error:
        raise CliFailure("OS_STATUS_REQUEST_INVALID", str(error)) from error
    return 0, result


def _execution_guard(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    if not args or args[0] not in {"check", "consume"}:
        raise CliFailure(
            "CLI_USAGE_ERROR", "execution-guard requires check or consume"
        )
    operation = args[0]
    options = _parse_options(
        args[1:],
        required=frozenset({"--endpoint", "--token", "--request"}),
    )
    request = _load_request(options["--request"])
    return invoke_execution_guard(
        endpoint=options["--endpoint"],
        token=options["--token"],
        operation=operation,
        payload=request,
    )


def _mutation_gateway(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    if not args or args[0] != "apply-file":
        raise CliFailure("CLI_USAGE_ERROR", "mutation-gateway requires apply-file")
    options = _parse_options(
        args[1:],
        required=frozenset({"--endpoint", "--token", "--request"}),
    )
    request = _load_request(options["--request"])
    return invoke_execution_guard(
        endpoint=options["--endpoint"],
        token=options["--token"],
        operation=args[0],
        payload=request,
    )


def _anchor_currentness(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    if args and args[0] == "evaluate":
        return _continuity_command("anchor-currentness", "evaluate", args)
    if not args or args[0] != "observe":
        raise CliFailure(
            "CLI_USAGE_ERROR", "anchor-currentness requires observe or evaluate"
        )
    options = _parse_options(
        args[1:],
        required=frozenset(
            {"--endpoint", "--token", "--session-id", "--frame-id", "--anchor-id"}
        ),
    )
    http_status, payload = call_host_adapter(
        endpoint=options["--endpoint"],
        token=options["--token"],
        method="POST",
        path="/v1/anchor-session-memory/current-input",
        payload={
            "session_id": options["--session-id"],
            "frame_id": options["--frame-id"],
            "anchor_id": options["--anchor-id"],
        },
    )
    success = http_status == 200 and payload.get("status") == "CURRENT_ANCHOR_OBSERVED"
    return (0 if success else 4), payload


def _source_review(args: Sequence[str]) -> tuple[int, Mapping[str, Any]]:
    if not args or args[0] != "check":
        raise CliFailure("CLI_USAGE_ERROR", "source-review requires check")
    options = _parse_options(
        args[1:],
        required=frozenset({"--request"}),
    )
    request = _load_request(options["--request"])
    try:
        result = evaluate_source_review(request)
    except SourceReviewError as error:
        raise CliFailure("SOURCE_REVIEW_REQUEST_INVALID", str(error)) from error
    return (0 if result["status"] == "SOURCE_REVIEW_PERMITTED" else 4), result


def _continuity_command(
    command: str, args: Sequence[str]
) -> tuple[int, Mapping[str, Any]]:
    operations = {
        "checkpoint": {"prepare", "save", "list", "load"},
        "memory-sync": {"prepare"},
        "handoff-append": {"attest"},
        "resume-save": {"prepare", "save"},
        "resume-restore": {"discover", "load"},
        "conversation-recall": {"query"},
        "anchor-currentness": {"evaluate"},
    }
    allowed = operations.get(command, set())
    if not args or args[0] not in allowed:
        raise CliFailure(
            "CLI_USAGE_ERROR",
            f"{command} requires one of: {', '.join(sorted(allowed))}",
        )
    operation = args[0]
    options = _parse_options(
        args[1:],
        required=frozenset({"--repo-root", "--request"}),
        defaults={
            "--profile": ".ai/runtime/reference_runtime/profiles/continuity-command-v1.json"
        },
    )
    repo_root = Path(options["--repo-root"]).resolve()
    profile_value = Path(options["--profile"])
    profile_path = (
        profile_value.resolve()
        if profile_value.is_absolute()
        else (repo_root / profile_value).resolve()
    )
    request = _load_request(options["--request"])
    store: ContinuityStore | None = None
    try:
        profile = load_continuity_profile(repo_root, profile_path)
        full_operation = f"{command}.{operation}"
        if full_operation in {"checkpoint.save", "resume-save.save"}:
            store = ContinuityStore.open_for_write(repo_root)
        elif full_operation in {
            "checkpoint.list",
            "checkpoint.load",
            "resume-restore.discover",
            "resume-restore.load",
        }:
            store = ContinuityStore.open_for_read(repo_root)
        result = run_continuity_command(
            profile=profile,
            operation=full_operation,
            request=request,
            store=store,
        )
    except (ContinuityCommandError, ContinuityStoreError) as error:
        raise CliFailure(error.error_code, error.detail) from error
    finally:
        if store is not None:
            store.close()
    return 0, result


def _help() -> Mapping[str, Any]:
    return {
        "schema": "ai-career.reference-runtime-cli.v1",
        "commands": [
            "capabilities [--capability <name>]",
            "capability <name>",
            "prepare-session --request <path|-> [--repo-root <path>]",
            "mode-registry list --repo-root <path>",
            "mode-registry show --repo-root <path> --mode <MODE>",
            "mode-registry plan --repo-root <path> --request <path|->",
            "task-frame propose --repo-root <path> --profile <path> --request <path|->",
            "task-frame run --repo-root <path> --profile <path> [--database <path>] --request <path|->",
            "task-frame continue --repo-root <path> --profile <path> --database <path> --request <path|->",
            "task-frame status --repo-root <path> --profile <path> --database <path>",
            "anchor-memory batch --request <path|->",
            "anchor-memory serve [--port <port>] [--token <token>]",
            (
                "session-boot serve --repo-root <path> --session-id <id> "
                "--anchor-id <id> --host-action <action> "
                "--session-location <surface> --commander-surface <surface> "
                "--execution-surface <surface> --repository-location <location> "
                "[--frame-id <id>] [--port <port>] [--token <token>]"
            ),
            (
                "runtime-status --repo-root <path> --endpoint <url> "
                "--token <token> --session-id <id>"
            ),
            "os-status source-only --request <path|->",
            (
                "execution-binding <propose|apply|begin-work> --endpoint <url> "
                "--token <token> --request <path|->"
            ),
            (
                "task-proposal <create|approve|record-result> "
                "--repo-root <path> --request <path|->"
            ),
            (
                "task-proposal status --repo-root <path> --proposal-id <id>"
            ),
            (
                "execution-guard <check|consume> --endpoint <url> "
                "--token <token> --request <path|->"
            ),
            (
                "mutation-gateway apply-file --endpoint <url> "
                "--token <token> --request <path|->"
            ),
            (
                "checkpoint <prepare|save|list|load> --repo-root <path> "
                "[--profile <path>] "
                "--request <path|->"
            ),
            (
                "memory-sync prepare --repo-root <path> [--profile <path>] "
                "--request <path|->"
            ),
            (
                "handoff-append attest --repo-root <path> [--profile <path>] "
                "--request <path|->"
            ),
            (
                "resume-save <prepare|save> --repo-root <path> "
                "[--profile <path>] --request <path|->"
            ),
            (
                "resume-restore <discover|load> --repo-root <path> "
                "[--profile <path>] "
                "--request <path|->"
            ),
            (
                "conversation-recall query --repo-root <path> [--profile <path>] "
                "--request <path|->"
            ),
            (
                "anchor-currentness observe --endpoint <url> --token <token> "
                "--session-id <id> --frame-id <id> --anchor-id <id>"
            ),
            (
                "anchor-currentness evaluate --repo-root <path> [--profile <path>] "
                "--request <path|->"
            ),
            "source-review check --request <path|->",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args or args[0] in {"help", "--help", "-h"}:
            _emit(_help())
            return 0
        command, command_args = args[0], args[1:]
        if command == "capabilities":
            code, payload = _capabilities(command_args)
        elif command == "capability":
            if len(command_args) != 1:
                raise CliFailure("CLI_USAGE_ERROR", "capability requires exactly one name")
            code, payload = _capabilities(command_args)
        elif command == "task-frame":
            code, payload = _task_frame(command_args)
        elif command == "prepare-session":
            code, payload = _prepare_session(command_args)
        elif command == "mode-registry":
            code, payload = _mode_registry(command_args)
        elif command == "anchor-memory":
            if command_args and command_args[0] == "serve":
                return _anchor_serve(command_args[1:])
            if command_args and command_args[0] == "batch":
                command_args = command_args[1:]
            code, payload = _anchor_batch(command_args)
        elif command == "session-boot":
            if not command_args or command_args[0] != "serve":
                raise CliFailure(
                    "CLI_USAGE_ERROR", "session-boot requires the serve operation"
                )
            return _session_boot_serve(command_args[1:])
        elif command == "execution-binding":
            code, payload = _execution_binding(command_args)
        elif command == "task-proposal":
            code, payload = _task_proposal(command_args)
        elif command == "runtime-status":
            code, payload = _runtime_status(command_args)
        elif command == "os-status":
            code, payload = _os_status(command_args)
        elif command == "execution-guard":
            code, payload = _execution_guard(command_args)
        elif command == "mutation-gateway":
            code, payload = _mutation_gateway(command_args)
        elif command == "checkpoint":
            code, payload = _continuity_command(command, command_args)
        elif command == "memory-sync":
            code, payload = _continuity_command(command, command_args)
        elif command == "handoff-append":
            code, payload = _continuity_command(command, command_args)
        elif command == "resume-save":
            code, payload = _continuity_command(command, command_args)
        elif command == "resume-restore":
            code, payload = _continuity_command(command, command_args)
        elif command == "conversation-recall":
            code, payload = _continuity_command(command, command_args)
        elif command == "anchor-currentness":
            code, payload = _anchor_currentness(command_args)
        elif command == "source-review":
            code, payload = _source_review(command_args)
        else:
            raise CliFailure(
                "CAPABILITY_UNSUPPORTED",
                f"unsupported command: {command}",
                context={"capability": command, "status": "UNKNOWN"},
            )
        _emit(payload)
        return code
    except CliFailure as error:
        _emit(_failure_payload(error))
        return error.exit_code
    except Exception as error:
        _emit(
            {
                "status": "UNKNOWN",
                "error_code": "REFERENCE_RUNTIME_FAILURE",
                "detail": str(error),
            }
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
