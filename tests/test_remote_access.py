from __future__ import annotations

from http import HTTPStatus
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


from remote_access import RemoteAccessError, RemoteAccessStore  # noqa: E402
from universe_remote_gateway import (  # noqa: E402
    GatewayError,
    _validate_listen_host,
    create_gateway,
)


class RemoteAccessStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "universe.sqlite3"
        self.store = RemoteAccessStore(self.database)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_pairing_is_single_use_and_device_session_is_revocable(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url="https://remote.example.test", ttl_seconds=600
        )
        request = self.store.request_pairing(
            code=invitation["code"],
            device_name="Test phone",
            user_agent="UniverseTest/1",
        )
        with self.assertRaisesRegex(RemoteAccessError, "already been claimed"):
            self.store.request_pairing(
                code=invitation["code"],
                device_name="Second phone",
                user_agent="UniverseTest/2",
            )

        awaiting = self.store.snapshot()["pairings"]
        self.assertEqual("AWAITING_APPROVAL", awaiting[0]["state"])
        self.store.decide_pairing(request["pairing_id"], approve=True)
        consumed = self.store.pairing_status(
            request["pairing_id"],
            request_token=request["request_token"],
            user_agent="UniverseTest/1",
        )
        self.assertEqual("CONSUMED", consumed["state"])

        device = self.store.authorize_device(
            consumed["session_token"], user_agent="UniverseTest/1"
        )
        self.assertEqual("PAIRED", device["state"])
        self.store.revoke_device(device["device_id"])
        with self.assertRaisesRegex(RemoteAccessError, "session is invalid"):
            self.store.authorize_device(
                consumed["session_token"], user_agent="UniverseTest/1"
            )

    def test_expired_pairing_fails_closed(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url="https://remote.example.test", ttl_seconds=60
        )
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE remote_pairing SET expires_at = '2000-01-01T00:00:00Z'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(RemoteAccessError, "expired"):
            self.store.request_pairing(
                code=invitation["code"],
                device_name="Late phone",
                user_agent="UniverseTest/1",
            )


class _UpstreamHandler(BaseHTTPRequestHandler):
    observed_headers: dict[str, str] = {}

    def do_GET(self) -> None:
        type(self).observed_headers = {key: value for key, value in self.headers.items()}
        if self.path == "/events":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"id: 1\ndata: first\n\n")
            self.wfile.flush()
            self.wfile.write(b"id: 2\ndata: second\n\n")
            self.wfile.flush()
            return
        if self.path == "/health":
            body = json.dumps({"status": "READY"}).encode("utf-8")
            content_type = "application/json"
        else:
            body = b"<html><body>Universe upstream</body></html>"
            content_type = "text/html"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class RemoteGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "universe.sqlite3"
        self.store = RemoteAccessStore(self.database)
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.upstream_thread.start()
        host, port = self.upstream.server_address[:2]
        self.upstream_state = root / "server.json"
        self.upstream_state.write_text(
            json.dumps(
                {
                    "endpoint": f"http://{host}:{port}",
                    "database": str(self.database),
                }
            ),
            encoding="utf-8",
        )
        self.gateway = create_gateway(
            listen_host="127.0.0.1",
            port=0,
            upstream_state=self.upstream_state,
            database_path=self.database,
        )
        self.gateway_thread = threading.Thread(
            target=self.gateway.serve_forever, daemon=True
        )
        self.gateway_thread.start()
        gateway_host, gateway_port = self.gateway.server_address[:2]
        self.endpoint = f"http://{gateway_host}:{gateway_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.user_agent = "UniverseMobileTest/1"

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.gateway_thread.join(timeout=5)
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=5)
        self.temp.cleanup()

    def _open(self, request: Request):
        request.add_header("User-Agent", self.user_agent)
        return self.opener.open(request, timeout=5)

    def test_gateway_requires_pairing_then_proxies_fixed_loopback_origin(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url=self.endpoint, ttl_seconds=600
        )
        form = urlencode(
            {"code": invitation["code"], "device_name": "Test mobile"}
        ).encode("utf-8")
        with self._open(
            Request(
                self.endpoint + "/pair/request",
                data=form,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        ) as response:
            self.assertEqual(HTTPStatus.OK, response.status)

        pending = self.store.snapshot()["pairings"]
        self.assertEqual(1, len(pending))
        self.store.decide_pairing(pending[0]["pairing_id"], approve=True)
        with self._open(
            Request(
                self.endpoint + f"/pair/status?id={pending[0]['pairing_id']}",
                method="GET",
            )
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertEqual("CONSUMED", result["state"])

        with self._open(
            Request(
                self.endpoint + "/",
                method="GET",
                headers={"Authorization": "Bearer must-not-forward"},
            )
        ) as response:
            self.assertIn(b"Universe upstream", response.read())
            self.assertEqual("REMOTE_BROWSER", response.headers["X-Universe-Access-Surface"])
        self.assertEqual(
            "REMOTE_BROWSER",
            _UpstreamHandler.observed_headers["X-Universe-Access-Surface"],
        )
        self.assertNotIn("Authorization", _UpstreamHandler.observed_headers)

        with self._open(
            Request(
                self.endpoint + "/events",
                method="GET",
                headers={"Accept": "text/event-stream", "Last-Event-ID": "0"},
            )
        ) as response:
            events = response.read()
            self.assertEqual("text/event-stream", response.headers.get_content_type())
        self.assertEqual(
            b"id: 1\ndata: first\n\nid: 2\ndata: second\n\n",
            events,
        )
        self.assertEqual("0", _UpstreamHandler.observed_headers["Last-Event-ID"])

        device = self.store.snapshot()["devices"][0]
        self.store.revoke_device(device["device_id"])
        with self.assertRaises(HTTPError) as raised:
            self._open(Request(self.endpoint + "/", method="GET"))
        try:
            self.assertEqual(HTTPStatus.UNAUTHORIZED, raised.exception.code)
        finally:
            raised.exception.close()

    def test_unpaired_browser_cannot_read_upstream(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.endpoint + "/app.js", timeout=5)
        try:
            self.assertEqual(HTTPStatus.UNAUTHORIZED, raised.exception.code)
        finally:
            raised.exception.close()

    def test_listen_host_rejects_public_and_wildcard_addresses(self) -> None:
        self.assertEqual("127.0.0.1", _validate_listen_host("127.0.0.1"))
        self.assertEqual("192.168.10.5", _validate_listen_host("192.168.10.5"))
        for address in ("0.0.0.0", "211.248.197.157"):
            with self.subTest(address=address):
                with self.assertRaises(GatewayError):
                    _validate_listen_host(address)


if __name__ == "__main__":
    unittest.main()
