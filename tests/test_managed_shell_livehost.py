"""Live-host regressions for Conductor review msg_d2b1eeabd09c0d3d.

Three blockers verified on the real Host are pinned here:

1. psutil is absent on the service and test interpreters, so process
   inspection must work natively or the whole lifecycle is inert.
2. ``sample_managed_shell`` had no product caller; sampling must be scheduled
   from the real per-terminal pump, not only from tests.
3. Recovery ignored GRACE and closed immediately after Ctrl+C; the grace
   window must be a real non-blocking deadline.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.managed_shell import (  # noqa: E402
    DEFAULT_INTERRUPT_GRACE_SECONDS,
    HOOK_TIMEOUT,
    AttachEvidence,
    ManagedShell,
    ProcessIdentity,
    ShellObservation,
    host_process_probes,
    plan_hook_timeout_recovery,
    process_inspection_available,
)
from universe_app.terminal_host import TerminalHost  # noqa: E402
from universe_app import windows_process  # noqa: E402

TERMINAL = "term_live"
ANCHOR = "session_anchor_live"
SHELL = ProcessIdentity(pid=700, started_at=1000.0)
CLI = ProcessIdentity(pid=701, started_at=1001.0)

HOST_SOURCE = (ROOT / "tools" / "universe_app" / "terminal_host.py").read_text(
    encoding="utf-8"
)


def _shell(**overrides: Any) -> ManagedShell:
    kwargs: dict[str, Any] = {
        "terminal_id": TERMINAL,
        "session_anchor_ref": ANCHOR,
        "shell": SHELL,
        "hook_timeout_seconds": 30.0,
    }
    kwargs.update(overrides)
    return ManagedShell(**kwargs)


def _timeout_shell() -> ManagedShell:
    """A launched CLI with no hook receipt: the HOOK_TIMEOUT case."""

    shell = _shell()
    shell.record_cli_launch(at=0.0)
    return shell


def _attached_shell() -> ManagedShell:
    shell = _shell()
    shell.record_cli_launch(at=0.0)
    shell.record_attach_evidence(
        AttachEvidence(
            terminal_id=TERMINAL,
            shell=SHELL,
            cli=CLI,
            session_anchor_ref=ANCHOR,
        )
    )
    return shell


class LiveHostInspectionTests(unittest.TestCase):
    """(1) Inspection must not depend on psutil."""

    def test_psutil_is_not_required(self) -> None:
        probes = host_process_probes()
        self.assertIsNotNone(
            probes, "process inspection must work without psutil installed"
        )
        self.assertTrue(process_inspection_available())

    @unittest.skipUnless(sys.platform == "win32", "Windows-native inspection")
    def test_native_probes_answer_for_this_process(self) -> None:
        pid = os.getpid()
        started = windows_process.process_start_time(pid)
        self.assertIsNotNone(started)
        self.assertGreater(float(started or 0), 0.0)
        self.assertTrue(windows_process.process_is_alive(pid))

    @unittest.skipUnless(sys.platform == "win32", "Windows-native inspection")
    def test_native_probes_report_a_dead_pid_as_not_alive(self) -> None:
        self.assertFalse(windows_process.process_is_alive(0x7FFFFFFF))

    @unittest.skipUnless(sys.platform == "win32", "Windows-native inspection")
    def test_parent_and_children_are_consistent(self) -> None:
        pid = os.getpid()
        parent = windows_process.parent_pid(pid)
        self.assertIsInstance(parent, int)
        self.assertIn(pid, windows_process.child_pids(int(parent or 0)))

    @unittest.skipUnless(sys.platform == "win32", "Windows-native inspection")
    def test_children_carry_paired_identities(self) -> None:
        probes = host_process_probes()
        assert probes is not None
        parent = windows_process.parent_pid(os.getpid()) or 0
        for identity in probes["children_of"](parent):
            self.assertIsInstance(identity, ProcessIdentity)
            self.assertGreater(identity.started_at, 0.0)

    def test_inspection_source_is_reported(self) -> None:
        probes = host_process_probes()
        assert probes is not None
        self.assertIn(probes.get("source"), {"WINDOWS_NATIVE", "PSUTIL"})


class SamplingScheduleTests(unittest.TestCase):
    """(2) Sampling is scheduled by the product pump, not only by tests."""

    def test_pump_loop_schedules_managed_sampling(self) -> None:
        pump = HOST_SOURCE[
            HOST_SOURCE.index("def _pump_session") : HOST_SOURCE.index(
                "def _pump_session"
            )
            + 3000
        ]
        self.assertIn("sample_managed_shell", pump)
        self.assertIn("_managed_sample_interval", pump)

    def test_host_defines_a_sampling_interval(self) -> None:
        host = TerminalHost(spawn=lambda *_a, **_k: None)
        self.assertGreater(host._managed_sample_interval, 0)

    def test_sampling_failure_never_kills_the_pump(self) -> None:
        pump = HOST_SOURCE[HOST_SOURCE.index("def _pump_session") :][:3000]
        guarded = pump[pump.index("sample_managed_shell") - 400 :]
        self.assertIn("except Exception", guarded)


class NonBlockingGraceTests(unittest.TestCase):
    """(3) Grace is a real deadline; recovery never sleeps or closes early."""

    def test_first_pass_interrupts_and_starts_grace_without_closing(self) -> None:
        shell = _attached_shell()
        observation = ShellObservation(
            shell_alive=True, shell=SHELL, cli_children=(CLI,)
        )
        steps = [
            action.step
            for action in plan_hook_timeout_recovery(shell, observation, now=100.0)
        ]
        self.assertIn("INTERRUPT_CLI", steps)
        self.assertIn("START_GRACE", steps)
        self.assertNotIn(
            "CLOSE_SHELL_PTY", steps, "the PTY must not close before grace elapses"
        )

    def test_inside_grace_window_nothing_is_closed(self) -> None:
        shell = _attached_shell()
        shell.grace_deadline = 200.0
        observation = ShellObservation(
            shell_alive=True, shell=SHELL, cli_children=(CLI,)
        )
        steps = [
            action.step
            for action in plan_hook_timeout_recovery(shell, observation, now=150.0)
        ]
        self.assertIn("GRACE", steps)
        self.assertNotIn("CLOSE_SHELL_PTY", steps)

    def test_cli_exiting_within_grace_preserves_the_shell(self) -> None:
        shell = _attached_shell()
        shell.grace_deadline = 200.0
        observation = ShellObservation(shell_alive=True, shell=SHELL)
        steps = [
            action.step
            for action in plan_hook_timeout_recovery(shell, observation, now=250.0)
        ]
        self.assertIn("GRACE_SATISFIED", steps)
        self.assertNotIn("CLOSE_SHELL_PTY", steps)

    def test_expired_grace_with_live_cli_closes_only_this_pty(self) -> None:
        shell = _attached_shell()
        shell.grace_deadline = 200.0
        observation = ShellObservation(
            shell_alive=True, shell=SHELL, cli_children=(CLI,)
        )
        actions = plan_hook_timeout_recovery(shell, observation, now=250.0)
        close = [a for a in actions if a.step == "CLOSE_SHELL_PTY"]
        self.assertEqual(len(close), 1)
        self.assertEqual(close[0].target_pid, SHELL.pid)
        self.assertTrue(all(a.terminal_id == TERMINAL for a in actions))


class GraceRuntimeTests(unittest.TestCase):
    """The Host executes the grace deadline across successive samples."""

    def _host(self, shell: ManagedShell, closed: list[str], written: list[Any]):
        host = TerminalHost(spawn=lambda *_a, **_k: None)
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
        return host

    @staticmethod
    def _probes(children):
        return {
            "is_alive": lambda pid: True,
            "children_of": lambda pid: list(children),
            "start_time_of": lambda pid: SHELL.started_at,
            "source": "TEST",
        }

    def test_first_sample_does_not_close_the_terminal(self) -> None:
        closed: list[str] = []
        written: list[Any] = []
        shell = _timeout_shell()
        host = self._host(shell, closed, written)
        result = host.sample_managed_shell(
            TERMINAL, probes=self._probes([CLI]), now=10_000.0
        )
        self.assertEqual(result["state"], HOOK_TIMEOUT)
        self.assertEqual(closed, [], "grace must elapse before any close")
        self.assertEqual([tid for tid, _ in written], [TERMINAL])
        self.assertIsNotNone(shell.grace_deadline)

    def test_close_happens_only_after_the_grace_deadline(self) -> None:
        closed: list[str] = []
        written: list[Any] = []
        shell = _timeout_shell()
        host = self._host(shell, closed, written)
        host.sample_managed_shell(TERMINAL, probes=self._probes([CLI]), now=10_000.0)
        deadline = shell.grace_deadline or 0.0

        # Still inside the window: nothing closes.
        host.sample_managed_shell(
            TERMINAL, probes=self._probes([CLI]), now=deadline - 0.1
        )
        self.assertEqual(closed, [])

        # Window expired with the CLI still running: this PTY closes.
        host.sample_managed_shell(
            TERMINAL, probes=self._probes([CLI]), now=deadline + 0.1
        )
        self.assertEqual(closed, [TERMINAL])

    def test_cli_exit_within_grace_leaves_the_terminal_open(self) -> None:
        closed: list[str] = []
        written: list[Any] = []
        shell = _timeout_shell()
        host = self._host(shell, closed, written)
        host.sample_managed_shell(TERMINAL, probes=self._probes([CLI]), now=10_000.0)
        deadline = shell.grace_deadline or 0.0
        host.sample_managed_shell(TERMINAL, probes=self._probes([]), now=deadline + 1)
        self.assertEqual(closed, [], "an obedient CLI must not cost the terminal")
        self.assertIsNone(shell.grace_deadline)

    def test_grace_default_is_positive(self) -> None:
        self.assertGreater(DEFAULT_INTERRUPT_GRACE_SECONDS, 0)


if __name__ == "__main__":
    unittest.main()
