# Runtime Model

Status: Candidate Core Architecture
Scope: ai-career
Layer: L2
Parent: `.ai/core/AI_RUNTIME_GOVERNANCE.md`
Created: 2026-07-01

## Purpose

Runtime Model defines the current command execution structure.

It is the layer where a user command becomes an executable frame and task.

Runtime is not persistence.

Runtime is not authority.

Runtime is built from the Active Anchor and current command.

## L2 Declaration

```text
WHEN A COMMAND TRIGGERS,
BUILD RUNTIME FROM CURRENT ANCHOR.

WHEN A FRAME COMPLETES,
WEAKEN COMPLETED FRAME.

WHEN TASK ASSUMPTIONS ARE LOCAL,
DISCARD THEM AFTER TASK END.

WHEN STRONG CONTEXT ARRIVES,
PULL ACTIVE ANCHOR FORWARD AND REBUILD RUNTIME.
```

## Runtime Chain

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
  -> Archive when useful
```

## Base

Base is the stable starting base.

Typical contents:

```text
Repository
Role
Core contract
Known write boundary
Source priority
```

Base is not the same as current task state.

## Anchor

Anchor is the active interpretation reference inside the runtime model.

L0 defines the Active Anchor as the point of reference.

L2 uses that Anchor to build the runtime.

```text
ANCHOR INTERPRETS CURRENT COMMAND.
ANCHOR BINDS DIRECTION AND BOUNDARY.
ANCHOR DOES NOT GRANT AUTHORITY BY ITSELF.
```

## Runtime

Runtime is the execution environment created for the current command.

It should be rebuilt from the current command when triggers compete or strong context arrives.

```text
TRIGGER
  -> ABSOLUTE CHECK
  -> GOAL RESET
  -> BOOT BUILD
  -> WORK SELECT
  -> TASK EXECUTE
```

## Frame

Frame is the currently active Parent work context.

There should be exactly one dominant active frame.

```text
ONLY ACTIVE FRAME CONTROLS EXECUTION.
COMPLETED FRAMES REMAIN RECOVERABLE BUT PASSIVE.
```

One dominant Parent Runtime Frame may coordinate optional subordinate Task
Frames. A Task Frame isolates bounded work but does not become a second Current
Anchor or execution authority.

```text
Parent Runtime Frame
  -> optional subordinate Task Frames
  -> Result Packets
  -> Parent Adoption Gate
```

Governing contract:

```text
TASK_FRAME_ORCHESTRATION.md
```

## Work

Work is selected context needed for the current command.

Examples:

```text
Resume files
Inbox queue
Current PR
Current repository files
Search results
Uploaded artifact
```

Unrelated previous strong anchors should be weakened.

## Task

Task is the concrete execution unit.

A task should be explicit enough that completion can be detected.

Long-running, divisible, review-oriented, or debate-oriented work may be
isolated in a Task Frame when the Host supports the required capabilities.
Task Frame completion does not adopt its result into the Parent.

Examples:

```text
Review PR
Create memory-sync note
Restore Conductor
Check inbox
Search source
Create candidate core doc
```

## Result

Result is the reported outcome of task execution.

Result should include completion state when useful.

```text
Complete
Ready
Waiting
Failed
Unknown
```

A subordinate Task Frame returns a Result Packet with
`adoption_state: CANDIDATE`. Only the current Parent may adopt it after current
evidence is rechecked.

## Weak

Weak lowers the dominance of a completed frame.

It does not delete history.

It prevents completed work from controlling unrelated future commands.

```text
AFTER TASK COMPLETION,
WEAKEN COMPLETED FRAME.
```

## Discard

Discard removes task-local working assumptions from active interpretation.

It does not delete durable memory.

It prevents experimental runtime state from leaking into the next runtime.

```text
AFTER TASK COMPLETION,
DISCARD TASK-LOCAL ASSUMPTIONS.
```

## Runtime Separation

Weak and Discard separate command runtimes.

```text
TASK COMPLETE
  -> RESULT
  -> WEAKEN COMPLETED FRAME
  -> DISCARD TASK-LOCAL ASSUMPTIONS
  -> ARCHIVE SELECTED MEANING WHEN USEFUL
```

Then the next command starts from Anchor again:

```text
NEW TRIGGER
  -> PULL ACTIVE ANCHOR FORWARD WHEN NEEDED
  -> REBUILD RUNTIME
  -> ACTIVATE NEW FRAME
```

## Strong Composition

Strong command behavior is not produced by a single strong word.

It is produced by composition:

```text
Trigger + Structure + Strong Keywords + Action
```

Candidate pattern:

```text
WHEN <condition>,
<ACTION> <TARGET>.
```

Examples:

```text
WHEN TRIGGERS COMPETE,
PULL ACTIVE ANCHOR FORWARD.

WHEN TASK COMPLETES,
WEAKEN COMPLETED FRAME.

WHEN ASSUMPTIONS ARE TASK-LOCAL,
DISCARD AFTER RESULT.
```

## Layer Boundary

L2 executes the current command frame.

L2 may produce results, summaries, candidates, or archive requests.

L2 does not make persistence artifacts authoritative.

```text
RUNTIME EXECUTES.
PERSISTENCE RECOVERS.
GOVERNANCE INTERPRETS.
```

## Placement Test

A concept belongs in L2 when it answers:

```text
What is active now?
What context is selected?
What task is running?
What completes the task?
What must be weakened or discarded afterward?
```
