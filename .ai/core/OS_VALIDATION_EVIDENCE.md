# OS Validation Evidence

Status: Candidate Core Architecture
Scope: ai-career / attached project runtime
Layer: Validation Evidence Surface
Parent: `.ai/core/RUNTIME_INSTRUCTION_SET.md`
Created: 2026-07-02

## Purpose

OS Validation Evidence defines where attached projects record proof that OS_INSTALL, OS_UPDATE, and OS_VALIDATE were performed against the active ai-career runtime instruction contract.

Project Anchor tells a project where to assemble locally.

OS Validation Evidence tells a project where to record proof that the local assembly is compatible.

## Core Declaration

```text
OS_UPDATE IS NOT READY UNTIL OS_VALIDATE EVIDENCE IS RECORDED.

PROJECT ANCHOR READY REQUIRES A VALIDATION EVIDENCE SURFACE.

CANONICAL CONTRACT FRESHNESS MUST BE SOURCE-BACKED.
```

## Required Evidence Surface

An attached project should provide one compact project-local validation surface:

```text
.ai/runtime/project_instance/validation/
.ai/runtime/project_instance/validation/latest.md
.ai/runtime/project_instance/validation/history.md
```

The exact file names may vary by project, but the project must be able to report:

```text
Where the latest OS_VALIDATE result is stored.
Where validation history is stored.
Which VERSION_MANIFEST was checked.
Which canonical contract source was used.
Where local update checkpoint/evidence is referenced.
```

`VERSION_MANIFEST` check and canonical contract freshness should be recorded inside the validation evidence record, not necessarily as separate files.

## Latest Validation Record

`validation/latest.md` should contain the latest OS_VALIDATE evidence.

A minimal latest record should include:

```text
Project
Project Anchor path
Project runtime root
Command
Instruction contract source
Instruction contract version or commit
VERSION_MANIFEST source
VERSION_MANIFEST status
Canonical contract freshness status
Validated local surfaces
Missing local surfaces
Primary mode template source
MASTER primary-mode entry status
MASTER Role / Mode Scope binding status
Project-owned runtime state status
Project-owned current anchor frame status
Authority / Execution Assignment separation status
Result
Evidence timestamp
Related local checkpoint or evidence reference
```

Recommended statuses:

```text
PASS
FAIL
PARTIAL
UNKNOWN
STALE
```

`STALE` is valid for OS_VALIDATE comparison results when a Runtime Image is behind or inconsistent while Git-backed source validates.

## Validation History

`validation/history.md` should track validation records over time.

Suggested shape:

```text
# OS Validation History

Project: <name>
Project Anchor: <path>
Latest Result: PASS | FAIL | PARTIAL | UNKNOWN
Latest Evidence: validation/latest.md
Latest Contract Source: <ref or commit>

## Records

- <timestamp> OS_UPDATE -> validation/latest.md -> <result>
```

History may link to separate timestamped evidence files if a project wants to keep full snapshots, but that is optional.

## VERSION_MANIFEST Check

OS_VALIDATE should verify that `VERSION_MANIFEST.md` exists or report it as missing.

The check result should be recorded inside `validation/latest.md`:

```text
VERSION_MANIFEST path
Read timestamp
Observed version fields
Observed runtime surfaces
Compatibility status
Missing fields
Unknown fields
```

If `VERSION_MANIFEST.md` is missing or stale, OS_VALIDATE should report `PARTIAL` or `UNKNOWN`, not `READY`.

## Canonical Contract Freshness

An attached project should record which ai-career contract it used for OS_UPDATE.

Freshness evidence should be recorded inside `validation/latest.md` and may include:

```text
ai-career repository
ai-career branch or ref
ai-career commit SHA
RUNTIME_COMMANDS.md source status
RUNTIME_INSTRUCTION_SET.md source status
PROJECT_ANCHOR.md source status
OS_VALIDATION_EVIDENCE.md source status
CORE_STACK_MAP.md source status
COMMANDER_WAIT_BUFFER_RULE.md source status
RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md source status
NODE_MODE_COORDINATE_CONTRACT.md source status
SESSION_CURRENTNESS.md source status
RUNTIME_STATE_TRUST_GATE.md source status
RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md source status
RUNTIME_AUTHORITY_EXECUTION_BINDING.md source status
Read timestamp
```

The project does not need to prove global truth.

It must prove which source-backed contract it used locally.

## Commander Wait Buffer Evidence

