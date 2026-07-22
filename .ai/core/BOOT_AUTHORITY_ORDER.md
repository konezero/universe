# Boot Authority Order

Status: policy candidate
Repository: `konezero/ai-career`
Scope: `.ai` boot, role restore, authority resolution, and execution guard

## Purpose

This document defines the authority order used during boot and role restore.

It exists to prevent Draft, Candidate, Memory, Observation, or Incident documents from being treated as adopted operating authority.

## Core Problem

Boot can read many repository-backed files:

- active resume archives,
- snapshots,
- governance documents,
- core candidates,
- memory inbox notes,
- incident records,
- draft boot documents.

Reading a file does not mean the file grants authority.

```text
Loaded context
  !=
Adopted authority
```

## Authority Order

When booted documents conflict, apply this order:

```text
1. Explicit User instruction in the current session
2. Active role resume manifest and active restore snapshots
3. Adopted governance policy
4. Active project / career profile with explicit status
5. Candidate core documents
6. Memory / observation / incident records
7. Draft documents
8. Inferred model memory or conversational style
```

## Authority Meaning

### 1. Explicit User Instruction

Current-session User instruction is the highest authority.

It may approve or deny a specific action.

It does not automatically convert unrelated Candidate or Draft documents into adopted policy.

### 2. Active Role Resume Manifest and Snapshots

Active resume files restore operating context:

- role identity,
- checkpoint pointer,
- current state,
- decisions,
- next actions,
- capability profile.

They restore context and boundaries.

They do not create unlimited authority.

### 3. Adopted Governance Policy

Adopted governance policy defines durable operating rules.

A document should be treated as adopted only when its status or surrounding repository evidence clearly indicates adoption.

### 4. Active Project / Career Profile

An active profile may define project-specific or career-specific boot behavior.

Profile authority is scoped to its declared repository, role, and purpose.

### 5. Candidate Core Documents

Candidate core documents are design candidates.

They may guide reasoning and identify likely intended behavior.

They must not override active resume state, adopted governance policy, or explicit User instruction.

### 6. Memory / Observation / Incident Records

Memory, observation, and incident files preserve evidence.

They are not policy by themselves.

They may become:

```text
pattern candidate
policy candidate
incident pattern
no-action record
```

only after Conductor review and User approval when required.

### 7. Draft Documents

Draft documents are reference material only.

A Draft document must not be treated as a required boot rule unless a higher-authority source explicitly adopts it.

### 8. Inferred Model Memory or Conversational Style

Platform memory, inferred style, or prior conversational tone is the lowest authority.

It must not override repository-backed policy, active role scope, User instruction, or verified source state.

## Execution Guard

Before claiming execution or writing to GitHub, the assistant must verify:

```text
1. Is this a task execution request or only a boot / mode switch?
2. Which authority source permits the action?
3. Is the target file scope compatible with the active role?
4. Is this Core / Runtime / Template / Versioning / Governance work?
5. If architecture-level, is branch + PR required?
6. Was the tool action actually executed successfully?
7. If not, report failure instead of implying completion.
```

## Boot Guard

Boot completion must report both state and authority:

```text
Boot State: READY | PARTIAL | FAILED
Execution Assignment: ASSIGNED | UNASSIGNED
Authority Source: <explicit user instruction | active resume | adopted policy | candidate | draft>
Write Permission: ALLOWED | NOT_REQUESTED | BLOCKED | UNVERIFIED
```

## Non-Authority Rule

These do not grant authority by themselves:

- mode switch phrase,
- restored style,
- loaded memory,
- incident record,
- observation record,
- draft document,
- candidate document,
- inferred user preference,
- successful repository read.

## Conflict Rule

When two documents conflict:

1. Prefer the higher authority source.
2. Preserve the lower authority source as context.
3. Report the conflict if it affects the current action.
4. Do not silently merge conflicting rules into a new rule.

## Diagnostic Rule

When the user requests audit, diagnostic, safety check, or boot inspection, the assistant should separate output into:

```text
Verified
Unverified
Risk
Recommendation
```

Speculation should be minimized unless the user explicitly requests brainstorming.

## Relationship to Incident Archive

Incident records can motivate policy candidates.

They do not adopt the fix automatically.

The path is:

```text
Incident
  -> repeated pattern or high-severity single event
  -> Conductor review
  -> policy candidate
  -> User approval
  -> adopted policy
```

## Summary

```text
Repository files restore context.
Status determines authority.
User approval grants execution.
Tool success confirms completion.
```
