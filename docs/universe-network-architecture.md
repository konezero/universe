# Universe Network Architecture

Status: ACTIVE DESIGN AND LAN DOGFOOD IMPLEMENTATION
Scope: access to the existing local Universe HTTP service
Active delivery target: open the local Universe Web UI from a paired mobile browser

## Purpose

Universe already runs its canonical application as a local HTTP service. The
first network feature does not create a second mobile application backend,
remote command protocol, cloud Runtime, or separate mobile state model. It
only makes the existing local Web page reachable from an approved mobile
browser.

```text
Local use
  browser -> http://127.0.0.1:<port>

Remote mobile use
  mobile browser -> paired remote access path
                 -> http://127.0.0.1:<port> on the user's PC
```

The HTML, CSS, JavaScript, HTTP API, Server-Sent Events, Project Rooms,
Universe Conductor, Project Master connections, SQLite state, and local
execution behavior remain the same on both paths.

## Fixed Decisions

1. The existing local Universe HTTP server remains the single application
   server.
2. The remote mobile browser loads that server's existing Web UI.
3. The first remote slice adds access and pairing only.
4. Existing HTTP request and SSE behavior is forwarded without semantic
   remapping.
5. No separate mobile API, remote message schema, remote database, or cloud
   execution layer is introduced.
6. The local server continues to bind to loopback. A desktop connector opens
   the outbound remote path.
7. Pairing controls which browser may use that path. OAuth is not required for
   the first personal-use slice.
8. Remote access does not create Mode, Current Anchor, Provider permission,
   Career Authority, Assignment, Execution Guard permission, or Project write
   capability.
9. When the desktop or local Universe service is offline, the remote page
   reports `HOST_OFFLINE`; it does not queue work for later execution.
10. Tailscale may remain an optional deployment adapter, not a dependency.

## Product Topology

```text
Mobile browser
  -> HTTPS remote URL
  -> Universe access gateway
  -> desktop-owned outbound tunnel
  -> fixed local origin: http://127.0.0.1:<Universe port>
  -> existing Universe HTTP handler
  -> existing Universe application and local execution paths
```

The access gateway and tunnel are network plumbing. They do not understand or
reimplement Project Rooms, Todo, Memory, Bench, Seed, Projection, Provider
sessions, or Task Frames.

## Existing Local Path

The local profile remains authoritative:

```yaml
interface_kind: HTTP_API
connection_kind: LOCAL
transport_kind: HTTP
auth_type: NONE
address_scope: LOOPBACK
```

Local browser behavior remains unchanged when remote access is disabled or
unavailable.

## Remote Access Path

The remote profile is a design target:

```yaml
interface_kind: HTTP_API
connection_kind: REMOTE
transport_kind: HTTP_TUNNEL
auth_type: DEVICE_PAIRING
upstream_origin: http://127.0.0.1:<Universe port>
capabilities:
  web_ui: true
  http_api: true
  sse: true
  arbitrary_upstream: false
```

`DEVICE_PAIRING` is implemented by the LAN dogfood gateway. The Internet-facing
`HTTP_TUNNEL` connector remains a deployment adapter and must fail closed until
a trusted HTTPS gateway is configured.

## LAN Dogfood Gateway

The first executable slice is intentionally narrower than the final Internet
tunnel:

```text
paired mobile browser on the same network
  -> LAN_DOGFOOD_GATEWAY
  -> fixed loopback Universe origin
```

- `tools/universe_remote_gateway.py` is a separate process; the canonical
  Universe service still listens only on loopback.
- The gateway accepts no arbitrary upstream and strips browser Authorization,
  Cookie, Host, and hop-by-hop headers before forwarding.
- A one-time, expiring code creates an approval request. Only the local desktop
  settings surface may approve, deny, or revoke a device.
- The browser receives an HttpOnly, SameSite=Strict device session. HTTPS adds
  the Secure attribute; LAN HTTP is dogfood-only.
- The same SPA, API, and SSE paths are streamed through the fixed gateway.
- Stopping the local service yields `HOST_OFFLINE`; requests are not queued.
- Windows tray controls can start, inspect, open, and stop the gateway.
- Automatic LAN start binds one detected RFC1918 private IPv4 address, never
  `0.0.0.0` or a public address. When no private IPv4 address exists, start
  fails with `SAFE_LAN_ADDRESS_UNAVAILABLE`; that Host needs the later trusted
  HTTPS outbound connector rather than an inbound HTTP listener.

This slice proves access and pairing. It is not a substitute for TLS, an
outbound connector, or a deployed Internet rendezvous service.

## Components

### Local Universe HTTP service

- remains bound to a literal loopback address;
- serves the canonical responsive Web UI;
- owns all existing API and SSE behavior;
- owns the Universe database and application state;
- has no public listener added for remote access.

### Desktop access connector

- starts with the Universe tray or service process;
- knows one fixed local Universe origin;
- opens an outbound persistent connection to the access gateway;
- forwards only requests for that fixed origin;
- supports ordinary HTTP responses and long-lived SSE streams;
- reports local Host and service availability;
- never accepts an arbitrary destination URL from the browser or gateway.

