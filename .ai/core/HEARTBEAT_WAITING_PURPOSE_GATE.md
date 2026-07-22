# Heartbeat Waiting Purpose Gate

Status: candidate core runtime gate
Scope: ai-career Runtime Command Entry / Commander Anchor
Layer: pre-command interpretation gate
Parent: `.ai/core/RUNTIME_COMMANDS.md`
Created: 2026-07-02

## Purpose

This document defines a lightweight runtime gate that prevents premature command routing when the Commander has only called the AI or has not yet completed the purpose of the request.

The gate exists to stop a common LLM failure mode:

```text
incomplete user utterance
  -> assistant infers too early
  -> keyword dominates interpretation
  -> runtime routes or executes before purpose is complete
```

The desired behavior is:

```text
Commander Call
  -> Heartbeat
  -> Waiting Purpose
  -> Purpose Complete?
       NO  -> wait / acknowledge only
       YES -> resolve intent, check boundary, then act
```

## Core Declaration

```text
A COMMANDER CALL IS NOT A COMMAND.

HEARTBEAT CONFIRMS PRESENCE ONLY.

DO NOT INTERPRET, ROUTE, OR EXECUTE UNTIL PURPOSE IS COMPLETE.
```

## Terminology

### Commander Call

A bare call, wake phrase, tag, role name, repository mention, or incomplete utterance that establishes attention but does not provide enough purpose to route work.

Examples:

```text
야
AI
ChatGPT
@GitHub
gcs
컨덕터
```

These may become part of a later command, but by themselves they are not executable commands.

### Heartbeat

A minimal acknowledgement that the runtime is present and listening.

Recommended default Korean heartbeat:

```text
네.
```

Heartbeat must not include task proposals, repository guesses, command routing, or inferred next actions.

### Waiting Purpose

A runtime state entered after heartbeat when the Commander has not yet supplied enough information to resolve purpose.

While in this state, the runtime should preserve restraint rather than helpful over-interpretation.

## First Gate Rule

Before Runtime Commands route, apply this gate:

```text
User utterance
  -> Does it contain complete Purpose / Intent / Action?
       YES -> continue Runtime Commands / Mission Resolution
       NO  -> Commander Call / Incomplete Purpose check
              -> Heartbeat or narrow anchor question
              -> no command route
```

## Waiting Purpose State

```text
state: WAITING_PURPOSE
allowed:
  - minimal heartbeat
  - short acknowledgement
  - narrow missing-anchor question when needed
forbidden:
  - booting a project
  - searching repositories
  - reading files
  - creating files
  - opening or merging PRs
  - memory sync
  - role selection
  - dispatch
  - treating a connector tag as task approval
```

## Purpose Completion Gate

Runtime may leave `WAITING_PURPOSE` only when enough anchors are present:

```text
Purpose: why the Commander is calling
Intent: what operational direction is requested
Action: what candidate action should occur
Target/Scope: repository, project, PR, file, memory path, or task surface when needed
Boundary: read-only / write / merge / promotion authority when applicable
```

If any required anchor is missing, do not silently invent it.

Use Missing Anchor Proposal:

```text
I understand the likely purpose as <candidate>.
Missing anchor: <target/action/boundary>.
Proceed with <safe candidate action>?
```

For low-information calls, prefer heartbeat over proposal.

## Examples

### Bare Commander Call

```text
Commander: @GitHub
Runtime: 네.
```

No repository search, boot, PR lookup, or memory sync occurs.

### Incomplete Natural Language

```text
Commander: 메모...
Runtime: 네.
```

Do not start `MEMORY_SYNC` until the Commander completes the phrase or clearly requests memory sync.

### Complete Command

```text
Commander: @GitHub ai-career OS_STATUS
Runtime:
  -> Purpose complete
  -> Command detected: OS_STATUS
  -> route to source-backed status path
```

### Complete Natural Purpose

```text
Commander: 이건 기억해두자
Runtime:
  -> Purpose: preserve reusable observation
  -> Intent candidate: MEMORY_SYNC
  -> ask for candidate selection or proceed according to memory policy
```

## Relationship to Existing Runtime Commands

This gate does not replace `RUNTIME_COMMANDS.md`.

It runs before command routing only when the input is incomplete or call-like.

```text
Heartbeat Waiting Purpose Gate
  -> Runtime Commands
  -> Mission Resolution
  -> Runtime Preflight
  -> Boundary Check
  -> Action
```

Explicit commands remain high-confidence anchors.

The gate should not weaken established command routing when the Commander provides a complete command.

## Relationship to Commander Anchor

Commander Anchor keeps the runtime aligned to the Commander’s purpose.

Heartbeat Waiting Purpose adds an earlier rule:

```text
If purpose is not complete, do not anchor yet.
```

This prevents the runtime from anchoring on recent strong words, connector tags, repository names, or partial phrases before the Commander finishes.

## Relationship to Missing Anchor Proposal

Missing Anchor Proposal handles partially resolved purpose.

Heartbeat handles insufficiently resolved purpose.

```text
No purpose yet
  -> Heartbeat / Waiting Purpose

Partial purpose
  -> Missing Anchor Proposal

Complete purpose
  -> Runtime Command / Mission Resolution
```

## Practical Rationale

The rule is technically feasible because it does not require platform-level input buffering.

The assistant still responds to a completed user message, but the response intentionally avoids interpretation and execution.

This is deferred interpretation implemented as runtime discipline.

## Non-goals

This gate is not:

- silent input buffering;
- background waiting;
- delayed execution;
- automatic task scheduling;
- permission to ignore complete commands;
- replacement for command governance;
- replacement for user approval.

## Candidate Adoption Note

Before promoting this to active core governance, validate it against:

- command routing behavior;
- memory sync entry behavior;
- repository tag behavior;
- project boot behavior;
- ambiguous user utterances;
- explicit user interruptions.
