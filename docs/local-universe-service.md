# Universe Local Service

The Universe local service is an application process. It is not the ai-career
Session Boot executor and it does not create project authority or execution
assignments.

## Operating Mode contract

The canonical operating coordinate is `Mode=UNIVERSE, Role=CONDUCTOR`.
`Universe Mode` and `Conductor Mode` are project-local aliases for that same
coordinate. The service validates the repository Mode Registry before listening.

`MASTER` remains separate and is required for Release DB installation, update,
Mode Registry mutation, and Universe policy lifecycle changes. Neither Mode
grants authority or permission to mutate an attached project. See
`docs/universe-mode-contract.md`.

## Responsibilities

- listen only on a loopback address;
- maintain the Universe project registry in SQLite;
- accept one-time project registration and later refreshes;
- retain append-only project observation events;
- queue Master-owned Project Seed discovery and verify published `.ai/universe` Seed assets;
- persist current Project Projections and missing-connection candidates;
- create read-only `.ai/universe/documents` derivation proposals;
- verify and retain immutable ai-career Release artifacts;
- create read-only Project release install/update proposals;
- queue durable Project dispatches and retain their complete event/result timeline;
- provide compact project summaries to a UI or LLM client.

Each attached project remains responsible for its own source mutation,
validation, Execution Guard, and evidence. Universe stores project roots and
evidence references; it does not merge or rewrite project Runtime databases.

## Start the service

```powershell
python tools/universe_server.py serve --open-ui
```

The process selects a free loopback port by default and writes its endpoint,
token, PID, and database location to:

```text
%LOCALAPPDATA%\Universe\server.json
```

The SQLite database defaults to:

```text
%LOCALAPPDATA%\Universe\universe.sqlite3
```

The database receives one immutable `universe_id` UUID when it is created.
Reopening the same database preserves that ID; creating another Universe
database creates another ID. The value is returned by `/health`, the service
startup result, and `server.json` so local clients can distinguish Universe
instances. It does not create authority, network identity proof, or discovery.

Use `--database`, `--state-file`, `--port`, or `--token` to override these
values. Non-loopback listen addresses are rejected.

## Register a project

With the service running:

```powershell
python tools/universe_server.py register `
  --project-id GCS `
  --project-root C:\workspace\GCS
```

Registration requires `REPOSITORY_MANIFEST.md`. When a Mode Registry exists,
its owner must match the supplied project ID. Project references must remain
relative to the registered project root.

Registering the same ID and root again refreshes metadata instead of creating
a duplicate connection.

List attached projects:

```powershell
python tools/universe_server.py list
```

## HTTP API

`GET /health` is public and returns only service readiness. Every `/v1` route
requires the local Bearer token from the state file.

```text
POST   /v1/projects/register
GET    /v1/projects
GET    /v1/projects/{project_id}
DELETE /v1/projects/{project_id}
POST   /v1/projects/{project_id}/events
GET    /v1/projects/{project_id}/events
POST   /v1/projects/{project_id}/seed
GET    /v1/projects/{project_id}/seed
GET    /v1/templates/project-seed
POST   /v1/projects/{project_id}/discovery-dispatch
POST   /v1/projects/{project_id}/sync
POST   /v1/projects/{project_id}/projection
GET    /v1/projects/{project_id}/projection
POST   /v1/projects/{project_id}/document-incorporation-proposals
GET    /v1/releases
GET    /v1/releases/{release_id}
POST   /v1/releases/import
GET    /v1/projects/{project_id}/release-proposals
POST   /v1/projects/{project_id}/release-proposals
GET    /v1/projects/{project_id}/dispatches
POST   /v1/projects/{project_id}/dispatches
GET    /v1/dispatches/{dispatch_id}
POST   /v1/dispatches/{dispatch_id}/deliver
POST   /v1/dispatches/{dispatch_id}/wake
POST   /v1/dispatches/{dispatch_id}/acknowledge
POST   /v1/dispatches/{dispatch_id}/start
POST   /v1/dispatches/{dispatch_id}/result
```

An event body has this shape:

```json
{
  "event_id": "gcs-status-001",
  "event_type": "STATUS_OBSERVED",
  "payload": {
    "repository_runtime": "VERIFIED"
  }
}
```

Event IDs are idempotency keys. Repeating the same event is accepted; reusing
an ID for different content is rejected.

## Release and dispatch boundary

