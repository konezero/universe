# universe Repository Manifest

<!-- ai-career-project-runtime-overlay:start -->
## Managed ai-career Runtime Binding

This source-managed block augments the project-owned policy outside the
block. The project may keep richer local routing, but shared Runtime
package entry, capability, and execution-gate references in this block
remain source-bound. Edit project policy outside this block.

Schema: ai-career.project-runtime-repository.v1
Project: universe
Node: universe
Runtime Workspace: `.ai/`

## Agent Entry Order

1. `REPOSITORY_MANIFEST.md`
2. `AGENTS.md`
3. `.ai/START_HERE.md`
4. `.ai/core/README.md`
5. `.ai/runtime/project_instance/boot_command_entry.md`
6. `.ai/runtime/state/project_runtime.sqlite3` Registry snapshot and Mode Current Anchor; use them
7. `.ai/runtime/session_store/` bound to that Current Anchor; create only if absent

Standalone and Universe-attached Hosts use that same store. Do not walk git
for live Mode. `session.md` and `current_anchor_frame.md` are companion refs only.

## Installation Evidence

Distribution Manifest: `.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json`
Validation: `.ai/runtime/project_instance/validation/latest.md`

Mutation Entry: `.ai/skills/common/execution-guard/SKILL.md`
Mutation Rule: Execution Guard before guarded project-owned / source / external mutation; first-class governed knowledge Actions use their validated Action Gateway; Runtime-owned state, HOST_STATE_PROJECTION, handoff, and continuity flush use their declared exception routes
Host State Projection: `.ai/skills/common/host-state-projection/SKILL.md`
Exact Text Edit Entry: `.ai/skills/common/receipt-aware-text-edit/SKILL.md`

Task Assignment Entry: `.ai/skills/common/task-assignment/SKILL.md`
Execution Binding Entry: `.ai/skills/common/execution-binding/SKILL.md`

Task Frame Entry: `.ai/skills/common/task-frame/SKILL.md`
Default Debate Entry: `.ai/skills/common/task-frame-debate/SKILL.md`
Task Worker Contract: `.ai/runtime/reference_runtime/TASK_WORKER_HOST_CONTRACT.md`
Common Agent Policy: `.ai/agents/common/README.md`
Worker Policy Pack: `.ai/agents/common/worker-policy-pack.json`
Source Review Entry: `.ai/skills/common/source-review/SKILL.md`
Windows Shell Entry: `.ai/skills/common/windows-shell-guard/SKILL.md`
Windows Native CLI Entry: `.ai/skills/common/windows-native-cli/SKILL.md`

Runtime Status Entry: `.ai/skills/common/runtime-status/SKILL.md`
Resume Save Entry: `.ai/skills/common/resume-save/SKILL.md`

Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
<!-- ai-career-project-runtime-overlay:end -->
