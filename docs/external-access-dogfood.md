# External Access Dogfood

Status: first Internet transport

Universe keeps its canonical service and the paired-browser Gateway on the PC.
The Internet slice adds only a resident outbound SSH reverse tunnel:

```text
mobile browser
  -> HTTPS on the trusted server
  -> server reverse proxy
  -> 127.0.0.1:<remote port> on the server
  -> outbound SSH tunnel owned by the Universe PC
  -> 127.0.0.1:<paired Gateway port> on the PC
  -> 127.0.0.1:<Universe service port>
```

The mobile browser never receives the local Universe API token, provider
credentials, SSH private key, Runtime receipts, or a generic proxy target.

## Server Prerequisites

The first dogfood Host needs:

- one DNS name whose HTTPS certificate terminates on the server;
- OpenSSH server with remote TCP forwarding enabled;
- one dedicated key-only SSH account;
- one unprivileged loopback port, for example `18443`;
- Caddy, Nginx, or an equivalent reverse proxy that preserves streaming.

The reverse tunnel requests only this listener:

```text
127.0.0.1:18443
```

It never requests `0.0.0.0`. Keep `GatewayPorts no` on the SSH server. Restrict
the dedicated public key to remote forwarding and the exact listener with an
`authorized_keys` prefix equivalent to:

```text
restrict,port-forwarding,permitlisten="127.0.0.1:18443"
```

Append the public key after those options. The server account must not be reused
for application administration.

Example Caddy route:

```caddyfile
universe.example.com {
  reverse_proxy 127.0.0.1:18443 {
    flush_interval -1
  }
}
```

The reverse proxy must preserve `text/event-stream`, avoid response buffering,
and allow long-lived Room and progress streams.

## PC Preparation

Create a dedicated key and register the server host key before using the
non-interactive connector:

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\universe_ed25519"
ssh -p 22 universe-tunnel@server.example.com
```

The second command is an operator verification step. Confirm the fingerprint
through an independent server source before accepting it, then exit. Universe
uses `BatchMode=yes` and `StrictHostKeyChecking=yes`; it will not open a hidden
password or host-key prompt.

## Universe Settings

Open `Settings -> Mobile access` and choose `Internet through my server`.
Provide:

- the public HTTPS origin;
- SSH server, port, and dedicated user;
- the server loopback port;
- the private key path;
- the known-hosts path.

Starting access performs these steps:

1. start the paired-browser Gateway on PC loopback only;
2. start one hidden SSH connector process;
3. require the remote forward to be accepted before reporting `READY`;
4. persist only connector configuration and process state locally;
5. create a pairing code only after both layers are ready.

The Windows tray can later restart the saved configuration without exposing a
console window. `Stop` ends the SSH child first, then the local Gateway.

## Dogfood Verification

1. Open the public HTTPS URL on the phone.
2. Enter the one-time pairing code.
3. Approve the browser from the local desktop Settings surface.
4. Confirm the graph, Settings-safe views, Room streaming, and message send.
5. Revoke the browser and confirm the next request returns `401`.
6. Stop remote access and confirm the public route no longer reaches Universe.
7. Restart from the tray and confirm the saved connector returns to `READY`.

`READY` proves that the local SSH process accepted the forward. The first
dogfood pass must still verify the public HTTPS route end to end because DNS,
certificate, reverse-proxy, and server firewall state remain outside Universe.

## Cookie-less clients (automation-driven browsers, bare HTTP)

A client that cannot persist `Set-Cookie` across the pairing redirect uses the
header path instead:

1. `POST /pair/request` with `Accept: application/json` (JSON or form body:
   `code`, `device_name`) -> `200 {pairing_id, request_token, expires_at}`.
2. Poll `GET /pair/status?id=<pairing_id>` with header
   `X-Request-Token: <request_token>` until `state` is `CONSUMED`; that
   response body carries `session_token`.
3. Send every later request with `X-Universe-Session: <session_token>` (same
   value the cookie would hold). The pairing token is single-use, short-lived,
   and grants UI access only — no execution authority.

If a request carries both a pairing cookie and `X-Request-Token` and they
disagree, `/pair/status` returns `400 REMOTE_PAIRING_TOKEN_CONFLICT`.
