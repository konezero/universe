# Runtime Source Verification

Status: Candidate Core Architecture
Scope: ai-career / runtime commands
Layer: Source Verification / Repository Truth
Parent: `.ai/core/RUNTIME_COMMANDS.md`
Created: 2026-07-02

## Purpose

Runtime Source Verification defines how runtime commands should use repository search and repository fetch.

Search may discover candidate paths or stale indexed content.

Fetch confirms the current source-backed truth for a specific repository, ref, and path.

## Core Declaration

```text
SEARCH IS DISCOVERY.
FETCH IS TRUTH.

DO NOT REPORT RUNTIME STATUS FROM SEARCH ALONE.
DO NOT PATCH FROM SEARCH ALONE.
FETCH CURRENT REPOSITORY FILES BEFORE STATUS, PROPOSAL, PATCH, OR VALIDATION.
```

## Why

A runtime command can see old search results after a file has been removed from `main`.

Therefore search results are useful for finding possible files, but not enough to decide current runtime state.

A source-backed runtime must verify by fetching the specific file from the intended repository and ref.

## Source Levels

```text
Search Result
  -> candidate / discovery only

Fetch File
  -> current file truth for repository + ref + path

Fetch Commit
  -> historical truth for one commit

Validation Evidence
  -> durable runtime proof when tied to fetched source
```

## Required Pattern

For runtime-sensitive commands:

```text
Search or known path
  -> fetch_file(repository, ref, path)
  -> evaluate fetched content or explicit 404
  -> report source-backed status
```

If fetch returns 404 for current `main`, report the file as absent from current `main`, even if search returns an older result.

## Applies To

This rule applies to:

```text
OS_STATUS
OS_INSTALL
OS_UPDATE
OS_VALIDATE
OS_PREFLIGHT
Memory Sync when claiming durable repository evidence
Tutorial Guide when claiming a source-backed Runtime fact
```

## OS_INSTALL

```text
OS_INSTALL
  -> search may find candidate surfaces
  -> fetch current repository files
  -> scan current repository state
  -> produce proposal
  -> require approval
  -> write only after approval
```

## OS_STATUS

```text
OS_STATUS
  -> fetch current status sources
  -> report missing files as UNKNOWN / ABSENT
  -> do not infer installed state from stale search hits
```

## OS_UPDATE

```text
OS_UPDATE
  -> fetch current local surfaces
  -> fetch current core surfaces
  -> compare fetched content
  -> propose patch
  -> require approval when needed
```

## OS_VALIDATE

```text
OS_VALIDATE
  -> fetch required evidence files
  -> fetch manifest / registry / core surface sources
  -> record explicit PASS / FAIL / PARTIAL / UNKNOWN
```

If OS_VALIDATE reads a Runtime Image, it must still verify against Git-backed source.

```text
Runtime Image
  -> session-scoped boot artifact
  -> may support validation queries
  -> not authority

Git-backed source
  -> current repository truth
  -> authority for validation
```

Source-backed comparison:

```text
Runtime Image PASS + Git source PASS
  -> PASS

Runtime Image PASS + Git source PARTIAL / FAIL
  -> UNKNOWN

Runtime Image PARTIAL / FAIL + Git source PASS
  -> STALE

Runtime Image PARTIAL + Git source PARTIAL
  -> PARTIAL when missing surfaces agree

Git source UNKNOWN
  -> UNKNOWN
```

The Runtime Image may never override fetched Git source.

If the Runtime Image and fetched Git source disagree and the conflict cannot be resolved, report `UNKNOWN`.

If the Runtime Image is behind the fetched Git source while Git source validates, report `STALE`.

## Source Provider Boundary

Git-backed source may be consumed through a local Git object database, a
connector, or an authenticated GitHub CLI that fetches repository files at one
immutable full commit.

```text
Local Git source
  -> commit and blob objects verified by local Git CLI
  -> source_binding: git-object-database

GitHub connector source bundle
  -> Host resolves one full commit and fetches every indexed path at that commit
  -> Python recomputes payload SHA-256 and Git blob OID
  -> source_binding: provider-attested
  -> source_cleanliness: NOT_APPLICABLE

GitHub CLI source bundle
  -> Host uses gh api to resolve one full commit and fetch every indexed path
  -> Python recomputes payload SHA-256 and Git blob OID
  -> source_provider: github-cli
  -> source_binding: provider-attested
  -> source_cleanliness: NOT_APPLICABLE
```

The remote provider path is source-backed only when the requested ref, resolved
commit, source index, path, provider-returned blob OID, fetched payload, and
truthful provider identity are recorded together. Search results or a manually
copied subset are insufficient.

The Host/Skill owns connector invocation and transport. The deterministic
installer owns bundle verification. It must not initialize a repository or
create a synthetic commit to imitate Git evidence.

`provider-attested` and `git-object-database` are distinct evidence bindings.
Neither may be silently remapped to the other.

## Status Language

Recommended source status terms:

```text
FETCHED
ABSENT
STALE_SEARCH_ONLY
UNKNOWN
SOURCE_BACKED
STALE_RUNTIME_IMAGE
```

Example:

```text
Search found: .ai/runtime/project_instance/os_install.md
Fetch main: 404
Status: ABSENT on main
Search status: STALE_SEARCH_ONLY
```

## Anti-Patterns

Avoid:

```text
- Reporting install PASS because search found an old validation file.
- Reporting OS_STATUS from search snippets only.
- Patching a file path found by search without fetching it first.
- Treating historical commit content as current main content.
```

Prefer:

```text
- Search for discovery.
- Fetch for current truth.
- Commit fetch for historical comparison.
- Explicit source status in runtime reports.
```

## Placement Test

A concept belongs in Runtime Source Verification when it answers:

```text
Is this runtime fact current?
Which repository/ref/path was fetched?
Is a search result stale?
Can this status be source-backed?
```

If it defines command routing, it belongs in `RUNTIME_COMMANDS.md`.

If it defines install flow, it belongs in `PROJECT_RUNTIME_INSTALL.md`.

If it defines validation evidence shape, it belongs in `OS_VALIDATION_EVIDENCE.md`.

## Adoption Status

This is a candidate Core architecture rule.

It should be tested by querying a path that search still finds but `fetch_file` on `main` returns 404. The runtime should report the fetch result as authoritative for current state.
