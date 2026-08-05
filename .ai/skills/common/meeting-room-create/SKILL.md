---
name: meeting-room-create
description: Create a multi-model Meeting room hosted by Conductor; user may interrupt. Function-first multi-room S3.
---

# Meeting Room Create

Invocation class: `UNIVERSE_PRODUCT_ROOM`

Creates a **MEETING** chat room via the local Universe service. The Conductor is
the host. Model slots receive session bindings (push/inject refs when known).
The user may post interrupt messages. Meeting output is conversation + optional
summary artifact later — **not** Execution Guard authority.

## Route

```text
POST /v1/rooms
{
  "room_type": "MEETING",
  "title": "API design debate",
  "topic": "...",
  "project_id": "<optional project id>",
  "conductor_provider": "GROK",
  "conductor_session_ref": "<optional>",
  "models": [
    {"provider": "GROK", "display_name": "Grok", "session_ref": "..."},
    {"provider": "CLAUDE", "display_name": "Claude", "session_ref": "..."}
  ]
}
```

Loopback Universe service must be running. Use the service token only if the
deployment requires Bearer on `/v1` (loopback often allows unauthenticated local).

## After create

- `GET /v1/rooms/<room_id>` — snapshot, bindings, bridge_line, messages
- `POST /v1/rooms/<room_id>/messages` — user interrupt (`author_role: USER`)
- `POST /v1/sessions/inject` — push more provider session refs into slots
- `GET /v1/rooms/<room_id>/stream` — SSE events

## Non-goals

- Automatic Task Frame / Boss execution from meeting consensus
- Hidden reasoning streaming
- Authority or write scope from meeting alone
