# Task Worker Host Contract

Status: distributable Reference Runtime contract
Governing Core: `.ai/core/TASK_FRAME_ORCHESTRATION.md`
Runtime capability: `worker_invocation = HOST_DEPENDENT`

## Purpose

This contract defines how a Host may connect one declared Task Frame turn to a
Host-provided Worker without allowing the Host, Skill, Reference Runtime, or
Worker to create authority.

```text
Core Task Frame contract
  -> Reference Runtime declaration and coordinate validation
  -> Host capability evidence
  -> Parent invokes one Boss
  -> Boss invokes bounded Sub Workers through Host transport
  -> Boss reviews Sub results and returns one final result
  -> Reference Runtime validation and Result Packet
  -> current Parent review and adoption gate
```

The Reference Runtime is deterministic coordination machinery. It does not
select a provider or model, spawn a vendor Worker, restore an Anchor, mutate a
Parent queue, adopt a result, or grant execution permission.

## Mandatory Load Order

Before a Skill or Host plans or invokes a Task Worker, it must read and follow:

1. `.ai/core/TASK_FRAME_ORCHESTRATION.md`
2. `.ai/runtime/reference_runtime/TASK_WORKER_HOST_CONTRACT.md`
3. `.ai/runtime/reference_runtime/capabilities.json`
4. the caller-selected immutable-commit-bound Task Frame profile
5. the current Parent, Anchor, and Execution Assignment evidence supplied to
   the Task Frame request

Missing or unreadable required input stops Worker invocation:

```yaml
task_worker_contract: UNKNOWN
worker_invocation: UNKNOWN
fabricated_worker_result: FORBIDDEN
```

Project proof files may be cited as evidence, but they do not replace this
installed contract.

## Invocation Boundary

```text
DECLARED != INVOKED
WORKER_INVOCATION_READY != Worker completion
Worker completion != Parent adoption
Parent ACCEPT != execution permission
```

Any agent or model invoked subordinate to the active Parent is a Task Frame
Worker for routing purposes. This includes platform-native sub-agents,
provider CLIs, model APIs, MCP-backed agents, and local agent processes. The
transport does not create an exception.

The Host must not invoke such an agent before a Task Frame has accepted the
declared turn and returned `WORKER_INVOCATION_READY`. A raw collaboration
spawn, direct provider CLI call, or equivalent model invocation is not a
bounded Worker capability and must not be used as fallback when Task Frame
setup or Host capability is unavailable.

Before invoking a Worker, the Host must obtain a Reference Runtime
`WORKER_INVOCATION_READY` result for the declared turn and then claim that turn
for one concrete `worker_id` using the same capability evidence and a unique
Host `worker_run_ref`.

For an approval-gated profile, the Host must first generate and display one
exact Task Frame execution proposal. The proposal binds the Parent actor,
Execution Assignment reference, turn order, Worker slots, provider-local model
selection, reasoning effort, Host capability status, and exact Frame/Anchor/
Session/task/source coordinates. It also binds `repository_write_scope` and
the exact `mutation_scope`. The user may revise the plan before approval. Any
revision creates a new digest and invalidates the old approval.

The required preconditions are:

```text
the Parent observation is MATCHED;
the Task Frame is ACTIVE;
the turn is READY;
the turn input bundle is complete;
the Execution Assignment reference is present as declared input;
the Host capability status is AVAILABLE;
the exact current execution proposal has user approval;
the claiming actor is not the Parent actor;
the Host supplies a concrete, unique `worker_run_ref` for that Worker;
the turn is successfully claimed by the selected Worker.
```

These checks validate coordinates only. They do not create currentness,
authority, Write Scope, assignment, or permission.

## Host Capability Evidence

The Host must check the concrete Worker invocation capability before it asks
the Reference Runtime for a plan.

```yaml
host_capability_status: AVAILABLE | UNAVAILABLE | UNKNOWN
capability_evidence_ref: <host tool, API, or receipt evidence when AVAILABLE>
```

