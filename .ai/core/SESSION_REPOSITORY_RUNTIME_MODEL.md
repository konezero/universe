# Session Repository Runtime Model

Status: Candidate Core Architecture
Scope: ai-career / runtime install / session attach
Layer: Runtime Host / Repository Target
Parent: `.ai/core/PROJECT_RUNTIME_INSTALL.md`
Created: 2026-07-02

## Purpose

Session Repository Runtime Model defines the relationship between the current AI session and the repository-backed runtime.

It clarifies that mobile, browser, chat, and sandbox environments are not durable install targets, but may host the active Session Runtime.

The durable target is the Repository Runtime.

## Core Declaration

```text
SESSION RUNTIME IS THE HOST.
REPOSITORY RUNTIME IS THE TARGET.

SANDBOX IS AN EXECUTION ENVIRONMENT, NOT DURABLE STORAGE.

OS_INSTALL CREATES THE DURABLE REPOSITORY RUNTIME.
BOOT CREATES THE DISPOSABLE SESSION RUNTIME.
SESSION_ATTACH CONNECTS THEM WITHOUT INSTALLING FILES.
```

## Model

```text
Chat / Mobile / Browser / Sandbox / Local Tool
  -> Session Runtime
  -> Session Attach / Boot
  -> Repository Runtime
```

Meaning:

```text
Session Runtime
  -> live AI execution context
  -> temporary
  -> may run in chat, browser, mobile, sandbox, or local tool

Repository Runtime
  -> durable source-backed runtime state
  -> stored in repository files
  -> can outlive any single session
```

## Host vs Target

```text
Host
  -> where the session is currently executing

Target
  -> where durable runtime state is stored
```

A sandbox may be a host.

A sandbox is not the durable Repository Runtime target unless the user explicitly asks for temporary sandbox testing and the result is reported as temporary.

A phone may host the chat session.

A phone is not the install target.

A GitHub repository may be the install target when source-backed read/write capability exists and the user approves the proposal.

## Durable Install and Session Boot

`OS_INSTALL` creates the durable Project Runtime. `BOOT` consumes that Runtime
to create a disposable Session Runtime.

```text
OS_INSTALL
  -> explicit durable target
  -> immutable source
  -> scan and proposal
  -> user approval
  -> Project Runtime assembly
  -> validation
  -> READY_FOR_BOOT

Mode Selection
  -> BOOT
  -> Session Runtime starts
  -> fresh Current Anchor
```

If the repository already has a valid Runtime at the requested source commit,
`OS_INSTALL` reports `ALREADY_INSTALLED` without mutation. The next session
action is `BOOT`, not another install.

If no valid runtime surface exists, OS_INSTALL proposes the minimal surfaces required before writing.

## Resolve Before Assemble

Source and durable target resolution come before assembly.

```text
OS_INSTALL
  -> resolve immutable source
  -> resolve explicit durable target
  -> decide whether Project Runtime exists
  -> assemble only if FRESH and approved
```

Source-only sessions may still use `SOURCE_ATTACH` and bounded read-only Boot
evidence. They must not report a durable install or Project Runtime verification.

## Status Vocabulary

```text
SESSION_READY
  -> session runtime is active

REPOSITORY_TARGET_DETECTED
  -> possible durable target identified

SESSION_ATTACH_READY
  -> a running Runtime or source-backed Boot input is available

REPOSITORY_WRITE_AVAILABLE
  -> writes are technically possible

REPOSITORY_RUNTIME_ABSENT
  -> no durable runtime surface detected

REPOSITORY_RUNTIME_PARTIAL
  -> claims or registry exist, but required fetched surfaces are missing

REPOSITORY_RUNTIME_VERIFIED
  -> fetched surfaces and validation evidence pass

APPROVAL_REQUIRED
  -> proposal exists but user has not approved repository writes
```

## Mobile / Browser / Chat

When OS_INSTALL is requested from mobile, browser, or chat:

```text
Report the Host separately from the durable target.
Require an explicit durable target and write-capable execution path.
Fetch repository sources when possible.
Produce an install proposal when the target can be mutated.
Otherwise report INSTALL_EXECUTOR_UNAVAILABLE or TARGET_REQUIRED.
Do not reinterpret source attachment as OS_INSTALL success.
```

Expected response shape:

```text
Host: chat / mobile / browser / sandbox
Durable target: <owner/repo-or-local-path> or UNKNOWN
Repository runtime: absent / partial / verified / unknown
Install executor: available / unavailable / unknown
Next step: target confirmation / proposal / approval / validate
```

## Sandbox

Sandbox is an execution host.

It may hold temporary files, experiments, or generated artifacts.

It is not durable runtime storage unless explicitly scoped as temporary.

```text
Sandbox install
  -> temporary test only
  -> not INSTALL_COMPLETE for repository runtime
```

## Relationship to Install UX

```text
SESSION_REPOSITORY_RUNTIME_MODEL.md
  -> defines host/target and attach model

RUNTIME_INSTALL_UX.md
  -> explains how the model is shown to the user

PROJECT_RUNTIME_INSTALL.md
  -> defines install phases and safety gates

RUNTIME_SOURCE_VERIFICATION.md
  -> defines fetch as current truth
```

## Anti-Patterns

Avoid:

```text
- Treating chat/mobile/sandbox as the durable install target.
- Saying install is impossible only because the user is on mobile.
- Saying install is complete because the Session Runtime is active.
- Creating repository runtime files before source/target resolution, proposal, and approval.
- Confusing Session Runtime readiness with Repository Runtime verification.
```

Prefer:

```text
- BOOT created the Session Runtime.
- Repository Runtime is durable target state.
- Resolve immutable source and durable target before assembly.
- Fetch before status or proposal.
- Assemble only if needed and approved.
- Sync validation evidence back to repository.
```

## Placement Test

A concept belongs here when it answers:

```text
Where is the current AI session running?
Where should durable runtime state live?
Where does OS_INSTALL write the durable Project Runtime?
What may SESSION_ATTACH connect without writing?
How do mobile, chat, browser, sandbox, local, and GitHub relate?
```

If it answers what to write, place it in `PROJECT_RUNTIME_INSTALL.md`.

If it answers how to ask for approval, place it in `RUNTIME_INSTALL_UX.md`.

If it answers what source is current truth, place it in `RUNTIME_SOURCE_VERIFICATION.md`.

## Adoption Status

This is a candidate Core architecture document.

It should be tested by running OS_INSTALL from mobile/chat and checking that the
runtime requires a durable target and capable install executor, while
SOURCE_ATTACH or BOOT reports the session Host separately without an install
claim.
