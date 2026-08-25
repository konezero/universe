# Universe

Universe is a local-first environment for connecting independent software projects,
recalling their proven histories, and proposing possible future work paths.

It treats each project as a navigable world:

- users direct goals through Universe;
- project Masters, Bosses, and Workers carry out bounded work;
- evidence, decisions, and memories remain connected to their origin;
- model sessions may change without losing project continuity.

Universe owns the project network, continuity, relationships, and experience
surface. Projects retain ownership of their repositories and artifacts. Career
is a separate governance and validation foundation that Universe consumes for
trusted rules, Seeds, and release/runtime lifecycle. Career is not the project
UI or the project data store.

## Core Product Loop

Universe begins with a **Feature Node**, not a Todo. When a user describes a
possible capability, the Conductor may open a user-visible **Meeting Room** and
bring together relevant persistent sessions, Masters, Bosses, Workers, and
research-oriented models. They use the room to retrieve evidence, expose
cross-feature dependencies, negotiate scope or escalation, and co-author
reviewable documents. The room is a general collaboration surface; automatic
round-robin debate is only one optional facilitation policy.

A meeting may produce several detailed implementation specifications. Those
alternatives are the Feature Node's **Expected Paths**: candidate future branches
that remain proposals until the user adopts one. Adoption preserves the selected
specification and its evidence, after which Universe may derive Goals and Todos
through the normal governed execution path. A Todo is therefore work *inside* an
adopted Feature Node path; it does not create or precede the Feature Node.

```text
User intent
  -> Feature Node
       -> Meeting Room: people + agents + retrieval + documents
            -> Expected Path A (detailed implementation specification)
            -> Expected Path B (detailed implementation specification)
            -> Expected Path N (detailed implementation specification)
                 -> user adoption
                      -> Goals -> Todos -> governed execution
```

Meeting participation, document creation, and path proposals do not by themselves
create execution authority.

## The Universe Today

The repository currently focuses on:

- persistent local Universe identity and SQLite-backed project state;
- registering and inspecting independent project worlds;
- an Official Development Seed for cold-start path candidates;
- local observations, memories, evidence references, and project dispatches;
- local server, UI, and Windows packaging for dogfooding.

Every suggestion is a candidate. Universe can propose a path, but it must not
silently change a project's source or create execution authority.

## The Universe Next

The next stage is to make the project graph a live operational map:

- show active Work, project ownership, model sessions, and execution hosts;
- attach decisions, memories, and evidence to project nodes;
- connect persistent Conductor and Project Master sessions to the worlds they operate;
- compare current project states with proven histories and suggest future branches;
- support approved remote access and projections between independent Universes.

These are product directions; the current repository should not be read as
claiming all of them are complete.

## Career Boundary

Career supplies reusable governance, validation rules, Seeds, and release/runtime
lifecycle. Universe uses those rules to connect and evolve project worlds. Projects
remain the owners of their source repositories, artifacts, and execution results.

```text
User
  -> Universe
       -> Project worlds
            -> Masters
                 -> Bosses
                      -> Workers
       -> Work -> Evidence -> Knowledge lineage

Career
  -> governance, validation, Seeds, and release/runtime lifecycle
```

## Official Development Seed v0

The seed is intentionally split into two forms:

- `seed/official-development-seed-v0.json` is the reviewable source.
- `dist/official-development-seed-v0.sqlite` is the generated read-only
  distribution artifact.

The v0 seed contains curated software-development archetypes, route templates,
failure patterns, and pivot rules. It does not claim learned probabilities or
verified future outcomes.

Build and inspect the seed:

```powershell
python tools/seed.py build
python tools/seed.py inspect
```

Request initial future-path candidates:

```powershell
python tools/seed.py suggest `
  --project "Local trading workstation" `
  --kind desktop-app `
  --tech python pyside6 sqlite `
  --goal "stable unattended operation with recoverable state"
```

Every suggestion is a candidate. It cannot update a Current Anchor, create
authority, assign work, or authorize execution.

## Development

Run the bounded regression tier that matches the change:

```powershell
python tools/run_test_tier.py changed --path tools/universe_app/connection.py
python tools/run_test_tier.py smoke
python tools/run_test_tier.py contract
python tools/run_test_tier.py full
```

`changed` and `smoke` target 30 seconds, while `contract` targets 90 seconds.
Use `--enforce-budget` when validating the tier budget. Run `full` before push
and for release validation; it remains equivalent to complete `unittest`
discovery under `tests/`.

The initial contract is documented in
`docs/official-development-seed-v0.md`.

Local product E2E (GCS seed + Master line) is fixed in
`docs/universe-e2e-product-scenario.md`. Smoke:

```powershell
python tools/universe_e2e_smoke.py run
python tools/universe_e2e_smoke.py check
```

Service and dispatch contracts are in `docs/local-universe-service.md`.

### Local packaging (Windows user)

```powershell
python tools/universe_server.py status
python tools/universe_server.py start --open-ui
python tools/universe_server.py stop
python tools/universe_server.py restart --no-open-ui
python tools/universe_server.py tray --start-service

powershell -ExecutionPolicy Bypass -File packaging\windows\install-user.ps1
# optional logon autostart:
powershell -ExecutionPolicy Bypass -File packaging\windows\install-user.ps1 -Autostart
```

Portable folder/zip (data stays inside the package):

```powershell
python tools/build_portable.py
python tools/build_portable.py --with-python
# -> dist/portable/UniversePortable-YYYYMMDD[-pyembed].zip
powershell -ExecutionPolicy Bypass -File packaging\windows\Install-Portable-User.ps1 `
  -Source dist\portable\UniversePortable-YYYYMMDD.zip
```

Details: `docs/universe-packaging.md`.

Memory RAG (project-local notes, not Candidates): `docs/universe-memory-rag.md`.
