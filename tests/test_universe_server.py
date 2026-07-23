from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

JsonObject = dict[str, Any]


from universe_server import (  # noqa: E402
    ConnectionCapabilities,
    HttpUniverseTransport,
    UniverseError,
    UniverseStore,
    load_universe_mode_registry,
    auth_provider_for,
    connection_profile,
    create_server,
    interface_profile,
    local_connection_profile,
    require_release_lifecycle_mode,
    resolve_universe_mode_intent,
)


class UniverseLocalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp.name)
        self.project_root = temp_root / "GCS"
        runtime_root = self.project_root / ".ai" / "runtime"
        project_instance = runtime_root / "project_instance"
        project_instance.mkdir(parents=True)
        (runtime_root / "anchor_store").mkdir()
        (self.project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# GCS Repository Manifest\n",
            encoding="utf-8",
        )
        (project_instance / "mode_registry.json").write_text(
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
        (project_instance / "status.md").write_text("Status: READY\n", encoding="utf-8")
        (self.project_root / "src").mkdir()
        (self.project_root / "docs").mkdir()
        self.broker_source = self.project_root / "src" / "broker.py"
        self.viewer_source = self.project_root / "src" / "viewer.py"
        self.architecture_doc = self.project_root / "docs" / "architecture.md"
        self.contract_doc = self.project_root / "docs" / "broker-contract.md"
        self.orphan_doc = self.project_root / "docs" / "operations.md"
        self.broker_source.write_text(
            "class BrokerClient:\n    pass\n", encoding="utf-8"
        )
        self.viewer_source.write_text(
            "class StrategyViewer:\n    pass\n", encoding="utf-8"
        )
        self.architecture_doc.write_text(
            "# GCS Architecture\n", encoding="utf-8"
        )
        self.contract_doc.write_text(
            "# Broker Contract\n", encoding="utf-8"
        )
        self.orphan_doc.write_text(
            "# Operations\n", encoding="utf-8"
        )

        self.token = "test-token"
        self.server = create_server(
            database_path=temp_root / "universe.sqlite3",
            token=self.token,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        if isinstance(host, bytes):
            host = host.decode("ascii")
        self.endpoint = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        token: str | None = None,
    ) -> tuple[int, JsonObject]:
        body = None
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.endpoint + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()

    def registration(self, **overrides: object) -> JsonObject:
        value: JsonObject = {
            "project_id": "GCS",
            "project_root": str(self.project_root),
        }
        value.update(overrides)
        return value

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def project_seed(self, **overrides: object) -> JsonObject:
        value: JsonObject = {
            "seed_id": "gcs-seed-001",
            "source": {
                "ref": "local-git:GCS@1111111111111111111111111111111111111111",
                "commit": "1111111111111111111111111111111111111111",
            },
            "project": {
                "kind": "desktop-app",
                "technologies": ["python", "pyside6", "sqlite"],
                "goal": "stable broker-connected trading workstation",
            },
            "nodes": [
                {
                    "node_id": "broker-client",
                    "kind": "integration",
                    "title": "Broker Client",
                    "refs": [
                        {
                            "path": "src/broker.py",
                            "sha256": self.digest(self.broker_source),
                            "kind": "source",
                            "symbol": "BrokerClient",
                        }
                    ],
                },
                {
                    "node_id": "strategy-viewer",
                    "kind": "ui",
                    "title": "Strategy Viewer",
                    "refs": [
                        {
                            "path": "src/viewer.py",
                            "sha256": self.digest(self.viewer_source),
                            "kind": "source",
                            "symbol": "StrategyViewer",
                        }
                    ],
                },
            ],
            "edges": [],
            "documents": [
                {
                    "document_id": "architecture",
                    "path": "docs/architecture.md",
                    "sha256": self.digest(self.architecture_doc),
                    "role": "architecture",
                    "node_ids": ["broker-client", "strategy-viewer"],
                },
                {
                    "document_id": "broker-contract",
                    "path": "docs/broker-contract.md",
                    "sha256": self.digest(self.contract_doc),
                    "role": "contract",
                    "node_ids": ["broker-client"],
                },
                {
                    "document_id": "operations",
                    "path": "docs/operations.md",
                    "sha256": self.digest(self.orphan_doc),
                    "role": "reference",
                    "node_ids": [],
                },
            ],
        }
        value.update(overrides)
        return value

    def test_health_is_public_but_project_data_requires_token(self) -> None:
        status, result = self.request("GET", "/health")
        self.assertEqual(200, status)
        self.assertEqual("READY", result["status"])
        self.assertEqual("LOCAL", result["connection"]["kind"])
        self.assertEqual("HTTP", result["connection"]["transport_kind"])
        self.assertEqual("LOCAL_TOKEN", result["connection"]["auth"]["type"])
        self.assertNotIn("token", result["connection"]["auth"])
        self.assertTrue(result["connection"]["capabilities"]["realtime"])
        self.assertEqual("HTTP_API", result["interfaces"][0]["kind"])

        status, result = self.request("GET", "/v1/projects")
        self.assertEqual(401, status)
        self.assertEqual("LOCAL_TOKEN_REQUIRED", result["error_code"])

    def test_registration_refresh_and_listing_are_idempotent(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(),
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_REGISTERED", result["status"])
        self.assertEqual("GCS", result["project"]["project_id"])

        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(metadata={"label": "Trading"}),
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_REFRESHED", result["status"])

        status, result = self.request("GET", "/v1/projects", token=self.token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(result["projects"]))
        self.assertEqual("Trading", result["projects"][0]["metadata"]["label"])

    def test_event_append_is_idempotent_and_detach_cascades(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        event = {
            "event_id": "gcs-status-001",
            "event_type": "STATUS_OBSERVED",
            "payload": {"repository_runtime": "VERIFIED"},
        }
        status, result = self.request(
            "POST", "/v1/projects/GCS/events", event, self.token
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_EVENT_APPENDED", result["status"])

        status, result = self.request(
            "POST", "/v1/projects/GCS/events", event, self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_EVENT_ALREADY_RECORDED", result["status"])

        status, result = self.request("GET", "/v1/projects/GCS/events", token=self.token)
        self.assertEqual(200, status)
        self.assertEqual(["gcs-status-001"], [item["event_id"] for item in result["events"]])

        status, result = self.request("DELETE", "/v1/projects/GCS", token=self.token)
        self.assertEqual(200, status)
        self.assertTrue(result["detached"])
        status, _ = self.request("GET", "/v1/projects/GCS", token=self.token)
        self.assertEqual(404, status)

    def test_project_seed_projection_and_incorporation_proposal_are_read_only(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }

        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_SEED_RECORDED", result["status"])
        seed = result["seed"]
        self.assertEqual(64, len(seed["seed_digest"]))
        self.assertTrue(all("content" not in node for node in seed["nodes"]))
        self.assertFalse(seed["verification"]["raw_file_content_stored"])
        self.assertEqual("PROJECT_SUBMITTED", seed["verification"]["source_commit"])
        self.assertEqual("VERIFIED_LOCAL", seed["verification"]["file_digests"])

        status, repeated = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_SEED_ALREADY_RECORDED", repeated["status"])

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/projection",
            {
                "seed_id": "gcs-seed-001",
                "expected_seed_digest": seed["seed_digest"],
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_PROJECTION_BUILT", result["status"])
        projection = result["projection"]
        self.assertEqual(
            {"broker-client", "strategy-viewer"},
            {node["node_id"] for node in projection["nodes"]},
        )
        self.assertEqual(
            {"node:broker-client", "node:strategy-viewer", "document:operations"},
            {
                item["subject_ref"]
                for item in projection["missing_connections"]
            },
        )
        self.assertEqual("NONE", projection["effects"]["project_source_write"])

        status, fetched = self.request(
            "GET", "/v1/projects/GCS/projection", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            projection["projection_digest"],
            fetched["projection"]["projection_digest"],
        )

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/document-incorporation-proposals",
            {
                "projection_id": projection["projection_id"],
                "expected_projection_digest": projection["projection_digest"],
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("INCORPORATION_PROPOSAL_READY", result["status"])
        proposal = result["proposal"]
        self.assertEqual("PROJECT", proposal["execution_owner"])
        self.assertEqual(0, proposal["effects"]["documents_moved"])
        self.assertTrue(
            all(
                operation["target_path"].startswith("docs/universe/")
                for operation in proposal["operations"]
            )
        )
        self.assertFalse((self.project_root / "docs" / "universe").exists())
        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            projection["projection_digest"],
            reopened.get_project_projection("GCS")["projection_digest"],
        )
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_project_seed_rejects_digest_mismatch_and_root_escape(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        invalid_digest = self.project_seed()
        invalid_digest["nodes"][0]["refs"][0]["sha256"] = "0" * 64
        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", invalid_digest, self.token
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "PROJECT_FILE_REF_DIGEST_MISMATCH", result["error_code"]
        )

        escaping = self.project_seed()
        escaping["documents"][0]["path"] = "../outside.md"
        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", escaping, self.token
        )
        self.assertEqual(400, status)
        self.assertEqual("PROJECT_REF_INVALID", result["error_code"])

        raw_content = self.project_seed()
        raw_content["nodes"][0]["content"] = "class Injected: pass"
        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", raw_content, self.token
        )
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_new_seed_invalidates_projection_until_rebuilt(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, first = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(201, status)
        self.request(
            "POST",
            "/v1/projects/GCS/projection",
            {"expected_seed_digest": first["seed"]["seed_digest"]},
            self.token,
        )

        updated = self.project_seed(
            seed_id="gcs-seed-002",
            source={
                "ref": "local-git:GCS@2222222222222222222222222222222222222222",
                "commit": "2222222222222222222222222222222222222222",
            },
        )
        status, second = self.request(
            "POST", "/v1/projects/GCS/seed", updated, self.token
        )
        self.assertEqual(201, status)
        self.assertNotEqual(
            first["seed"]["seed_digest"], second["seed"]["seed_digest"]
        )
        status, result = self.request(
            "GET", "/v1/projects/GCS/projection", token=self.token
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "PROJECT_PROJECTION_REBUILD_REQUIRED", result["error_code"]
        )

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/projection",
            {"expected_seed_digest": first["seed"]["seed_digest"]},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("PROJECT_SEED_DIGEST_MISMATCH", result["error_code"])

    def test_registration_rejects_ref_escape_and_owner_mismatch(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(refs={"manifest": "../outside.md"}),
            self.token,
        )
        self.assertEqual(400, status)
        self.assertEqual("PROJECT_REF_INVALID", result["error_code"])

        registry_path = (
            self.project_root / ".ai" / "runtime" / "project_instance" / "mode_registry.json"
        )
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["owner"] = "OTHER"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        status, result = self.request(
            "POST", "/v1/projects/register", self.registration(), self.token
        )
        self.assertEqual(409, status)
        self.assertEqual("PROJECT_IDENTITY_MISMATCH", result["error_code"])

    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(UniverseError, "loopback"):
            create_server(
                database_path=Path(self.temp.name) / "forbidden.sqlite3",
                token="token",
                host="0.0.0.0",
            )

    def test_http_transport_revalidates_endpoint_and_request_path(self) -> None:
        profile = local_connection_profile(self.endpoint)
        invalid_profile = replace(profile, endpoint="file:///tmp/universe.json")
        transport = HttpUniverseTransport(
            invalid_profile,
            auth_provider_for(invalid_profile, self.token),
        )
        with self.assertRaisesRegex(UniverseError, "absolute HTTP or HTTPS"):
            transport.request_json(method="GET", path="/v1/projects")

        safe_profile = local_connection_profile(self.endpoint)
        safe_transport = HttpUniverseTransport(
            safe_profile,
            auth_provider_for(safe_profile, self.token),
        )
        with self.assertRaisesRegex(UniverseError, "absolute path"):
            safe_transport.request_json(method="GET", path="https://example.com")

    def test_universe_mode_contract_and_aliases(self) -> None:
        registry_path = Path(self.temp.name) / "universe-mode-registry.json"
        registry_path.write_text(
            json.dumps(
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
                        "UNIVERSE": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        registry = load_universe_mode_registry(registry_path)
        self.assertEqual("CONDUCTOR", registry["modes"]["UNIVERSE"]["role"])
        for intent in (
            "UNIVERSE",
            "Universe mode",
            "CONDUCTOR",
            "Conductor mode",
            "\uc720\ub2c8\ubc84\uc2a4",
            "\uc720\ub2c8\ubc84\uc2a4\ubaa8\ub4dc",
            "\ucee8\ub355\ud130",
            "\ucee8\ub355\ud130\ubaa8\ub4dc",
        ):
            with self.subTest(intent=intent):
                self.assertEqual("UNIVERSE", resolve_universe_mode_intent(intent))

        require_release_lifecycle_mode("MASTER")
        with self.assertRaisesRegex(UniverseError, "require MASTER Mode"):
            require_release_lifecycle_mode("UNIVERSE")

        registry["modes"]["UNIVERSE"]["role"] = "NAVIGATOR"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(UniverseError, "UNIVERSE"):
            load_universe_mode_registry(registry_path)

    def test_remote_auth_types_are_reserved_without_runtime_implementation(self) -> None:
        profile = connection_profile(
            connection_id="personal-cloud",
            kind="REMOTE",
            transport_kind="GIT",
            endpoint="https://universe.example.com",
            auth_type="OAUTH2",
            credential_ref="os-keychain://universe/personal-cloud",
            capabilities=ConnectionCapabilities(
                read=True,
                append=True,
                realtime=False,
                bidirectional=True,
                durable=True,
            ),
        )
        self.assertEqual("REMOTE", profile.kind)
        self.assertEqual("GIT", profile.transport_kind)
        self.assertEqual("OAUTH2", profile.auth.auth_type)
        with self.assertRaisesRegex(UniverseError, "reserved but not implemented"):
            auth_provider_for(profile, "not-used")

    def test_local_connection_profile_requires_loopback(self) -> None:
        with self.assertRaisesRegex(UniverseError, "loopback"):
            connection_profile(
                connection_id="local",
                kind="LOCAL",
                transport_kind="HTTP",
                endpoint="https://universe.example.com",
                auth_type="LOCAL_TOKEN",
                credential_ref="server-state://token",
                capabilities=ConnectionCapabilities(
                    read=True,
                    append=True,
                    realtime=True,
                    bidirectional=True,
                    durable=True,
                ),
            )

    def test_mcp_is_an_interface_not_a_transport(self) -> None:
        profile = interface_profile(
            interface_id="mcp-server",
            kind="MCP",
            direction="INBOUND",
            active=False,
        )
        self.assertEqual("MCP", profile.kind)
        self.assertFalse(profile.active)

        with self.assertRaisesRegex(UniverseError, "transport kind"):
            connection_profile(
                connection_id="invalid-mcp-transport",
                kind="REMOTE",
                transport_kind="MCP",
                endpoint="https://universe.example.com",
                auth_type="OAUTH2",
                credential_ref="os-keychain://universe/invalid",
                capabilities=ConnectionCapabilities(
                    read=True,
                    append=True,
                    realtime=True,
                    bidirectional=True,
                    durable=True,
                ),
            )

    def test_reserved_p2p_transport_does_not_inherit_http_address_rules(self) -> None:
        profile = connection_profile(
            connection_id="peer-universe",
            kind="PEER",
            transport_kind="P2P",
            endpoint="peer://universe/example",
            auth_type="PEER_KEY",
            credential_ref="os-keychain://universe/peer/example",
            capabilities=ConnectionCapabilities(
                read=True,
                append=True,
                realtime=True,
                bidirectional=True,
                durable=False,
            ),
        )
        self.assertEqual("peer://universe/example", profile.endpoint)
        with self.assertRaisesRegex(UniverseError, "reserved but not implemented"):
            auth_provider_for(profile, "not-used")


if __name__ == "__main__":
    unittest.main()
