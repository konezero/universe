# Session Broker Host

Universe owns goals, rooms, policy, and durable product records. The session broker owns live provider-session handles so a Universe HTTP server restart does not discard a resumed Claude, Codex, or Grok connection.

## Current vertical slice

- `SessionBrokerClient` discovers or starts one authenticated loopback broker process.
- The broker keeps one resident provider host per provider chat and exact resume coordinate.
- Each provider chat receives an isolated SQLite session store. Coordinates from one provider cannot overwrite another provider's host metadata.
- Universe meeting turns cross the broker IPC route instead of using the HTTP server's process-local provider host.
- Runtime liveness (`LIVE` or `UNKNOWN`) and persistence (`SAVED`) are reported as separate axes.
- Provider errors retain their concrete error code across the IPC boundary.

## Ownership boundary

The broker is a provider-handle host, not yet the final TerminalSession object broker. It does not claim PTY, process, Anchor, authority, or BOOT ownership. The adopted Goal work plan adds those capabilities incrementally with evidence-backed liveness and explicit lifecycle states.

## Process lifecycle

The client reads `session-broker-state.json`, validates the authenticated `/v1/status` endpoint, and reuses the broker when it is healthy. If no healthy broker exists, it launches `session_broker_host.py serve` as a detached process and waits for the state file. `/v1/turn` sends one bounded prompt to an exact provider-session descriptor.

The broker's lifetime is independent of the Universe HTTP server. Restarting Universe may replace the server PID while the broker PID and resident provider handles remain stable.

## Verification

Tests cover resident-host reuse, replacement after a resume-coordinate change, per-chat database isolation, authenticated IPC, exact provider error propagation, meeting-room broker routing, and Claude canonical resume-coordinate normalization.
