# universe Boot Command Entry

## Project-Owned Mode Entry

`유니버스모드` / `유니버스` / `컨덕터모드` / `컨덕터` /
`UNIVERSE` / `UNIVERSE mode` / `CONDUCTOR` / `CONDUCTOR mode`
  -> internal `MODE_CHANGE`
  -> resolve `UNIVERSE` from `.ai/runtime/project_instance/mode_registry.json`
  -> Role `CONDUCTOR`
  -> Scope `project-network/navigation/distribution`

`Universe Mode` and `Conductor Mode` are aliases for the same registered
`UNIVERSE` coordinate. `MASTER` remains the immutable root and maintenance
Mode for Mode Registry, policy, schema, release, and Runtime lifecycle
changes. Generic `BOOT` proposes registered Mode selection; it does not
silently replace `MASTER` as the Registry root.

<!-- ai-career-project-runtime-overlay:start -->
## Managed ai-career Runtime Binding

This source-managed block augments the project-owned policy outside the
block. The project may keep richer local routing, but shared Runtime
package entry, capability, and execution-gate references in this block
remain source-bound. Edit project policy outside this block.

Schema: ai-career.project-runtime-command-entry.v1
Node: universe

## Primary Mode

`universe MASTER` / `MASTER` / `MASTER mode`
  -> read `.ai/runtime/project_instance/mode_registry.json`
  -> require the requested Mode to be registered
  -> read `.ai/runtime/state/session.md`
  -> read `.ai/runtime/state/current_anchor_frame.md`
  -> resolve Role and Scope from the registered Mode
  -> keep authority and execution assignment separate

## Commands

`BOOT` reads `.ai/START_HERE.md`, follows
`.ai/skills/common/boot/SKILL.md`, and executes the installed
Session Boot Executor when Host capability is available.
`TASK FRAME` and Task Worker requests follow
`.ai/skills/common/task-frame/SKILL.md`, including the mandatory
`.ai/runtime/reference_runtime/TASK_WORKER_HOST_CONTRACT.md` load.
`SOURCE REVIEW` and pull request review follow
`.ai/skills/common/source-review/SKILL.md` before Candidate policy
is consumed or Candidate code is executed.
`TASK ASSIGN` follows `.ai/skills/common/task-assignment/SKILL.md`.
`EXECUTION BIND` follows `.ai/skills/common/execution-binding/SKILL.md`.
`DEBATE` follows `.ai/skills/common/task-frame-debate/SKILL.md`.
`OS_STATUS` follows `.ai/skills/common/os-management/SKILL.md` and
`.ai/skills/common/runtime-status/SKILL.md`. Source-only status must
stop at `SOURCE_READY`; checkpoint, Resume Archive, validation, and
Runtime Image documents remain observed references.
`RUNTIME STATUS` follows `.ai/skills/common/runtime-status/SKILL.md`.
`CHECKPOINT` follows `.ai/skills/common/checkpoint/SKILL.md`.
`MEMORY SYNC` follows `.ai/skills/common/memory-sync/SKILL.md`.
`SKILL OBSERVATION` follows
`.ai/skills/common/skill-observation/SKILL.md`.
`UNIVERSE SEED` follows
`.ai/skills/common/universe-project-seed/SKILL.md`.
`RESUME` follows `.ai/skills/common/resume-restore/SKILL.md`.
`RESUME SAVE` follows `.ai/skills/common/resume-save/SKILL.md`.
`CONVERSATION RECALL` follows
`.ai/skills/common/conversation-recall/SKILL.md`.
`ANCHOR CURRENTNESS` follows
`.ai/skills/common/anchor-currentness/SKILL.md`.
`MODE LIST`, `MODE SHOW`, `MODE ADD`, `MODE MODIFY`, and `MODE DELETE`
follow `.ai/skills/common/master-mode-registry/SKILL.md`.
Any mutation follows `.ai/skills/common/execution-guard/SKILL.md`
before a file, shell, API, database, Git, or external write tool runs.
`STATUS` reads `.ai/runtime/project_instance/status.md`.
`OS_VALIDATE` runs `.ai/runtime/tools/project_runtime_installer.py validate`.

Node: universe
Mode: MASTER
Role: MASTER
Mode Scope: architecture/governance
Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
State: READY
<!-- ai-career-project-runtime-overlay:end -->
