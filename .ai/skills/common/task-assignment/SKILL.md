---
name: task-assignment
description: Route a mutation request to either strict exact assignment or bounded direct-user project-source work.
---

# Task Assignment

Invocation class: `REFERENCE_RUNTIME_ADAPTER`

Capability classification: `task_assignment_proposal = AVAILABLE`

This Skill selects the narrowest valid assignment route. It does not grant
generic repository authority or execution permission.

Read-only discussion, inspection, and Task Frame coordination may keep
`execution_assignment_ref: UNASSIGNED`.

## Direct User Project-Source Work

Use `PROJECT_SOURCE_WORK` when a direct user instruction clearly requests
implementation inside project-source roots and only `CREATE` and/or `MODIFY`
is needed. This creates an Instruction Receipt and process-local Work Receipt;
it does not show a second per-file approval prompt.

```json
{
  "session_id": "<active-session-id>",
  "work": {
    "scope_kind": "PROJECT_SOURCE_WORK",
    "write_roots": ["<absolute-existing-project-source-root>"],
    "write_operations": ["CREATE", "MODIFY"],
    "boundary": "<bounded-project-source-work-boundary>",
    "task_summary": "<user-requested-implementation>",
    "instruction_ref": "<direct-user-instruction-evidence-ref>"
  }
}
```

Invoke:

```text
python .ai/runtime/reference_runtime/cli.py execution-binding begin-work \
  --endpoint <session-boot-endpoint> \
  --token <session-boot-token> \
  --request <utf8-json-file-or->
```

The resulting `WORK_RECEIPT_ACTIVATED` permits scope expansion only inside the
declared roots. Each concrete file write still goes through Execution Guard and
receives a one-time internal Mutation Receipt.

After implementation and validation, ordinary local Git staging, commit, and
push remain outside the Runtime. Do not create a Runtime proposal, approval,
or Execution Binding for those SCM operations. The resulting Git SHA may be
appended later to the approved Task Proposal's Result Receipt as work evidence.

When `write_roots` is omitted, let the Runtime resolve the root. A project-owned
`REPOSITORY_MANIFEST.md` declaration at `layers.application.root` is used first
and must name an existing directory. Otherwise, a Runtime-managed empty project
is `FRESH`: it defaults to `<repository>/src` and the receipt-aware gateway may
create that root with the first in-scope `CREATE`. An `EXISTING` project without
that declaration must name an already-existing source root; never create `src`
merely because it is conventional. The gateway may still create missing child
directories beneath a selected source root during bounded source work.

## Strict Exact Assignment

Use this route for DELETE, MOVE, COMMAND, Core/policy/template/config changes,
external effects, unknown scope, or any request outside declared project-source
roots.

```json
{
  "session_id": "<active-session-id>",
  "request": {
    "operation": "CREATE|MODIFY|DELETE|MOVE|COMMAND",
    "target": "<absolute-target>",
    "boundary": "<exact-task-boundary>",
    "write_roots": ["<approved-candidate-root>"],
    "write_operations": ["<candidate-operation>"],
    "task_summary": "<bounded-task-summary>",
    "request_ref": "<current-user-request-evidence-ref>"
  }
}
```

```text
python .ai/runtime/reference_runtime/cli.py execution-binding propose \
  --endpoint <session-boot-endpoint> \
  --token <session-boot-token> \
  --request <utf8-json-file-or->
```

Present the raw `EXECUTION_ASSIGNMENT_PROPOSED` candidate to the user before
binding or mutation. It requires exact approval.

The receipt semantics are defined in
`.ai/core/INSTRUCTION_WORK_RECEIPT_CONTRACT.md`.
