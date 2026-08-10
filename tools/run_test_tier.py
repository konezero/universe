#!/usr/bin/env python3
"""Run bounded Universe regression tiers from one versioned manifest."""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import time
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "test_tiers.json"


class TierError(ValueError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TierError(f"test tier manifest is unavailable: {error}") from error
    if not isinstance(value, dict) or value.get("schema") != "universe.test-tiers.v1":
        raise TierError("test tier manifest schema is invalid")
    tiers = value.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise TierError("test tier manifest must define tiers")
    return value


def selected_test_names(
    manifest: dict[str, Any], tier: str, changed_paths: list[str]
) -> list[str]:
    raw_tier = manifest["tiers"].get(tier)
    if not isinstance(raw_tier, dict):
        raise TierError(f"unknown test tier: {tier}")
    names = list(raw_tier.get("tests") or [])
    if tier == "changed" and changed_paths:
        names = []
        mappings = raw_tier.get("path_mappings") or {}
        for changed_path in changed_paths:
            normalized = changed_path.replace("\\", "/").lstrip("./")
            for pattern, mapped_names in mappings.items():
                if fnmatch.fnmatch(normalized, pattern):
                    names.extend(mapped_names)
        if not names:
            names = list(raw_tier.get("tests") or [])
    if not all(isinstance(name, str) and name for name in names):
        raise TierError(f"tier {tier} contains an invalid test name")
    return list(dict.fromkeys(names))


def build_suite(
    manifest: dict[str, Any], tier: str, changed_paths: list[str]
) -> unittest.TestSuite:
    raw_tier = manifest["tiers"][tier]
    loader = unittest.defaultTestLoader
    discovery = raw_tier.get("discovery")
    if isinstance(discovery, dict):
        start_dir = (ROOT / discovery["start_dir"]).resolve()
        return loader.discover(
            str(start_dir),
            pattern=discovery["pattern"],
            top_level_dir=str(start_dir),
        )
    suite = unittest.TestSuite()
    for name in selected_test_names(manifest, tier, changed_paths):
        suite.addTests(loader.loadTestsFromName(name))
    return suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Universe regression tier")
    parser.add_argument("tier", choices=("changed", "smoke", "contract", "full"))
    parser.add_argument("--path", action="append", default=[], dest="changed_paths")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--list", action="store_true", dest="list_only")
    parser.add_argument("--enforce-budget", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        manifest = load_manifest(args.manifest)
        names = selected_test_names(manifest, args.tier, args.changed_paths)
        if args.list_only and args.tier != "full":
            print("\n".join(names))
            return 0
        started = time.monotonic()
        result = unittest.TextTestRunner(verbosity=2).run(
            build_suite(manifest, args.tier, args.changed_paths)
        )
        elapsed = time.monotonic() - started
        target = manifest["tiers"][args.tier].get("target_seconds")
        print(
            json.dumps(
                {
                    "schema": "universe.test-tier-result.v1",
                    "tier": args.tier,
                    "successful": result.wasSuccessful(),
                    "tests_run": result.testsRun,
                    "elapsed_seconds": round(elapsed, 3),
                    "target_seconds": target,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        if not result.wasSuccessful():
            return 1
        if args.enforce_budget and isinstance(target, int) and elapsed > target:
            return 5
        return 0
    except (KeyError, TypeError, TierError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
