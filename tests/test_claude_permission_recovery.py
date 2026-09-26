"""Real MCP processes against an isolated broker, with no provider or repo edits."""
import json
import os
import queue
import subprocess
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from claude_permission_broker import ClaudePermissionBroker
from claude_permission_bridge import ClaudePermissionBridge
from claude_permission_peer import replacement_allowed


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.broker = ClaudePermissionBroker(bridge=ClaudePermissionBridge(
            session_ref="claude-code:isolated-recovery", permission_requester=lambda _: "allow-once"))
        self.broker.start()
        self.environment = {**os.environ, **self.broker.mcp_config()["mcpServers"]["universe_permission"]["env"],
                            "PYTHONIOENCODING": "cp949"}
        self.children = []

    def tearDown(self):
        for p in self.children:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=5)
            for stream in (p.stdin, p.stdout, p.stderr):
                stream.close()
        self.broker.close()

    def child(self):
        p = subprocess.Popen([sys.executable, str(ROOT / "tools/claude_permission_mcp.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.environment, shell=False)
        self.children.append(p)
        responses = queue.Queue()
        def read():
            for line in p.stdout:
                responses.put(json.loads(line.decode("utf-8")))
        threading.Thread(target=read, daemon=True).start()
        def call(method, params=None):
            p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                      "params": params or {}}, ensure_ascii=False) + "\n").encode("utf-8"))
            p.stdin.flush()
            return responses.get(timeout=10)
        call("initialize")
        return p, call

    def approve(self, call, tool_id):
        response = call("tools/call", {"name": "approve", "arguments": {
            "tool_name": "Read", "tool_use_id": tool_id,
            "input": {"file_path": "isolated-한글-😀.txt"}}})
        return json.loads(response["result"]["content"][0]["text"])

    @unittest.skipUnless(sys.platform == "win32", "Windows TCP peer identity")
    def test_real_mcp_restart_rotates_token_and_preserves_replay_denial(self):
        first, call = self.child()
        self.assertIsNotNone(self.broker._registered_peer)
        self.assertEqual("allow", self.approve(call, "before")["behavior"])
        old = self.broker.token.value
        first.kill()
        first.wait(timeout=5)
        # The parent test process has the launch proof but is not a replacement
        # MCP child. Caller-supplied PID/session fields cannot authorize it.
        request = urllib.request.Request(self.broker.endpoint + "/v1/claude-permission/recover",
            data=json.dumps({"pid": first.pid, "session_ref": "claude-code:isolated-recovery"}).encode(),
            headers={"Content-Type": "application/json", "X-Universe-Claude-Permission-Token":
                     self.environment["UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"]}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual("RECOVERY_PEER_INVALID", json.load(response)["reason"])
        second, call = self.child()
        self.assertNotEqual(old, self.broker.token.value)
        self.assertEqual("allow", self.approve(call, "after")["behavior"])
        self.assertIn("DUPLICATE", self.approve(call, "before")["message"])
        self.assertEqual("deny", self.broker.handle_payload({}, presented_token=old)["behavior"])
        self.assertIsNone(second.poll())

    @unittest.skipUnless(sys.platform == "win32", "Windows TCP peer identity")
    def test_second_live_client_is_denied_and_original_keeps_working(self):
        _, original = self.child()
        old = self.broker.token.value
        _, competing = self.child()
        self.assertEqual("CLAUDE_PERMISSION_RECOVERY_DENIED", self.approve(competing, "bad")["message"])
        self.assertEqual(old, self.broker.token.value)
        self.assertEqual("allow", self.approve(original, "good")["behavior"])

    def test_no_os_evidence_bad_proof_and_revocation_fail_closed(self):
        bootstrap = self.environment["UNIVERSE_CLAUDE_PERMISSION_BOOTSTRAP"]
        self.broker.exchange_bootstrap(bootstrap)
        self.assertEqual("RECOVERY_PEER_INVALID", self.broker.recover_connection(bootstrap)["reason"])
        self.assertEqual("RECOVERY_PROOF_INVALID", self.broker.recover_connection("wrong")["reason"])
        self.broker.abort_registration()
        self.assertEqual("RECOVERY_PROOF_INVALID", self.broker.recover_connection(bootstrap)["reason"])
        self.broker.close()
        self.assertEqual("BROKER_STOPPED", self.broker.recover_connection(bootstrap)["reason"])

    def test_other_parent_image_pid_reuse_and_live_old_process_are_denied(self):
        old = dict(pid=10, started=100, parent=20, parent_started=50, image="python.exe")
        new = {**old, "pid": 11, "started": 101}
        for field, value in (("parent", 21), ("parent_started", 51), ("image", "other.exe"), ("started", 99)):
            self.assertFalse(replacement_allowed(old, {**new, field: value}))
        with patch("claude_permission_peer.processes.process_start_time", return_value=100), \
             patch("claude_permission_peer.processes.process_is_alive", return_value=True):
            self.assertFalse(replacement_allowed(old, new))


if __name__ == "__main__":
    unittest.main()
