from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


from universe_remote_connector import (  # noqa: E402
    CONFIG_SCHEMA,
    RemoteConnectorError,
    _public_state,
    build_ssh_command,
    connector_status,
    load_connector_config,
    normalize_connector_config,
    save_connector_config,
    stop_connector,
)


class RemoteConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = self.root / "universe_ed25519"
        self.known_hosts = self.root / "known_hosts"
        self.ssh = self.root / "ssh.exe"
        self.identity.write_bytes(b"PRIVATE_KEY_MATERIAL")
        self.known_hosts.write_bytes(b"known host")
        self.ssh.write_bytes(b"MZ")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def config(self, **overrides):
        value = {
            "transport_kind": "SSH_REVERSE_TUNNEL",
            "public_base_url": "https://universe.example.test",
            "ssh_host": "server.example.test",
            "ssh_port": 2222,
            "ssh_user": "universe-tunnel",
            "remote_port": 18443,
            "identity_file": str(self.identity),
            "known_hosts_file": str(self.known_hosts),
        }
        value.update(overrides)
        return value

    def test_configuration_is_persisted_without_private_key_material(self) -> None:
        path = self.root / "connector.json"

        saved = save_connector_config(self.config(), path)

        self.assertEqual(CONFIG_SCHEMA, saved["schema"])
        self.assertEqual("127.0.0.1", saved["remote_bind_host"])
        self.assertEqual(saved, load_connector_config(path))
        self.assertNotIn("PRIVATE_KEY_MATERIAL", path.read_text(encoding="utf-8"))

    def test_configuration_rejects_insecure_or_prompting_inputs(self) -> None:
        invalid = (
            {"public_base_url": "http://universe.example.test"},
            {"public_base_url": "https://user:secret@universe.example.test"},
            {"ssh_host": "server.example.test -o ProxyCommand=bad"},
            {"ssh_user": "bad user"},
            {"remote_port": 443},
            {"identity_file": str(self.root / "missing")},
            {"known_hosts_file": str(self.root / "missing-known-hosts")},
        )
        for override in invalid:
            with (
                self.subTest(override=override),
                self.assertRaises(RemoteConnectorError),
            ):
                normalize_connector_config(self.config(**override))

    def test_ssh_command_is_fixed_to_loopback_gateway_and_remote_listener(self) -> None:
        command = build_ssh_command(
            ssh_executable=self.ssh,
            config=self.config(),
            gateway_endpoint="http://127.0.0.1:52742",
        )

        self.assertEqual(str(self.ssh.resolve()), command[0])
        self.assertIn("BatchMode=yes", command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("PasswordAuthentication=no", command)
        self.assertIn("127.0.0.1:18443:127.0.0.1:52742", command)
        self.assertEqual("universe-tunnel@server.example.test", command[-1])
        self.assertNotIn("0.0.0.0", command)

    def test_gateway_endpoint_must_be_loopback_http(self) -> None:
        for endpoint in (
            "https://127.0.0.1:52742",
            "http://192.168.1.2:52742",
            "http://127.0.0.1:52742/path",
        ):
            with (
                self.subTest(endpoint=endpoint),
                self.assertRaises(RemoteConnectorError),
            ):
                build_ssh_command(
                    ssh_executable=self.ssh,
                    config=self.config(),
                    gateway_endpoint=endpoint,
                )

    def test_absent_connector_is_idempotently_offline(self) -> None:
        state = self.root / "missing-state.json"

        self.assertEqual("OFFLINE", connector_status(state)["status"])
        self.assertEqual("OFFLINE", stop_connector(state)["status"])

    def test_public_status_hides_host_and_local_path_details(self) -> None:
        public = _public_state(
            {
                "schema": "universe.remote-connector.v1",
                "status": "READY",
                "transport_kind": "SSH_REVERSE_TUNNEL",
                "public_base_url": "https://universe.example.test",
                "ssh_host": "server.example.test",
                "ssh_user": "universe-tunnel",
                "identity_file_name": "universe_ed25519",
                "gateway_endpoint": "http://127.0.0.1:52742",
                "control_token": "secret",
            }
        )

        self.assertEqual("READY", public["status"])
        self.assertNotIn("ssh_host", public)
        self.assertNotIn("ssh_user", public)
        self.assertNotIn("identity_file_name", public)
        self.assertNotIn("gateway_endpoint", public)
        self.assertNotIn("control_token", public)


if __name__ == "__main__":
    unittest.main()
