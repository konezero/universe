# Project Runtime Database Migration Todo

Status: first vertical slice implemented in source worktrees

## P0 - Mode Boot authority

- [x] Define one Host-owned SQLite database per Project.
- [x] Store Project identity, Mode Registry snapshot, one Current Anchor per
  Mode, Beyond Anchor history, and Mode Boot Binding.
- [x] Reject Project identity rebinding and Registry revision conflicts.
- [x] Make `prepare-session` create an opaque PREPARED binding.
- [x] Make Session Boot claim it once, verify Mode/Role/Scope/Anchor/Registry,
  activate it after Host activation, and fail closed on mismatch.
- [x] Reject binding-free Session Boot after the Project Runtime database exists.
- [x] Route Universe Conductor and Project Master startup through the binding.
- [x] Keep provider sessions and Workers read-only to the database.
- [x] Commit the Career Runtime source change and produce an immutable release
  at `b98a74693d779841f2b913cfdc82cb388bfc2181` on ai-career PR #269.
- [x] OS_UPDATE the Universe installation from that release and run live
  CONDUCTOR plus non-default `UNIVERSE` Mode dogfood.

## P1 - Runtime state consolidation

- [ ] Move Mode Current Anchor writes from per-Mode compatibility stores into
  the Project Runtime database as the only write path.
- [ ] Render Markdown state companions from the database as non-authoritative
  projections.
- [ ] Move Assignment and Task Frame indexes without changing approval or
  Execution Guard boundaries.
- [ ] Move continuity/checkpoint indexes while keeping transcript and raw
  provider content outside the database.
- [ ] Add schema migrations, backup/restore, corruption recovery, and vacuum
  policy.

## Acceptance

- Dynamic MASTER-created Modes boot with their Registry Role and Scope even
  when the installation default remains MASTER.
- Restart never falls back to a provider-specific or installation-default Mode.
- The same binding cannot start two Session Boot processes.
- A new Current Anchor retires every unconsumed binding for the previous one.
- Universe shared hosting and standalone installation use the same logical
  Project database contract.
