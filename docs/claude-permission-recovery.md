# Claude permission MCP process recovery

The broker belongs to one Host provider invocation. Its session token remains
memory-only. The initial `/exchange` is still single-use, and reading or replaying
the initial launch credential alone does not recover permission.

On Windows the broker records the authenticated initial connection's client PID,
creation time, executable image, parent PID and parent creation time. These come
from the OS TCP owner table and process APIs, never the request payload. After a
connection loss, the provider may restart its MCP child with its original config.
The replacement attempts `/recover` after `BOOTSTRAP_ALREADY_USED`.

Recovery requires all of:

- The original launch proof matches the broker's in-memory hash.
- The old MCP process instance is no longer alive.
- The replacement postdates it and has the same executable and same live parent
  process instance, with creation-time checks preventing PID reuse.
- The broker is still active and its capability has not been revoked.

The broker rotates the session token and revokes the old token. Its session,
target, bridge turn and duplicate-request history are preserved. A concurrent
client, different parent/image, stale proof or missing OS identity is denied.
No request-supplied session/PID field authorizes recovery. Closing the Host or
aborting registration clears recovery state; no cross-Host recovery exists.

This is a Windows process-identity boundary, not a sandbox against a privileged
local process capable of injecting into the provider or reading process memory.
Non-Windows/missing native identity supports initial exchange but does not support
this recovery capability. There is no disk-token or bootstrap-only fallback.
If a provider changes its direct MCP parent across restarts, recovery is denied;
do not weaken identity checks to accommodate it without observed launch evidence.

The MCP explicitly uses UTF-8 stdin/stdout independent of console code page.
Registration recovery failures have typed `CLAUDE_PERMISSION_RECOVERY_*` results.
This prevents a known encoding hazard but does not establish why a previously
reported `Connection closed` occurred.

Verification: `tests/test_claude_permission_recovery.py` uses actual MCP Python
processes and an isolated HTTP broker, including forced termination, restart,
token rotation, Unicode input/output under a cp949 environment, duplicate request
rejection and forged peer data. It does not itself prove Claude provider restart
behavior or completion of the Fleet Todo; those require separate Worker evidence.
