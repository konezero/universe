"""Pump-thread and native-prototype regressions.

Covers Conductor reviews msg_be5fc29fd16e1386 and msg_49171f824d2eed39:

1. Grace expiry closes the terminal from inside its own pump thread, which
   cannot join itself.
2. TERMINAL_MANAGED_STATE is audited on transition only, not per sample.
3. PTY responsiveness is observed, never aliased from process liveness.
4. Win32 HANDLE-returning APIs declare 64-bit-safe prototypes.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app import windows_process  # noqa: E402
from universe_app.managed_shell import (  # noqa: E402
    CLI_STARTING,
    HOOK_TIMEOUT,
    PTY_RESPONSIVENESS_UNKNOWN,
    PTY_UNRESPONSIVE,
    PTY_UNRESPONSIVE_OBSERVED,
    SHELL_READY,
    ManagedShell,
    ProcessIdentity,
    ShellObservation,
    observe_process_tree,
)
from universe_app.terminal_host import TerminalHost  # noqa: E402

TERMINAL = "term_pump"
ANCHOR = "session_anchor_pump"
SHELL = ProcessIdentity(pid=700, started_at=1000.0)
CLI = ProcessIdentity(pid=701, started_at=1001.0)


def _timeout_shell() -> ManagedShell:
    shell = ManagedShell(
        terminal_id=TERMINAL,
        session_anchor_ref=ANCHOR,
        shell=SHELL,
        hook_timeout_seconds=30.0,
    )
    shell.record_cli_launch(at=0.0)
    return shell


def _probes(children):
    return {
        "is_alive": lambda pid: True,
        "children_of": lambda pid: list(children),
        "start_time_of": lambda pid: SHELL.started_at,
        "source": "TEST",
    }


class PumpSelfJoinTests(unittest.TestCase):
    """(1) A pump thread must be able to close its own terminal."""

    def test_a_thread_cannot_join_itself(self) -> None:
        # Pins the underlying constraint this fix exists for.
        captured: list[BaseException] = []

        def body() -> None:
            try:
                threading.current_thread().join(timeout=0.1)
            except RuntimeError as error:
                captured.append(error)

        thread = threading.Thread(target=body)
        thread.start()
        thread.join()
        self.assertEqual(len(captured), 1)

    def test_close_from_the_pump_thread_completes_cleanup(self) -> None:
        host = TerminalHost(spawn=lambda *_a, **_k: None)
        session = SimpleNamespace(
            terminal_id=TERMINAL,
            managed_shell=_timeout_shell(),
            backend=SimpleNamespace(close=lambda: None),
            state="LIVE",
            pump_stop=threading.Event(),
            pump_thread=None,
            lock=threading.Lock(),
            subscribers=[],
            channel_broker=None,
            public=lambda: {"terminal_id": TERMINAL},
        )
        host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
        host.record_audit_event = lambda *a, **k: None  # type: ignore
        host.bus = SimpleNamespace(drop_terminal=lambda tid: None)  # type: ignore

        errors: list[BaseException] = []

        def pump() -> None:
            session.pump_thread = threading.current_thread()
            try:
                host.close(TERMINAL)
            except BaseException as error:  # noqa: BLE001 - record for assertion
                errors.append(error)

        thread = threading.Thread(target=pump)
        thread.start()
        thread.join(timeout=5)

        self.assertEqual(errors, [], "close from the pump thread must not raise")
        self.assertNotIn(TERMINAL, host._sessions)  # type: ignore[attr-defined]
        self.assertTrue(session.pump_stop.is_set())
        self.assertIsNone(session.pump_thread)

    def test_grace_expiry_closes_cleanly_from_the_real_pump_path(self) -> None:
        """Drive the actual sample->recovery->close path on a pump thread."""

        host = TerminalHost(spawn=lambda *_a, **_k: None)
        shell = _timeout_shell()
        session = SimpleNamespace(
            terminal_id=TERMINAL,
            managed_shell=shell,
            backend=SimpleNamespace(close=lambda: None, is_alive=lambda: True),
            state="LIVE",
            pump_stop=threading.Event(),
            pump_thread=None,
            lock=threading.Lock(),
            subscribers=[],
            channel_broker=None,
            pty_last_read_at=time.time(),
            public=lambda: {"terminal_id": TERMINAL},
        )
        host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
        host.record_audit_event = lambda *a, **k: None  # type: ignore
        host.bus = SimpleNamespace(drop_terminal=lambda tid: None)  # type: ignore
        host.write = lambda tid, data: None  # type: ignore

        errors: list[BaseException] = []
        states: list[str] = []

        def pump() -> None:
            session.pump_thread = threading.current_thread()
            try:
                first = host.sample_managed_shell(
                    TERMINAL, probes=_probes([CLI]), now=10_000.0
                )
                states.append(first["state"])
                deadline = shell.grace_deadline or 0.0
                second = host.sample_managed_shell(
                    TERMINAL, probes=_probes([CLI]), now=deadline + 1
                )
                states.append(second["state"])
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        thread = threading.Thread(target=pump)
        thread.start()
        thread.join(timeout=5)

        self.assertEqual(
            errors, [], "grace expiry must close cleanly from the pump thread"
        )
        self.assertEqual(states, [HOOK_TIMEOUT, HOOK_TIMEOUT])
        self.assertNotIn(
            TERMINAL,
            host._sessions,  # type: ignore[attr-defined]
            "the terminal must actually be closed after grace expiry",
        )


class TransitionOnlyAuditTests(unittest.TestCase):
    """(2) Periodic sampling must not spam the durable audit trail."""

    def _host(self, events: list[Any]):
        host = TerminalHost(spawn=lambda *_a, **_k: None)
        session = SimpleNamespace(
            terminal_id=TERMINAL,
            managed_shell=ManagedShell(
                terminal_id=TERMINAL, session_anchor_ref=ANCHOR, shell=SHELL
            ),
            backend=SimpleNamespace(is_alive=lambda: True),
            state="LIVE",
            pty_last_read_at=time.time(),
            public=lambda: {"terminal_id": TERMINAL},
        )
        host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
        host.record_audit_event = (  # type: ignore
            lambda name, **kw: events.append((name, kw.get("context", {})))
        )
        return host

    def test_unchanged_state_is_never_re_audited(self) -> None:
        events: list[Any] = []
        host = self._host(events)
        for tick in range(5):
            host.sample_managed_shell(
                TERMINAL, probes=_probes([]), now=1000.0 + tick
            )
        managed = [item for item in events if item[0] == "TERMINAL_MANAGED_STATE"]
        self.assertEqual(
            managed, [], "an unchanged lifecycle must not be audited at all"
        )

    def test_a_transition_is_audited_with_its_previous_state(self) -> None:
        events: list[Any] = []
        host = self._host(events)
        host.sample_managed_shell(TERMINAL, probes=_probes([]), now=1000.0)
        shell = host._sessions[TERMINAL].managed_shell  # type: ignore[attr-defined]
        shell.record_cli_launch(at=1000.0)
        host.sample_managed_shell(TERMINAL, probes=_probes([CLI]), now=1001.0)

        managed = [item for item in events if item[0] == "TERMINAL_MANAGED_STATE"]
        self.assertEqual(len(managed), 1, "only the transition is audited")
        self.assertEqual(managed[0][1]["lifecycle_state"], CLI_STARTING)
        self.assertEqual(managed[0][1]["previous_state"], SHELL_READY)


class ResponsivenessIsUnknownTests(unittest.TestCase):
    """(3) Responsiveness is UNKNOWN; PTY_UNRESPONSIVE is never derived."""

    def test_host_reports_unknown_regardless_of_backend(self) -> None:
        for backend in (
            None,
            SimpleNamespace(is_alive=lambda: True),
            SimpleNamespace(is_alive=lambda: False),
        ):
            with self.subTest(backend=backend):
                session = SimpleNamespace(backend=backend)
                self.assertEqual(
                    TerminalHost._pty_responsive(session),
                    PTY_RESPONSIVENESS_UNKNOWN,
                )

    def test_unknown_never_evaluates_to_pty_unresponsive(self) -> None:
        shell = ManagedShell(
            terminal_id=TERMINAL, session_anchor_ref=ANCHOR, shell=SHELL
        )
        state = shell.evaluate(
            ShellObservation(
                shell_alive=True,
                shell=SHELL,
                pty_responsive=PTY_RESPONSIVENESS_UNKNOWN,
            ),
            now=1000.0,
        )
        self.assertNotEqual(state, PTY_UNRESPONSIVE)
        self.assertEqual(state, SHELL_READY)

    def test_default_observation_responsiveness_is_unknown(self) -> None:
        self.assertEqual(
            ShellObservation(shell_alive=True).pty_responsive,
            PTY_RESPONSIVENESS_UNKNOWN,
        )

    def test_observe_process_tree_default_path_is_unknown(self) -> None:
        # The probe default must not reintroduce bool semantics.
        observation = observe_process_tree(
            SHELL,
            is_alive=lambda pid: True,
            children_of=lambda pid: [],
            start_time_of=lambda pid: SHELL.started_at,
        )
        self.assertEqual(observation.pty_responsive, PTY_RESPONSIVENESS_UNKNOWN)
        self.assertNotIsInstance(observation.pty_responsive, bool)

    def test_observe_process_tree_unavailable_path_is_unknown(self) -> None:
        observation = observe_process_tree(
            SHELL,
            is_alive=lambda pid: True,
            children_of=lambda pid: [],
            start_time_of=lambda pid: SHELL.started_at,
            inspection_available=False,
        )
        self.assertEqual(observation.pty_responsive, PTY_RESPONSIVENESS_UNKNOWN)

    def test_only_an_observed_non_answer_derives_unresponsive(self) -> None:
        shell = ManagedShell(
            terminal_id=TERMINAL, session_anchor_ref=ANCHOR, shell=SHELL
        )
        self.assertEqual(
            shell.evaluate(
                ShellObservation(
                    shell_alive=True,
                    shell=SHELL,
                    pty_responsive=PTY_UNRESPONSIVE_OBSERVED,
                ),
                now=1000.0,
            ),
            PTY_UNRESPONSIVE,
        )

    def test_live_sampling_never_produces_pty_unresponsive(self) -> None:
        host = TerminalHost(spawn=lambda *_a, **_k: None)
        shell = ManagedShell(
            terminal_id=TERMINAL, session_anchor_ref=ANCHOR, shell=SHELL
        )
        session = SimpleNamespace(
            terminal_id=TERMINAL,
            managed_shell=shell,
            backend=None,
            state="LIVE",
            public=lambda: {"terminal_id": TERMINAL},
        )
        host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
        host.record_audit_event = lambda *a, **k: None  # type: ignore
        for tick in range(3):
            result = host.sample_managed_shell(
                TERMINAL, probes=_probes([]), now=1000.0 + tick
            )
            self.assertNotEqual(result["state"], PTY_UNRESPONSIVE)


class OneShotTimeoutEvidenceTests(unittest.TestCase):
    """Failure evidence is emitted once per timeout, not per in-grace sample."""

    def _host(self, shell: ManagedShell, closed: list, written: list):
        host = TerminalHost(spawn=lambda *_a, **_k: None)
        session = SimpleNamespace(
            terminal_id=TERMINAL,
            managed_shell=shell,
            backend=SimpleNamespace(is_alive=lambda: True),
            state="LIVE",
            public=lambda: {"terminal_id": TERMINAL},
        )
        host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
        host.write = lambda tid, data: written.append(tid)  # type: ignore
        host.close = lambda tid, **kw: closed.append(tid)  # type: ignore
        host.record_audit_event = lambda *a, **k: None  # type: ignore
        return host

    def test_repeated_in_grace_samples_record_evidence_once(self) -> None:
        closed: list = []
        written: list = []
        shell = _timeout_shell()
        host = self._host(shell, closed, written)

        host.sample_managed_shell(TERMINAL, probes=_probes([CLI]), now=10_000.0)
        deadline = shell.grace_deadline or 0.0
        for tick in range(4):
            host.sample_managed_shell(
                TERMINAL, probes=_probes([CLI]), now=deadline - 1 + (tick * 0.1)
            )

        timeouts = [
            item for item in shell.failure_evidence if item["kind"] == "HOOK_TIMEOUT"
        ]
        self.assertEqual(
            len(timeouts), 1, "one timeout must record evidence exactly once"
        )
        self.assertEqual(closed, [], "still inside the grace window")

    def test_in_grace_samples_emit_no_preserve_or_record_steps(self) -> None:
        closed: list = []
        written: list = []
        shell = _timeout_shell()
        host = self._host(shell, closed, written)

        first = host.sample_managed_shell(
            TERMINAL, probes=_probes([CLI]), now=10_000.0
        )
        self.assertEqual(
            [item["step"] for item in first["recovery"]],
            [
                "PRESERVE_HISTORY",
                "RECORD_FAILURE_EVIDENCE",
                "INTERRUPT_CLI",
                "START_GRACE",
            ],
        )
        deadline = shell.grace_deadline or 0.0
        during = host.sample_managed_shell(
            TERMINAL, probes=_probes([CLI]), now=deadline - 0.5
        )
        self.assertEqual([item["step"] for item in during["recovery"]], ["GRACE"])

    def test_grace_only_samples_write_no_recovery_audit(self) -> None:
        closed: list = []
        written: list = []
        audits: list = []
        shell = _timeout_shell()
        host = self._host(shell, closed, written)
        host.record_audit_event = (  # type: ignore
            lambda name, **kw: audits.append((name, kw.get("context", {})))
        )

        host.sample_managed_shell(TERMINAL, probes=_probes([CLI]), now=10_000.0)
        opening = [
            item for item in audits if item[0] == "TERMINAL_HOOK_TIMEOUT_RECOVERY"
        ]
        self.assertEqual(len(opening), 1, "the opening pass is audited once")

        deadline = shell.grace_deadline or 0.0
        for tick in range(5):
            host.sample_managed_shell(
                TERMINAL, probes=_probes([CLI]), now=deadline - 1 + (tick * 0.1)
            )
        during = [
            item for item in audits if item[0] == "TERMINAL_HOOK_TIMEOUT_RECOVERY"
        ]
        self.assertEqual(
            len(during), 1, "waiting out the grace window must not add audit rows"
        )

        host.sample_managed_shell(
            TERMINAL, probes=_probes([CLI]), now=deadline + 1
        )
        final = [
            item for item in audits if item[0] == "TERMINAL_HOOK_TIMEOUT_RECOVERY"
        ]
        self.assertEqual(len(final), 2, "the terminal close action is audited")
        self.assertIn("CLOSE_SHELL_PTY", final[-1][1]["steps"])

    def test_interrupt_is_delivered_once_not_every_sample(self) -> None:
        closed: list = []
        written: list = []
        shell = _timeout_shell()
        host = self._host(shell, closed, written)

        host.sample_managed_shell(TERMINAL, probes=_probes([CLI]), now=10_000.0)
        deadline = shell.grace_deadline or 0.0
        for tick in range(3):
            host.sample_managed_shell(
                TERMINAL, probes=_probes([CLI]), now=deadline - 1 + (tick * 0.1)
            )
        self.assertEqual(written, [TERMINAL], "the CLI is interrupted once")


@unittest.skipUnless(sys.platform == "win32", "Windows-native prototypes")
class NativePrototypeTests(unittest.TestCase):
    """(4) HANDLE-returning APIs must not default to a 32-bit int."""

    def test_handle_returning_apis_declare_pointer_restype(self) -> None:
        kernel32 = windows_process._kernel32()
        for name in ("OpenProcess", "CreateToolhelp32Snapshot"):
            with self.subTest(api=name):
                restype = getattr(kernel32, name).restype
                self.assertIs(
                    restype,
                    ctypes.c_void_p,
                    f"{name} must return a pointer-width HANDLE",
                )

    def test_supporting_apis_declare_prototypes(self) -> None:
        kernel32 = windows_process._kernel32()
        for name in (
            "CloseHandle",
            "GetProcessTimes",
            "WaitForSingleObject",
            "Process32FirstW",
            "Process32NextW",
        ):
            with self.subTest(api=name):
                self.assertIsNotNone(getattr(kernel32, name).argtypes)

    def test_kernel32_binding_is_cached(self) -> None:
        self.assertIs(windows_process._kernel32(), windows_process._kernel32())

    def test_process_entry_structure_size_matches_windows(self) -> None:
        entry = windows_process._ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(windows_process._ProcessEntry32W)
        # 9 DWORD/long fields, one pointer, and MAX_PATH wide characters.
        expected = ctypes.sizeof(wintypes.DWORD) * 7
        expected += ctypes.sizeof(ctypes.c_long)
        expected += ctypes.sizeof(ctypes.POINTER(ctypes.c_ulong))
        expected += ctypes.sizeof(wintypes.WCHAR) * 260
        self.assertGreaterEqual(entry.dwSize, expected)

    def test_prototypes_survive_a_real_call(self) -> None:
        import os

        pid = os.getpid()
        self.assertTrue(windows_process.process_is_alive(pid))
        self.assertIsNotNone(windows_process.process_start_time(pid))
        self.assertIn(pid, windows_process.child_pids(windows_process.parent_pid(pid) or 0))


if __name__ == "__main__":
    unittest.main()
