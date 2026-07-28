[CmdletBinding()]
param(
    [string]$RequestPath,
    [string]$Provider,
    [switch]$CapabilityOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$runtimeTmp = Join-Path $repositoryRoot '.ai\runtime\tmp'

function Invoke-HostPost([string]$Endpoint, [string]$Token, [string]$Path, [object]$Payload) {
    $headers = @{ 'X-Anchor-Session-Memory-Token' = $Token }
    return Invoke-RestMethod -Method Post -Uri ($Endpoint.TrimEnd('/') + $Path) -Headers $headers -ContentType 'application/json' -Body ($Payload | ConvertTo-Json -Depth 20)
}

function Get-GrokCapability {
    $grokHome = if ([string]::IsNullOrWhiteSpace($env:GROK_HOME)) { Join-Path $env:USERPROFILE '.grok' } else { $env:GROK_HOME }
    $exe = Join-Path $grokHome 'bin\grok.exe'
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return [ordered]@{ status='UNAVAILABLE'; provider='GROK'; reason='GROK_CLI_UNAVAILABLE' } }
    try {
        $version = (& $exe --version 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) { return [ordered]@{ status='UNAVAILABLE'; provider='GROK'; reason='GROK_CLI_LAUNCH_FAILED' } }
        return [ordered]@{ status='AVAILABLE'; provider='GROK'; capability_evidence_ref="grok-cli:$([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($exe))):$version" }
    } catch { return [ordered]@{ status='UNAVAILABLE'; provider='GROK'; reason='GROK_CLI_LAUNCH_FAILED' } }
}

function Get-ProviderCapability([string]$Name) {
    $normalized = $Name.Trim().ToUpperInvariant()
    if ($normalized -eq 'GROK') { return Get-GrokCapability }
    if ($normalized -eq 'CODEX') {
        $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repositoryRoot '.ai\adapters\codex\worker.ps1') -CapabilityOnly
        return (($output -join "`n") | ConvertFrom-Json)
    }
    return [ordered]@{ status='UNAVAILABLE'; provider=$normalized; reason='WORKER_PROVIDER_UNSUPPORTED' }
}

