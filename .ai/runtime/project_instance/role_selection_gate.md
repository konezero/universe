# universe Role Selection Gate

<!-- ai-career-project-runtime-overlay:start -->
## Managed ai-career Runtime Binding

This source-managed block augments the project-owned policy outside the
block. The project may keep richer local routing, but shared Runtime
package entry, capability, and execution-gate references in this block
remain source-bound. Edit project policy outside this block.

Schema: ai-career.project-runtime-mode-gate.v1
Node: universe
Mode: MASTER
Role: MASTER
Mode Scope: architecture/governance
State: READY

Registry: `.ai/runtime/project_instance/mode_registry.json`
Registry Policy: MASTER_MANAGED
Root Mode: MASTER

Mode resolves Role and Scope only through the Registry. Mode, Role,
Scope, and READY do not grant repository mutation or external
execution authority.

Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
<!-- ai-career-project-runtime-overlay:end -->
