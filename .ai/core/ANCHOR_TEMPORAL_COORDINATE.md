# Anchor Temporal Coordinate

Status: core runtime contract
Scope: ai-career / attached project runtime
Layer: Session Currentness / Runtime Anchor Frame / Beyond Recall
Parent: `.ai/core/SESSION_CURRENTNESS.md`, `.ai/core/RUNTIME_STATE_TRUST_GATE.md`
Created: 2026-07-16
Source validation: GCS process-local SQLite proof, 9 deterministic tests

## Purpose

Anchor Temporal Coordinate defines how a Current Anchor advances with physical
Runtime time while preserving past Anchors as immutable recall footprints.

It separates four meanings that must not be collapsed:

```text
Current Anchor  -> the live execution coordinate
Beyond Anchor   -> a past coordinate retained for candidate recall
Archive / Git   -> evidence used to verify a candidate
Resume          -> conversation restoration, not Anchor reactivation
```

## Core Declaration

```text
THE CURRENT ANCHOR ALWAYS MOVES FORWARD.

EVERY OBSERVED USER INPUT ADVANCES THE CURRENT ANCHOR'S PHYSICAL OBSERVATION TIME.

SAME-ANCHOR OBSERVATION CHANGES observed_at ONLY.

TIME PASSAGE ALONE DOES NOT CREATE STALE.

REPLACED ANCHORS REMAIN BEYOND FOOTPRINTS.

BEYOND RECALL RETURNS A CANDIDATE, NEVER A REACTIVATED OLD ANCHOR.

ADOPTION CREATES A NEW CURRENT ANCHOR AT CURRENT PHYSICAL TIME.

BEYOND ADOPTION REQUIRES A CURRENT SOURCE AND VALIDATION BASIS.
HISTORICAL SOURCE IDENTITY MUST NOT BE COPIED INTO THE NEW CURRENT ANCHOR.

ANCHOR TIME DOES NOT CREATE AUTHORITY, ASSIGNMENT, WRITE PERMISSION, OR ADOPTION.
```

## Temporal Fields

The minimum temporal coordinate is:

```yaml
anchor_temporal_coordinate:
  entered_at: <anchor-creation-time>
  observed_at: <last-current-anchor-input-or-runtime-observation-time>
  state_updated_at: <last-semantic-state-transition-time>
  validated_at: <last-source-backed-validation-time-or-empty>
```

Field meanings:

```text
entered_at
  -> when this Anchor became a new coordinate

observed_at
  -> the latest Host physical time at which the same Current Anchor was observed

state_updated_at
  -> when the Anchor's semantic state or lifecycle last changed

validated_at
  -> when source-backed validation last completed
```

These fields are independent. A user input normally changes only
`observed_at`.

## User Input Observation

`USER_INPUT_OBSERVED` means the Host received the user's utterance and recorded
Host physical time before using the utterance for time-relative interpretation.
It does not mean that the Runtime approved, adopted, or semantically accepted
the request.

Required flow:

```text
user input arrives
  -> input_at = Host physical Runtime time
  -> read previous observed_at
  -> compare elapsed physical time
  -> verify current session_id + frame_id + anchor_id + source coordinate
  -> update observed_at only when the same Current Anchor remains valid
```

Same-Anchor observation MUST NOT change:

```text
anchor_id
state
state_updated_at
validated_at
source_ref / source_commit
authority / authority_ref
execution_assignment / assignment_ref
checkpoint_ref
```

The Host must reject a timestamp that would move `observed_at` backward.

## Forward-Only Anchor Rule

A semantic transition creates a new Anchor coordinate.

```text
Current Anchor A
  -> topic transition / explicit realignment / adopted recall
  -> Anchor A becomes a Beyond footprint
  -> new Current Anchor B is created at current physical time
```

The old coordinate remains evidence. It must not receive a later Current touch,
become Current by direct mutation, or inherit new authority.

## Currentness Results

Physical age is evidence, not a verdict.

```text
CURRENT
  -> current identity and source coordinates still match

STALE
  -> source-backed replacement, supersession, or explicit obsolescence proves
     that the Anchor is no longer the Current execution coordinate

RECHECK_REQUIRED
  -> session, frame, source, writer, validation, or source-supplied freshness
     evidence requires another check

UNKNOWN
  -> required evidence is missing, conflicting, unverifiable, or time regressed
```

