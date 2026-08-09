from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from universe_server import (  # noqa: E402
    _windows_tray_command,
    _windows_tray_creationflags,
)


class WindowsTrayContractTests(unittest.TestCase):
    def test_tray_receives_exact_native_python_executable(self) -> None:
        python = Path("C:/Python314/python.exe")
        command = _windows_tray_command(
            powershell="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            tray_script=ROOT / "packaging" / "windows" / "Universe-Tray.ps1",
            universe_root=ROOT,
            python_executable=python,
            start_service=True,
        )
        index = command.index("-PythonExecutable")
        self.assertEqual(str(python.resolve()), command[index + 1])
        self.assertIn("-STA", command)
        self.assertEqual("-StartService", command[-1])

    def test_tray_script_rejects_shell_shims(self) -> None:
        script = (
            ROOT / "packaging" / "windows" / "Universe-Tray.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("native python executable required", script)
        self.assertIn("Universe.ico", script)
        self.assertIn("Local\\Universe.Tray", script)
        self.assertIn("ShowBalloonTip", script)
        self.assertIn("Start saved remote access", script)
        self.assertIn('transport_kind = "SAVED"', script)
        self.assertIn("/v1/settings/remote-access/start", script)
        self.assertNotIn("universe_remote_gateway.py", script)
        self.assertNotIn("Get-Command python -", script)

    def test_windows_shortcuts_use_the_universe_icon(self) -> None:
        for relative_path in (
            Path("packaging/windows/install-user.ps1"),
            Path("packaging/windows/Install-Portable-User.ps1"),
        ):
            script = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("Universe.ico", script)
            self.assertIn("IconLocation", script)

    def test_custom_icon_is_a_valid_ico_asset(self) -> None:
        icon = ROOT / "packaging" / "windows" / "Universe.ico"
        header = icon.read_bytes()
        self.assertGreaterEqual(len(header), 6)
        self.assertEqual(b"\x00\x00\x01\x00", header[:4])
        self.assertGreater(len(header), 32)

    def test_tray_stays_attached_to_the_user_desktop(self) -> None:
        flags = _windows_tray_creationflags()
        detached = getattr(__import__("subprocess"), "DETACHED_PROCESS", 0)
        self.assertEqual(0, flags & detached)


if __name__ == "__main__":
    unittest.main()