Generic filesystem, shell, network, or model access is not proof of a bounded
Worker invocation and result-transport surface. When capability is unavailable
or unverified, submit `UNAVAILABLE` or `UNKNOWN` to `worker_invocation_plan`,
report `worker_invocation: UNKNOWN`, and do not fabricate a Worker response.

The capability evidence reference is opaque Host evidence. `worker_run_ref` is
an opaque correlation key that binds plan, claim, and terminal result for one
Worker run. It is not authority, evidence, or a durable receipt. The Reference
Runtime does not cryptographically prove a vendor's internal execution, and
the Host must not fabricate these values.

```yaml
parent_actor_ref: <current Parent identity>
invoker_actor_ref: <Parent for /root/boss; Boss for /root/boss/subN>
worker_actor_ref: <distinct Host Worker identity>
worker_path: </root/boss or /root/boss/subN>
capability_evidence_ref: <bounded Worker capability evidence>
worker_run_ref: <unique Host run correlation key>
```

Using the Parent actor under a Worker-like label is
`PARENT_SELF_SUBSTITUTION_BLOCKED`.

## Host Procedure

1. Record the user's raw instruction, constraints, expected output,
   `repository_write_scope`, exact `mutation_scope`, and current Parent
   Session/Frame/Anchor coordinates in the Task Frame instruction ledger.
2. Resolve the exact provider-local models, reasoning efforts, Worker slots,
   Parent actor, capability status, and turn route.
3. Generate and display the source-bound execution proposal.
4. Apply user edits by generating a new proposal; obtain exact approval for the
   final proposal.
5. Create one Task Frame with that proposal, approval, source-backed Parent
   observation, and the recorded Parent instruction. The instruction
   repository boundary must exactly match the approved proposal.
6. Declare exactly the approved turns through the Reference Runtime.
7. Request the root Boss invocation plan as the Parent. Its input bundle must contain the
   unchanged Parent instruction ledger and Parent coordinates.
8. Invoke exactly one Boss at `/root/boss` with that bundle unchanged. The Boss
   stays active until every declared Sub result has been reviewed and the final
   synthesis has been returned.
   The Boss must not re-enter repository startup, read `AGENTS.md`, execute
   BOOT, or reinterpret Mode and governance policy. Exact source references
   needed for the turn must already be present in the bounded input bundle.
9. The active Boss acknowledges every current instruction digest and calls
   `submit_boss_allocations` with one allocation and one `/root/boss/subN` path
   for every declared Sub turn. The Boss is not completed at this point.
10. The active Boss requests `worker_invocation_plan` for one READY Sub turn.
    The input bundle must contain its Runtime-validated Boss allocation.
11. Stop unless the raw Runtime result is `WORKER_INVOCATION_READY` and its
    `invoker_actor_ref` is the claimed Boss actor.
12. The Boss invokes the Sub through the Host's bounded nested-Worker transport.
    The Host supplies physical process or model transport only; the Parent and
    Host must not become the logical Sub caller.
    The Sub receives only its Runtime-validated allocation and bounded context;
    it must not discover repository control-plane instructions independently.
13. The Boss captures the concrete Sub actor ID and unique Host run reference,
    then claims the turn with its own actor reference as `invoker_actor_ref`.
14. Require one JSON-compatible bounded Sub result and source evidence references.
15. The Host transport submits the unchanged envelope through
    `/v1/task-frame/worker-result` with the same Worker actor, `worker_run_ref`,
    and exactly one terminal `result_receipt_ref`. The Parent must not call
    `complete_turn` or reconstruct this envelope.
16. The Runtime computes and stores the canonical envelope digest before it
    completes the turn, without adding authority,
    currentness, permission, adoption, or an undeclared next task.
17. The Boss continues through its allocations, then calls `boss_result_bundle`
    to read all recorded Sub envelopes and review them. It may return a Sub for
    rework only through a declared route; the Parent does not issue that retry.
18. The same active Boss submits one final bounded envelope and completes.
19. Build the Result Packet only through the Reference Runtime.
20. Return the Boss final envelope and Result Packet to the current Parent for
    separate review. The Parent relays it without adding debate content.

