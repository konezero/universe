# Intent First Routing Gate

Status: candidate core runtime gate
Scope: ai-career Runtime Command Entry / Intent Grammar
Layer: pre-command interpretation gate
Parent: `.ai/core/RUNTIME_COMMANDS.md`
Created: 2026-07-05

## Purpose

This document defines the first interpretation gate before command or mode
routing.

It prevents a known command, mode, role, or anchor token from being routed only
because the token appears in the Commander utterance.

The gate exists to stop this failure mode:

```text
utterance contains known runtime token
  -> token match dominates interpretation
  -> runtime routes mode / command before intent is known
  -> review question becomes mode switch or command execution
```

## Core Declaration

```text
INTENT CLASSIFICATION PRECEDES COMMAND OR MODE ROUTING.

TOKEN MATCH IS EVIDENCE.

INTENT CONFIRMATION IS ROUTING AUTHORITY.

A MENTIONED COMMAND / MODE / ROLE / ANCHOR TOKEN IS NOT A COMMAND BY ITSELF.
```

## Inference Timing Principle

```text
INFERENCE IS NOT HARMFUL.
UNTIMED INFERENCE IS HARMFUL.

INFERENCE IS NOT FORBIDDEN.
INFERENCE TIMING IS GOVERNED.
```

This gate does not prevent the runtime from reasoning.
It decides whether reasoning is currently allowed to become execution,
proposal, question, wait state, or no route.

## Rule

Before command routing or mode switching, classify the Commander utterance
intent.

A known command, mode, role, or anchor token mentioned as the subject of a
question, review, wording discussion, comparison, documentation critique, or
example must not trigger command routing or mode switching by itself.

Permission-shaped language must not be treated as execution authorization by
itself.

Examples:

```text
that should be fine
that can be shown that way
it is okay to change it
it is okay to commit it
you can treat it as authorized
표시하면 돼
그렇게 하면 돼
수정해도 돼
커밋해도 돼
권한 있다고 보면 돼
```

These utterances may express permission, judgment, design correction, or
acceptance.

They do not grant execution unless the utterance also contains explicit
imperative execution intent and a sufficiently scoped target.

Routing is allowed only when the utterance intent confirms one of:

```text
command execution
mode switch
runtime boot
status request
validation
update
checkpoint / resume / archive action
another recognized runtime action
```

## Permission-Shaped Language Rule

```text
PERMISSION-SHAPED LANGUAGE IS NOT EXECUTION AUTHORIZATION.

NATURAL LANGUAGE PERMISSION DOES NOT GRANT MUTATION.

EXECUTION REQUIRES EXPLICIT IMPERATIVE INTENT.
```

When an utterance states how something should be interpreted, displayed,
allowed, or judged, classify it as design / review / answer intent unless an
explicit execution verb is present.

Examples of non-execution design or permission language:

```text
OS_STATUS should only show policy.
OS_STATUS는 정책만 표시하면 돼.
worktree should be separated from session.
session.md should not own project worktree state.
Runtime Image can be absent here.
Authority can remain UNASSIGNED.
```

Expected runtime behavior:

```text
Intent: design note / policy judgment
Routing: no mutation
Execution Assignment: UNASSIGNED
Response: explain, summarize, or propose the patch; wait for explicit execution approval
```

Explicit execution verbs may include project-local equivalents of:

```text
modify
apply
patch
add
remove
delete
write
commit
push
open PR
수정해
반영해
적용해
패치해
추가해
제거해
삭제해
써줘
커밋해
푸쉬
PR 올려
```

Action-only confirmations such as `go`, `proceed`, or `gogo` still require the
No Forced Inference Proposal Gate when target or scope is incomplete.

## Mentioned Token Cases

These utterances mention runtime tokens, but do not route the token as a command
or mode switch:

```text
마스터 한글 병기 표기 괜찮은가?
  -> Intent: wording review
  -> Target: "마스터" alias notation
  -> Mode switch: no

OS_UPDATE 문구 이상하지?
  -> Intent: documentation review
  -> Target: OS_UPDATE wording
  -> OS_UPDATE execution: no

리부트라는 표현 바꿀까?
  -> Intent: wording discussion
  -> Target: REBOOT wording
  -> REBOOT execution: no

메모싱크 alias 남길까?
  -> Intent: alias review
  -> Target: MEMORY_SYNC alias
  -> MEMORY_SYNC execution: no
```

## Routing Cases

These utterances may route after intent and scope are checked:

```text
마스터모드
  -> Intent: mode switch candidate
  -> Scope: incomplete unless project / node context is available
  -> Route only after Node / Mode and authority checks

@GitHub gcs 마스터모드
  -> Intent: project mode switch / boot candidate
  -> Node: GCS
  -> Mode: MASTER
  -> Route through project entry surfaces before broad search

OS_UPDATE
  -> Intent: runtime update candidate
  -> Scope: incomplete unless source / target is known
  -> Route only after scope / target / purpose checks
```

## Relationship To Existing Gates

This gate runs before:

```text
HEARTBEAT_WAITING_PURPOSE_GATE.md
NO_FORCED_INFERENCE_PROPOSAL_GATE.md
ROLE_MODE_AUTHORITY_GATE.md
RUNTIME_COMMANDS.md mission routing
```

It does not weaken explicit complete commands.

It only prevents early token-trigger routing before intent is classified.

## Failure Handling

If intent cannot be classified, do not route from token match alone.

Report:

```text
Intent: UNKNOWN
Routing: blocked
Reason: known runtime token mentioned, but execution or mode-switch intent is not confirmed
```

If the utterance is a question, review, wording discussion, or example, answer
or review within that intent and keep execution authority `UNASSIGNED`.

## Guardrails

```text
Do not treat a token mention as command authority.
Do not treat a mode label mention as mode-switch authority.
Do not treat permission-shaped language as execution authorization.
Do not treat design judgment as patch approval.
Do not route from search result tokens before intent is classified.
Do not execute from examples, comparisons, or wording discussions.
Do not infer authority from role / mode / anchor labels.
```
