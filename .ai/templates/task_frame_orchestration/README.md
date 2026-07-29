# Task Frame Orchestration Template

Status: Optional project assembly template
Scope: attached project runtime assembly
Governing Core: `.ai/core/TASK_FRAME_ORCHESTRATION.md`
Proof lineage: `docs/TASK_FRAME_RUNTIME_CONTRACT_CANDIDATE.md`

## Purpose

Use this template when a project wants to keep the Parent conversation active
while one or more bounded Task Frames perform long-running, parallel, review,
or debate work.

This template is optional. It does not make every task multi-worker and it does
not grant Worker invocation capability.

## Expected Operational Effect

```text
Keep detailed work context inside bounded Task Frames.
Keep only task coordinates, status, and adopted Result Packets in the Parent.
Allow the Parent conversation to continue while Host-supported work runs.
Reduce Parent-context growth and potentially delay context compaction.
```

These are expected operational effects, not a claim that total system tokens,
latency, cross-host behavior, or cross-model behavior are already proven.

## Optional Activation

Consider this template when at least one condition applies:

```text
the work is long-running;
the work can be split into independent bounded scopes;
implementation and verification should be isolated;
multiple reviewers or a turn-based debate are useful;
the Parent should remain available for the Commander conversation.
```

Do not activate it merely because multiple Workers are available.

If the Host cannot provide bounded Worker invocation or result transport,
report the capability as `UNKNOWN` and keep the task in the Parent or use a
single local execution path. Do not fabricate background work.

## Assembly Model

```text
Current Anchor
  -> origin Anchor Snapshot per Task Frame
  -> Parent creates one or more bounded Task Frames
       -> optional Boss / decision_role
       -> scoped Worker Turns
       -> append-only evidence and Result Packet
  -> Parent receives completion notification
  -> origin/current Parent evidence is rechecked
  -> Parent Adoption Gate
       -> ACCEPT: selected result may update the Current Anchor
       -> RETURN: declared follow-up route only
       -> DEFER / REJECT / UNKNOWN: preserve as candidate evidence
       -> stale or mismatched origin: UNATTACHED
```

Task completion is not Parent adoption.

## Required Coordinates

Every locally assembled Task Frame should preserve at least:

```yaml
task_frame:
  frame_id: <stable-frame-id>
  origin_anchor_ref: <source-backed-anchor-reference>
  origin_session_id: <evidence-coordinate-or-UNKNOWN>
  origin_frame_id: <evidence-coordinate-or-UNKNOWN>
  task_summary_ref: <bounded-parent-task-reference>
  source_ref: <contract-or-manifest-reference>
  execution_assignment_ref: <reference-or-UNASSIGNED>
  repository_write_scope: NONE | BOUNDED
  mutation_scope:
    operations: []
    targets: []
  status: DECLARED | ING | COMPLETED | FAILED | UNKNOWN
  boss_ref: <decision-role-reference-or-null>
  worker_refs: []
  dispatch_topology:
    interaction_carrier: <mobile-web-desktop-or-UNKNOWN>
    execution_host: <bound-host-or-UNKNOWN>
    write_target: <bound-target-or-UNKNOWN>
```

`session_id`, `frame_id`, Anchor Snapshot, Mode, Role, and Task Frame status are
dispatch-time coordinates. They are not authority and do not update the live
Mode Current Anchor after the Task Frame is created.

## Parent / Boss / Worker Split

### Parent

```text
owns the Commander conversation and Current Anchor;
creates or approves bounded task scope;
receives completion notifications;
rechecks currentness;
adopts, returns, defers, rejects, or leaves results UNKNOWN.
```

Before invoking a Boss, the Parent records the unchanged instruction plus an
explicit repository boundary. `NONE` requires empty `mutation_scope` arrays.
`BOUNDED` requires exact operations and absolute targets. This boundary must
match the approved execution proposal and cannot change inside the Frame.

### Boss / decision_role

```text
decomposes the bounded task;
assigns declared Worker Turns;
reviews evidence and declared routes;
returns one Result Packet to the Parent;
has no independent authority.
```

The Boss acknowledges every current Parent instruction digest. It may omit
mutation scope for read-only Sub work or narrow a `BOUNDED` scope. It may not
allocate mutation under `NONE` or add an operation or target that the Parent
did not declare.

A simple one-Worker Task Frame may omit the Boss.

### Worker

```text
executes only its declared Turn;
returns bounded evidence and result fields;
does not adopt results;
does not create authority, currentness, write scope, or a next task.
```

When a project binds a project-owned Skill to a Worker allocation, the Worker
also returns one bounded Skill-run observation per declared binding. The Task
Frame journal preserves those observations with the Result Packet. A project
may later prepare a redacted export candidate through
`.ai/skills/common/skill-observation/SKILL.md`; that preparation and any
provider handoff are separate from Task Frame completion and Parent adoption.

## Result Packet

```yaml
result_packet:
  frame_id: <frame-id>
  origin_anchor_ref: <origin-anchor-reference>
  turn_refs: []
  evidence_refs: []
  attachment_state: ATTACHED | UNATTACHED | UNKNOWN
  adoption_state: CANDIDATE
  status: COMPLETED | FAILED | UNKNOWN
  result: <bounded-result-or-null>
```

Successful Worker completion must not change `adoption_state` from
`CANDIDATE`. Only the Parent Adoption Gate may adopt a result.

## Concurrency Boundary

Projects may assemble more than one Task Frame under the same Current Anchor
when their task scopes and write targets do not conflict.

```text
independent read/review scopes
  -> concurrent frames may be allowed

overlapping write targets or dependent transitions
  -> serialize, declare ordering, or block one frame
```

Concurrency is a project/Host capability decision. This template does not
create a scheduler or guarantee parallel execution.

## Implementation Freedom

The attached project owns its local implementation. Compatible choices may
include:

```text
runtime memory
SQLite :memory:
file-backed SQLite journal
temporary files
Host task APIs
project-local Python Reference Runtime
another disposable Task Frame store
```

The implementation must remain disposable and subordinate to Git-backed Core
and the active Runtime Anchor Frame. A Task Frame store is not canonical
governance state.

## External Mutation Boundary

```text
Adopted Task Frame result
  -> existing Execution Binding
  -> existing Pre-Execution Verification
  -> Host Capability + Write Scope + current Execution Assignment
  -> external mutation only when separately authorized
```

Neither a Boss decision nor Worker completion may bypass this route.
The instruction boundary is enforced by the Task Frame ledger. It does not
claim that the Runtime can revoke unrelated Host tools.

## Validation Checklist

An attached project should be able to report:

```text
Current Anchor and origin snapshot remain distinct.
Parent conversation remains the adoption surface.
Detailed Worker context does not have to enter the Parent context.
Every Worker has a bounded task and result envelope.
Parent instruction and approved proposal repository boundaries match.
Boss allocations never expand the Parent mutation scope.
Missing Host capability remains UNKNOWN.
Completed results remain CANDIDATE until Parent adoption.
Stale or mismatched results remain UNATTACHED.
Conflicting write scopes are serialized or blocked.
External mutation still passes existing execution gates.
Cross-host and cross-model behavior remain UNKNOWN until separately tested.
```

## Non-Goals

This template does not:

- promote the Task Frame candidate contract to Core;
- require a Boss for every task;
- require SQLite or a durable database;
- select a model or provider;
- invoke a vendor Worker by itself;
- expose hidden model reasoning;
- guarantee lower total token use;
- restore an origin Anchor automatically;
- mutate a Parent Queue automatically;
- grant authority, write scope, execution assignment, or execution permission.
