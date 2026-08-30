# Core Release DB

Status: TEST_PROTOTYPE

Career produces an immutable Runtime Release DB. Universe distributes the
selected release without requiring an attached Project to access the private
ai-career Git repository. Project attachment and project-facing templates are
Universe-owned; the Release DB remains Career-owned.

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
- ordered Skill Load Profiles and context profiles in Release DB v3;
- legacy Release DB v1/v2 verification and compatibility reads without
  treating their historical Mode Profile fields as live policy;
- legacy Release DB v1 verification without profile claims;
- content-addressed Universe Catalog import;
- read-only Project `OS_INSTALL`/`OS_UPDATE` lifecycle planning;
- provider-attested source-bundle materialization from immutable Release DB
  bytes;
- receipt-aware Project Lifecycle Host application after exact plan approval;
- durable, idempotent application receipts.
- durable per-Project release selection created only by a successful lifecycle
  application;
- Resident Universe Conductor and Project Master Boot source binding resolved
  from the selected Release DB, rather than the mutable working-tree `HEAD`.

Not implemented yet:

- signed release promotion;
- release channel selection;
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
POST /v1/projects/{project_id}/release-proposals/apply
```

A proposal resolves the current Project lifecycle as `OS_INSTALL` or
`OS_UPDATE` and binds the target root, Project identity, Release DB identity,
source commit, installed manifest digest, and plan digest. Proposal creation is
read-only and persists `project_write: NONE`.

## Release Selection and Boot

`project_release_application` is an immutable history of applied lifecycle
receipts. `project_release_selection` is the one current release that a
Project is allowed to use for resident runtime Boot. A successful, validated
application updates that selection atomically; merely importing a Release DB,
creating a proposal, or creating a temporary Runtime lease does not.

```text
Release DB import
  -> approved OS_INSTALL / OS_UPDATE
  -> verified lifecycle application receipt
  -> durable Project release selection
  -> Conductor / Project Master Boot source binding
```

At Boot, the runtime receives a source reference in this form:

```text
universe-release-db://<release_id>@<release_database_sha256>
```

and the pinned source commit from the same selected artifact. It must not
resolve its source identity from the mutable Project worktree. If the Project
has no selected release, resident Boot fails closed with a release-selection
required state. This does not affect standalone command-line/test adapters
that are intentionally constructed without the Universe release resolver.

The apply route accepts only `APPROVED` plus the exact proposal ID and digest.
The Universe-owned Project Lifecycle Host then:

1. re-verifies the imported Release DB and current Project lifecycle state;
2. materializes a temporary `universe-release-db` provider-attested source
   bundle from content-addressed Release DB bytes;
3. runs the pinned ai-career Host lifecycle entry from the same artifact;
4. requires `PASS`, `VERIFIED`, matching target/source/operation, and
   `READY_FOR_BOOT`;
5. stores one durable application receipt keyed by proposal and approval.

This Host is independent of the resident Project Master, so a Fresh Project can
be installed before its Master Runtime exists. A resident Master is stopped
before update and started or reconnected after a successful lifecycle. Repeating
the same approved request returns the stored receipt without running the
lifecycle again.

A v2 artifact may still report `profile_catalog.status: ABSENT` when its pinned
source commit has no catalog surface. The artifact remains verifiable and
installable, but Skill or Mode profile resolution fails explicitly with
`PROFILE_CATALOG_REQUIRED`; Universe does not infer missing profile order.
