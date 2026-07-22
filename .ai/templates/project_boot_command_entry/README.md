# Project Boot Command Entry Template

Status: template candidate
Repository: `konezero/ai-career`
Scope: project-local command entry aliases for fresh LLM sessions

## Purpose

A fresh project session may not know the short command vocabulary yet.

This template maps governance-level commands to a project's existing boot documents without changing project business logic.

## Instance Placement

Recommended project instance path:

```text
.ai/runtime/project_instance/boot_command_entry.md
```

Project entry files such as `AGENTS.md`, `.ai/START_HERE.md`, or equivalent boot documents should link to the instance.

## Template

```text
# <PROJECT_NAME> Boot Command Entry

Status: project runtime instance
Source template: ai-career project_boot_command_entry
Target project: <PROJECT_ID>
Scope: fresh-session command entry aliases only

## Purpose

Fresh sessions may not know the short command vocabulary yet.

This file maps short commands to the project's existing boot procedure.

## Command Entry Rules

### Intent-First Routing

Intent classification must happen before command or mode routing.

Token match is evidence.

Intent confirmation is routing authority.

A known command, mode, role, or anchor token mentioned as the subject of a
question, review, wording discussion, comparison, documentation critique, or
example must not trigger command routing or mode switching by itself.

Examples:

```text
마스터 한글 병기 표기 괜찮은가?
  -> wording review
  -> do not switch to MASTER mode

OS_UPDATE 문구 이상하지?
  -> documentation review
  -> do not execute OS_UPDATE

리부트라는 표현 바꿀까?
  -> wording discussion
  -> do not execute REBOOT
```

Governing Core surface:

```text
.ai/core/INTENT_FIRST_ROUTING_GATE.md
.ai/core/RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md
```

### Connector-Backed Entry Priority

Previous conversation context is not routing authority.

Execution and runtime routing must originate from the active Runtime Anchor
Frame and source-backed runtime evidence.

Conversation context is reference only, not routing authority.

If the required Runtime Anchor Frame is missing, stale, or mismatched, return
`ANCHOR_FRAME_REQUIRED`.

For connector-backed project entry, do not start with broad repository search
when the utterance contains a known project command or primary mode switch.

Entry surfaces come first:

```text
1. README.md
2. AGENTS.md
3. .ai/runtime/project_instance/boot_command_entry.md
4. .ai/runtime/state/session.md
5. .ai/runtime/state/current_anchor_frame.md
```

Only search broadly after entry surfaces fail to resolve the command.

Do not use previous context, search snippets, role labels, or mode labels as
the first routing source.

Role label from conversation is not Role Authority.

Mode label from conversation is not Mode Authority.

Previous context cannot grant authority.

Connector search result cannot grant authority.

### Project Primary Mode Switches

Each attached project must define direct entries for its primary user-facing
mode switch phrases.

Required shape:

```text
<project primary mode phrase>
  -> treat as a project primary MODE_SWITCH command
  -> read the project runtime state / current anchor frame
  -> resolve Node / Mode first
  -> resolve Role and Scope from Mode
  -> keep Authority separate
  -> keep Execution Assignment UNASSIGNED unless a scoped task is approved
  -> report READY with source-backed facts only
```

GCS example:

```text
마스터모드 / MASTER / MASTER mode / GCS MASTER
  -> Node: GCS
  -> Mode: MASTER
  -> Role: MASTER
  -> Mode Scope: architecture/governance
  -> Authority: UNASSIGNED
  -> Execution Assignment: UNASSIGNED
```

Mode switch aliases are command entry aliases.

They are not file modification approval, commit approval, PR approval, or
execution authority.

Mode switch aliases must not be reported as ON / OFF toggles.

They select runtime coordinates.

Required READY report shape:

```text
Node: <project-node>
Mode: <selected-mode>
Role: <resolved-role>
Mode Scope: <resolved-scope>
Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
State: READY
```

Anchor/session surface reporting rule:

```text
When reporting anchor/session surface fields, read:
- .ai/runtime/state/current_anchor_frame.md for current values
- .ai/runtime/project_instance/runtime_anchor_frame.md for field definitions

Do not explain session_location, commander_surface, execution_surface,
repository_location, execution Host binding, or write target capability from
inference alone. A mobile/web Commander may be current while its execution
Host and write target remain UNKNOWN, or it may have a separately evidenced
binding to a local, Sandbox, or MCP executor.
```

### BOOT / 부트

BOOT / 부트
  -> read the project entry documents
  -> follow the project Boot Manager / session alignment procedure
  -> propose or confirm Node / Mode when required
  -> resolve Role and Scope from Mode
  -> report READY with facts only

### REBOOT / 리부트

REBOOT / 리부트
  -> release current role/task assumptions
  -> clear temporary task context
  -> reread the project boot entry
  -> follow the project Boot Manager / session alignment procedure
  -> report READY with facts only

### STATUS / 상태

STATUS / 상태
  -> report source-backed facts only
  -> unknown fields must be marked UNKNOWN

### MEMORY_SYNC / 메모싱크

MEMORY_SYNC / 메모싱크
  -> extract memory candidates
  -> show Candidate List
  -> require User Selection
  -> do not write directly

### @GitHub MEMORY_SYNC / @GitHub 메모싱크

@GitHub MEMORY_SYNC / @GitHub 메모싱크
  -> extract memory candidates
  -> show Candidate List
  -> require User Selection
  -> require provider write capability and an approved Runtime-owned append path
  -> persist selected candidates only as HANDOFF_APPEND with provider evidence
  -> if provider write is unavailable: PREPARED / HANDOFF_APPEND_UNAVAILABLE

## Fallback

If a short command is not understood directly, use the explicit instruction:

Read the project entry documents and perform the requested boot command.
```

## Non-Goals

This template does not define project business behavior.

It only maps short global commands to the project's boot/session entry procedure.
