# Runtime Status Source Rule

Status: core runtime candidate
Repository: `konezero/ai-career`
Scope: task runtime observability / status reporting

## Purpose

Runtime status must not be invented from conversational confidence.

A status report must distinguish between confirmed state, inferred state, and unknown state.

This rule was added after a Conductor session displayed synthetic runtime status such as `Scheduler READY`, `Manifest LOADED`, and `Summary Cache FLUSHED` without reading an actual runtime state source.

This rule now also requires OS_STATUS to distinguish session runtime readiness from repository runtime verification.

## Core Rule

```text
Runtime status requires a source.
No source -> UNKNOWN.

SESSION_RUNTIME_READY is not REPOSITORY_RUNTIME_VERIFIED.
```

## OS_STATUS Split

OS_STATUS must report two separate surfaces when applicable:

```text
Session Runtime
  -> whether the current AI session has ai-career Runtime Governance active

Repository Runtime
  -> whether an attached repository/project runtime surface exists and passes source-backed validation
```

Do not collapse these into one install result.

```text
INSTALL_COMPLETE
  != SESSION_RUNTIME_READY + REPOSITORY_RUNTIME_VERIFIED unless both are separately evidenced
```

## Source-Only OS_STATUS Baseline

When the available Host can read one immutable repository source but has no
raw executable Runtime, Anchor store, restore execution, or current validation
result, `OS_STATUS` must stop at this baseline:

```yaml
status: SOURCE_READY
checkpoint:
  status: OBSERVED_REFERENCE | NOT_OBSERVED
resume_restore:
  status: NOT_PERFORMED
validation:
  status: NOT_RUN
mode_current_anchor: UNKNOWN
session_runtime: UNKNOWN
repository_runtime: UNKNOWN
executable_runtime_currentness: UNKNOWN
authority: UNASSIGNED
execution_assignment: UNASSIGNED
repository_write: false
```

Checkpoint, Resume Archive, validation, Runtime Image, and status documents
read from the repository are observed references. Their text may describe a
past successful run, but source observation does not replay that run or make
its state current.

```text
checkpoint document observed  != checkpoint active
Resume Archive observed       != Resume restore performed
validation file observed      != validation run at the selected source commit
Runtime Image document read   != Runtime Image active
Core gate document read       != gate execution observed
```

The deterministic Reference Runtime operation is:

```text
os-status source-only --request <json>
```

It accepts the immutable source coordinate and observed reference paths. It
does not accept caller-supplied active-state claims.

## Status Source Classes

```text
CONFIRMED
  -> read from a tool, connector, repository file, runtime state file, or explicit platform result

INFERRED
  -> derived from current conversation behavior, but not directly read from a source

UNKNOWN
  -> no reliable source is available
```

`CONFIRMED` applies only to the fact directly observed. Reading a repository
file confirms that file and its bytes at the selected source coordinate. It
does not confirm that a historical state named inside the file is active now.

## Required Status Shape

A runtime status entry should include:

```yaml
status_entry:
  name: scheduler
  value: ready
  source_type: confirmed | inferred | unknown
  source: .ai/runtime/state/session.md
  updated_at: <timestamp-or-null>
```

If no source exists:

```yaml
status_entry:
  name: scheduler
  value: unknown
  source_type: unknown
  source: null
  updated_at: null
```

## Required OS_STATUS Shape

OS_STATUS should prefer this top-level split:

```yaml
os_status:
  session_runtime:
    state: READY | PARTIAL | NOT_READY | UNKNOWN
    source_type: confirmed | inferred | unknown
    source: <session-visible evidence or null>
    notes: []

  repository_runtime:
    state: VERIFIED | PARTIAL | NOT_VERIFIED | NOT_ATTACHED | UNKNOWN
    repository: <owner/repo or null>
    runtime_root: <path-or-null>
    validation_evidence: <path-or-null>
    source_type: confirmed | inferred | unknown
    notes: []
```

Allowed interpretation:

```text
SESSION_RUNTIME_READY
  -> current session is operating under the ai-career runtime governance contract

REPOSITORY_RUNTIME_VERIFIED
  -> fetched repository files and validation evidence prove a project runtime surface exists and is compatible

NOT_ATTACHED
  -> no repository/project was attached or requested for the status operation

UNKNOWN
  -> no reliable source exists for that field
```

## Display Rule

A status display must not present inferred or unknown runtime state as confirmed.

Bad:

```text
Scheduler READY
Manifest LOADED
Summary Cache FLUSHED
Repository Runtime VERIFIED
```

unless those values were actually read from a source.

Good:

```text
Session Runtime: UNKNOWN (no current Host Runtime evidence)
Repository Runtime: NOT_ATTACHED (no repository requested)
Scheduler: UNKNOWN (no runtime state source)
Manifest: UNKNOWN (no runtime state source)
Last GitHub result: CONFIRMED (connector returned PR #152 merged)
```

