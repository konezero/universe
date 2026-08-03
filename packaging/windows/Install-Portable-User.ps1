#Requires -Version 5.1
<#
.SYNOPSIS
  Per-user install of a Universe portable package (no MSI, no admin).

.DESCRIPTION
  Copies a portable folder (or expands a zip) into
  %LOCALAPPDATA%\Programs\UniversePortable and creates Start Menu shortcuts.
  This is the interim "installer" until a signed MSIX/MSI exists.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Source,

  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\UniversePortable"),

  [switch]$Autostart,
  [switch]$StartAfterInstall
)

$ErrorActionPreference = "Stop"
$sourcePath = Resolve-Path $Source

if (Test-Path $InstallDir) {
  Remove-Item -Recurse -Force $InstallDir
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if ($sourcePath.Path.ToLower().EndsWith(".zip")) {
  Expand-Archive -Path $sourcePath.Path -DestinationPath $InstallDir -Force
  # If zip contains a single top-level folder, flatten one level.
  $children = Get-ChildItem $InstallDir
  if ($children.Count -eq 1 -and $children[0].PSIsContainer) {
    $inner = $children[0].FullName
    Get-ChildItem $inner | Move-Item -Destination $InstallDir -Force
    Remove-Item -Recurse -Force $inner
  }
} else {
  Copy-Item -Path (Join-Path $sourcePath.Path "*") -Destination $InstallDir -Recurse -Force
}

$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Universe"
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
$shell = New-Object -ComObject WScript.Shell
$iconPath = Join-Path $InstallDir "packaging\windows\Universe.ico"

function New-LauncherShortcut([string]$Name, [string]$Target) {
  $path = Join-Path $startMenu $Name
  $sc = $shell.CreateShortcut($path)
  $sc.TargetPath = $Target
  $sc.WorkingDirectory = $InstallDir
  $sc.WindowStyle = 7
  if (Test-Path -LiteralPath $iconPath -PathType Leaf) {
    $sc.IconLocation = "$iconPath,0"
  }
  $sc.Save()
  return $path
}

$shortcuts = @()
$shortcuts += New-LauncherShortcut "Universe.lnk" (Join-Path $InstallDir "Start-Universe.cmd")
$shortcuts += New-LauncherShortcut "Universe Status.lnk" (Join-Path $InstallDir "Status-Universe.cmd")
$shortcuts += New-LauncherShortcut "Universe Tray.lnk" (Join-Path $InstallDir "Start-Universe-Tray.cmd")

$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$tray = Join-Path $InstallDir "Start-Universe-Tray.cmd"
if ($Autostart -and (Test-Path $tray)) {
  New-ItemProperty -Path $runKey -Name "UniverseLocalService" -PropertyType String `
    -Value "`"$tray`"" -Force | Out-Null
}

if ($StartAfterInstall) {
  Start-Process -FilePath (Join-Path $InstallDir "Start-Universe.cmd") -WorkingDirectory $InstallDir
}

[ordered]@{
  schema = "universe.windows-portable-user-install.v1"
  status = "INSTALLED"
  install_dir = $InstallDir
  source = $sourcePath.Path
  shortcuts = $shortcuts
  autostart = [bool]$Autostart
  includes_python = (Test-Path (Join-Path $InstallDir "runtime\python\python.exe"))
} | ConvertTo-Json -Depth 5