Release import verifies both database and manifest, then copies the immutable
pair into content-addressed Universe storage. Import and Project release
proposal creation require the explicit request coordinate `mode: MASTER`.
Proposal generation reads the attached Project only to calculate actions and
collisions. It never applies those actions.

Dispatch creation is durable and idempotent. Delivery to the Project-owned
`.ai/inbox/MASTER` path is a separate mutation and requires
`{"approval": "APPROVED"}` on that request. The explicit Deliver action in the
desktop UI supplies this value. The Project must already expose the inbox path;
Universe does not create it. Wake adapters record receipts but do not advance
dispatch status.

The ordered lifecycle is:

```text
QUEUED -> DELIVERED -> ACKNOWLEDGED -> STARTED -> COMPLETED | BLOCKED
```

Every compare-and-set transition and event append occurs in one SQLite
transaction. A stale concurrent transition returns `DISPATCH_STATE_CHANGED`
and appends no event. The desktop UI shows release proposal state, collisions,
dispatch evidence, wake receipts, and the final Result Packet.

## Desktop UI

The UI is served by the same loopback process and contains no embedded access
token. `--open-ui` opens a fragment URL once; the page moves the token into
session storage and removes it from the visible URL.

The first slice supports:

- explicit local project connection;
- Functional, Implementation, Documents, and Future graph views;
- Project Seed preparation Dispatch creation and automatic asset sync on refresh;
- immutable Release DB import in MASTER;
- read-only Project install/update planning with collision visibility;
- durable MASTER dispatch creation and approved Inbox delivery;
- dispatch event and Result Packet inspection.

Release proposals never apply project files from the browser. The proposal
digest and plan are handed to the Project Host for approval, guarded writes,
validate/status, and completion evidence.

## Project Seed and Projection

Universe queues a Master-owned, read-only discovery request. The Project Master
then publishes the canonical Seed bundle under `.ai/universe/`: a manifest,
functional graph, implementation graph, functional-to-implementation bindings,
and document catalog. Universe verifies the manifest and its file digests,
reconstructs the Seed, and stores no raw project source content.

Building a Projection returns the current node/edge/document map, structural
gaps, and user-selectable predicted paths. The UI places component documents
next to their linked system nodes and Project-wide documents next to the main
Project node. Project summaries and working-reference rules are shown in the
main-node inspector. A newer Project Seed invalidates the prior current
Projection until it is rebuilt.

The document-incorporation endpoint returns a proposal for the Project-owned
`.ai/universe/documents/` hierarchy. It never creates a directory or moves a
legacy document. The Project Master must approve and execute canonical document
derivation through its own Runtime.
See `docs/project-projection-contract.md`.

## Connection and authentication boundary

The local service exposes a connection profile instead of making callers depend
on a hard-coded local endpoint. The current implementation provides exactly one
active combination:

```yaml
interface_kind: HTTP_API
connection_kind: LOCAL
transport_kind: HTTP
auth_type: NONE
credential_ref: NONE
capabilities:
  read: true
  append: true
  realtime: true
  bidirectional: true
  durable: true
```

The axes remain independent:

- interface: `HTTP_API`, with `MCP` and `CLI` reserved
- connection: `LOCAL`, with `REMOTE` and `PEER` reserved
- transport: `HTTP`, with `GIT` and `P2P` reserved
- authentication: `NONE` for loopback local HTTP, with `OAUTH2` and `PEER_KEY` reserved for future remote or peer adapters

Only HTTP addresses are currently validated as HTTP URLs. Git and P2P address
formats remain Adapter-owned so this initial contract does not force future
connections into an HTTP-specific shape.

MCP is an interface contract, not a transport. A future MCP server adapter may
expose Universe operations to an LLM, while an MCP client adapter may invoke an
external project interface over whichever network transport that connection
selects.

Reserved values do not start remote synchronization, OAuth flows, peer
discovery, key exchange, Git exchange, or MCP tools. Requesting an unimplemented
authentication provider fails explicitly with `AUTH_PROVIDER_NOT_IMPLEMENTED`.

The local service binds only to a literal loopback address and accepts local
HTTP requests without a browser or API token. That exemption applies only to
this local connection profile; remote and peer adapters must declare their own
credential reference and authentication provider when implemented.

## Next boundary

OS_INSTALL integration should create a connection candidate and display it to
the user. Only the approved registration request is sent to this service.
Installation must not silently attach every discovered project.
