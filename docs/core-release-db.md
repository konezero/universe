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
- read-only verification without Candidate execution;
- ordered Skill Load Profiles and Mode Profiles in Release DB v2;
- legacy Release DB v1 verification without profile claims;
- content-addressed Universe Catalog import;
- read-only Project install/update planning with collision detection;
- Project Host-owned application after exact plan approval.

Not implemented yet:

- signed release promotion;
- release channel selection;
- receipt-aware Project Host apply adapter;
- rollback and project fleet update orchestration.

## Catalog and Project boundary

Universe verifies the external manifest and immutable SQLite payload before
copying both into content-addressed local catalog storage. Import and proposal
creation require `MASTER`; `UNIVERSE` may display catalog and proposal state but
cannot apply a release.

```text
POST /v1/releases/import
GET  /v1/releases
GET  /v1/releases/{release_id}
POST /v1/projects/{project_id}/release-proposals
GET  /v1/projects/{project_id}/release-proposals
```

A proposal computes `FRESH_INSTALL` or `UPDATE`, exact actions, collisions, and
the plan digest. The service persists the proposal with `project_write: NONE`.
The attached Project Host remains responsible for user approval, Execution
Guard, receipt-aware writes, validate/status, and the final install receipt.
Universe deliberately exposes no HTTP apply endpoint.
