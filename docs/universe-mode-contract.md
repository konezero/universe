# Universe Mode Contract

Universe separates self-governance from cross-project operation.

## Canonical coordinates

```yaml
MASTER:
  role: MASTER
  scope: architecture/governance

CONDUCTOR:
  role: CONDUCTOR
  scope: project-network/navigation/distribution
  mode_profile: GOVERNANCE_ONLY
```

`UNIVERSE` is the app and observatory name. It is not a Mode, Role, Mode alias,
or Mode Registry entry. Only `MASTER` and `CONDUCTOR` are Mode coordinates.
Application observation uses registered `CONDUCTOR` Mode. The Role always
resolves from the Mode Registry.

A separate Mode Host owns Conductor BOOT, Current Anchor, provider session,
and lifecycle. The Universe app server observes; it must not BOOT, prepare,
bind, own, or start a Conductor Mode session or runtime.

The installed Distribution Manifest Mode is only a first-start default. A
prepared Session binds the Registry-resolved Mode, Role, Scope, Current Anchor,
and Registry digests into the Project Runtime database. Session Boot must
consume that opaque Mode Boot Binding and must not resolve active Mode from the
installation default again.

## Lifecycle boundary

Initial setup runs in `MASTER`. MASTER installs and validates an ai-career
Release DB, updates Universe policy, and manages the Mode Registry. A successful
release installation may propose a change to `CONDUCTOR`; it does not silently
change Mode.

Daily cross-project navigation, projection, release rollout proposals, and
project coordination run in `CONDUCTOR`. Release DB installation or update
requested from `CONDUCTOR` returns
`MASTER_MODE_REQUIRED`.

Release artifact import and the persisted install/update proposal are Universe
self-lifecycle operations and therefore require `MASTER`. The proposal itself
has `project_write: NONE`. Universe does not expose an HTTP apply route.

After the proposal, the user approves the exact plan in the attached Project.
That Project Host performs receipt-aware writes, validate/status, and completion
evidence inside its own boundary.

Dispatch creation is allowed for daily `CONDUCTOR` operation. Delivery is
separate because it appends to the Project-owned MASTER Inbox; each delivery
request must carry `approval: APPROVED` as explicit Project inbox-write
approval. Wake, acknowledgement, start, and result events do not create project
source-write authority.

## Invariants

```text
CONDUCTOR Mode MUST resolve Role CONDUCTOR.
Conductor intent MUST resolve Mode CONDUCTOR.
Universe / UNIVERSE intent MUST NOT resolve as a Mode or alias.
Session Boot Mode MUST equal its prepared Mode Boot Binding Mode.
Missing, stale, reused, or mismatched Mode Boot Bindings MUST fail closed.
UNIVERSE MUST NOT appear under modes in the Mode Registry.
A separate Mode Host MUST own Conductor lifecycle; the Universe app observes.
Release lifecycle mutation MUST require MASTER Mode.
Mode or Role MUST NOT create authority or execution permission.
```
