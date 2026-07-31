#Requires -Version 5.1
<#
.SYNOPSIS
  User-scope Universe launcher install (Start Menu + optional autostart).

.DESCRIPTION
  Does not require admin. Creates Start Menu shortcuts and optionally a current-user
  Run key for autostart. Does not install Python, services, or system drivers.
#>
[CmdletBinding()]
param(
  [string]$UniverseRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [switch]$Autostart,
  [switch]$NoAutostart
)

$ErrorActionPreference = "Stop"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Universe"
$launcher = Join-Path $UniverseRoot "packaging\windows\Start-Universe.cmd"
$python = (Get-Command python -ErrorAction Stop).Source

if (-not (Test-Path $launcher)) {
  throw "Launcher missing: $launcher"
}

New-Item -ItemType Directory -Force -Path $startMenu | Out-Null

$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $startMenu "Universe.lnk"
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $UniverseRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Start local Universe service and open UI"
$shortcut.Save()

$statusShortcutPath = Join-Path $startMenu "Universe Status.lnk"
$statusShortcut = $shell.CreateShortcut($statusShortcutPath)
$statusShortcut.TargetPath = $python
$statusShortcut.Arguments = "`"$UniverseRoot\tools\universe_server.py`" status"
$statusShortcut.WorkingDirectory = $UniverseRoot
$statusShortcut.WindowStyle = 1
$statusShortcut.Description = "Show local Universe service status"
$statusShortcut.Save()

$trayLauncher = Join-Path $UniverseRoot "packaging\windows\Start-Universe-Tray.cmd"
$trayShortcutPath = Join-Path $startMenu "Universe Tray.lnk"
$trayShortcut = $shell.CreateShortcut($trayShortcutPath)
$trayShortcut.TargetPath = $trayLauncher
$trayShortcut.WorkingDirectory = $UniverseRoot
$trayShortcut.WindowStyle = 7
$trayShortcut.Description = "Universe system tray (status / start / stop / open UI)"
$trayShortcut.Save()

$enableAutostart = $Autostart.IsPresent -and -not $NoAutostart.IsPresent
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if ($enableAutostart) {
  # Prefer tray host so logon starts service + tray controls.
  $autostartTarget = if (Test-Path $trayLauncher) { $trayLauncher } else { $launcher }
  New-ItemProperty -Path $runKey -Name "UniverseLocalService" -PropertyType String `
    -Value "`"$autostartTarget`"" -Force | Out-Null
}

$result = [ordered]@{
  schema = "universe.windows-user-install.v1"
  status = "INSTALLED"
  universe_root = $UniverseRoot
  start_menu = $startMenu
  shortcuts = @($shortcutPath, $statusShortcutPath, $trayShortcutPath)
  autostart = $enableAutostart
  autostart_value = if ($enableAutostart) { $autostartTarget } else { $null }
}
$result | ConvertTo-Json -Depth 4
