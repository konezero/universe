"""Read-only scheduled extraction frames on Host-owned execution contexts."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json
import sys
from .connection import UniverseError

PROFILE = Path(".ai/runtime/reference_runtime/profiles/task-frame-instruction-v2.json")

@contextmanager
def prepared_memory_frame(host, binding, public, config):
    if not config.get("persisted") or not config.get("enabled") or not config.get("config_id") or not config.get("revision"):
        raise UniverseError("MEMORY_BATCH_SCHEDULE_INSTRUCTION_REQUIRED", "Persisted enabled batch configuration required", 409)
    capability = host.provider_capability(config["provider"])
    if capability.get("status") != "AVAILABLE" or capability.get("model") != config["model_ref"]:
        raise UniverseError("FAST_EXTRACT_MODEL_UNAVAILABLE", "Host did not attest the configured extraction model", 409)
    frame_id = "memory-batch-" + uuid4().hex
    turn_id = frame_id + "-extract"
    source_ref = "universe://projects/{}/memory-batch-config/{}?revision={}".format(config["project_id"], config["config_id"], config["revision"])
    actor = binding["parent_actor_ref"]
    instruction_id = frame_id + "-instruction"
    plan = {
        "profile_id": "task-frame-instruction-v2", "requested_shape": "DEBATE", "resolved_shape": "DEBATE",
        "model_mode": "EXPLICIT", "frame_id": frame_id, "origin_anchor_ref": binding["origin_anchor_ref"],
        "origin_session_id": binding["session_id"], "origin_frame_id": binding["origin_frame_id"],
        "task_summary_ref": source_ref, "source_ref": source_ref, "candidate_source_ref": "NONE",
        "source_review_result": None, "parent_actor_ref": actor, "commander_surface": "UNIVERSE_SCHEDULER",
        "execution_assignment_ref": "instruction:" + instruction_id, "host_worker_capability": "AVAILABLE",
        "repository_write_scope": "NONE", "mutation_scope": {"operations": [], "targets": []},
        "fallback_reason": "NONE", "transcript_policy": "BOUNDED_RETURNED_MESSAGES_ONLY",
        "turns": [{"turn_id": turn_id, "role": "BOSS", "worker_slot_ref": "memory-extract-boss",
                   "provider": config["provider"], "model": config["model_ref"], "reasoning_effort": "max"}],
    }
    proposal = host._build_task_frame_proposal(plan, prefix="memory-batch-proposal-", profile=PROFILE)
    now = datetime.now(timezone.utc).isoformat()
    def post(path, payload):
        return host._post_runtime(binding["endpoint"], binding["token"], path, payload)
    coords = {"session_id": binding["session_id"], "frame_id": frame_id}
    created = False
    try:
        result = post("/v1/task-frame/create", {"session_id": binding["session_id"], "profile": str(PROFILE), "frame": {
            **{k: plan[k] for k in ("frame_id", "origin_anchor_ref", "origin_session_id", "origin_frame_id",
                                   "task_summary_ref", "source_ref", "execution_assignment_ref")},
            "task_frame_execution_proposal": proposal, "task_frame_execution_approval": None,
            "parent_instruction": {"instruction_id": instruction_id, "instruction_ref": source_ref,
                "user_instruction_raw": json.dumps({"operation": "MEMORY_FAST_EXTRACT", "scheduled_configuration": config}, sort_keys=True),
                "constraints": ["READ_ONLY", "NO_SOURCE_MUTATION", "NO_AUTOMATIC_ADOPTION", "NO_SUBAGENTS"],
                "expected_output": {"schema": "universe.memory-fast-extract-result.v1"},
                "repository_write_scope": "NONE", "mutation_scope": {"operations": [], "targets": []}},
            "parent_observation": {"status": "MATCHED", "evidence_ref": source_ref}, "observed_at": now}})
        if result.get("status") != "TASK_FRAME_HOST_ACTIVE":
            raise UniverseError("MEMORY_BATCH_FRAME_CREATE_FAILED", "Runtime did not activate extraction frame", 409)
        created = True
        result = post("/v1/task-frame/operation", {**coords, "operation": {
            "operation": "declare_turns", "turns": [{"turn_id": turn_id, "role": "BOSS"}], "observed_at": now}})
        if result.get("status") != "TASK_FRAME_OPERATION_APPLIED" or result.get("output", {}).get("status") != "TASK_TURNS_DECLARED":
            raise UniverseError("MEMORY_BATCH_FRAME_DECLARE_FAILED", "Runtime did not declare extraction turn", 409)
        yield {**public, "frame_id": frame_id, "task_frame_ref": frame_id, "turn_id": turn_id, "invoker_actor_ref": actor}
    finally:
        if created:
            primary = sys.exc_info()[1]
            try:
                result = post("/v1/task-frame/close", coords)
                if result.get("status") != "TASK_FRAME_HOST_CLOSED":
                    raise UniverseError("MEMORY_BATCH_FRAME_CLOSE_FAILED", "Runtime did not close extraction frame", 409)
            except Exception as cleanup:
                if primary is None:
                    raise
                primary.add_note("Extraction frame cleanup also failed: " + str(getattr(cleanup, "code", type(cleanup).__name__)))
