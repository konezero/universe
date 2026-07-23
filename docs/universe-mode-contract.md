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

Universe does not directly mutate an attached project. It proposes a compatible
release, the user approves it, and the Project Installer validates and applies
the artifact inside that project's own boundary.

## Invariants

```text
UNIVERSE Mode MUST resolve Role CONDUCTOR.
CONDUCTOR intent in this repository MUST resolve Mode UNIVERSE.
Release lifecycle mutation MUST require MASTER Mode.
Mode or Role MUST NOT create authority or execution permission.
```
