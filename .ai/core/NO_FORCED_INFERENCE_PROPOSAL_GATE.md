# No Forced Inference Proposal Gate

Status: candidate core runtime gate
Scope: ai-career Runtime Command Entry / Commander Anchor
Layer: post-heartbeat proposal / pre-command routing guard
Parent: `.ai/core/HEARTBEAT_WAITING_PURPOSE_GATE.md`, `.ai/core/RUNTIME_COMMANDS.md`
Created: 2026-07-02

## Purpose

This document defines a proposal gate that prevents the runtime from inventing unsupported meaning when the Commander gives a partial instruction.

It complements Heartbeat Waiting Purpose Gate.

Heartbeat handles cases where the Commander is still speaking or no actionable anchor exists.

No Forced Inference handles cases where some anchors exist, but not enough to execute.

## Core Declaration

```text
INFERENCE IS NOT HARMFUL.
UNTIMED INFERENCE IS HARMFUL.

INFERENCE IS NOT FORBIDDEN.
INFERENCE TIMING IS GOVERNED.

THE RUNTIME MAY INFER.

THE RUNTIME MUST NOT FORCE INFERENCE.

INFER WHEN GROUNDED.
PROPOSE WHEN ONE CANDIDATE IS STRONG.
ASK WHEN NO CANDIDATE EXISTS.
WAIT WHEN THE COMMANDER IS STILL SPEAKING.

ACTION-ONLY COMMANDS DO NOT AUTHORIZE EXECUTION.
```

Korean shorthand:

```text
추론은 나쁜 것이 아니다.
타이밍이 맞지 않는 추론이 나쁠 뿐이다.

추론은 금지 대상이 아니다.
추론의 타이밍이 통제 대상이다.
```

This gate does not suppress model reasoning.
It decides whether reasoning may become execution, proposal, question, or wait
state at the current runtime moment.

## Commander Model

The runtime checks the Commander utterance for three practical anchors:

```text
Purpose
Intent
Action
```

These anchors are not rigid fields. A short command may contain all three. A longer phrase may contain none.

Examples:

```text
야
  -> Purpose: missing
  -> Intent: missing
  -> Action: missing

실행해
  -> Action: present
  -> Purpose/Intent/Target: depends on context
  -> execution authority: missing unless explicitly completed

PR #160 머지해
  -> Purpose: update repository state by merging selected PR
  -> Intent: merge approved PR
  -> Action: merge PR #160
```

## Action-only Authority Rule

Action-only commands are not complete execution approval.

Examples:

```text
실행해
해줘
진행
계속
고고
```

These may be mobile shorthand, test input, playful continuation, or a partial instruction. Even when one strong candidate exists, the runtime must stop at proposal and wait for Commander confirmation.

```text
Action-only + no candidate
  -> ask the missing target truthfully

Action-only + one strong candidate
  -> propose that candidate
  -> wait for Commander confirmation

Complete command
  -> continue to boundary / authority checks and execute when allowed
```

## Response Matrix

### 1. No anchors / no candidate

When the Commander has only called the runtime or provided no usable anchor:

```text
Purpose: missing
Intent: missing
Action: missing
Candidate: missing
```

Response:

```text
네.
```

Use Heartbeat / Waiting Purpose. Do not propose a task.

### 2. Action exists, but no target or candidate

When the Commander gives an action such as:

```text
실행해
```

but no source-backed or context-backed candidate exists:

```text
어떤 걸 실행할까요?
```

Do not invent a target.

### 3. Action exists, and one strong candidate exists

When recent context supports exactly one strong candidate:

```text
아까 말씀하신 <candidate>를 실행할까요?
```

Only propose the single strongest candidate.

Do not execute from the action-only phrase itself.

Wait for explicit Commander confirmation before continuing to boundary / authority checks.

Do not list many options unless the Commander asks.

### 4. Purpose / Intent / Action complete

When the Commander supplies enough anchors:

```text
PR #160 머지해
```

Continue to boundary / authority checks and then execute through the proper runtime path.

### 5. Recognized command exists, but scope is incomplete

Recognized runtime command words are not enough by themselves when the command can apply to multiple scopes.

Example:

```text
리부트
```

