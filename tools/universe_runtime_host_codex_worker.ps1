[CmdletBinding()]
param(
    [string]$RequestPath,
    [switch]$CapabilityOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-CodexCli {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_CLI_PATH)) {
        if (Test-Path -LiteralPath $env:CODEX_CLI_PATH -PathType Leaf) {
            return [string]$env:CODEX_CLI_PATH
        }
        return $null
    }
    $command = Get-Command codex -ErrorAction SilentlyContinue
    if ($null -eq $command) { return $null }
    return [string]$command.Source
}

function Get-CodexCapability {
    $cli = Get-CodexCli
    if ([string]::IsNullOrWhiteSpace($cli)) {
        return [ordered]@{ schema='universe.codex-worker-capability.v1'; status='UNAVAILABLE'; provider='CODEX_CLI'; reason='CODEX_CLI_UNAVAILABLE' }
    }
    try {
        $version = (& $cli --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) {
            return [ordered]@{ schema='universe.codex-worker-capability.v1'; status='UNAVAILABLE'; provider='CODEX_CLI'; reason='CODEX_CLI_LAUNCH_FAILED' }
        }
        return [ordered]@{ schema='universe.codex-worker-capability.v1'; status='AVAILABLE'; provider='CODEX_CLI'; capability_evidence_ref="codex-cli:$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($cli))):$version" }
    } catch {
        return [ordered]@{ schema='universe.codex-worker-capability.v1'; status='UNAVAILABLE'; provider='CODEX_CLI'; reason='CODEX_CLI_LAUNCH_FAILED' }
    }
}

function Convert-StructuredInput([object]$Value, [string]$Name) {
    if ($null -eq $Value) { throw "$Name is required." }
    if ($Value -is [string]) {
        if ([string]::IsNullOrWhiteSpace($Value)) { throw "$Name is required." }
        return [string]$Value
    }
    return ($Value | ConvertTo-Json -Depth 20 -Compress)
}

$capability = Get-CodexCapability
if ($CapabilityOnly) { $capability | ConvertTo-Json -Depth 5; exit 0 }
if ($capability.status -ne 'AVAILABLE') { $capability | ConvertTo-Json -Depth 5; exit 4 }
if ([string]::IsNullOrWhiteSpace($RequestPath)) { throw 'RequestPath is required.' }
$request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
if ($request.schema -ne 'universe.codex-worker-request.v1') { throw 'Unsupported request schema.' }
if ([string]$request.repository_write_scope -ne 'NONE') { throw 'Codex worker accepts read-only Task Frame work only.' }
if ($null -eq $request.mutation_scope -or $request.mutation_scope.operations.Count -ne 0 -or $request.mutation_scope.targets.Count -ne 0) { throw 'Codex worker requires an empty mutation scope.' }
$contextPack = Convert-StructuredInput $request.context_pack 'context_pack'
$outputContract = Convert-StructuredInput $request.output_contract 'output_contract'
if ([string]::IsNullOrWhiteSpace($contextPack) -or [string]::IsNullOrWhiteSpace($outputContract)) { throw 'Context Pack and output contract are required.' }
$workerRunRef = [string]$request.worker_run_ref
if ([string]::IsNullOrWhiteSpace($workerRunRef)) { throw 'worker_run_ref is required.' }
$resultMode = if ($null -eq $request.PSObject.Properties['result_mode']) { 'REDACTED' } else { ([string]$request.result_mode).Trim().ToUpperInvariant() }
if ($resultMode -notin @('REDACTED', 'STRUCTURED_JSON')) { throw 'Unsupported result mode.' }
$formatInstruction = if ($resultMode -eq 'STRUCTURED_JSON') { "`nReturn exactly one JSON object matching the Output Contract. Do not use Markdown fences or explanatory text." } else { '' }
$prompt = "Task Frame ID: $($request.task_frame_id)`nTurn ID: $($request.turn_id)`n`nContext Pack:`n$contextPack`n`nOutput Contract:`n$outputContract$formatInstruction"
$raw = & (Get-CodexCli) exec --json --sandbox read-only --skip-git-repo-check -C $env:TEMP $prompt 2>&1
if ($LASTEXITCODE -ne 0) { throw 'Codex CLI worker execution failed.' }
$messages = @()
foreach ($line in $raw) {
    try {
        $event = ([string]$line | ConvertFrom-Json -ErrorAction Stop)
        if ($event.type -eq 'item.completed' -and $null -ne $event.item -and $event.item.type -eq 'agent_message' -and -not [string]::IsNullOrWhiteSpace([string]$event.item.text)) { $messages += [string]$event.item.text }
        elseif ($event.type -eq 'agent_message' -and -not [string]::IsNullOrWhiteSpace([string]$event.text)) { $messages += [string]$event.text }
    } catch { }
}
if ($messages.Count -eq 0) { throw 'Codex CLI returned no bounded agent message.' }
$runId = [guid]::NewGuid().ToString('N')
[ordered]@{
    schema='universe.codex-worker-result.v1'
    status='COMPLETED'
    runtime_provider='CODEX_CLI'
    runtime_profile='TASK_FRAME_RUNTIME'
    source_mutation='HOST_GATEWAY_ONLY'
    worker_id="codex-cli:$runId"
    worker_run_ref=$workerRunRef
    result_receipt_ref="codex-cli:$runId"
    result=[ordered]@{ text=($messages -join "`n"); stop_reason='COMPLETED' }
    repository_write_scope='NONE'
} | ConvertTo-Json -Depth 6
