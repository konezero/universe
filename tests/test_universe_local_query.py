from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import create_server, universe_mode_contract  # noqa: E402
from host_profile import HostProfileStore  # noqa: E402
from universe_file_index import sync_project_index_from_hook  # noqa: E402


class UniverseLocalQueryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp.name)
        self.project_root = temp_root / "GCS"
        self.project_root.mkdir()
        (self.project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# GCS Repository Manifest\n", encoding="utf-8"
        )
        (self.project_root / "src").mkdir()
        (self.project_root / "src" / "broker.py").write_text(
            "class BrokerClient:\n    pass\n", encoding="utf-8"
        )
        store = self.project_root / ".ai" / "runtime" / "state"
        store.mkdir(parents=True)
        db = store / "project_runtime.sqlite3"
        connection = sqlite3.connect(db)
        connection.execute(
            """
            CREATE TABLE mode_current_anchor (
                mode TEXT PRIMARY KEY,
                frame_id TEXT NOT NULL,
                anchor_id TEXT NOT NULL,
                state TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO mode_current_anchor VALUES ('MASTER', 'current', 'MASTER-CURRENT-TEST', 'CURRENT')"
        )
        connection.commit()
        connection.close()
        self.token = "test-token"
        self.server = create_server(
            database_path=temp_root / "universe.sqlite3",
            token=self.token,
            auto_start_project_masters=False,
            mode_contract=universe_mode_contract(
                {
                    "owner": "universe",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 1,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                        "CONDUCTOR": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                    },
                }
            ),
            host_profile=HostProfileStore(temp_root / "host.json"),
            service_state_path=temp_root / "server.json",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"
        self.request(
            "POST",
            "/v1/projects/register",
            {"project_id": "GCS", "project_root": str(self.project_root)},
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        body = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.endpoint + path, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_file_index_and_retrieval_require_current_anchor(self) -> None:
        status, blocked = self.request(
            "POST",
            "/v1/projects/GCS/file-index/search",
            {"query": "BrokerClient"},
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual("MODE_CURRENT_ANCHOR_REQUIRED", blocked["error_code"])

        status, blocked_sync = self.request(
            "POST",
            "/v1/projects/GCS/file-index/sync",
            {"mode": "MASTER", "anchor_id": "MASTER-CURRENT-TEST"},
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual("PROJECT_INDEX_HOOK_REQUIRED", blocked_sync["error_code"])

        synced = sync_project_index_from_hook(
            project_id="GCS",
            project_root=self.project_root,
            mode="MASTER",
            anchor_id="MASTER-CURRENT-TEST",
        )
        self.assertEqual("PROJECT_INDEX_HOOK_SYNCED", synced["status"])
        self.assertGreaterEqual(
            synced["index"]["created"] + synced["index"]["unchanged"], 1
        )
        central = sqlite3.connect(self.server.store.database_path)
        try:
            central_index_tables = central.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'project_file_%'"
            ).fetchall()
        finally:
            central.close()
        self.assertEqual([], central_index_tables)
        self.assertGreaterEqual(synced["index"]["graph_reconcile"]["current_count"], 1)
        self.assertEqual(
            "USER_APPROVAL_REQUIRED",
            synced["index"]["graph_reconcile"]["promotion_state"],
        )

        status, found = self.request(
            "POST",
            "/v1/projects/GCS/file-index/search",
            {
                "mode": "MASTER",
                "anchor_id": "MASTER-CURRENT-TEST",
                "query": "BrokerClient",
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FILE_SEARCH_COMPLETED", found["status"])
        self.assertEqual("src/broker.py", found["search"]["hits"][0]["relative_path"])
        self.assertIn("BrokerClient", found["search"]["hits"][0]["excerpt"])
        self.assertEqual("MASTER-CURRENT-TEST", found["anchor_id"])

        status, retrieval = self.request(
            "POST",
            "/v1/projects/GCS/retrieval-context",
            {
                "mode": "MASTER",
                "anchor_id": "MASTER-CURRENT-TEST",
                "query": "BrokerClient",
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("RETRIEVAL_CONTEXT_READY", retrieval["status"])
        self.assertEqual(
            "universe.project-llm-retrieval-context.v1",
            retrieval["retrieval"]["schema"],
        )
        self.assertEqual(
            "src/broker.py",
            retrieval["retrieval"]["file_index"]["search"]["hits"][0]["relative_path"],
        )

        status, index_state = self.request("GET", "/v1/projects/GCS/file-index")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("SUCCEEDED", index_state["index"]["sync"]["state"])
        self.assertEqual("READ_ONLY", index_state["universe_access"])
        self.assertGreaterEqual(
            index_state["index"]["graph_candidates"]["current_count"], 1
        )

        status, graph = self.request("GET", "/v1/projects/GCS/semantic-graph")
        self.assertEqual(HTTPStatus.OK, status)
        candidate = next(
            node
            for node in graph["nodes"]
            if node["entity_type"] == "IMPLEMENTATION_MODULE_CANDIDATE"
            and node["data"]["relative_path"] == "src/broker.py"
        )
        self.assertTrue(candidate["projection_only"])
        self.assertEqual("USER_APPROVAL_REQUIRED", candidate["data"]["promotion_state"])

    def test_project_hook_incrementally_tracks_add_and_remove(self) -> None:
        sync_project_index_from_hook(
            project_id="GCS",
            project_root=self.project_root,
            mode="MASTER",
            anchor_id="MASTER-CURRENT-TEST",
        )
        auto_file = self.project_root / "src" / "auto_worker.py"
        auto_file.write_text("class AutoIndexedSymbol:\n    pass\n", encoding="utf-8")
        synced = sync_project_index_from_hook(
            project_id="GCS",
            project_root=self.project_root,
            mode="MASTER",
            anchor_id="MASTER-CURRENT-TEST",
            changed_paths=["src/auto_worker.py"],
        )
        self.assertEqual("HOOK_CHANGED_PATHS", synced["index"]["scope"])
        self.assertEqual(1, synced["index"]["created"])
        status, found = self.request(
            "POST",
            "/v1/projects/GCS/file-index/search",
            {
                "mode": "MASTER",
                "anchor_id": "MASTER-CURRENT-TEST",
                "query": "AutoIndexedSymbol",
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("src/auto_worker.py", found["search"]["hits"][0]["relative_path"])

        (self.project_root / "src" / "broker.py").unlink()
        sync_project_index_from_hook(
            project_id="GCS",
            project_root=self.project_root,
            mode="MASTER",
            anchor_id="MASTER-CURRENT-TEST",
            changed_paths=["src/broker.py"],
        )
        status, graph = self.request("GET", "/v1/projects/GCS/semantic-graph")
        self.assertEqual(HTTPStatus.OK, status)
        removed = next(
            (
                node
                for node in graph["nodes"]
                if node["entity_type"] == "IMPLEMENTATION_MODULE_CANDIDATE"
                and node["data"]["relative_path"] == "src/broker.py"
                and node["lifecycle_state"] == "REMOVED"
            ),
            None,
        )
        self.assertIsNotNone(removed)
        self.assertEqual("USER_APPROVAL_REQUIRED", removed["data"]["promotion_state"])


if __name__ == "__main__":
    unittest.main()
