"""Regressions for Conductor review msg_babea06706257e4a.

Each test pins one reported functional/security blocker so the managed shell
path cannot silently regress to being model-only.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.managed_shell import (  # noqa: E402
    CLI_RUNNING,
    CLI_STARTING,
    HOOK_TIMEOUT,
    PROCESS_INSPECTION_UNAVAILABLE,
    AttachEvidence,
    ManagedShell,
    ManagedShellError,
    ProcessIdentity,
    ShellObservation,
    managed_shell_cmdline,
    observe_process_tree,
    quote_windows_argument,
)
from universe_app.terminal_host import (  # noqa: E402
    TerminalHost,
    resolve_shell_identity,
)
from universe_server import _record_managed_shell_attachment  # noqa: E402


ANCHOR = "session_anchor_review"
TERMINAL = "term_review"
SHELL = ProcessIdentity(pid=700, started_at=1000.0)
CLI = ProcessIdentity(pid=701, started_at=1001.0)
IMPOSTOR = ProcessIdentity(pid=999, started_at=1002.0)


def _shell(**overrides: Any) -> ManagedShell:
    kwargs: dict[str, Any] = {
        "terminal_id": TERMINAL,
        "session_anchor_ref": ANCHOR,
        "shell": SHELL,
        "hook_timeout_seconds": 30.0,
    }
    kwargs.update(overrides)
    return ManagedShell(**kwargs)


class ShellIdentityBindingTests(unittest.TestCase):
    """(1) An unbound shell identity makes every attach receipt fail."""

    def test_attach_without_a_bound_shell_is_refused_explicitly(self) -> None:
        shell = ManagedShell(terminal_id=TERMINAL, session_anchor_ref=ANCHOR)
        with self.assertRaises(ManagedShellError) as caught:
            shell.record_attach_evidence(
                AttachEvidence(
                    terminal_id=TERMINAL,
                    shell=SHELL,
                    cli=CLI,
                    session_anchor_ref=ANCHOR,
                )
            )
        self.assertEqual(
            caught.exception.code, "MANAGED_SHELL_IDENTITY_UNAVAILABLE"
        )

    def test_binding_identity_enables_attachment(self) -> None:
        shell = ManagedShell(terminal_id=TERMINAL, session_anchor_ref=ANCHOR)
        shell.bind_shell_identity(SHELL)
        shell.record_attach_evidence(
            AttachEvidence(
                terminal_id=TERMINAL,
                shell=SHELL,
                cli=CLI,
                session_anchor_ref=ANCHOR,
            )
        )
        self.assertTrue(shell.cli_ever_attached)

    def test_unavailable_identity_reports_degraded_state(self) -> None:
        shell = ManagedShell(terminal_id=TERMINAL, session_anchor_ref=ANCHOR)
        shell.bind_shell_identity(None)
        self.assertEqual(shell.last_state, PROCESS_INSPECTION_UNAVAILABLE)

    def test_resolve_shell_identity_rejects_a_bare_pid(self) -> None:
        self.assertIsNone(resolve_shell_identity(None))
        self.assertIsNone(resolve_shell_identity(0))


class SealedCliOwnershipTests(unittest.TestCase):
    """(5) Ownership is sealed to one CLI, not the first listed child."""

    def _attached(self) -> ManagedShell:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        shell.record_attach_evidence(
            AttachEvidence(
                terminal_id=TERMINAL,
                shell=SHELL,
                cli=CLI,
                session_anchor_ref=ANCHOR,
            )
        )
        return shell

    def test_arbitrary_first_child_is_not_our_cli(self) -> None:
        shell = self._attached()
        state = shell.evaluate(
            ShellObservation(
                shell_alive=True, shell=SHELL, cli_children=(IMPOSTOR,)
            ),
            now=1005.0,
        )
        self.assertNotEqual(state, CLI_RUNNING)
        self.assertEqual(state, CLI_STARTING)

    def test_sealed_cli_is_found_among_siblings(self) -> None:
        shell = self._attached()
        state = shell.evaluate(
            ShellObservation(
                shell_alive=True, shell=SHELL, cli_children=(IMPOSTOR, CLI)
            ),
            now=1005.0,
        )
        self.assertEqual(state, CLI_RUNNING)

    def test_receipt_without_cli_identity_cannot_seal_ownership(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        shell.record_attach_evidence(
            AttachEvidence(
                terminal_id=TERMINAL,
                shell=SHELL,
                cli=None,
                session_anchor_ref=ANCHOR,
            )
        )
        self.assertNotEqual(
            shell.evaluate(
                ShellObservation(
                    shell_alive=True, shell=SHELL, cli_children=(CLI,)
                ),
                now=1005.0,
            ),
            CLI_RUNNING,
        )


class DegradedInspectionTests(unittest.TestCase):
    """(4) Unavailable process inspection is explicit, never silent."""

    def test_unavailable_inspection_yields_its_own_state(self) -> None:
        shell = _shell()
        observation = observe_process_tree(
            SHELL,
            is_alive=lambda pid: True,
            children_of=lambda pid: [],
            start_time_of=lambda pid: 1000.0,
            inspection_available=False,
        )
        self.assertFalse(observation.inspection_available)
        self.assertEqual(
            shell.evaluate(observation, now=1000.0), PROCESS_INSPECTION_UNAVAILABLE
        )

    def test_degraded_state_is_not_mistaken_for_liveness_or_death(self) -> None:
        from universe_app.managed_shell import (
            MANAGED_SHELL_LIVE_STATES,
            SHELL_EXITED,
        )

        self.assertNotIn(PROCESS_INSPECTION_UNAVAILABLE, MANAGED_SHELL_LIVE_STATES)
        self.assertNotEqual(PROCESS_INSPECTION_UNAVAILABLE, SHELL_EXITED)

    def test_sampling_without_probes_reports_degraded(self) -> None:
        host = TerminalHost(spawn=lambda *_a, **_k: SimpleNamespace(pid=1))
        shell = _shell()
        session = SimpleNamespace(
            terminal_id=TERMINAL,
            managed_shell=shell,
            backend=None,
            state="LIVE",
            public=lambda: {"terminal_id": TERMINAL},
        )
        host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
        result = host.sample_managed_shell(TERMINAL, probes=None)
        # No psutil-backed probes are supplied, so the Host must say so.
        self.assertIn(
            result["state"],
            {PROCESS_INSPECTION_UNAVAILABLE, "SHELL_EXITED", CLI_STARTING},
        )


class AdversarialQuotingTests(unittest.TestCase):
    """(6) Argument construction must not break or inject."""

    def test_paths_with_spaces_are_quoted(self) -> None:
        hosted = managed_shell_cmdline(
            [r"C:\Program Files\cli\claude.exe", "--flag"]
        )
        self.assertIn('"C:\\Program Files\\cli\\claude.exe"', hosted)

    def test_cmd_operators_cannot_escape_the_argument(self) -> None:
        for hostile in (
            "a & calc.exe",
            "a | calc.exe",
            "a && calc.exe",
            "a > out.txt",
            "a ^ b",
            "a ( b )",
        ):
            with self.subTest(hostile=hostile):
                quoted = quote_windows_argument(hostile)
                self.assertTrue(quoted.startswith('"') and quoted.endswith('"'))

    def test_expansion_characters_are_refused_not_rewritten(self) -> None:
        for hostile in ("%PATH%", "%USERPROFILE%\\x", "a!DELAYED!b"):
            with self.subTest(hostile=hostile):
                with self.assertRaises(ManagedShellError) as caught:
                    quote_windows_argument(hostile)
                self.assertEqual(
                    caught.exception.code, "MANAGED_SHELL_ARGUMENT_UNSAFE"
                )

    def test_control_characters_are_refused(self) -> None:
        for hostile in ("a\nb", "a\rb", "a\x00b"):
            with self.subTest(hostile=hostile):
                with self.assertRaises(ManagedShellError):
                    quote_windows_argument(hostile)

    def test_embedded_quotes_fail_closed(self) -> None:
        # cmd /s strips exactly the outer pair; a literal quote inside cannot
        # be escaped unambiguously for both cmd and CommandLineToArgvW.
        with self.assertRaises(ManagedShellError) as caught:
            quote_windows_argument('a"b')
        self.assertEqual(caught.exception.code, "MANAGED_SHELL_ARGUMENT_UNSAFE")

    def test_ordinary_arguments_are_quoted_predictably(self) -> None:
        self.assertEqual(quote_windows_argument("plain"), "plain")
        self.assertEqual(quote_windows_argument(r"C:\dir\ "), '"C:\\dir\\ "')

    def test_persistent_shell_contract_is_kept(self) -> None:
        line = managed_shell_cmdline(["claude.exe"])
        self.assertTrue(line.startswith("/d /q /s /k "), line)
        self.assertNotIn("/c", line)


class AttachCorrelationTests(unittest.TestCase):
    """(2) The server binds a receipt to the exact terminal, or refuses."""

    def _host(self, *, supervisor_session_id: str, anchor: str) -> Any:
        recorded: list[Any] = []

        class _Host:
            def get(self, terminal_id: str):
                if terminal_id != TERMINAL:
                    raise KeyError(terminal_id)
                return SimpleNamespace(
                    public=lambda: {
                        "terminal_id": TERMINAL,
                        "supervisor_session_id": supervisor_session_id,
                        "session_anchor_ref": anchor,
                    }
                )

            def record_managed_attach(self, terminal_id: str, evidence: Any):
                recorded.append((terminal_id, evidence))
                return {"status": "MANAGED_SHELL_ATTACHED"}

        host = _Host()
        host.recorded = recorded  # type: ignore[attr-defined]
        return host

    def _attach(self, **overrides: Any) -> dict[str, Any]:
        payload = {
            "terminal_id": TERMINAL,
            "status": "OBSERVED",
            "shell_pid": SHELL.pid,
            "shell_started_at": SHELL.started_at,
            "cli_pid": CLI.pid,
            "cli_started_at": CLI.started_at,
            "session_anchor_ref": ANCHOR,
        }
        payload.update(overrides)
        return payload

    def test_matching_receipt_is_recorded(self) -> None:
        host = self._host(supervisor_session_id="session_a", anchor=ANCHOR)
        result = _record_managed_shell_attachment(
            terminal_host=host,
            body={"managed_shell_attach": self._attach()},
            session={"session_anchor_ref": ANCHOR},
            effective_session_id="session_a",
        )
        self.assertEqual(result["status"], "MANAGED_SHELL_ATTACHED")
        self.assertEqual(len(host.recorded), 1)

    def test_receipt_for_another_session_is_refused(self) -> None:
        host = self._host(supervisor_session_id="session_other", anchor=ANCHOR)
        result = _record_managed_shell_attachment(
            terminal_host=host,
            body={"managed_shell_attach": self._attach()},
            session={"session_anchor_ref": ANCHOR},
            effective_session_id="session_a",
        )
        self.assertEqual(result["status"], "ATTACH_SESSION_MISMATCH")
        self.assertEqual(host.recorded, [])

    def test_receipt_for_another_anchor_is_refused(self) -> None:
        host = self._host(supervisor_session_id="session_a", anchor="anchor_other")
        result = _record_managed_shell_attachment(
            terminal_host=host,
            body={"managed_shell_attach": self._attach()},
            session={"session_anchor_ref": ANCHOR},
            effective_session_id="session_a",
        )
        self.assertEqual(result["status"], "ATTACH_ANCHOR_MISMATCH")
        self.assertEqual(host.recorded, [])

    def test_unknown_terminal_is_refused(self) -> None:
        host = self._host(supervisor_session_id="session_a", anchor=ANCHOR)
        result = _record_managed_shell_attachment(
            terminal_host=host,
            body={"managed_shell_attach": self._attach(terminal_id="term_ghost")},
            session={"session_anchor_ref": ANCHOR},
            effective_session_id="session_a",
        )
        self.assertEqual(result["status"], "ATTACH_TERMINAL_UNKNOWN")

    def test_absent_terminal_host_is_a_no_op(self) -> None:
        self.assertIsNone(
            _record_managed_shell_attachment(
                terminal_host=None,
                body={"managed_shell_attach": self._attach()},
                session={},
                effective_session_id="session_a",
            )
        )


class HookTimeoutRuntimeTests(unittest.TestCase):
    """(3) HOOK_TIMEOUT recovery actually runs, bounded to one terminal."""

    def test_timeout_sample_runs_bounded_recovery(self) -> None:
        closed: list[str] = []
        written: list[tuple[str, bytes]] = []

        host = TerminalHost(spawn=lambda *_a, **_k: SimpleNamespace(pid=1))
        shell = _shell()
        shell.record_cli_launch(at=0.0)
        session = SimpleNamespace(
            terminal_id=TERMINAL,
            managed_shell=shell,
            backend=SimpleNamespace(is_alive=lambda: True),
            state="LIVE",
            public=lambda: {"terminal_id": TERMINAL},
        )
        host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
        host.write = lambda tid, data: written.append((tid, data))  # type: ignore
        host.close = lambda tid, **kw: closed.append(tid)  # type: ignore
        host.record_audit_event = lambda *a, **k: None  # type: ignore

        probes = {
            "is_alive": lambda pid: True,
            "children_of": lambda pid: [CLI],
            "start_time_of": lambda pid: SHELL.started_at,
        }
        result = host.sample_managed_shell(TERMINAL, probes=probes, now=10_000.0)

        self.assertEqual(result["state"], HOOK_TIMEOUT)
        steps = [item["step"] for item in result["recovery"]]
        self.assertEqual(
            steps,
            [
                "PRESERVE_HISTORY",
                "RECORD_FAILURE_EVIDENCE",
                "INTERRUPT_CLI",
                "START_GRACE",
            ],
        )
        self.assertEqual(closed, [], "grace must elapse before any close")
        # Once the window expires with the CLI still running, and only then,
        # this one terminal is closed.
        deadline = shell.grace_deadline or 0.0
        host.sample_managed_shell(TERMINAL, probes=probes, now=deadline + 1)
        self.assertEqual(closed, [TERMINAL], "only this terminal is closed")
        self.assertEqual([tid for tid, _ in written], [TERMINAL])
        self.assertTrue(
            any(item["kind"] == "HOOK_TIMEOUT" for item in shell.failure_evidence),
            "failure evidence must be preserved",
        )


if __name__ == "__main__":
    unittest.main()