The Parent may return to the Commander conversation after Frame creation when
the Host has concrete nonblocking Worker capability. It may append later user
constraints to the instruction ledger, observe status, or cancel the Frame,
but it must not join the Worker content path or author Sub allocations.
Later Parent instructions may add constraints, but they may not change the
Frame's repository boundary. A changed boundary requires a new proposal,
approval, and Task Frame.

The previous direct procedure is invalid:

```text
Parent receives whole objective
  -> Parent creates file-specific Sub prompts
  -> Host invokes Workers

result: PARENT_ALLOCATION_LEAK
```

The required procedure is:

```text
Parent records whole objective
  -> Parent invokes /root/boss
  -> Boss reads ledger and records allocations
  -> Boss invokes /root/boss/sub1 and /root/boss/sub2 through Host transport
  -> Boss reads recorded Sub results and synthesizes
  -> Parent receives one candidate Result Packet
```

The Parent observation remains source-backed and must still be supplied when
the Frame is created. It is not replaced by the instruction ledger.

The Host may add transport metadata outside the Worker result. It must not
rewrite the Worker result to make it appear more complete or more authoritative.

## Parent Transcript Visibility

The approved execution proposal binds:

```text
transcript_policy: BOUNDED_RETURNED_MESSAGES_ONLY
```

The current Parent may observe only bounded Worker messages, status events,
evidence references, and returned result fields submitted through the Host
envelope or Task Frame journal. Hidden model reasoning, internal chain of
thought, and unreturned intermediate context are never exposed or inferred by
the Reference Runtime. Observation is read-only and does not grant authority
or adoption.

## Required Worker Result

The Host invocation must yield exactly one JSON-compatible result envelope:

```yaml
turn_id: <declared turn>
worker_id: <claiming Host Worker>
worker_run_ref: <same run correlation key used to claim the turn>
result_receipt_ref: <one terminal Host result receipt>
status: COMPLETED | FAILED | UNKNOWN
evidence_refs:
  - <source-backed evidence reference>
result: <bounded JSON-compatible task result>
review_decision: <ACCEPT | RETURN | BLOCKED | UNKNOWN | empty when no route>
```

While the root Boss turn remains `CLAIMED`, the Boss submits these fields
through `submit_boss_allocations`:

```yaml
instruction_digests:
  - <every current Parent instruction digest>
worker_allocations:
  - turn_id: <declared SUB_REVIEWER turn>
    worker_slot_ref: <approved Worker slot>
    worker_path: </root/boss/subN>
    task: <Boss-authored bounded task>
    expected_output: <JSON-compatible output contract>
    mutation_scope:
      operations: [<CREATE | MODIFY | DELETE>]
      targets: [<exact absolute target>]
```

Only the claimed root Boss actor may populate the allocation ledger. Allocation
does not complete the Boss. There is no Parent allocation operation and no
Parent direct Sub invocation operation.

`mutation_scope` is optional for read-only work and normalizes to an empty
scope. A Sub mutation is ineligible unless its Boss allocation declares the
exact operation and target. The allocation is only a narrowing record; it does
not create Authority, Write Scope, approval, assignment, or permission.

The Parent instruction uses the same shape:

```yaml
repository_write_scope: NONE | BOUNDED
mutation_scope:
  operations: [<CREATE | MODIFY | DELETE>]
  targets: [<exact absolute target>]
```

`NONE` requires both arrays to be empty. `BOUNDED` requires both arrays to be
non-empty. The Boss must acknowledge the digest containing this boundary.
Every mutating Sub allocation must be a subset of it. The Runtime blocks a
mutating allocation under `NONE` and any allocation that expands a `BOUNDED`
scope.

The Host binds this envelope to the claimed turn when it constructs the
dedicated Worker-result request. The Runtime computes `worker_result_digest`
from that exact envelope and preserves the envelope, including its one terminal
`result_receipt_ref`, in execution evidence and the Result Packet. Missing
fields remain missing or `UNKNOWN`; the Host and Parent must not infer them
from conversation history.

```text
Parent direct complete_turn
  -> WORKER_RESULT_ENVELOPE_REQUIRED

rewritten envelope, missing worker_run_ref, or missing result_receipt_ref
  -> blocked before turn completion
```

