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
  [switch]$StartService
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$python = (Get-Command python -ErrorAction Stop).Source
$serverPy = Join-Path $UniverseRoot "tools\universe_server.py"
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

function Open-UniverseUi {
  param($Status)
  if ($Status -and $Status.endpoint) {
    # Prefer status endpoint; token is only in server.json for security — open health UI root.
    Start-Process ($Status.endpoint.TrimEnd("/") + "/")
    return
  }
  Invoke-UniverseCli -Args @("start", "--open-ui") | Out-Null
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Universe Tray"
$form.ShowInTaskbar = $false
$form.WindowState = "Minimized"
$form.Visible = $false
$form.Opacity = 0

$icon = [System.Drawing.SystemIcons]::Application
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $icon
$notify.Visible = $true
$notify.Text = "Universe"

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$itemOpen = $menu.Items.Add("Open UI")
$itemStatus = $menu.Items.Add("Refresh status")
$itemStart = $menu.Items.Add("Start service")
$itemStop = $menu.Items.Add("Stop service")
$itemRestart = $menu.Items.Add("Restart service")
[void]$menu.Items.Add("-")
$itemExit = $menu.Items.Add("Exit tray")
$notify.ContextMenuStrip = $menu

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

Update-TrayStatus | Out-Null
[System.Windows.Forms.Application]::Run()
