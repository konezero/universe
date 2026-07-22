# AI Runtime Governance

Status: Candidate Core Architecture
Scope: ai-career
Role: Conductor
Created: 2026-07-01

## Purpose

This document defines the candidate top-level runtime governance model for `ai-career`.

`ai-career` is not only a Resume, Memory, or Session recovery system. It is an AI Runtime Governance architecture.

The goal is to keep AI execution stable when context, memory, repository state, user commands, and prior runtime frames compete for influence.

## L0 Declaration

```text
ACTIVE ANCHOR IS THE POINT OF REFERENCE.

WHEN INTERPRETATION IS UNCERTAIN,
PULL ACTIVE ANCHOR FORWARD.
```

The Active Anchor is not a coercive command.

It is the reference point used to interpret current authority, contract, boundary, safety, and execution scope.

When strong context, competing triggers, stale assumptions, memory, resume data, or prior task frames create ambiguity, the runtime should not solve the conflict inside the old frame.

It should pull the Active Anchor forward, then rebuild the current runtime from that reference point.

## Layer Model

```text
L0  AI Runtime Governance
    Purpose: anchor, principles, contract, authority, safety, boundaries

L1  Session Framework
    Purpose: session lifecycle, boot, reboot, resume, mode switch, session out

L2  Runtime Model
    Purpose: current command execution structure

L3  Persistence Model
    Purpose: recoverable durable state
```

## L0: AI Runtime Governance

L0 defines the highest-level interpretation rule.

It answers:

```text
What is the reference point?
Which authority applies?
Which contract is active?
Which boundary limits execution?
What must remain safe?
```

Candidate declarations:

```text
ACTIVE ANCHOR IS THE POINT OF REFERENCE.

WHEN INTERPRETATION IS UNCERTAIN,
PULL ACTIVE ANCHOR FORWARD.

WHEN TRIGGERS COMPETE,
PULL ACTIVE ANCHOR FORWARD.

WHEN AUTHORITY IS AMBIGUOUS,
INTERPRET FROM ACTIVE ANCHOR.

WHEN BOUNDARIES ARE UNCERTAIN,
INTERPRET FROM ACTIVE ANCHOR.
```

### Strong Sentence Pattern

Runtime governance declarations should be written in a form that is both human-readable and AI-salient.

Preferred pattern:

```text
[TRIGGER / CONDITION]
[ACTION]
[TARGET]
```

Examples:

```text
WHEN TRIGGERS COMPETE,
PULL ACTIVE ANCHOR FORWARD.

WHEN AUTHORITY IS AMBIGUOUS,
INTERPRET FROM ACTIVE ANCHOR.

BEFORE EXECUTION BEGINS,
ESTABLISH ACTIVE FRAME.

AFTER TASK COMPLETION,
WEAKEN COMPLETED FRAME.
```

This avoids weak prose-only policy and avoids treating noun lists as executable runtime guidance.

## L1: Session Framework

L1 defines session lifecycle transitions.

```text
Boot
Reboot
Resume
Mode Change
Session Out
```

Candidate meanings:

```text
Boot        -> start a new session lifecycle.
Reboot      -> pull Active Anchor forward and rebuild session/runtime alignment.
Resume      -> propose recovery from durable state; do not auto-grant authority.
Mode Change -> change role or mode without treating prior frame as authority.
Session Out -> end the current session lifecycle and preserve recoverable state when needed.
```

Reboot is not context deletion.

Reboot is anchor-based priority rebinding:

```text
PULL ACTIVE ANCHOR FORWARD
  -> DISCARD STALE WORKING ASSUMPTIONS
  -> LOAD GOVERNANCE
  -> CHECK RESUME / CHECKPOINT CANDIDATE
  -> DECLARE ACTIVE RULES
  -> REBUILD RUNTIME
```

## L2: Runtime Model

L2 defines active command execution.

Candidate structure:

```text
Base
  -> Anchor
  -> Runtime
  -> Frame
  -> Work
  -> Task
  -> Result
  -> Weak
  -> Discard
```

Candidate meanings:

```text
Base     -> stable starting base, usually repository + role + core contract.
Anchor   -> active interpretation reference for command, mode, direction, and boundary.
Runtime  -> execution environment built from the current goal and anchor.
Frame    -> currently active work context.
Work     -> selected context needed for the current command.
Task     -> concrete execution unit.
Result   -> reported output of the task.
Weak     -> completed frame remains recoverable but no longer dominant.
Discard  -> task-local working assumptions are removed from active interpretation.
```

Key rule:

```text
STRONG WORDS DO NOT DECIDE AUTHORITY.
ACTIVE ANCHOR INTERPRETS STRONG WORDS.
```

A command runtime should be rebuilt from the current user command first, not inherited from the previous active frame.

Candidate command flow:

```text
TRIGGER
  -> ABSOLUTE
  -> GOAL
  -> BOOT
  -> WORK
  -> TASK
  -> RESULT
  -> WEAK
  -> DISCARD
  -> ARCHIVE WHEN USEFUL
```

## L3: Persistence Model

L3 defines what remains recoverable after execution.

```text
Summary
Checkpoint
Resume Candidate
Memory
Archive
```

Candidate meanings:

```text
Summary          -> reduces recovery loss.
Checkpoint       -> anchors restore.
Resume Candidate -> proposes recovery; it does not grant authority.
Memory           -> preserves selected knowledge; it remains passive until used.
Archive          -> durable history and source of truth for derived state.
```

Persistence artifacts must not become active execution frames automatically.

```text
PERSISTENCE DOES NOT GRANT AUTHORITY.
MEMORY DOES NOT OVERRIDE ACTIVE ANCHOR.
ARCHIVE REMAINS DURABLE BUT PASSIVE UNTIL RESTORED.
```

## Runtime Separation

Weak and Discard are required to separate runtimes.

Without Weak, a completed frame can remain dominant.

Without Discard, task-local working assumptions can leak into the next runtime.

Candidate separation flow:

```text
TASK COMPLETE
  -> RESULT
  -> WEAKEN COMPLETED FRAME
  -> DISCARD TASK-LOCAL ASSUMPTIONS
  -> ARCHIVE SELECTED MEANING WHEN USEFUL
```

Then a new strong context or command can start cleanly:

```text
STRONG CONTEXT ARRIVES
  -> PULL ACTIVE ANCHOR FORWARD
  -> REBUILD RUNTIME
  -> ACTIVATE NEW FRAME
```

## Placement Test

Future documents should be classified by layer.

```text
Anchor / Contract / Authority / Safety / Boundary -> L0
Boot / Reboot / Resume / Mode Change / Session Out -> L1
Base / Runtime / Frame / Work / Task / Weak / Discard -> L2
Summary / Checkpoint / Resume Candidate / Memory / Archive -> L3
```

If a concept cannot be placed cleanly, it is not ready to become core policy.

## Candidate Principle

```text
THE RUNTIME DOES NOT NEED MORE RULES FIRST.
THE RUNTIME NEEDS A STABLE POINT OF REFERENCE.
```

## Adoption Status

This is a candidate core architecture document.

It is based on memory-sync hardpoints from 2026-07-01, including:

- AI Runtime Governance Layer Model
- Command Runtime Trigger / Strong Composition
- Runtime Contract Active Frame
- Task Isolation Runtime Boundary
- Discard / Reboot / Priority Rebinding
- Status Command and Runtime Archive observations
- Anchor / Trigger / Action sentence rule

It should be reviewed by Conductor before becoming canonical policy.
