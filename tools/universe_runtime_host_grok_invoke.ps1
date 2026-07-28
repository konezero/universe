[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$RequestPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Require-Text([object]$Value, [string]$Name) {
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        throw "$Name is required."
    }
    return [string]$Value
}

$request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
if ($request.schema -ne 'universe.grok-worker-request.v1') {
    throw 'Unsupported request schema.'
}
if ((Require-Text $request.repository_write_scope 'repository_write_scope') -ne 'NONE') {
    throw 'Grok adapter accepts read-only Task Frame work only.'
}
if ($null -eq $request.mutation_scope -or $request.mutation_scope.operations.Count -ne 0 -or $request.mutation_scope.targets.Count -ne 0) {
    throw 'Grok adapter requires an empty mutation scope.'
}

$runtimeProfile = if ($null -eq $request.runtime_profile) { 'READ_ONLY' } else { (Require-Text $request.runtime_profile 'runtime_profile').ToUpperInvariant() }
if ($runtimeProfile -notin @('READ_ONLY', 'TASK_FRAME_RUNTIME')) {
    throw 'Unsupported Grok runtime profile.'
}
$contextPack = Require-Text $request.context_pack 'context_pack'
$outputContract = Require-Text $request.output_contract 'output_contract'
$workerRunRef = Require-Text $request.worker_run_ref 'worker_run_ref'
$maxTurns = if ($null -eq $request.max_turns) { 3 } else { [int]$request.max_turns }
if ($maxTurns -lt 1 -or $maxTurns -gt 8) {
    throw 'max_turns must be between 1 and 8.'
}

$grokHome = if ([string]::IsNullOrWhiteSpace($env:GROK_HOME)) { Join-Path $env:USERPROFILE '.grok' } else { $env:GROK_HOME }
$grokExe = Join-Path $grokHome 'bin\grok.exe'
$env:GROK_HOME = $grokHome
if (-not (Test-Path -LiteralPath $grokExe -PathType Leaf)) {
    throw 'Grok CLI executable is unavailable.'
}

$systemPrompt = if ($runtimeProfile -eq 'TASK_FRAME_RUNTIME') {
    'You are a bounded Task Frame Runtime provider. You receive all usable context in the supplied Context Pack. Do not inspect local files, create files, modify files, invoke subagents, or claim authority. Source mutation is Host-gateway-only. Return only the requested result content.'
} else {
    'You are a bounded read-only Task Frame worker. You receive all usable context in the supplied Context Pack. Do not inspect local files, execute commands, use network tools, create files, modify files, invoke subagents, or claim authority. Return only the requested result content.'
}
$prompt = "Task Frame ID: $(Require-Text $request.task_frame_id 'task_frame_id')`nTurn ID: $(Require-Text $request.turn_id 'turn_id')`n`nContext Pack:`n$contextPack`n`nOutput Contract:`n$outputContract"

$raw = & $grokExe --no-auto-update --no-subagents --no-memory --disable-web-search --permission-mode plan --sandbox read-only --max-turns $maxTurns --cwd $env:TEMP --system-prompt-override $systemPrompt -p $prompt --output-format json
if ($LASTEXITCODE -ne 0) {
    throw "Grok CLI failed with exit code $LASTEXITCODE."
}
$response = $raw | ConvertFrom-Json
$text = Require-Text $response.text 'response.text'
$grokSessionId = Require-Text $response.sessionId 'response.sessionId'
$requestId = Require-Text $response.requestId 'response.requestId'

[ordered]@{
    schema = 'universe.grok-worker-result.v1'
    status = if ($response.stopReason -eq 'EndTurn') { 'COMPLETED' } else { 'UNKNOWN' }
    runtime_provider = 'GROK_CLI'
    runtime_profile = $runtimeProfile
    source_mutation = 'HOST_GATEWAY_ONLY'
    worker_id = "grok-cli:$grokSessionId"
    worker_run_ref = $workerRunRef
    result_receipt_ref = "grok-cli:$grokSessionId`:$requestId"
    result = [ordered]@{ text = $text; stop_reason = $response.stopReason }
    sandbox_profile = 'read-only'
    permission_mode = 'plan'
    repository_write_scope = 'NONE'
} | ConvertTo-Json -Depth 6
