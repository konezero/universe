#!/usr/bin/env python3
"""Build a relocatable Universe portable folder (+ optional zip).

Does not bundle a Python interpreter. Host Python must remain on PATH.
Portable data lives under <package>/data so the tree can move between machines.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INCLUDE_DIRS = (
    "tools",
    "packaging",
    "docs",
    "seed",
    "templates",
)
INCLUDE_FILES = (
    "README.md",
    "LICENSE",
    "REPOSITORY_MANIFEST.md",
    "AGENTS.md",
)
INCLUDE_AI_FILES = (
    Path(".ai") / "runtime" / "project_instance" / "mode_registry.json",
)
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".pytest_cache",
    "node_modules",
    "anchor_store",
    "continuity",
    "tmp",
    "task_frames",
}


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def copy_tree(src: Path, dest: Path) -> None:
    if not src.is_dir():
        return
    for item in src.rglob("*"):
        if _should_skip(item.relative_to(src)):
            continue
        if item.is_dir():
            continue
        if item.suffix in {".pyc", ".pyo"}:
            continue
        rel = item.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def write_launcher(path: Path, body: str) -> None:
    path.write_text(body.replace("\n", "\r\n"), encoding="utf-8", newline="")


def build_portable(output_dir: Path, *, make_zip: bool) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    package_name = f"UniversePortable-{stamp}"
    package_root = output_dir / package_name
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True)

    for name in INCLUDE_DIRS:
        src = ROOT / name
        if src.is_dir():
            copy_tree(src, package_root / name)

    for name in INCLUDE_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, package_root / name)

    for rel in INCLUDE_AI_FILES:
        src = ROOT / rel
        if src.is_file():
            dest = package_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    data_dir = package_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".gitkeep").write_text("", encoding="utf-8")
    (package_root / "logs").mkdir(exist_ok=True)

    env_snippet = r"""@echo off
set "UNIVERSE_PORTABLE_ROOT=%~dp0"
if "%UNIVERSE_PORTABLE_ROOT:~-1%"=="\" set "UNIVERSE_PORTABLE_ROOT=%UNIVERSE_PORTABLE_ROOT:~0,-1%"
set "UNIVERSE_DATA_DIR=%UNIVERSE_PORTABLE_ROOT%\data"
set "UNIVERSE_STATE_FILE=%UNIVERSE_DATA_DIR%\server.json"
set "UNIVERSE_DATABASE=%UNIVERSE_DATA_DIR%\universe.sqlite3"
set "UNIVERSE_LOG_FILE=%UNIVERSE_PORTABLE_ROOT%\logs\service.log"
set "UNIVERSE_MODE_REGISTRY=%UNIVERSE_PORTABLE_ROOT%\.ai\runtime\project_instance\mode_registry.json"
if not exist "%UNIVERSE_DATA_DIR%" mkdir "%UNIVERSE_DATA_DIR%"
if not exist "%UNIVERSE_PORTABLE_ROOT%\logs" mkdir "%UNIVERSE_PORTABLE_ROOT%\logs"
cd /d "%UNIVERSE_PORTABLE_ROOT%"
"""

    write_launcher(
        package_root / "Start-Universe.cmd",
        env_snippet
        + "python tools\\universe_server.py start --open-ui\r\n"
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Start-Universe-Headless.cmd",
        env_snippet
        + "python tools\\universe_server.py start --no-open-ui\r\n"
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Stop-Universe.cmd",
        env_snippet
        + "python tools\\universe_server.py stop\r\n"
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Status-Universe.cmd",
        env_snippet
        + "python tools\\universe_server.py status\r\n"
        + "pause\r\n"
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Start-Universe-Tray.cmd",
        env_snippet
        + "python tools\\universe_server.py tray --start-service\r\n"
        + "exit /b %ERRORLEVEL%\r\n",
    )

    readme = f"""Universe portable package
=========================

Built: {stamp} (UTC)
Scenario: relocatable tree with local data/ under this folder.

Requirements
------------
- Windows Host
- Python 3 on PATH (not bundled in this zip)

Quick start
-----------
1. Unzip anywhere (no admin required).
2. Double-click Start-Universe.cmd  (or Start-Universe-Tray.cmd)
3. Status-Universe.cmd / Stop-Universe.cmd for control

Data location
-------------
All service state stays inside this package:

  data\\server.json
  data\\universe.sqlite3
  logs\\service.log

Moving the folder keeps data with it. Do not run two portable copies that share
the same data path at once.

Environment (set by launchers)
------------------------------
UNIVERSE_DATA_DIR, UNIVERSE_STATE_FILE, UNIVERSE_DATABASE,
UNIVERSE_LOG_FILE, UNIVERSE_MODE_REGISTRY

Docs
----
docs\\universe-packaging.md
docs\\local-universe-service.md
docs\\universe-e2e-product-scenario.md
"""
    (package_root / "README-PORTABLE.txt").write_text(readme, encoding="utf-8")
    (package_root / "VERSION.txt").write_text(
        json.dumps(
            {
                "schema": "universe.portable-package.v1",
                "package_name": package_name,
                "built_at": datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "includes_python": False,
                "data_dir": "data",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    zip_path = None
    if make_zip:
        zip_path = output_dir / f"{package_name}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in package_root.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(output_dir).as_posix())

    return {
        "schema": "universe.portable-build.v1",
        "status": "BUILT",
        "package_dir": str(package_root),
        "zip_path": str(zip_path) if zip_path else None,
        "package_name": package_name,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Universe portable package")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist" / "portable",
        help="Directory that receives the portable folder and zip",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Only create the folder, skip zip",
    )
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = build_portable(args.output_dir, make_zip=not args.no_zip)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