When a repository is attached:

```text
Session Runtime: INFERRED READY (runtime governance contract active in session)
Repository Runtime: NOT_VERIFIED (expected validation file fetch returned 404)
Registry Claim: CONFIRMED INSTALLED (registry file fetched)
Proof: FAIL / PARTIAL because registry is claim, not validation evidence
```

## Runtime State File

Runtime status should be read from the project-local state file:

```text
.ai/runtime/state/session.md
```

Candidate shape:

```yaml
session:
  role: conductor
  source: session_boot
  runtime_governance:
    state: ready
    source: SESSION_RUNTIME_GOVERNANCE.md

task_queue:
  active: null
  ready: []
  blocked: []
  source: runtime_state

last_result:
  task_id: github-open-pr-check
  state: complete
  pop: true
  source: connector_result
```

## Repository Runtime Evidence Candidate

When a repository/project is attached, OS_STATUS should fetch expected repository runtime surfaces before claiming verification.

Candidate evidence fields:

```yaml
repository_runtime:
  repository: konezero/ai-career
  registry_claim:
    path: .ai/registry/project_runtimes.md
    state: INSTALLED | PARTIAL | UNKNOWN
    source_type: confirmed
  runtime_root:
    path: .ai/runtime/project_instance/
    source_type: confirmed | unknown
  validation:
    latest: .ai/runtime/project_instance/validation/latest.md
    history: .ai/runtime/project_instance/validation/history.md
    source_type: confirmed | unknown
  distribution:
    installed_manifest: .ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json
    source_commit: <immutable-commit-or-UNKNOWN>
    required_hashes: MATCH | MISMATCH | MISSING | UNKNOWN
    source_type: confirmed | unknown
  result: VERIFIED | PARTIAL | NOT_VERIFIED | UNKNOWN
```

Registry claims are not proof by themselves.

```text
Registry is claim / index.
Fetch + validation evidence is proof.
```

For a standalone project-local Runtime, validation evidence is complete only
when the installed distribution manifest and all required managed-surface
hashes were checked. Project-instance structure evidence alone may report its
own PASS, but repository-runtime status remains `PARTIAL`.

Even when the repository Runtime is `VERIFIED`, a fresh install reports Session
Runtime `UNKNOWN`, Session Initialization `UNINITIALIZED`, and Currentness
`UNKNOWN` until a later BOOT or attach produces session-scoped evidence. Install
Host metadata is not session evidence.

Status must report installation authority separately from live session
authority. `OS_INSTALL` and its internal installer operation keep
`installation_authority: UNASSIGNED`.
A later session may report a different `authority` or `execution_assignment`
only when the current session/frame agree and their evidence references exist.

## Relation to Task Normalizer

The Task Normalizer can produce confirmed status for the task it just normalized.

Example:

```yaml
task_result:
  task_id: github-open-pr-check
  state: complete
  result_type: no_open_prs
  pop: true
  source_type: confirmed
  source: github.search_prs
```

But the normalizer must not claim global runtime state unless that state is explicitly read or updated.

## Conductor Rule

When asked for task status, Conductor must answer from source-backed state.

If no runtime state source exists, Conductor should say so directly.

Preferred response:

```text
Source status: SOURCE_READY (immutable repository source observed).
Confirmed: last GitHub connector call returned open_prs = 0.
Observed reference: historical checkpoint and validation documents, if read.
Not performed: Resume restore and current validation.
Unknown: current Anchor, Runtime Image, scheduler queue state, and cache state.
```

For OS_STATUS, Conductor should additionally split session and repository state:

```text
Session Runtime: READY / PARTIAL / NOT_READY / UNKNOWN
Repository Runtime: VERIFIED / PARTIAL / NOT_VERIFIED / NOT_ATTACHED / UNKNOWN
```

Each field must include its evidence class.

## Validation Plan

1. Ask for status after a confirmed GitHub connector call.
2. Report only the GitHub result as confirmed.
3. Mark runtime queue/scheduler/cache as unknown unless read from state.
4. Read `.ai/runtime/state/session.md` and verify status becomes confirmed only when its session evidence is current.
5. Ask `@GitHub ai-career OS_STATUS` after BOOT session governance is active.
6. Verify response separates `SESSION_RUNTIME_READY` from `REPOSITORY_RUNTIME_VERIFIED`.
7. Verify registry claim is not treated as repository verification without validation evidence.
8. Run source-only OS_STATUS with old checkpoint, Resume Archive, validation,
   and Runtime Image references.
9. Verify the result remains `SOURCE_READY`, restore remains `NOT_PERFORMED`,
   validation remains `NOT_RUN`, and active Runtime/Anchor fields remain
   `UNKNOWN`.

## Status

Observation promoted to core runtime candidate.

This rule should be treated as an observability guard for Task Queue Runtime v1 and OS_STATUS session/repository split reporting.
