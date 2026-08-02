"""Lifetime boundary: who may hold a resident Claude session and who may not.

Resident: Universe Conductor, Universe Master, each Project Master.
Not resident: Task Frame Boss, Task Frame Worker, probe/evaluation calls.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

TOOLS = ROOT / "tools"
WORKER_DISPATCH = TOOLS / "universe_runtime_worker_dispatch.py"
PROJECT_MASTER = TOOLS / "project_master_host.py"


def _calls_named(source: Path, callee: str) -> list[ast.Call]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "id", None) or getattr(target, "attr", None)
        if name == callee:
            found.append(node)
    return found


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


class TaskFrameWorkerIsNotResidentTests(unittest.TestCase):
    def test_worker_dispatch_never_builds_a_resident_claude_session(self) -> None:
        """Test 12: Boss/Worker must not share or hold a resident session."""

        source = WORKER_DISPATCH.read_text(encoding="utf-8")

        self.assertNotIn("ClaudeResidentSession", source)
        self.assertNotIn("claude_resident_session", source)
        self.assertNotIn("ClaudePermissionBroker", source)

    def test_every_worker_claude_session_is_ephemeral_and_sessionless(self) -> None:
        calls = _calls_named(WORKER_DISPATCH, "ClaudeCodeSession")
        self.assertTrue(calls, "worker dispatch should build a Claude worker session")

        for call in calls:
            ephemeral = _keyword(call, "ephemeral")
            self.assertIsNotNone(ephemeral, "worker session must set ephemeral")
            self.assertIs(True, getattr(ephemeral, "value", None))

            # A worker must not resume or inherit another session's id.
            session_id = _keyword(call, "session_id")
            self.assertIsNotNone(session_id)
            self.assertIsNone(getattr(session_id, "value", "missing"))

    def test_worker_sessions_reject_permission_requests(self) -> None:
        """A bounded worker has no operator to ask, so it must refuse."""

        for callee in ("ClaudeCodeSession", "CodexAppServerSession", "GrokAcpSession"):
            for call in _calls_named(WORKER_DISPATCH, callee):
                requester = _keyword(call, "permission_requester")
                self.assertIsNotNone(requester, f"{callee} must bind a requester")
                name = getattr(requester, "attr", None) or getattr(
                    requester, "id", None
                )
                self.assertEqual(
                    "_reject_task_frame_permission",
                    name,
                    f"{callee} worker must reject permissions",
                )

    def test_providers_with_an_ephemeral_flag_set_it(self) -> None:
        """Claude and Codex expose ``ephemeral``; both must set it for workers.

        ``GrokAcpSession`` has no ``ephemeral`` parameter, so it is covered by
        the sessionless assertion below instead.
        """

        for callee in ("ClaudeCodeSession", "CodexAppServerSession"):
            calls = _calls_named(WORKER_DISPATCH, callee)
            self.assertTrue(calls, f"{callee} should appear in worker dispatch")
            for call in calls:
                ephemeral = _keyword(call, "ephemeral")
                self.assertIs(
                    True,
                    getattr(ephemeral, "value", None),
                    f"{callee} worker session must be ephemeral",
                )

    def test_no_worker_provider_resumes_a_stored_session(self) -> None:
        """Boundedness for every provider: a worker never reuses a session."""

        for callee in ("ClaudeCodeSession", "CodexAppServerSession", "GrokAcpSession"):
            calls = _calls_named(WORKER_DISPATCH, callee)
            self.assertTrue(calls, f"{callee} should appear in worker dispatch")
            for call in calls:
                session_id = _keyword(call, "session_id")
                self.assertIsNotNone(session_id, f"{callee} must pin session_id")
                self.assertIsNone(
                    getattr(session_id, "value", "missing"),
                    f"{callee} worker must not resume a stored session",
                )


class ProjectMasterIsResidentTests(unittest.TestCase):
    def test_project_master_sessions_are_not_ephemeral(self) -> None:
        """Test 11 (structural half): Master sessions persist, workers do not."""

        for callee in ("ClaudeCodeSession", "CodexAppServerSession", "GrokAcpSession"):
            for call in _calls_named(PROJECT_MASTER, callee):
                ephemeral = _keyword(call, "ephemeral")
                if ephemeral is None:
                    continue  # default is resident
                self.assertIsNot(
                    True,
                    getattr(ephemeral, "value", None),
                    f"{callee} Project Master session must not be ephemeral",
                )

    def test_project_master_observes_session_ids_for_resume(self) -> None:
        calls = _calls_named(PROJECT_MASTER, "ClaudeCodeSession")
        self.assertTrue(calls)
        for call in calls:
            self.assertIsNotNone(
                _keyword(call, "session_observer"),
                "a resident Master session must report its session id",
            )


if __name__ == "__main__":
    unittest.main()
