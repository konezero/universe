#!/usr/bin/env python3
"""Build a relocatable Universe portable folder (+ optional zip).

Default: Host Python on PATH.
Optional: --with-python / --python-zip embeds Windows CPython embeddable package.
Portable data lives under <package>/data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
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

# Official Windows embeddable package (amd64). Override with --python-zip.
DEFAULT_EMBED_PYTHON_URL = (
    "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
)


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


def env_snippet(*, python_cmd: str) -> str:
    return rf"""@echo off
set "UNIVERSE_PORTABLE_ROOT=%~dp0"
if "%UNIVERSE_PORTABLE_ROOT:~-1%"=="\" set "UNIVERSE_PORTABLE_ROOT=%UNIVERSE_PORTABLE_ROOT:~0,-1%"
set "UNIVERSE_DATA_DIR=%UNIVERSE_PORTABLE_ROOT%\data"
set "UNIVERSE_STATE_FILE=%UNIVERSE_DATA_DIR%\server.json"
set "UNIVERSE_DATABASE=%UNIVERSE_DATA_DIR%\universe.sqlite3"
set "UNIVERSE_LOG_FILE=%UNIVERSE_PORTABLE_ROOT%\logs\service.log"
set "UNIVERSE_MODE_REGISTRY=%UNIVERSE_PORTABLE_ROOT%\.ai\runtime\project_instance\mode_registry.json"
set "UNIVERSE_PYTHON={python_cmd}"
if not exist "%UNIVERSE_DATA_DIR%" mkdir "%UNIVERSE_DATA_DIR%"
if not exist "%UNIVERSE_PORTABLE_ROOT%\logs" mkdir "%UNIVERSE_PORTABLE_ROOT%\logs"
cd /d "%UNIVERSE_PORTABLE_ROOT%"
"""


def write_launchers(package_root: Path, *, includes_python: bool) -> None:
    python_cmd = (
        r"%UNIVERSE_PORTABLE_ROOT%\runtime\python\python.exe"
        if includes_python
        else "python"
    )
    base = env_snippet(python_cmd=python_cmd)
    # env_snippet already sets UNIVERSE_PYTHON; for PATH mode still allow runtime override
    if not includes_python:
        base = base.replace(
            'set "UNIVERSE_PYTHON=python"\r\n',
            'set "UNIVERSE_PYTHON=python"\r\n'
            + 'if exist "%UNIVERSE_PORTABLE_ROOT%\\runtime\\python\\python.exe" set "UNIVERSE_PYTHON=%UNIVERSE_PORTABLE_ROOT%\\runtime\\python\\python.exe"\r\n',
        )
    write_launcher(
        package_root / "Start-Universe.cmd",
        base
        + '"%UNIVERSE_PYTHON%" tools\\universe_server.py start --open-ui\r\n'
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Start-Universe-Headless.cmd",
        base
        + '"%UNIVERSE_PYTHON%" tools\\universe_server.py start --no-open-ui\r\n'
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Stop-Universe.cmd",
        base
        + '"%UNIVERSE_PYTHON%" tools\\universe_server.py stop\r\n'
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Status-Universe.cmd",
        base
        + '"%UNIVERSE_PYTHON%" tools\\universe_server.py status\r\n'
        + "pause\r\n"
        + "exit /b %ERRORLEVEL%\r\n",
    )
    write_launcher(
        package_root / "Start-Universe-Tray.cmd",
        base
        + '"%UNIVERSE_PYTHON%" tools\\universe_server.py tray --start-service\r\n'
        + "exit /b %ERRORLEVEL%\r\n",
    )


def embed_python(package_root: Path, python_zip: Path) -> dict:
    runtime = package_root / "runtime" / "python"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(python_zip) as archive:
        archive.extractall(runtime)
    # Enable site-packages import path for embeddable distro (stdlib only is enough
    # for Universe tools, but pth unlock is still useful for future wheels).
    for pth in runtime.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8")
        lines = []
        for line in text.splitlines():
            if line.strip().startswith("#import site"):
                lines.append("import site")
            else:
                lines.append(line)
        if "import site" not in lines:
            lines.append("import site")
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    python_exe = runtime / "python.exe"
    if not python_exe.is_file():
        raise FileNotFoundError(f"python.exe missing after extract: {runtime}")
    return {
        "python_exe": str(python_exe),
        "source_zip": str(python_zip),
    }


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, open(dest, "wb") as handle:
        shutil.copyfileobj(response, handle)
    return dest


def build_portable(
    output_dir: Path,
    *,
    make_zip: bool,
    with_python: bool = False,
    python_zip: Path | None = None,
    python_url: str = DEFAULT_EMBED_PYTHON_URL,
) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    package_name = f"UniversePortable-{stamp}"
    if with_python:
        package_name += "-pyembed"
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

    python_meta = None
    if with_python:
        cache_dir = output_dir / "_python_cache"
        if python_zip is None:
            cached = cache_dir / Path(python_url).name
            if not cached.is_file():
                download_file(python_url, cached)
            python_zip = cached
        python_meta = embed_python(package_root, python_zip)

    write_launchers(package_root, includes_python=with_python)

    req_line = (
        "Bundled: runtime\\python\\python.exe (Windows embeddable CPython)"
        if with_python
        else "Requires: Python 3 on PATH (or place embeddable package under runtime\\python)"
    )
    readme = f"""Universe portable package
