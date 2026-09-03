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
    SESSION_COOKIE,
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
    live_first_sent = threading.Event()
    live_release_second = threading.Event()

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
        if self.path == "/events-live":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"id: 1\ndata: first\n\n")
            self.wfile.flush()
            type(self).live_first_sent.set()
            type(self).live_release_second.wait(3)
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
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.user_agent = "UniverseMobileTest/1"
        _UpstreamHandler.live_first_sent.clear()
        _UpstreamHandler.live_release_second.clear()

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

    def test_cookieless_client_pairs_and_authorizes_with_header_tokens(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url=self.endpoint, ttl_seconds=600
        )
        # A bare client with no cookie jar: it cannot persist Set-Cookie, so it
        # must get the request token in the body and replay it as a header.
        request = Request(
            self.endpoint + "/pair/request",
            data=json.dumps(
                {"code": invitation["code"], "device_name": "grok vm chrome"}
            ).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
        )
        with urlopen(request, timeout=5) as response:
            issued = json.loads(response.read().decode("utf-8"))
        self.assertEqual(HTTPStatus.OK, response.status)
        request_token = issued["request_token"]
        pairing_id = issued["pairing_id"]
        self.assertTrue(request_token)

        def _status() -> dict:
            poll = Request(
                self.endpoint + f"/pair/status?id={pairing_id}",
                headers={
                    "X-Request-Token": request_token,
                    "User-Agent": self.user_agent,
                },
            )
            with urlopen(poll, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        self.assertEqual("AWAITING_APPROVAL", _status()["state"])
        self.store.decide_pairing(pairing_id, approve=True)
        consumed = _status()
        self.assertEqual("CONSUMED", consumed["state"])
        session_token = consumed["session_token"]
        self.assertTrue(session_token)

        proxied = Request(
            self.endpoint + "/",
            headers={
                "X-Universe-Session": session_token,
                "User-Agent": self.user_agent,
            },
        )
        with urlopen(proxied, timeout=5) as response:
            self.assertIn(b"Universe upstream", response.read())
            self.assertEqual(
                "REMOTE_BROWSER", response.headers["X-Universe-Access-Surface"]
            )

    def test_pairing_status_without_a_token_is_a_named_client_error(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url=self.endpoint, ttl_seconds=600
        )
        request = self.store.request_pairing(
            code=invitation["code"],
            device_name="d",
            user_agent=self.user_agent,
        )
        self.store.decide_pairing(request["pairing_id"], approve=True)
        poll = Request(
            self.endpoint + f"/pair/status?id={request['pairing_id']}",
            headers={"User-Agent": self.user_agent},
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(poll, timeout=5)
        self.assertEqual(400, raised.exception.code)
        body = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual("REMOTE_ACCESS_REQUEST_INVALID", body["error_code"])

    def test_pairing_status_rejects_conflicting_cookie_and_header_tokens(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url=self.endpoint, ttl_seconds=600
        )
        form = urlencode(
            {"code": invitation["code"], "device_name": "d"}
        ).encode("utf-8")
        with self._open(
            Request(
                self.endpoint + "/pair/request",
                data=form,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        ):
            pass
        pending = self.store.snapshot()["pairings"][0]
        poll = Request(
            self.endpoint + f"/pair/status?id={pending['pairing_id']}",
            headers={
                "X-Request-Token": "a-different-token",
                "User-Agent": self.user_agent,
            },
        )
        with self.assertRaises(HTTPError) as raised:
            self.opener.open(poll, timeout=5)
        self.assertEqual(400, raised.exception.code)
        body = json.loads(raised.exception.read().decode("utf-8"))
        self.assertEqual("REMOTE_PAIRING_TOKEN_CONFLICT", body["error_code"])

    def test_gateway_flushes_sse_event_before_upstream_stream_finishes(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url=self.endpoint, ttl_seconds=600
        )
        form = urlencode(
            {"code": invitation["code"], "device_name": "Live test mobile"}
        ).encode("utf-8")
        with self._open(
            Request(
                self.endpoint + "/pair/request",
                data=form,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        ):
            pass
        pending = self.store.snapshot()["pairings"][0]
        self.store.decide_pairing(pending["pairing_id"], approve=True)
        with self._open(
            Request(
                self.endpoint + f"/pair/status?id={pending['pairing_id']}",
                method="GET",
            )
        ):
            pass

        try:
            with self._open(
                Request(
                    self.endpoint + "/events-live",
                    method="GET",
                    headers={"Accept": "text/event-stream"},
                )
            ) as response:
                self.assertTrue(_UpstreamHandler.live_first_sent.wait(1))
                self.assertEqual(b"id: 1\n", response.readline())
        finally:
            _UpstreamHandler.live_release_second.set()

        device = self.store.snapshot()["devices"][0]
        self.store.revoke_device(device["device_id"])
        with self.assertRaises(HTTPError) as raised:
            self._open(
                Request(
                    self.endpoint + "/app.js",
                    method="GET",
                    headers={"Accept": "application/json"},
                )
            )
        try:
            self.assertEqual(HTTPStatus.UNAUTHORIZED, raised.exception.code)
        finally:
            raised.exception.close()

    def test_unpaired_browser_cannot_read_upstream(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            urlopen(
                Request(
                    self.endpoint + "/app.js",
                    headers={"Accept": "application/json"},
                ),
                timeout=5,
            )
        try:
            self.assertEqual(HTTPStatus.UNAUTHORIZED, raised.exception.code)
        finally:
            raised.exception.close()

    def test_gateway_reports_host_offline_when_fixed_upstream_is_unreachable(self) -> None:
        self.upstream_state.write_text(
            json.dumps(
                {
                    "endpoint": "http://127.0.0.1:9",
                    "database": str(self.database),
                }
            ),
            encoding="utf-8",
        )
        with self._open(
            Request(self.endpoint + "/_universe/health", method="GET")
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(HTTPStatus.OK, response.status)
        self.assertEqual("HOST_OFFLINE", payload["status"])
        self.assertEqual("LOOPBACK_FIXED", payload["upstream"])

    def test_gateway_fails_closed_for_non_loopback_upstream_state(self) -> None:
        self.upstream_state.write_text(
            json.dumps(
                {
                    "endpoint": "http://203.0.113.10:18443",
                    "database": str(self.database),
                }
            ),
            encoding="utf-8",
        )
        with self._open(
            Request(self.endpoint + "/_universe/health", method="GET")
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual("HOST_OFFLINE", payload["status"])

    def test_proxy_does_not_forward_browser_credentials_or_proxy_headers(self) -> None:
        invitation = self.store.create_pairing(
            public_base_url=self.endpoint, ttl_seconds=600
        )
        form = urlencode(
            {"code": invitation["code"], "device_name": "Header test"}
        ).encode("utf-8")
        with self._open(
            Request(
                self.endpoint + "/pair/request",
                data=form,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        ):
            pass
        pending = self.store.snapshot()["pairings"][0]
        self.store.decide_pairing(pending["pairing_id"], approve=True)
        with self._open(
            Request(
                self.endpoint + f"/pair/status?id={pending['pairing_id']}",
                method="GET",
            )
        ):
            pass

        session_token = next(
            cookie.value
            for cookie in self.cookie_jar
            if cookie.name == SESSION_COOKIE
        )

        with self._open(
            Request(
                self.endpoint + "/",
                method="GET",
                headers={
                    "Authorization": "Bearer local-secret",
                    "Cookie": f"{SESSION_COOKIE}={session_token}; local-token=secret",
                    "X-Forwarded-Host": "attacker.invalid",
                    "X-Forwarded-For": "203.0.113.10",
                },
            )
        ) as response:
            self.assertIn(b"Universe upstream", response.read())

        observed = _UpstreamHandler.observed_headers
        self.assertNotIn("Authorization", observed)
        self.assertNotIn("Cookie", observed)
        self.assertNotIn("X-Forwarded-Host", observed)
        self.assertNotIn("X-Forwarded-For", observed)
        self.assertNotEqual("attacker.invalid", observed.get("Host"))

    def test_listen_host_rejects_public_and_wildcard_addresses(self) -> None:
        self.assertEqual("127.0.0.1", _validate_listen_host("127.0.0.1"))
        self.assertEqual("192.168.10.5", _validate_listen_host("192.168.10.5"))
        for address in ("0.0.0.0", "211.248.197.157"):
            with self.subTest(address=address):
                with self.assertRaises(GatewayError):
                    _validate_listen_host(address)


if __name__ == "__main__":
    unittest.main()