The Worker result must not create or claim:

```text
authority
currentness
execution_assignment
execution_permission
repository_write_scope
parent_adoption
parent_decision
next_task_id
```

The Reference Runtime rejects these claims as
`PROHIBITED_TURN_RESULT_CLAIM`.

## Project-Owned Skill Observation

ai-career does not own a project implementation Skill catalog. The active Boss
may attach optional project-owned `skill_bindings` to a Sub allocation:

```yaml
skill_id: <project-defined identifier>
skill_version: <project-defined version>
skill_ref: <project-local reference>
context_pack_digest: <sha256>
operation_class: READ | PROPOSE | EXECUTE
```

The Runtime computes and persists each binding digest as part of the allocation
digest. A Worker may use only those declared bindings. When a completed Worker
uses a binding, its unchanged envelope must include one bounded observation:

```yaml
skill_binding_digest: <Runtime-computed binding digest>
model_ref: <Host-observed reference or UNKNOWN>
outcome: SUCCEEDED | FAILED | UNKNOWN
validation_state: PASS | FAIL | NOT_RUN | UNKNOWN
evidence_refs: [<bounded evidence reference>]
metrics: {duration_ms: <non-negative number>}
```

Unbound, substituted, duplicated, incomplete, or malformed observations block
the Worker envelope before turn completion. The Task Frame SQLite journal stores
validated observations with the envelope and exposes them only as Result Packet
and execution evidence. It does not publish to Universe, select a model, score
a Skill, or create execution authority.

## Result Rejoin

```text
current Parent evidence matches
  -> attachment_state may be ATTACHED
  -> adoption_state remains CANDIDATE

current Parent evidence is missing, mismatched, stale, or UNKNOWN
  -> attachment_state is UNATTACHED or UNKNOWN
  -> adoption_state remains CANDIDATE
```

A valid Result Packet is recoverable evidence. It does not restore the origin
Anchor, mutate the Current Anchor, mutate a Parent queue, or select a next task.
Only the current Parent may decide `ACCEPT`, `RETURN`, `DEFER`, `REJECT`, or
`UNKNOWN` after rechecking current evidence.

## External Mutation Boundary

If an adopted result would change a repository, file, database, API, Git
remote, or another external system, the concrete mutation must pass the normal
Execution Binding and `.ai/skills/common/execution-guard/SKILL.md` path.

For a pre-authorized write-capable Frame, the Parent binds one Assignment and
Write Scope before Boss invocation. Each claimed Sub then submits this
candidate lineage with its concrete Guard request:

```yaml
task_frame_id: <active Task Frame>
parent_assignment_id: <bound Parent assignment>
boss_allocation_id: <Runtime-recorded allocation>
sub_turn_id: <claimed Sub turn>
sub_worker_id: <claiming Worker actor>
worker_path: /root/boss/subN
```

The Session Host adapter, not the Parent or Worker, verifies that candidate
against the live Task Frame ledger. The Guard receives the Host-verified
lineage separately, checks that the concrete operation and target are within
both the Boss allocation and Parent Write Scope, and seals the lineage into the
one-time receipt. It repeats verification at receipt consumption.

That one-time pre-write receipt is internal to the mutation gateway. It is not
a Task Frame Worker Result Receipt and must not be surfaced as an additional
Worker receipt.

```text
unverified or forged lineage -> BLOCK
allocation outside Parent Write Scope -> BLOCK
Sub no longer CLAIMED -> BLOCK
exact allocated mutation -> normal Guard intersection
```

```text
Worker completion
  != repository write permission
  != external execution permission
```

## Non-Goals

- This contract does not install a platform Worker API.
- This contract does not make every Host capable of background execution.
- This contract does not expose hidden model reasoning.
- This contract does not select a provider or model.
- This contract does not allow the Parent to participate in Worker turns.
- This contract does not auto-retry, auto-adopt, or auto-restore.
- This contract does not create durable Task Frame state; the bundled Runtime
  remains process-local SQLite `:memory:`.
