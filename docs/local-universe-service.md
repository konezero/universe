# Universe Local Service

The Universe local service is an application process. It is not the ai-career
Session Boot executor and it does not create project authority or execution
assignments.

## Responsibilities

- listen only on a loopback address;
- maintain the Universe project registry in SQLite;
- accept one-time project registration and later refreshes;
- retain append-only project observation events;
- validate reference-only Project Seeds;
- persist current Project Projections and missing-connection candidates;
- create read-only `docs/universe` incorporation proposals;
- provide compact project summaries to a UI or LLM client.

Each attached project remains responsible for its own source mutation,
validation, Execution Guard, and evidence. Universe stores project roots and
evidence references; it does not merge or rewrite project Runtime databases.

## Start the service

```powershell
python tools/universe_server.py serve
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
POST   /v1/projects/{project_id}/projection
GET    /v1/projects/{project_id}/projection
POST   /v1/projects/{project_id}/document-incorporation-proposals
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

## Project Seed and Projection

The Project initiates connection and submits a Project Seed containing only
metadata plus repository-relative file references and SHA-256 digests.
Universe validates those references against the registered root and stores no
raw file contents.

Building a Projection returns the current node/edge/document map, structural
gaps, and user-selectable predicted paths. A newer Project Seed invalidates
the prior current Projection until it is rebuilt.

The document-incorporation endpoint returns a proposal for the Project-owned
`docs/universe/` hierarchy. It never creates a directory or moves a document.
The Project must approve and execute that mutation through its own Runtime.
See `docs/project-projection-contract.md`.

## Connection and authentication boundary

The local service exposes a connection profile instead of making callers depend
on a hard-coded local endpoint. The current implementation provides exactly one
active combination:

```yaml
interface_kind: HTTP_API
connection_kind: LOCAL
transport_kind: HTTP
auth_type: LOCAL_TOKEN
credential_ref: server-state://token
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
- authentication: `LOCAL_TOKEN`, with `OAUTH2` and `PEER_KEY` reserved

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

Connection profiles carry only a `credential_ref`; they never contain the
credential itself. The prototype continues to keep the local token in the
protected local server state for compatibility. A desktop package can later
resolve `credential_ref` through Windows Credential Manager or macOS Keychain
without changing the transport interface.

## Next boundary

OS_INSTALL integration should create a connection candidate and display it to
the user. Only the approved registration request is sent to this service.
Installation must not silently attach every discovered project.
