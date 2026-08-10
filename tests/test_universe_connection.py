from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from universe_app.connection import (  # noqa: E402
    ConnectionCapabilities,
    HttpUniverseTransport,
    LocalTokenAuthProvider,
    UniverseError,
    auth_provider_for,
    connection_profile,
    interface_profile,
    local_connection_profile,
)
from universe_server import (  # noqa: E402
    ConnectionProfile as LegacyConnectionProfile,
    UniverseError as LegacyUniverseError,
)


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps({"status": "OK"}).encode("utf-8")


class UniverseConnectionContractTests(unittest.TestCase):
    def test_legacy_entrypoint_reexports_connection_contract(self) -> None:
        self.assertIs(LegacyConnectionProfile, local_connection_profile("http://127.0.0.1:1").__class__)
        self.assertIs(LegacyUniverseError, UniverseError)

    def test_local_http_profile_requires_literal_loopback(self) -> None:
        profile = local_connection_profile("http://127.0.0.1:8765")
        self.assertEqual("LOCAL", profile.kind)
        with self.assertRaisesRegex(UniverseError, "literal loopback"):
            connection_profile(
                connection_id="local",
                kind="LOCAL",
                transport_kind="HTTP",
                endpoint="http://localhost:8765",
                auth_type="NONE",
                credential_ref="NONE",
                capabilities=profile.capabilities,
            )

    def test_interface_and_auth_profiles_remain_separate(self) -> None:
        interface = interface_profile(
            interface_id="mcp-server",
            kind="MCP",
            direction="INBOUND",
            active=False,
        )
        self.assertEqual("MCP", interface.kind)
        provider = LocalTokenAuthProvider("secret")
        self.assertEqual({"Authorization": "Bearer secret"}, provider.headers())

    def test_http_transport_rejects_origin_escape(self) -> None:
        profile = local_connection_profile("http://127.0.0.1:8765")
        transport = HttpUniverseTransport(profile, auth_provider_for(profile, ""))
        with self.assertRaisesRegex(UniverseError, "absolute path"):
            transport.request_json(method="GET", path="https://example.com/x")
        with self.assertRaisesRegex(UniverseError, "absolute HTTP or HTTPS"):
            HttpUniverseTransport(
                replace(profile, endpoint="file:///tmp/universe.json"),
                auth_provider_for(profile, ""),
            ).request_json(method="GET", path="/v1/projects")

    def test_http_transport_serializes_json_without_changing_public_shape(self) -> None:
        profile = connection_profile(
            connection_id="remote",
            kind="REMOTE",
            transport_kind="HTTP",
            endpoint="https://universe.example.com",
            auth_type="LOCAL_TOKEN",
            credential_ref="memory://token",
            capabilities=ConnectionCapabilities(
                read=True,
                append=True,
                realtime=True,
                bidirectional=True,
                durable=False,
            ),
        )
        transport = HttpUniverseTransport(profile, LocalTokenAuthProvider("secret"))
        with patch("universe_app.connection.urlopen", return_value=_Response()) as opened:
            status, payload = transport.request_json(
                method="POST", path="/v1/projects", payload={"project_id": "GCS"}
            )
        self.assertEqual(200, status)
        self.assertEqual({"status": "OK"}, payload)
        request = opened.call_args.args[0]
        self.assertEqual("Bearer secret", request.headers["Authorization"])
        self.assertEqual(b'{"project_id":"GCS"}', request.data)


if __name__ == "__main__":
    unittest.main()
