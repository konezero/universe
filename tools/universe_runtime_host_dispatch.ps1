[CmdletBinding()]
param(
    [string]$RequestPath,
    [string]$Provider,
    [switch]$CapabilityOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$dispatchProvider = ''
$stage = 'DISPATCH_SETUP'
trap {
    [ordered]@{
        status = 'WORKER_TRANSPORT_FAILED'
        provider = $dispatchProvider
        stage = $stage
        reason = 'DISPATCH_EXCEPTION'
        repository_write = $false
    } | ConvertTo-Json -Depth 6
    exit 4
}
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stateRoot = if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    Join-Path $env:TEMP 'Universe'
} else {
    Join-Path $env:LOCALAPPDATA 'Universe'
}
$runtimeTmp = Join-Path $stateRoot 'runtime-tmp'

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
        $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repositoryRoot 'tools\universe_runtime_host_codex_worker.ps1') -CapabilityOnly
        return (($output -join "`n") | ConvertFrom-Json)
    }
    return [ordered]@{ status='UNAVAILABLE'; provider=$normalized; reason='WORKER_PROVIDER_UNSUPPORTED' }
}

if ($CapabilityOnly) {
    $stage = 'CAPABILITY_CHECK'
    if ([string]::IsNullOrWhiteSpace($Provider)) { throw 'Provider is required for capability check.' }
    Get-ProviderCapability $Provider | ConvertTo-Json -Depth 6
    exit 0
}
$stage = 'REQUEST_LOAD'
if ([string]::IsNullOrWhiteSpace($RequestPath)) { throw 'RequestPath is required.' }
$request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
if ($request.schema -ne 'universe.task-frame-worker-dispatch-request.v1') { throw 'Unsupported dispatch request schema.' }
$dispatchProvider = [string]$request.provider
$stage = 'CAPABILITY_CHECK'
$capability = Get-ProviderCapability $dispatchProvider
if ($capability.status -ne 'AVAILABLE') { [ordered]@{ status='WORKER_INVOCATION_UNAVAILABLE'; provider=$dispatchProvider; capability=$capability; repository_write=$false } | ConvertTo-Json -Depth 8; exit 4 }
if ([string]$request.repository_write_scope -ne 'NONE' -or $null -eq $request.mutation_scope -or $request.mutation_scope.operations.Count -ne 0 -or $request.mutation_scope.targets.Count -ne 0) { throw 'Dispatcher currently supports read-only Task Frame turns only.' }
$stage = 'TASK_FRAME_PLAN'
$now = (Get-Date).ToUniversalTime().ToString('o')
$planPayload = [ordered]@{ session_id=$request.session_id; frame_id=$request.frame_id; operation=[ordered]@{ operation='worker_invocation_plan'; turn_id=$request.turn_id; host_capability_status='AVAILABLE'; capability_evidence_ref=$capability.capability_evidence_ref; invoker_actor_ref=$request.invoker_actor_ref; observed_at=$now } }
$plan = Invoke-HostPost $request.endpoint $request.token '/v1/task-frame/operation' $planPayload
if ($plan.status -ne 'TASK_FRAME_OPERATION_APPLIED' -or $plan.output.status -ne 'WORKER_INVOCATION_READY') { $plan | ConvertTo-Json -Depth 12; exit 4 }
$plannedInvocation = $plan.output.worker_invocation
$plannedProvider = [string]$plannedInvocation.provider
if (-not [string]::IsNullOrWhiteSpace($plannedProvider) -and $plannedProvider.Trim().ToUpperInvariant() -ne $dispatchProvider.Trim().ToUpperInvariant()) { [ordered]@{ status='WORKER_PROVIDER_PLAN_MISMATCH'; requested=$dispatchProvider; planned=$plannedProvider; repository_write=$false } | ConvertTo-Json -Depth 6; exit 4 }
$skillBindings = @()
if ($null -ne $plannedInvocation.PSObject.Properties['input_bundle']) {
    $inputBundle = $plannedInvocation.input_bundle
    if ($null -ne $inputBundle -and $null -ne $inputBundle.PSObject.Properties['boss_allocation']) {
        $allocation = $inputBundle.boss_allocation
        if ($null -ne $allocation -and $null -ne $allocation.PSObject.Properties['skill_bindings']) {
            $skillBindings = @($allocation.skill_bindings)
        }
    }
}
[IO.Directory]::CreateDirectory($runtimeTmp) | Out-Null
$callId = [guid]::NewGuid().ToString('N')
$workerRunRef = "universe-runtime-host:$callId"
$adapterRequestPath = Join-Path $runtimeTmp "worker-dispatch-$callId.json"
$adapterRequest = [ordered]@{ task_frame_id=$request.frame_id; turn_id=$request.turn_id; worker_run_ref=$workerRunRef; repository_write_scope='NONE'; mutation_scope=[ordered]@{ operations=@(); targets=@() }; context_pack=$request.context_pack; output_contract=$request.output_contract; max_turns=$request.max_turns }
if ($dispatchProvider.Trim().ToUpperInvariant() -eq 'GROK') { $adapterRequest.schema='universe.grok-worker-request.v1'; $adapterRequest.runtime_profile='TASK_FRAME_RUNTIME'; $adapter = Join-Path $repositoryRoot 'tools\universe_runtime_host_grok_invoke.ps1' } else { $adapterRequest.schema='universe.codex-worker-request.v1'; $adapter = Join-Path $repositoryRoot 'tools\universe_runtime_host_codex_worker.ps1' }
$stage = 'WORKER_ADAPTER'
$adapterStopwatch = [Diagnostics.Stopwatch]::StartNew()
try {
    [IO.File]::WriteAllText($adapterRequestPath, ($adapterRequest | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    $adapterOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $adapter -RequestPath $adapterRequestPath
    $worker = (($adapterOutput -join "`n") | ConvertFrom-Json)
} finally {
    $adapterStopwatch.Stop()
    Remove-Item -LiteralPath $adapterRequestPath -Force -ErrorAction SilentlyContinue
}
if ($worker.status -ne 'COMPLETED') { [ordered]@{ status='WORKER_PROVIDER_FAILED'; provider=$dispatchProvider; worker=$worker; repository_write=$false } | ConvertTo-Json -Depth 12; exit 4 }
if ([string]$worker.worker_run_ref -ne $workerRunRef) { [ordered]@{ status='WORKER_RUN_REF_MISMATCH'; provider=$dispatchProvider; repository_write=$false } | ConvertTo-Json -Depth 6; exit 4 }
$stage = 'TASK_FRAME_CLAIM'
$claimPayload = [ordered]@{ session_id=$request.session_id; frame_id=$request.frame_id; operation=[ordered]@{ operation='claim_turn'; turn_id=$request.turn_id; worker_id=$worker.worker_id; worker_run_ref=$workerRunRef; capability_evidence_ref=$capability.capability_evidence_ref; invoker_actor_ref=$request.invoker_actor_ref; observed_at=(Get-Date).ToUniversalTime().ToString('o') } }
$claim = Invoke-HostPost $request.endpoint $request.token '/v1/task-frame/operation' $claimPayload
if ($claim.status -ne 'TASK_FRAME_OPERATION_APPLIED' -or $claim.output.status -ne 'TURN_CLAIMED') { $claim | ConvertTo-Json -Depth 12; exit 4 }
$stage = 'TASK_FRAME_RESULT'
$modelRef = if (
    $null -ne $plannedInvocation.PSObject.Properties['model'] -and
    -not [string]::IsNullOrWhiteSpace([string]$plannedInvocation.model)
) {
    [string]$plannedInvocation.model
} else {
    "${dispatchProvider}:UNKNOWN"
}
$skillRunObservations = @()
foreach ($binding in $skillBindings) {
    $bindingDigest = [string]$binding.skill_binding_digest
    if ([string]::IsNullOrWhiteSpace($bindingDigest)) { throw 'Declared Skill binding digest is required.' }
    $skillRunObservations += [ordered]@{
        skill_binding_digest = $bindingDigest
        model_ref = $modelRef
        outcome = 'SUCCEEDED'
        validation_state = 'NOT_RUN'
        evidence_refs = @([string]$worker.result_receipt_ref)
        metrics = [ordered]@{
            duration_ms = [Math]::Round($adapterStopwatch.Elapsed.TotalMilliseconds, 3)
        }
    }
}
$envelope = [ordered]@{ turn_id=$request.turn_id; worker_id=$worker.worker_id; worker_run_ref=$workerRunRef; result_receipt_ref=$worker.result_receipt_ref; status=$worker.status; evidence_refs=@($worker.result_receipt_ref); result=$worker.result; review_decision='' }
if ($skillRunObservations.Count -gt 0) {
    $envelope['skill_run_observations'] = @($skillRunObservations)
}
$resultPayload = [ordered]@{ session_id=$request.session_id; frame_id=$request.frame_id; envelope=$envelope; observed_at=(Get-Date).ToUniversalTime().ToString('o') }
$result = Invoke-HostPost $request.endpoint $request.token '/v1/task-frame/worker-result' $resultPayload
[ordered]@{ status=$result.status; provider=$dispatchProvider; worker_id=$worker.worker_id; result_receipt_ref=$worker.result_receipt_ref; skill_run_observation_count=$skillRunObservations.Count; repository_write=$false; runtime_result=$result } | ConvertTo-Json -Depth 16
