"""Project-scoped, paged Galaxy proposal projection.

This read model intentionally does not evaluate RAG review contracts. Proposal
acceptance remains owned by the server Action and its transactional store path.
"""

import json
import sqlite3
from typing import Any


def list_goal_proposals(
    connection: sqlite3.Connection, project_id: str, *, limit: int, offset: int
) -> dict[str, Any]:
    if not 1 <= limit <= 200 or offset < 0:
        raise ValueError("limit must be 1..200 and offset must be nonnegative")
    rows = connection.execute(
        """
        SELECT candidate.candidate_id, candidate.project_id, candidate.kind,
               candidate.state, candidate.candidate_digest,
               candidate.candidate_json, candidate.updated_at,
               accepted.goal_id, accepted.candidate_digest AS accepted_digest,
               accepted.accepted_at
        FROM memory_candidate AS candidate
        LEFT JOIN memory_goal_acceptance AS accepted
          ON accepted.candidate_id = candidate.candidate_id
        WHERE candidate.project_id = ?
          AND (candidate.kind IN ('IDEA', 'HYPOTHESIS', 'PRODUCT')
               OR json_extract(candidate.candidate_json, '$.knowledge.kind') = 'USER_IDEA')
        ORDER BY candidate.updated_at DESC, candidate.candidate_id DESC
        LIMIT ? OFFSET ?
        """,
        (project_id, limit + 1, offset),
    ).fetchall()
    proposals = []
    for row in rows[:limit]:
        candidate = json.loads(row["candidate_json"])
        # Identity, decision state, and digest come from the authoritative row.
        candidate.update(
            candidate_id=row["candidate_id"], project_id=row["project_id"],
            kind=row["kind"], state=row["state"],
            candidate_digest=row["candidate_digest"], updated_at=row["updated_at"],
        )
        candidate["goal_acceptance"] = (
            {"goal_id": row["goal_id"], "candidate_digest": row["accepted_digest"],
             "accepted_at": row["accepted_at"]}
            if row["goal_id"] is not None else None
        )
        proposals.append(candidate)
    return {"proposals": proposals, "offset": offset, "limit": limit,
            "has_more": len(rows) > limit, "next_offset": offset + len(proposals)}
