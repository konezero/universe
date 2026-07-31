from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_portable import build_portable, embed_python, write_launchers  # noqa: E402


class BuildPortableTests(unittest.TestCase):
    def test_build_portable_folder_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            result = build_portable(out, make_zip=True)
            package = Path(result["package_dir"])
            self.assertTrue(package.is_dir())
            self.assertTrue((package / "tools" / "universe_server.py").is_file())
            self.assertTrue(
                (
                    package
                    / ".ai"
                    / "runtime"
                    / "project_instance"
                    / "mode_registry.json"
                ).is_file()
            )
            self.assertTrue((package / "Start-Universe.cmd").is_file())
            self.assertTrue((package / "data" / ".gitkeep").is_file())
            self.assertTrue((package / "VERSION.txt").is_file())
            version = json.loads((package / "VERSION.txt").read_text(encoding="utf-8"))
            self.assertFalse(version["includes_python"])
            launcher = (package / "Start-Universe.cmd").read_text(encoding="utf-8")
            self.assertIn("UNIVERSE_PYTHON", launcher)
            zip_path = Path(result["zip_path"])
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith("tools/universe_server.py") for name in names))

    def test_embed_python_from_local_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "pkg"
            package.mkdir()
            # Minimal fake embed zip with python.exe
            zip_path = root / "python-embed.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("python.exe", b"fake")
                archive.writestr("python312._pth", "python312.zip\n.\n#import site\n")
            meta = embed_python(package, zip_path)
            self.assertTrue(Path(meta["python_exe"]).is_file())
            pth = next((package / "runtime" / "python").glob("python*._pth"))
            self.assertIn("import site", pth.read_text(encoding="utf-8"))
            write_launchers(package, includes_python=True)
            text = (package / "Start-Universe.cmd").read_text(encoding="utf-8")
            self.assertIn(r"runtime\python\python.exe", text)


if __name__ == "__main__":
    unittest.main()
