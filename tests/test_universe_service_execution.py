from pathlib import Path
import copy
import json
import sys
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
        self.host = ServiceExecution(self.root, "session-test", state_path=self.state_path)
        self.anchor = {"session_id": "session-test", "frame_id": "current", "anchor_id": "anchor-1", "source_commit": "commit-1"}
        patch = mock.patch.object(self.host, "_anchor", return_value=self.anchor)
        patch.start()
        self.addCleanup(patch.stop)
        self.proposal = self.host.prepare()
        self.approval = {"status": "APPROVED", "proposal_id": self.proposal["proposal_id"], "action_id": "service.restart", "target": str(self.state_path), "expected_pid": 1234, "instruction_ref": "conversation:user-restart"}

    def permit(self):
        binding = self.host.bind(self.proposal, self.approval)
        return self.host.check(binding["binding_id"])

    def test_requires_separate_exact_lifecycle_binding(self):
        with self.assertRaisesRegex(LifecycleError, "binding is required"):
            self.host.check("source-work-receipt")
        for change in ({"expected_pid": 42}, {"instruction_ref": "UNKNOWN"}, {"target": str(self.root)}, {"status": "PENDING"}):
            with self.assertRaises(LifecycleError):
                self.host.bind(self.proposal, {**self.approval, **change})

    def test_one_time_permit_dispatches_exact_proposal_without_leaking_token(self):
        permit = self.permit()
        self.assertEqual("EXECUTION_GUARD_PERMITTED", permit["status"])
        with mock.patch.object(self.host, "_dispatch", return_value={"status": "ACCEPTED"}) as dispatch:
            self.host.execute(permit["receipt_id"])
            dispatch.assert_called_once_with(self.proposal, self.state["token"], permit["receipt_id"])
            with self.assertRaisesRegex(LifecycleError, "consumed"):
                self.host.execute(permit["receipt_id"])
            self.assertEqual(1, dispatch.call_count)
        self.assertNotIn(self.state["token"], json.dumps(self.proposal))
        self.assertNotIn(self.state["token"], self.host.ledger.read_bytes().decode("latin1"))

    def test_expired_permit_cannot_dispatch(self):
        permit = self.permit()
        with mock.patch("universe_service_execution.time.time", return_value=permit["expires_at"] + 1), mock.patch.object(self.host, "_dispatch") as dispatch:
            with self.assertRaises(LifecycleError):
                self.host.execute(permit["receipt_id"])
            dispatch.assert_not_called()

    def test_target_and_anchor_are_rechecked_at_execution(self):
        permit = self.permit()
        with mock.patch.object(self.host, "_dispatch") as dispatch:
            self.anchor["anchor_id"] = "changed-anchor"
            with self.assertRaises(LifecycleError):
                self.host.execute(permit["receipt_id"])
            self.anchor["anchor_id"] = "anchor-1"
            self.state_path.write_text(json.dumps({**self.state, "pid": 42}), encoding="utf-8")
            with self.assertRaises(LifecycleError):
                self.host.execute(permit["receipt_id"])
            dispatch.assert_not_called()

    def test_modified_command_and_external_endpoint_rejected(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["request"]["expected_pid"] = 42
        with self.assertRaises(LifecycleError):
            self.host.bind(proposal, self.approval)
        self.state_path.write_text(json.dumps({**self.state, "endpoint": "http://example.com:12345"}), encoding="utf-8")
        with self.assertRaises(LifecycleError):
            self.host.prepare()

    def test_exact_http_action_and_no_redirect_transport(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"status":"ACCEPTED"}'
        with mock.patch("universe_service_execution.build_opener") as opener:
            opener.return_value.open.return_value = response
            result = self.host._dispatch(self.proposal, "private-token", "receipt")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual("http://127.0.0.1:12345/v1/actions", request.full_url)
            self.assertEqual({"action_id": "service.restart", "request": self.proposal["request"]}, json.loads(request.data))
            self.assertEqual("Bearer private-token", request.headers["Authorization"])
            self.assertNotIn("private-token", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