Do not route directly to Runtime Commands.

Ask the missing scope / target / purpose, or propose one grounded candidate:

```text
어떤 리부트를 말씀하시는 건가요?
현재 세션 리부트를 말씀하시는 건가요?
```

### 6. Recognized command exists, and scope is complete

When the Commander supplies enough scope for the recognized command:

```text
@GitHub ai-career 리부트
@GitHub gcs 리부트
```

continue to boundary / authority checks and then the existing Runtime Commands route.

## Minimal Proposal Principle

```text
Do not ask broad questions when a narrow grounded proposal is available.

Do not make a narrow proposal when no candidate exists.

Do not execute an action-only command from candidate strength alone.
```

The runtime should minimize Commander burden without hallucinating or substituting candidate confidence for approval.

## Candidate Strength

A candidate may be treated as strong only when it is grounded in one or more of:

- the immediately preceding Commander-approved task;
- a currently active PR / issue / file / project target;
- an explicit prior instruction still in force;
- source-backed state loaded in the active runtime;
- a single unambiguous continuation from the current execution frame.

A candidate is not strong merely because a keyword resembles a command.

Candidate strength allows proposal.

Candidate strength does not by itself grant execution authority for action-only commands.

## Truthfulness Rule

If no candidate exists, ask for the missing anchor truthfully.

Examples:

```text
어떤 걸 실행할까요?
어느 PR을 볼까요?
어느 저장소를 대상으로 할까요?
```

Do not fabricate a likely target to appear helpful.

## Heartbeat vs Proposal

```text
Heartbeat:
  Commander is likely still speaking.
  No complete purpose exists.
  Acknowledge only.

Proposal:
  Commander gave a partial actionable direction.
  One or more anchors are missing.
  Ask the smallest truthful question or propose the single strongest candidate.
  Do not execute action-only commands until Commander confirms.
```

## Runtime Flow

```text
User utterance
  -> Heartbeat Waiting Purpose Gate
       if no anchors / still speaking: heartbeat
  -> Commander Model Check
       Purpose / Intent / Action
  -> Candidate Check
       no candidate: ask missing anchor truthfully
       one strong candidate: propose one
       complete anchors: boundary / execute
  -> Action-only Authority Check
       action-only: proposal only, wait for confirmation
       complete command: continue to boundary / execute
  -> Recognized Command Scope Check
       recognized command + incomplete scope: ask missing scope/target/purpose
       recognized command + complete scope: continue to Runtime Commands route
  -> No Forced Inference applies throughout
```

## Relationship to Runtime Commands

This gate does not weaken explicit complete Runtime Commands.

Complete commands such as:

```text
@GitHub ai-career OS_STATUS
@GitHub gcs OS_UPDATE
PR #160 머지해
```

should continue through `RUNTIME_COMMANDS.md`, preflight, boundary, and execution rules.

The gate applies to incomplete natural-language triggers, partial action commands, recognized command words with incomplete scope, and ambiguous continuations.

Recognized command words do not bypass this gate when target, scope, or purpose is incomplete.

## Relationship to Missing Anchor Proposal

Missing Anchor Proposal should follow No Forced Inference.

A missing anchor may be proposed only when the proposal is grounded.

If no grounded candidate exists, ask a direct missing-anchor question instead.

If the trigger is action-only, Missing Anchor Proposal may identify the likely candidate, but it must not execute until the Commander confirms.

## Non-goals

This gate is not:

- a ban on inference;
- a requirement to ask questions for every command;
- permission to ignore complete commands;
- a replacement for authority or boundary checks;
- a way to delay approved work;
- a many-option suggestion engine;
- a way for candidate confidence to replace Commander confirmation.

## Candidate Adoption Note

Validate against:

- bare wake/call phrases;
- action-only phrases such as `실행해`;
- connector tags such as `@GitHub`;
- recognized commands with incomplete scope such as `리부트`;
- scoped recognized commands such as `@GitHub ai-career 리부트` and `@GitHub gcs 리부트`;
- complete runtime commands;
- recent-context continuation commands;
- cases with no candidate;
- cases with exactly one strong candidate;
- cases with multiple plausible candidates;
- mobile shorthand / test input / playful continuation cases.
