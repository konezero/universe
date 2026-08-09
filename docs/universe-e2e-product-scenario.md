# Universe Product E2E Scenario (fixed line)

Status: fixed product scenario  
Scope: one local product path for GCS-attached Universe  
Not: authority creation, Task Frame execution permission, or remote multi-host proof

## Purpose

Lock one end-to-end product line that a human or agent can re-run and judge
pass/fail without inventing a new path each session.

This document is the scenario contract. Implementation APIs and inbox rules
live in `docs/local-universe-service.md`. Design context lives in
`docs/universe-design-and-bench-flow.md`.

## Scenario ID

```text
UNIVERSE_E2E_GCS_SEED_AND_MASTER_LINE_V1
```

## Product line (one sentence)

```text
Start local Universe
  -> connect project GCS
  -> open Project Master surface
  -> queue/deliver seed discovery dispatch
  -> publish Project Seed assets under .ai/universe
  -> close dispatch with Result Packet
  -> (optional) Fresh Composition Master handoff DELIVER
  -> inspect Activity / projection / Todo work map
```

## Preconditions

| # | Requirement | Notes |
|---|-------------|--------|
| P1 | Windows Host with Python available | Service entry: `python tools/universe_server.py serve` |
| P2 | Universe repo checkout | Working directory: Universe root |
| P3 | Project root with `REPOSITORY_MANIFEST.md` | Example: `C:\workspace\GCS` |
| P4 | Project MASTER inbox directory exists | Registered `master_inbox` must already exist (Universe never creates it) |
| P5 | Project `.ai/universe/` state root exists | Installer/README root only is enough before seed assets |

### Inbox path contract (must match registration)

```text
default / canonical:     .ai/inbox/MASTER
allowed under prefix:    .ai/inbox/<name>
project-owned alternate: .ai/master/inbox   (exact)
```

GCS live registration uses `.ai/master/inbox`. See
`docs/local-universe-service.md` (Register a project, Release and dispatch
boundary).

## Steps

### 1) Start local Universe service

```powershell
cd C:\workspace\universe
python tools/universe_server.py serve
```

Optional UI:

```powershell
python tools/universe_server.py serve --open-ui
```

**Pass when:**

- process prints `UNIVERSE_SERVICE_READY` (or equivalent READY status)
- `%LOCALAPPDATA%\Universe\server.json` has `endpoint`, `token`, `pid`, `database`
- `GET {endpoint}/health` returns `status: READY`

**Evidence fields:**

```text
endpoint
universe_id
database
mode_contract.mode = CONDUCTOR
```

### 2) Connect project (if not already registered)

```powershell
python tools/universe_server.py register `
  --project-id GCS `
  --project-root C:\workspace\GCS
```

Or UI: **Connect project**.

**Pass when:**

- `GET /v1/projects` includes `project_id: GCS`
- `project_root` points at the real checkout
- `refs.master_inbox` is an allow-listed path and the directory exists

**Fail when:**

- owner/manifest mismatch
- master inbox path missing or outside allow-list

### 3) Project Master surface ready

UI:

1. Select project **GCS**
2. Composer `+` → **Call GCS Master** (prepares resident Master session / bridge)

API checks:

```text
GET /v1/projects/GCS/master-bridge
  -> bridge present (REGISTERED or AVAILABLE)
```

**Pass when:** bridge is registered or available, or room-only path is explicit
and messages still persist in the Project Room.

**Note:** Room conversation is not a dispatch inbox write. Dispatch delivery is
a separate APPROVED mutation.

### 4) Seed discovery dispatch (queue → deliver)

Create (idempotent if already present):

```text
POST /v1/projects/GCS/discovery-dispatch
  body: {}
```

UI equivalent: **Request Project Map** / discovery preparation.

Deliver:

```text
POST /v1/dispatches/{dispatch_id}/deliver
  body: { "approval": "APPROVED" }
```

UI equivalent: Activity → **Deliver** on QUEUED dispatch.

**Pass when:**

