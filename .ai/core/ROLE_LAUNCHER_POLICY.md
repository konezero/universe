# Role Launcher Policy

Status: adopted-candidate
Created: 2026-06-30
Repository: `konezero/ai-career`
Scope: reusable `.ai` boot / role-selection policy

## Purpose

Role Launcher Policy defines the minimal user-facing boot surface before an AI session receives delegated role authority.

The launcher is not a task picker, work queue, or execution approval screen.

It presents the available Role choices, waits for the User to select one, and only then loads the matching role profile.

## Core Rule

```text
Boot Manager presents Role choices only.
Boot Manager does not infer authority.
Boot Manager does not display Work Queue.
Boot Manager does not use Node selection.
Boot Manager waits for User selection.
```

## Launcher UI

```text
GCS Boot Manager

Repository : konezero/gcs
Boot State : READY
Execution  : UNASSIGNED

────────────────────────

1. MASTER
   프로젝트 운영 및 작업 조율

2. DESIGN
   설계 및 아키텍처

3. IMPLEMENTATION
   코드 구현 및 테스트

4. DEBUG
   오류 분석 및 원인 추적

────────────────────────

Role을 선택하세요.

예)
1
2
3
4
```

## Role Set

The standard launcher Role set is:

| Role | User-facing meaning |
| --- | --- |
| `MASTER` | 프로젝트 운영 및 작업 조율 |
| `DESIGN` | 설계 및 아키텍처 |
| `IMPLEMENTATION` | 코드 구현 및 테스트 |
| `DEBUG` | 오류 분석 및 원인 추적 |

## Non-Goals

The launcher must not show these as first-class user choices:

- Node
- Work Queue
- internal responsibility labels
- implementation file scopes
- inferred task candidates
- hidden execution plans

## Role vs Responsibility

Only the four launcher Roles are user-facing boot choices.

Internal responsibilities such as memory keeping, documentation upkeep, review support, checkpoint handling, or governance checks may be mapped behind a selected Role, but they are not standalone Role choices in the launcher.

Examples:

```text
Project Memory Keeper
  -> responsibility
  -> not a launcher Role

Documentation maintenance
  -> responsibility or task type
  -> not a launcher Role unless mapped under MASTER or DESIGN by project policy
```

## Node Rule

`Node` is not part of the Role Launcher UI.

A project may still internally use topic, domain, directory, or load-scope hints after Role selection, but the user-facing launcher must not require Node selection.

```text
User selects Role.
Project policy may derive scope from the user request.
```

## Work Queue Rule

`Work Queue` is not part of the Role Launcher UI.

Work Queue may appear after Role selection if the selected Role and current request require task prioritization.

It must not appear before Role delegation.

## Boot State Boundary

Role selection and execution approval remain separate.

```text
Boot State = READY
Execution Assignment = UNASSIGNED
```

After the User selects a Role:

```text
Role Selected
  ↓
Load Role Profile
  ↓
Apply Load Scope / Write Scope
  ↓
READY
  ↓
Wait for explicit task execution request when needed
```

Role selection alone does not approve file modification, merge, deployment, or task execution.

## Project Binding

This policy was first crystallized through GCS boot discussion, but it is stored in `ai-career` because the Role Launcher is reusable across project `.ai` workspaces.

Projects may customize the displayed repository name and the one-line Role descriptions, but they should preserve the same minimal launcher shape unless a project governance profile explicitly overrides it.

## Safety

Do not store secrets.

Do not store private chain-of-thought.

Do not treat Role selection as execution approval.

Do not infer authority when the launcher is awaiting user selection.

## One-Line Summary

Role Launcher shows only four Roles with one-line descriptions, waits for User selection, and keeps execution unassigned until a scoped task is explicitly approved.
