# Universe packaging (Windows user slice)

Status: first productization slice  
Scope: local service lifecycle + user-scope Start Menu / optional autostart  
Not: Tauri tray binary, MSI/Code-signed installer, admin services, remote hosts

**Install mode (Universe attach vs project standalone)** is fixed in
[`docs/universe-install-mode.md`](universe-install-mode.md). Default product
path: host boots, project is PWD attach. Install packs must offer that choice.

## Goals

1. Start / stop / restart / status the local Universe service without hunting PIDs.
2. Give a normal Windows user a Start Menu entry and optional logon autostart.
3. Keep loopback-only operation and existing `server.json` state contract.

## Service lifecycle CLI

From the Universe repository root:

```powershell
python tools/universe_server.py status
python tools/universe_server.py start --open-ui
python tools/universe_server.py start --no-open-ui
python tools/universe_server.py stop
python tools/universe_server.py restart --no-open-ui
python tools/universe_server.py serve --open-ui
```

| Command | Behavior |
|---------|----------|
| `status` | Reads `%LOCALAPPDATA%\Universe\server.json`, checks PID, probes `/health` |
| `start` | Detached background `serve` if not READY; optional UI open |
| `stop` | Authenticated graceful shutdown through the recorded service endpoint and control token |
| `restart` | `stop` then `start` |
| `serve` | Foreground service (existing developer path) |
| `tray` | Windows system-tray host (Open UI / Start / Stop / Restart) |

```powershell
python tools/universe_server.py tray --start-service
# or:
packaging\windows\Start-Universe-Tray.cmd
```

State file remains:

```text
%LOCALAPPDATA%\Universe\server.json
```

Logs for detached start:

```text
%LOCALAPPDATA%\Universe\service.log
```

## Windows user install

```powershell
cd C:\workspace\universe
powershell -ExecutionPolicy Bypass -File packaging\windows\install-user.ps1
# optional autostart at logon:
powershell -ExecutionPolicy Bypass -File packaging\windows\install-user.ps1 -Autostart
```

Creates under the current user Start Menu:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Universe\Universe.lnk
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Universe\Universe Status.lnk
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Universe\Universe Tray.lnk
```

`-Autostart` registers the tray launcher at logon when available.

Uninstall shortcuts + autostart (does not delete DB):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\uninstall-user.ps1
```

## Boundary

- No administrator elevation required for this slice.
- Does not ship Python; Host Python must already be on `PATH`.
- Does not create Windows Services (`services.msc`).
- Ships a PowerShell WinForms tray host; a compiled native tray binary and custom icon remain follow-up work.
- Does not grant project authority or Execution Assignment.

## Portable package (zip / folder)

Build from the repository root:

```powershell
python tools/build_portable.py
# folder only:
python tools/build_portable.py --no-zip
# embed Windows CPython (download official embed zip, or pass local zip):
python tools/build_portable.py --with-python
python tools/build_portable.py --with-python --python-zip C:\cache\python-3.12.8-embed-amd64.zip
```

Output:

```text
dist/portable/UniversePortable-YYYYMMDD/
dist/portable/UniversePortable-YYYYMMDD.zip
dist/portable/UniversePortable-YYYYMMDD-pyembed/   # when --with-python
```

Layout:

```text
UniversePortable-*/
  Start-Universe.cmd
  Start-Universe-Headless.cmd
  Start-Universe-Tray.cmd
  Stop-Universe.cmd
  Status-Universe.cmd
  tools/
  packaging/
  docs/
  .ai/runtime/project_instance/mode_registry.json
  data/          # portable server.json + sqlite
  logs/
  README-PORTABLE.txt
  VERSION.txt
```

Launchers set:

```text
UNIVERSE_DATA_DIR
UNIVERSE_STATE_FILE
UNIVERSE_DATABASE
UNIVERSE_LOG_FILE
UNIVERSE_MODE_REGISTRY
```

so state stays inside the package when the folder is moved.

Python options:

| Build | Runtime |
|-------|---------|
| default | Host `python` on PATH |
| `--with-python` | `runtime/python/python.exe` (official embeddable CPython) |

### Per-user install (no MSI)

```powershell
# after build_portable.py
powershell -ExecutionPolicy Bypass -File packaging\windows\Install-Portable-User.ps1 `
  -Source dist\portable\UniversePortable-YYYYMMDD.zip `
  -Autostart `
  -StartAfterInstall
```

Installs under `%LOCALAPPDATA%\Programs\UniversePortable` and creates Start Menu
shortcuts. This is the interim installer path until signed MSIX/MSI.

## Follow-ups

1. ~~Optional system tray process~~ → `packaging/windows/Universe-Tray.ps1` + `tray` CLI
2. ~~Single-folder portable zip~~ → `tools/build_portable.py`
3. ~~Embed Python in portable~~ → `--with-python` / `--python-zip`
4. ~~Per-user portable installer~~ → `Install-Portable-User.ps1`
5. Signed MSIX/MSI (WiX) for enterprise distribution.
6. In-app settings panel actions that shell out only via Host-approved control.
7. Custom tray icon asset (currently uses application system icon).

## Signed MSI (still later)

```text
1. tools/build_portable.py --with-python
2. WiX/MSIX harvest of the portable tree
3. per-user package + Start Menu + optional autostart
4. code-sign installer and runtime/python binaries
```