- dispatch status becomes `DELIVERED`
- file exists: `{project_root}/{master_inbox}/dispatch_{id}.json`
- event chain includes `QUEUED` then `DELIVERED`

**Fail when:**

- `DISPATCH_DELIVERY_BLOCKED` (inbox missing or path not allow-listed)
- approval missing (`DISPATCH_DELIVERY_APPROVAL_REQUIRED`)

### 5) Publish Project Seed assets

Universe may already hold a current Seed in its store. Publishing to the Project
filesystem uses the seed-asset proposal path:

```text
GET  /v1/projects/GCS/seed-asset-proposal
POST /v1/projects/GCS/seed-asset-proposal/apply
  body: {
    "approval": "APPROVED",
    "proposal_id": "<from GET>",
    "proposal_digest": "<from GET>"
  }
```

**Pass when:** Project root contains exact asset set under `.ai/universe/`:

```text
.ai/universe/bindings.json
.ai/universe/documents.json
.ai/universe/functional-graph.json
.ai/universe/implementation-graph.json
.ai/universe/manifest.json
```

and apply returns `PROJECT_SEED_ASSET_APPLICATION_DELIVERED` (or equivalent
success status with host mutation receipts).

**Fail when:**

- proposal stale (`PROJECT_SEED_ASSET_APPROVAL_STALE`)
- Project Master Host / mutation gateway blocked

### 6) Close dispatch lifecycle

Ordered transitions only:

```text
QUEUED -> DELIVERED -> ACKNOWLEDGED -> STARTED -> COMPLETED | BLOCKED
```

```text
POST /v1/dispatches/{dispatch_id}/acknowledge
  body: { "evidence_ref": "<inbox or host evidence>" }

POST /v1/dispatches/{dispatch_id}/start
  body: { "evidence_ref": "<seed-apply or task evidence>" }

POST /v1/dispatches/{dispatch_id}/result
  body: {
    "status": "COMPLETED",
    "summary": "<one-line result>",
    "evidence_refs": ["<inbox file>", "<proposal ref>", ".ai/universe/manifest.json"],
    "outputs": { "published_assets": [ ... ] }
  }
```

**Pass when:**

- dispatch status `COMPLETED`
- `result_packet` present with matching status
- events include `ACKNOWLEDGED`, `STARTED`, `COMPLETED`

### 7) Optional: Fresh Composition Master handoff

When a Fresh Project Composition adoption already exists:

```text
POST /v1/projects/GCS/master-handoffs
  body: {
    "source": {
      "kind": "FRESH_PROJECT_COMPOSITION",
      "adoption_id": "<adoption_id>"
    },
    "purpose": "Deliver adopted Fresh Project plan to Project Master"
  }

POST /v1/projects/GCS/master-handoffs/{handoff_id}/deliver
  body: { "approval": "DELIVER" }
```

**Pass when:**

- propose → `PROPOSAL_ONLY` then deliver → `DELIVERED_TO_MASTER`
- wrong approval (`ADOPTED`) is rejected
- repeat deliver is idempotent (`ALREADY_DELIVERED`)

### 8) Observation surfaces (no execution)

| Surface | Pass check |
|---------|------------|
| Projection | `GET /v1/projects/GCS/projection` returns nodes/documents |
| Todo work map | `GET /v1/todos` lists project todos; user can edit without creating Task Frames |
| Activity | dispatch events + room/handoff evidence visible |
| Seed | `GET /v1/projects/GCS/seed` returns current seed |

Todo remains planning-only: view, organize, instruct. It is not a Master queue
or Task Frame start path.

## Out of scope (must not claim)

- Project source mutation beyond receipt-aware seed asset apply
- Authority, Write Scope, or Execution Assignment creation by Universe
- Task Frame execution permission
- Packaging (tray/autostart/installer)
- Memory RAG / Experience promotion

## Re-run matrix

