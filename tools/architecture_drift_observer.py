"""Structured, redacted architecture drift observations for Universe."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "universe.architecture-drift-observation.v1"
ALLOWED_STATES = frozenset({"OPEN", "CLOSED"})
FORBIDDEN_KEYS = frozenset(
    {
        "transcript",
        "prompt",
        "response",
        "reasoning",
        "source_path",
        "provider_session_ref",
        "session_ref",
        "command",
        "token",
        "secret",
    }
)


class ArchitectureDriftError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArchitectureDriftError(f"{field} must be non-empty text")
    return value.strip()


def _sha(value: Any, field: str) -> str:
    text = _text(value, field).lower()
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        raise ArchitectureDriftError(f"{field} must be a 40-character Git SHA")
    return text


def _validate_redacted(value: Mapping[str, Any]) -> None:
    for key in value:
        normalized = str(key).lower()
        if normalized in FORBIDDEN_KEYS or normalized.endswith("_path"):
            raise ArchitectureDriftError(f"forbidden drift field: {key}")


class ArchitectureDriftStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS architecture_drift_observation (
                    incident_id TEXT PRIMARY KEY,
                    expected_contract_ref TEXT NOT NULL,
                    observed_behavior_code TEXT NOT NULL,
                    drift_class TEXT NOT NULL,
                    source_commit TEXT NOT NULL,
                    validation_ref TEXT NOT NULL,
                    correction_commit TEXT,
                    regression_test_ref TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    closed_at TEXT,
                    CHECK(state IN ('OPEN','CLOSED'))
                )
                """
            )

    def record(self, value: Mapping[str, Any]) -> dict[str, Any]:
        _validate_redacted(value)
        material = {
            "expected_contract_ref": _text(
                value.get("expected_contract_ref"), "expected_contract_ref"
            ),
            "observed_behavior_code": _text(
                value.get("observed_behavior_code"), "observed_behavior_code"
            ).upper(),
            "drift_class": _text(value.get("drift_class"), "drift_class").upper(),
            "source_commit": _sha(value.get("source_commit"), "source_commit"),
            "validation_ref": _text(value.get("validation_ref"), "validation_ref"),
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        incident_id = "drift_" + digest[:24]
        created_at = _now()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """
                INSERT OR IGNORE INTO architecture_drift_observation(
                    incident_id, expected_contract_ref, observed_behavior_code,
                    drift_class, source_commit, validation_ref, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?)
                """,
                (
                    incident_id,
                    material["expected_contract_ref"],
                    material["observed_behavior_code"],
                    material["drift_class"],
                    material["source_commit"],
                    material["validation_ref"],
                    created_at,
                ),
            )
        return self.get(incident_id)

    def close(
        self,
        incident_id: str,
        *,
        correction_commit: Any,
        regression_test_ref: Any,
    ) -> dict[str, Any]:
        correction = _sha(correction_commit, "correction_commit")
        regression = _text(regression_test_ref, "regression_test_ref")
        closed_at = _now()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            result = connection.execute(
                """
                UPDATE architecture_drift_observation
                SET correction_commit = ?, regression_test_ref = ?,
                    state = 'CLOSED', closed_at = ?
                WHERE incident_id = ?
                """,
                (correction, regression, closed_at, _text(incident_id, "incident_id")),
            )
            if result.rowcount != 1:
                raise ArchitectureDriftError("unknown incident_id")
        return self.get(incident_id)

    def get(self, incident_id: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM architecture_drift_observation WHERE incident_id = ?",
                (_text(incident_id, "incident_id"),),
            ).fetchone()
        if row is None:
            raise ArchitectureDriftError("unknown incident_id")
        return {"schema": SCHEMA, **dict(row)}
