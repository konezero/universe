[CmdletBinding()]
param(
    [string]$RequestPath,
    [string]$Provider,
    [switch]$CapabilityOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

[ordered]@{
    status = 'WINDOWS_NATIVE_CLI_ROUTE_REQUIRED'
    stage = 'LEGACY_POWERSHELL_DISPATCH'
    reason = 'USE_UNIVERSE_RUNTIME_HOST_PYTHON_DISPATCHER'
    repository_write = $false
} | ConvertTo-Json -Depth 4
exit 4
