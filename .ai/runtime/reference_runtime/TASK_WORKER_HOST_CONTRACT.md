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

Before invoking a Worker, the Host must obtain a Reference Runtime
`WORKER_INVOCATION_READY` result for the declared turn and then claim that turn
for one concrete `worker_id` using the same capability evidence and the Host's
actual invocation receipt.

For an approval-gated profile, the Host must first generate and display one
exact Task Frame execution proposal. The proposal binds the Parent actor,
Execution Assignment reference, turn order, Worker slots, provider-local model
selection, reasoning effort, Host capability status, and exact Frame/Anchor/
Session/task/source coordinates. The user may revise the plan before approval.
Any revision creates a new digest and invalidates the old approval.

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
the Host supplies a concrete invocation receipt for that Worker;
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

The capability evidence reference and invocation receipt are opaque Host
evidence. The Reference Runtime checks their presence and continuity across
plan, claim, and completion; it does not cryptographically prove a vendor's
internal execution. The Host must not fabricate these references.

```yaml
parent_actor_ref: <current Parent identity>
invoker_actor_ref: <Parent for /root/boss; Boss for /root/boss/subN>
worker_actor_ref: <distinct Host Worker identity>
worker_path: </root/boss or /root/boss/subN>
capability_evidence_ref: <bounded Worker capability evidence>
host_invocation_receipt_ref: <actual Host invocation receipt>
```

Using the Parent actor under a Worker-like label is
`PARENT_SELF_SUBSTITUTION_BLOCKED`.

## Host Procedure

1. Record the user's raw instruction, constraints, expected output, and current
   Parent Session/Frame/Anchor coordinates in the Task Frame instruction ledger.
2. Resolve the exact provider-local models, reasoning efforts, Worker slots,
   Parent actor, capability status, and turn route.
3. Generate and display the source-bound execution proposal.
4. Apply user edits by generating a new proposal; obtain exact approval for the
   final proposal.
5. Create one Task Frame with that proposal, approval, source-backed Parent
   observation, and the recorded Parent instruction.
6. Declare exactly the approved turns through the Reference Runtime.
7. Request the root Boss invocation plan as the Parent. Its input bundle must contain the
   unchanged Parent instruction ledger and Parent coordinates.
8. Invoke exactly one Boss at `/root/boss` with that bundle unchanged. The Boss
   stays active until every declared Sub result has been reviewed and the final
   synthesis has been returned.
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
13. The Boss captures the concrete Sub actor ID and Host invocation receipt,
    then claims the turn with its own actor reference as `invoker_actor_ref`.
14. Require one JSON-compatible bounded Sub result and source evidence references.
15. The Host transport submits the unchanged envelope through
    `/v1/task-frame/worker-result` with the same Worker actor, invocation
    receipt, and concrete Host result evidence reference. The Parent must not
    call `complete_turn` or reconstruct this envelope.
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
host_invocation_receipt_ref: <same receipt used to claim the turn>
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

The Host binds this envelope to the claimed turn when it constructs the
dedicated Worker-result request. The Runtime computes `worker_result_digest`
from that exact envelope and preserves the envelope in execution evidence and
the Result Packet. Missing fields remain missing or `UNKNOWN`; the Host and
Parent must not infer them from conversation history.

```text
Parent direct complete_turn
  -> WORKER_RESULT_ENVELOPE_REQUIRED

rewritten envelope or missing Host result evidence
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
