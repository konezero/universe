from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_portable import build_portable, embed_python, write_launchers  # noqa: E402
from project_integration_catalog import load_project_integration_catalog  # noqa: E402


class BuildPortableTests(unittest.TestCase):
    def test_build_portable_folder_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            host_binary = out / "release-universe-session-host.exe"
            host_binary.write_bytes(b"release-host-binary")
            result = build_portable(
                out,
                make_zip=True,
                session_host_binary=host_binary,
            )
            package = Path(result["package_dir"])
            self.assertTrue(package.is_dir())
            self.assertTrue((package / "tools" / "universe_server.py").is_file())
            self.assertTrue((package / "tools" / "universe_session_inbox.py").is_file())
            self.assertTrue(
                (
                    package
                    / "templates"
                    / "project-integration"
                    / "project-binding.example.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    package
                    / ".ai"
                    / "runtime"
                    / "project_instance"
                    / "mode_registry.json"
                ).is_file()
            )
            packaged_ai_files = [
                path.relative_to(package).as_posix()
                for path in (package / ".ai").rglob("*")
                if path.is_file()
            ]
            self.assertEqual(
                [".ai/runtime/project_instance/mode_registry.json"],
                packaged_ai_files,
            )
            self.assertEqual(
                json.loads(
                    (
                        ROOT / "templates/universe-runtime/mode_registry.json"
                    ).read_text(encoding="utf-8")
                ),
                json.loads(
                    (
                        package
                        / ".ai/runtime/project_instance/mode_registry.json"
                    ).read_text(encoding="utf-8")
                ),
            )
            self.assertTrue((package / "Start-Universe.cmd").is_file())
            self.assertTrue((package / "Start-Universe-Tray.cmd").is_file())
            self.assertTrue(
                (package / "packaging" / "windows" / "Universe.ico").is_file()
            )
            source_icon = ROOT / "packaging" / "windows" / "Universe.ico"
            packaged_icon = package / "packaging" / "windows" / "Universe.ico"
            self.assertEqual(source_icon.read_bytes(), packaged_icon.read_bytes())
            self.assertGreater(len(packaged_icon.read_bytes()), 32)
            self.assertTrue((package / "data" / ".gitkeep").is_file())
            packaged_host = (
                package
                / "runtime"
                / "session-host"
                / "universe-session-host.exe"
            )
            self.assertEqual(host_binary.read_bytes(), packaged_host.read_bytes())
            self.assertFalse((package / "tools" / "session_host" / "target").exists())
            self.assertTrue((package / "VERSION.txt").is_file())
            version = json.loads((package / "VERSION.txt").read_text(encoding="utf-8"))
            self.assertFalse(version["includes_python"])
            self.assertEqual(
                "runtime/session-host/universe-session-host.exe",
                version["reconnection_host"]["path"],
            )
            self.assertEqual(
                hashlib.sha256(host_binary.read_bytes()).hexdigest(),
                version["reconnection_host"]["sha256"],
            )
            catalog_manifest_path = package / "project-integration-catalog.json"
            self.assertTrue(catalog_manifest_path.is_file())
            catalog_manifest = json.loads(catalog_manifest_path.read_text(encoding="utf-8"))
            package_catalog = load_project_integration_catalog(package)
            self.assertEqual(package_catalog, catalog_manifest)
            self.assertEqual(
                package_catalog["catalog_digest"],
                version["project_integration_catalog"]["catalog_digest"],
            )
            self.assertEqual(
                "project-integration-catalog.json",
                version["project_integration_catalog"]["path"],
            )
            launcher = (package / "Start-Universe.cmd").read_text(encoding="utf-8")
            self.assertIn("UNIVERSE_PYTHON", launcher)
            self.assertIn("UNIVERSE_RECONNECTION_HOST_ENABLED=1", launcher)
            self.assertIn(
                r"runtime\session-host\universe-session-host.exe",
                launcher,
            )
            zip_path = Path(result["zip_path"])
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
                self.assertIn(
                    f"{package.name}/packaging/windows/Universe.ico",
                    names,
                )
            self.assertTrue(any(name.endswith("tools/universe_server.py") for name in names))
            self.assertTrue(
                any(name.endswith("runtime/session-host/universe-session-host.exe") for name in names)
            )
            self.assertFalse(any("/tools/session_host/target/" in name for name in names))
            zipped_ai_files = sorted(
                name
                for name in names
                if "/.ai/" in name and not name.endswith("/")
            )
            self.assertEqual(1, len(zipped_ai_files))
            self.assertTrue(
                zipped_ai_files[0].endswith(
                    ".ai/runtime/project_instance/mode_registry.json"
                )
            )

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
