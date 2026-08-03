#Requires -Version 5.1
<#
.SYNOPSIS
  System tray host for the local Universe service (Windows).

.DESCRIPTION
  No admin, no extra Python packages. Uses WinForms NotifyIcon and the
  Universe service CLI (status/start/stop/restart).
#>
[CmdletBinding()]
param(
  [string]$UniverseRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$PythonExecutable = "",
  [switch]$StartService
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$createdNew = $false
$trayMutex = [System.Threading.Mutex]::new(
  $true,
  "Local\Universe.Tray",
  [ref]$createdNew
)
if (-not $createdNew) {
  $trayMutex.Dispose()
  exit 0
}

if (-not $PythonExecutable) {
  if ($env:UNIVERSE_PYTHON) {
    $PythonExecutable = $env:UNIVERSE_PYTHON.Trim('"')
  } else {
    $PythonExecutable = (Get-Command python.exe -ErrorAction Stop).Source
  }
}
$python = [System.IO.Path]::GetFullPath($PythonExecutable)
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw "native python executable not found: $python"
}
if ([System.IO.Path]::GetExtension($python) -ne ".exe") {
  throw "native python executable required: $python"
}
$serverPy = Join-Path $UniverseRoot "tools\universe_server.py"
$iconPath = Join-Path $UniverseRoot "packaging\windows\Universe.ico"
if (-not (Test-Path $serverPy)) {
  throw "universe_server.py not found: $serverPy"
}

