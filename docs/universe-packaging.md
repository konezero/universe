# Universe packaging (Windows user slice)

Status: first productization slice  
Scope: local service lifecycle + user-scope Start Menu / optional autostart  
Not: Tauri tray binary, MSI/Code-signed installer, admin services, remote hosts

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
| `stop` | `taskkill` of recorded PID tree (Windows) |
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
- Does not implement a native tray icon yet (follow-up: optional tray host).
- Does not grant project authority or Execution Assignment.

## Portable package (zip / folder)

Build from the repository root:

```powershell
python tools/build_portable.py
# folder only:
python tools/build_portable.py --no-zip
```

Output:

```text
dist/portable/UniversePortable-YYYYMMDD/
dist/portable/UniversePortable-YYYYMMDD.zip
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

so state stays inside the package when the folder is moved. Python is **not**
bundled; the Host must provide `python` on `PATH`.

## Follow-ups

1. ~~Optional system tray process~~ → `packaging/windows/Universe-Tray.ps1` + `tray` CLI
2. ~~Single-folder portable zip~~ → `tools/build_portable.py` (Python still Host-provided)
3. MSIX / signed installer for non-developer users (outline only below).
4. Embed/pin a private Python runtime inside the portable zip.
5. In-app settings panel actions that shell out only via Host-approved control.
6. Custom tray icon asset (currently uses application system icon).

## MSI / embed Python (outline — not implemented)

Target later:

```text
1. Build portable tree with tools/build_portable.py
2. Bundle official CPython embeddable package under runtime/python/
3. Point launchers at runtime/python/python.exe instead of PATH
4. Wrap with WiX / MSIX:
   - per-user install
   - Start Menu + optional autostart
   - no admin unless machine-wide requested
5. Code-sign installer and Python binaries
```

Until then, portable zip + Host Python remains the supported distribution.
