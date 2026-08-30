from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / ".ai" / "runtime"
if not (RUNTIME_ROOT / "reference_runtime").is_dir():
    raise unittest.SkipTest(
        "installed Universe AI Workspace is required for Runtime integration tests"
    )
sys.path.insert(0, str(RUNTIME_ROOT))

from reference_runtime.anchor_session_memory_adapter import (  # noqa: E402
    AnchorSessionMemoryHostAdapter,
)
from reference_runtime.task_frame_runtime import (  # noqa: E402
    build_task_frame_execution_proposal,
)


CONTRACT_PATHS = (
    ".ai/core/TASK_FRAME_ORCHESTRATION.md",
    ".ai/runtime/reference_runtime/TASK_WORKER_HOST_CONTRACT.md",
)
PROFILE_REF = ".ai/runtime/reference_runtime/profiles/task-frame-debate-v1.json"
AGENT_POLICY_PATH = ".ai/agents/common/worker-policy-pack.json"


class UniverseTaskFrameSkillObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name)
        for relative in (*CONTRACT_PATHS, PROFILE_REF, AGENT_POLICY_PATH):
            destination = self.target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        rows = []
        for relative in (*CONTRACT_PATHS, PROFILE_REF, AGENT_POLICY_PATH):
            rows.append(
                {
                    "class": (
                        "agent"
                        if relative == AGENT_POLICY_PATH
                        else "core_runtime"
                        if relative.startswith(".ai/core/")
                        else "runtime"
                    ),
                    "target_path": relative,
                    "local_sha256": hashlib.sha256(
                        (self.target / relative).read_bytes()
                    ).hexdigest(),
                }
            )
        manifest_path = (
            self.target
            / ".ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": "ai-career.project-runtime-installation.v1",
                    "source": {
                        "repository": "konezero/ai-career",
                        "commit": "a" * 40,
                    },
                    "installation": {"project": "universe-test"},
                    "managed_paths": rows,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (manifest_path.parent / "mode_registry.json").write_text(
            json.dumps(
                {
                    "schema": "ai-career.mode-registry.v2",
                    "owner": "universe-test",
                    "repository_kind": "PROJECT",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 1,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                        }
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def test_bound_sub_skill_observation_survives_result_packet_reopen(
        self,
    ) -> None:
        session_id = "session:universe-bench"
        frame_id = "frame:universe-bench"
        anchor_id = "anchor:universe-bench"
        plan = {
            "profile_id": "task-frame-debate-v1",
            "requested_shape": "DEBATE",
            "resolved_shape": "DEBATE",
            "model_mode": "EXPLICIT",
            "frame_id": frame_id,
            "origin_anchor_ref": anchor_id,
            "origin_session_id": session_id,
            "origin_frame_id": "current",
            "task_summary_ref": "task:universe-bench",
            "source_ref": "source:universe-bench",
            "candidate_source_ref": "NONE",
            "source_review_result": None,
            "parent_actor_ref": "parent:universe-bench",
            "commander_surface": "codex-desktop",
            "execution_assignment_ref": "UNASSIGNED",
            "host_worker_capability": "AVAILABLE",
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "fallback_reason": "NONE",
            "transcript_policy": "BOUNDED_RETURNED_MESSAGES_ONLY",
            "turns": [
                {
                    "turn_id": "boss",
                    "role": "BOSS",
                    "worker_slot_ref": "boss-slot",
                    "provider": "GROK",
                    "model": "grok-build",
                    "reasoning_effort": "standard",
                },
                {
                    "turn_id": "sub",
                    "role": "SUB_REVIEWER",
                    "worker_slot_ref": "sub-slot",
                    "provider": "GROK",
                    "model": "grok-build",
                    "reasoning_effort": "standard",
                },
            ],
        }
        proposal = build_task_frame_execution_proposal(plan)
        approval = {
            "status": "APPROVED",
            "proposal_id": proposal["proposal_id"],
            "plan_digest": proposal["plan_digest"],
            "commander_surface": "codex-desktop",
            "evidence_ref": "user://approval/universe-bench",
        }
        adapter = AnchorSessionMemoryHostAdapter(repository_root=self.target)
        self.addCleanup(adapter.close)
        adapter.activate(
            {
                "session_id": session_id,
                "anchor_mode": "MASTER",
                "source_ref": "source:universe-bench",
                "snapshot": {
                    "frame_id": "current",
                    "anchor_id": anchor_id,
                    "state": "READY",
                    "observed_at": "2026-07-29T03:00:00Z",
                },
            }
        )
        frame_payload = {
            "session_id": session_id,
            "profile": PROFILE_REF,
            "frame": {
                "frame_id": frame_id,
                "origin_anchor_ref": anchor_id,
                "origin_session_id": session_id,
                "origin_frame_id": "current",
                "task_summary_ref": "task:universe-bench",
                "source_ref": "source:universe-bench",
                "execution_assignment_ref": "UNASSIGNED",
                "task_frame_execution_proposal": proposal,
                "task_frame_execution_approval": approval,
                "parent_instruction": {
                    "instruction_id": "instruction:universe-bench",
                    "user_instruction_raw": "Review the bounded context.",
                    "constraints": ["READ_ONLY"],
                    "expected_output": {"type": "review"},
                    "repository_write_scope": "NONE",
                    "mutation_scope": {"operations": [], "targets": []},
                },
                "parent_observation": {
                    "status": "MATCHED",
                    "evidence_ref": "parent://universe-bench",
                },
                "observed_at": "2026-07-29T03:00:01Z",
            },
        }
        created = adapter.create_task_frame(frame_payload)
        self.assertEqual("TASK_FRAME_HOST_ACTIVE", created["status"])

        def operation(value: dict[str, object]) -> dict[str, object]:
            return adapter.apply_task_frame_operation(
                {
                    "session_id": session_id,
                    "frame_id": frame_id,
                    "operation": value,
                }
            )["output"]

        operation(
            {
                "operation": "declare_turns",
                "turns": [
                    {"turn_id": "boss", "role": "BOSS"},
                    {
                        "turn_id": "sub",
                        "role": "SUB_REVIEWER",
                        "input_turn_ids": ["boss"],
                    },
                ],
                "observed_at": "2026-07-29T03:00:02Z",
            }
        )
        boss_plan = operation(
            {
                "operation": "worker_invocation_plan",
                "turn_id": "boss",
                "host_capability_status": "AVAILABLE",
                "capability_evidence_ref": "host://grok/capability",
                "invoker_actor_ref": "parent:universe-bench",
                "observed_at": "2026-07-29T03:00:03Z",
            }
        )
        operation(
            {
                "operation": "claim_turn",
                "turn_id": "boss",
                "worker_id": "worker:boss",
                "worker_run_ref": "host://grok/run/boss",
                "capability_evidence_ref": "host://grok/capability",
                "invoker_actor_ref": "parent:universe-bench",
                "observed_at": "2026-07-29T03:00:04Z",
            }
        )
        operation(
            {
                "operation": "submit_boss_allocations",
                "boss_turn_id": "boss",
                "boss_worker_id": "worker:boss",
                "worker_run_ref": "host://grok/run/boss",
                "instruction_digests": boss_plan["worker_invocation"][
                    "input_bundle"
                ]["parent_instruction_bundle"]["instruction_digests"],
                "worker_allocations": [
                    {
                        "turn_id": "sub",
                        "worker_slot_ref": "sub-slot",
                        "worker_path": "/root/boss/sub1",
                        "task": "Review the bounded context with the declared Skill.",
                        "expected_output": {"type": "review"},
                        "mutation_scope": {"operations": [], "targets": []},
                        "skill_bindings": [
                            {
                                "skill_id": "project.source-review",
                                "skill_version": "1.0.0",
                                "skill_ref": ".ai/project_skills/source-review.md",
                                "context_pack_digest": "b" * 64,
                                "operation_class": "READ",
                            }
                        ],
                    }
                ],
                "observed_at": "2026-07-29T03:00:05Z",
            }
        )
        sub_plan = operation(
            {
                "operation": "worker_invocation_plan",
                "turn_id": "sub",
                "host_capability_status": "AVAILABLE",
                "capability_evidence_ref": "host://grok/capability",
                "invoker_actor_ref": "worker:boss",
                "observed_at": "2026-07-29T03:00:06Z",
            }
        )
        binding = sub_plan["worker_invocation"]["input_bundle"]["boss_allocation"][
            "skill_bindings"
        ][0]
        operation(
            {
                "operation": "claim_turn",
                "turn_id": "sub",
                "worker_id": "worker:sub",
                "worker_run_ref": "host://grok/run/sub",
                "capability_evidence_ref": "host://grok/capability",
                "invoker_actor_ref": "worker:boss",
                "observed_at": "2026-07-29T03:00:07Z",
            }
        )
        completed = adapter.accept_task_frame_worker_result(
            {
                "session_id": session_id,
                "frame_id": frame_id,
                "envelope": {
                    "turn_id": "sub",
                    "worker_id": "worker:sub",
                    "worker_run_ref": "host://grok/run/sub",
                    "result_receipt_ref": "host://grok/result/sub",
                    "status": "COMPLETED",
                    "evidence_refs": ["host://grok/result/sub"],
                    "result": {"returned_message": "review complete"},
                    "review_decision": "",
                    "skill_run_observations": [
                        {
                            "skill_binding_digest": binding[
                                "skill_binding_digest"
                            ],
                            "model_ref": "provider://GROK/model/grok-build",
                            "outcome": "SUCCEEDED",
                            "validation_state": "PASS",
                            "evidence_refs": ["host://grok/result/sub"],
                            "metrics": {"duration_ms": 42},
                        }
                    ],
                },
                "observed_at": "2026-07-29T03:00:08Z",
            }
        )
        self.assertEqual("TURN_COMPLETED", completed["status"])
        packet = operation({"operation": "build_result_packet"})
        self.assertEqual("RESULT_PACKET_BUILT", packet["status"])
        observations = packet["result_packet"]["skill_run_observations"]
        self.assertEqual(1, len(observations))
        self.assertEqual("project.source-review", observations[0]["skill_binding"]["skill_id"])
        self.assertEqual(
            "provider://GROK/model/grok-build", observations[0]["model_ref"]
        )

        adapter.close()
        reopened = AnchorSessionMemoryHostAdapter(repository_root=self.target)
        self.addCleanup(reopened.close)
        reopened.activate(
            {
                "session_id": session_id,
                "anchor_mode": "MASTER",
                "source_ref": "source:universe-bench",
                "snapshot": {
                    "frame_id": "current",
                    "anchor_id": anchor_id,
                    "state": "READY",
                    "observed_at": "2026-07-29T03:00:09Z",
                },
            }
        )
        rehydrated = reopened.create_task_frame(frame_payload)
        self.assertEqual("TASK_FRAME_HOST_ACTIVE", rehydrated["status"])
        evidence = reopened.task_frame_status(
            session_id=session_id, frame_id=frame_id
        )["execution_evidence"]
        self.assertEqual(
            1,
            len(
                evidence["execution_gate"]["worker_invocations"][1][
                    "skill_run_observations"
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
