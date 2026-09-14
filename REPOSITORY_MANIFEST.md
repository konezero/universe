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

Follow `AGENTS.md` for reserved commands and Mode intent before normal entry.
At first ordinary entry read this manifest and `.ai/START_HERE.md` once.
Reuse unchanged context; read the Boot command entry for Runtime operations
and `.ai/core/README.md` only to locate a relevant contract.
Runtime operations use the Registry snapshot and Mode Current Anchor in
`.ai/runtime/state/project_runtime.sqlite3`, then the bound session SQL.
Create that SQL only if absent. Read-only source work need not prepare a session.

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
Requested Debate / Worker Review: `.ai/skills/common/task-frame-debate/SKILL.md`
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