| Step | Fresh environment | Existing live GCS Universe |
|------|-------------------|----------------------------|
| 1 Start service | Required | Required if stopped |
| 2 Register | Required | Skip if already connected |
| 3 Master surface | Required | Call Master or use existing bridge |
| 4 Discovery dispatch | Create + deliver | Deliver if QUEUED; skip if already COMPLETED |
| 5 Seed assets | Apply proposal | Skip if five JSON assets already present |
| 6 Result packet | Required to close | Skip if COMPLETED |
| 7 Handoff | Optional | Optional |
| 8 Observe | Always | Always |

## Live evidence snapshot (reference run)

Captured 2026-07-31 on the local Host after this scenario line was exercised:

```text
universe_id:     2b2335bd-5a4e-4a61-bcf5-5a38f3627a42
service:         READY (loopback; endpoint from %LOCALAPPDATA%\Universe\server.json)
project:         GCS
master_inbox:    .ai/master/inbox
dispatch_id:     dispatch_a0f30ce664e93ef8426bbedb
dispatch_title:  Prepare Universe project seed
dispatch_status: COMPLETED
dispatch_events: QUEUED -> DELIVERED -> ACKNOWLEDGED -> STARTED -> COMPLETED
inbox_file:      .ai/master/inbox/dispatch_a0f30ce664e93ef8426bbedb.json
seed_id:         gcs-context-map-20260727-r2
published:       .ai/universe/{bindings,documents,functional-graph,implementation-graph,manifest}.json
handoff_id:      handoff_16eae6f0e8aca19b8aa6ab48
handoff_state:   DELIVERED_TO_MASTER (FRESH_PROJECT_COMPOSITION)
todos:           5 (planning work map)
```

This snapshot is evidence that the line can complete. It is not a universal
health score and does not grant authority.

## Pass / fail summary

**Scenario PASS** only when all required steps that were attempted in this run
meet their step Pass criteria and no required step remains blocked on inbox
path, approval, or seed-asset apply.

**Scenario FAIL** if any required step ends in a blocking error above, or if
dispatch remains `QUEUED` after approved deliver while inbox path is valid.

## Automated smoke harness

```powershell
# Isolated in-process product line (CI-safe)
python tools/universe_e2e_smoke.py run

# Observe the running local service from %LOCALAPPDATA%\Universe\server.json
python tools/universe_e2e_smoke.py check
python tools/universe_e2e_smoke.py check --json

# Unit tests for the harness
python -m pytest tests/test_universe_e2e_product_scenario.py -q
```

`run` closes seed discovery dispatch through COMPLETED without Host seed-asset
apply (that Host path remains a live `check` criterion). `check` requires a
READY local service and reports PASS/FAIL per step for the GCS line.

## Fresh-clone verification companion

Before a project Master or Task Frame is prepared for a fresh clone, callers
may run the read-only install preflight:

```powershell
python tools/project_install_flow.py preflight `
  --project-root C:\workspace\GCS `
  --project-id GCS `
  --source-commit <full-ai-career-commit>
```

The result must be `PROJECT_INSTALL_PLAN_READY`, with `state: PLAN_READY` and
`candidate_execution: FORBIDDEN`. The command does not create `.ai`, start a
Runtime, or invoke a provider. `PROJECT_STANDALONE` is an explicit opt-in;
the default is `UNIVERSE_ATTACHED` with `prefer_boot: HOST`.

The caller then passes the exact `plan_digest` to a lifecycle adapter. The
adapter, not Universe, performs `OS_INSTALL`/`OS_UPDATE`. The adapter must
return the exact target, operation, install mode, immutable `ai-career`
source commit, managed paths, and `READY_FOR_BOOT`. Universe verifies the
installed `.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json`, the
required companion artifacts, the live source commit, and preservation of
pre-existing project files before reporting `PROJECT_INSTALL_READY_FOR_BOOT`.

Any missing artifact, stale plan, partial `.ai`, source mismatch, unmanaged
local-file change, or adapter response that merely claims READY is a blocked
result. No provider credentials or network access are part of this scenario.

## Related documents

- `docs/local-universe-service.md` — service, register, inbox, dispatch APIs
- `docs/universe-design-and-bench-flow.md` — product design flow
- `docs/project-projection-contract.md` — projection rebuild rules
- `docs/universe-worklist-merged.md` — backlog status for this line
