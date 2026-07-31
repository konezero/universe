#Requires -Version 5.1
[CmdletBinding()]
param(
  [switch]$KeepData
)

$ErrorActionPreference = "Stop"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Universe"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

if (Test-Path $runKey) {
  Remove-ItemProperty -Path $runKey -Name "UniverseLocalService" -ErrorAction SilentlyContinue
}

if (Test-Path $startMenu) {
  Remove-Item -Recurse -Force $startMenu
}

# Optional: stop running service if CLI is available from current workspace parent.
$candidate = Join-Path $PSScriptRoot "..\..\tools\universe_server.py"
if (Test-Path $candidate) {
  try {
    python $candidate stop 2>$null | Out-Null
  } catch {
    # best-effort
  }
}

[ordered]@{
  schema = "universe.windows-user-uninstall.v1"
  status = "REMOVED"
  start_menu_removed = -not (Test-Path $startMenu)
  autostart_removed = $true
  data_kept = [bool]$KeepData
  note = if ($KeepData) {
    "Local DB/state under %LOCALAPPDATA%\Universe left intact"
  } else {
    "Local DB/state under %LOCALAPPDATA%\Universe left intact (delete manually if desired)"
  }
} | ConvertTo-Json -Depth 4
