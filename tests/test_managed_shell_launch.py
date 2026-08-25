"""Live Windows launch regressions for the managed cmd shell.

These execute real processes. String-shape assertions passed while the managed
shell could not launch anything at all: ``subprocess.list2cmdline`` escaped the
pre-quoted ``/s`` token into ``\\"...\\"``, so cmd read the whole command as a
program name and returned RC 9009. Only running it catches that.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_app.managed_shell import (  # noqa: E402
    ManagedShellError,
    managed_shell_cmdline,
)
from universe_app import windows_process  # noqa: E402

WHERE = r"C:\Windows\System32\where.exe"


def _run(parts, feed="exit\r\n", timeout=30):
    """Launch the managed shell exactly as the product does."""

    line = "cmd.exe " + managed_shell_cmdline(parts)
    process = subprocess.Popen(
        line,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output, _ = process.communicate(feed, timeout=timeout)
    return process.returncode, output


@unittest.skipUnless(sys.platform == "win32", "managed cmd shell is Windows-only")
class ManagedShellLaunchTests(unittest.TestCase):
    """The provider CLI must actually start inside the managed shell."""

    def test_cli_launches_and_produces_its_own_output(self) -> None:
        code, output = _run([WHERE, "cmd.exe"])
        self.assertEqual(code, 0, output)
        self.assertIn("cmd.exe", output.lower())
        self.assertNotIn("9009", output)

    def test_launch_does_not_report_an_unrecognized_command(self) -> None:
        code, output = _run([WHERE, "cmd.exe"])
        # RC 9009 is "not recognized as an internal or external command" --
        # the exact failure a pre-quoted argv produced.
        self.assertNotEqual(code, 9009, output)

    def test_executable_path_containing_spaces_launches(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "dir with space"
            folder.mkdir()
            script = folder / "probe.bat"
            script.write_text("@echo LAUNCHED_OK\r\n", encoding="ascii")
            code, output = _run([str(script)])
            self.assertEqual(code, 0, output)
            self.assertIn("LAUNCHED_OK", output)


@unittest.skipUnless(sys.platform == "win32", "managed cmd shell is Windows-only")
class ManagedShellArgumentTests(unittest.TestCase):
    """Arguments must survive as arguments, not as shell syntax."""

    def _probe(self, folder: Path) -> Path:
        script = folder / "args.bat"
        script.write_text(
            "@echo ARG1=[%1]\r\n@echo ARG2=[%2]\r\n@echo ARG3=[%3]\r\n",
            encoding="ascii",
        )
        return script

    def test_argument_with_spaces_stays_one_argument(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            script = self._probe(Path(raw))
            code, output = _run([str(script), "a b", "second"])
            self.assertEqual(code, 0, output)
            self.assertIn('ARG1=["a b"]', output)
            self.assertIn("ARG2=[second]", output)
            self.assertIn("ARG3=[]", output)

    def test_metacharacter_argument_does_not_inject(self) -> None:
        marker = "INJECTED_MARKER"
        with tempfile.TemporaryDirectory() as raw:
            script = self._probe(Path(raw))
            # If & escaped the argument, cmd would run `echo MARKER` as a
            # separate command and the marker would appear on its own line.
            # Delivered correctly it appears only inside ARG1, because the
            # probe echoes its own argument back.
            code, output = _run([str(script), f"A&echo {marker}"])
            self.assertEqual(code, 0, output)
            lines = [line.strip() for line in output.splitlines()]
            self.assertIn(f'ARG1=["A&echo {marker}"]', lines)
            self.assertNotIn(
                marker,
                lines,
                "an & in an argument must not start a new command",
            )
            self.assertIn("ARG2=[]", lines, "the & must not split the argument")

    def test_metacharacter_reaches_the_program_as_literal_text(self) -> None:
        code, output = _run([WHERE, "A&B"])
        # where.exe simply fails to find a file named A&B; the point is that
        # cmd never treated & as an operator.
        self.assertNotEqual(code, 0)
        self.assertNotIn("Volume", output)

    def test_literal_quote_fails_closed_before_launch(self) -> None:
        with self.assertRaises(ManagedShellError) as caught:
            managed_shell_cmdline([WHERE, 'a"b'])
        self.assertEqual(caught.exception.code, "MANAGED_SHELL_ARGUMENT_UNSAFE")


@unittest.skipUnless(sys.platform == "win32", "managed cmd shell is Windows-only")
class TrailingBackslashTests(unittest.TestCase):
    """A trailing backslash must reach the CLI exactly as written.

    The managed shell hosts .exe provider CLIs, which parse their command line
    with CommandLineToArgvW.  Under that rule a lone backslash before the
    closing quote escapes the quote, so it must be doubled to survive.  A
    batch callee shows the doubled form verbatim because ``%1`` returns the raw
    token and never applies the rule -- that display is not corruption.
    """

    def _argv_of(self, value: str) -> list:
        with tempfile.TemporaryDirectory() as raw:
            printer = Path(raw) / "argprint.py"
            printer.write_text(
                "import sys, json\n"
                "print('ARGV_JSON=' + json.dumps(sys.argv[1:]))\n",
                encoding="ascii",
            )
            code, output = _run([sys.executable, str(printer), value])
            self.assertEqual(code, 0, output)
            for line in output.splitlines():
                if line.startswith("ARGV_JSON="):
                    import json

                    return json.loads(line[len("ARGV_JSON=") :])
        self.fail("the probe produced no argv line")

    def test_trailing_backslash_reaches_the_cli_intact(self) -> None:
        value = "C:\\path with space\\"
        self.assertEqual(self._argv_of(value), [value])

    def test_trailing_backslash_does_not_leak_a_quote(self) -> None:
        received = self._argv_of("C:\\dir\\")[0]
        self.assertNotIn('"', received, "the closing quote must not enter the value")
        self.assertTrue(received.endswith("\\"))

    def test_multiple_trailing_backslashes_are_preserved(self) -> None:
        value = "C:\\deep path\\\\"
        self.assertEqual(self._argv_of(value), [value])

    def test_interior_backslashes_are_untouched(self) -> None:
        value = "C:\\a b\\c d\\file.txt"
        self.assertEqual(self._argv_of(value), [value])


class SpawnConPtyForwardingTests(unittest.TestCase):
    """spawn_conpty must hand the raw command line through unchanged."""

    def _capture(self, argv):
        from universe_app import terminal_host

        seen = {}

        class _Fake:
            def __init__(self, executable, cwd, cols, rows, argv=None, environment=None):
                seen["argv"] = argv
                seen["executable"] = executable

        # spawn_conpty imports the backend lazily, so import it here to patch.
        from universe_app import windows_conpty as module

        original = module.WindowsConPTY
        module.WindowsConPTY = _Fake  # type: ignore[misc]
        try:
            terminal_host.spawn_conpty("cmd.exe", str(ROOT), 120, 32, argv, {})
        finally:
            module.WindowsConPTY = original  # type: ignore[misc]
        return seen

    @unittest.skipUnless(sys.platform == "win32", "spawn_conpty is Windows-only")
    def test_raw_command_line_is_forwarded_verbatim(self) -> None:
        raw = managed_shell_cmdline([WHERE, "cmd.exe"])
        self.assertEqual(self._capture(raw)["argv"], raw)

    @unittest.skipUnless(sys.platform == "win32", "spawn_conpty is Windows-only")
    def test_a_raw_string_is_not_converted_to_a_list(self) -> None:
        forwarded = self._capture('/d /q /s /k "x y"')["argv"]
        self.assertIsInstance(forwarded, str)
        self.assertNotIsInstance(forwarded, list)

    @unittest.skipUnless(sys.platform == "win32", "spawn_conpty is Windows-only")
    def test_empty_string_is_preserved_not_coerced_to_list(self) -> None:
        # ``argv or []`` would turn this into [], losing the spawn shape.
        forwarded = self._capture("")["argv"]
        self.assertEqual(forwarded, "")
        self.assertIsInstance(forwarded, str)

    @unittest.skipUnless(sys.platform == "win32", "spawn_conpty is Windows-only")
    def test_none_becomes_an_empty_list(self) -> None:
        self.assertEqual(self._capture(None)["argv"], [])

    @unittest.skipUnless(sys.platform == "win32", "spawn_conpty is Windows-only")
    def test_list_argv_is_still_forwarded_as_a_list(self) -> None:
        self.assertEqual(self._capture(["a", "b c"])["argv"], ["a", "b c"])


@unittest.skipUnless(sys.platform == "win32", "managed cmd shell is Windows-only")
class ConPtyProductBoundaryTests(unittest.TestCase):
    """Exercise the real WindowsConPTY spawn, not just subprocess."""

    def setUp(self) -> None:
        try:
            import winpty  # type: ignore  # noqa: F401
        except ImportError:
            self.skipTest("winpty is not installed on this Host")
        from universe_app.windows_conpty import WindowsConPTY

        self._conpty = WindowsConPTY

    def _drain(self, backend, needle: str, timeout: float = 20.0) -> str:
        collected = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = backend.read(0.2)
            if chunk:
                collected += chunk.decode("utf-8", "replace")
                if needle in collected:
                    break
        return collected

    def test_conpty_launches_the_cli_through_the_raw_cmdline(self) -> None:
        backend = self._conpty(
            os.environ.get("ComSpec") or "cmd.exe",
            str(ROOT),
            120,
            32,
            managed_shell_cmdline([WHERE, "cmd.exe"]),
            {},
        )
        try:
            output = self._drain(backend, "cmd.exe")
            self.assertIn("cmd.exe", output.lower())
            self.assertNotIn("9009", output)
            self.assertNotIn("is not recognized", output.lower())
        finally:
            backend.close()

    def test_conpty_keeps_an_argument_with_spaces_intact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            script = Path(raw) / "args.bat"
            script.write_text("@echo ARG1=[%1]\r\n", encoding="ascii")
            backend = self._conpty(
                os.environ.get("ComSpec") or "cmd.exe",
                str(ROOT),
                120,
                32,
                managed_shell_cmdline([str(script), "a b"]),
                {},
            )
            try:
                output = self._drain(backend, "ARG1=")
                self.assertIn('ARG1=["a b"]', output)
            finally:
                backend.close()

    def test_conpty_shell_outlives_its_cli(self) -> None:
        backend = self._conpty(
            os.environ.get("ComSpec") or "cmd.exe",
            str(ROOT),
            120,
            32,
            managed_shell_cmdline([WHERE, "cmd.exe"]),
            {},
        )
        try:
            self._drain(backend, "cmd.exe")
            # The CLI has run and exited; /k must keep the shell alive.
            self.assertIsNot(
                backend.is_alive(), False, "the managed shell must outlive its CLI"
            )
            backend.write(b"echo STILL_HERE\r\n")
            self.assertIn("STILL_HERE", self._drain(backend, "STILL_HERE"))

            # Only an explicit exit ends it, and the backend must observe that
            # rather than us merely dropping the handle.
            backend.write(b"exit\r\n")
            deadline = time.time() + 20
            while time.time() < deadline:
                if backend.is_alive() is False:
                    break
                backend.read(0.2)
            self.assertIs(
                backend.is_alive(),
                False,
                "the managed shell must exit on an explicit exit command",
            )
        finally:
            backend.close()


@unittest.skipUnless(sys.platform == "win32", "managed cmd shell is Windows-only")
class ManagedShellPersistenceTests(unittest.TestCase):
    """/k must keep the shell alive after its CLI exits."""

    def test_shell_survives_cli_exit_then_exits_on_explicit_exit(self) -> None:
        line = "cmd.exe " + managed_shell_cmdline([WHERE, "cmd.exe"])
        process = subprocess.Popen(
            line,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            # The CLI (where.exe) runs and exits almost immediately.  A /c
            # shell would die with it; /k must still be alive here.
            deadline = time.time() + 10
            while time.time() < deadline:
                if not windows_process.child_pids(process.pid):
                    break
                time.sleep(0.2)
            self.assertIsNone(
                process.poll(),
                "the managed shell must outlive its CLI (/k, not /c)",
            )
            self.assertTrue(windows_process.process_is_alive(process.pid))

            # Only an explicit exit ends the shell.
            output, _ = process.communicate("exit\r\n", timeout=20)
            self.assertEqual(process.returncode, 0, output)
            self.assertIn("cmd.exe", output.lower())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_shell_remains_for_a_second_command(self) -> None:
        line = "cmd.exe " + managed_shell_cmdline([WHERE, "cmd.exe"])
        process = subprocess.Popen(
            line,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            output, _ = process.communicate(
                "echo SECOND_COMMAND_OK\r\nexit\r\n", timeout=20
            )
            self.assertIn(
                "SECOND_COMMAND_OK",
                output,
                "a persistent shell accepts another command after the CLI exits",
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_managed_shell_pid_is_inspectable_natively(self) -> None:
        line = "cmd.exe " + managed_shell_cmdline([WHERE, "cmd.exe"])
        process = subprocess.Popen(
            line,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            self.assertTrue(windows_process.process_is_alive(process.pid))
            started = windows_process.process_start_time(process.pid)
            self.assertIsNotNone(started)
            self.assertGreater(float(started or 0), 0.0)
        finally:
            process.communicate("exit\r\n", timeout=20)


if __name__ == "__main__":
    unittest.main()