### Universe access gateway

- exposes one HTTPS URL for the paired Universe instance;
- performs pairing rendezvous and remote browser session checks;
- routes approved HTTP traffic through the matching desktop tunnel;
- preserves HTTP method, path, query, selected headers, status, and body;
- disables buffering for SSE responses;
- rate-limits pairing and browser requests;
- cannot create work or interpret application responses as permission.

### Mobile browser

- opens the remote HTTPS URL;
- completes one pairing flow;
- receives the existing Universe Web UI from the local PC;
- uses the same UI controls and API behavior as a desktop browser;
- stores only the remote browser session credential;
- never receives the local API token, Provider credentials, Runtime endpoint
  token, or mutation receipt.

## Request Flow

### Page load

```text
GET / from mobile
  -> gateway verifies paired browser session
  -> desktop tunnel
  -> GET / on local Universe server
  -> local HTML response
  -> tunnel
  -> mobile browser
```

Static assets follow the same route. The gateway must not host a second,
independently versioned copy of the SPA in the first release.

### API request

```text
mobile fetch('/v1/...')
  -> same remote HTTPS origin
  -> gateway session check
  -> desktop tunnel
  -> existing local /v1/... handler
  -> unchanged application result
```

The gateway is an allow-listed reverse path to one fixed service, not a generic
HTTP proxy.

### Streaming response

```text
mobile EventSource('/v1/projects/<id>/room/stream')
  -> gateway
  -> desktop tunnel
  -> existing local SSE endpoint
  -> incremental events forwarded without buffering
```

Reconnect uses the existing browser and application SSE semantics. The gateway
may carry connection metadata, but it does not become the canonical room
history store.

### Host offline

```text
desktop connector absent or local health probe fails
  -> gateway returns HOST_OFFLINE
  -> remote UI disables new submissions
  -> no request is queued for delayed execution
```

## Pairing

### First connection

1. The desktop user enables remote access.
2. Universe creates a random, single-use pairing record with a short expiry.
3. The desktop displays a QR code and optional human-readable code.
4. The mobile opens the encoded remote HTTPS URL.
5. The gateway routes the pairing request to the connected desktop.
6. The desktop shows the requesting device and requires explicit confirmation.
7. The gateway issues a browser session bound to that paired device.
8. The pairing record is consumed and cannot be reused.

The QR must not contain the local Universe API token, Provider credential,
Project credential, Runtime endpoint token, or a reusable bearer secret.

### Later connections

The browser reuses its paired session until expiry or revocation. The desktop
Settings UI lists paired devices, last-seen state, and a revoke action. Losing
browser state requires a new pairing.

### Browser credential

The first implementation should use an HTTPS-only, `HttpOnly`, restrictive
same-site browser credential or an equivalent browser-bound mechanism. It must
not place a durable secret in URL query parameters or application logs.

## Authentication and Runtime Authority

Pairing answers only this question:

```text
May this browser reach this user's local Universe Web UI?
```

It does not answer:

```text
May this Provider tool call run?
May this Project be modified?
Is this Mode current?
Is an Assignment active?
Has Execution Guard permitted this mutation?
```

Those decisions remain inside the existing local Universe, Provider-session,
Project Master, Task Frame, and installed Project Runtime boundaries.

## Header and Origin Boundary

The gateway and connector must apply a fixed forwarding policy:

- remove hop-by-hop headers;
- set the local upstream Host to the fixed loopback service;
- preserve an explicit original HTTPS origin for application checks;
- reject attempts to select another upstream Host or port;
- reject Web proxy methods such as arbitrary `CONNECT`;
- set bounded request and response body sizes;
- preserve SSE content type and disable response buffering;
- define trusted proxy headers once and strip caller-supplied copies;
- apply CSRF protection to state-changing browser requests;
- set a restrictive Content Security Policy for the remote origin.

The local application must never trust a caller-provided forwarding header
unless it arrived through the authenticated desktop connector path.

## Persistence

| Data | Canonical owner | Gateway persistence |
|---|---|---|
| Universe Web assets | Local PC | None |
| Project and room state | Local Universe DB | None |
| Provider session references | Local Universe Host | None |
| Runtime tokens and receipts | Local Project Runtime | Never |
| Pairing record | Desktop + gateway | Expiring, single use |
| Paired-device session | Desktop + gateway | Revocable session metadata |
| Tunnel presence | Gateway | Ephemeral |
| HTTP/SSE payload | Local PC | Transit only |

The gateway must not create a second canonical application database. Gateway
logs exclude request bodies, response bodies, credentials, local paths,
prompts, and Provider output.

## Trust Choice for the First Gateway

An ordinary HTTPS reverse gateway terminates browser TLS and can technically
observe forwarded HTTP content. The first implementation must state this
clearly rather than claiming end-to-end secrecy.

Two deployment levels are allowed:

```text
PERSONAL_TRUSTED_GATEWAY
  - user-operated or explicitly trusted gateway
  - HTTPS browser connection
  - pairing and strict proxy allow-list

END_TO_END_TUNNEL
  - later hardened transport
  - gateway routes encrypted traffic without application visibility
  - requires a reviewed certificate or application-encryption design
```

