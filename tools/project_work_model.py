from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping


INSTANCE_SCHEMA = "universe.project-work-template-instance.v1"
WORK_SURFACE_SCHEMA = "universe.project-work-surface.v1"

DDL = """
CREATE TABLE IF NOT EXISTS project_work_template_instance (
    instance_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL
        REFERENCES project_connection(project_id)
        ON DELETE CASCADE,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('PROJECT', 'GOAL')),
    scope_ref TEXT NOT NULL,
    goal_id TEXT,
    node_ref TEXT,
    template_id TEXT NOT NULL,
    template_digest TEXT NOT NULL,
    graph_json TEXT NOT NULL,
    documents_json TEXT NOT NULL,
    materialization_state TEXT NOT NULL
        CHECK(materialization_state IN ('SKELETON_READY')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, scope_kind, scope_ref)
);

CREATE INDEX IF NOT EXISTS project_work_template_instance_scope
ON project_work_template_instance(project_id, node_ref, scope_kind, scope_ref);
"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(DDL)


def _instance_material(
    *,
    project_id: str,
    scope_kind: str,
    scope_ref: str,
    goal_id: str | None,
    node_ref: str | None,
    title: str,
    template: Mapping[str, Any],
) -> dict[str, Any]:
    coordinates = {
        "project_id": project_id,
        "scope_kind": scope_kind,
        "scope_ref": scope_ref,
        "goal_id": goal_id,
        "node_ref": node_ref,
    }
    graph = {
        "schema": "universe.project-work-template-graph.v1",
        "coordinates": coordinates,
        "title": title,
        "nodes": list(template.get("nodes") or []),
        "edges": list(template.get("edges") or []),
    }
    documents = []
    for item in template.get("living_documents") or []:
        if not isinstance(item, Mapping):
            continue
        document = dict(item)
        document["template_document_id"] = str(item.get("document_id") or "")
        document["document_id"] = (
            f"{scope_kind.casefold()}-{scope_ref}-{item.get('document_id') or 'document'}"
        )
        document["coordinates"] = coordinates
        document["materialization_state"] = "SKELETON_READY"
        document["project_source_write"] = "NONE"
        documents.append(document)
    material = {
        "schema": INSTANCE_SCHEMA,
        "coordinates": coordinates,
        "title": title,
        "template_id": str(template["template_id"]),
        "graph": graph,
        "documents": documents,
        "materialization_state": "SKELETON_READY",
        "effects": {
            "project_source_write": "NONE",
            "project_seed_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
        },
    }
    material["template_digest"] = _digest(material)
    material["instance_id"] = "worktpl_" + _digest(
        {"project_id": project_id, "scope_kind": scope_kind, "scope_ref": scope_ref}
    )[:24]
    return material


def ensure_template_instance(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    scope_kind: str,
    scope_ref: str,
    title: str,
    template: Mapping[str, Any],
    goal_id: str | None = None,
    node_ref: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    normalized_scope = scope_kind.strip().upper()
    if normalized_scope not in {"PROJECT", "GOAL"}:
        raise ValueError("scope_kind must be PROJECT or GOAL")
    material = _instance_material(
        project_id=project_id,
        scope_kind=normalized_scope,
        scope_ref=scope_ref,
        goal_id=goal_id,
        node_ref=node_ref,
        title=title,
        template=template,
    )
    observed_at = now or _now()
    connection.execute(
        """
        INSERT INTO project_work_template_instance(
            instance_id, project_id, scope_kind, scope_ref, goal_id, node_ref,
            template_id, template_digest, graph_json, documents_json,
            materialization_state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SKELETON_READY', ?, ?)
        ON CONFLICT(project_id, scope_kind, scope_ref) DO UPDATE SET
            goal_id = excluded.goal_id,
            node_ref = excluded.node_ref,
            template_id = excluded.template_id,
            template_digest = excluded.template_digest,
            graph_json = excluded.graph_json,
            documents_json = excluded.documents_json,
            materialization_state = excluded.materialization_state,
            updated_at = CASE
                WHEN project_work_template_instance.template_digest != excluded.template_digest
                  OR project_work_template_instance.node_ref IS NOT excluded.node_ref
                THEN excluded.updated_at
                ELSE project_work_template_instance.updated_at
            END
        """,
        (
            material["instance_id"],
            project_id,
            normalized_scope,
            scope_ref,
            goal_id,
            node_ref,
            material["template_id"],
            material["template_digest"],
            _canonical(material["graph"]),
            _canonical(material["documents"]),
            observed_at,
            observed_at,
        ),
    )
    return get_template_instance(connection, material["instance_id"])


def get_template_instance(
    connection: sqlite3.Connection, instance_id: str
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM project_work_template_instance WHERE instance_id = ?",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise KeyError(instance_id)
    return {
        "schema": INSTANCE_SCHEMA,
        "instance_id": str(row["instance_id"]),
        "project_id": str(row["project_id"]),
        "scope_kind": str(row["scope_kind"]),
        "scope_ref": str(row["scope_ref"]),
        "goal_id": row["goal_id"],
        "node_ref": row["node_ref"],
        "template_id": str(row["template_id"]),
        "template_digest": str(row["template_digest"]),
        "graph": json.loads(str(row["graph_json"])),
        "documents": json.loads(str(row["documents_json"])),
        "materialization_state": str(row["materialization_state"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "effects": {
            "project_source_write": "NONE",
            "project_seed_write": "NONE",
            "authority": "NONE",
            "execution_assignment": "NONE",
        },
    }


def list_template_instances(
    connection: sqlite3.Connection, *, project_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT instance_id FROM project_work_template_instance
        WHERE project_id = ?
        ORDER BY CASE scope_kind WHEN 'PROJECT' THEN 0 ELSE 1 END, scope_ref
        """,
        (project_id,),
    ).fetchall()
    return [get_template_instance(connection, str(row["instance_id"])) for row in rows]


def backfill_template_instances(
    connection: sqlite3.Connection, *, template: Mapping[str, Any], now: str | None = None
) -> None:
    observed_at = now or _now()
    for row in connection.execute(
        "SELECT project_id, metadata_json FROM project_connection ORDER BY project_id"
    ).fetchall():
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        project_id = str(row["project_id"])
        ensure_template_instance(
            connection,
            project_id=project_id,
            scope_kind="PROJECT",
            scope_ref=project_id,
            title=str(metadata.get("display_name") or project_id),
            template=template,
            now=observed_at,
        )
    for row in connection.execute(
        "SELECT goal_id, project_id, scope_kind, node_ref, title FROM project_goal ORDER BY goal_id"
    ).fetchall():
        ensure_template_instance(
            connection,
            project_id=str(row["project_id"]),
            scope_kind="GOAL",
            scope_ref=str(row["goal_id"]),
            goal_id=str(row["goal_id"]),
            node_ref=row["node_ref"],
            title=str(row["title"]),
            template=template,
            now=observed_at,
        )
