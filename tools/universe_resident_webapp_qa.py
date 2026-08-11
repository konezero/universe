#!/usr/bin/env python3
"""Validate the resident Universe Web app without owning its lifecycle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from universe_app.resident_webapp_qa import run_resident_qa  # noqa: E402


def default_state_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Universe" / "server.json"
    return Path.home() / "AppData" / "Local" / "Universe" / "server.json"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--state", type=Path, default=default_state_path())
    value.add_argument(
        "--artifacts",
        type=Path,
        default=ROOT / ".ai" / "runtime" / "tmp" / "resident-webapp-qa",
    )
    value.add_argument("--timeout", type=float, default=30.0)
    value.add_argument("--http-only", action="store_true")
    value.add_argument("--output", type=Path)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = run_resident_qa(
        args.state,
        artifacts_dir=args.artifacts,
        timeout_seconds=max(1.0, args.timeout),
        browser=not args.http_only,
    )
    raw = json.dumps(report.as_dict(), indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw + "\n", encoding="utf-8")
    print(raw)
    return 0 if report.overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
