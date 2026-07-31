from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_portable import build_portable  # noqa: E402


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
            zip_path = Path(result["zip_path"])
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith("tools/universe_server.py") for name in names))


if __name__ == "__main__":
    unittest.main()
