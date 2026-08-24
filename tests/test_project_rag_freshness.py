from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import UniverseError, UniverseStore  # noqa: E402


class ProjectRagFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "universe.sqlite3"
        self.store = UniverseStore(self.database)
        for project_id in ("alpha", "beta"):
            project_root = root / project_id
            project_root.mkdir()
            (project_root / "REPOSITORY_MANIFEST.md").write_text(
                f"# {project_id}\n", encoding="utf-8"
            )
            self.store.register_project(
                {"project_id": project_id, "project_root": str(project_root)}
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_memory(self, project_id: str, title: str, body: str, node_ref: str) -> dict:
        return self.store.create_project_memory(
            project_id,
            {
                "title": title,
                "body": body,
                "state": "OBSERVED",
                "node_ref": node_ref,
                "graph": "functional",
                "origin_ref": f"test://{project_id}/{title.casefold().replace(' ', '-')}",
            },
        )

    def set_metadata(
        self, memory_id: str, *, updated_at: str, retrieval_state: str = "ACTIVE"
    ) -> None:
        connection = sqlite3.connect(self.database)
        try:
            row = connection.execute(
                "SELECT memory_json FROM project_memory WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
            material = json.loads(row[0])
            material["updated_at"] = updated_at
            material["retrieval_state"] = retrieval_state
            connection.execute(
                "UPDATE project_memory SET memory_json = ?, updated_at = ? WHERE memory_id = ?",
                (
                    json.dumps(material, sort_keys=True, separators=(",", ":")),
                    updated_at,
                    memory_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_result_lineage_ingestion_is_idempotent_and_ignores_status_text(self) -> None:
        empty = self.store.sync_project_lineage_memories("alpha")
        self.assertEqual(0, empty["observed_count"])
        self.store.create_room_message(
            "alpha",
            {
                "kind": "STATUS",
                "sender": "PROJECT_MASTER",
                "body": "notification only",
                "idempotency_key": "status-1",
            },
        )
        result_message, _ = self.store.create_room_message(
            "alpha",
            {
                "kind": "RESULT",
                "sender": "PROJECT_MASTER",
                "body": "Broker reconciliation validation passed",
                "idempotency_key": "result-1",
            },
        )

        first = self.store.sync_project_lineage_memories("alpha")
        second = self.store.sync_project_lineage_memories("alpha")
        memories = self.store.list_project_memories("alpha", link_state="LINKED")

        self.assertEqual(1, first["created_count"])
        self.assertEqual(0, second["created_count"])
        self.assertEqual(1, second["replayed_count"])
        self.assertEqual(1, len(memories))
        self.assertEqual("ROOM_RESULT", memories[0]["lineage"]["source_kind"])
        self.assertEqual(result_message["message_id"], memories[0]["lineage"]["event_id"])
        self.assertNotIn("notification only", json.dumps(memories))
        self.assertEqual("COMPLETE", second["status"])

        conflict_event = {
            "project_id": "alpha",
            "producer_kind": "UNIVERSE_STORE",
            "source_kind": "ROOM_RESULT",
            "event_id": result_message["message_id"],
            "event_type": "RESULT_ATTACHED",
            "title": f"Project Room result · {result_message['message_id']}",
            "summary": "changed result material",
            "source_ref": f"universe://project-room-messages/{result_message['message_id']}",
            "observed_at": result_message["created_at"],
        }
        with self.assertRaises(UniverseError) as conflict:
            self.store.ingest_project_lineage_memory("alpha", conflict_event)
        self.assertEqual("LINEAGE_MEMORY_SOURCE_CONFLICT", conflict.exception.code)
        with self.assertRaises(UniverseError) as mismatch:
            self.store.ingest_project_lineage_memory(
                "beta", {**conflict_event, "summary": "Broker reconciliation validation passed"}
            )
        self.assertEqual("LINEAGE_MEMORY_PROJECT_MISMATCH", mismatch.exception.code)

        retrieval = self.store.build_project_llm_retrieval_context(
            "alpha", query="broker reconciliation"
        )
        self.assertEqual(1, len(retrieval["memory"]["hits"]))
        hit = retrieval["memory"]["hits"][0]
        self.assertTrue(hit["source_ref"].startswith("universe://project-room-messages/"))
        self.assertIn("created_at", hit)
        self.assertIn("updated_at", hit)
        self.assertIn("age_seconds", hit["freshness"])

    def test_retrieval_excludes_unrelated_and_orders_newest_with_state_handling(self) -> None:
        old = self.create_memory("alpha", "Old broker", "broker reconciliation evidence", "risk")
        new = self.create_memory("alpha", "New broker", "broker reconciliation evidence", "risk")
        unrelated = self.create_memory("alpha", "Unrelated", "orchid gardening notes", "garden")
        selected = self.create_memory("alpha", "Selected node", "zero lexical overlap", "risk")
        conflicted = self.create_memory("alpha", "Conflicted broker", "broker reconciliation evidence", "risk")
        superseded = self.create_memory("alpha", "Superseded broker", "broker reconciliation evidence", "risk")
        foreign = self.create_memory("beta", "Foreign broker", "broker reconciliation evidence", "risk")
        self.set_metadata(old["memory_id"], updated_at="2020-01-01T00:00:00Z")
        self.set_metadata(new["memory_id"], updated_at="2026-08-23T00:00:00Z")
        self.set_metadata(conflicted["memory_id"], updated_at="2026-08-24T00:00:00Z", retrieval_state="CONFLICTED")
        self.set_metadata(superseded["memory_id"], updated_at="2026-08-24T01:00:00Z", retrieval_state="SUPERSEDED")

        retrieval = self.store.build_project_llm_retrieval_context(
            "alpha", query="broker reconciliation", memory_limit=20
        )
        hits = retrieval["memory"]["hits"]
        ids = [item["memory_id"] for item in hits]

        self.assertEqual(new["memory_id"], ids[0])
        self.assertNotIn(unrelated["memory_id"], ids)
        self.assertNotIn(selected["memory_id"], ids)
        self.assertNotIn(superseded["memory_id"], ids)
        self.assertNotIn(foreign["memory_id"], ids)
        old_hit = next(item for item in hits if item["memory_id"] == old["memory_id"])
        conflict_hit = next(item for item in hits if item["memory_id"] == conflicted["memory_id"])
        self.assertEqual("STALE", old_hit["freshness"]["state"])
        self.assertEqual("CONFLICTED", conflict_hit["retrieval_state"])

        node_retrieval = self.store.build_project_llm_retrieval_context(
            "alpha", query="no matching vocabulary", node_ids=["risk"], memory_limit=20
        )
        node_ids = {item["memory_id"] for item in node_retrieval["memory"]["hits"]}
        self.assertIn(selected["memory_id"], node_ids)
        self.assertTrue(
            all(item["match"]["node_match"] for item in node_retrieval["memory"]["hits"])
        )


if __name__ == "__main__":
    unittest.main()