if ($CapabilityOnly) {
    if ([string]::IsNullOrWhiteSpace($Provider)) { throw 'Provider is required for capability check.' }
    Get-ProviderCapability $Provider | ConvertTo-Json -Depth 6
    exit 0
}
if ([string]::IsNullOrWhiteSpace($RequestPath)) { throw 'RequestPath is required.' }
$request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
if ($request.schema -ne 'universe.task-frame-worker-dispatch-request.v1') { throw 'Unsupported dispatch request schema.' }
$provider = [string]$request.provider
$capability = Get-ProviderCapability $provider
if ($capability.status -ne 'AVAILABLE') { [ordered]@{ status='WORKER_INVOCATION_UNAVAILABLE'; provider=$provider; capability=$capability; repository_write=$false } | ConvertTo-Json -Depth 8; exit 4 }
if ([string]$request.repository_write_scope -ne 'NONE' -or $null -eq $request.mutation_scope -or $request.mutation_scope.operations.Count -ne 0 -or $request.mutation_scope.targets.Count -ne 0) { throw 'Dispatcher currently supports read-only Task Frame turns only.' }
$now = (Get-Date).ToUniversalTime().ToString('o')
$planPayload = [ordered]@{ session_id=$request.session_id; frame_id=$request.frame_id; operation=[ordered]@{ operation='worker_invocation_plan'; turn_id=$request.turn_id; host_capability_status='AVAILABLE'; capability_evidence_ref=$capability.capability_evidence_ref; invoker_actor_ref=$request.invoker_actor_ref; observed_at=$now } }
$plan = Invoke-HostPost $request.endpoint $request.token '/v1/task-frame/operation' $planPayload
if ($plan.status -ne 'TASK_FRAME_OPERATION_APPLIED' -or $plan.output.status -ne 'WORKER_INVOCATION_READY') { $plan | ConvertTo-Json -Depth 12; exit 4 }
$plannedProvider = [string]$plan.output.worker_invocation.provider
if (-not [string]::IsNullOrWhiteSpace($plannedProvider) -and $plannedProvider.Trim().ToUpperInvariant() -ne $provider.Trim().ToUpperInvariant()) { [ordered]@{ status='WORKER_PROVIDER_PLAN_MISMATCH'; requested=$provider; planned=$plannedProvider; repository_write=$false } | ConvertTo-Json -Depth 6; exit 4 }
[IO.Directory]::CreateDirectory($runtimeTmp) | Out-Null
$callId = [guid]::NewGuid().ToString('N')
$adapterRequestPath = Join-Path $runtimeTmp "worker-dispatch-$callId.json"
$adapterRequest = [ordered]@{ task_frame_id=$request.frame_id; turn_id=$request.turn_id; repository_write_scope='NONE'; mutation_scope=[ordered]@{ operations=@(); targets=@() }; context_pack=$request.context_pack; output_contract=$request.output_contract; max_turns=$request.max_turns }
if ($provider.Trim().ToUpperInvariant() -eq 'GROK') { $adapterRequest.schema='universe.grok-worker-request.v1'; $adapterRequest.runtime_profile='TASK_FRAME_RUNTIME'; $adapter = Join-Path $repositoryRoot '.ai\adapters\grok\invoke.ps1' } else { $adapterRequest.schema='universe.codex-worker-request.v1'; $adapter = Join-Path $repositoryRoot '.ai\adapters\codex\worker.ps1' }
try { [IO.File]::WriteAllText($adapterRequestPath, ($adapterRequest | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false)); $adapterOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $adapter -RequestPath $adapterRequestPath; $worker = (($adapterOutput -join "`n") | ConvertFrom-Json) } finally { Remove-Item -LiteralPath $adapterRequestPath -Force -ErrorAction SilentlyContinue }
if ($worker.status -ne 'COMPLETED') { [ordered]@{ status='WORKER_PROVIDER_FAILED'; provider=$provider; worker=$worker; repository_write=$false } | ConvertTo-Json -Depth 12; exit 4 }
$claimPayload = [ordered]@{ session_id=$request.session_id; frame_id=$request.frame_id; operation=[ordered]@{ operation='claim_turn'; turn_id=$request.turn_id; worker_id=$worker.worker_id; host_invocation_receipt_ref=$worker.host_invocation_receipt_ref; capability_evidence_ref=$capability.capability_evidence_ref; invoker_actor_ref=$request.invoker_actor_ref; observed_at=(Get-Date).ToUniversalTime().ToString('o') } }
$claim = Invoke-HostPost $request.endpoint $request.token '/v1/task-frame/operation' $claimPayload
if ($claim.status -ne 'TASK_FRAME_OPERATION_APPLIED' -or $claim.output.status -ne 'TURN_CLAIMED') { $claim | ConvertTo-Json -Depth 12; exit 4 }
$envelope = [ordered]@{ turn_id=$request.turn_id; worker_id=$worker.worker_id; host_invocation_receipt_ref=$worker.host_invocation_receipt_ref; status=$worker.status; evidence_refs=@($worker.host_result_evidence_ref); result=$worker.result; review_decision='' }
$resultPayload = [ordered]@{ session_id=$request.session_id; frame_id=$request.frame_id; envelope=$envelope; host_result_evidence_ref=$worker.host_result_evidence_ref; observed_at=(Get-Date).ToUniversalTime().ToString('o') }
$result = Invoke-HostPost $request.endpoint $request.token '/v1/task-frame/worker-result' $resultPayload
[ordered]@{ status=$result.status; provider=$provider; worker_id=$worker.worker_id; host_invocation_receipt_ref=$worker.host_invocation_receipt_ref; host_result_evidence_ref=$worker.host_result_evidence_ref; repository_write=$false; runtime_result=$result } | ConvertTo-Json -Depth 16