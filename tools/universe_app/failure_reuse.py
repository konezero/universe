"""Project-local, review-only failure recall over the Memory Candidate store.

Matching is explicit, not semantic/causal inference. Feedback never changes the
candidate's original validation or promotes it to adopted knowledge.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from knowledge_redaction import SECRET_VALUE_PATTERNS


SCHEMA = "universe.failure-knowledge.v1"
QUERY_SCHEMA = "universe.failure-recall.v1"
REUSE_SCHEMA = "universe.failure-reuse-observation.v1"
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")
RECALL_STATES = frozenset({"REVIEW_REQUIRED", "KEEP"})
SIGNATURE_FIELDS = ("component", "operation", "error_code")


class FailureReuseError(ValueError):
    def __init__(self, code: str, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _object(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    if (
        not isinstance(value, Mapping)
        or not required <= set(value)
        or set(value) - required - (optional or set())
    ):
        raise FailureReuseError(
            "FAILURE_REUSE_FIELDS_INVALID", "missing or unsupported fields"
        )
    return dict(value)


def _text(value: Any, *, maximum: int = 600) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise FailureReuseError(
            "FAILURE_REUSE_TEXT_INVALID", "bounded non-empty text required"
        )
    if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
        raise FailureReuseError(
            "FAILURE_REUSE_SECRET_FORBIDDEN", "secret-like values are not accepted"
        )
    return value.strip()


def _key(value: Any) -> str:
    text = _text(value, maximum=128)
    if not KEY.fullmatch(text):
        raise FailureReuseError(
            "FAILURE_REUSE_KEY_INVALID", "invalid structured identifier"
        )
    return text.casefold()


def _texts(value: Any, *, refs: bool = False, required: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 16 or (required and not value):
        raise FailureReuseError("FAILURE_REUSE_LIST_INVALID", "expected a bounded list")
    result = sorted(set(_text(item) for item in value))
    if refs:
        for ref in result:
            try:
                parsed = urlsplit(ref)
            except ValueError as error:
                raise FailureReuseError(
                    "FAILURE_REUSE_EVIDENCE_REF_INVALID", "invalid evidence URI"
                ) from error
            if (
                parsed.scheme
                not in {
                    "repo",
                    "universe",
                    "test-run",
                    "task-frame",
                    "session-bus",
                    "git",
                    "conversation",
                    "https",
                    "http",
                }
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
            ):
                raise FailureReuseError(
                    "FAILURE_REUSE_EVIDENCE_REF_INVALID",
                    "credential-free evidence URI required",
                )
    return result


def _context(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 12:
        raise FailureReuseError(
            "FAILURE_REUSE_CONTEXT_INVALID", "context must have at most 12 dimensions"
        )
    result = {
        _key(key): _text(item, maximum=128).casefold() for key, item in value.items()
    }
    if len(result) != len(value):
        raise FailureReuseError(
            "FAILURE_REUSE_CONTEXT_INVALID", "duplicate normalized context dimension"
        )
    return result


def normalize_failure_knowledge(value: Any) -> dict[str, Any]:
    value = _object(
        value,
        {
            "failure_ref",
            "observation_kind",
            *SIGNATURE_FIELDS,
            "symptom",
            "cause",
            "remedy",
            "validation",
            "limitations",
            "evidence_refs",
        },
        {"schema"},
    )
    if value.get("schema", SCHEMA) != SCHEMA:
        raise FailureReuseError(
            "FAILURE_KNOWLEDGE_SCHEMA_INVALID", "unsupported failure knowledge schema"
        )
    kind = _text(value["observation_kind"]).upper()
    if kind not in {"LIVE_OBSERVATION", "RETROSPECTIVE_OBSERVATION"}:
        raise FailureReuseError(
            "FAILURE_OBSERVATION_KIND_INVALID", "explicit observation origin required"
        )
    cause = _object(value["cause"], {"key", "summary", "state"})
    cause_state = _text(cause["state"]).upper()
    if cause_state not in {"CONFIRMED", "HYPOTHESIS", "UNKNOWN"}:
        raise FailureReuseError(
            "FAILURE_CAUSE_STATE_INVALID", "invalid reported cause state"
        )
    remedy = _object(value["remedy"], {"summary", "applicability"})
    raw_validation = value["validation"]
    if not isinstance(raw_validation, list) or not 1 <= len(raw_validation) <= 12:
        raise FailureReuseError(
            "FAILURE_VALIDATION_INVALID", "1..12 explicit validation planes required"
        )
    validation, planes = [], set()
    for item in raw_validation:
        item = _object(item, {"plane", "status", "evidence_refs"})
        plane, status = _key(item["plane"]), _text(item["status"]).upper()
        if plane in planes or status not in {
            "PASS",
            "FAIL",
            "NOT_RUN",
            "NOT_APPLICABLE",
        }:
            raise FailureReuseError(
                "FAILURE_VALIDATION_INVALID",
                "unique plane and explicit validation state required",
            )
        planes.add(plane)
        validation.append(
            {
                "plane": plane,
                "status": status,
                "evidence_refs": _texts(
                    item["evidence_refs"],
                    refs=True,
                    required=status in {"PASS", "FAIL"},
                ),
            }
        )
    material = {
        "schema": SCHEMA,
        "failure_ref": _key(value["failure_ref"]),
        "observation_kind": kind,
        **{key: _key(value[key]) for key in SIGNATURE_FIELDS},
        "symptom": _text(value["symptom"]),
        "cause": {
            "key": _key(cause["key"]),
            "summary": _text(cause["summary"]),
            "state": cause_state,
        },
        "remedy": {
            "summary": _text(remedy["summary"]),
            "applicability": _context(remedy["applicability"]),
        },
        "validation": sorted(validation, key=lambda item: item["plane"]),
        "limitations": _texts(value["limitations"]),
        "evidence_refs": _texts(value["evidence_refs"], refs=True, required=True),
    }
    if len(canonical(material)) > 12000:
        raise FailureReuseError(
            "FAILURE_KNOWLEDGE_TOO_LARGE",
            "failure knowledge exceeds the 12000-character bound",
        )
    return material


def failure_candidate_id(project_id: str, failure: Mapping[str, Any]) -> str:
    return (
        "memory_failure_"
        + digest({"project_id": project_id, "failure_ref": failure["failure_ref"]})[:24]
    )


def normalize_failure_query(value: Any) -> dict[str, Any]:
    value = _object(value, set(SIGNATURE_FIELDS), {"cause_key", "context", "limit"})
    limit = value.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise FailureReuseError("FAILURE_REUSE_LIMIT_INVALID", "limit must be 1..20")
    return {
        **{key: _key(value[key]) for key in SIGNATURE_FIELDS},
        "cause_key": _key(value["cause_key"])
        if value.get("cause_key") is not None
        else None,
        "context": _context(value.get("context", {})),
        "limit": limit,
    }


def match_failure(
    failure: Mapping[str, Any], query: Mapping[str, Any]
) -> dict[str, Any] | None:
    if any(failure[key] != query[key] for key in SIGNATURE_FIELDS):
        return None
    if query["cause_key"] is not None and failure["cause"]["key"] != query["cause_key"]:
        return None
    required = failure["remedy"]["applicability"]
    context = query["context"]
    if any(key in context and context[key] != item for key, item in required.items()):
        return None
    missing = sorted(set(required) - set(context))
    cause_confirmed = bool(
        query["cause_key"] and failure["cause"]["state"] == "CONFIRMED"
    )
    return {
        "basis": "EXACT_SIGNATURE_AND_REPORTED_CAUSE"
        if cause_confirmed
        else "EXACT_SIGNATURE_ONLY",
        "cause_confirmation_required": not cause_confirmed,
        "applicability": "CONTEXT_REQUIRED" if missing else "CONTEXT_MATCHED",
        "missing_context": missing,
        "causal_inference": "NONE",
    }


class FailureReuseService:
    """Thin owner over the existing store connection and candidate row adapter."""

    def __init__(self, store: Any) -> None:
        self.store = store
        with store._connection() as connection:
            self.initialize(connection)

    @staticmethod
    def initialize(connection: Any) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS memory_batch_attempt_evidence (
                project_id TEXT NOT NULL REFERENCES project_connection(project_id) ON DELETE CASCADE,
                run_id TEXT NOT NULL REFERENCES memory_batch_run(run_id) ON DELETE CASCADE,
                attempt INTEGER NOT NULL CHECK(attempt > 0),
                evidence_json TEXT NOT NULL,
                PRIMARY KEY(run_id, attempt)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS failure_reuse_observation (
                project_id TEXT NOT NULL REFERENCES project_connection(project_id) ON DELETE CASCADE,
                reuse_ref TEXT NOT NULL,
                candidate_id TEXT NOT NULL REFERENCES memory_candidate(candidate_id) ON DELETE CASCADE,
                candidate_digest TEXT NOT NULL,
                observation_digest TEXT NOT NULL,
                observation_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY(project_id, reuse_ref)
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS failure_reuse_candidate
            ON failure_reuse_observation(project_id, candidate_id, candidate_digest)
        """)

    def query(self, project_id: str, value: Any) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        project_id = project["project_id"]
        query = normalize_failure_query(value)
        with self.store._connection() as connection:
            # Match before limiting: ordinary/recent candidates cannot hide an
            # older relevant failure. A feedback aggregate never rewrites it.
            rows = connection.execute(
                """
                SELECT * FROM memory_candidate WHERE project_id = ?
                AND state IN ('REVIEW_REQUIRED', 'KEEP')
                AND json_extract(candidate_json, '$.failure.component') = ?
                AND json_extract(candidate_json, '$.failure.operation') = ?
                AND json_extract(candidate_json, '$.failure.error_code') = ?
                ORDER BY candidate_id
            """,
                (project["project_id"], *(query[key] for key in SIGNATURE_FIELDS)),
            ).fetchall()
            matches = []
            for row in rows:
                candidate = self.store._memory_candidate_row(row)
                if self._excluded_by_relation(
                    connection, project_id, candidate["candidate_id"]
                ):
                    continue
                match = match_failure(candidate["failure"], query)
                if match is None:
                    continue
                feedback = connection.execute(
                    """
                    SELECT json_extract(observation_json, '$.outcome') AS outcome, COUNT(*) AS count
                    FROM failure_reuse_observation
                    WHERE project_id = ? AND candidate_id = ? AND candidate_digest = ?
                    GROUP BY outcome
                """,
                    (
                        project_id,
                        candidate["candidate_id"],
                        candidate["candidate_digest"],
                    ),
                ).fetchall()
                matches.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "candidate_digest": candidate["candidate_digest"],
                        "candidate_state": candidate["state"],
                        "summary": candidate["summary"],
                        "failure": candidate["failure"],
                        "match": match,
                        "reuse_outcomes": {
                            item["outcome"]: item["count"] for item in feedback
                        },
                        "reuse_outcomes_scope": "ALL_RECORDED_CONTEXTS_AND_VALIDATION_PLANES",
                    }
                )
        matches.sort(
            key=lambda item: (
                len(item["match"]["missing_context"]),
                item["match"]["cause_confirmation_required"],
                item["candidate_id"],
            )
        )
        result = {
            "schema": QUERY_SCHEMA,
            "project_id": project["project_id"],
            "status": "FAILURE_EVIDENCE_FOUND"
            if matches
            else "FAILURE_EVIDENCE_NOT_FOUND",
            "query": query,
            "matches": matches[: query["limit"]],
            "match_count": len(matches),
            "truncated": len(matches) > query["limit"],
            "policy": "CANDIDATE_ONLY",
            "validation_basis": "RECORDED_EVIDENCE_NOT_REEXECUTED",
            "next_operation": "REVIEW_FAILURE_EVIDENCE"
            if matches
            else "RESEARCH_GAP_REVIEW",
            "effects": {
                "automatic_patch": False,
                "automatic_research": False,
                "canonical_adoption": False,
                "source_write": "NONE",
            },
        }
        result["retrieval_digest"] = digest(result)
        return result

    def record_batch_attempt(
        self, connection: Any, result: Mapping[str, Any], now: str
    ) -> dict[str, Any]:
        """Persist with the run transition; a retry is not proof a remedy was used."""
        project_id, run_id = result["project_id"], result["run_id"]
        attempt, status = result.get("attempt", 1), result["status"]
        if status not in {"FAILED", "COMPLETED"}:
            raise FailureReuseError(
                "FAILURE_ATTEMPT_STATE_INVALID", "terminal batch state required"
            )
        existing = connection.execute(
            "SELECT evidence_json FROM memory_batch_attempt_evidence WHERE run_id = ? AND attempt = ?",
            (run_id, attempt),
        ).fetchone()
        if existing is not None:
            evidence = json.loads(existing["evidence_json"])
            if evidence["status"] != status:
                raise FailureReuseError(
                    "FAILURE_ATTEMPT_CONFLICT",
                    "attempt already has another terminal state",
                    409,
                )
            return evidence
        source_ref = f"universe://projects/{project_id}/memory-batches/{run_id}/attempts/{attempt}"
        execution = result.get("execution") or {}
        evidence = {
            "schema": "universe.memory-batch-attempt-evidence.v1",
            "project_id": project_id,
            "run_id": run_id,
            "attempt": attempt,
            "status": status,
            "source_ref": source_ref,
            "recorded_at": now,
            "validation": {
                "plane": "memory_batch_operation",
                "status": "PASS" if status == "COMPLETED" else "FAIL",
            },
            "remedy_application": "UNKNOWN",
            "cause_resolution": "UNKNOWN",
            "effects": {
                "automatic_retry": False,
                "automatic_patch": False,
                "canonical_adoption": False,
            },
        }
        if result.get("retry_failure_recall") is not None:
            evidence["retry_failure_recall"] = result["retry_failure_recall"]
            evidence["candidate_use_basis"] = "CONTEXT_PREPARED_NOT_PROVEN_APPLIED"
        if status == "FAILED":
            code = str(
                execution.get("error_code")
                or (result.get("failure") or {}).get("reason")
                or "UNKNOWN"
            )
            if not KEY.fullmatch(code):
                code = "UNKNOWN"
            query = {
                "component": "memory_batch",
                "operation": str(result["stage"]).lower(),
                "error_code": code,
                "context": {"execution_plane": "governed_task_frame"},
            }
            # Do not derive a cause key from an error code, nor create knowledge
            # claiming an unobserved remedy. Matching remains review-only.
            evidence["failure_recall"] = self.query(project_id, query)
        previous = connection.execute(
            "SELECT evidence_json FROM memory_batch_attempt_evidence WHERE run_id = ? AND attempt < ? ORDER BY attempt DESC LIMIT 1",
            (run_id, attempt),
        ).fetchone()
        if previous is not None:
            prior = json.loads(previous["evidence_json"])
            evidence["previous_attempt_ref"] = prior["source_ref"]
            evidence["previous_failure_candidates"] = [
                {key: hit[key] for key in ("candidate_id", "candidate_digest")}
                for hit in prior.get("failure_recall", {}).get("matches", [])
            ]
            evidence.setdefault("candidate_use_basis", "SURFACED_NOT_PROVEN_USED")
        connection.execute(
            "INSERT INTO memory_batch_attempt_evidence(project_id, run_id, attempt, evidence_json) VALUES (?, ?, ?, ?)",
            (project_id, run_id, attempt, canonical(evidence)),
        )
        return evidence

    def batch_attempts(self, project_id: str, run_id: Any) -> dict[str, Any]:
        project_id = self.store.get_project(project_id)["project_id"]
        run_id = _text(run_id, maximum=128)
        with self.store._connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM memory_batch_run WHERE project_id = ? AND run_id = ?",
                    (project_id, run_id),
                ).fetchone()
                is None
            ):
                raise FailureReuseError(
                    "FAILURE_ATTEMPT_RUN_NOT_FOUND",
                    "batch run not found in this project",
                    404,
                )
            rows = connection.execute(
                "SELECT evidence_json FROM memory_batch_attempt_evidence WHERE project_id = ? AND run_id = ? ORDER BY attempt DESC LIMIT 21",
                (project_id, run_id),
            ).fetchall()
        return {
            "schema": "universe.memory-batch-attempt-history.v1",
            "project_id": project_id,
            "run_id": run_id,
            "attempts": [json.loads(row["evidence_json"]) for row in rows[:20]],
            "truncated": len(rows) > 20,
            "limit": 20,
            "policy": "OBSERVED_NOT_PROMOTED",
        }

    def observations(self, project_id: str, candidate_id: Any) -> dict[str, Any]:
        project_id = self.store.get_project(project_id)["project_id"]
        candidate_id = _text(candidate_id, maximum=128)
        if not KEY.fullmatch(candidate_id):
            raise FailureReuseError(
                "FAILURE_REUSE_CANDIDATE_INVALID", "exact candidate id required"
            )
        with self.store._connection() as connection:
            found = connection.execute(
                "SELECT 1 FROM memory_candidate WHERE project_id = ? AND candidate_id = ?",
                (project_id, candidate_id),
            ).fetchone()
            if found is None:
                raise FailureReuseError(
                    "FAILURE_REUSE_CANDIDATE_NOT_FOUND",
                    "candidate not found in this project",
                    404,
                )
            rows = connection.execute(
                """
                SELECT observation_json FROM failure_reuse_observation
                WHERE project_id = ? AND candidate_id = ?
                ORDER BY recorded_at DESC, reuse_ref LIMIT 21
            """,
                (project_id, candidate_id),
            ).fetchall()
        return {
            "schema": REUSE_SCHEMA,
            "status": "FAILURE_REUSE_OBSERVATIONS_COLLECTED",
            "project_id": project_id,
            "candidate_id": candidate_id,
            "observations": [json.loads(row["observation_json"]) for row in rows[:20]],
            "limit": 20,
            "truncated": len(rows) > 20,
            "policy": "OBSERVED_NOT_PROMOTED",
        }

    def record_reuse(self, project_id: str, value: Any) -> tuple[dict[str, Any], bool]:
        project = self.store.get_project(project_id)
        project_id = project["project_id"]
        value = _object(
            value,
            {
                "reuse_ref",
                "candidate_id",
                "candidate_digest",
                "query",
                "outcome",
                "validation",
                "evidence_refs",
                "limitations",
            },
        )
        candidate_id = _text(value["candidate_id"], maximum=128)
        expected_digest = _text(value["candidate_digest"], maximum=64)
        if not KEY.fullmatch(candidate_id) or not HEX64.fullmatch(expected_digest):
            raise FailureReuseError(
                "FAILURE_REUSE_CANDIDATE_INVALID",
                "exact candidate id and digest required",
            )
        outcome = _text(value["outcome"]).upper()
        validation = _object(value["validation"], {"plane", "status"})
        status = _text(validation["status"]).upper()
        if outcome not in {"RESOLVED", "NOT_RESOLVED", "UNKNOWN"} or status not in {
            "PASS",
            "FAIL",
            "NOT_RUN",
        }:
            raise FailureReuseError(
                "FAILURE_REUSE_OUTCOME_INVALID",
                "explicit observed outcome and validation required",
            )
        if (outcome == "RESOLVED" and status != "PASS") or (
            outcome == "NOT_RESOLVED" and status != "FAIL"
        ):
            raise FailureReuseError(
                "FAILURE_REUSE_VALIDATION_REQUIRED",
                "resolved/unresolved outcomes require PASS/FAIL evidence",
            )
        material = {
            "schema": REUSE_SCHEMA,
            "project_id": project["project_id"],
            "reuse_ref": _key(value["reuse_ref"]),
            "candidate_id": candidate_id,
            "candidate_digest": expected_digest,
            "query": normalize_failure_query(value["query"]),
            "outcome": outcome,
            "validation": {"plane": _key(validation["plane"]), "status": status},
            "evidence_refs": _texts(
                value["evidence_refs"], refs=True, required=status != "NOT_RUN"
            ),
            "limitations": _texts(value["limitations"]),
        }
        observation_digest = digest(material)
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM failure_reuse_observation WHERE project_id = ? AND reuse_ref = ?",
                (project_id, material["reuse_ref"]),
            ).fetchone()
            if existing is not None:
                if existing["observation_digest"] != observation_digest:
                    raise FailureReuseError(
                        "FAILURE_REUSE_CONFLICT",
                        "reuse_ref already identifies different evidence",
                        409,
                    )
                return json.loads(existing["observation_json"]), False
            row = connection.execute(
                "SELECT * FROM memory_candidate WHERE project_id = ? AND candidate_id = ?",
                (project_id, candidate_id),
            ).fetchone()
            if row is None:
                raise FailureReuseError(
                    "FAILURE_REUSE_CANDIDATE_NOT_FOUND",
                    "candidate not found in this project",
                    404,
                )
            candidate = self.store._memory_candidate_row(row)
            if (
                candidate["candidate_digest"] != expected_digest
                or candidate["state"] not in RECALL_STATES
                or "failure" not in candidate
            ):
                raise FailureReuseError(
                    "FAILURE_REUSE_CANDIDATE_STALE",
                    "candidate digest or review state is not eligible",
                    409,
                )
            if self._excluded_by_relation(connection, project_id, candidate_id):
                raise FailureReuseError(
                    "FAILURE_REUSE_CANDIDATE_STALE",
                    "candidate has conflicting or superseding evidence",
                    409,
                )
            match = match_failure(candidate["failure"], material["query"])
            if match is None:
                raise FailureReuseError(
                    "FAILURE_REUSE_MATCH_REQUIRED",
                    "failure signature, cause or context does not match",
                    409,
                )
            if outcome == "RESOLVED" and (
                match["cause_confirmation_required"] or match["missing_context"]
            ):
                raise FailureReuseError(
                    "FAILURE_REUSE_MATCH_UNCONFIRMED",
                    "confirm cause and applicability before claiming resolution",
                    409,
                )
            material["observation_digest"] = observation_digest
            material["recorded_at"] = datetime.now(timezone.utc).isoformat()
            material["effects"] = {
                "canonical_adoption": False,
                "candidate_validation_changed": False,
                "automatic_patch": False,
            }
            connection.execute(
                """
                INSERT INTO failure_reuse_observation(
                    project_id, reuse_ref, candidate_id, candidate_digest,
                    observation_digest, observation_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    project_id,
                    material["reuse_ref"],
                    candidate_id,
                    expected_digest,
                    observation_digest,
                    canonical(material),
                    material["recorded_at"],
                ),
            )
        return material, True

    @staticmethod
    def _excluded_by_relation(
        connection: Any, project_id: str, candidate_id: str
    ) -> bool:
        return (
            connection.execute(
                """
            SELECT 1 FROM memory_candidate_relation
            WHERE project_id = ? AND (
                (target_candidate_id = ? AND relation IN ('SUPERSEDES', 'CONFLICTS_WITH'))
                OR (candidate_id = ? AND relation IN ('CONFLICTS_WITH', 'MERGED_INTO', 'DUPLICATE_OF'))
            ) LIMIT 1
        """,
                (project_id, candidate_id, candidate_id),
            ).fetchone()
            is not None
        )
