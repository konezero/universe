from __future__ import annotations

import json
import sys
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_memory import propose_node_links  # noqa: E402
from universe_server import create_server, universe_mode_contract  # noqa: E402


class UniverseMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project_root = root / "GCS"
        runtime = self.project_root / ".ai" / "runtime" / "project_instance"
        runtime.mkdir(parents=True)
        (self.project_root / ".ai" / "runtime" / "anchor_store").mkdir(parents=True)
        (self.project_root / ".ai" / "inbox" / "MASTER").mkdir(parents=True)
        (self.project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# GCS\n", encoding="utf-8"
        )
        (runtime / "mode_registry.json").write_text(
            json.dumps(
                {
                    "schema": "ai-career.mode-registry.v1",
                    "owner": "GCS",
                    "repository_kind": "PROJECT",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 1,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (runtime / "status.md").write_text("Status: READY\n", encoding="utf-8")
        self.token = "memory-test-token"
        self.server = create_server(
            database_path=root / "universe.sqlite3",
            token=self.token,
            auto_start_project_masters=False,
            auto_start_conductor_runtime=False,
            mode_contract=universe_mode_contract(
                {
                    "owner": "universe",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 3,
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
        )
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}"
        import threading

        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.store = self.server.store
        self.store.register_project(
            {
                "project_id": "GCS",
                "project_root": str(self.project_root),
            }
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        data = None
        headers = {"Authorization": f"Bearer {self.token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(self.endpoint + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=10) as response:
                return int(response.status), json.loads(response.read().decode())
        except HTTPError as error:
            return int(error.code), json.loads(error.read().decode())

    def test_create_list_and_link_memory(self) -> None:
        status, created = self.request(
            "POST",
            "/v1/projects/GCS/memories",
            {
                "title": "Order risk boundary note",
                "body": "Need to document order-risk functional node constraints",
                "state": "BRAINSTORM",
            },
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        memory = created["memory"]
        self.assertEqual("UNLINKED", memory["link_state"])
        self.assertEqual("NONE", memory["effects"]["seed_write"])

        status, listed = self.request(
            "GET", "/v1/projects/GCS/memories?link_state=UNLINKED"
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(listed["memories"]))

        status, linked = self.request(
            "POST",
            "/v1/projects/GCS/memories/link",
            {
                "memory_id": memory["memory_id"],
                "node_ref": "order-risk",
                "graph": "functional",
                "link_state": "LINKED",
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("LINKED", linked["memory"]["link_state"])
        self.assertEqual("order-risk", linked["memory"]["node_ref"])

    def test_token_overlap_proposals(self) -> None:
        proposals = propose_node_links(
            memories=[
                {
                    "memory_id": "memory_1",
                    "link_state": "UNLINKED",
                    "title": "order risk",
                    "body": "order risk validation path",
                }
            ],
            nodes=[
                {"node_id": "order-risk", "label": "Order Risk", "kind": "system"},
                {"node_id": "market-data", "label": "Market Data", "kind": "system"},
            ],
        )
        self.assertTrue(proposals)
        self.assertEqual("order-risk", proposals[0]["node_ref"])
        self.assertEqual("NONE", proposals[0]["effects"]["seed_write"])


if __name__ == "__main__":
    unittest.main()
