# Runtime Anchor Frame Routing Constraint

Status: core runtime gate
Scope: ai-career / attached project runtime
Layer: Runtime Routing Constraint
Parent: `.ai/core/RUNTIME_LIFECYCLE.md`
Created: 2026-07-06

## Purpose

Runtime Anchor Frame Routing Constraint prevents a session from using partial
conversation context, connector search snippets, role labels, mode labels, or
stale assumptions as runtime routing authority.

This gate applies during fresh boot, mid-conversation entry, resume, reboot,
OS_STATUS, OS_VALIDATE, mode selection, and immediately before execution
routing.

## Core Declaration

```text
EXECUTION AND RUNTIME ROUTING MUST ORIGINATE FROM THE ACTIVE RUNTIME ANCHOR FRAME AND SOURCE-BACKED RUNTIME EVIDENCE.

CONVERSATION CONTEXT IS REFERENCE ONLY.

CONVERSATION CONTEXT IS NOT ROUTING AUTHORITY.

IF THE REQUIRED RUNTIME ANCHOR FRAME IS MISSING, STALE, OR MISMATCHED, RETURN ANCHOR_FRAME_REQUIRED.
```

## Rule

Before runtime routing or execution, resolve the current coordinate from:

```text
Active Runtime Anchor Frame
  + source-backed runtime evidence
  + current Commander intent
```

Do not route execution from:

```text
previous conversation context
partial mid-session context
compressed context
connector search snippets
role label in conversation
mode label in conversation
anchor label in conversation
session_id alone
session location alone
```

## Required Runtime Anchor Frame

The active Runtime Anchor Frame should provide or reference:

```text
Node
Mode
Role
Mode Scope
Anchor ID
State
Authority
Execution Assignment when applicable
Session coordinate when applicable
Evidence references
Updated / currentness coordinate when applicable
```

Unknown fields must remain `UNKNOWN`.

Missing fields do not become authority through conversation context.

## Routing Constraint

Runtime routing may continue only when the active frame is usable for the
requested route.

```text
Frame present
  + frame current for the requested surface
  + frame matches the target repository / node / mode when relevant
  + source-backed evidence is available
  -> route by current Commander intent
```

If the frame is not usable:

```text
missing frame      -> ANCHOR_FRAME_REQUIRED
stale frame        -> ANCHOR_FRAME_REQUIRED
mismatched frame   -> ANCHOR_FRAME_REQUIRED
unreadable frame   -> ANCHOR_FRAME_REQUIRED
```

`ANCHOR_FRAME_REQUIRED` means the runtime must read or rebuild the active
Runtime Anchor Frame before routing execution.

## Mid-Session Entry

When a session enters an existing conversation in the middle:

```text
partial prior context is reference only
  -> read active Runtime Anchor Frame
  -> read source-backed runtime evidence
  -> resolve current coordinate
  -> classify current Commander intent
  -> route only after the frame constraint passes
```

The session must not infer role, mode, authority, or execution permission from
the visible conversation prefix.

## Relationship To Existing Gates

This gate does not replace:

```text
INTENT_FIRST_ROUTING_GATE.md
COMMANDER_WAIT_BUFFER_RULE.md
ROLE_MODE_AUTHORITY_GATE.md
RUNTIME_STATE_TRUST_GATE.md
RUNTIME_AUTHORITY_EXECUTION_BINDING.md
PRE_EXECUTION_VERIFICATION.md
```

It constrains their starting coordinate.

Expected order for routing-sensitive work:

```text
Runtime Anchor Frame Routing Constraint
  -> Intent First Routing Gate
  -> Commander Wait Buffer Rule when wait/still-speaking intent appears
  -> Runtime Commands / Runtime Instruction Set
  -> Role / Mode Authority Gate
  -> Runtime State Trust Gate
  -> Runtime Authority Execution Binding when mutation is in scope
  -> Pre-Execution Verification
```

Optional subordinate Task Frames remain under this same starting coordinate:

```text
Active Runtime Anchor Frame
  -> TASK_FRAME_ORCHESTRATION.md
  -> bounded Task Frame work
  -> Parent evidence recheck and adoption
```

A Task Frame or origin Anchor Snapshot must not replace the active Runtime
Anchor Frame as the routing reference.

## Authority Boundary

Runtime Anchor Frame records current coordinate and currentness evidence.

Runtime Anchor Frame does not create authority.

Authority remains source-backed and must be verified by the appropriate
authority, binding, boundary, and pre-execution gates.

## Validation Requirements

OS_VALIDATE should record whether runtime routing compared attached project
entry and frame surfaces against this constraint when a project has a Runtime
Anchor Frame or equivalent current-coordinate surface.

Required checks:

```text
active_runtime_anchor_frame_present
conversation_context_reference_only
frame_required_before_execution_routing
missing_frame_returns_anchor_frame_required
stale_frame_returns_anchor_frame_required
mismatched_frame_returns_anchor_frame_required
role_mode_authority_not_inferred_from_conversation
current_commander_intent_routes_only_after_frame_constraint
```

If a project routes execution from conversation context without checking the
active frame, OS_VALIDATE should report `PARTIAL`, `FAIL`, or `UNKNOWN`, not
`PASS`.

## Non-Goals

This gate does not:

```text
- create a new authority source;
- make conversation context useless;
- require a specific file name for every project;
- require a durable database;
- replace source-backed Runtime Boot;
- bypass current Commander intent classification;
- grant execution authority from frame presence alone.
```

Conversation context may still be used as reference after the active frame is
resolved.