No custom cryptographic protocol should be invented. The hardened path must use
maintained, reviewed libraries and a separately reviewed threat model.

## Failure and Recovery

- Pairing expiry: reject and create a new pairing record.
- Reused pairing record: reject.
- Revoked device: terminate its browser session and reject reconnection.
- Desktop connector loss: return `HOST_OFFLINE`.
- Local Universe restart: connector waits for local health, then reconnects.
- Gateway restart: desktop reconnects outward; browser refreshes its session.
- SSE interruption: browser reconnects through the existing stream contract.
- Duplicate state-changing HTTP request: existing endpoint idempotency remains
  authoritative; the gateway must not invent a success response.
- Unknown Host, path, method, or upstream: fail closed.

## Security Requirements

1. The desktop requires no public inbound port or fixed public IP.
2. The local Universe service remains loopback-only.
3. The connector forwards to one configured local origin only.
4. Remote browser access requires a valid paired-device session.
5. Pairing records are random, short-lived, single-use, and rate-limited.
6. Browser credentials are revocable and excluded from URLs and logs.
7. The gateway is not a generic reverse proxy or local-network proxy.
8. Existing local API tokens and Runtime secrets never leave the PC.
9. State-changing requests use origin and CSRF checks.
10. Request sizes, stream counts, idle times, and connection rates are bounded.
11. Remote enablement is opt-in and visible in desktop Settings.
12. The UI reports whether the connection is local, trusted-gateway remote, or
    a later end-to-end remote transport.

## Network Relationships Outside Remote UI

The remote browser tunnel is not the whole Universe ecosystem, but other
network relationships remain separate from it.

### Project to Universe

```text
Project Task Frame / Project Master
  -> Project-to-Universe queue
  -> local Universe ingest and Bench aggregation
```

This remains a local project data boundary. Mobile access merely views and
operates the resulting local Universe UI.

### Universe to Career

```text
Universe reusable candidate
  -> Universe-to-Career queue
  -> Career Carrier
  -> Career Conductor review
```

Carrier delivery is not routed through the remote browser tunnel.

### Career release to Universe and Projects

```text
Career Release DB
  -> Universe release catalog/update
  -> approved Project OS_INSTALL / OS_UPDATE
```

Release provenance and lifecycle receipts remain independent of Web access.

### Universe to Universe

Future Universe peer exchange may use a different `PEER` connection profile.
It must not reuse a paired browser session as peer identity and is excluded
from the first remote release.

## Implementation Sequence

### Slice 1: Local proxy boundary

- define one fixed local upstream origin;
- put current browser HTTP calls behind an origin-neutral client boundary;
- verify the full SPA works unchanged through a local test reverse proxy;
- verify SSE, POST, errors, large responses, and reconnect behavior.

### Slice 2: Outbound tunnel and Host presence

- add a desktop connector that opens an outbound persistent tunnel;
- add a minimal access gateway that maps one remote URL to one connector;
- expose health and `HOST_OFFLINE` behavior;
- keep pairing disabled and use a development-only credential.

### Slice 3: Device pairing

- add expiring QR/code pairing;
- require desktop confirmation;
- issue and revoke paired-browser sessions;
- add Settings UI for remote status and paired devices.

### Slice 4: Complete Web UI pass-through

- forward the existing SPA, HTTP API, and SSE endpoints;
- add origin, CSRF, header, body-size, timeout, and rate-limit controls;
- run desktop and mobile viewport end-to-end tests through the gateway.

### Slice 5: Packaging and hardening

- start the connector with the Universe tray application;
- add gateway URL and remote enablement configuration;
- define trusted-gateway deployment and operational monitoring;
- separately design an end-to-end tunnel before claiming Relay confidentiality.

### Later

- optional OAuth for account recovery or multi-user access;
- optional Tailscale/private-overlay adapter;
- direct LAN adapter;
- end-to-end encrypted tunnel;
- P2P optimization;
- Universe peer exchange.

## Acceptance Criteria

1. The mobile browser opens the exact Web UI served by the local PC.
2. No second mobile backend or duplicate SPA deployment is required.
3. Existing local HTTP API and SSE behavior works without semantic changes.
4. The desktop has no public inbound port and keeps the local server on
   loopback.
5. A new mobile browser cannot connect without pairing and desktop approval.
6. Revocation blocks the next request from that browser.
7. Turning off the desktop or local service reports `HOST_OFFLINE` and does not
   queue work.
8. The gateway cannot proxy an arbitrary local address or port.
9. Local Provider, Project, Task Frame, and Runtime boundaries remain unchanged.
10. Local loopback use continues to work without the gateway.

## Non-Goals for the First Remote Release

- a native mobile application;
- a second mobile-specific Universe backend;
- a new remote command or agent protocol;
- cloud execution;
- generic remote desktop, shell, Git, or filesystem access;
- cloning Tailscale or implementing a general VPN;
- offline command execution;
- OAuth account administration;
- Universe-to-Universe federation.
