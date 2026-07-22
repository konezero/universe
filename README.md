# Universe

Universe is a local-first environment for connecting independent software
projects, recalling their proven histories, and proposing possible future work
paths.

The first implementation surface is the Official Development Seed. It solves
the cold-start problem before a user has accumulated enough project history to
support useful path recall.

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

Run the seed validation suite:

```powershell
python -m unittest discover -s tests -v
```

The initial contract is documented in
`docs/official-development-seed-v0.md`.