=========================

Built: {stamp} (UTC)
{req_line}

Quick start
-----------
1. Unzip anywhere (no admin required).
2. Double-click Start-Universe.cmd  (or Start-Universe-Tray.cmd)
3. Status-Universe.cmd / Stop-Universe.cmd for control

Data location
-------------
  data\\server.json
  data\\universe.sqlite3
  logs\\service.log

Environment (set by launchers)
------------------------------
UNIVERSE_DATA_DIR, UNIVERSE_STATE_FILE, UNIVERSE_DATABASE,
UNIVERSE_LOG_FILE, UNIVERSE_MODE_REGISTRY, UNIVERSE_PYTHON

Docs
----
docs\\universe-packaging.md
docs\\local-universe-service.md
docs\\universe-memory-rag.md
"""
    (package_root / "README-PORTABLE.txt").write_text(readme, encoding="utf-8")
    version = {
        "schema": "universe.portable-package.v1",
        "package_name": package_name,
        "built_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "includes_python": with_python,
        "data_dir": "data",
        "python": python_meta,
    }
    (package_root / "VERSION.txt").write_text(
        json.dumps(version, indent=2) + "\n", encoding="utf-8"
    )

    zip_path = None
    if make_zip:
        zip_path = output_dir / f"{package_name}.zip"
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in package_root.rglob("*"):
                if file_path.is_file():
                    archive.write(
                        file_path, file_path.relative_to(output_dir).as_posix()
                    )

    return {
        "schema": "universe.portable-build.v1",
        "status": "BUILT",
        "package_dir": str(package_root),
        "zip_path": str(zip_path) if zip_path else None,
        "package_name": package_name,
        "includes_python": with_python,
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
    parser.add_argument(
        "--with-python",
        action="store_true",
        help="Embed Windows CPython (download or --python-zip)",
    )
    parser.add_argument(
        "--python-zip",
        type=Path,
        default=None,
        help="Local path to python-*-embed-amd64.zip (skips download)",
    )
    parser.add_argument(
        "--python-url",
        default=DEFAULT_EMBED_PYTHON_URL,
        help="Embeddable CPython zip URL when --with-python and no --python-zip",
    )
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = build_portable(
        args.output_dir,
        make_zip=not args.no_zip,
        with_python=bool(args.with_python),
        python_zip=args.python_zip,
        python_url=args.python_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