When a project records wait, pause, still-speaking, or multi-part instruction
handling, OS_VALIDATE should record whether the surface was compared against
`.ai/core/COMMANDER_WAIT_BUFFER_RULE.md`.

Recommended evidence shape:

```yaml
commander_wait_buffer:
  source: .ai/core/COMMANDER_WAIT_BUFFER_RULE.md
  source_commit: <commit-or-UNKNOWN>
  explicit_wait_enters_waiting_commander: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  mutation_blocked_while_waiting: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  buffered_fragments_not_authority: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  release_intent_required: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  release_routes_through_normal_gates: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  result: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  reason: <source-backed explanation>
```

If a project has wait or still-speaking handling but cannot prove buffered
fragments are non-authoritative, OS_VALIDATE should report `PARTIAL` or
`UNKNOWN`, not `PASS`.

## Runtime Anchor Frame Routing Constraint Evidence

When a project records a Runtime Anchor Frame, current-coordinate surface, or
connector-backed boot entry, OS_VALIDATE should record whether that surface was
compared against `.ai/core/RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md`.

Recommended evidence shape:

```yaml
runtime_anchor_frame_routing_constraint:
  source: .ai/core/RUNTIME_ANCHOR_FRAME_ROUTING_CONSTRAINT.md
  source_commit: <commit-or-UNKNOWN>
  project_surfaces_checked:
    - <path-or-UNKNOWN>
  active_runtime_anchor_frame_present: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  conversation_context_reference_only: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  frame_required_before_execution_routing: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  missing_stale_or_mismatched_frame_returns_anchor_frame_required: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  role_mode_authority_not_inferred_from_conversation: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  result: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  reason: <source-backed explanation>
```

If a project has runtime frame surfaces but routes execution from conversation
context without proving active-frame currentness, OS_VALIDATE should report
`PARTIAL`, `FAIL`, or `UNKNOWN`, not `PASS`.

## Runtime Image Comparison Evidence

OS_VALIDATE may include a Runtime Image comparison section when a session-scoped Runtime Image exists.

Runtime Image evidence is not authority.

Git-backed source remains authority.

The validation record should distinguish:

```text
runtime_image_result
git_source_validation
comparison_result
final_result
authority_source
reason
```

Required comparison rules:

```text
PASS requires Runtime Image and Git-backed source to agree.
If Runtime Image overclaims compared to Git source, final result is UNKNOWN.
If Runtime Image is stale while Git source validates, final result is STALE.
If both sides agree required surfaces are missing, final result is PARTIAL.
If Git source cannot be verified, final result is UNKNOWN.
```

Recommended evidence shape:

```yaml
runtime_image:
  status: PASS | PARTIAL | FAIL | UNKNOWN
  source_commit: <commit-or-UNKNOWN>
git_validation:
  status: PASS | PARTIAL | FAIL | UNKNOWN
  source_commit: <commit-or-UNKNOWN>
comparison:
  equal: true | false
final:
  status: PASS | PARTIAL | FAIL | UNKNOWN | STALE
authority: Git-backed source
reason: <source-backed explanation>
```

When no Runtime Image exists, OS_VALIDATE should still validate Git-backed source and may omit the Runtime Image comparison section.

## Runtime Image Assembly Evidence

When a project or session records a Boot Evidence Bundle or Runtime Image,
OS_VALIDATE should record whether the surface was compared against
`.ai/core/RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md`.

Recommended evidence shape:

```yaml
runtime_image_assembly:
  source: .ai/core/RUNTIME_IMAGE_ASSEMBLY_CONTRACT.md
  source_commit: <commit-or-UNKNOWN>
  implementation_profile: memory | markdown | yaml | json | sqlite_memory | sqlite_file | file_cache | other | UNKNOWN
  boot_evidence_bundle: present | missing | partial | UNKNOWN | NOT_APPLICABLE
  source_coordinates: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  disposable_artifact: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  authority_boundary: PASS | PARTIAL | FAIL | UNKNOWN
  stale_detection: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  result: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  reason: <source-backed explanation>
```

If a Runtime Image exists but source coordinates or authority boundary cannot be
verified, OS_VALIDATE should report `PARTIAL` or `UNKNOWN`, not `PASS`.

## Node / Mode Coordinate Evidence

When a project records or resolves Node, Mode, Role, Scope, status, or Runtime
Anchor Frame data, OS_VALIDATE should record whether the project was compared
against `.ai/core/NODE_MODE_COORDINATE_CONTRACT.md`.

