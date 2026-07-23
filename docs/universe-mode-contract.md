# Universe Mode Contract

Universe separates self-governance from cross-project operation.

## Canonical coordinates

```yaml
MASTER:
  role: MASTER
  scope: architecture/governance

UNIVERSE:
  role: CONDUCTOR
  scope: project-network/navigation/distribution
  mode_profile: GOVERNANCE_ONLY
```

`Universe Mode` and `Conductor Mode` are project-local user-facing aliases for
the registered `UNIVERSE` Mode. They do not create a second `CONDUCTOR` Mode.
The Role always resolves from the Mode Registry.

## Lifecycle boundary

Initial setup runs in `MASTER`. MASTER installs and validates an ai-career
Release DB, updates Universe policy, and manages the Mode Registry. A successful
release installation may propose a change to `UNIVERSE`; it does not silently
change Mode.

Daily cross-project navigation, projection, release rollout proposals, and
project coordination run in `UNIVERSE` with Role `CONDUCTOR`. Release DB
installation or update requested from `UNIVERSE` returns
`MASTER_MODE_REQUIRED`.

Release artifact import and the persisted install/update proposal are Universe
self-lifecycle operations and therefore require `MASTER`. The proposal itself
has `project_write: NONE`. Universe does not expose an HTTP apply route.

After the proposal, the user approves the exact plan in the attached Project.
That Project Host performs receipt-aware writes, validate/status, and completion
evidence inside its own boundary.

Dispatch creation is allowed for daily `UNIVERSE` operation. Delivery is
separate because it appends to the Project-owned MASTER Inbox; each delivery
request must carry `approval: APPROVED` as explicit Project inbox-write
approval. Wake, acknowledgement, start, and result events do not create project
source-write authority.

## Invariants

```text
UNIVERSE Mode MUST resolve Role CONDUCTOR.
CONDUCTOR intent in this repository MUST resolve Mode UNIVERSE.
Release lifecycle mutation MUST require MASTER Mode.
Mode or Role MUST NOT create authority or execution permission.
```
