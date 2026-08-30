# Project Runtime Database Migration Todo

Status: P0 and P1 implemented, released, installed, and verified

## P0 - Mode and Anchor authority

- [x] Define one Host-owned SQLite database per Project.
- [x] Store Project identity, the Mode Registry snapshot, one Current Anchor
  per Mode, Beyond Anchor history, and provider/session observations.
- [x] Reject Project identity rebinding and Registry revision conflicts.
- [x] Have the SessionStart Hook and Anchor Graph establish and observe the
  current Session Anchor; do not derive authority from a Mode Profile or an
  installation default.
- [x] Resolve authority, write scope, and execution assignment from the
  current Anchor Graph records immediately before execution.
- [x] Keep provider sessions and Workers read-only to the database.
- [x] Commit the Career Runtime source change and produce an immutable release
  at `b98a74693d779841f2b913cfdc82cb388bfc2181` on ai-career PR #269.
- [x] OS_UPDATE the Universe installation from that release and run live
  Conductor Mode dogfood through the Universe app/observatory without treating
  UNIVERSE as a Mode.

The earlier binding-oriented wording in historical plans is superseded by the
SessionStart Hook plus Anchor Graph path above. Existing release databases and
their provenance remain immutable; a later release or MODE_CHANGE supersedes
them rather than rewriting them.

## P1 - Runtime state consolidation

- [x] Move Mode Current Anchor writes from per-Mode compatibility stores into
  the Project Runtime database as the only write path.
- [x] Render Markdown state companions from the database as non-authoritative
  projections.
- [x] Move Assignment and Task Frame indexes without changing approval or
  Execution Guard boundaries.
- [x] Move continuity/checkpoint indexes while keeping transcript and raw
  provider content outside the database.
- [x] Add schema migrations, backup/restore, corruption recovery, and vacuum
  policy.

P1 source acceptance includes schema v1-to-v2 migration, DB-only Current
Anchor writes, forward-only commander observations, reference-only Assignment /
Task Frame / Continuity indexes, integrity checks, rollback-safe restore, and
non-authoritative Markdown projections. Runtime installation tests include
`project_runtime_store.py` in the fresh-install package fixture.

P1 release and consumer evidence:

- ai-career PR #269 source commit:
  `94460a5228603a2ce2f80f6b0ee1a0092bf53f7d`
- Universe Host Runtime Lifecycle receipt:
  `host-runtime-lifecycle-374ee07f881d9b5f587e4eb0`
- Post-update repository validation: `PASS / VERIFIED`
- Validation ID:
  `1c39336eb8bdb786b628314e2874f1d5d838eb18d7210aa65249860fc18710c4`
- Service dogfood endpoint after restart: `http://127.0.0.1:60589/`

## Acceptance

- Dynamic MASTER-created Modes boot with their Registry Role and Scope even
  when the installation default remains MASTER.
- Restart never falls back to a provider-specific or installation-default Mode.
- The same binding cannot start two Session Boot processes.
- A new Current Anchor retires every unconsumed binding for the previous one.
- Universe shared hosting and standalone installation use the same logical
  Project database contract.
