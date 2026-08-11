# Runtime Executor Supervisor Boundary

The resident Universe Supervisor owns the lifecycle record for an adopted
Session Boot executor. A runtime executor is registered as a persistent Mode
session with `provider: RUNTIME`, exact process identity, loopback endpoint, and
a short-lived graceful-stop capability.

## Storage and restart behavior

- Process identity is durable and secret-free. Sensitive command arguments are
  fingerprinted before they enter the registry.
- The graceful-stop capability is protected at rest with Windows DPAPI. The
  plaintext value is returned only to the internal stop path and is never part
  of the registry projection or audit event.
- `adopt_runtime_executor` requires exact Host process evidence and rejects
  non-`RUNTIME` providers.
- A managed stop first transitions the lease to `STOP_AUTHORIZED`, requests
  the executor's authenticated loopback shutdown, and releases the lease only
  after Supervisor-observed process absence. No raw PID kill is a fallback.
- Missing capability, stale PID, uncertain observation, or failed graceful
  shutdown remains visible as a pending/unknown lifecycle state.

## Current implementation slice

The durable store and provider-owned lease registration are implemented in:

- `tools/protected_capability.py`
- `tools/session_supervisor.py`
- `tools/project_master_host.py`
- `tools/universe_conductor_runtime.py`

The regression suite is `tests/test_runtime_executor_supervisor.py`.

The HTTP adopt/stop route and resident server shutdown hook are intentionally
separate from this slice. The installed Runtime mutation gateway currently
limits one JSON mutation envelope to 1 MiB; the existing `tools/universe_server.py`
is larger than that envelope after Base64 transport. The route wiring must wait
for the incremental/streaming file mutation transport rather than bypassing
the governed write boundary.

Verification on 2026-08-11: the focused Supervisor/provider suite passed 84 tests with 9 subtests, the full regression passed 597 tests with 40 subtests, and Ruff passed for every changed module. Repository-wide Ruff still reports 11 pre-existing unused imports in `tools/universe_server.py`; those edits are intentionally deferred until the streaming mutation transport is installed.
