from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_rendezvous_client import (  # noqa: E402
    CLIENT_SCHEMA,
    RendezvousClientError,
    RendezvousHttpClient,
    UniverseRendezvousService,
    _Identity,
    normalize_rendezvous_config,
)


class RendezvousClientTests(unittest.TestCase):
    def test_signing_headers_cover_canonical_fields(self) -> None:
        identity = _Identity.generate()
        client = RendezvousHttpClient("http://127.0.0.1:8080", identity=identity)
        body = b'{"status":"ONLINE"}'
        headers = client.signed_headers("POST", "/v1/universes/u/presence", body)
        self.assertIn("x-ur-public-key", headers)
        self.assertEqual(headers["x-ur-public-key"], identity.public_key)
        self.assertTrue(headers["x-ur-timestamp"].isdigit())
        self.assertGreaterEqual(len(headers["x-ur-nonce"]), 8)
        self.assertTrue(headers["x-ur-signature"])

    def test_config_rejects_non_https_remote_origin(self) -> None:
        with self.assertRaises(RendezvousClientError):
            normalize_rendezvous_config(
                {
                    "base_url": "http://example.com",
                    "display_name": "Aurora",
                    "endpoint_url": "https://universe.example.com",
                }
            )

    def test_service_registers_presence_and_pending_flow(self) -> None:
        calls: list[tuple[str, str, bytes]] = []
        universe_id = "11111111-1111-4111-8111-111111111111"
        request_id = "22222222-2222-4222-8222-222222222222"

        def transport(method, path, body, headers):
            calls.append((method, path, body))
            self.assertIn("x-ur-signature", headers)
            if method == "POST" and path == "/v1/universes/register":
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual("PUBLIC", payload["visibility"])
                self.assertTrue(payload["endpoint_url"].startswith("http://"))
                return 201, json.dumps(
                    {
                        "universe_id": universe_id,
                        "display_name": payload["display_name"],
                        "visibility": payload["visibility"],
                        "owner_public_key": headers["x-ur-public-key"],
                        "created_at": 1,
                        "invite_code": None,
                        "endpoint_url": payload["endpoint_url"],
                    }
                ).encode("utf-8")
            if method == "POST" and path.endswith("/presence"):
                return 200, json.dumps(
                    {"status": "ONLINE", "expires_at": 120}
                ).encode("utf-8")
            if method == "GET" and path.endswith("/connect-requests"):
                return 200, json.dumps(
                    {
                        "universe_id": universe_id,
                        "connect_requests": [
                            {
                                "connect_request_id": request_id,
                                "universe_id": universe_id,
                                "state": "PENDING",
                                "created_at": 1,
                                "expires_at": 600,
                                "decided_at": None,
                                "signaling": None,
                            }
                        ],
                    }
                ).encode("utf-8")
            if method == "POST" and path.endswith("/approve"):
                return 200, json.dumps(
                    {
                        "connect_request_id": request_id,
                        "universe_id": universe_id,
                        "state": "APPROVED",
                        "created_at": 1,
                        "expires_at": 600,
                        "decided_at": 10,
                        "signaling": {
                            "session_id": "s1",
                            "websocket_path": "/v1/signaling/s1",
                            "expires_at": 300,
                        },
                    }
                ).encode("utf-8")
            return 404, json.dumps(
                {"error": {"code": "NOT_FOUND", "message": path}}
            ).encode("utf-8")

        http = RendezvousHttpClient(
            "http://127.0.0.1:8080",
            transport=transport,
        )
        service = UniverseRendezvousService(
            {
                "base_url": "http://127.0.0.1:8080",
                "display_name": "Aurora",
                "visibility": "PUBLIC",
                "endpoint_url": "http://127.0.0.1:8765",
                "presence_interval_seconds": 60,
                "poll_interval_seconds": 5,
            },
            http=http,
        )
        # Drive without background threads: register + methods only.
        registration = service.http.request(
            "POST",
            "/v1/universes/register",
            {
                "display_name": "Aurora",
                "visibility": "PUBLIC",
                "endpoint_url": "http://127.0.0.1:8765",
            },
        )
        service._universe_id = registration["universe_id"]
        service._started = True
        pending = service.refresh_pending()
        self.assertEqual(1, len(pending))
        self.assertEqual(request_id, pending[0]["connect_request_id"])
        approved = service.approve(request_id)
        self.assertEqual("APPROVED", approved["state"])
        status = service.public_status()
        self.assertEqual(CLIENT_SCHEMA, status["schema"])
        self.assertEqual(0, status["pending_count"])
        self.assertNotIn("private", json.dumps(status).lower())
        self.assertTrue(any(path == "/v1/universes/register" for _, path, _ in calls))

    def test_public_status_never_includes_key_material(self) -> None:
        service = UniverseRendezvousService(
            {
                "base_url": "http://127.0.0.1:8080",
                "display_name": "Aurora",
                "endpoint_url": "http://127.0.0.1:1",
            }
        )
        status = service.public_status()
        encoded = json.dumps(status)
        self.assertNotIn(service.http.identity.public_key, encoded)
        self.assertNotIn("private_key", encoded)

    def test_start_reuses_saved_universe_instead_of_reregistering(self) -> None:
        from universe_rendezvous_client import (
            _Identity,
            save_rendezvous_state,
        )

        root = Path(tempfile.mkdtemp())
        state_path = root / "state.json"
        identity = _Identity.generate()
        universe_id = "33333333-3333-4333-8333-333333333333"
        save_rendezvous_state(
            identity=identity,
            universe_id=universe_id,
            base_url="http://127.0.0.1:8080",
            path=state_path,
        )
        calls: list[str] = []

        def transport(method, path, body, headers):
            calls.append(f"{method} {path}")
            if method == "POST" and path.endswith("/presence"):
                return 200, json.dumps(
                    {"status": "ONLINE", "expires_at": 120}
                ).encode("utf-8")
            if method == "GET" and path.endswith("/connect-requests"):
                return 200, json.dumps(
                    {"universe_id": universe_id, "connect_requests": []}
                ).encode("utf-8")
            if method == "POST" and path == "/v1/universes/register":
                self.fail("should not re-register when saved universe is valid")
            return 404, b"{}"

        http = RendezvousHttpClient(
            "http://127.0.0.1:8080",
            identity=identity,
            transport=transport,
        )
        service = UniverseRendezvousService(
            {
                "base_url": "http://127.0.0.1:8080",
                "display_name": "Aurora",
                "endpoint_url": "http://127.0.0.1:1",
            },
            http=http,
            state_path=state_path,
        )
        # Inject saved id the way __init__ would when http is not provided.
        service._saved_universe_id = universe_id
        status = service.start()
        self.assertEqual(universe_id, status["universe_id"])
        self.assertTrue(any(c.endswith("/presence") for c in calls))
        service.stop()


if __name__ == "__main__":
    unittest.main()
