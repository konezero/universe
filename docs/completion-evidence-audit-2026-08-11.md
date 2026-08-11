# Completion Evidence Audit - 2026-08-11

Status: authoritative reopening record for the 2026-08-10 through 2026-08-11
integration pass.

This audit separates source implementation, isolated contract tests, a running
Universe service, and a real browser product path. Passing one plane does not
prove the others.

## Incident

The page observed at `127.0.0.1:51234` was not the resident Universe product
service. It was an ad hoc smoke server started against a temporary SQLite
database and a fixed port. The temporary database was later removed while the
server process remained alive. Static assets still returned successfully, but
dynamic APIs reopened an empty database and the UI rendered unavailable state,
no projects, and no provider tail.

The external smoke helper considered a listening TCP port ready and terminated
only its shell child. It did not prove the product database, `/health`, core API
responses, browser console state, or process-tree cleanup. A static shell could
therefore be reported as a successful visual smoke while leaving an orphan
server behind.

The resident product service remained a separate process with its endpoint and
database recorded under the local Universe service state. Test and product
service identities must never be inferred from a familiar port or page shell.

## Reclassified Work

| Work item | Prior claim | Audited state | Evidence and missing exit condition |
| --- | --- | --- | --- |
| Desktop live visual smoke | Complete | **INVALIDATED / REOPENED** | The observed fixed-port server used temporary state and survived cleanup. Repeat against the resident service identity, verify core APIs and browser console, then prove cleanup for any test-owned listener. |
| Live Provider Session Room | Complete | **PARTIAL / REOPENED** | Provider output tail, redaction, cursors, Project Room delivery, and cancellation exist. The main composer still targets Universe Conductor or Project Room. A one-to-one Provider Session target with provider-native input is not yet a completed product path. |
| Session/Provider observatory | Complete | **PARTIAL** | Discovery, grouping, Anchor binding, and transient tail are implemented. Product-browser validation against the resident service and provider-specific long-running recovery remain open. |
| Session Boot executor supervision | Complete | **KEPT, WITH SEPARATE FOLLOW-UP** | Supervisor-owned executors have focused lifecycle coverage. The orphan was an ad hoc test server, not a managed Session Boot executor. Test-server ownership still needs the new smoke lifecycle gate. |
| Server modularization | In progress | **IN PROGRESS** | Connection, streaming, Memory execution, and Bench components are extracted. Session/Provider, storage, API/runtime/CLI, and remaining schema/bootstrap ownership are still in the monolith. |
| Memory and Bench extraction | Complete structurally | **KEPT, PRODUCT DOGFOOD OPEN** | Module and contract tests support the extraction. Scheduled real-provider, restart, quota, and browser runs remain separate completion evidence. |

## Functional Node Provenance

Universe startup does not synthesize feature nodes from source code.

1. Startup idempotently registers the `universe` repository and a discovered
   sibling `ai-career` repository as Project anchors.
2. Functional nodes enter storage only through an explicit Project Seed. The
   Seed contract requires its `nodes` array and source commit/reference.
3. Building a Project Projection copies the Seed nodes without inventing new
   functional nodes.
4. The map loads each current Projection and always expands those Seed nodes
   around the Project.

The current local database has a Projection only for GCS. Its displayed nodes
(`market-data`, `order-risk`, `runtime`, and `strategy`) came from the historical
GCS Project Seed, not from automatic Universe inference. Universe and Career are
auto-registered Project anchors but have no current functional-node Projection.

The UI must therefore label these items as **Project Seed nodes** and expose the
Seed ID, source reference, and source commit in the inspector.

## Completion Gate

A work item may be marked `DONE` only when every claimed plane has matching
evidence:

1. **Source:** the implementation exists and focused tests cover behavior, not
   only source-string presence.
2. **HTTP service:** a test-owned server uses port `0`, serves the SPA, returns
   `READY` from `/health`, and returns expected status values from core APIs.
3. **Identity:** the test records whether it used isolated temporary state or
   the resident product state. The two are never interchangeable.
4. **Lifecycle:** test-owned threads/listeners are shut down and verified absent
   before the test reports success. Fixed ports and shell-child-only cleanup are
   forbidden completion evidence.
5. **Browser:** product-browser completion uses the resident service endpoint,
   verifies the intended interaction, console state, responsive layout, and the
   backing API response.
6. **Provider:** a live-session claim requires provider-native input acceptance,
   incremental output, disconnect/restart behavior, and quota-state evidence for
   the provider combinations named by the claim.
7. **Todo reconciliation:** a failed later dogfood run reopens the source
   worklist immediately. The local Todo database is reconciled separately and
   cannot override source-backed failure evidence.

## Required Follow-up

- Keep the in-process HTTP lifecycle probe in the `smoke` tier.
- Replace source-string-only UI assertions with browser/API behavior tests for
  Provider Session selection and direct input.
- Implement a distinct `PROVIDER_SESSION` composer target. Until native input is
  available, it must fail closed rather than silently route to Conductor or
  Project Room queues.
- Run browser dogfood only against the endpoint read from resident service
  state; never reuse a fixed smoke port.
- Reconcile the local Todo DB with this reopening record through its normal Todo
  API, not by directly editing SQLite.
