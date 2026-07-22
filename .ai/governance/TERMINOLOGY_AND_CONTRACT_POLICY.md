# Terminology and Contract Policy

Status: active governance policy
Repository: `konezero/ai-career`
Scope: naming rules for governance, runtime, project, memory, resume, and source-of-truth documents

## Purpose

ai-career must use stable terms so the Runtime does not drift when the conversation context changes.

This document defines the preferred vocabulary for governance-level contracts, runtime specifications, decision records, and source-of-truth state.

## Core Rule

```text
Name the layer by responsibility.
Do not overload one word across Governance, Runtime, Project, Memory, and Resume.
```

## Semantic Conservation

Repository, Runtime, project, and generated surfaces must preserve the meaning
of canonical concepts.

```text
1. Do not add a concept whose responsibility is already owned by a canonical
   concept.
2. Add a concept only when the existing schema cannot express the requirement
   and the new concept has a defined producer, consumer, authority boundary,
   lifecycle, and persistence boundary.
3. Do not make a field a required precondition when no defined producer can
   supply it.
4. Do not expand domain meaning to compensate for an implementation, transport,
   Host, vendor, or storage limitation.
5. Treat an unapproved concept as a candidate. It must not become an active
   contract, required field, status, coordinate, or execution gate.
6. Promoting a new concept requires an explicit contract change, source-backed
   registration, validation coverage, and User approval.
7. When two concepts own the same responsibility, retain the canonical concept
   and deprecate the duplicate through an explicit compatibility path.
```

Implementation detail does not create vocabulary authority.

```text
NEW STORAGE DOES NOT CREATE NEW DOMAIN MEANING.
NEW TRANSPORT DOES NOT CREATE NEW DOMAIN MEANING.
NEW HOST EVIDENCE DOES NOT CREATE NEW DOMAIN MEANING.
```

## Command And Internal Operation Boundary

A user-facing command and an internal implementation operation are different
layers.

```text
Canonical user-facing command
  -> names the lifecycle intent shown to the user

Internal operation
  -> names the deterministic adapter or executor action
  -> may appear in approval payloads and raw evidence
  -> must not be promoted into a second user command
```

Conversation headings, proposals, approval prompts, progress reports,
completion messages, and suggested next commands must retain the canonical
user-facing command. Internal operation labels may be included as technical
detail only.

For Project Runtime reconciliation:

```text
user-facing command: OS_UPDATE
internal operation: RUNTIME_UPDATE
```

Core and Runtime responsibility boundaries do not create separate update
commands.

## Primary Layer Terms

```text
Governance
  -> invariant contracts, vocabulary, authority boundaries

Runtime
  -> implementation of lifecycle, resolution, execution, normalization

Project
  -> project-local instances, snapshots, business logic

Resume
  -> role-scoped restore surface

Memory
  -> candidate knowledge, observations, hypotheses, adopted notes
```

## Document Type Terms

### Contract

Use `Contract` for invariant behavior promises or command meanings.

Examples:

```text
Command Contract
Global Command Contract
Memory Sync Contract
Reboot Contract
```

Preferred use:

```text
Governance defines contracts.
Runtime implements contracts.
Projects consume contracts.
```

### Specification / Spec

Use `Specification` or `Spec` for implementation-facing requirements or runtime behavior descriptions.

Examples:

```text
Runtime Specification
Connector Manifest Specification
Project Instance Specification
```

Preferred use:

```text
Spec says how something must behave or be shaped.
```

### Policy

Use `Policy` for rules, boundaries, permissions, and allowed/forbidden behavior.

Examples:

```text
Command Governance Policy
Runtime Status Source Rule
Memory Hardpoint Policy
```

Preferred use:

```text
Policy constrains behavior.
```

### Protocol

Use `Protocol` for ordered lifecycle flows.

Examples:

```text
Boot Protocol
Reboot Protocol
Memory Sync Protocol
Checkpoint Protocol
```

Preferred use:

```text
Protocol defines sequence.
```

### ADR / Decision Record

Use `ADR` or `Decision Record` for why a design decision was made.

Examples:

```text
Decision Record: Global Command belongs to Governance
Decision Record: Mode change maps to Reboot
```

Preferred use:

```text
ADR records context, decision, and consequence.
```

### Source of Truth / SSOT

Use `Source of Truth` or `SSOT` for the authoritative location of a state, fact, or contract.

Examples:

```text
.ai/memory is the Git-backed memory source of truth.
Runtime state file is the runtime status source of truth when implemented.
Governance policy is the command contract source of truth.
```

Preferred use:

```text
If multiple copies exist, identify which one is authoritative.
```

## ai-career Mapping

```text
Governance
  -> Contract + Policy

Core Runtime
  -> Specification + Protocol + Policy

Resume / Checkpoint
  -> Role-scoped restore surface + Decision Records + state pointers

Memory
  -> Git-backed candidate/adopted knowledge, not unresolved chat drift

Runtime State
  -> Source of Truth for live status when implemented
```

## Contract Reading Rule

When answering questions about a Contract, Policy, Specification, or Protocol:

```text
1. Deep-read the relevant document.
2. Do not infer missing terms.
3. If the document does not define the term, answer UNKNOWN / not found.
4. If the document conflicts with conversational memory, follow the document.
5. Summarize only after reading the relevant contract context.
```

Short form:

```text
Read contract first.
Reason later.
```

## Anti-Pattern

Bad:

```text
User asks about Resume.
Assistant invents `Deep Resume` because nearby text mentions deeper continuity.
```

Correct:

```text
Search contract documents.
Read the relevant Resume / Checkpoint files.
Answer only with terms found in the documents.
If a term is not found, say it is not defined.
```

## Current Preferred Terms

```text
Global Command Contract
Command Governance Policy
Global Command Runtime Rule
Context Management Runtime
Runtime Specification
Project Instance
Role-scoped Resume
Checkpoint Lifecycle
Memory Hardpoint
Source-backed Status
```

## Research Notes

The naming choice aligns with common software terms:

```text
Contract
  -> interface/behavior promises

Specification
  -> technical requirements / shape / expected behavior

ADR / Decision Record
  -> architectural decision context and consequence

SSOT / Source of Truth
  -> one authoritative source for a state or fact
```

## Status

Active governance policy.

This policy should be loaded before naming new governance/runtime documents.
