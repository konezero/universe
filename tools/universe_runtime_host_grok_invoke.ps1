[CmdletBinding()]
param(
    [string]$RequestPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

[ordered]@{
    schema = 'universe.grok-worker-result.v1'
    status = 'WINDOWS_NATIVE_CLI_ROUTE_REQUIRED'
    reason = 'USE_UNIVERSE_RUNTIME_HOST_PYTHON_DISPATCHER'
    repository_write_scope = 'NONE'
} | ConvertTo-Json -Depth 4
exit 4