Rules:

```text
elapsed wall-clock time alone -> no STALE transition
source-supplied stale_after elapsed -> RECHECK_REQUIRED, not STALE
session/frame/source mismatch -> RECHECK_REQUIRED
explicit Current Anchor replacement -> old Anchor STALE as execution coordinate
```

`STALE` does not erase an Anchor. It only blocks use of that old coordinate for
current execution.

## Beyond Recall

A time-relative request such as "the thing we discussed yesterday" starts from
the current input's Host physical time.

```text
current input_at
  -> derive a bounded time window
  -> query Beyond Anchor footprints
  -> resolve Archive / Git evidence when available
  -> return CANDIDATE or UNKNOWN
  -> Parent / User adoption
  -> create a new Current Anchor at input_at
```

The recalled Anchor remains unchanged. The new Anchor may reference it through
`recalled_from_anchor_id` or an equivalent evidence pointer.

Before adoption, Runtime must derive a current interpretation basis from the
active source, validation, session, and Host-time evidence. This basis is a
transient interpretation input, not an Authority source or durable state
object.

```text
Beyond candidate from source A
  + current source / validation basis B
  + Parent or User adoption evidence
  -> new Current Anchor interpreted under basis B
  -> recalled_from_anchor_id points to the source A footprint
  -> source A footprint remains byte-for-byte unchanged
```

The new Anchor may carry a newly derived context reference for the recalled
idea, but it must use the current source and validation identity. It must not
copy the old Anchor's session identity, source identity, validation status,
Authority, or Execution Assignment.

Missing, `UNKNOWN`, stale, or mismatched current-basis evidence must fail
without changing the current Anchor, the Beyond footprint, or repository state.

Resume may restore enough conversation for the user and model to understand a
candidate. Resume is not source verification and must not reactivate the old
Anchor automatically.

## Event Ordering Boundary

`event_seq` and logical revision counters are not public Anchor currentness
requirements.

A storage implementation may keep an internal append order for integrity or
debugging. That internal order:

```text
does not define currentness
does not replace Host physical time
does not create authority
does not become a required project schema field
```

## Host Adapter Requirement

When process-local Anchor memory is available, the Host adapter SHOULD expose a
bounded input-observation call that:

```text
uses Host physical time rather than LLM-supplied time
verifies session_id + frame_id + anchor_id
updates observed_at only
returns previous_observed_at and elapsed_seconds as evidence
does not persist user content
does not create authority or assignment
```

If that capability is unavailable, the model must not claim that the temporal
coordinate was updated. The observation result remains `UNKNOWN`.

## Template Requirement

Project runtime frame templates that expose a Current Anchor should include:

```text
entered_at
observed_at
state_updated_at
validated_at
```

An implementation may use Markdown, JSON, YAML, SQLite, process memory, or
another project-local store. The field meanings and forward-only behavior are
the contract; the storage backend is not.

## OS_UPDATE / OS_VALIDATE

When a project implements Anchor temporal coordinates, validation should check:

```text
Host physical time is used for input observation
same-Anchor input changes observed_at only
observation time cannot move backward
time passage alone does not create STALE
replacement leaves a Beyond footprint
recall remains CANDIDATE until adoption
adoption creates a new Current Anchor
adoption requires current source and validation evidence
new Anchor does not copy the historical source identity
failed adoption leaves Current and Beyond records unchanged
temporal fields do not create authority or assignment
```

Missing implementation evidence should produce `PARTIAL` or `UNKNOWN`, not an
inferred `PASS`.

## Reference Runtime Storage Boundary

The promoted proof uses SQLite `:memory:` as a deterministic interpreter. It
proves behavior only.

The installed Reference Runtime persists each project Mode's Current Anchor
and retired Beyond Anchor footprints in a project-local SQLite file. This is a
coordinate store, not a Runtime Image or authority store. It may preserve raw
anchor snapshots and candidate history across executor restarts, but it does
not establish executable Runtime readiness, repository verification, execution
permission, Archive evidence, or Resume adoption.

This Reference Runtime choice does not require SQLite for every Host. Other
Hosts may provide an equivalent project-local implementation while preserving
the temporal fields, replacement footprint, and non-authority boundary above.
