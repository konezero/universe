# User Interrupt Runtime Rule

Status: core runtime candidate
Repository: `konezero/ai-career`
Scope: task lifecycle interruption / resume behavior

## Purpose

A task may stop because the User interrupts generation or cancels execution before the runtime can finish reporting the result.

This is different from a blocked task or a failed connector.

## Core Rule

```text
User interrupt is a task lifecycle state.
Interrupted tasks must not be treated as complete.
```

## Task State

Task Queue Runtime should include `INTERRUPTED` as a distinct state.

```text
READY
ACTIVE
INTERRUPTED
BLOCKED
FALLBACK
COMPLETE
FAILED
```

## Difference from BLOCKED

```text
BLOCKED
  -> environment, connector, permission, executor, or validation prevents progress

INTERRUPTED
  -> user stopped or cancelled while task was active
```

## Normalized Result

When a task is interrupted, the normalized result should preserve the task for possible resume.

```yaml
task_result:
  state: interrupted
  result_type: user_interrupt
  pop: false
  reason: user_cancelled_generation_or_execution
  resume: true
  confirmed_commit: null
  next_action: check_side_effect_then_resume_or_discard
```

## Recovery Rule

If the task may have performed side effects before interruption, the next run must check the target source before retrying.

Example:

```text
GitHub create_file started
  -> user interrupt
  -> no final commit response
  -> recovery must check whether the target file or commit exists
  -> then resume, skip, or repair
```

## Scheduler Behavior

```text
state: interrupted
  -> pop: false
  -> keep task in queue or mark resumable
  -> require recovery check before retry when side effects may have occurred
```

## Status Reporting Rule

The assistant must not report interrupted work as complete.

Correct report:

```text
Task was interrupted.
Confirmed: create attempt started.
Unknown: whether commit/file was created.
Next: check source before retry.
```

Incorrect report:

```text
Task complete.
```

unless a source-backed completion result is confirmed.

## Status

Core runtime candidate.

This rule complements `TASK_QUEUE_RUNTIME_V1.md` and `RUNTIME_STATUS_SOURCE_RULE.md`.
