# Universe Local Service

The Universe local service is an application process. It is not the ai-career
Session Boot executor and it does not create project authority or execution
assignments.

## Responsibilities

- listen only on a loopback address;
- maintain the Universe project registry in SQLite;
- accept one-time project registration and later refreshes;
- retain append-only project observation events;
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

## Next boundary

OS_INSTALL integration should create a connection candidate and display it to
the user. Only the approved registration request is sent to this service.
Installation must not silently attach every discovered project.
