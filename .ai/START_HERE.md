# universe Runtime Start Here

<!-- ai-career-project-runtime-overlay:start -->
## Managed ai-career Runtime Binding

This source-managed block augments the project-owned policy outside the
block. The project may keep richer local routing, but shared Runtime
package entry, capability, and execution-gate references in this block
remain source-bound. Edit project policy outside this block.

Schema: ai-career.project-runtime-entry.v1
Project: universe

## Boot Order

1. `REPOSITORY_MANIFEST.md`
2. `AGENTS.md`
3. `.ai/core/README.md`
4. `.ai/runtime/project_instance/boot_command_entry.md`
5. `.ai/runtime/project_instance/project_anchor.md`
6. `.ai/runtime/project_instance/role_selection_gate.md`
7. `.ai/runtime/project_instance/mode_registry.json`
8. `.ai/runtime/state/session.md`
9. `.ai/runtime/state/current_anchor_frame.md`
10. `.ai/runtime/project_instance/validation/latest.md`

Report only source-backed fields. Unknown values remain UNKNOWN.

On a Windows Host, follow
`.ai/skills/common/windows-shell-guard/SKILL.md` before constructing
commands and `.ai/skills/common/windows-native-cli/SKILL.md` before
invoking an external executable. Host command routing does not
replace Assignment, approval, Execution Guard, or source-review
isolation.

For source-only `OS_STATUS`, repository checkpoint, Resume Archive,
validation, Runtime Image, and state documents are
`OBSERVED_REFERENCE` only. Follow
`.ai/skills/common/runtime-status/SKILL.md`; do not promote their
historical labels into current Runtime, Anchor, restore, validation,
gate, authority, or assignment state.

## Mutation Entry

Before every **project-owned** durable mutation, execute
`.ai/skills/common/execution-guard/SKILL.md`. BOOT readiness and a
Current Anchor do not replace the required Guard result and receipt.
Runtime-owned state, `HOST_STATE_PROJECTION`, handoff evidence, and
automatic continuity do **not** use Guard; see
`.ai/skills/common/host-state-projection/SKILL.md` and the
Runtime-Owned State Exception in execution-guard.
For one exact, single-occurrence repository text replacement on an
existing file, prefer
`.ai/skills/common/receipt-aware-text-edit/SKILL.md`; that Skill
calculates preimage and payload hashes, checks once, and immediately
consumes the one-time receipt through mutation-gateway apply-file.
Ordinary local Git staging, commit, and push after completed,
validated work remain outside the Runtime. Their immutable commit SHA
may be appended to the approved Task Proposal's Result Receipt and
never creates Runtime authority, Binding, or an execution receipt.

For a new **project-owned** mutation request, first follow
`.ai/skills/common/task-assignment/SKILL.md`, then bind exact approval
through `.ai/skills/common/execution-binding/SKILL.md`. Neither step
replaces the final Guard for that class of work.

## Task Worker Entry

Before any Host Worker invocation, follow
`.ai/skills/common/task-frame/SKILL.md`. That Skill must load
`.ai/runtime/reference_runtime/TASK_WORKER_HOST_CONTRACT.md` and
preserve unverified Host capability as `UNKNOWN`.

Every subordinate agent invocation uses this route. A raw platform
sub-agent, provider CLI, model API, MCP-backed agent, or local agent
process must not substitute for an accepted Task Frame Worker plan.

The default bounded discussion route is
`.ai/skills/common/task-frame-debate/SKILL.md`. Combined repository and
process-local status uses `.ai/skills/common/runtime-status/SKILL.md`;
durable conversation handoff uses `.ai/skills/common/resume-save/SKILL.md`.

Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
<!-- ai-career-project-runtime-overlay:end -->
