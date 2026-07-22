# Commander Wait Buffer Rule

Status: core runtime gate
Scope: ai-career Runtime Command Entry / Multi-part Commander Input
Layer: pre-intent wait / deferred interpretation
Parent: `.ai/core/HEARTBEAT_WAITING_PURPOSE_GATE.md`
Created: 2026-07-05

## Purpose

This document defines how Runtime should behave when the Commander explicitly
pauses, says they are still speaking, or provides a multi-part instruction over
several messages.

Commander Wait is not only a stop signal.

It is a deferred interpretation state.

## Core Declaration

```text
COMMANDER WAIT MEANS STOP MUTATION.

WAITING_COMMANDER COLLECTS CONTEXT BUT DOES NOT EXECUTE IT.

BUFFERED FRAGMENTS ARE NOT AUTHORITY.

EXECUTION REQUIRES EXPLICIT RELEASE INTENT.
```

## Wait Triggers

Runtime enters `WAITING_COMMANDER` when the Commander says or implies:

```text
wait
hold on
pause
not yet
one sec
let me finish
still speaking
잠만
기다려
기다려봐
아직
하나 더
두 개야
말 더 할게
```

Project-local language variants should be interpreted by meaning, not by exact
string match only.

## Buffer Model

`WAITING_COMMANDER` may collect fragments conceptually.

Example:

```text
WAIT
  ||
  fragment 1: infer this
  fragment 2: explain that
  fragment 3: exclude this scope
  ||
EXECUTE
```

Collected fragments form a candidate instruction bundle.

The bundle is not executable by itself.

## Allowed While Waiting

Runtime may:

```text
acknowledge briefly
record that it is waiting
summarize received fragments when asked
ask a narrow clarification question
identify conflict or missing scope
```

Runtime must keep these responses non-mutating.

## Forbidden While Waiting

Runtime must not:

```text
route commands
switch modes
boot or update projects
read broad repository surfaces
patch files
commit
push
open PRs
memory sync
promote candidates
execute tools for mutation
```

Read-only clarification is allowed only when it helps the Commander finish the
instruction and does not route into work.

## Release Intent

Runtime may leave `WAITING_COMMANDER` only when the Commander provides explicit
release intent.

Examples:

```text
execute
run it
continue
proceed
go
do it
now apply
이제 해
실행해
진행해
고고
반영해
적용해
수정해
```

Release intent does not bypass routing.

It releases the buffered bundle into the normal runtime gates.

## Release Flow

```text
WAITING_COMMANDER
  -> release intent received
  -> summarize interpreted bundle when useful
  -> classify Intent
  -> verify Purpose / Scope / Target
  -> run No Forced Inference Proposal Gate when incomplete
  -> verify Boundary / Authority / Currentness
  -> Pre-Execution Verification
  -> Execution only if all checks pass
```

If the bundle is ambiguous, risky, or has multiple plausible targets, Runtime
must ask for confirmation before mutation.

## Authority Boundary

Buffered fragments are not authority.

Release intent is not authority by itself.

Authority still comes from source-backed runtime policy, Commander approval when
required, execution assignment, and pre-execution verification.

## Relationship To Other Gates

```text
Commander Wait Buffer Rule
  -> handles explicit pause / still-speaking / multi-part instruction collection.

Heartbeat Waiting Purpose Gate
  -> handles bare calls and incomplete purpose.

Intent First Routing Gate
  -> classifies utterance intent after wait is released.

No Forced Inference Proposal Gate
  -> blocks execution when the released bundle is incomplete or over-inferred.
```

Commander Wait has priority over candidate confidence.

```text
Strong candidate exists
  !=
permission to continue while Commander is still speaking
```

## Non-Goals

This rule is not:

- platform-level silent input buffering;
- background task scheduling;
- permission to execute after wait without release intent;
- permission to treat collected fragments as authority;
- replacement for Intent First Routing;
- replacement for No Forced Inference;
- replacement for Pre-Execution Verification.

## Validation Questions

Runtime QA should ask:

```text
Does explicit wait enter WAITING_COMMANDER?
Does WAITING_COMMANDER block mutation and command routing?
Can the runtime summarize buffered fragments without executing them?
Does release intent route the bundle through normal gates?
Does ambiguous release ask for confirmation?
Are buffered fragments rejected as authority?
```