function Invoke-UniverseCli {
  param([Parameter(Mandatory = $true)][string[]]$Args)
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $python
  $psi.Arguments = (@("`"$serverPy`"") + $Args | ForEach-Object { $_ }) -join " "
  # Build argv safely
  $argList = @($serverPy) + $Args
  $psi.Arguments = ($argList | ForEach-Object {
      if ($_ -match '\s') { '"{0}"' -f ($_ -replace '"', '\"') } else { $_ }
    }) -join " "
  $psi.WorkingDirectory = $UniverseRoot
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $proc = [System.Diagnostics.Process]::Start($psi)
  $stdout = $proc.StandardOutput.ReadToEnd()
  $stderr = $proc.StandardError.ReadToEnd()
  $proc.WaitForExit(120000) | Out-Null
  return [pscustomobject]@{
    ExitCode = $proc.ExitCode
    StdOut   = $stdout
    StdErr   = $stderr
  }
}

function Get-UniverseStatusObject {
  $result = Invoke-UniverseCli -Args @("status")
  try {
    return ($result.StdOut | ConvertFrom-Json)
  } catch {
    return [pscustomobject]@{ status = "UNKNOWN"; endpoint = $null; pid = $null }
  }
}

function Invoke-RemoteAccessApi {
  param(
    [Parameter(Mandatory = $true)][ValidateSet("GET", "POST")][string]$Method,
    [Parameter(Mandatory = $true)][string]$Path,
    [hashtable]$Body = @{}
  )
  $status = Get-UniverseStatusObject
  if ($status.status -ne "READY" -or -not $status.endpoint) {
    throw "Universe service is not ready."
  }
  $uri = $status.endpoint.TrimEnd("/") + $Path
  try {
    if ($Method -eq "GET") {
      return Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 10
    }
    $json = $Body | ConvertTo-Json -Compress
    return Invoke-RestMethod -Uri $uri -Method Post -ContentType "application/json" -Body $json -TimeoutSec 30
  } catch {
    throw $_.Exception.Message
  }
}

function Get-RemoteGatewayStatusObject {
  try {
    return Invoke-RemoteAccessApi -Method GET -Path "/v1/settings/remote-access"
  } catch {
    return [pscustomobject]@{
      gateway = [pscustomobject]@{ status = "UNKNOWN"; public_base_url = $null }
      connector = [pscustomobject]@{ status = "UNKNOWN" }
    }
  }
}

function Open-UniverseUi {
  param($Status)
  if ($Status -and $Status.endpoint) {
    # Prefer status endpoint; token is only in server.json for security — open health UI root.
    Start-Process ($Status.endpoint.TrimEnd("/") + "/")
    return
  }
  Invoke-UniverseCli -Args @("start", "--open-ui") | Out-Null
}

$ownsIcon = $false
if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
  $icon = [System.Drawing.Icon]::new($iconPath)
  $ownsIcon = $true
} else {
  $icon = [System.Drawing.SystemIcons]::Application
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Universe Tray"
$form.ShowInTaskbar = $false
$form.WindowState = "Minimized"
$form.Visible = $false
$form.Opacity = 0
$form.Icon = $icon

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $icon
$notify.Text = "Universe"
$notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$itemOpen = $menu.Items.Add("Open UI")
$itemStatus = $menu.Items.Add("Refresh status")
$itemStart = $menu.Items.Add("Start service")
$itemStop = $menu.Items.Add("Stop service")
$itemRestart = $menu.Items.Add("Restart service")
[void]$menu.Items.Add("-")
$itemRemoteStart = $menu.Items.Add("Start saved remote access")
$itemRemoteOpen = $menu.Items.Add("Open mobile URL")
$itemRemoteStop = $menu.Items.Add("Stop mobile access")
[void]$menu.Items.Add("-")
$itemExit = $menu.Items.Add("Exit tray")
$notify.ContextMenuStrip = $menu
$notify.Visible = $true

function Update-TrayStatus {
  $status = Get-UniverseStatusObject
  $label = if ($status.status) { $status.status } else { "UNKNOWN" }
  $notify.Text = ("Universe: {0}" -f $label)
  if ($label -eq "READY") {
    $notify.BalloonTipTitle = "Universe"
    # Avoid spamming balloons on every timer tick — only update tooltip text.
  }
  return $status
}

$itemOpen.Add_Click({
    $status = Get-UniverseStatusObject
    if ($status.status -ne "READY") {
      Invoke-UniverseCli -Args @("start", "--open-ui") | Out-Null
      Start-Sleep -Seconds 1
      $status = Get-UniverseStatusObject
    }
    Open-UniverseUi -Status $status
  })

$itemStatus.Add_Click({
    $status = Update-TrayStatus
    $notify.BalloonTipTitle = "Universe status"
    $notify.BalloonTipText = ("{0}`n{1}" -f $status.status, $status.endpoint)
    $notify.ShowBalloonTip(2500)
  })

$itemStart.Add_Click({
    Invoke-UniverseCli -Args @("start", "--no-open-ui") | Out-Null
    Update-TrayStatus | Out-Null
  })

$itemStop.Add_Click({
    Invoke-UniverseCli -Args @("stop") | Out-Null
    Update-TrayStatus | Out-Null
  })

$itemRestart.Add_Click({
    Invoke-UniverseCli -Args @("restart", "--no-open-ui") | Out-Null
    Update-TrayStatus | Out-Null
  })

$itemRemoteStart.Add_Click({
    $status = Get-UniverseStatusObject
    if ($status.status -ne "READY") {
      Invoke-UniverseCli -Args @("start", "--no-open-ui") | Out-Null
      Start-Sleep -Seconds 1
    }
    try {
      $remote = Invoke-RemoteAccessApi -Method POST -Path "/v1/settings/remote-access/start" -Body @{ transport_kind = "SAVED" }
    } catch {
      $remote = $null
      $remoteError = $_.Exception.Message
    }
    $notify.BalloonTipTitle = "Universe mobile access"
    $notify.BalloonTipText = if ($remote) {
      $remote.gateway.public_base_url
    } else {
      $remoteError
    }
    $notify.ShowBalloonTip(3000)
  })

$itemRemoteOpen.Add_Click({
    $remote = Get-RemoteGatewayStatusObject
    if ($remote.gateway.public_base_url) {
      Start-Process $remote.gateway.public_base_url
    } else {
      $notify.BalloonTipTitle = "Universe mobile access"
      $notify.BalloonTipText = "Mobile gateway is offline."
      $notify.ShowBalloonTip(2500)
    }
  })

$itemRemoteStop.Add_Click({
    try {
      Invoke-RemoteAccessApi -Method POST -Path "/v1/settings/remote-access/stop" | Out-Null
    } catch {
      $notify.BalloonTipTitle = "Universe mobile access"
      $notify.BalloonTipText = $_.Exception.Message
      $notify.ShowBalloonTip(2500)
    }
  })

$itemExit.Add_Click({
    $notify.Visible = $false
    [System.Windows.Forms.Application]::Exit()
  })

$notify.Add_DoubleClick({
    $status = Get-UniverseStatusObject
    if ($status.status -ne "READY") {
      Invoke-UniverseCli -Args @("start", "--open-ui") | Out-Null
      Start-Sleep -Seconds 1
      $status = Get-UniverseStatusObject
    }
    Open-UniverseUi -Status $status
  })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 8000
$timer.Add_Tick({ Update-TrayStatus | Out-Null })
$timer.Start()

if ($StartService) {
  Invoke-UniverseCli -Args @("start", "--no-open-ui") | Out-Null
}

$initialStatus = Update-TrayStatus
$notify.BalloonTipTitle = "Universe"
$notify.BalloonTipText = if ($initialStatus.status -eq "READY") {
  "Local service is ready."
} else {
  "Tray controls are ready; service status is $($initialStatus.status)."
}
$notify.ShowBalloonTip(2500)
try {
  [System.Windows.Forms.Application]::Run()
} finally {
  $timer.Stop()
  $timer.Dispose()
  $notify.Visible = $false
  $notify.Dispose()
  $menu.Dispose()
  $form.Dispose()
  if ($ownsIcon) {
    $icon.Dispose()
  }
  if ($createdNew) {
    $trayMutex.ReleaseMutex()
  }
  $trayMutex.Dispose()
}
