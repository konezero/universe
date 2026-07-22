from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import (  # noqa: E402
    ConnectionCapabilities,
    UniverseError,
    auth_provider_for,
    connection_profile,
    create_server,
    interface_profile,
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

        self.token = "test-token"
        self.server = create_server(
            database_path=temp_root / "universe.sqlite3",
            token=self.token,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
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
        payload: dict[str, object] | None = None,
        token: str | None = None,
    ) -> tuple[int, dict[str, object]]:
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

    def registration(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "project_id": "GCS",
            "project_root": str(self.project_root),
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
