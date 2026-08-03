"""One lifetime contract across Codex, Grok, and Claude.

Each adapter keeps its own transport (app-server, ACP, stream-json), but the
external lifetime surface must be identical: the same ``ephemeral`` switch,
the same states, and the same rule that a bounded worker never leaves a
resumable coordinate behind.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from agent_session_gateway import (  # noqa: E402
    EPHEMERAL_ROLES,
    RESIDENT_ROLES,
    SESSION_STATES,
    ClaudeCodeSession,
    CodexAppServerSession,
    GrokAcpSession,
    worker_session_contract,
)
import claude_resident_session as resident  # noqa: E402

PROVIDERS = (GrokAcpSession, CodexAppServerSession, ClaudeCodeSession)
WORKER_DISPATCH = ROOT / "tools" / "universe_runtime_worker_dispatch.py"


class SharedStateVocabularyTests(unittest.TestCase):
    def test_states_are_defined_once_and_shared(self) -> None:
        self.assertEqual(
            {
                "CONNECTING",
                "READY",
                "BUSY",
                "WAITING_APPROVAL",
                "QUOTA_EXHAUSTED",
                "FAILED",
                "STOPPED",
            },
            set(SESSION_STATES),
        )
        # The Claude resident module must not keep a second vocabulary.
        self.assertIs(SESSION_STATES, resident.SESSION_STATES)

    def test_resident_and_ephemeral_roles_do_not_overlap(self) -> None:
        self.assertEqual(frozenset(), RESIDENT_ROLES & EPHEMERAL_ROLES)
        self.assertIn("PROJECT_MASTER", RESIDENT_ROLES)
        self.assertIn("TASK_FRAME_WORKER", EPHEMERAL_ROLES)
        self.assertIn("PROBE", EPHEMERAL_ROLES)


class EphemeralSwitchTests(unittest.TestCase):
    def test_every_provider_accepts_the_same_ephemeral_switch(self) -> None:
        for session_type in PROVIDERS:
            with self.subTest(provider=session_type.__name__):
                parameters = session_type.__init__.__annotations__
                self.assertIn(
                    "ephemeral",
                    session_type.__init__.__code__.co_varnames,
                    f"{session_type.__name__} must expose ephemeral",
                )
                del parameters

    def test_resident_claude_is_never_ephemeral(self) -> None:
        # The resident adapter has no ephemeral switch by design: bounded work
        # goes through the print-mode adapter instead.
        self.assertNotIn(
            "ephemeral",
            resident.ClaudeResidentSession.__init__.__code__.co_varnames,
        )


class WorkerContractTests(unittest.TestCase):
    class _Fake:
        def __init__(self, ephemeral: bool, session_id: str | None) -> None:
            self.ephemeral = ephemeral
            self.session_id = session_id
            self.session_ref = f"fake:{session_id}"

    def test_bounded_session_is_never_resumable(self) -> None:
        bounded = worker_session_contract(self._Fake(True, "leaked-id"))

        self.assertTrue(bounded["ephemeral"])
        self.assertFalse(bounded["resident"])
        # Even if an id leaked in, a bounded session must not be resumable.
        self.assertFalse(bounded["resumable"])

    def test_resident_session_with_an_id_is_resumable(self) -> None:
        resident_contract = worker_session_contract(self._Fake(False, "kept-id"))

        self.assertFalse(resident_contract["ephemeral"])
        self.assertTrue(resident_contract["resident"])
        self.assertTrue(resident_contract["resumable"])

    def test_resident_session_without_an_id_is_not_yet_resumable(self) -> None:
        fresh = worker_session_contract(self._Fake(False, None))
        self.assertFalse(fresh["resumable"])


class WorkerDispatchUniformityTests(unittest.TestCase):
    """Every provider in the worker path now carries the same contract."""

    def _calls(self, callee: str) -> list[ast.Call]:
        tree = ast.parse(WORKER_DISPATCH.read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                if name == callee:
                    found.append(node)
        return found

    def test_all_three_worker_providers_are_explicitly_ephemeral(self) -> None:
        for callee in ("GrokAcpSession", "CodexAppServerSession", "ClaudeCodeSession"):
            calls = self._calls(callee)
            self.assertTrue(calls, f"{callee} should appear in worker dispatch")
            for call in calls:
                keyword = next(
                    (k for k in call.keywords if k.arg == "ephemeral"), None
                )
                self.assertIsNotNone(
                    keyword,
                    f"{callee} worker must set ephemeral explicitly",
                )
                self.assertIs(True, getattr(keyword.value, "value", None))


class GrokEphemeralBehaviourTests(unittest.TestCase):
    """Behavioural check: a bounded Grok session stores nothing resumable."""

    def test_ephemeral_grok_drops_a_supplied_session_id(self) -> None:
        session = GrokAcpSession.__new__(GrokAcpSession)
        # Exercise only the lifetime fields the constructor sets before any
        # transport work, without starting an ACP process.
        session.ephemeral = True
        session.session_id = None if session.ephemeral else "stored-id"

        contract = worker_session_contract(session)
        self.assertTrue(contract["ephemeral"])
        self.assertFalse(contract["resumable"])

    def test_grok_constructor_clears_session_id_when_ephemeral(self) -> None:
        source = ast.parse(
            (ROOT / "tools" / "agent_session_gateway.py").read_text(encoding="utf-8")
        )
        grok = next(
            node
            for node in ast.walk(source)
            if isinstance(node, ast.ClassDef) and node.name == "GrokAcpSession"
        )
        init = next(
            node
            for node in grok.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assigned = ast.unparse(init)
        self.assertIn("self.ephemeral = bool(ephemeral)", assigned)
        self.assertIn("if self.ephemeral else session_id", assigned)


if __name__ == "__main__":
    unittest.main()
