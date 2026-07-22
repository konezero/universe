from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "tools"))

from seed import (  # noqa: E402
    DEFAULT_SOURCE,
    SeedError,
    build_seed,
    inspect_seed,
    load_source,
    suggest_paths,
    validate_source,
)


class OfficialDevelopmentSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "official.sqlite"
        self.manifest = root / "manifest.json"
        build_seed(DEFAULT_SOURCE, self.database, self.manifest)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_has_expected_catalog_and_integrity(self) -> None:
        result = inspect_seed(self.database)

        self.assertEqual("official-development-seed-v0", result["metadata"]["seed_id"])
        self.assertEqual("CURATED_HYPOTHESIS", result["metadata"]["claim.evidence_level"])
        self.assertGreaterEqual(result["counts"]["route"], 4)
        self.assertGreaterEqual(result["counts"]["route_step"], 20)
        self.assertGreaterEqual(result["counts"]["failure_pattern"], 7)

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual("ok", connection.execute("PRAGMA integrity_check").fetchone()[0])
            self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            connection.close()

    def test_manifest_binds_source_and_database(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        database_sha = hashlib.sha256(self.database.read_bytes()).hexdigest()

        self.assertEqual(database_sha, manifest["database_sha256"])
        self.assertEqual(
            inspect_seed(self.database)["metadata"]["source_sha256"],
            manifest["source_sha256"],
        )

    def test_desktop_request_returns_candidate_without_state_effects(self) -> None:
        result = suggest_paths(
            self.database,
            project="Local trading workstation",
            kind="desktop-app",
            technologies=["Python", "PySide6", "SQLite"],
            goal="stable unattended operation with recoverable state",
        )

        self.assertEqual("FUTURE_PATH_CANDIDATES", result["status"])
        self.assertEqual("durable-desktop-state", result["candidates"][0]["route_id"])
        self.assertEqual("CURATED_HYPOTHESIS", result["candidates"][0]["support_level"])
        self.assertEqual("NOT_AVAILABLE", result["probabilities"])
        self.assertEqual(
            {
                "decision": "NONE",
                "current_anchor": "NONE",
                "authority": "NONE",
                "assignment": "NONE",
                "execution": "NONE",
            },
            result["effects"],
        )
        self.assertEqual("USER_SELECTION_REQUIRED", result["next_operation"])

    def test_agent_request_prefers_governed_agent_route(self) -> None:
        result = suggest_paths(
            self.database,
            project="Persistent coding agents",
            kind="agent-runtime",
            technologies=["python", "sqlite", "http-api"],
            goal="governed persistent worker execution",
        )

        self.assertEqual("governed-agent-execution", result["candidates"][0]["route_id"])

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaisesRegex(SeedError, "unknown project kind"):
            suggest_paths(
                self.database,
                project="Unknown",
                kind="imaginary-kind",
                technologies=[],
                goal="test",
            )

    def test_claim_boundary_cannot_be_relaxed(self) -> None:
        source = load_source(DEFAULT_SOURCE)
        source["claims"]["probabilities"] = "AVAILABLE"

        with self.assertRaisesRegex(SeedError, "claims exceed"):
            validate_source(source)

    def test_official_database_can_be_opened_read_only(self) -> None:
        connection = sqlite3.connect(
            f"file:{self.database.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO release_metadata(key, value) VALUES ('x', 'y')"
                )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
