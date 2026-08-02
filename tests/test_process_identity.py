from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from process_identity import (  # noqa: E402
    exact_identity_match,
    launched_process_identity,
    process_instance_observation,
    redact_sensitive_argv,
)


@unittest.skipUnless(os.name == "nt", "exact creation-time proof is Windows-only")
class ProcessIdentityTests(unittest.TestCase):
    def test_redaction_covers_inline_secret_arguments(self) -> None:
        redacted = redact_sensitive_argv(
            [
                "worker.exe",
                "--token=inline-token",
                "--API-KEY=inline-key",
                "--client-secret",
                "client-secret-value",
            ]
        )

        self.assertEqual(
            "--token=sha256:" + hashlib.sha256(b"inline-token").hexdigest(),
            redacted[1],
        )
        self.assertEqual(
            "--API-KEY=sha256:" + hashlib.sha256(b"inline-key").hexdigest(),
            redacted[2],
        )
        self.assertNotIn("inline-token", repr(redacted))
        self.assertNotIn("inline-key", repr(redacted))
        self.assertEqual(
            "sha256:" + hashlib.sha256(b"client-secret-value").hexdigest(),
            redacted[4],
        )
        self.assertNotIn("client-secret-value", repr(redacted))

    def test_owned_process_identity_uses_exact_handle_creation_time(self) -> None:
        command = [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            "--token",
            "runtime-secret",
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            identity = launched_process_identity(
                process,
                executable=Path(sys.executable),
                command=command,
                endpoint="http://127.0.0.1:51702",
                handshake_token="secret",
            )
            self.assertEqual(process.pid, identity["pid"])
            self.assertTrue(identity["process_created_at"].endswith("Z"))
            self.assertEqual(command[:-1], identity["command"][:-1])
            self.assertEqual(
                "sha256:" + hashlib.sha256(b"runtime-secret").hexdigest(),
                identity["command"][-1],
            )
            self.assertNotIn("runtime-secret", repr(identity))
            self.assertEqual(
                hashlib.sha256(b"secret").hexdigest(),
                identity["handshake_fingerprint"],
            )
            self.assertTrue(exact_identity_match(identity, dict(identity)))
            mismatch = dict(identity)
            mismatch["endpoint"] = "http://127.0.0.1:59999"
            self.assertFalse(exact_identity_match(identity, mismatch))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def test_handshake_secret_is_redacted_without_flag_semantics(self) -> None:
        secret = "positional-handshake-secret"
        command = [sys.executable, "worker.py", secret, f"--future-flag={secret}"]
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            identity = launched_process_identity(
                process,
                executable=Path(sys.executable),
                command=command,
                endpoint="http://127.0.0.1:51702",
                handshake_token=secret,
            )
            fingerprint = "sha256:" + hashlib.sha256(secret.encode()).hexdigest()
            self.assertEqual(fingerprint, identity["command"][2])
            self.assertEqual(f"--future-flag={fingerprint}", identity["command"][3])
            self.assertNotIn(secret, repr(identity))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def test_process_absence_observation_distinguishes_live_and_gone_instance(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        identity = launched_process_identity(
            process,
            executable=Path(sys.executable),
            command=[sys.executable, "worker.py"],
            endpoint="http://127.0.0.1:51702",
            handshake_token="secret",
        )
        try:
            present = process_instance_observation(
                process.pid, identity["process_created_at"]
            )
            self.assertEqual("PROCESS_PRESENT_EXACT", present["status"])
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        absent = process_instance_observation(
            process.pid, identity["process_created_at"]
        )
        self.assertEqual("ORIGINAL_PROCESS_ABSENT", absent["status"])


if __name__ == "__main__":
    unittest.main()
