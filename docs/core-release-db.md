# Core Release DB

Status: TEST_PROTOTYPE

Universe distributes an immutable ai-career Core Release without requiring a
project to access the private ai-career Git repository.

## Boundary

```text
private ai-career Git objects
  -> Core Release DB build
  -> Universe Release Catalog
  -> approved Project Installer
  -> project-local validate/status
```

The builder reads only blobs named by the pinned commit's canonical
`project_runtime_source_index.json`. Candidate Python, Skills, hooks, and
installers are stored as data and are never imported or executed while the
Release DB is built.

The Release DB is not an active Runtime database. Consumers must treat it as a
read-only artifact and verify both the external manifest database digest and
the internal payload digest before extraction or installation.

## Test build

```powershell
$sha = "<immutable-pr-head-sha>"
python tools/core_release.py build `
  --source-repo C:\workspace\ai-career `
  --source-ref $sha `
  --expected-commit $sha `
  --source-repository konezero/ai-career `
  --database dist\releases\ai-career-$($sha.Substring(0, 8)).sqlite3 `
  --manifest dist\releases\ai-career-$($sha.Substring(0, 8)).manifest.json

python tools/core_release.py verify `
  --database dist\releases\ai-career-$($sha.Substring(0, 8)).sqlite3 `
  --manifest dist\releases\ai-career-$($sha.Substring(0, 8)).manifest.json
```

## Current scope

Implemented:

- immutable Git commit resolution;
- canonical source-index inventory;
- path traversal, symlink, size, and duplicate-path rejection;
- per-file Git object ID, size, and SHA-256 storage;
- deterministic payload identity and external database digest;
- read-only verification without Candidate execution.

Not implemented yet:

- signed release promotion;
- Universe Catalog import and channel selection;
- Release DB extraction/source adapter;
- Project Installer invocation;
- rollback and project fleet update orchestration.