Recommended evidence shape:

```yaml
node_mode_coordinate_contract:
  source: .ai/core/NODE_MODE_COORDINATE_CONTRACT.md
  source_commit: <commit-or-UNKNOWN>
  project_surfaces_checked:
    - <path-or-UNKNOWN>
  node_status: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  mode_status: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  role_scope_resolution: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  authority_boundary: PASS | PARTIAL | FAIL | UNKNOWN
  result: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  reason: <source-backed explanation>
```

If project coordinate-bearing surfaces exist but this comparison is missing,
OS_VALIDATE should report `PARTIAL` or `UNKNOWN`, not `PASS`.

## Runtime State Trust Evidence

When an attached project records runtime frame or active state data, OS_VALIDATE
should record whether the project was compared against
`.ai/core/RUNTIME_STATE_TRUST_GATE.md`.

Recommended evidence shape:

```yaml
runtime_state_trust:
  source: .ai/core/RUNTIME_STATE_TRUST_GATE.md
  source_commit: <commit-or-UNKNOWN>
  project_surfaces_checked:
    - <path-or-UNKNOWN>
  active_ing_rule: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  restored_ing_recheck: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  state_origin: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  state_freshness: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  evidence_priority: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  result: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  reason: <source-backed explanation>
```

If active `*_ING` state exists but restored/stale continuation rules cannot be
verified, OS_VALIDATE should report `PARTIAL` or `UNKNOWN`, not `PASS`.

If state evidence conflicts and source-backed priority cannot resolve it,
OS_VALIDATE should report `UNKNOWN`.

## Runtime Authority Execution Binding Evidence

When mutation is in scope or a project records certificate/execution binding
data, OS_VALIDATE should record whether the surface was compared against
`.ai/core/RUNTIME_AUTHORITY_EXECUTION_BINDING.md`.

Recommended evidence shape:

```yaml
runtime_authority_execution_binding:
  source: .ai/core/RUNTIME_AUTHORITY_EXECUTION_BINDING.md
  source_commit: <commit-or-UNKNOWN>
  certificate_presence_is_not_execution_authority: PASS | PARTIAL | FAIL | UNKNOWN
  current_writer_binding: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  execution_surface_binding: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  repository_location_binding: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  execution_assignment: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  approval_evidence: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  source_anchor_frame_binding: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  stale_certificate_blocks_mutation: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  result: PASS | PARTIAL | FAIL | UNKNOWN | NOT_APPLICABLE
  reason: <source-backed explanation>
```

If mutation is requested and binding evidence is missing, OS_VALIDATE should
report `PARTIAL`, `FAIL`, or `UNKNOWN`, not `PASS`.

## Project Anchor Readiness

Project Anchor readiness requires validation evidence paths.

```text
Project Anchor Ready =
  Project Anchor exists
  + validation/latest.md exists or missing state is recorded
  + validation/history.md exists or missing state is recorded
  + VERSION_MANIFEST status is recorded inside validation/latest.md
  + canonical contract freshness source is recorded inside validation/latest.md
```

If those evidence records are absent, Project Anchor status should be `PARTIAL` or `UNKNOWN`.

## Standalone Distribution Validation

When the project claims a standalone project-local Runtime, OS_VALIDATE must
also verify the materialized distribution evidence:

```text
.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json exists
source repository and immutable source commit are recorded
source provider, source binding, and read policy are recorded
source index path, Git blob OID, and SHA-256 are recorded
remote source bundle hash, provider identity, and capability evidence are recorded when applicable
remote bundle source_cleanliness is NOT_APPLICABLE rather than inferred CLEAN
source Core Surface Registry hash is recorded
distribution manifest hash is recorded
every required managed path exists
every required managed path matches its recorded SHA-256
registered Core and Template sets match the selected source Registry
required Runtime executables are present
generated router/state surfaces preserve Node / Mode / Role coordinates
generated initial state does not claim active session currentness
Authority remains separate from Execution Assignment
required local `.ai` references resolve
excluded research/archive/proof-fixture roots were not copied by implication
```

Validation result boundaries:

```text
project-instance structure valid, distribution absent
  -> project_instance_structure: PASS
  -> repository_runtime: PARTIAL

distribution present, required file missing or hash mismatched
  -> repository_runtime: PARTIAL or FAIL

all required distribution and project-instance checks pass
  -> repository_runtime: VERIFIED
  -> session_runtime: UNKNOWN
  -> session_initialization: UNINITIALIZED
  -> currentness: UNKNOWN
```

