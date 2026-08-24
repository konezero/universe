from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import universe_session_inbox as inbox_cli  # noqa: E402


class SessionInboxCliTests(unittest.TestCase):
    def ready_endpoint(self) -> dict[str, str]:
        return {
            "status": "UNIVERSE_LOCAL_ENDPOINT_READY",
            "endpoint": "http://127.0.0.1:43111",
            "token": "secret-not-for-output",
        }

    def test_list_uses_session_anchor_projection(self) -> None:
        args = inbox_cli._parser().parse_args(
            ["--anchor", "anchor_cli_1", "list", "--projection", "ACTIVITY"]
        )
        with mock.patch.object(inbox_cli, "resolve", return_value=self.ready_endpoint()), mock.patch.object(
            inbox_cli,
            "_http",
            return_value=(200, {"status": "OK", "messages": []}),
        ) as request:
            result = inbox_cli.run(args)

        self.assertEqual("INBOX_COMPLETED", result["status"])
        called_path = request.call_args.args[3]
        self.assertIn("session_anchor_ref=anchor_cli_1", called_path)
        self.assertIn("projection=ACTIVITY", called_path)
        self.assertNotIn("secret-not-for-output", str(result))

    def test_reply_reads_body_file_and_posts_result(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            body_file = Path(temporary) / "reply.txt"
            body_file.write_text("provider result", encoding="utf-8")
            args = inbox_cli._parser().parse_args(
                [
                    "--terminal",
                    "term_cli_1",
                    "reply",
                    "msg_1",
                    "--body-file",
                    str(body_file),
                    "--result-ref",
                    "provider-session://chat/msg",
                ]
            )
            with mock.patch.object(inbox_cli, "resolve", return_value=self.ready_endpoint()), mock.patch.object(
                inbox_cli,
                "_http",
                return_value=(201, {"status": "REPLIED"}),
            ) as request:
                result = inbox_cli.run(args)

        self.assertEqual("INBOX_COMPLETED", result["status"])
        self.assertTrue(request.call_args.args[3].endswith("/msg_1/reply"))
        posted = request.call_args.args[4]
        self.assertEqual("provider result", posted["body_text"])
        self.assertEqual("term_cli_1", posted["terminal_id"])

    def test_state_maps_provider_friendly_working_alias(self) -> None:
        args = inbox_cli._parser().parse_args(
            ["--anchor", "anchor_cli_1", "state", "msg_1", "WORKING"]
        )
        with mock.patch.object(inbox_cli, "resolve", return_value=self.ready_endpoint()), mock.patch.object(
            inbox_cli,
            "_http",
            return_value=(200, {"status": "OK"}),
        ) as request:
            inbox_cli.run(args)

        self.assertEqual("STARTED", request.call_args.args[4]["state"])


if __name__ == "__main__":
    unittest.main()
