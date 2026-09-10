# Managed Session Runtime bootstrap

Work receipt: `work_f22939e40597ba31b0098584`

## Scope and finding

Managed injection must leave a project Runtime Session Anchor and a Mode
Current Anchor membership, using the server-resolved Supervisor session ID.
Provider conversation IDs and Universe anchor IDs are not interchangeable DB
keys. The SQLite filename is the first 24 hex characters of SHA-256(session_id).

There is already a conditional executable attachment route in
`UniverseServer.attach_session_runtime_from_hook`: only SessionStart with
`MANAGED_SHELL_ATTACHED` invokes it. It can return `ATTACH_FAILED` while injection
still returns HTTP 200. `prepare_project_runtime_server` also requires an existing
Mode Current Anchor and stored registry before activating durable session memory.
Thus “no creation code exists” is not the finding. This change ensures the durable
projection independently of that conditional executable-host attachment. It does
not establish which historical branch caused each missing live database.

## Change

After successful managed injection, session_start, mode_change and manual hooks
use the returned Supervisor identity to bootstrap the Runtime database and append
Mode membership through the shared Runtime stores. No executable host, authority
or work assignment is created. Existing snapshot context and execution metadata
are retained. Repeated injection and late provider IDs use the same session key.
Conflicting identities and unknown Modes fail explicitly. Projection failure is
reported as `RUNTIME_SESSION_BOOTSTRAP_FAILED`, not plain `INJECTED`.

## Verification

- `test_managed_runtime_bootstrap.py`: 7 tests passed, real temporary SQLite stores.
- `test_session_inject_hook.py`: 33 tests passed.
- Scoped `git diff --check`: passed.
- Covered all three providers, repeat injection, late provider identity, identity
  conflict, invalid Mode, shared mode-change lookup, existing execution metadata,
  and hook failure when the HTTP reply lacks Supervisor identity.
- Hook transport is mocked in tests; real provider startup and live server
  executable attachment are NOT verified by these tests.

## Operational limits

Existing live sessions have not been repaired or restarted. No deployment was
performed. This does not fix an executable attachment failure or claim WORK_READY;
it ensures durable identity/membership when this updated hook runs successfully.
The session and project stores are separate SQLite databases: a partial write is
reported as failure and can be retried; this is not a cross-database transaction.