`repository_runtime: VERIFIED` validates the durable install only. It must not
promote an install-time placeholder Session ID, Frame ID, Host, or Anchor into
an active session/currentness claim.

`validation: PASS` must identify which validation scope passed. A structure-only
PASS must not be reused as standalone Runtime verification.

### Legacy Migration Evidence

When a declared legacy migration profile is used, validation evidence must be
traceable to both the old and new surfaces:

```text
migration profile ID
declared legacy source commit
complete legacy collision path set
legacy inventory SHA-256
per-file legacy SHA-256
per-file disposition
immutable archive path
replacement source commit
new managed distribution manifest
post-migration OS_VALIDATE result
```

Missing paths, extra collisions, marker mismatches, unreadable files, or archive
collisions must fail before replacement. A plain `--force` run is not legacy
migration evidence.

Archived state may support a later Resume review, but it does not prove active
session currentness. Immediate migration validation must keep Session Runtime
and Currentness `UNKNOWN` until a separate BOOT/Resume path supplies valid
evidence.

## OS_UPDATE Completion Rule

OS_UPDATE should not be reported as complete from file changes alone.

Expected completion:

```text
OS_UPDATE
  -> project-local assembly/update
  -> OS_VALIDATE
  -> validation/latest.md updated
  -> validation/history.md updated
  -> compatibility state reported
```

Completion states:

```text
Complete
  -> latest validation evidence exists and result is PASS or accepted PARTIAL

Partial
  -> local assembly changed but validation evidence is incomplete

Unknown
  -> validation evidence path is missing or unreadable

Failed
  -> OS_VALIDATE reports incompatible surfaces
```

## Relationship To Runtime Instruction Set

Runtime Instruction Set defines the instruction.

OS Validation Evidence proves the local result.

```text
RUNTIME_INSTRUCTION_SET.md
  -> what must be assembled or updated

OS_VALIDATION_EVIDENCE.md
  -> where the project records proof
```

## Relationship To Project Anchor

Project Anchor localizes the instruction contract.

OS Validation Evidence validates the localized assembly.

```text
Project Anchor
  -> local reference point

OS Validation Evidence
  -> source-backed proof of local compatibility
```

## Relationship To Runtime Status Source Rule

OS validation results must be source-backed.

If the evidence file is missing, unreadable, or not tied to a source contract, status should be reported as `UNKNOWN` or `PARTIAL`.

## Anti-Patterns

Avoid:

```text
- Reporting OS_UPDATE complete from file edits only.
- Reporting Project Anchor ready without validation evidence location.
- Reporting contract freshness without source reference.
- Treating missing VERSION_MANIFEST as success.
- Reporting OS_INSTALL complete when the mandatory MASTER primary
  mode or project-owned state/currentness surfaces are absent.
- Reporting repository-runtime VERIFIED from the project-instance structure
  check while the required distribution manifest, Core pack, Template pack, or
  Runtime executable hashes are absent.
- Creating separate required files for every individual check when latest.md can hold the evidence.
```

Prefer:

```text
- Source-backed evidence.
- One latest validation record.
- One validation history file.
- Explicit UNKNOWN for missing proof.
- Explicit MASTER_MODE: READY | NOT_READY | UNKNOWN evidence for newly attached
  project runtimes.
- Project-local checkpoint reference after validation.
- Compatibility by behavior and evidence.
```

## Placement Test

A concept belongs in OS Validation Evidence when it answers:

```text
Where is OS_VALIDATE recorded?
What proves VERSION_MANIFEST was checked?
What proves the canonical contract source used for OS_UPDATE?
What evidence makes OS_UPDATE Complete, Partial, Unknown, or Failed?
```

If a concept defines the local reference point, it belongs in `PROJECT_ANCHOR.md`.

If it defines instruction requirements, it belongs in `RUNTIME_INSTRUCTION_SET.md`.

If it defines command routing, it belongs in `RUNTIME_COMMANDS.md`.

If it reports runtime status without evidence, use `RUNTIME_STATUS_SOURCE_RULE.md`.

## Adoption Status

This is a candidate evidence-surface document.

It should be validated by applying OS_UPDATE to an attached project and confirming that OS_VALIDATE writes or updates `validation/latest.md` and `validation/history.md`.
