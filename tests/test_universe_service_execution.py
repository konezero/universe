from pathlib import Path
from contextlib import closing
import json
import sys
import sqlite3
import tempfile
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from universe_service_execution import ServiceExecution, LifecycleError


class ServiceExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state_path = self.root / "server.json"
        self.state = {"pid": 1234, "endpoint": "http://127.0.0.1:12345", "token": "test-private-token"}
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        self.host = ServiceExecution(self.root, state_path=self.state_path)
        self.request = {"request_id": "restart-audit-1234", "expected_pid": 1234}

    def records(self):
        with closing(sqlite3.connect(self.host.ledger)) as db:
            return [json.loads(r[0]) for r in db.execute("SELECT record_json FROM execution_event ORDER BY rowid")]

    def test_executes_without_session_or_receipt_and_records_observed_phases(self):
        with mock.patch.object(self.host, "_dispatch", return_value={"status": "ACCEPTED"}) as dispatch:
            result = self.host.execute(self.request, instruction_ref="conversation:user-restart")
        self.assertEqual("LIFECYCLE_ACTION_DISPATCHED", result["status"])
        self.assertNotIn("receipt_id", result)
        self.assertEqual(["ATTEMPTED", "VALIDATED", "DISPATCHED"], [r["phase"] for r in self.records()])
        self.assertNotIn(self.state["token"], json.dumps(self.records()))
        self.assertEqual(self.request, dispatch.call_args.args[1]["request"])
        with closing(sqlite3.connect(self.host.ledger)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='permit'").fetchone())

    def test_target_pid_change_is_rejected_and_logged(self):
        with mock.patch.object(self.host, "_dispatch") as dispatch:
            with self.assertRaisesRegex(LifecycleError, "instance changed"):
                self.host.execute({**self.request, "expected_pid": 42}, instruction_ref="user:restart")
        dispatch.assert_not_called()
        self.assertEqual("REJECTED", self.records()[-1]["phase"])
        self.assertEqual("LIFECYCLE_INSTANCE_CHANGED", self.records()[-1]["error_code"])

    def test_arbitrary_command_endpoint_and_receipt_substitution_rejected(self):
        for extra in ({"command": "anything"}, {"endpoint": "http://elsewhere"}, {"receipt_id": "old-permit"}):
            with self.assertRaises(LifecycleError):
                self.host.execute({**self.request, **extra}, instruction_ref="user:restart")
        self.state_path.write_text(json.dumps({**self.state, "endpoint": "http://example.com:12345"}), encoding="utf-8")
        with self.assertRaises(LifecycleError):
            self.host.execute(self.request, instruction_ref="user:restart")

    def test_uncertain_dispatch_is_not_success_and_not_retried(self):
        with mock.patch.object(self.host, "_dispatch", side_effect=LifecycleError("LIFECYCLE_DISPATCH_UNCERTAIN", "connection lost")) as dispatch:
            with self.assertRaises(LifecycleError):
                self.host.execute(self.request, instruction_ref="automation:existing-run")
        self.assertEqual(1, dispatch.call_count)
        self.assertEqual("UNCONFIRMED", self.records()[-1]["phase"])
        self.assertNotIn("SUCCEEDED", [r["phase"] for r in self.records()])

    def test_attempt_record_failure_does_not_dispatch(self):
        with mock.patch.object(self.host, "_record", side_effect=sqlite3.OperationalError("disk full")), mock.patch.object(self.host, "_dispatch") as dispatch:
            with self.assertRaisesRegex(LifecycleError, "record the execution attempt"):
                self.host.execute(self.request, instruction_ref="user:restart")
        dispatch.assert_not_called()

    def test_post_dispatch_audit_failure_preserves_actual_acceptance(self):
        original = self.host._record
        def record(**kwargs):
            if kwargs["phase"] == "DISPATCHED":
                raise sqlite3.OperationalError("disk full")
            return original(**kwargs)
        with mock.patch.object(self.host, "_record", side_effect=record), mock.patch.object(self.host, "_dispatch", return_value={"status": "ACCEPTED"}) as dispatch:
            result = self.host.execute(self.request, instruction_ref="user:restart")
        self.assertEqual("ACCEPTED", result["result"]["status"])
        self.assertEqual("EXECUTION_AUDIT_FAILED", result["audit"]["status"])
        self.assertEqual(1, dispatch.call_count)

    def test_exact_http_action_without_redirect_or_token_in_result(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"ACCEPTED"}'
        body = {"action_id": "service.restart", "request": self.request}
        with mock.patch("universe_service_execution.build_opener") as opener:
            opener.return_value.open.return_value = response
            result = self.host._dispatch(self.state, body)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual("http://127.0.0.1:12345/v1/actions", request.full_url)
            self.assertEqual(body, json.loads(request.data))
            self.assertEqual("Bearer test-private-token", request.headers["Authorization"])
            self.assertNotIn("test-private-token", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
