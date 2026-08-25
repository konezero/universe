"""Hook correlation regressions for nested cmd chains.

A provider may launch SessionStart through an inner ``cmd /c``:

```text
outer cmd (Supervisor-owned) -> provider CLI -> inner cmd -> hook python
```

Selecting the nearest ancestor cmd would seal the transient hook shell and its
python child, the Supervisor would reject the receipt, and a perfectly healthy
CLI would age into HOOK_TIMEOUT. Selection must be by exact (pid, start time)
identity against the shell the Supervisor actually spawned.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.managed_shell import (  # noqa: E402
    CLI_RUNNING,
    CLI_STARTING,
    ManagedShell,
    ProcessIdentity,
    ShellObservation,
)
from universe_app.terminal_host import TerminalHost  # noqa: E402

TERMINAL = "term_corr"
ANCHOR = "session_anchor_corr"

# outer cmd -> provider CLI -> inner cmd -> hook python
OUTER_CMD = ProcessIdentity(pid=1000, started_at=100.0)
PROVIDER = ProcessIdentity(pid=1001, started_at=101.0)
INNER_CMD = ProcessIdentity(pid=1002, started_at=102.0)
HOOK_PY = ProcessIdentity(pid=1003, started_at=103.0)


def _candidate(shell: ProcessIdentity, cli: ProcessIdentity) -> dict[str, Any]:
    return {
        "shell_pid": shell.pid,
        "shell_started_at": shell.started_at,
        "cli_pid": cli.pid,
        "cli_started_at": cli.started_at,
    }


def _nested_evidence() -> dict[str, Any]:
    """Exactly what the hook reports for the nested chain, nearest first."""

    candidates = [
        _candidate(INNER_CMD, HOOK_PY),
        _candidate(OUTER_CMD, PROVIDER),
    ]
    evidence = {
        "schema": "universe.managed-shell-attach-evidence.v1",
        "terminal_id": TERMINAL,
        "status": "OBSERVED",
        "shell_candidates": candidates,
        "session_anchor_ref": ANCHOR,
    }
    # Legacy flat fields mirror the NEAREST pair, which is the wrong one here.
    evidence.update(candidates[0])
    return evidence


def _simple_evidence() -> dict[str, Any]:
    candidates = [_candidate(OUTER_CMD, PROVIDER)]
    evidence = {
        "schema": "universe.managed-shell-attach-evidence.v1",
        "terminal_id": TERMINAL,
        "status": "OBSERVED",
        "shell_candidates": candidates,
        "session_anchor_ref": ANCHOR,
    }
    evidence.update(candidates[0])
    return evidence


def _host(shell: ManagedShell) -> TerminalHost:
    host = TerminalHost(spawn=lambda *_a, **_k: None)
    session = SimpleNamespace(
        terminal_id=TERMINAL,
        managed_shell=shell,
        backend=None,
        state="LIVE",
        provider="CLAUDE",
        session_anchor_ref=ANCHOR,
        public=lambda: {"terminal_id": TERMINAL},
    )
    host._sessions[TERMINAL] = session  # type: ignore[attr-defined]
    host.record_audit_event = lambda *a, **k: None  # type: ignore
    return host


def _owned_shell() -> ManagedShell:
    shell = ManagedShell(
        terminal_id=TERMINAL,
        session_anchor_ref=ANCHOR,
        shell=OUTER_CMD,
        hook_timeout_seconds=30.0,
    )
    shell.record_cli_launch(at=0.0)
    return shell


class NestedCmdCorrelationTests(unittest.TestCase):
    """The outer managed cmd and the provider CLI must be sealed."""

    def test_outer_managed_cmd_is_selected_not_the_nearest(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        result = host.record_managed_attach(TERMINAL, _nested_evidence())

        self.assertEqual(result["status"], "MANAGED_SHELL_ATTACHED")
        assert shell.attach_evidence is not None
        self.assertEqual(shell.attach_evidence.shell.pid, OUTER_CMD.pid)
        self.assertNotEqual(shell.attach_evidence.shell.pid, INNER_CMD.pid)

    def test_provider_cli_is_sealed_not_the_hook_python(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        host.record_managed_attach(TERMINAL, _nested_evidence())

        assert shell.attach_evidence is not None
        assert shell.attach_evidence.cli is not None
        self.assertEqual(shell.attach_evidence.cli.pid, PROVIDER.pid)
        self.assertNotEqual(shell.attach_evidence.cli.pid, HOOK_PY.pid)

    def test_a_healthy_nested_launch_reaches_cli_running(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        host.record_managed_attach(TERMINAL, _nested_evidence())

        state = shell.evaluate(
            ShellObservation(
                shell_alive=True, shell=OUTER_CMD, cli_children=(PROVIDER,)
            ),
            now=5.0,
        )
        self.assertEqual(
            state,
            CLI_RUNNING,
            "a healthy nested launch must not age into HOOK_TIMEOUT",
        )

    def test_nearest_selection_would_have_failed(self) -> None:
        # Guards the regression itself: sealing the inner pair leaves the
        # terminal unattached, which is how a healthy CLI timed out.
        shell = _owned_shell()
        shell.bind_shell_identity(INNER_CMD)
        host = _host(shell)
        host.record_managed_attach(TERMINAL, _nested_evidence())

        state = shell.evaluate(
            ShellObservation(
                shell_alive=True, shell=OUTER_CMD, cli_children=(PROVIDER,)
            ),
            now=5.0,
        )
        self.assertNotEqual(state, CLI_RUNNING)


class SimpleCmdCorrelationTests(unittest.TestCase):
    """The single-cmd case must keep working unchanged."""

    def test_single_candidate_is_selected(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        result = host.record_managed_attach(TERMINAL, _simple_evidence())

        self.assertEqual(result["status"], "MANAGED_SHELL_ATTACHED")
        assert shell.attach_evidence is not None
        self.assertEqual(shell.attach_evidence.shell.pid, OUTER_CMD.pid)
        self.assertIsNotNone(shell.attach_evidence.cli)

    def test_identity_only_receipt_seals_single_owned_child(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        identity_only = {
            "schema": "universe.managed-shell-attach-evidence.v1",
            "terminal_id": TERMINAL,
            "status": "OBSERVED",
            "session_anchor_ref": ANCHOR,
            "shell_candidates": [
                {
                    "shell_pid": OUTER_CMD.pid,
                    "shell_started_at": OUTER_CMD.started_at,
                    "cli_pid": None,
                    "cli_started_at": None,
                }
            ],
        }
        probes = {
            "is_alive": lambda pid: pid in {OUTER_CMD.pid, PROVIDER.pid},
            "children_of": lambda pid: (PROVIDER,) if pid == OUTER_CMD.pid else (),
            "start_time_of": lambda pid: {
                OUTER_CMD.pid: OUTER_CMD.started_at,
                PROVIDER.pid: PROVIDER.started_at,
            }.get(pid),
            "source": "TEST",
        }
        with mock.patch(
            "universe_app.terminal_host.host_process_probes", return_value=probes
        ):
            result = host.record_managed_attach(TERMINAL, identity_only)

        self.assertEqual("MANAGED_SHELL_ATTACHED", result["status"])
        assert shell.attach_evidence is not None
        self.assertEqual(PROVIDER, shell.attach_evidence.cli)
        self.assertEqual(
            CLI_RUNNING,
            shell.evaluate(
                ShellObservation(
                    shell_alive=True, shell=OUTER_CMD, cli_children=(PROVIDER,)
                ),
                now=5.0,
            ),
        )

    def test_flat_legacy_evidence_without_candidates_still_works(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        legacy = {
            "schema": "universe.managed-shell-attach-evidence.v1",
            "terminal_id": TERMINAL,
            "status": "OBSERVED",
            "session_anchor_ref": ANCHOR,
            **_candidate(OUTER_CMD, PROVIDER),
        }
        result = host.record_managed_attach(TERMINAL, legacy)
        self.assertEqual(result["status"], "MANAGED_SHELL_ATTACHED")


class CorrelationRejectionTests(unittest.TestCase):
    """A receipt describing another tree is rejected, never resolved."""

    def test_no_matching_candidate_is_rejected(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        foreign = {
            "schema": "universe.managed-shell-attach-evidence.v1",
            "terminal_id": TERMINAL,
            "status": "OBSERVED",
            "session_anchor_ref": ANCHOR,
            "shell_candidates": [
                _candidate(INNER_CMD, HOOK_PY),
                _candidate(ProcessIdentity(pid=7777, started_at=77.0), HOOK_PY),
            ],
        }
        result = host.record_managed_attach(TERMINAL, foreign)

        self.assertEqual(result["status"], "ATTACH_SHELL_NOT_MATCHED")
        self.assertIsNone(shell.attach_evidence)
        self.assertTrue(
            any(
                item["kind"] == "ATTACH_SHELL_NOT_MATCHED"
                for item in shell.failure_evidence
            )
        )

    def test_a_recycled_pid_candidate_is_not_accepted(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        recycled = ProcessIdentity(pid=OUTER_CMD.pid, started_at=OUTER_CMD.started_at + 900)
        evidence = {
            "schema": "universe.managed-shell-attach-evidence.v1",
            "terminal_id": TERMINAL,
            "status": "OBSERVED",
            "session_anchor_ref": ANCHOR,
            "shell_candidates": [_candidate(recycled, PROVIDER)],
        }
        result = host.record_managed_attach(TERMINAL, evidence)
        self.assertEqual(result["status"], "ATTACH_SHELL_NOT_MATCHED")

    def test_selection_never_falls_back_to_the_first_candidate(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        evidence = _nested_evidence()
        # Remove the matching entry; only the nearest (wrong) one remains.
        evidence["shell_candidates"] = [_candidate(INNER_CMD, HOOK_PY)]
        result = host.record_managed_attach(TERMINAL, evidence)
        self.assertEqual(result["status"], "ATTACH_SHELL_NOT_MATCHED")


class AttachAnchorRequiredTests(unittest.TestCase):
    """Anchor-first: a receipt must state this session's exact Anchor."""

    def test_host_rejects_an_omitted_anchor(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        evidence = _simple_evidence()
        evidence.pop("session_anchor_ref")
        result = host.record_managed_attach(TERMINAL, evidence)

        self.assertEqual(result["status"], "ATTACH_ANCHOR_REQUIRED")
        self.assertIsNone(shell.attach_evidence)
        self.assertFalse(
            shell.cli_ever_attached, "a refused receipt must not mark attachment"
        )

    def test_host_rejects_an_empty_anchor(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        evidence = _simple_evidence()
        evidence["session_anchor_ref"] = "   "
        result = host.record_managed_attach(TERMINAL, evidence)

        self.assertEqual(result["status"], "ATTACH_ANCHOR_REQUIRED")
        self.assertFalse(shell.cli_ever_attached)

    def test_host_rejects_a_wrong_anchor(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        evidence = _simple_evidence()
        evidence["session_anchor_ref"] = "session_anchor_someone_else"
        result = host.record_managed_attach(TERMINAL, evidence)

        self.assertEqual(result["status"], "ATTACH_ANCHOR_MISMATCH")
        self.assertIsNone(shell.attach_evidence)
        self.assertFalse(shell.cli_ever_attached)

    def test_managed_shell_itself_refuses_an_anchorless_receipt(self) -> None:
        from universe_app.managed_shell import AttachEvidence, ManagedShellError

        shell = _owned_shell()
        with self.assertRaises(ManagedShellError) as caught:
            shell.record_attach_evidence(
                AttachEvidence(terminal_id=TERMINAL, shell=OUTER_CMD, cli=PROVIDER)
            )
        self.assertEqual(
            caught.exception.code, "MANAGED_SHELL_ATTACH_ANCHOR_REQUIRED"
        )
        self.assertFalse(shell.cli_ever_attached)

    def test_managed_shell_itself_refuses_a_wrong_anchor(self) -> None:
        from universe_app.managed_shell import AttachEvidence, ManagedShellError

        shell = _owned_shell()
        with self.assertRaises(ManagedShellError) as caught:
            shell.record_attach_evidence(
                AttachEvidence(
                    terminal_id=TERMINAL,
                    shell=OUTER_CMD,
                    cli=PROVIDER,
                    session_anchor_ref="session_anchor_other",
                )
            )
        self.assertEqual(
            caught.exception.code, "MANAGED_SHELL_ATTACH_ANCHOR_MISMATCH"
        )
        self.assertFalse(shell.cli_ever_attached)

    def test_a_refused_anchor_leaves_the_cli_unattached(self) -> None:
        shell = _owned_shell()
        host = _host(shell)
        evidence = _simple_evidence()
        evidence["session_anchor_ref"] = ""
        host.record_managed_attach(TERMINAL, evidence)
        self.assertNotEqual(
            shell.evaluate(
                ShellObservation(
                    shell_alive=True, shell=OUTER_CMD, cli_children=(PROVIDER,)
                ),
                now=5.0,
            ),
            CLI_RUNNING,
        )


class ServerAttachAnchorTests(unittest.TestCase):
    """The server boundary applies the same fail-closed Anchor rule."""

    def _server_host(self, terminal_anchor: str):
        recorded: list = []

        class _Host:
            def get(self, terminal_id: str):
                if terminal_id != TERMINAL:
                    raise KeyError(terminal_id)
                return SimpleNamespace(
                    public=lambda: {
                        "terminal_id": TERMINAL,
                        "supervisor_session_id": "session_a",
                        "session_anchor_ref": terminal_anchor,
                    }
                )

            def record_managed_attach(self, terminal_id: str, evidence: Any):
                recorded.append(terminal_id)
                return {"status": "MANAGED_SHELL_ATTACHED"}

        host = _Host()
        host.recorded = recorded  # type: ignore[attr-defined]
        return host

    def _call(self, host, anchor_value, present=True):
        from universe_server import _record_managed_shell_attachment

        attach = dict(_simple_evidence())
        if present:
            attach["session_anchor_ref"] = anchor_value
        else:
            attach.pop("session_anchor_ref", None)
        return _record_managed_shell_attachment(
            terminal_host=host,
            body={"managed_shell_attach": attach},
            session={"session_anchor_ref": ANCHOR},
            effective_session_id="session_a",
        )

    def test_server_rejects_an_omitted_anchor(self) -> None:
        host = self._server_host(ANCHOR)
        result = self._call(host, None, present=False)
        self.assertEqual(result["status"], "ATTACH_ANCHOR_REQUIRED")
        self.assertEqual(host.recorded, [])

    def test_server_rejects_an_empty_anchor(self) -> None:
        host = self._server_host(ANCHOR)
        result = self._call(host, "   ")
        self.assertEqual(result["status"], "ATTACH_ANCHOR_REQUIRED")
        self.assertEqual(host.recorded, [])

    def test_server_rejects_a_wrong_anchor(self) -> None:
        host = self._server_host(ANCHOR)
        result = self._call(host, "session_anchor_other")
        self.assertEqual(result["status"], "ATTACH_ANCHOR_MISMATCH")
        self.assertEqual(host.recorded, [])

    def test_server_rejects_when_the_terminal_has_no_anchor(self) -> None:
        host = self._server_host("")
        result = self._call(host, ANCHOR)
        self.assertEqual(result["status"], "ATTACH_ANCHOR_MISMATCH")
        self.assertEqual(host.recorded, [])

    def test_server_accepts_an_exact_anchor(self) -> None:
        host = self._server_host(ANCHOR)
        result = self._call(host, ANCHOR)
        self.assertEqual(result["status"], "MANAGED_SHELL_ATTACHED")
        self.assertEqual(host.recorded, [TERMINAL])


class HookCandidateOrderTests(unittest.TestCase):
    """The hook reports ancestors nearest-first with their descendants."""

    def test_walk_reports_every_ancestor_cmd_in_order(self) -> None:
        import universe_session_inject_hook as hook
        from universe_app import windows_process

        tree = {
            HOOK_PY.pid: (INNER_CMD.pid, "python.exe", HOOK_PY.started_at),
            INNER_CMD.pid: (PROVIDER.pid, "cmd.exe", INNER_CMD.started_at),
            PROVIDER.pid: (OUTER_CMD.pid, "claude.exe", PROVIDER.started_at),
            OUTER_CMD.pid: (None, "cmd.exe", OUTER_CMD.started_at),
        }
        original = (
            windows_process.parent_pid,
            windows_process.process_name,
            windows_process.process_start_time,
            windows_process.native_probes,
        )
        try:
            windows_process.parent_pid = lambda pid: tree.get(pid, (None,))[0]
            windows_process.process_name = lambda pid: tree.get(pid, (None, ""))[1]
            windows_process.process_start_time = lambda pid: tree.get(
                pid, (None, "", None)
            )[2]
            windows_process.native_probes = lambda: {"is_alive": lambda pid: True}
            observed = hook._native_attach_observation.__wrapped__ if hasattr(
                hook._native_attach_observation, "__wrapped__"
            ) else hook._native_attach_observation
            import os

            real_getpid = os.getpid
            os.getpid = lambda: HOOK_PY.pid
            try:
                result = observed(TERMINAL, {"UNIVERSE_SESSION_ANCHOR_REF": ANCHOR})
            finally:
                os.getpid = real_getpid
        finally:
            (
                windows_process.parent_pid,
                windows_process.process_name,
                windows_process.process_start_time,
                windows_process.native_probes,
            ) = original

        if result is None:
            self.skipTest("native inspection is unavailable on this Host")
        self.assertEqual(result["status"], "OBSERVED")
        candidates = result["shell_candidates"]
        self.assertEqual(
            [item["shell_pid"] for item in candidates],
            [INNER_CMD.pid, OUTER_CMD.pid],
            "ancestors must be reported nearest first",
        )
        self.assertEqual(candidates[0]["cli_pid"], HOOK_PY.pid)
        self.assertEqual(
            candidates[1]["cli_pid"],
            PROVIDER.pid,
            "each candidate pairs with its own direct descendant",
        )


if __name__ == "__main__":
    unittest.main()
