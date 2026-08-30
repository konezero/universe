# Universe Mode Contract

Universe separates self-governance from cross-project operation.

## Canonical coordinates

Fresh project registries use `ai-career.mode-registry.v2`. A Mode definition
contains descriptive `role` and `scope` only:

```yaml
schema: ai-career.mode-registry.v2
modes:
  MASTER:
    role: MASTER
    scope: architecture/governance
  CONDUCTOR:
    role: CONDUCTOR
    scope: project-network/navigation/distribution
```

The loader tolerates v1 registries during the transition, but a v1
`mode_profile` field is legacy data: it is ignored, stripped from the
normalized definition, and never surfaced as a live contract. New installs do
not emit it. Neither the Mode name, Role, Scope, nor a registry field creates
authority or execution permission.

`UNIVERSE` is the app and observatory name. It is not a Mode, Role, Mode alias,
or Mode Registry entry. Only `MASTER` and `CONDUCTOR` are Mode coordinates.
Application observation uses registered `CONDUCTOR` Mode. The Role and Scope
are descriptive registry coordinates, not an authorization decision.

The SessionStart Hook observes the provider session and its exact Session
Anchor. The Anchor Graph DB is the currentness and authority source for that
session: `mode_current_anchor`, `authority`, `write_scope`, and
`execution_assignment` records must be resolved server-side and checked at
the point of execution. Markdown companions and installation defaults are
projections or seeds only.

The Universe app server observes the separate Mode Host; it must not BOOT,
bind, own, or start a Conductor provider session or manufacture an Anchor.

## Lifecycle boundary

Initial setup runs in `MASTER`. MASTER installs and validates an ai-career
Release DB, updates Universe policy, and manages the Mode Registry. A
successful release installation may propose a change to `CONDUCTOR`; it does
not silently change Mode or currentness.

Daily cross-project navigation, projection, release rollout proposals, and
project coordination run in `CONDUCTOR`. Release DB installation or update
requested from `CONDUCTOR` returns `MASTER_MODE_REQUIRED`.

Release artifact import and the persisted install/update proposal are Universe
self-lifecycle operations and therefore require `MASTER`. The proposal itself
has `project_write: NONE`. Universe does not expose an HTTP apply route.

After the proposal, the user approves the exact plan in the attached Project.
That Project Host performs receipt-aware writes, validation, and completion
evidence inside its own boundary.

Dispatch creation is allowed for daily `CONDUCTOR` operation. Delivery is
separate because it appends to the Project-owned MASTER Inbox; each delivery
request must carry `approval: APPROVED` as explicit Project inbox-write
approval. Wake, acknowledgement, start, and result events do not create
project source-write authority.

## Invariants

```text
CONDUCTOR Mode MUST resolve Role CONDUCTOR and its descriptive Scope.
Conductor intent MUST resolve Mode CONDUCTOR.
Universe / UNIVERSE intent MUST NOT resolve as a Mode or alias.
SessionStart MUST observe an exact provider Session Anchor.
Anchor Graph currentness, authority, write scope, and assignment MUST be
  verified server-side immediately before execution.
Missing, stale, or mismatched Anchor Graph evidence MUST fail closed.
UNIVERSE MUST NOT appear under modes in the Mode Registry.
A separate Mode Host MUST own Conductor lifecycle; the Universe app observes.
Release lifecycle mutation MUST require MASTER Mode.
Mode, Role, Scope, or legacy registry metadata MUST NOT create authority.
```
