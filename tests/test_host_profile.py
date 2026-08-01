from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from host_profile import (  # noqa: E402
    HostProfileError,
    HostProfileStore,
    PROFILE_REVISION,
    PROFILE_SCHEMA,
    default_host_profile_path,
)
from windows_native_cli import NativeCliResult  # noqa: E402


def completed(version: str = "tool 1.0") -> NativeCliResult:
    return NativeCliResult(
        contract="test",
        status="COMPLETED",
        return_code=0,
        duration_ms=1,
        stdout=version,
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )


class HostProfileTests(unittest.TestCase):
    def test_default_path_uses_one_environment_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "host.json"
            self.assertEqual(
                expected.resolve(),
                default_host_profile_path(
                    {
                        "AI_CAREER_HOST_PROFILE": str(expected),
                        "LOCALAPPDATA": str(Path(directory) / "ignored"),
                    }
                ),
            )

    def test_discovery_persists_native_tools_and_rejects_batch_shims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "host.json"
            python = root / "python.exe"
            git = root / "git.exe"
            codex_batch = root / "codex.cmd"
            grok = root / "grok.exe"
            claude = root / ".local" / "bin" / "claude.exe"
            claude.parent.mkdir(parents=True)
            for path in (python, git, codex_batch, grok, claude):
                path.write_bytes(b"placeholder")
            lookup = {
                "git.exe": str(git),
                "codex.exe": str(codex_batch),
                "grok.exe": str(grok),
            }
            calls: list[Path] = []

            def runner(request):
                calls.append(request.executable)
                return completed(request.executable.stem + " 1.0")

            store = HostProfileStore(
                profile_path,
                environment={},
                path_lookup=lambda name: lookup.get(name),
                native_runner=runner,
                current_python=python,
                home=root,
            )
            result = store.discover()

            self.assertEqual("HOST_PROFILE_READY", result["status"])
            self.assertEqual("AVAILABLE", result["tools"]["python"]["status"])
            self.assertEqual("AVAILABLE", result["tools"]["git"]["status"])
            self.assertEqual("UNAVAILABLE", result["tools"]["codex"]["status"])
            self.assertEqual("AVAILABLE", result["tools"]["grok"]["status"])
            self.assertEqual("AVAILABLE", result["tools"]["claude"]["status"])
            self.assertNotIn(codex_batch.resolve(), calls)
            stored = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(PROFILE_SCHEMA, stored["schema"])
            self.assertEqual(PROFILE_REVISION, stored["revision"])
            self.assertEqual("NOT_APPLICABLE", stored["tools"]["python"]["model"])
            self.assertEqual("grok-build", stored["tools"]["grok"]["model"])
            self.assertEqual("default", stored["tools"]["claude"]["model"])

    def test_legacy_environment_is_migration_input_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "host.json"
            python = root / "python.exe"
            git = root / "git.exe"
            codex = root / "codex.exe"
            grok_home = root / "grok"
            grok = grok_home / "bin" / "grok.exe"
            grok.parent.mkdir(parents=True)
            for path in (python, git, codex, grok):
                path.write_bytes(b"placeholder")
            environment = {
                "CODEX_CLI_PATH": str(codex),
                "GROK_HOME": str(grok_home),
            }
            store = HostProfileStore(
                profile_path,
                environment=environment,
                path_lookup=lambda name: str(git) if name == "git.exe" else None,
                native_runner=lambda request: completed(),
                current_python=python,
                home=root,
            )
            store.discover()

            environment.clear()
            codex_resolution = store.resolve("codex")
            grok_resolution = store.resolve("grok")
            self.assertEqual(codex.resolve(), codex_resolution.executable)
            self.assertEqual(grok.resolve(), grok_resolution.executable)
            self.assertEqual(
                str(grok_home.resolve()),
                grok_resolution.environment["GROK_HOME"],
            )

    def test_resolve_fails_closed_when_registered_path_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "host.json"
            python = root / "python.exe"
            git = root / "git.exe"
            python.write_bytes(b"placeholder")
            git.write_bytes(b"placeholder")
            store = HostProfileStore(
                profile_path,
                environment={},
                path_lookup=lambda name: str(git) if name == "git.exe" else None,
                native_runner=lambda request: completed(),
                current_python=python,
                home=root,
            )
            store.discover()
            python.unlink()

            self.assertIsNone(store.resolve("python"))

    def test_existing_profile_discovers_new_supported_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "host.json"
            claude = root / ".local" / "bin" / "claude.exe"
            claude.parent.mkdir(parents=True)
            claude.write_bytes(b"placeholder")
            profile_path.write_text(
                json.dumps(
                    {
                        "schema": PROFILE_SCHEMA,
                        "revision": 1,
                        "created_at": "before",
                        "updated_at": "before",
                        "tools": {},
                    }
                ),
                encoding="utf-8",
            )
            store = HostProfileStore(
                profile_path,
                environment={},
                path_lookup=lambda _name: None,
                native_runner=lambda _request: completed("claude 1.0"),
                current_python=root / "missing-python.exe",
                home=root,
            )

            result = store.ensure_initialized()

            self.assertEqual("AVAILABLE", result["tools"]["claude"]["status"])
            self.assertEqual(
                claude.resolve(),
                store.resolve("claude").executable,
            )
            self.assertEqual(PROFILE_REVISION, result["revision"])

    def test_revision_one_profile_migrates_models_without_rediscovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "host.json"
            claude = root / ".local" / "bin" / "claude.exe"
            claude.parent.mkdir(parents=True)
            claude.write_bytes(b"placeholder")
            profile_path.write_text(
                json.dumps(
                    {
                        "schema": PROFILE_SCHEMA,
                        "revision": 1,
                        "created_at": "before",
                        "updated_at": "before",
                        "tools": {
                            tool: {
                                "status": "AVAILABLE" if tool == "claude" else "UNAVAILABLE",
                                "executable": str(claude) if tool == "claude" else "UNKNOWN",
                                "version": "claude 1.0" if tool == "claude" else "UNKNOWN",
                                "verified_at": "before" if tool == "claude" else "UNKNOWN",
                                "discovery_source": "USER_SELECTED" if tool == "claude" else "NONE",
                                "environment": {},
                                "evidence_ref": "test://claude" if tool == "claude" else "UNKNOWN",
                                "reason": f"{tool.upper()}_NOT_DISCOVERED",
                            }
                            for tool in ("python", "git", "codex", "grok", "claude")
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = HostProfileStore(
                profile_path,
                environment={},
                path_lookup=lambda _name: None,
                native_runner=lambda _request: self.fail("migration rediscovered tools"),
                current_python=root / "missing-python.exe",
                home=root,
            )

            result = store.ensure_initialized()

            self.assertEqual(PROFILE_REVISION, result["revision"])
            self.assertEqual("default", result["tools"]["claude"]["model"])
            self.assertEqual("grok-build", result["tools"]["grok"]["model"])
            self.assertEqual("NOT_APPLICABLE", result["tools"]["git"]["model"])

    def test_provider_model_is_validated_and_preserved_by_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "host.json"
            claude = root / ".local" / "bin" / "claude.exe"
            claude.parent.mkdir(parents=True)
            claude.write_bytes(b"placeholder")
            store = HostProfileStore(
                profile_path,
                environment={},
                path_lookup=lambda _name: None,
                native_runner=lambda _request: completed("claude 2.1"),
                current_python=root / "missing-python.exe",
                home=root,
            )
            store.discover()

            selected = store.set_model("claude", "opus")
            verified = store.verify_tool("claude")

            self.assertEqual("opus", selected["tools"]["claude"]["model"])
            self.assertEqual("opus", verified["tools"]["claude"]["model"])
            self.assertEqual("opus", store.resolve("claude").model)
            with self.assertRaises(HostProfileError) as raised:
                store.set_model("claude", "opus --dangerously-skip-permissions")
            self.assertEqual("HOST_TOOL_MODEL_INVALID", raised.exception.code)
            with self.assertRaises(HostProfileError) as raised:
                store.set_model("python", "3.14")
            self.assertEqual("HOST_TOOL_MODEL_UNSUPPORTED", raised.exception.code)

    def test_profile_rejects_unknown_tool_and_does_not_store_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "host.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": PROFILE_SCHEMA,
                        "tools": {
                            "python": {"status": "UNAVAILABLE"},
                            "token": {"value": "secret"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = HostProfileStore(path, environment={})

            with self.assertRaises(HostProfileError) as raised:
                store.snapshot()
            self.assertEqual("HOST_PROFILE_INVALID", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
