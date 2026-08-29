"""Managed cmd shell lifecycle regressions for msg_8683240ada2fe0c9.

One managed path per terminal: Session Anchor -> Supervisor-owned headless
ConPTY cmd.exe -> provider CLI.  State comes from the owned process tree plus
the SessionStart hook receipt, never from prompt text, PTY activity, or a bare
PID.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.managed_shell import (  # noqa: E402
    CLI_RUNNING,
    CLI_STARTING,
    CLI_START_FAILED,
    HOOK_TIMEOUT,
    MANAGED_SHELL_LIVE_STATES,
    MANAGED_SHELL_STATES,
    PTY_UNRESPONSIVE,
    SHELL_EXITED,
    SHELL_IDLE,
    SHELL_READY,
    AttachEvidence,
    ManagedShell,
    ManagedShellError,
    ProcessIdentity,
    ShellObservation,
    managed_provider_command_line,
    managed_shell_cmdline,
    observe_process_tree,
    plan_hook_timeout_recovery,
)
from universe_app.terminal_host import TerminalHost, TerminalHostError  # noqa: E402


ANCHOR = "session_anchor_managed"
TERMINAL = "term_managed"
SHELL = ProcessIdentity(pid=4242, started_at=1000.0)
CLI = ProcessIdentity(pid=4343, started_at=1001.0)


def _shell(**overrides: Any) -> ManagedShell:
    kwargs: dict[str, Any] = {
        "terminal_id": TERMINAL,
        "session_anchor_ref": ANCHOR,
        "provider": "CLAUDE",
        "shell": SHELL,
        "hook_timeout_seconds": 30.0,
    }
    kwargs.update(overrides)
    return ManagedShell(**kwargs)


def _attach(**overrides: Any) -> AttachEvidence:
    kwargs: dict[str, Any] = {
        "terminal_id": TERMINAL,
        "shell": SHELL,
        "cli": CLI,
        "provider": "CLAUDE",
        "session_anchor_ref": ANCHOR,
    }
    kwargs.update(overrides)
    return AttachEvidence(**kwargs)


class AnchorBeforeSpawnTests(unittest.TestCase):
    def test_managed_shell_requires_a_resolved_anchor(self) -> None:
        with self.assertRaises(ManagedShellError) as caught:
            ManagedShell(terminal_id=TERMINAL, session_anchor_ref="")
        self.assertEqual(caught.exception.code, "MANAGED_SHELL_ANCHOR_REQUIRED")

    def test_terminal_create_refuses_to_spawn_without_an_anchor(self) -> None:
        host = TerminalHost(spawn=lambda *a, **k: self.fail("spawn must not run"))
        with self.assertRaises(TerminalHostError) as caught:
            host.create(
                project_id="universe",
                mode="MASTER",
                cwd=str(ROOT),
                provider="CLAUDE",
            )
        self.assertEqual(caught.exception.code, "TERMINAL_ANCHOR_REQUIRED")


class ManagedShellCmdlineTests(unittest.TestCase):
    """One managed builder; the argv variant was removed as unusable."""

    def test_cmdline_hosts_the_cli_inside_a_persistent_headless_cmd(self) -> None:
        line = managed_shell_cmdline(["claude.exe", "--flag", "value"])
        self.assertTrue(line.startswith("/d /q /s /k "), line)
        self.assertIn("claude.exe --flag value", line)

    def test_provider_command_can_be_written_into_a_host_owned_shell(self) -> None:
        self.assertEqual(
            '"C:\\Program Files\\Claude\\claude.exe" --flag value',
            managed_provider_command_line(
                ["C:\\Program Files\\Claude\\claude.exe", "--flag", "value"]
            ),
        )

    def test_stream_protocol_can_receive_console_input_through_one_cmd(self) -> None:
        line = managed_shell_cmdline(
            ["claude.exe", "--input-format", "stream-json"],
            pipe_console_input=True,
        )
        self.assertEqual(
            '/d /q /s /k "more | claude.exe --input-format stream-json"',
            line,
        )

    def test_shell_is_persistent_not_single_shot(self) -> None:
        # /c would tear the shell down with the CLI, collapsing SHELL_IDLE and
        # CLI_START_FAILED into SHELL_EXITED.
        line = managed_shell_cmdline(["claude.exe"])
        self.assertIn("/k", line)
        self.assertNotIn("/c", line)

    def test_empty_command_is_refused(self) -> None:
        with self.assertRaises(ManagedShellError) as caught:
            managed_shell_cmdline([])
        self.assertEqual(caught.exception.code, "MANAGED_SHELL_COMMAND_REQUIRED")

    def test_the_broken_argv_builder_is_gone(self) -> None:
        from universe_app import managed_shell

        self.assertFalse(
            hasattr(managed_shell, "managed_shell_argv"),
            "a second, list2cmdline-incompatible builder must not exist",
        )


class LifecycleStateTests(unittest.TestCase):
    def test_all_required_states_exist(self) -> None:
        for name in (
            "SHELL_READY",
            "CLI_STARTING",
            "CLI_ATTACHED",
            "CLI_RUNNING",
            "SHELL_IDLE",
            "CLI_START_FAILED",
            "HOOK_TIMEOUT",
            "PTY_UNRESPONSIVE",
            "SHELL_EXITED",
        ):
            self.assertIn(name, MANAGED_SHELL_STATES)

    def test_shell_ready_before_any_cli_launch(self) -> None:
        shell = _shell()
        state = shell.evaluate(
            ShellObservation(shell_alive=True, shell=SHELL), now=1000.0
        )
        self.assertEqual(state, SHELL_READY)

    def test_cli_starting_until_a_hook_receipt_arrives(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        state = shell.evaluate(
            ShellObservation(shell_alive=True, shell=SHELL, cli_children=(CLI,)),
            now=1005.0,
        )
        self.assertEqual(
            state, CLI_STARTING, "a running CLI process alone is not attachment"
        )

    def test_cli_running_needs_both_receipt_and_process(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        shell.record_attach_evidence(_attach())
        self.assertEqual(
            shell.evaluate(
                ShellObservation(shell_alive=True, shell=SHELL, cli_children=(CLI,)),
                now=1005.0,
            ),
            CLI_RUNNING,
        )
        # Receipt without a live CLI child is no longer RUNNING.
        self.assertEqual(
            shell.evaluate(
                ShellObservation(shell_alive=True, shell=SHELL), now=1006.0
            ),
            SHELL_IDLE,
        )

    def test_hook_timeout_when_receipt_never_arrives(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        state = shell.evaluate(
            ShellObservation(shell_alive=True, shell=SHELL, cli_children=(CLI,)),
            now=1031.0,
        )
        self.assertEqual(state, HOOK_TIMEOUT)

    def test_cli_start_failed_when_no_process_appears(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        state = shell.evaluate(
            ShellObservation(shell_alive=True, shell=SHELL), now=1031.0
        )
        self.assertEqual(state, CLI_START_FAILED)

    def test_pty_unresponsive_is_distinct_from_exited(self) -> None:
        from universe_app.managed_shell import PTY_UNRESPONSIVE_OBSERVED

        shell = _shell()
        state = shell.evaluate(
            ShellObservation(
                shell_alive=True,
                shell=SHELL,
                pty_responsive=PTY_UNRESPONSIVE_OBSERVED,
            ),
            now=1000.0,
        )
        self.assertEqual(state, PTY_UNRESPONSIVE)
        self.assertNotEqual(state, SHELL_EXITED)

    def test_a_bare_falsy_responsiveness_value_does_not_derive_unresponsive(
        self,
    ) -> None:
        # Responsiveness is tri-state.  A stray False must not be read as an
        # observed non-answer.
        shell = _shell()
        self.assertNotEqual(
            shell.evaluate(
                ShellObservation(
                    shell_alive=True, shell=SHELL, pty_responsive=False
                ),
                now=1000.0,
            ),
            PTY_UNRESPONSIVE,
        )

    def test_shell_exited_when_process_is_gone(self) -> None:
        shell = _shell()
        self.assertEqual(
            shell.evaluate(ShellObservation(shell_alive=False), now=1000.0),
            SHELL_EXITED,
        )

    def test_recycled_pid_is_not_our_shell(self) -> None:
        shell = _shell()
        recycled = ProcessIdentity(pid=SHELL.pid, started_at=SHELL.started_at + 900.0)
        state = shell.evaluate(
            ShellObservation(shell_alive=True, shell=recycled), now=2000.0
        )
        self.assertEqual(
            state, SHELL_EXITED, "a reused PID must not inherit the lifecycle"
        )


class PersistentShellRuntimeContractTests(unittest.TestCase):
    """The owned shell must survive a normal CLI exit."""

    def test_shell_remains_while_cli_child_exits(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        shell.record_attach_evidence(_attach())

        running = ShellObservation(
            shell_alive=True, shell=SHELL, cli_children=(CLI,)
        )
        self.assertEqual(shell.evaluate(running, now=1005.0), CLI_RUNNING)

        # The CLI exits normally.  The managed cmd shell is still alive, so the
        # terminal is idle and reusable -- not exited.
        cli_exited = ShellObservation(shell_alive=True, shell=SHELL)
        self.assertEqual(
            shell.evaluate(cli_exited, now=1010.0),
            SHELL_IDLE,
            "a persistent shell must report SHELL_IDLE after its CLI exits",
        )
        self.assertNotEqual(shell.last_state, SHELL_EXITED)

        # Only when the shell process itself goes away is it SHELL_EXITED.
        self.assertEqual(
            shell.evaluate(ShellObservation(shell_alive=False), now=1011.0),
            SHELL_EXITED,
        )

    def test_idle_shell_can_host_another_cli_without_respawn(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        shell.record_attach_evidence(_attach())
        shell.evaluate(ShellObservation(shell_alive=True, shell=SHELL), now=1010.0)
        self.assertEqual(shell.last_state, SHELL_IDLE)

        second = ProcessIdentity(pid=CLI.pid + 1, started_at=1020.0)
        shell.record_cli_launch(at=1020.0)
        # The surviving shell hosts a new CLI, which seals its own identity.
        shell.record_attach_evidence(_attach(cli=second))
        self.assertEqual(
            shell.evaluate(
                ShellObservation(
                    shell_alive=True, shell=SHELL, cli_children=(second,)
                ),
                now=1021.0,
            ),
            CLI_RUNNING,
            "the surviving shell keeps its attach receipt for the same terminal",
        )


class AttachEvidenceTests(unittest.TestCase):
    def test_evidence_for_another_terminal_is_refused(self) -> None:
        shell = _shell()
        with self.assertRaises(ManagedShellError) as caught:
            shell.record_attach_evidence(_attach(terminal_id="term_other"))
        self.assertEqual(
            caught.exception.code, "MANAGED_SHELL_ATTACH_TERMINAL_MISMATCH"
        )

    def test_evidence_from_a_different_shell_process_is_refused(self) -> None:
        shell = _shell()
        with self.assertRaises(ManagedShellError) as caught:
            shell.record_attach_evidence(
                _attach(shell=ProcessIdentity(pid=SHELL.pid, started_at=9999.0))
            )
        self.assertEqual(caught.exception.code, "MANAGED_SHELL_ATTACH_SHELL_MISMATCH")


class HookTimeoutRecoveryTests(unittest.TestCase):
    def test_recovery_preserves_history_then_scopes_teardown(self) -> None:
        shell = _shell()
        shell.record_cli_launch(at=1000.0)
        observation = ShellObservation(
            shell_alive=True, shell=SHELL, cli_children=(CLI,)
        )
        # First pass interrupts and opens the grace window; the PTY is only
        # closed on a later pass once that window expires.
        steps = [
            action.step
            for action in plan_hook_timeout_recovery(shell, observation, now=100.0)
        ]
        self.assertEqual(
            steps,
            [
                "PRESERVE_HISTORY",
                "RECORD_FAILURE_EVIDENCE",
                "INTERRUPT_CLI",
                "START_GRACE",
            ],
        )
        shell.grace_deadline = 105.0
        later = [
            action.step
            for action in plan_hook_timeout_recovery(shell, observation, now=110.0)
        ]
        self.assertIn("CLOSE_SHELL_PTY", later)

    def test_recovery_targets_only_this_terminal(self) -> None:
        shell = _shell()
        observation = ShellObservation(
            shell_alive=True, shell=SHELL, cli_children=(CLI,)
        )
        actions = plan_hook_timeout_recovery(shell, observation, now=100.0)
        self.assertTrue(all(a.terminal_id == TERMINAL for a in actions))
        interrupts = [a for a in actions if a.step == "INTERRUPT_CLI"]
        self.assertEqual([a.target_pid for a in interrupts], [CLI.pid])

        shell.grace_deadline = 105.0
        expired = plan_hook_timeout_recovery(shell, observation, now=110.0)
        self.assertTrue(all(a.terminal_id == TERMINAL for a in expired))
        close = [a for a in expired if a.step == "CLOSE_SHELL_PTY"]
        self.assertEqual([a.target_pid for a in close], [SHELL.pid])


class ProcessTreeObservationTests(unittest.TestCase):
    def test_observation_uses_injected_host_probes(self) -> None:
        observation = observe_process_tree(
            SHELL,
            is_alive=lambda pid: pid == SHELL.pid,
            children_of=lambda pid: [CLI],
            start_time_of=lambda pid: SHELL.started_at,
        )
        self.assertTrue(observation.shell_alive)
        self.assertEqual(observation.cli_children, (CLI,))

    def test_dead_shell_reports_not_alive(self) -> None:
        observation = observe_process_tree(
            SHELL,
            is_alive=lambda pid: False,
            children_of=lambda pid: [],
            start_time_of=lambda pid: None,
        )
        self.assertFalse(observation.shell_alive)


class CurrentnessBoundaryTests(unittest.TestCase):
    def test_live_states_never_include_authority_or_currentness(self) -> None:
        # Liveness is an observation about a process tree.  These names must
        # stay free of Mode/Authority vocabulary so no consumer can read
        # currentness out of them.
        for state in MANAGED_SHELL_LIVE_STATES:
            self.assertNotIn("CURRENT", state)
            self.assertNotIn("AUTHORITY", state)
            self.assertNotIn("MASTER", state)


if __name__ == "__main__":
    unittest.main()
