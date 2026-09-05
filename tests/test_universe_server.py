from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import ANY, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

JsonObject = dict[str, Any]


from core_release import build_release  # noqa: E402
from universe_app.feature_node_proposal import FEATURE_NODE_PROPOSAL_SCHEMA  # noqa: E402
from host_profile import HostProfileStore  # noqa: E402
from todo_mutation_gateway import TodoMutationGateway, TodoMutationGatewayError  # noqa: E402
from project_master_host import (  # noqa: E402
    ProjectMasterHostError,
    ProjectTaskProposalAdapter,
)
from project_seed_assets import materialize_project_seed_assets  # noqa: E402
from universe_app.terminal_host import TerminalHostError  # noqa: E402
from universe_app.session_bus import SessionBusError  # noqa: E402
from universe_server import (  # noqa: E402
    ConductorPermissionBridge,
    ConnectionCapabilities,
    HttpUniverseTransport,
    UniverseError,
    UniverseStore,
    _vendor_chat_key,
    _provider_source_key,
    attach_supervisor_session,
    load_server_state,
    load_universe_mode_registry,
    auth_provider_for,
    connection_profile,
    create_server,
    interface_profile,
    is_governance_approval_command,
    local_connection_profile,
    normalize_conductor_ui_action,
    normalize_conductor_room_message,
    normalize_conductor_delegation,
    normalize_room_message,
    normalize_planning_runtime_binding,
    normalize_project_attachment,
    normalize_skill_observation_candidate,
    perform_session_ref_inject,
    provider_ref_from_model_ref,
    publish_skill_observation,
    prepare_skill_observation_archive,
    require_release_lifecycle_mode,
    resolve_project_work_preflight,
    resolve_universe_mode_intent,
    parser,
    universe_mode_contract,
    write_server_state,
)


class UniverseWorkPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / ".universe").mkdir()
        (self.root / ".universe" / "project.json").write_text(
            json.dumps({"project_id": "GCS"}), encoding="utf-8"
        )
        self.endpoint = "http://127.0.0.1:41999"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_result_claim_serializes_origin_delivery_against_cancellation(self) -> None:
        (self.root / "REPOSITORY_MANIFEST.md").write_text("# GCS\n", encoding="utf-8")
        store = UniverseStore(self.root / "universe.sqlite3")
        store.register_project(
            {"project_id": "GCS", "project_root": str(self.root)}
        )

        def create(key: str) -> dict[str, Any]:
            delegation, _ = store.create_conductor_delegation(
                {
                    "project_id": "GCS",
                    "summary": "Review the bounded result",
                    "idempotency_key": key,
                    "origin_session_anchor_ref": "session_anchor_origin",
                    "target_session_anchor_ref": "session_anchor_target",
                    "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
                }
            )
            return store.start_conductor_delegation(delegation["delegation_id"])

        running = create("result-claim-wins")
        claimed, permitted = store.claim_conductor_delegation_result(
            running["delegation_id"]
        )
        self.assertTrue(permitted)
        self.assertEqual("RESULT_ROUTING", claimed["state"])
        self.assertEqual(
            "RESULT_ROUTING",
            store.cancel_conductor_delegation(running["delegation_id"], {})["state"],
        )
        self.assertEqual(
            "COMPLETED",
            store.complete_conductor_delegation(
                running["delegation_id"], {"result_summary": "bounded result"}
            )["state"],
        )

        cancelled = create("cancellation-wins")
        store.cancel_conductor_delegation(cancelled["delegation_id"], {})
        ignored, permitted = store.claim_conductor_delegation_result(
            cancelled["delegation_id"]
        )
        self.assertFalse(permitted)
        self.assertEqual("CANCELLED", ignored["state"])
        self.assertEqual("PROVIDER_RESULT_IGNORED", ignored["result"]["cancellation_scope"])

    def _responses(self) -> list[tuple[int, dict[str, Any]]]:
        return [
            (200, {"status": "READY", "universe": {"universe_id": "test"}}),
            (
                200,
                {
                    "proposal_id": "project_integration_test",
                    "proposal_digest": "a" * 64,
                    "assets": [{"target_path": ".universe/project.json"}],
                    "apply_contract": {"execution": "NOT_STARTED"},
                },
            ),
        ]

    def test_work_preflight_requires_project_runtime_before_apply(self) -> None:
        with patch("universe_server.request_json", side_effect=self._responses()):
            status, result = resolve_project_work_preflight(
                project_root=self.root,
                project_id="",
                endpoint=self.endpoint,
                token="token",
            )

        self.assertEqual(200, status)
        self.assertEqual("PROJECT_RUNTIME_INSTALL_REQUIRED", result["status"])
        self.assertEqual("NONE", result["effects"]["project_source_write"])

    def test_work_preflight_requires_exact_integration_approval_when_binding_missing(
        self,
    ) -> None:
        manifest = self.root / ".ai" / "runtime" / "project_instance"
        manifest.mkdir(parents=True)
        (manifest / "DISTRIBUTION_MANIFEST.json").write_text("{}\n", encoding="utf-8")

        with patch("universe_server.request_json", side_effect=self._responses()):
            status, result = resolve_project_work_preflight(
                project_root=self.root,
                project_id="GCS",
                endpoint=self.endpoint,
                token="token",
            )

        self.assertEqual(200, status)
        self.assertEqual("PROJECT_INTEGRATION_APPROVAL_REQUIRED", result["status"])
        self.assertEqual(1, result["integration_proposal"]["asset_count"])

    def test_work_preflight_is_ready_for_matching_installed_binding(self) -> None:
        instance = self.root / ".ai" / "runtime" / "project_instance"
        instance.mkdir(parents=True)
        (instance / "DISTRIBUTION_MANIFEST.json").write_text("{}\n", encoding="utf-8")
        binding = self.root / ".ai" / "universe"
        binding.mkdir(parents=True)
        (binding / "install_binding.json").write_text(
            json.dumps(
                {
                    "schema": "universe.install-binding.v1",
                    "project_id": "GCS",
                    "install_mode": "UNIVERSE_ATTACHED",
                    "prefer_boot": "HOST",
                }
            ),
            encoding="utf-8",
        )

        with patch("universe_server.request_json", side_effect=self._responses()):
            status, result = resolve_project_work_preflight(
                project_root=self.root,
                project_id="",
                endpoint=self.endpoint,
                token="token",
            )

        self.assertEqual(200, status)
        self.assertEqual("UNIVERSE_WORK_READY", result["status"])
        self.assertEqual(str(self.root.resolve()), result["work_context"]["cwd"])

    def test_work_command_is_registered(self) -> None:
        args = parser().parse_args(["work", str(self.root), "--project-id", "GCS"])
        self.assertEqual("work", args.command)
        self.assertEqual("GCS", args.project_id)

    def test_shared_work_queue_is_labeled_only_for_cross_session_delegation(self) -> None:
        delegated = normalize_conductor_delegation(
            {
                "project_id": "GCS",
                "summary": "Ask the other session to review the bounded diff.",
                "idempotency_key": "cross-session-test",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            }
        )
        self.assertEqual("CROSS_SESSION_DELEGATION", delegated["queue_scope"])
        with self.assertRaisesRegex(
            UniverseError, "origin and an existing target Session Anchor"
        ):
            normalize_conductor_delegation(
                {
                    "project_id": "GCS",
                    "summary": "Invalid unscoped delegation.",
                    "idempotency_key": "cross-session-missing",
                }
            )
        with self.assertRaisesRegex(
            UniverseError, "origin and an existing target Session Anchor"
        ):
            normalize_conductor_delegation(
                {
                    "project_id": "GCS",
                    "summary": "Invalid partial delegation.",
                    "idempotency_key": "cross-session-partial",
                    "origin_session_anchor_ref": "session_anchor_origin",
                }
            )


class UniverseLocalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp.name)
        self.temp_root = temp_root
        self.project_root = temp_root / "GCS"
        runtime_root = self.project_root / ".ai" / "runtime"
        project_instance = runtime_root / "project_instance"
        project_instance.mkdir(parents=True)
        (runtime_root / "anchor_store").mkdir()
        (self.project_root / ".ai" / "inbox" / "MASTER").mkdir(parents=True)
        (self.project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# GCS Repository Manifest\n",
            encoding="utf-8",
        )
        (project_instance / "mode_registry.json").write_text(
            json.dumps(
                {
                    "schema": "ai-career.mode-registry.v2",
                    "owner": "GCS",
                    "repository_kind": "PROJECT",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 1,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (project_instance / "status.md").write_text("Status: READY\n", encoding="utf-8")
        (self.project_root / "src").mkdir()
        (self.project_root / "docs").mkdir()
        self.broker_source = self.project_root / "src" / "broker.py"
        self.viewer_source = self.project_root / "src" / "viewer.py"
        self.architecture_doc = self.project_root / "docs" / "architecture.md"
        self.contract_doc = self.project_root / "docs" / "broker-contract.md"
        self.orphan_doc = self.project_root / "docs" / "operations.md"
        self.broker_source.write_text(
            "class BrokerClient:\n    pass\n", encoding="utf-8"
        )
        self.viewer_source.write_text(
            "class StrategyViewer:\n    pass\n", encoding="utf-8"
        )
        self.architecture_doc.write_text("# GCS Architecture\n", encoding="utf-8")
        self.contract_doc.write_text("# Broker Contract\n", encoding="utf-8")
        self.orphan_doc.write_text("# Operations\n", encoding="utf-8")

        self.token = "test-token"
        self.server = create_server(
            database_path=temp_root / "universe.sqlite3",
            token=self.token,
            auto_start_project_masters=False,
            mode_contract=universe_mode_contract(
                {
                    "owner": "universe",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 3,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                        },
                        "CONDUCTOR": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                        },
                    },
                }
            ),
            host_profile=HostProfileStore(temp_root / "host.json"),
            service_state_path=temp_root / "server.json",
            remote_gateway_state_path=temp_root / "remote-gateway.json",
            remote_connector_state_path=temp_root / "remote-connector.json",
            remote_connector_config_path=temp_root / "remote-connector-config.json",
            auto_start_goal_scheduler=False,
        )
        self.host_tool_patchers = [
            patch(
                "core_release.resolve_host_tool",
                side_effect=self.server.host_profile.resolve,
            ),
        ]
        for patcher in self.host_tool_patchers:
            patcher.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        if isinstance(host, bytes):
            host = host.decode("ascii")
        self.endpoint = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        for patcher in reversed(self.host_tool_patchers):
            patcher.stop()
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        token: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, JsonObject]:
        body = None
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            headers.update(extra_headers)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.endpoint + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()

    def registration(self, **overrides: object) -> JsonObject:
        value: JsonObject = {
            "project_id": "GCS",
            "project_root": str(self.project_root),
        }
        value.update(overrides)
        return value

    def test_native_directory_picker_returns_selected_host_path(self) -> None:
        self.server.directory_selector = lambda: str(self.project_root)

        status, payload = self.request(
            "POST", "/v1/host/select-directory", {}, self.token
        )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("DIRECTORY_SELECTED", payload["status"])
        self.assertEqual(str(self.project_root.resolve()), payload["directory"])

    def test_native_directory_picker_cancel_is_a_noop(self) -> None:
        self.server.directory_selector = lambda: None

        status, payload = self.request(
            "POST", "/v1/host/select-directory", {}, self.token
        )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("DIRECTORY_SELECTION_CANCELLED", payload["status"])
        self.assertIsNone(payload["directory"])

    def test_native_release_file_picker_returns_selected_host_path(self) -> None:
        database = self.project_root / "release.sqlite3"
        database.write_bytes(b"sqlite fixture")
        self.server.file_selector = lambda kind: (
            str(database) if kind == "RELEASE_DATABASE" else None
        )

        status, payload = self.request(
            "POST",
            "/v1/host/select-file",
            {"kind": "RELEASE_DATABASE"},
            self.token,
        )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FILE_SELECTED", payload["status"])
        self.assertEqual(str(database.resolve()), payload["file"])

    def test_native_release_file_picker_cancel_is_a_noop(self) -> None:
        self.server.file_selector = lambda kind: None

        status, payload = self.request(
            "POST",
            "/v1/host/select-file",
            {"kind": "RELEASE_MANIFEST"},
            self.token,
        )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FILE_SELECTION_CANCELLED", payload["status"])
        self.assertIsNone(payload["file"])

    def create_task_proposal_fixture(
        self,
        *,
        scope: JsonObject | None = None,
        request_ref: str = "universe://project-room/messages/request-001",
    ) -> JsonObject:
        database_path = (
            self.project_root
            / ".ai"
            / "runtime"
            / "task_frames"
            / "task-proposals.sqlite3"
        )
        database_path.parent.mkdir(parents=True, exist_ok=True)
        proposal_digest = "a" * 64
        proposal: JsonObject = {
            "schema": "ai-career.task-proposal.v1",
            "status": "TASK_PROPOSAL_CREATED",
            "proposal_id": "task_proposal_test_001",
            "proposal_digest": proposal_digest,
            "repository_ref": str(self.project_root),
            "task_summary": "Implement the rendezvous endpoint",
            "boundary": "tools and tests only",
            "request_ref": request_ref,
            "scope": scope or {"operations": ["MODIFY"], "roots": ["tools", "tests"]},
            "source_ref": "git:" + "b" * 40,
            "created_at": "2026-08-03T00:00:00Z",
            "approval_required": True,
            "authority_created": False,
            "repository_write": False,
        }
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE proposal (
                    proposal_id TEXT PRIMARY KEY,
                    proposal_digest TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    approval_json TEXT,
                    cancellation_json TEXT,
                    completed_at TEXT
                );
                """
            )
            connection.execute(
                """
                INSERT INTO proposal(
                    proposal_id, proposal_digest, proposal_json, state,
                    created_at, approved_at, approval_json, cancellation_json,
                    completed_at
                ) VALUES (?, ?, ?, 'PROPOSED', ?, NULL, NULL, NULL, NULL)
                """,
                (
                    proposal["proposal_id"],
                    proposal_digest,
                    json.dumps(proposal, sort_keys=True, separators=(",", ":")),
                    proposal["created_at"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return proposal

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def build_release_fixture(self) -> tuple[Path, Path]:
        source = self.temp_root / "release-source"
        source.mkdir()

        def git(*args: str) -> str:
            completed = subprocess.run(
                ["git", "-C", str(source), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            return completed.stdout.strip()

        def write(relative: str, content: str) -> None:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        source_index_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "project_runtime_source_index.json"
        )
        distribution_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "project_runtime_distribution_manifest.json"
        )
        installer_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "project_runtime_installer.py"
        )
        catalog_path = (
            ".ai/distribution/context_management_runtime_pack/"
            "release_profile_catalog.json"
        )
        core_path = ".ai/core/CORE_SURFACE_REGISTRY.md"
        paths = [
            source_index_path,
            distribution_path,
            installer_path,
            core_path,
            catalog_path,
        ]
        write(
            source_index_path,
            json.dumps(
                {
                    "schema": "ai-career.project-runtime-source-index.v1",
                    "core_registry_path": core_path,
                    "installer_path": installer_path,
                    "package_manifest_path": distribution_path,
                    "release_profile_catalog_path": catalog_path,
                    "paths": paths,
                },
                sort_keys=True,
            ),
        )

        write(
            distribution_path,
            json.dumps(
                {
                    "schema": "ai-career.project-runtime-distribution.v1",
                    "package": {
                        "name": "fixture-runtime",
                        "source_index_path": source_index_path,
                    },
                },
                sort_keys=True,
            ),
        )
        write(installer_path, "raise RuntimeError('must not execute')\n")
        write(core_path, "# Fixture Core Registry\n")
        write(
            catalog_path,
            json.dumps(
                {
                    "schema": "ai-career.release-profile-catalog.v1",
                    "owner": "fixture/ai-career",
                    "load_profiles": [
                        {
                            "profile_id": "BOOT_CORE",
                            "description": "Boot control surfaces.",
                            "surfaces": [
                                {"path": core_path, "required": True},
                                {"path": installer_path, "required": False},
                            ],
                        }
                    ],
                    "skill_bindings": [{"skill_id": "boot", "profile_id": "BOOT_CORE"}],
                    "mode_profiles": [
                        {
                            "mode_profile_id": "MASTER_BASE",
                            "overlay_policy": "APPEND_ONLY",
                            "load_profiles": ["BOOT_CORE"],
                        }
                    ],
                },
                sort_keys=True,
            ),
        )
        git("init", "-q")
        git("config", "user.name", "Universe Tests")
        git("config", "user.email", "universe-tests@example.invalid")
        git("add", "--all")
        git("commit", "-q", "-m", "fixture")
        commit = git("rev-parse", "HEAD")
        database = self.temp_root / "fixture-release.sqlite3"
        manifest = self.temp_root / "fixture-release.manifest.json"
        build_release(
            source_repo=source,
            source_ref=commit,
            expected_commit=commit,
            source_repository="fixture/ai-career",
            database_path=database,
            manifest_path=manifest,
        )
        return database, manifest

    def test_legacy_governance_decision_schema_migrates_for_cancellation(self) -> None:
        database_path = self.temp_root / "legacy-governance.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE project_connection (
                    project_id TEXT PRIMARY KEY
                );
                CREATE TABLE governance_proposal_decision (
                    decision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL
                        REFERENCES project_connection(project_id)
                        ON DELETE CASCADE,
                    proposal_id TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision = 'APPROVE'),
                    source TEXT NOT NULL
                        CHECK(source IN ('BUTTON', 'NATURAL_LANGUAGE')),
                    commander_surface TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL
                        CHECK(state IN ('RECORDED', 'APPLIED', 'FAILED')),
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    UNIQUE(project_id, proposal_id),
                    UNIQUE(project_id, idempotency_key)
                );
                CREATE INDEX governance_proposal_decision_project_time
                ON governance_proposal_decision(project_id, created_at, decision_id);
                """
            )
            connection.commit()
        finally:
            connection.close()

        migrated = UniverseStore(database_path)
        with migrated._connection() as connection:
            decision_schema = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'governance_proposal_decision'"
                ).fetchone()["sql"]
            )
            index = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' "
                "AND name = 'governance_proposal_decision_project_time'"
            ).fetchone()
        self.assertIn("CANCEL", decision_schema.upper())
        self.assertIsNotNone(index)

    def project_seed(self, **overrides: object) -> JsonObject:
        value: JsonObject = {
            "seed_id": "gcs-seed-001",
            "source": {
                "ref": "local-git:GCS@1111111111111111111111111111111111111111",
                "commit": "1111111111111111111111111111111111111111",
            },
            "project": {
                "kind": "desktop-app",
                "technologies": ["python", "pyside6", "sqlite"],
                "goal": "stable broker-connected trading workstation",
            },
            "nodes": [
                {
                    "node_id": "broker-client",
                    "kind": "integration",
                    "title": "Broker Client",
                    "refs": [
                        {
                            "path": "src/broker.py",
                            "sha256": self.digest(self.broker_source),
                            "kind": "source",
                            "symbol": "BrokerClient",
                        }
                    ],
                },
                {
                    "node_id": "strategy-viewer",
                    "kind": "ui",
                    "title": "Strategy Viewer",
                    "refs": [
                        {
                            "path": "src/viewer.py",
                            "sha256": self.digest(self.viewer_source),
                            "kind": "source",
                            "symbol": "StrategyViewer",
                        }
                    ],
                },
            ],
            "edges": [],
            "documents": [
                {
                    "document_id": "architecture",
                    "path": "docs/architecture.md",
                    "sha256": self.digest(self.architecture_doc),
                    "role": "architecture",
                    "node_ids": ["broker-client", "strategy-viewer"],
                },
                {
                    "document_id": "broker-contract",
                    "path": "docs/broker-contract.md",
                    "sha256": self.digest(self.contract_doc),
                    "role": "contract",
                    "node_ids": ["broker-client"],
                },
                {
                    "document_id": "operations",
                    "path": "docs/operations.md",
                    "sha256": self.digest(self.orphan_doc),
                    "role": "reference",
                    "node_ids": [],
                },
            ],
        }
        value.update(overrides)
        return value

    def skill_observation_candidate(self, **overrides: object) -> JsonObject:
        value: JsonObject = {
            "candidate_id": "skill-observation-gcs-001",
            "candidate": {
                "schema": "ai-career.skill-observation-candidate.v1",
                "project_ref": "project://GCS",
                "task_frame_ref": "task-frame-gcs-001",
                "source_ref": "git:GCS@1111111111111111111111111111111111111111",
                "observations": [
                    {
                        "observation_digest": "a" * 64,
                        "skill_binding_digest": "b" * 64,
                        "skill": {
                            "skill_id": "source-review",
                            "skill_version": "1.0.0",
                            "operation_class": "READ",
                            "context_pack_digest": "c" * 64,
                        },
                        "model_ref": "provider://OPENAI/model/gpt-test",
                        "outcome": "SUCCEEDED",
                        "validation_state": "PASS",
                        "evidence_refs": ["receipt://gcs/test-001"],
                        "metrics": {
                            "duration_ms": 12,
                            "input_tokens": 42,
                            "output_tokens": 7,
                        },
                    }
                ],
                "observed_at": "2026-07-28T00:00:00Z",
                "target_ref": "universe://local",
                "redaction_state": "REDACTED",
            },
        }
        value.update(overrides)
        return value

    def skill_observation_publication_approval(
        self, prepared: JsonObject, **overrides: object
    ) -> JsonObject:
        candidate_digest = normalize_skill_observation_candidate(
            "GCS",
            {
                "candidate_id": prepared["candidate_id"],
                "candidate": prepared["candidate"],
            },
        )["candidate_digest"]
        value: JsonObject = {
            "schema": "universe.skill-observation-publication-approval.v1",
            "status": "APPROVED",
            "operation_class": "UNIVERSE_OBSERVATION_QUEUE",
            "project_ref": "project://GCS",
            "candidate_id": prepared["candidate_id"],
            "candidate_digest": candidate_digest,
            "selection_ref": "project-master-selection-gcs-001",
            "approver": "PROJECT_MASTER",
            "evidence_ref": "project-master://GCS/approval/gcs-001",
        }
        value.update(overrides)
        return value

    def test_runtime_host_uses_the_universe_database_for_failure_evidence(self) -> None:
        evidence_store = (
            self.server.runtime_host.worker_dispatcher.failure_evidence_store
        )
        self.assertIsNotNone(evidence_store)
        self.assertEqual(
            self.server.store.database_path,
            evidence_store.database_path,
        )

    def test_loopback_health_and_project_data_do_not_require_a_token(self) -> None:
        status, result = self.request("GET", "/health")
        self.assertEqual(200, status)
        self.assertEqual("READY", result["status"])
        self.assertEqual("Universe Server", result["program"]["name"])
        self.assertEqual("Python", result["program"]["runtime"])
        self.assertEqual(
            {
                "schema": "universe.mode-contract.v1",
                "status": "ACTIVE",
                "mode": "CONDUCTOR",
                "role": "CONDUCTOR",
                "scope": "project-network/navigation/distribution",
                "registry_revision": 3,
            },
            result["mode_contract"],
        )
        self.assertEqual(
            self.server.store.identity(),
            result["universe"],
        )
        self.assertEqual("LOCAL", result["connection"]["kind"])
        self.assertEqual("HTTP", result["connection"]["transport_kind"])
        self.assertEqual("NONE", result["connection"]["auth"]["type"])
        self.assertEqual("NONE", result["connection"]["auth"]["credential_ref"])
        self.assertTrue(result["connection"]["capabilities"]["realtime"])
        self.assertEqual("HTTP_API", result["interfaces"][0]["kind"])

        status, result = self.request("GET", "/v1/projects")
        self.assertEqual(200, status)
        self.assertEqual("PROJECTS_COLLECTED", result["status"])

        project, _ = self.server.store.register_project(self.registration())
        self.assertFalse(project["projection_available"])

    def test_universe_identity_is_durable_and_unique_per_database(self) -> None:
        identity = self.server.store.identity()
        self.assertEqual("universe.identity.v1", identity["schema"])
        uuid.UUID(identity["universe_id"])

        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(identity, reopened.identity())

        other = UniverseStore(self.temp_root / "other-universe.sqlite3")
        self.assertNotEqual(
            identity["universe_id"],
            other.identity()["universe_id"],
        )

    def test_todo_actions_are_idempotent_and_completion_requires_validation(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration())
        status, created = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Currentize from hook actions",
                "detail": "",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            },
        )
        self.assertEqual(201, status)
        todo_id = created["todo"]["todo_id"]

        started_action = {
            "action_id": "git-commit-" + "a" * 40,
            "outcome": "STARTED",
            "source": "GIT_TRACE2",
            "evidence_ref": "git://commit/" + "a" * 40,
        }
        status, raw_blocked = self.request(
            "POST", f"/v1/todos/{todo_id}/actions", started_action
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "TODO_ACTION_MUTATION_RECEIPT_REQUIRED", raw_blocked["error_code"]
        )

        started = self.server.store.apply_todo_action(todo_id, started_action)
        self.assertEqual("TODO_ACTION_APPLIED", started["status"])
        self.assertEqual("IN_PROGRESS", started["todo"]["state"])

        repeated = self.server.store.apply_todo_action(todo_id, started_action)
        self.assertEqual("TODO_ACTION_ALREADY_APPLIED", repeated["status"])

        completed_action = {
            "action_id": "validation-run-001",
            "outcome": "COMPLETED",
            "source": "TEST_HOOK",
            "evidence_ref": "test-run://validation-run-001",
        }
        with self.assertRaises(UniverseError) as blocked:
            self.server.store.apply_todo_action(todo_id, completed_action)
        self.assertEqual(
            "TODO_COMPLETION_VALIDATION_REQUIRED", blocked.exception.code
        )

        completed_action["validation"] = {
            "status": "PASSED",
            "evidence_ref": "test-run://validation-run-001/passed",
        }
        completed = self.server.store.apply_todo_action(todo_id, completed_action)
        self.assertEqual("DONE", completed["todo"]["state"])
        self.assertIn("result_fanout", completed)

        status, todos = self.request("GET", "/v1/todos")
        self.assertEqual(200, status)
        retained = next(item for item in todos["todos"] if item["todo_id"] == todo_id)
        self.assertEqual("DONE", retained["state"])

    def test_todo_mutation_gateway_is_identical_for_attached_and_standalone_sessions(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        attached, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "todo-attached-session",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "todo-attached-provider-ref",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        standalone, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "todo-standalone-session",
                "project_id": "standalone-project",
                "node": "standalone-project",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "todo-standalone-provider-ref",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        gateway = TodoMutationGateway(self.endpoint, self.token)
        todo = {
            "scope_kind": "PROJECT",
            "project_id": "GCS",
            "title": "Use one receipt path",
            "detail": "Mode and source_kind are not mutation authority.",
            "priority": "P1",
            "state": "READY",
            "source_kind": "CONDUCTOR",
            "sort_order": 0,
        }

        attached_result = gateway.create_todo(
            provider="CODEX",
            provider_session_ref="todo-attached-provider-ref",
            session_id=attached["session_id"],
            session_anchor_ref=attached["session_anchor_ref"],
            instruction_ref="conversation://test/attached-todo",
            todo=todo,
        )
        standalone_result = gateway.create_todo(
            provider="CLAUDE",
            provider_session_ref="todo-standalone-provider-ref",
            session_id=standalone["session_id"],
            session_anchor_ref=standalone["session_anchor_ref"],
            instruction_ref="conversation://test/standalone-todo",
            todo={**todo, "title": "Use one standalone receipt path"},
        )

        self.assertEqual("TODO_MUTATION_APPLIED", attached_result["status"])
        self.assertEqual("TODO_MUTATION_APPLIED", standalone_result["status"])
        self.assertEqual(
            "CONSUMED", attached_result["receipt"]["status"]
        )
        self.assertEqual(
            "CONSUMED", standalone_result["receipt"]["status"]
        )
        self.assertNotIn(
            "todo-attached-provider-ref", json.dumps(attached_result)
        )

        replay = gateway.create_todo(
            provider="CODEX",
            provider_session_ref="todo-attached-provider-ref",
            session_id=attached["session_id"],
            session_anchor_ref=attached["session_anchor_ref"],
            instruction_ref="conversation://test/attached-todo",
            todo=todo,
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            attached_result["todo"]["todo_id"], replay["todo"]["todo_id"]
        )
        with self.assertRaises(TodoMutationGatewayError) as caught:
            gateway.create_todo(
                provider="CODEX",
                provider_session_ref="todo-attached-provider-ref",
                session_id=attached["session_id"],
                session_anchor_ref=attached["session_anchor_ref"],
                instruction_ref="conversation://test/attached-todo",
                todo={**todo, "title": "Conflicting replay"},
            )
        self.assertEqual("TODO_MUTATION_RECEIPT_CONFLICT", caught.exception.code)

        started = gateway.apply_action(
            provider="CODEX",
            provider_session_ref="todo-attached-provider-ref",
            session_id=attached["session_id"],
            session_anchor_ref=attached["session_anchor_ref"],
            instruction_ref="conversation://test/attached-todo-start",
            todo_id=attached_result["todo"]["todo_id"],
            action={
                "action_id": "guarded-start-001",
                "outcome": "STARTED",
                "source": "CODEX_DESKTOP",
                "evidence_ref": "conversation://test/started-without-validation",
            },
        )
        self.assertEqual("TODO_ACTION_MUTATION_APPLIED", started["status"])
        self.assertEqual("IN_PROGRESS", started["todo"]["state"])

        completed_action = {
            "action_id": "guarded-completion-001",
            "outcome": "COMPLETED",
            "source": "CODEX_DESKTOP",
            "evidence_ref": "git://commit/" + "a" * 40,
            "validation": {
                "status": "PASSED",
                "evidence_ref": "test-run://guarded-completion/pass",
            },
        }
        action_coordinates = {
            "provider": "CODEX",
            "provider_session_ref": "todo-attached-provider-ref",
            "session_id": attached["session_id"],
            "session_anchor_ref": attached["session_anchor_ref"],
            "instruction_ref": "conversation://test/attached-todo-complete",
            "todo_id": attached_result["todo"]["todo_id"],
            "action": completed_action,
        }
        guarded_action = gateway.apply_action(**action_coordinates)
        self.assertEqual("TODO_ACTION_MUTATION_APPLIED", guarded_action["status"])
        self.assertEqual("CONSUMED", guarded_action["receipt"]["status"])
        self.assertEqual("DONE", guarded_action["todo"]["state"])
        self.assertFalse(guarded_action["replayed"])
        self.assertNotIn(
            "todo-attached-provider-ref", json.dumps(guarded_action)
        )

        guarded_replay = gateway.apply_action(**action_coordinates)
        self.assertTrue(guarded_replay["replayed"])
        self.assertEqual("DONE", guarded_replay["todo"]["state"])
        with self.assertRaises(TodoMutationGatewayError) as action_conflict:
            gateway.apply_action(
                **{
                    **action_coordinates,
                    "action": {
                        **completed_action,
                        "evidence_ref": "git://commit/" + "b" * 40,
                    },
                }
            )
        self.assertEqual(
            "TODO_ACTION_MUTATION_RECEIPT_CONFLICT", action_conflict.exception.code
        )

    def test_todo_mutation_accepts_exact_current_anchor_live_pty(self) -> None:
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "todo-current-pty-session",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "todo-current-pty-provider",
                "state": "LIVE",
                "currentness": "STALE",
            }
        )
        terminal = {
            "terminal_id": "term_todo_current_pty",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CLAUDE",
            "state": "LIVE",
            "supervisor_session_id": session["session_id"],
            "active_session_anchor_ref": session["session_anchor_ref"],
        }
        terminal_host = Mock()
        terminal_host.get.return_value = terminal
        self.server.terminal_host = terminal_host
        anchor_projection = {
            "sessions": [
                {
                    "mode": "MASTER",
                    "currentness": "CURRENT",
                    "observer_session_ref": "claude-code:todo-current-pty-provider",
                    "pty_binding": {
                        "status": "BOUND",
                        "terminal_id": terminal["terminal_id"],
                    },
                }
            ]
        }
        request = {
            "provider": "CLAUDE",
            "provider_session_ref": "todo-current-pty-provider",
            "session_id": session["session_id"],
            "session_anchor_ref": session["session_anchor_ref"],
        }

        with patch.object(
            self.server, "list_project_anchor_sessions", return_value=anchor_projection
        ):
            resolved = self.server.resolve_todo_mutation_session(request)
            self.assertEqual(session["session_id"], resolved["session_id"])

            terminal_host.get.return_value = {
                **terminal,
                "supervisor_session_id": "another-session",
            }
            with self.assertRaises(UniverseError) as caught:
                self.server.resolve_todo_mutation_session(request)
        self.assertEqual("TODO_MUTATION_SESSION_NOT_CURRENT", caught.exception.code)

    def test_todo_mutation_gateway_rejects_wrong_anchor_and_expired_receipt(self) -> None:
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "todo-guard-session",
                "project_id": "standalone-project",
                "node": "standalone-project",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "todo-guard-provider-ref",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        gateway = TodoMutationGateway(self.endpoint, self.token)
        todo = {
            "scope_kind": "UNIVERSE",
            "title": "Reject stale mutation",
            "detail": "",
            "priority": "P1",
            "state": "READY",
            "source_kind": "MASTER",
            "sort_order": 0,
        }
        wrong_anchor = gateway.mutation_request(
            provider="CODEX",
            provider_session_ref="todo-guard-provider-ref",
            session_id=session["session_id"],
            session_anchor_ref="session_anchor_wrong",
            instruction_ref="conversation://test/wrong-anchor",
            todo=todo,
        )
        with self.assertRaises(TodoMutationGatewayError) as caught:
            gateway.prepare(wrong_anchor)
        self.assertEqual("TODO_MUTATION_ANCHOR_MISMATCH", caught.exception.code)

        expiring = gateway.mutation_request(
            provider="CODEX",
            provider_session_ref="todo-guard-provider-ref",
            session_id=session["session_id"],
            session_anchor_ref=session["session_anchor_ref"],
            instruction_ref="conversation://test/expired-receipt",
            todo=todo,
            ttl_seconds=1,
        )
        prepared = gateway.prepare(expiring)
        receipt_id = prepared["receipt"]["receipt_id"]
        connection = sqlite3.connect(self.server.store.database_path)
        try:
            connection.execute(
                "UPDATE todo_mutation_receipt SET expires_at = ? WHERE receipt_id = ?",
                ("2000-01-01T00:00:00Z", receipt_id),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(TodoMutationGatewayError) as caught:
            gateway.consume(receipt_id, expiring)
        self.assertEqual("TODO_MUTATION_RECEIPT_EXPIRED", caught.exception.code)

    def test_todo_work_map_is_editable_prioritized_and_execution_neutral(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration())

        status, universe_result = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "UNIVERSE",
                "title": "Review nightly seed extraction",
                "detail": "Keep this as planning state until explicitly dispatched.",
                "priority": "P2",
                "state": "BACKLOG",
                "source_kind": "CONDUCTOR",
                "sort_order": 20,
            },
        )
        self.assertEqual(201, status)
        self.assertFalse(universe_result["task_frame_created"])
        self.assertFalse(universe_result["execution_assignment_created"])

        status, project_result = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Confirm broker contract",
                "detail": "",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 10,
            },
        )
        self.assertEqual(201, status)
        project_todo = project_result["todo"]
        self.assertEqual(1, project_todo["revision"])

        status, node_result = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "NODE",
                "project_id": "GCS",
                "node_ref": "risk-engine",
                "title": "Add boundary test",
                "detail": "Cover the rejected order path.",
                "priority": "P0",
                "state": "IN_PROGRESS",
                "source_kind": "MASTER",
                "sort_order": 0,
            },
        )
        self.assertEqual(201, status)
        self.assertEqual("NODE", node_result["todo"]["scope_kind"])

        status, list_result = self.request("GET", "/v1/todos")
        self.assertEqual(200, status)
        self.assertEqual("TODOS_COLLECTED", list_result["status"])
        self.assertEqual(
            [
                "Add boundary test",
                "Confirm broker contract",
                "Review nightly seed extraction",
            ],
            [item["title"] for item in list_result["todos"]],
        )
        self.assertFalse(list_result["task_frame_created"])
        self.assertFalse(list_result["execution_assignment_created"])

        update = {
            "scope_kind": project_todo["scope_kind"],
            "project_id": project_todo["project_id"],
            "title": "Confirm broker contract and examples",
            "detail": "Review the request and response examples.",
            "priority": "P0",
            "state": "IN_PROGRESS",
            "source_kind": project_todo["source_kind"],
            "sort_order": project_todo["sort_order"],
            "revision": project_todo["revision"],
        }
        status, update_result = self.request(
            "PATCH",
            f"/v1/todos/{project_todo['todo_id']}",
            update,
        )
        self.assertEqual(200, status)
        self.assertEqual(2, update_result["todo"]["revision"])
        self.assertEqual("P0", update_result["todo"]["priority"])
        self.assertFalse(update_result["task_frame_created"])

        status, conflict = self.request(
            "PATCH",
            f"/v1/todos/{project_todo['todo_id']}",
            update,
        )
        self.assertEqual(409, status)
        self.assertEqual("TODO_REVISION_CONFLICT", conflict["error_code"])

        status, delete_result = self.request(
            "DELETE",
            f"/v1/todos/{universe_result['todo']['todo_id']}",
        )
        self.assertEqual(200, status)
        self.assertTrue(delete_result["deleted"])
        self.assertFalse(delete_result["task_frame_created"])

        status, invalid = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "UNIVERSE",
                "project_id": "GCS",
                "title": "Invalid coordinate",
                "detail": "",
                "priority": "P3",
                "state": "BACKLOG",
                "source_kind": "USER",
                "sort_order": 0,
            },
        )
        self.assertEqual(400, status)
        self.assertEqual("TODO_SCOPE_COORDINATE_INVALID", invalid["error_code"])

    def test_project_goal_plan_is_hierarchical_and_execution_neutral(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration())
        status, goal_result = self.request(
            "POST",
            "/v1/projects/GCS/goals",
            {
                "title": "Ship the operator work spine",
                "description": "Make planning visible before execution.",
                "owner": "Project Master",
                "state": "DESIGNING",
                "sort_order": 0,
            },
        )
        self.assertEqual(201, status, goal_result)
        self.assertFalse(goal_result["task_frame_created"])
        self.assertFalse(goal_result["execution_assignment_created"])
        goal = goal_result["goal"]

        status, milestone_result = self.request(
            "POST",
            f"/v1/goals/{goal['goal_id']}/milestones",
            {
                "title": "Confirm the product flow",
                "description": "The project plan works on desktop and mobile.",
                "state": "PLANNED",
                "sort_order": 0,
            },
        )
        self.assertEqual(201, status)
        milestone = milestone_result["milestone"]

        status, todo_result = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "goal_id": goal["goal_id"],
                "milestone_id": milestone["milestone_id"],
                "title": "Verify the responsive hierarchy",
                "detail": "",
                "priority": "P0",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            },
        )
        self.assertEqual(201, status)

        status, plan = self.request("GET", "/v1/projects/GCS/goals")
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_GOAL_PLAN_COLLECTED", plan["status"])
        self.assertFalse(plan["task_frame_created"])
        self.assertEqual([], plan["unassigned_todos"])
        self.assertEqual(
            todo_result["todo"]["todo_id"],
            plan["goals"][0]["milestones"][0]["todos"][0]["todo_id"],
        )

        status, semantic = self.request("GET", "/v1/projects/GCS/semantic-graph")
        self.assertEqual(200, status)
        self.assertEqual("SEMANTIC_PROJECT_GRAPH_COLLECTED", semantic["status"])
        self.assertTrue(semantic["invariants"]["projection_only"])
        self.assertFalse(semantic["invariants"]["auto_promote"])
        node_types = {item["entity_type"] for item in semantic["nodes"]}
        self.assertTrue({"PROJECT", "GOAL", "MILESTONE", "TODO"}.issubset(node_types))
        edge_types = {item["edge_type"] for item in semantic["edges"]}
        self.assertTrue({"PROJECT_HAS_GOAL", "GOAL_HAS_MILESTONE", "MILESTONE_HAS_TODO"}.issubset(edge_types))

        stale = {
            "title": goal["title"],
            "description": goal["description"],
            "owner": goal["owner"],
            "state": "READY",
            "sort_order": goal["sort_order"],
            "revision": goal["revision"],
        }
        status, updated = self.request(
            "PATCH", f"/v1/goals/{goal['goal_id']}", stale
        )
        self.assertEqual(200, status)
        self.assertEqual(2, updated["goal"]["revision"])
        status, conflict = self.request(
            "PATCH", f"/v1/goals/{goal['goal_id']}", stale
        )
        self.assertEqual(409, status)
        self.assertEqual("GOAL_REVISION_CONFLICT", conflict["error_code"])

    def test_universe_goal_connects_project_goal_and_auto_prioritizes_global_work(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration())
        status, root_result = self.request(
            "POST",
            "/v1/universe-goals",
            {
                "title": "Keep the semantic work spine current",
                "description": "Connect global automation to project delivery.",
                "owner": "Universe Master",
                "state": "ACTIVE",
                "sort_order": 0,
            },
        )
        self.assertEqual(201, status, root_result)
        root = root_result["goal"]

        status, project_result = self.request(
            "POST",
            "/v1/projects/GCS/goals",
            {
                "title": "Apply the work spine to GCS",
                "description": "Project delivery stays connected to the global outcome.",
                "owner": "GCS Master",
                "state": "READY",
                "sort_order": 0,
                "universe_goal_id": root["universe_goal_id"],
            },
        )
        self.assertEqual(201, status, project_result)

        status, todo_result = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "UNIVERSE",
                "universe_goal_id": root["universe_goal_id"],
                "title": "Repair global runtime anchor failure",
                "detail": "Restore the runtime foundation before further automation.",
                "priority": "AUTO",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            },
        )
        self.assertEqual(201, status, todo_result)
        todo = todo_result["todo"]
        self.assertEqual("P0", todo["priority"])
        self.assertEqual("P0", todo["priority_recommendation"]["priority"])

        status, manually_ranked = self.request(
            "PATCH",
            f"/v1/todos/{todo['todo_id']}",
            {
                "scope_kind": todo["scope_kind"],
                "title": todo["title"],
                "detail": todo["detail"],
                "priority": "P3",
                "state": todo["state"],
                "source_kind": todo["source_kind"],
                "sort_order": todo["sort_order"],
                "universe_goal_id": todo["universe_goal_id"],
                "revision": todo["revision"],
            },
        )
        self.assertEqual(200, status, manually_ranked)
        self.assertEqual("P3", manually_ranked["todo"]["priority"])

        status, global_plan = self.request("GET", "/v1/universe-goals")
        self.assertEqual(200, status)
        self.assertEqual("UNIVERSE_GOAL_PLAN_COLLECTED", global_plan["status"])
        self.assertEqual(todo["todo_id"], global_plan["goals"][0]["todos"][0]["todo_id"])
        self.assertEqual(
            project_result["goal"]["goal_id"],
            global_plan["goals"][0]["project_goals"][0]["goal_id"],
        )

        status, semantic = self.request("GET", "/v1/projects/GCS/semantic-graph")
        self.assertEqual(200, status)
        node_types = {item["entity_type"] for item in semantic["nodes"]}
        self.assertTrue({"UNIVERSE", "UNIVERSE_GOAL"}.issubset(node_types))
        edge_types = {item["edge_type"] for item in semantic["edges"]}
        self.assertTrue(
            {
                "UNIVERSE_HAS_GOAL",
                "UNIVERSE_GOAL_HAS_PROJECT_GOAL",
                "UNIVERSE_GOAL_HAS_TODO",
            }.issubset(edge_types)
        )

    def test_semantic_project_graph_projects_room_anchor_bindings(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        room = self.server.multi_rooms.ensure_project_room("GCS")
        attached = self.server.multi_rooms.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-vendor-thread-graph",
                "supervisor_session_id": "session-graph-master",
                "session_anchor_ref": "session_anchor_graph_master",
                "display_name": "GCS Master",
                "participant_state": "ATTACHED",
            },
        )
        binding = attached["binding"]

        status, semantic = self.request(
            "GET", "/v1/projects/GCS/semantic-graph", None, self.token
        )

        self.assertEqual(HTTPStatus.OK, status)
        node_ids = {item["id"] for item in semantic["nodes"]}
        self.assertTrue(
            {
                f"chat_room:{room['room_id']}",
                f"room_binding:{binding['binding_id']}",
                "session_anchor:session_anchor_graph_master",
            }.issubset(node_ids)
        )
        edge_types = {item["edge_type"] for item in semantic["edges"]}
        self.assertTrue(
            {
                "PROJECT_HAS_CHAT_ROOM",
                "CHAT_ROOM_HAS_BINDING",
                "ROOM_BINDING_REFS_SESSION_ANCHOR",
            }.issubset(edge_types)
        )

    def test_semantic_project_graph_collects_redacted_multi_room_messages_and_results(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        room = self.server.multi_rooms.create_boss_room(
            project_id="GCS",
            task_frame_id="tf-room-graph-001",
        )
        report = self.server.multi_rooms.worker_report(
            room["room_id"],
            {
                "body_text": "Do not expose this worker report in the graph.",
                "severity": "BLOCKER",
                "idempotency_key": "room-graph-worker-report-001",
            },
        )

        graph = self.server.store.semantic_project_graph("GCS")
        message = report["message"]
        message_nodes = [
            item for item in graph["nodes"]
            if item["entity_type"] == "CHAT_ROOM_MESSAGE"
            and item["data"].get("message_id") == message["message_id"]
        ]
        result_nodes = [
            item for item in graph["nodes"]
            if item["entity_type"] == "ROOM_RESULT"
            and item["data"].get("event_id") == report["event"]["event_id"]
        ]
        self.assertEqual(1, len(message_nodes))
        self.assertEqual(1, len(result_nodes))
        self.assertTrue(message_nodes[0]["data"]["body_in_graph"] is False)
        self.assertNotIn("body_text", message_nodes[0]["data"])
        self.assertNotIn("Do not expose", json.dumps(graph))
        self.assertEqual("BLOCKER", result_nodes[0]["data"]["severity"])
        edge_types = {item["edge_type"] for item in graph["edges"]}
        self.assertIn("CHAT_ROOM_HAS_MESSAGE", edge_types)
        self.assertIn("CHAT_ROOM_HAS_RESULT", edge_types)
        self.assertIn("ROOM_RESULT_REFS_MESSAGE", edge_types)

    def test_multi_room_writes_advance_redacted_semantic_collection_cursors(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        room = self.server.multi_rooms.create_boss_room(
            project_id="GCS",
            task_frame_id="tf-room-cursor-001",
        )

        status, recorded = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/worker-report",
            {
                "body_text": "Sensitive implementation detail remains outside cursor storage.",
                "severity": "BLOCKER",
                "idempotency_key": "room-cursor-worker-report-001",
            },
            self.token,
        )

        self.assertEqual(HTTPStatus.CREATED, status)
        cursors = {
            item["source_kind"]: item
            for item in self.server.store.list_semantic_collection_cursors("GCS")
        }
        message_cursor = cursors["MULTI_ROOM_MESSAGE"]
        result_cursor = cursors["MULTI_ROOM_RESULT"]
        self.assertEqual(recorded["message"]["message_id"], message_cursor["last_event_id"])
        self.assertEqual(recorded["event"]["event_id"], result_cursor["last_event_id"])
        self.assertNotIn("Sensitive implementation detail", json.dumps(cursors))

    def test_semantic_graph_extracts_only_explicit_typed_multi_room_messages(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        room = self.server.multi_rooms.create_room(
            room_type="MEETING",
            title="Typed extraction",
            host_role="CONDUCTOR",
            project_id="GCS",
        )
        for kind in (
            "MESSAGE",
            "DECISION",
            "TASK_DRAFT",
            "DOCUMENT_DRAFT",
            "FAILURE",
            "BENCH_OBSERVATION",
        ):
            self.server.multi_rooms.post_message(
                room["room_id"],
                {
                    "author_role": "USER",
                    "kind": kind,
                    "body_text": f"private {kind} source text",
                    "idempotency_key": f"typed-room-{kind}",
                },
            )

        graph = self.server.store.semantic_project_graph("GCS")
        typed = {
            item["entity_type"]: item
            for item in graph["nodes"]
            if item["provenance"]["source_kind"] == "MULTI_ROOM_EXTRACTION"
        }
        self.assertEqual(
            {"ROOM_DECISION", "TODO_CANDIDATE", "DOCUMENT_CANDIDATE", "FAILURE_CANDIDATE", "BENCH_OBSERVATION"},
            set(typed),
        )
        self.assertEqual(
            "USER_SELECTION_REQUIRED", typed["TODO_CANDIDATE"]["data"]["promotion_state"]
        )
        self.assertEqual(
            "AUTO_OBSERVED", typed["BENCH_OBSERVATION"]["lifecycle_state"]
        )
        self.assertNotIn("private MESSAGE source text", json.dumps(graph))
        edge_types = {item["edge_type"] for item in graph["edges"]}
        self.assertNotIn("ROOM_MESSAGE_DERIVES_MESSAGE", edge_types)
        self.assertIn("ROOM_MESSAGE_DERIVES_DECISION", edge_types)
        self.assertIn("ROOM_MESSAGE_DERIVES_DOCUMENT_CANDIDATE", edge_types)

    def test_semantic_graph_projects_room_findings_without_private_detail(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        room = self.server.multi_rooms.create_room(
            room_type="MEETING",
            title="Finding graph",
            host_role="CONDUCTOR",
            project_id="GCS",
        )
        source = self.server.multi_rooms.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "source message"},
        )
        finding = self.server.multi_rooms.record_finding(
            room["room_id"],
            {
                "finding_type": "ESCALATION_REQUEST",
                "summary": "Master review required",
                "detail_text": "private escalation detail",
                "author_role": "USER",
                "evidence_refs": ["universe://evidence/escalation"],
                "feature_refs": ["feature://meeting-room"],
                "requested_owner_role": "MASTER",
                "source_message_id": source["message_id"],
            },
        )

        graph = self.server.store.semantic_project_graph("GCS")
        nodes = [
            node for node in graph["nodes"]
            if node["entity_type"] == "ROOM_ESCALATION_REQUEST"
            and node["data"].get("finding_id") == finding["finding_id"]
        ]
        self.assertEqual(1, len(nodes))
        self.assertEqual("OWNER_ACTION_REQUIRED", nodes[0]["data"]["resolution_state"])
        self.assertTrue(nodes[0]["data"]["detail_in_graph"] is False)
        self.assertNotIn("private escalation detail", json.dumps(graph))
        edge_types = {edge["edge_type"] for edge in graph["edges"]}
        self.assertIn("CHAT_ROOM_HAS_FINDING", edge_types)
        self.assertIn("ROOM_FINDING_REFS_MESSAGE", edge_types)
        self.assertIn("ROOM_FINDING_REFS_FEATURE", edge_types)

    def test_semantic_graph_projects_room_artifact_revisions_without_body(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        room = self.server.multi_rooms.create_room(
            room_type="MEETING",
            title="Artifact graph",
            host_role="CONDUCTOR",
            project_id="GCS",
        )
        source = self.server.multi_rooms.post_message(
            room["room_id"],
            {"author_role": "USER", "body_text": "private artifact source"},
        )
        artifact = self.server.multi_rooms.create_artifact(
            room["room_id"],
            {
                "artifact_type": "COMPARISON",
                "title": "Two paths",
                "body_text": "private comparison body",
                "author_role": "USER",
                "evidence_refs": ["universe://evidence/comparison"],
                "source_message_id": source["message_id"],
            },
        )
        self.server.multi_rooms.revise_artifact(
            room["room_id"],
            artifact["artifact_id"],
            {
                "expected_revision": 1,
                "body_text": "private revised body",
                "author_role": "CONDUCTOR",
            },
        )

        graph = self.server.store.semantic_project_graph("GCS")
        artifact_nodes = [
            node for node in graph["nodes"]
            if node["entity_type"] == "ROOM_ARTIFACT"
            and node["data"].get("artifact_id") == artifact["artifact_id"]
        ]
        revision_nodes = [
            node for node in graph["nodes"]
            if node["entity_type"] == "ROOM_ARTIFACT_REVISION"
            and node["data"].get("artifact_id") == artifact["artifact_id"]
        ]
        self.assertEqual(1, len(artifact_nodes))
        self.assertEqual(2, len(revision_nodes))
        self.assertEqual(
            "USER_SELECTION_REQUIRED",
            artifact_nodes[0]["data"]["promotion_state"],
        )
        self.assertNotIn("private comparison body", json.dumps(graph))
        self.assertNotIn("private revised body", json.dumps(graph))
        edge_types = {edge["edge_type"] for edge in graph["edges"]}
        self.assertIn("CHAT_ROOM_HAS_ARTIFACT", edge_types)
        self.assertIn("ROOM_ARTIFACT_HAS_REVISION", edge_types)
        self.assertIn("ROOM_ARTIFACT_REVISION_REFS_MESSAGE", edge_types)

    def test_multi_room_rejects_unknown_explicit_message_kind(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        room = self.server.multi_rooms.create_room(
            room_type="MEETING",
            title="Typed validation",
            host_role="CONDUCTOR",
            project_id="GCS",
        )
        with self.assertRaisesRegex(ValueError, "unsupported message_kind"):
            self.server.multi_rooms.post_message(
                room["room_id"],
                {"author_role": "USER", "kind": "AUTO_TODO", "body_text": "no"},
            )

    def test_semantic_project_graph_projects_current_functional_seed_nodes(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, seed_result = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(HTTPStatus.CREATED, status)

        status, semantic = self.request(
            "GET", "/v1/projects/GCS/semantic-graph", None, self.token
        )

        self.assertEqual(HTTPStatus.OK, status)
        functional = {
            item["data"]["node_id"]: item
            for item in semantic["nodes"]
            if item["entity_type"] == "FUNCTIONAL_NODE"
        }
        self.assertEqual(
            {"broker-client", "strategy-viewer"}, set(functional)
        )
        self.assertTrue(
            all(
                item["provenance"]["source_ref"].endswith(seed_result["seed"]["seed_id"])
                for item in functional.values()
            )
        )
        self.assertIn(
            "PROJECT_HAS_FUNCTIONAL_NODE",
            {item["edge_type"] for item in semantic["edges"]},
        )

    def test_semantic_project_graph_projects_session_anchor_lineage(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "semantic-graph-session-001",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-vendor-semantic-graph-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        mode_anchor = self.server.session_supervisor.get_project_mode_anchor(
            "GCS", "MASTER"
        )

        status, semantic = self.request(
            "GET", "/v1/projects/GCS/semantic-graph", None, self.token
        )

        self.assertEqual(HTTPStatus.OK, status)
        node_ids = {item["id"] for item in semantic["nodes"]}
        self.assertIn("session:semantic-graph-session-001", node_ids)
        self.assertIn(
            f"session_anchor:{session['session_anchor_ref']}", node_ids
        )
        self.assertIn(f"mode_anchor:{mode_anchor['anchor_ref']}", node_ids)
        edge_types = {item["edge_type"] for item in semantic["edges"]}
        self.assertTrue(
            {
                "PROJECT_HAS_SESSION",
                "SESSION_OWNS_ANCHOR",
                "PROJECT_HAS_MODE_ANCHOR",
                "MODE_ANCHOR_REFS_SESSION_ANCHOR",
            }.issubset(edge_types)
        )

    def test_project_master_git_trace_status_becomes_redacted_graph_input(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.session_supervisor.register_session(
            {
                "session_id": "git-trace-semantic-session",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-app-server:vendor-secret-ref",
            }
        )
        completion = {
            "status": "COMPLETED",
            "project_id": "GCS",
            "message_id": "master-message-git-001",
            "provider_session_ref": "codex-app-server:vendor-secret-ref",
            "work_statuses": [
                {
                    "schema": "universe.git-trace2-work-status.v1",
                    "source": "GIT_TRACE2",
                    "operation": "PUSH",
                    "state": "COMPLETED",
                    "exit_code": 0,
                    "commit_sha": "b" * 40,
                    "commit_message": "must be ignored",
                    "changed_files": ["must-not-appear.txt"],
                }
            ],
        }

        self.server._observe_project_master_completion(completion)
        self.server._observe_project_master_completion(completion)

        events = self.server.store.list_events("GCS")
        git_events = [item for item in events if item["event_type"] == "GIT_WORK_STATUS"]
        self.assertEqual(1, len(git_events))
        payload = git_events[0]["payload"]
        self.assertEqual("REDACTED", payload["redaction_state"])
        self.assertEqual("PUSH", payload["operation"])
        self.assertNotIn("commit_message", payload)
        self.assertNotIn("changed_files", payload)
        self.assertNotEqual(
            completion["provider_session_ref"], payload["provider_session_digest"]
        )
        status, semantic = self.request(
            "GET", "/v1/projects/GCS/semantic-graph", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertIn(
            f"git_milestone:{git_events[0]['event_id']}",
            {item["id"] for item in semantic["nodes"]},
        )
        self.assertIn(
            "GIT_MILESTONE_FROM_SESSION",
            {item["edge_type"] for item in semantic["edges"]},
        )

    def test_todo_cannot_bind_to_a_goal_from_another_project(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration())
        _, goal_result = self.request(
            "POST",
            "/v1/projects/GCS/goals",
            {
                "title": "Foreign goal",
                "description": "",
                "owner": "Project Master",
                "state": "DESIGNING",
                "sort_order": 0,
            },
        )
        connection = sqlite3.connect(self.server.store.database_path)
        try:
            connection.execute(
                "UPDATE project_goal SET project_id = 'OTHER' WHERE goal_id = ?",
                (goal_result["goal"]["goal_id"],),
            )
            connection.commit()
        finally:
            connection.close()
        status, result = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "goal_id": goal_result["goal"]["goal_id"],
                "title": "Invalid cross-project binding",
                "detail": "",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            },
        )
        self.assertEqual(400, status)
        self.assertEqual("TODO_PLAN_COORDINATE_INVALID", result["error_code"])

    def test_project_integration_catalog_is_read_only_and_digest_bound(self) -> None:
        status, result = self.request("GET", "/v1/project-templates")

        self.assertEqual(200, status)
        self.assertEqual("universe.project-integration-catalog.v1", result["schema"])
        self.assertEqual("PROJECT_INTEGRATION_CATALOG_READY", result["status"])
        self.assertEqual("LOCAL_ONLY", result["project_binding"]["workspace_tracking"])
        self.assertEqual("NONE", result["effects"]["project_source_write"])
        self.assertEqual(5, len(result["templates"]))

    def test_project_integration_proposal_is_project_bound_and_non_executing(
        self,
    ) -> None:
        self.server.store.register_project(self.registration())

        status, result = self.request(
            "GET", "/v1/projects/GCS/integration-template-proposal"
        )

        self.assertEqual(200, status)
        self.assertEqual("GCS", result["project_id"])
        self.assertEqual("PROPOSED", result["effects"]["project_source_write"])
        self.assertEqual("NOT_STARTED", result["apply_contract"]["execution"])
        self.assertEqual(5, len(result["assets"]))

    def test_provider_session_observer_api_projects_redacted_activity(self) -> None:
        rollout = self.temp_root / "rollout-observer.jsonl"
        rollout.write_text(
            json.dumps({"type": "tool_call", "command": "private command"})
            + "\n"
            + json.dumps({"type": "turn_completed", "text": "private completion"})
            + "\n",
            encoding="utf-8",
        )
        self.server.store.register_project(self.registration())
        status, registered = self.request(
            "POST",
            "/v1/session-observer/sources",
            {
                "provider": "CODEX",
                "provider_session_id": "codex-observer-1",
                "source_path": str(rollout),
                "source_kind": "CODEX_ROLLOUT_JSONL",
                "source_version": "v1",
            },
        )
        self.assertEqual(201, status)
        source_id = registered["source"]["source_id"]
        status, scanned = self.request(
            "POST", f"/v1/session-observer/sources/{source_id}/scan", {}
        )
        self.assertEqual(200, status)
        self.assertEqual(2, scanned["added"])

        status, activities = self.request(
            "GET", f"/v1/session-observer/activities?source_id={source_id}"
        )
        self.assertEqual(200, status)
        self.assertEqual(
            {"TOOL_PHASE", "TURN_COMPLETED"},
            {item["event_kind"] for item in activities["activities"]},
        )
        self.assertNotIn("private command", json.dumps(activities))
        self.assertNotIn("private completion", json.dumps(activities))
        public_activity_keys = {
            "schema",
            "activity_id",
            "source_id",
            "ordinal",
            "event_kind",
            "activity_state",
            "observed_at",
            "activity_digest",
            "active",
            "recorded_at",
        }
        self.assertTrue(
            all(
                set(activity) <= public_activity_keys
                for activity in activities["activities"]
            )
        )
        self.assertNotIn("provider_event_id", json.dumps(activities))
        self.assertNotIn("branch_parent_id", json.dumps(activities))
        status, batch = self.request(
            "GET", f"/v1/session-observer/sources/{source_id}/batch-candidate"
        )
        self.assertEqual(200, status)
        self.assertEqual("REVIEW_REQUIRED", batch["candidate"]["status"])
        self.assertEqual(
            "REDACTED", batch["candidate"]["source"]["provider_session_id"]
        )
        status, recorded = self.request(
            "POST",
            f"/v1/session-observer/sources/{source_id}/record-memory",
            {"project_id": "GCS"},
        )
        self.assertEqual(201, status)
        self.assertEqual("PROVIDER_ACTIVITY_MEMORY_RECORDED", recorded["status"])
        memory = recorded["memory"]
        self.assertEqual("OBSERVED", memory["state"])
        self.assertEqual("UNLINKED", memory["link_state"])
        self.assertNotIn("private", json.dumps(memory))
        status, duplicate = self.request(
            "POST",
            f"/v1/session-observer/sources/{source_id}/record-memory",
            {"project_id": "GCS"},
        )
        self.assertEqual(200, status)
        self.assertEqual(
            "PROVIDER_ACTIVITY_MEMORY_ALREADY_RECORDED", duplicate["status"]
        )
        self.assertEqual(memory["memory_id"], duplicate["memory"]["memory_id"])
        status, public_sources = self.request("GET", "/v1/session-observer/sources")
        self.assertEqual(200, status)
        self.assertEqual("REDACTED", public_sources["sources"][0]["source_path"])
        self.assertEqual(
            "REDACTED", public_sources["sources"][0]["provider_session_id"]
        )
        self.assertIsNone(public_sources["sources"][0]["file_identity"])
        self.assertTrue(
            public_sources["sources"][0]["source_key"].startswith("provider_source_")
        )
        status, remote_sources = self.request(
            "GET",
            "/v1/session-observer/sources",
            extra_headers={"X-Universe-Access-Surface": "REMOTE_BROWSER"},
        )
        self.assertEqual(200, status)
        self.assertEqual("REDACTED", remote_sources["sources"][0]["source_path"])
        self.assertEqual(
            public_sources["sources"][0]["source_key"],
            remote_sources["sources"][0]["source_key"],
        )
        status, denied = self.request(
            "POST",
            "/v1/session-observer/sources",
            {
                "provider": "CODEX",
                "provider_session_id": "codex-remote-denied",
                "source_path": str(rollout),
                "source_kind": "CODEX_ROLLOUT_JSONL",
                "source_version": "v1",
            },
            extra_headers={"X-Universe-Access-Surface": "REMOTE_BROWSER"},
        )
        self.assertEqual(403, status)
        self.assertEqual("LOCAL_OPERATOR_REQUIRED", denied["error_code"])
        status, remote_record = self.request(
            "POST",
            f"/v1/session-observer/sources/{source_id}/record-memory",
            {"project_id": "GCS"},
            extra_headers={"X-Universe-Access-Surface": "REMOTE_BROWSER"},
        )
        self.assertEqual(403, status)
        self.assertEqual("LOCAL_OPERATOR_REQUIRED", remote_record["error_code"])

        discovered_source = {
            "schema": "universe.provider-session-source.v1",
            "status": "DISCOVERED",
            "provider": "CODEX",
            "provider_session_id": "rollout-20260808",
            "source_path": str(rollout),
            "source_kind": "CODEX_ROLLOUT_JSONL",
            "source_version": "v1",
            "last_modified_at": "2026-08-08T00:00:00Z",
        }
        with patch.object(
            self.server.store,
            "discover_provider_session_sources",
            return_value=[discovered_source],
        ) as discover:
            status, discovered = self.request(
                "GET", "/v1/session-observer/discover?provider=CODEX"
            )
            self.assertEqual(200, status)
            self.assertEqual(
                "PROVIDER_SESSION_SOURCES_DISCOVERED", discovered["status"]
            )
            self.assertEqual("REDACTED", discovered["sources"][0]["source_path"])
            self.assertEqual(
                "REDACTED", discovered["sources"][0]["provider_session_id"]
            )
            self.assertNotIn(str(rollout), json.dumps(discovered))
            discover.assert_called_once_with("CODEX")
        status, remote_discovery = self.request(
            "GET",
            "/v1/session-observer/discover?provider=CODEX",
            extra_headers={"X-Universe-Access-Surface": "REMOTE_BROWSER"},
        )
        self.assertEqual(403, status)
        self.assertEqual("LOCAL_OPERATOR_REQUIRED", remote_discovery["error_code"])

        maintenance = self.server.run_supervisor_maintenance_once(idle_seconds=0)
        self.assertIn("provider_activity", maintenance)
        self.assertEqual(1, len(maintenance["provider_activity"]))
        with patch.object(
            self.server,
            "provider_chat_catalog",
            return_value={"rooms": [], "transcript_content": "EXCLUDED"},
        ):
            status, tail = self.request(
                "POST", "/v1/session-observer/tail", {}
            )
        self.assertEqual(200, status)
        self.assertEqual("PROVIDER_ACTIVITY_TAIL_UPDATED", tail["status"])
        self.assertNotIn("source_path", json.dumps(tail))
        self.assertNotIn("provider_session_id", json.dumps(tail))

    def test_provider_chat_catalog_lists_all_vendors_and_joins_optional_binding(
        self,
    ) -> None:
        status, _ = self.request(
            "POST",
            "/v1/supervisor/sessions",
            {
                "session_id": "session-gcs-master",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-chat-001",
                "anchor_ref": "MASTER-CURRENT-GCS-001",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.server.session_supervisor.set_default(
            "session-gcs-master",
            expected_pointer_version=0,
        )

        def discovered(provider: str) -> list[dict[str, Any]]:
            session_id = {
                "CODEX": "codex-chat-001",
                "CLAUDE": "claude-chat-001",
                "GROK": "grok-chat-001",
            }[provider]
            return [
                {
                    "schema": "universe.provider-session-source.v1",
                    "status": "DISCOVERED",
                    "provider": provider,
                    "provider_session_id": session_id,
                    "source_path": str(self.temp_root / provider / "chat.jsonl"),
                    "source_kind": f"{provider}_SESSION_JSONL",
                    "source_version": "v1",
                    "last_modified_at": (
                        "2026-08-09T00:01:00Z"
                        if provider == "CLAUDE"
                        else "2026-08-09T00:00:00Z"
                    ),
                    "workspace": f"C:\\workspace\\{provider.lower()}",
                    "workspace_name": provider.lower(),
                    "display_name": f"{provider.title()} chat",
                    "session_kind": "CHAT",
                    "parent_provider_session_id": None,
                    "identity_state": "VERIFIED",
                    "transcript_content": "EXCLUDED",
                }
            ]

        anchor_observations = [
            {
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "anchor_ref": "MASTER-CURRENT-GCS-001",
                "temporality": "CURRENT",
                "observed_at": "2026-08-09T00:00:00Z",
                "provider": "CODEX",
                "provider_session_ref": "codex-chat-001",
                "evidence_ref": "universe://projects/GCS/anchor-store/MASTER-CURRENT-GCS-001",
            }
        ]
        with (
            patch.object(
                self.server.store,
                "discover_provider_session_sources",
                side_effect=discovered,
            ),
            patch.object(
                self.server,
                "_project_anchor_observations",
                return_value=anchor_observations,
            ),
            patch.object(
                self.server,
                "_project_anchor_supervisor_projection",
                return_value={"status": "PROJECT_ANCHORS_PROJECTED"},
            ),
            patch.object(
                self.server.store,
                "list_provider_session_sources",
                return_value=[
                    {
                        "source_id": "source-codex-chat-001",
                        "provider": "CODEX",
                        "provider_session_id": "codex-chat-001",
                        "source_path": str(
                            self.temp_root / "CODEX" / "chat.jsonl"
                        ),
                    }
                ],
            ),
            patch.object(
                self.server.store,
                "list_provider_session_activities",
                return_value=[
                    {
                        "activity_id": "old-activity",
                        "activity_state": "WAITING",
                        "observed_at": "2026-08-08T00:00:00Z",
                    }
                ],
            ),
        ):
            status, catalog = self.request("GET", "/v1/session-observer/chat-rooms")
            repeated_status, repeated_catalog = self.request(
                "GET", "/v1/session-observer/chat-rooms"
            )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(HTTPStatus.OK, repeated_status)
        self.assertEqual(
            {"CODEX", "CLAUDE", "GROK"},
            {room["provider"] for room in catalog["rooms"]},
        )
        codex = next(room for room in catalog["rooms"] if room["provider"] == "CODEX")
        self.assertEqual("ANCHOR_OBSERVED", codex["binding"]["state"])
        self.assertEqual("CURRENT", codex["binding"]["observer_currentness"])
        self.assertEqual("2026-08-09T00:00:00Z", codex["last_activity_at"])
        self.assertEqual("GCS MASTER", codex["binding"]["alias"])
        self.assertEqual("GCS", codex["binding"]["current_project_id"])
        self.assertEqual("GCS", codex["binding"]["node"])
        self.assertEqual(
            "MASTER-CURRENT-GCS-001", codex["binding"]["current_anchor_ref"]
        )
        claude = next(room for room in catalog["rooms"] if room["provider"] == "CLAUDE")
        self.assertEqual("UNBOUND", claude["binding"]["state"])
        rendered = json.dumps(catalog)
        self.assertNotIn("provider_session_id", rendered)
        self.assertNotIn("source_path", rendered)
        self.assertNotIn('"workspace":', rendered)
        self.assertNotIn("legacy_refs", rendered)
        self.assertNotIn("provider_session_ref", rendered)
        self.assertNotIn("transport_state", rendered)
        self.assertEqual("EXCLUDED", catalog["transcript_content"])
        self.assertEqual(
            [room["chat_key"] for room in catalog["rooms"]],
            [room["chat_key"] for room in repeated_catalog["rooms"]],
        )

    def test_provider_chat_catalog_binds_current_default_without_anchor_observation(
        self,
    ) -> None:
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-universe-master-current",
                "node": "universe",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-current-master",
                "anchor_ref": "MASTER-CURRENT-LIVE",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        self.server.session_supervisor.set_default(
            session["session_id"],
            expected_pointer_version=session["default_pointer_version"],
        )

        def discovered(provider: str) -> list[dict[str, Any]]:
            if provider != "CODEX":
                return []
            return [
                {
                    "schema": "universe.provider-session-source.v1",
                    "status": "DISCOVERED",
                    "provider": "CODEX",
                    "provider_session_id": "codex-current-master",
                    "source_path": str(self.temp_root / "CODEX" / "current.jsonl"),
                    "source_kind": "CODEX_ROLLOUT_JSONL",
                    "source_version": "v1",
                    "last_modified_at": "2026-08-11T15:24:04Z",
                    "workspace": r"C:\workspace\universe",
                    "workspace_name": "universe",
                    "display_name": "Codex session",
                    "session_kind": "CHAT",
                    "identity_state": "VERIFIED",
                    "transcript_content": "EXCLUDED",
                }
            ]

        with (
            patch.object(
                self.server.store,
                "discover_provider_session_sources",
                side_effect=discovered,
            ),
            patch.object(
                self.server,
                "_project_anchor_observations",
                return_value=[],
            ),
        ):
            catalog = self.server.provider_chat_catalog()

        self.assertEqual(1, len(catalog["rooms"]))
        room = catalog["rooms"][0]
        self.assertEqual("CODEX", room["provider"])
        self.assertEqual("INDEPENDENT", room["binding"]["state"])
        self.assertEqual("WORKSPACE_OBSERVED", room["binding"].get("currentness_source"))
        self.assertEqual("universe", room["binding"].get("current_project_id"))
        self.assertEqual(
            None, room["binding"].get("current_anchor_ref")
        )
        self.assertEqual(
            None,
            room["binding"].get("universe_session_id"),
        )

    def test_provider_chat_catalog_projects_supervisor_session_without_source_file(
        self,
    ) -> None:
        self.server.store.register_project(self.registration())
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-gcs-codex-app-server",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-app-server:current",
                "anchor_ref": "MASTER-CURRENT-GCS-APP",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
                "activity_state": "IDLE",
                "last_seen_at": "2026-08-13T00:00:00Z",
                "alias": "GCS MASTER",
            }
        )
        self.server.session_supervisor.set_default(
            session["session_id"],
            expected_pointer_version=session["default_pointer_version"],
        )

        with (
            patch.object(
                self.server.store,
                "discover_provider_session_sources",
                return_value=[],
            ),
            patch.object(
                self.server,
                "_project_anchor_observations",
                return_value=[],
            ),
        ):
            catalog = self.server.provider_chat_catalog()
            self.assertEqual(1, len(catalog["rooms"]))
            room = catalog["rooms"][0]
            descriptor = self.server.resolve_provider_chat_session(
                room["chat_key"]
            )

        self.assertEqual("CODEX", room["provider"])
        self.assertIsNone(room["source_id"])
        self.assertEqual("CHAT", room["session_kind"])
        self.assertEqual("SUPERVISOR_OBSERVED", room["identity_state"])
        self.assertEqual("BOUND", room["binding"]["state"])
        self.assertEqual("GCS", room["binding"]["current_project_id"])
        self.assertEqual("UNKNOWN", room["binding"]["observer_currentness"])
        self.assertEqual("UNKNOWN", room["binding"]["currentness_source"])
        self.assertEqual(
            "MASTER-CURRENT-GCS-APP",
            room["binding"]["current_anchor_ref"],
        )
        self.assertEqual(
            "session-gcs-codex-app-server",
            room["binding"]["universe_session_id"],
        )
        self.assertEqual(
            "codex-app-server:current",
            descriptor["provider_session_ref"],
        )
        self.assertEqual("VERIFIED", descriptor["identity_state"])
        self.assertEqual("SESSION_SUPERVISOR", descriptor["identity_source"])
        self.assertEqual(str(self.project_root), descriptor["repository_root"])
        rendered = json.dumps(catalog)
        self.assertNotIn("codex-app-server:current", rendered)
        self.assertNotIn("provider_session_ref", rendered)

    def test_provider_chat_catalog_deduplicates_rotated_chat_files_but_keeps_workers(
        self,
    ) -> None:
        def discovered(provider: str) -> list[dict[str, Any]]:
            if provider != "CLAUDE":
                return []
            base = {
                "schema": "universe.provider-session-source.v1",
                "status": "DISCOVERED",
                "provider": "CLAUDE",
                "provider_session_id": "claude-chat-rotated",
                "source_kind": "CLAUDE_SESSION_JSONL",
                "source_version": "v1",
                "workspace": r"C:\workspace\universe",
                "workspace_name": "universe",
                "identity_state": "VERIFIED",
                "transcript_content": "EXCLUDED",
            }
            return [
                {
                    **base,
                    "source_path": str(self.temp_root / "chat-old.jsonl"),
                    "last_modified_at": "2026-08-08T00:00:00Z",
                    "display_name": "Rotated chat",
                    "session_kind": "CHAT",
                },
                {
                    **base,
                    "source_path": str(self.temp_root / "chat-current.jsonl"),
                    "last_modified_at": "2026-08-09T00:00:00Z",
                    "display_name": "Rotated chat",
                    "session_kind": "CHAT",
                },
                {
                    **base,
                    "source_path": str(self.temp_root / "agent-one.jsonl"),
                    "last_modified_at": "2026-08-09T00:01:00Z",
                    "display_name": "Review worker",
                    "session_kind": "WORKER",
                },
            ]

        with patch.object(
            self.server.store,
            "discover_provider_session_sources",
            side_effect=discovered,
        ):
            catalog = self.server.provider_chat_catalog()

        chats = [
            room for room in catalog["rooms"] if room["session_kind"] == "CHAT"
        ]
        workers = [
            room for room in catalog["rooms"] if room["session_kind"] == "WORKER"
        ]
        self.assertEqual(1, len(chats))
        self.assertEqual("2026-08-09T00:00:00Z", chats[0]["last_activity_at"])
        self.assertEqual(1, len(workers))
        self.assertNotEqual(chats[0]["chat_key"], workers[0]["chat_key"])

    def test_provider_chat_catalog_keeps_each_bound_session_chat_distinct(
        self,
    ) -> None:
        def discovered(provider: str) -> list[dict[str, Any]]:
            session_id = {
                "CODEX": "codex-same-anchor",
                "CLAUDE": "claude-same-anchor",
                "GROK": "grok-anchorless",
            }[provider]
            return [
                {
                    "schema": "universe.provider-session-source.v1",
                    "status": "DISCOVERED",
                    "provider": provider,
                    "provider_session_id": session_id,
                    "source_path": str(self.temp_root / provider / "chat.jsonl"),
                    "source_kind": f"{provider}_SESSION_JSONL",
                    "source_version": "v1",
                    "last_modified_at": (
                        "2026-08-09T00:01:00Z"
                        if provider == "CODEX"
                        else "2026-08-09T00:00:00Z"
                    ),
                    "workspace": r"C:\workspace\universe",
                    "workspace_name": "universe",
                    "display_name": f"{provider.title()} chat",
                    "session_kind": "CHAT",
                    "identity_state": "VERIFIED",
                    "transcript_content": "EXCLUDED",
                }
            ]

        supervisor_sessions = [
            {
                "session_id": "canonical-codex",
                "provider": "CODEX",
                "provider_session_ref": "codex-same-anchor",
                "node": "CONDUCTOR",
                "mode": "CONDUCTOR",
                "anchor_ref": "MASTER-BEYOND-OLD",
                "is_default": False,
                "updated_at": "2026-08-08T00:00:00Z",
            },
            {
                "session_id": "canonical-claude",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-same-anchor",
                "node": "universe",
                "mode": "MASTER",
                "anchor_ref": "MASTER-CURRENT-SAME",
                "is_default": True,
                "updated_at": "2026-08-09T00:00:00Z",
            },
            {
                "session_id": "legacy-anchorless",
                "provider": "GROK",
                "provider_session_ref": "grok-anchorless",
                "node": "universe",
                "mode": "MASTER",
                "anchor_ref": None,
                "is_default": False,
                "updated_at": "2026-08-09T00:00:00Z",
            },
        ]
        anchor_observations = [
            {
                "project_id": "universe",
                "node": "universe",
                "mode": "MASTER",
                "anchor_ref": "MASTER-CURRENT-SAME",
                "temporality": "CURRENT",
                "observed_at": "2026-08-09T00:00:00Z",
                "provider": provider,
                "provider_session_ref": session_ref,
                "evidence_ref": "universe://projects/universe/anchor-store/MASTER-CURRENT-SAME",
                "anchor_session_currentness": (
                    "CURRENT" if provider == "CLAUDE" else "PAST"
                ),
                "anchor_currentness_source": "PROJECT_ANCHOR_DB",
            }
            for provider, session_ref in (
                ("CODEX", "codex-same-anchor"),
                ("CLAUDE", "claude-same-anchor"),
            )
        ]
        with (
            patch.object(
                self.server.store,
                "discover_provider_session_sources",
                side_effect=discovered,
            ),
            patch.object(
                self.server.session_supervisor,
                "list_sessions",
                return_value=supervisor_sessions,
            ),
            patch.object(
                self.server,
                "_project_anchor_observations",
                return_value=anchor_observations,
            ),
            patch.object(
                self.server,
                "_project_anchor_supervisor_projection",
                return_value={"status": "PROJECT_ANCHORS_PROJECTED"},
            ),
        ):
            catalog = self.server.provider_chat_catalog()

        observed = [
            room
            for room in catalog["rooms"]
            if room["binding"]["state"] == "ANCHOR_OBSERVED"
        ]
        self.assertEqual(2, len(observed))
        self.assertEqual(
            {"CODEX", "CLAUDE"}, {room["provider"] for room in observed}
        )
        self.assertTrue(
            all(
                room["binding"]["selection_scope"] == "UI_NAVIGATION_ONLY"
                for room in observed
            )
        )
        self.assertTrue(
            all(room["binding"]["session_anchor_ref"] for room in observed)
        )
        grok = next(room for room in catalog["rooms"] if room["provider"] == "GROK")
        self.assertEqual("INDEPENDENT", grok["binding"]["state"])

    def test_session_observatory_card_uses_anchor_observer_identity(self) -> None:
        session = {
            "session_id": "supervisor-claude",
            "node": "universe",
            "mode": "MASTER",
            "current_project_id": "universe",
            "provider": "CLAUDE",
            "provider_session_ref": "claude-current",
            "anchor_ref": "MASTER-STALE",
            "state": "LIVE",
            "currentness": "CURRENT",
            "updated_at": "2026-08-14T01:00:00Z",
        }
        observations = [
            {
                "project_id": "GCS",
                "node": "GCS",
                "mode": "CONDUCTOR",
                "anchor_ref": "CONDUCTOR-CURRENT-NEW",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-current",
                "current_anchor_observed_at": "2026-08-14T02:00:00Z",
                "anchor_session_currentness": "CURRENT",
                "anchor_currentness_source": "PROJECT_ANCHOR_DB",
            }
        ]
        with patch.object(
            self.server.multi_rooms,
            "preview_for_session",
            return_value={"lines": [], "source": "NONE", "match": "NONE"},
        ):
            card = self.server._session_observatory_card(
                session,
                continuity_by_project={},
                projects_by_id={},
                anchor_observations=observations,
            )

        self.assertEqual("GCS", card["node"])
        self.assertEqual("CONDUCTOR", card["mode"])
        self.assertEqual("CONDUCTOR-CURRENT-NEW", card["anchor_ref"])
        self.assertEqual("CURRENT", card["currentness"])
        self.assertEqual("PROJECT_ANCHOR_DB", card["anchor_currentness_source"])
        self.assertTrue(card["is_anchor_current"])
        self.assertEqual(
            "CONDUCTOR-CURRENT-NEW",
            card["anchor_session"]["current_anchor_ref"],
        )

    def test_project_mode_anchor_endpoint_returns_append_only_session_lineage(self) -> None:
        first, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "anchor-endpoint-one",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-anchor-endpoint-one",
            }
        )
        second, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "anchor-endpoint-two",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-anchor-endpoint-two",
            }
        )

        status, response = self.request(
            "GET", "/v1/supervisor/project-mode-anchors/GCS/MASTER", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROJECT_MODE_ANCHOR_COLLECTED", response["status"])
        self.assertEqual(
            [first["session_anchor_ref"], second["session_anchor_ref"]],
            [
                item["session_anchor_ref"]
                for item in response["anchor"]["session_anchor_refs"]
            ],
        )

    def test_session_graph_projects_mode_session_and_task_frame_lineage(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        first, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-graph-one",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-session-graph-one",
            }
        )
        second, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-graph-two",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-session-graph-two",
            }
        )
        self.server.task_frame_lineage.create_task_frame(
            frame_ref="task-frame-session-graph",
            origin_session_anchor_ref=first["session_anchor_ref"],
            target_session_anchor_ref=second["session_anchor_ref"],
        )
        self.server.task_frame_lineage.attach_result(
            result_ref="task-frame-session-graph-result",
            frame_ref="task-frame-session-graph",
            origin_session_anchor_ref=first["session_anchor_ref"],
            result={"text": "private provider output must not enter the graph"},
        )

        status, response = self.request(
            "GET", "/v1/session-graph?project_id=GCS", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        graph = response["graph"]
        self.assertEqual("SESSION_GRAPH_PROJECTED", graph["status"])
        self.assertTrue(graph["projection_policy"]["read_only"])
        self.assertFalse(graph["projection_policy"]["authority_created"])
        node_types = [item["entity_type"] for item in graph["nodes"]]
        self.assertEqual(1, node_types.count("MODE_ANCHOR"))
        self.assertEqual(2, node_types.count("SESSION_ANCHOR"))
        self.assertEqual(1, node_types.count("TASK_FRAME"))
        self.assertEqual(1, node_types.count("TASK_FRAME_RESULT"))
        result_node = next(
            item for item in graph["nodes"]
            if item["entity_type"] == "TASK_FRAME_RESULT"
        )
        self.assertFalse(result_node["result_in_graph"])
        self.assertNotIn("private provider output", json.dumps(graph))
        edge_types = {item["edge_type"] for item in graph["edges"]}
        self.assertIn("MODE_ANCHOR_HAS_SESSION_ANCHOR", edge_types)
        self.assertIn("SESSION_ANCHOR_HAS_TASK_FRAME", edge_types)
        self.assertIn("TASK_FRAME_TARGETS_SESSION", edge_types)
        self.assertIn("TASK_FRAME_HAS_RESULT", edge_types)
        self.assertIn("TASK_FRAME_RESULT_ORIGINATES_FROM_SESSION", edge_types)

    def test_semantic_graph_projects_task_frame_lineage_without_result_payload(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        first, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "semantic-task-frame-one",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-semantic-task-frame-one",
            }
        )
        second, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "semantic-task-frame-two",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-semantic-task-frame-two",
            }
        )
        self.server.task_frame_lineage.create_task_frame(
            frame_ref="semantic-task-frame-001",
            origin_session_anchor_ref=first["session_anchor_ref"],
            target_session_anchor_ref=second["session_anchor_ref"],
        )
        self.server.task_frame_lineage.attach_result(
            result_ref="semantic-task-frame-result-001",
            frame_ref="semantic-task-frame-001",
            origin_session_anchor_ref=first["session_anchor_ref"],
            result={"summary": "secret provider transcript must not project"},
        )

        graph = self.server.store.semantic_project_graph("GCS")
        nodes = {item["id"]: item for item in graph["nodes"]}
        self.assertIn("task_frame:semantic-task-frame-001", nodes)
        result = nodes["task_frame_result:semantic-task-frame-result-001"]
        self.assertEqual("ATTACHED", result["lifecycle_state"])
        self.assertNotIn("result", result["data"])
        self.assertNotIn("secret provider transcript", json.dumps(result))
        edge_types = {item["edge_type"] for item in graph["edges"]}
        self.assertIn("PROJECT_HAS_TASK_FRAME", edge_types)
        self.assertIn("SESSION_ANCHOR_HAS_TASK_FRAME", edge_types)
        self.assertIn("TASK_FRAME_TARGETS_SESSION_ANCHOR", edge_types)
        self.assertIn("TASK_FRAME_HAS_RESULT", edge_types)

    def test_semantic_graph_projects_test_work_status(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.store.append_event(
            "GCS",
            {
                "event_id": "test-work-status-001",
                "event_type": "TEST_WORK_STATUS",
                "payload": {
                    "schema": "universe.test-tier-result.v1",
                    "source": "RUN_TEST_TIER",
                    "tier": "changed",
                    "successful": True,
                    "tests_run": 12,
                    "elapsed_seconds": 1.25,
                    "redaction_state": "SUMMARY_ONLY",
                },
            },
        )
        graph = self.server.store.semantic_project_graph("GCS")
        test_nodes = [item for item in graph["nodes"] if item["entity_type"] == "TEST_RUN"]
        self.assertEqual(1, len(test_nodes))
        self.assertEqual("Tests · changed · PASSED", test_nodes[0]["label"])
        self.assertEqual(12, test_nodes[0]["data"]["tests_run"])
        cursors = self.server.store.list_semantic_collection_cursors("GCS")
        self.assertEqual("TEST_RUNNER", cursors[0]["source_kind"])
        self.assertEqual("test-work-status-001", cursors[0]["last_event_id"])
        self.assertIn(
            "TEST_RUNNER",
            {
                item["data"]["source_kind"]
                for item in graph["nodes"]
                if item["entity_type"] == "COLLECTION_CURSOR"
            },
        )

    def test_semantic_graph_derives_failure_candidate_from_failed_test_status(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.store.append_event(
            "GCS",
            {
                "event_id": "test-work-status-failed-001",
                "event_type": "TEST_WORK_STATUS",
                "payload": {
                    "schema": "universe.test-tier-result.v1",
                    "source": "RUN_TEST_TIER",
                    "tier": "changed",
                    "successful": False,
                    "tests_run": 3,
                    "failure_output": "private test output must stay out of graph",
                },
            },
        )

        graph = self.server.store.semantic_project_graph("GCS")
        candidates = [
            item for item in graph["nodes"]
            if item["entity_type"] == "FAILURE_CANDIDATE"
        ]
        self.assertEqual(1, len(candidates))
        candidate = candidates[0]
        self.assertEqual("USER_SELECTION_REQUIRED", candidate["data"]["promotion_state"])
        self.assertNotIn("failure_output", candidate["data"])
        self.assertNotIn("private test output", json.dumps(graph))
        self.assertIn(
            "TEST_RUN_DERIVES_FAILURE_CANDIDATE",
            {item["edge_type"] for item in graph["edges"]},
        )

    def test_semantic_graph_derives_drift_only_for_live_stale_sessions(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        stale, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "semantic-drift-live",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "semantic-drift-live-provider",
                "state": "LIVE",
                "currentness": "STALE",
            }
        )
        self.server.session_supervisor.register_session(
            {
                "session_id": "semantic-drift-historical",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "semantic-drift-historical-provider",
                "state": "STOPPED",
                "currentness": "STALE",
            }
        )

        graph = self.server.store.semantic_project_graph("GCS")
        drifts = [item for item in graph["nodes"] if item["entity_type"] == "DRIFT_CANDIDATE"]
        self.assertEqual(1, len(drifts))
        self.assertEqual(stale["session_id"], drifts[0]["data"]["session_id"])
        self.assertEqual("USER_SELECTION_REQUIRED", drifts[0]["data"]["promotion_state"])
        self.assertIn(
            "SESSION_DERIVES_DRIFT_CANDIDATE",
            {item["edge_type"] for item in graph["edges"]},
        )

    def test_memory_batch_completion_advances_semantic_collection_cursor(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.store.persist_memory_batch_result(
            run_id="memory-batch-run-001",
            project_id="GCS",
            stage="FAST_EXTRACT",
            result={
                "config_digest": "a" * 64,
                "status": "COMPLETED",
                "candidate_ids": [],
            },
            input_digest="b" * 64,
            output_digest="c" * 64,
            now="2026-08-21T00:00:00Z",
        )

        cursors = self.server.store.list_semantic_collection_cursors("GCS")

        self.assertEqual(1, len(cursors))
        self.assertEqual("MEMORY_BATCH_FAST_EXTRACT", cursors[0]["source_kind"])
        self.assertEqual("memory-batch-run-001", cursors[0]["last_event_id"])
        self.assertEqual("c" * 64, cursors[0]["source_digest"])

    def test_skill_observation_advances_bench_collection_cursor(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        result, created = self.server.store.ingest_skill_observations(
            "GCS", self.skill_observation_candidate()
        )

        self.assertTrue(created)
        cursors = self.server.store.list_semantic_collection_cursors("GCS")
        self.assertEqual(1, len(cursors))
        self.assertEqual("BENCH_OBSERVATION", cursors[0]["source_kind"])
        self.assertEqual(
            result["observations"][-1]["observation_id"],
            cursors[0]["last_event_id"],
        )

    def test_collection_prediction_is_proposal_only(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.store.propose_work_loop_predictions = Mock(
            return_value=(
                {
                    "proposal_id": "prediction-001",
                    "review_state": "PROPOSAL_ONLY",
                },
                True,
            )
        )

        prediction = self.server._propose_prediction_after_collection("GCS")

        self.assertEqual("PREDICTION_PROPOSAL_READY", prediction["status"])
        self.assertEqual("prediction-001", prediction["proposal_id"])
        self.assertTrue(prediction["proposal_created"])
        self.assertFalse(prediction["goal_created"])
        self.assertFalse(prediction["todo_created"])
        self.assertFalse(prediction["task_frame_created"])
        self.assertFalse(prediction["execution_assignment_created"])

    def test_collection_creates_document_automation_candidate_without_write(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.store.create_document_incorporation_proposal = Mock(
            return_value=({"proposal_id": "document-automation-001"}, True)
        )

        result = self.server._propose_document_after_collection("GCS")

        self.assertEqual("DOCUMENT_AUTOMATION_PROPOSAL_READY", result["status"])
        self.assertEqual("document-automation-001", result["proposal_id"])
        self.assertTrue(result["proposal_created"])
        self.assertFalse(result["document_written"])

    def test_semantic_graph_projects_cross_session_work_allocation(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        allocation, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "GCS",
                "summary": "Send the bounded implementation work to the selected Master.",
                "idempotency_key": "graph-allocation-001",
                "worker_role": "PROJECT_MASTER",
                "origin_session_anchor_ref": "session-anchor-conductor-001",
                "target_session_anchor_ref": "session-anchor-master-001",
                "origin_session_chat_key": "provider_chat_1234567890abcdef12345678",
            }
        )
        self.assertTrue(created)
        graph = self.server.store.semantic_project_graph("GCS")
        allocation_nodes = [
            item
            for item in graph["nodes"]
            if item["entity_type"] == "WORK_ALLOCATION"
        ]
        self.assertEqual(1, len(allocation_nodes))
        self.assertEqual(allocation["delegation_id"], allocation_nodes[0]["data"]["delegation_id"])
        edge_types = {item["edge_type"] for item in graph["edges"]}
        self.assertIn("SESSION_ANCHOR_ALLOCATES_WORK", edge_types)
        self.assertIn("WORK_ALLOCATION_TARGETS_SESSION_ANCHOR", edge_types)

    def test_new_session_allocation_waits_for_hook_before_transport(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        request = normalize_conductor_delegation(
            {
                "project_id": "GCS",
                "summary": "Open a Master session for automatic bounded work.",
                "idempotency_key": "allocation-new-001",
                "worker_role": "PROJECT_MASTER",
                "target_session_action": "NEW",
                "origin_session_anchor_ref": "session-anchor-conductor-001",
                "origin_session_chat_key": "provider_chat_1234567890abcdef12345678",
            }
        )
        self.server.prepare_project_master_session = Mock(
            return_value={
                "session_connection": {
                    "session_anchor_ref": "session-anchor-new-master-001"
                }
            }
        )
        self.server.session_anchor_transport.deliver = Mock()
        outcome = self.server._dispatch_project_master_delegation(
            {
                "delegation_id": "delegation-new-001",
                "project_id": "GCS",
                "request": request,
                "progress": {},
            }
        )
        self.assertEqual("WAITING_FOR_VENDOR_HOOK", outcome["progress"]["step"])
        self.assertEqual(
            "session-anchor-new-master-001",
            outcome["progress"]["target_session_anchor_ref"],
        )
        self.server.prepare_project_master_session.assert_called_once_with(
            "GCS", {"session_action": "NEW"}
        )
        self.server.session_anchor_transport.deliver.assert_not_called()

    def test_hook_resumes_only_matching_waiting_session_allocation(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        delegation, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "GCS",
                "summary": "Wait for the exact target vendor session hook.",
                "idempotency_key": "allocation-hook-001",
                "worker_role": "PROJECT_MASTER",
                "target_session_action": "NEW",
                "origin_session_anchor_ref": "session-anchor-conductor-001",
                "origin_session_chat_key": "provider_chat_1234567890abcdef12345678",
            }
        )
        self.assertTrue(created)
        self.server.store.start_conductor_delegation(delegation["delegation_id"])
        self.server.store.update_conductor_delegation_progress(
            delegation["delegation_id"],
            {
                "summary": "Awaiting the exact vendor Session Hook.",
                "step": "WAITING_FOR_VENDOR_HOOK",
                "target_session_anchor_ref": "session-anchor-new-master-001",
            },
        )
        self.server.session_anchor_transport.deliver = Mock(
            return_value={
                "progress": {
                    "summary": "Delivered to the verified target session.",
                    "step": "TARGET_ACCEPTED",
                }
            }
        )
        observed = self.server.record_session_hook_observation(
            {
                "project_id": "GCS",
                "hook_observation": {
                    "trigger": "SESSION_START",
                    "observed_at": "2026-08-21T00:00:00Z",
                },
            },
            {
                "supervisor_session": {
                    "session_id": "supervisor-new-master-001",
                    "provider": "CODEX",
                    "provider_session_ref": "vendor-session-001",
                    "session_anchor_ref": "session-anchor-new-master-001",
                }
            },
        )
        self.assertIsNotNone(observed)
        self.server.session_anchor_transport.deliver.assert_called_once()
        resumed = self.server.store.get_conductor_delegation(
            delegation["delegation_id"]
        )
        self.assertEqual("TARGET_ACCEPTED", resumed["progress"]["step"])

    def test_session_hook_keeps_existing_provider_ref_when_turn_id_is_unavailable(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        existing, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-hook-preserve-ref-001",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-code:existing-turn-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

        injected = perform_session_ref_inject(
            session_supervisor=self.server.session_supervisor,
            multi_rooms=self.server.multi_rooms,
            body={
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "supervisor_session_id": existing["session_id"],
                "state": "LIVE",
                "room_type": "PROJECT",
                "slot_role": "MASTER",
                "make_default": False,
            },
        )

        self.assertEqual(
            "claude-code:existing-turn-001",
            injected["supervisor_session"]["provider_session_ref"],
        )

    def test_session_runtime_attachment_is_exact_idempotent_and_redacts_token(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-runtime-attach-001",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-runtime-attach-001",
                "anchor_ref": "session-anchor-runtime-attach-001",
                "state": "STARTING",
            }
        )
        starts: list[tuple[Path, dict[str, Any]]] = []

        class FakeSessionRuntime:
            def __init__(self, root: Path, value: dict[str, Any]) -> None:
                starts.append((root, value))

            def start(self) -> dict[str, Any]:
                return {
                    "endpoint": "http://127.0.0.1:41994",
                    "token": "must-not-leak",
                    "session_id": session["session_id"],
                    "origin_anchor_ref": session["session_anchor_ref"],
                    "origin_frame_id": "current",
                    "attachment_path": "ANCHOR_GRAPH",
                    "runtime_currentness_observation": "CURRENT",
                    "binding_evidence_ref": "process-local://session-runtime",
                }

            def stop(self) -> None:
                return None

        self.server._session_runtime_factory = FakeSessionRuntime
        first = self.server.ensure_session_runtime_attachment(session)
        second = self.server.ensure_session_runtime_attachment(session)

        self.assertEqual("WORK_READY", first["status"])
        self.assertEqual(session["session_anchor_ref"], first["session_anchor_ref"])
        self.assertNotIn("endpoint", first)
        self.assertNotIn("token", first)
        self.assertEqual(first, second)
        self.assertEqual(1, len(starts))

        triggered = self.server.attach_session_runtime_from_hook(
            {
                "hook_observation": {
                    "schema": "universe.hook-session-observation.v1",
                    "trigger": "session_start",
                }
            },
            {
                "supervisor_session": session,
                "managed_shell_attachment": {
                    "status": "MANAGED_SHELL_ATTACHED"
                },
            },
        )
        self.assertEqual("WORK_READY", triggered["status"])

    def test_claude_session_without_channel_does_not_inject_pty(self) -> None:
        terminal = {
            "terminal_id": "term-session-hook-001",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CLAUDE",
            "state": "LIVE",
            "supervisor_session_id": "supervisor-session-hook-001",
        }
        self.server.terminal_host.find_live = Mock(return_value=terminal)
        self.server.terminal_host.get = Mock(return_value=terminal)
        self.server.terminal_host.write = Mock()
        self.server._provider_chat_key_for_session_instruction = Mock(
            return_value=None
        )
        posted = self.server.session_bus.deliver_to_terminal(
            self.server.terminal_host,
            terminal=terminal,
            source={"project_id": "universe", "mode": "CONDUCTOR", "provider": "UI"},
            to={"project_id": "GCS", "mode": "MASTER", "provider": "CLAUDE"},
            kind="INSTRUCTION",
            notify="NONE",
            body="Create the requested project documents.",
        )

        dispatched = self.server._dispatch_pending_session_instruction(
            project_id="GCS",
            session={
                "session_id": "supervisor-session-hook-001",
                "session_anchor_ref": "session-anchor-hook-001",
                "provider": "CLAUDE",
                "mode": "MASTER",
            },
            trigger="SESSION_START",
        )

        self.assertEqual("CLAUDE_CHANNEL_UNAVAILABLE", dispatched["status"])
        self.assertEqual("CLAUDE_CODE_CHANNEL_REQUIRED", dispatched["delivery_mode"])
        self.assertIsNone(dispatched["hook_stdout"])
        self.server.terminal_host.write.assert_not_called()
        self.assertEqual(1, self.server.session_bus.unread_count("term-session-hook-001"))
        self.assertEqual(
            posted["message_id"],
            self.server.session_bus.inbox(
                self.server.terminal_host,
                terminal_id="term-session-hook-001",
            )["messages"][0]["message_id"],
        )

    def test_claude_session_bus_uses_channel_without_pty_injection(self) -> None:
        terminal = {
            "terminal_id": "term-session-channel-001",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CLAUDE",
            "state": "LIVE",
            "supervisor_session_id": "supervisor-session-channel-001",
        }
        self.server.terminal_host.find_live = Mock(return_value=terminal)
        self.server.terminal_host.get = Mock(return_value=terminal)
        self.server.terminal_host.channel_state = Mock(return_value="READY")
        def push_channel_result(
            _terminal_id: str,
            payload: Mapping[str, Any],
            *,
            on_result: Callable[[Mapping[str, Any]], None] | None = None,
        ) -> dict[str, Any]:
            if on_result is not None:
                timer = threading.Timer(
                    0.01,
                    lambda: on_result(
                        {
                            "message_id": payload["message_id"],
                            "body_text": "Created the requested project documents.",
                            "outcome": "COMPLETED",
                            "result_ref": "artifact://claude-channel-001",
                        }
                    ),
                )
                timer.daemon = True
                timer.start()
            return {"status": "QUEUED", "message_id": payload["message_id"]}

        self.server.terminal_host.push_channel = Mock(side_effect=push_channel_result)
        self.server.terminal_host.write = Mock()
        posted = self.server.session_bus.deliver_to_terminal(
            self.server.terminal_host,
            terminal=terminal,
            source={"project_id": "universe", "mode": "CONDUCTOR", "provider": "UI"},
            to={"project_id": "GCS", "mode": "MASTER", "provider": "CLAUDE"},
            kind="INSTRUCTION",
            notify="NONE",
            body="Create the requested project documents.",
        )

        dispatched = self.server._dispatch_pending_session_instruction(
            project_id="GCS",
            session={
                "session_id": "supervisor-session-channel-001",
                "session_anchor_ref": "session-anchor-channel-001",
                "provider": "CLAUDE",
                "mode": "MASTER",
            },
            trigger="SESSION_START",
        )

        self.assertEqual("DISPATCHED", dispatched["status"])
        self.assertEqual("CLAUDE_CODE_CHANNEL", dispatched["delivery_mode"])
        self.assertEqual(posted["message_id"], dispatched["message_id"])
        self.server.terminal_host.write.assert_not_called()
        payload = self.server.terminal_host.push_channel.call_args.args[1]
        self.assertEqual("Create the requested project documents.", payload["content"])
        self.assertEqual("UNIVERSE_UI", payload["meta"]["sender_id"])
        self.assertEqual(0, self.server.session_bus.unread_count(terminal["terminal_id"]))
        deadline = time.monotonic() + 1.0
        results: list[Mapping[str, Any]] = []
        while time.monotonic() < deadline:
            results = self.server.session_bus.inbox(
                self.server.terminal_host,
                session_anchor_ref="session-anchor-channel-001",
                projection="RESULTS",
            )["messages"]
            if results:
                break
            time.sleep(0.01)
        self.assertEqual(1, len(results))
        self.assertEqual(
            "Created the requested project documents.",
            results[0]["body_text"],
        )
        self.assertEqual(posted["message_id"], results[0]["in_reply_to"])

    def test_interactive_rust_host_codex_uses_host_input_before_native_chat(self) -> None:
        terminal = {
            "terminal_id": "term-rust-host-codex-001",
            "project_id": "GCS",
            "mode": "CONDUCTOR",
            "provider": "CODEX",
            "state": "LIVE",
            "supervisor_session_id": "supervisor-rust-host-codex-001",
            "backend_owner": "RUST_RECONNECTION_HOST",
            "launch_profile": "INTERACTIVE",
        }
        self.server.terminal_host.find_live = Mock(return_value=terminal)
        self.server.terminal_host.get = Mock(return_value=terminal)
        self.server.terminal_host.write = Mock()
        self.server._provider_chat_key_for_session_instruction = Mock(
            return_value="chat_codex_native_should_not_run"
        )
        self.server.provider_sessions.submit_channel = Mock()
        posted = self.server.session_bus.deliver_to_terminal(
            self.server.terminal_host,
            terminal=terminal,
            source={"project_id": "universe", "mode": "CONDUCTOR", "provider": "UI"},
            to={"project_id": "GCS", "mode": "CONDUCTOR", "provider": "CODEX"},
            kind="INSTRUCTION",
            notify="NONE",
            body="Continue through the visible Rust Host session.",
        )

        dispatched = self.server._dispatch_pending_session_instruction(
            project_id="GCS",
            session={
                "session_id": "supervisor-rust-host-codex-001",
                "session_anchor_ref": "session-anchor-rust-host-codex-001",
                "provider": "CODEX",
                "provider_session_ref": "codex-thread-native-001",
                "mode": "CONDUCTOR",
            },
            trigger="TURN_IDLE",
        )

        self.assertEqual("DISPATCHED", dispatched["status"])
        self.assertEqual("RUST_HOST_INPUT", dispatched["delivery_mode"])
        self.assertEqual(posted["message_id"], dispatched["message_id"])
        expected = (
            f"instruction_ref: session-bus:{posted['message_id']}\n"
            "Continue through the visible Rust Host session.\r"
        ).encode("utf-8")
        self.server.terminal_host.write.assert_called_once_with(
            terminal["terminal_id"],
            expected,
        )
        self.server.provider_sessions.submit_channel.assert_not_called()

    def test_claude_channel_pending_does_not_fall_back_to_pty(self) -> None:
        terminal = {
            "terminal_id": "term-session-channel-pending-001",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CLAUDE",
            "state": "LIVE",
            "supervisor_session_id": "supervisor-session-channel-pending-001",
        }
        self.server.terminal_host.find_live = Mock(return_value=terminal)
        self.server.terminal_host.get = Mock(return_value=terminal)
        self.server.terminal_host.channel_state = Mock(return_value="PENDING")
        self.server.terminal_host.write = Mock()
        posted = self.server.session_bus.deliver_to_terminal(
            self.server.terminal_host,
            terminal=terminal,
            source={"project_id": "universe", "mode": "CONDUCTOR", "provider": "UI"},
            to={"project_id": "GCS", "mode": "MASTER", "provider": "CLAUDE"},
            kind="INSTRUCTION",
            notify="NONE",
            body="Wait for the authenticated channel.",
        )

        dispatched = self.server._dispatch_pending_session_instruction(
            project_id="GCS",
            session={
                "session_id": "supervisor-session-channel-pending-001",
                "session_anchor_ref": "session-anchor-channel-pending-001",
                "provider": "CLAUDE",
                "mode": "MASTER",
            },
            trigger="SESSION_START",
        )

        self.assertEqual("CLAUDE_CHANNEL_PENDING", dispatched["status"])
        self.assertEqual("CLAUDE_CODE_CHANNEL_PENDING", dispatched["delivery_mode"])
        self.server.terminal_host.write.assert_not_called()
        self.assertEqual(1, self.server.session_bus.unread_count(terminal["terminal_id"]))
        self.assertEqual(
            posted["message_id"],
            self.server.session_bus.inbox(
                self.server.terminal_host,
                terminal_id=terminal["terminal_id"],
            )["messages"][0]["message_id"],
        )

    def test_supervised_session_keeps_instruction_pending_until_native_chat_is_visible(self) -> None:
        terminal = {
            "terminal_id": "term-native-pending-001",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CLAUDE",
            "state": "LIVE",
            "supervisor_session_id": "supervisor-native-pending-001",
        }
        self.server.terminal_host.find_live = Mock(return_value=terminal)
        self.server.terminal_host.get = Mock(return_value=terminal)
        self.server.terminal_host.write = Mock()
        self.server._provider_chat_key_for_session_instruction = Mock(
            return_value=None
        )
        posted = self.server.session_bus.deliver_to_terminal(
            self.server.terminal_host,
            terminal=terminal,
            source={"project_id": "universe", "mode": "CONDUCTOR", "provider": "UI"},
            to={"project_id": "GCS", "mode": "MASTER", "provider": "CLAUDE"},
            kind="INSTRUCTION",
            notify="NONE",
            body="Wait for the native Claude session.",
        )

        dispatched = self.server._dispatch_pending_session_instruction(
            project_id="GCS",
            session={
                "session_id": "supervisor-native-pending-001",
                "session_anchor_ref": "session-anchor-native-pending-001",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-code:pending-turn-001",
                "mode": "MASTER",
            },
            trigger="SESSION_START",
        )

        self.assertEqual("NATIVE_PROVIDER_UNAVAILABLE", dispatched["status"])
        self.assertEqual("PROVIDER_NATIVE_PENDING", dispatched["delivery_mode"])
        self.server.terminal_host.write.assert_not_called()
        self.assertEqual(1, self.server.session_bus.unread_count(terminal["terminal_id"]))
        self.assertEqual(posted["message_id"], self.server.session_bus.inbox(
            self.server.terminal_host,
            terminal_id=terminal["terminal_id"],
        )["messages"][0]["message_id"])

    def test_native_chat_resolution_requires_exact_supervisor_anchor(self) -> None:
        chat_key = _vendor_chat_key("CLAUDE", "claude-code:native-001")
        self.server.provider_chat_catalog = Mock(
            return_value={
                "rooms": [
                    {
                        "chat_key": chat_key,
                        "provider": "CLAUDE",
                        "binding": {
                            "state": "BOUND",
                            "universe_session_id": "supervisor-native-001",
                            "session_anchor_ref": "session-anchor-native-001",
                        },
                    }
                ]
            }
        )
        self.server.resolve_provider_chat_session = Mock(
            return_value={
                "provider": "CLAUDE",
                "provider_session_ref": "claude-code:native-001",
            }
        )

        self.assertEqual(
            chat_key,
            self.server._provider_chat_key_for_session_instruction(
                session={
                    "session_id": "supervisor-native-001",
                    "session_anchor_ref": "session-anchor-native-001",
                    "provider": "CLAUDE",
                    "provider_session_ref": "claude-code:native-001",
                }
            ),
        )
        self.assertIsNone(
            self.server._provider_chat_key_for_session_instruction(
                session={
                    "session_id": "supervisor-native-001",
                    "session_anchor_ref": "session-anchor-other-001",
                    "provider": "CLAUDE",
                    "provider_session_ref": "claude-code:native-001",
                }
            )
        )

    def test_native_chat_resolution_accepts_exact_private_join_during_catalog_lag(
        self,
    ) -> None:
        chat_key = _vendor_chat_key("GROK", "grok-native-lag-001")
        self.server.provider_chat_catalog = Mock(
            return_value={
                "rooms": [
                    {
                        "chat_key": chat_key,
                        "provider": "GROK",
                        "binding": {"state": "INDEPENDENT"},
                    }
                ]
            }
        )
        self.server.resolve_provider_chat_session = Mock(
            return_value={
                "provider": "GROK",
                "provider_session_ref": "grok-native-lag-001",
                "supervisor_session_id": "supervisor-native-lag-001",
                "origin_session_anchor_ref": "session-anchor-native-lag-001",
            }
        )

        session = {
            "session_id": "supervisor-native-lag-001",
            "session_anchor_ref": "session-anchor-native-lag-001",
            "provider": "GROK",
            "provider_session_ref": "grok-native-lag-001",
        }
        self.assertEqual(
            chat_key,
            self.server._provider_chat_key_for_session_instruction(session=session),
        )
        self.assertIsNone(
            self.server._provider_chat_key_for_session_instruction(
                session={**session, "session_id": "supervisor-other"}
            )
        )

    def test_live_ui_instruction_dispatches_without_waiting_for_next_session_start(self) -> None:
        terminal = {
            "terminal_id": "term-live-session-hook-001",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CODEX",
            "state": "LIVE",
            "supervisor_session_id": "supervisor-live-session-hook-001",
        }
        self.server.terminal_host.list_sessions = Mock(return_value=[terminal])
        self.server.terminal_host.get = Mock(return_value=terminal)
        self.server.terminal_host.find_live = Mock(return_value=terminal)
        self.server.terminal_host.write = Mock()
        native_chat_key = "provider_chat_1234567890abcdef12345678"
        self.server._provider_chat_key_for_session_instruction = Mock(
            return_value=native_chat_key
        )

        def accept_native_turn(
            chat_key: str,
            value: Mapping[str, Any],
            *,
            on_accepted: Callable[[Mapping[str, Any]], None] | None = None,
            on_terminal: Callable[[Mapping[str, Any]], None] | None = None,
        ) -> dict[str, Any]:
            self.assertEqual(native_chat_key, chat_key)
            self.assertEqual(
                "universe.host-message-channel.v1", value["schema"]
            )
            self.assertEqual(
                "Reply with exactly: HOOK_DISPATCH_CONFIRMED",
                value["content"],
            )
            self.assertTrue(str(value["message_id"]).startswith("msg_"))
            self.assertEqual("CODEX", value["meta"]["provider"])
            if on_accepted is not None:
                on_accepted({"message_id": "provider-reply-001"})
            if on_terminal is not None:
                on_terminal(
                    {
                        "message_id": "provider-reply-001",
                        "state": "COMPLETED",
                        "body": "HOOK_DISPATCH_CONFIRMED",
                    }
                )
            return {
                "status": "PROVIDER_SESSION_INPUT_ACCEPTED",
                "message": {"message_id": "provider-user-001"},
                "reply": {"message_id": "provider-reply-001"},
            }

        self.server.provider_sessions.submit_channel = Mock(side_effect=accept_native_turn)
        self.server.session_supervisor.register_session(
            {
                "session_id": "supervisor-live-session-hook-001",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-app-server:live-turn-001",
                "session_anchor_ref": "session-anchor-live-hook-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

        older = self.server.session_bus.post(
            self.server.terminal_host,
            {
                "to": {"terminal_id": terminal["terminal_id"]},
                "from": {
                    "project_id": "universe",
                    "mode": "CONDUCTOR",
                    "provider": "UI",
                },
                "kind": "INSTRUCTION",
                "body_text": "OLDER_PENDING_MUST_NOT_BE_DISPATCHED",
            },
        )["messages"][0]
        posted = self.server.post_session_bus_message(
            {
                "to": {"terminal_id": terminal["terminal_id"]},
                "from": {
                    "project_id": "universe",
                    "mode": "CONDUCTOR",
                    "provider": "UI",
                },
                "kind": "INSTRUCTION",
                "body_text": "Reply with exactly: HOOK_DISPATCH_CONFIRMED",
            }
        )

        message = posted["messages"][0]
        self.assertEqual("DISPATCHED", message["delivery_state"])
        self.assertEqual("DISPATCHED", message["dispatch_status"])
        self.server.provider_sessions.submit_channel.assert_called_once()
        self.server.terminal_host.write.assert_not_called()
        pending = self.server.session_bus.inbox(
            self.server._session_anchor_terminal_host(),
            terminal_id=terminal["terminal_id"],
        )["messages"]
        older_after = next(
            item for item in pending if item["message_id"] == older["message_id"]
        )
        self.assertEqual("PENDING", older_after["delivery_state"])
        deadline = time.monotonic() + 1.0
        results: list[Mapping[str, Any]] = []
        recipient_anchor_ref = str(message["recipient_anchor_ref"])
        while time.monotonic() < deadline:
            results = self.server.session_bus.inbox(
                self.server._session_anchor_terminal_host(),
                session_anchor_ref=recipient_anchor_ref,
                projection="RESULTS",
            )["messages"]
            if results:
                break
            time.sleep(0.01)
        activity = self.server.session_bus.inbox(
            self.server._session_anchor_terminal_host(),
            terminal_id=terminal["terminal_id"],
            projection="ACTIVITY",
        )["messages"]
        self.assertEqual(1, len(results), activity)
        self.assertEqual("HOOK_DISPATCH_CONFIRMED", results[0]["body_text"])
        self.assertEqual(message["thread_id"], results[0]["thread_id"])
        self.assertEqual(message["message_id"], results[0]["in_reply_to"])

    def test_session_bus_state_and_reply_http_routes_project_results(self) -> None:
        terminal_id = "term-session-bus-result-http-001"
        anchor_ref = "anchor-session-bus-result-http-001"
        terminal = {
            "terminal_id": terminal_id,
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CODEX",
            "state": "LIVE",
        }
        host = Mock()
        host.get.return_value = terminal
        posted = self.server.session_bus.deliver_to_terminal(
            host,
            terminal=terminal,
            to={
                "terminal_id": terminal_id,
                "session_anchor_ref": anchor_ref,
            },
            source={
                "provider": "UI",
                "node_ref": "GCS",
                "task_frame_ref": "task-frame://http-result",
            },
            kind="INSTRUCTION",
            notify="NONE",
            body="return an HTTP result",
        )
        self.server.session_bus.claim_instruction(
            host,
            terminal_id=terminal_id,
            session_anchor_ref=anchor_ref,
        )
        self.server.session_bus.complete_instruction_claim(
            terminal_id=terminal_id,
            message_id=posted["message_id"],
            session_anchor_ref=anchor_ref,
        )

        status, completed = self.request(
            "POST",
            f"/v1/session-bus/messages/{posted['message_id']}/state",
            {
                "state": "COMPLETED",
                "terminal_id": terminal_id,
                "session_anchor_ref": anchor_ref,
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("COMPLETED", completed["lifecycle_state"])
        status, reply = self.request(
            "POST",
            f"/v1/session-bus/messages/{posted['message_id']}/reply",
            {
                "body_text": "HTTP durable result",
                "terminal_id": terminal_id,
                "session_anchor_ref": anchor_ref,
                "result_ref": "artifact://result-001",
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("REPLIED", reply["status"])
        status, projected = self.request(
            "GET",
            "/v1/session-bus/inbox?session_anchor_ref="
            + anchor_ref
            + "&projection=RESULTS",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("HTTP durable result", projected["messages"][0]["body_text"])
        context = projected["messages"][0]["event_context"]
        self.assertEqual(posted["message_id"], context["source_event_id"])
        self.assertEqual(anchor_ref, context["session_anchor_ref"])
        self.assertEqual("task-frame://http-result", context["task_frame_ref"])
        self.assertEqual("GCS", context["node_ref"])
        self.assertEqual(["artifact://result-001"], context["artifact_refs"])
        status, filtered = self.request(
            "GET",
            "/v1/session-bus/inbox?session_anchor_ref="
            + anchor_ref
            + "&projection=RESULTS&event_kind=RESULT"
            + "&lifecycle_state=COMPLETED"
            + "&task_frame_ref=task-frame%3A%2F%2Fhttp-result"
            + "&node_ref=GCS",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual([reply["result"]["message_id"]], [item["message_id"] for item in filtered["messages"]])

    def test_grok_session_bus_reply_lands_on_conductor_inbox(self) -> None:
        master_tid = "term-grok-master-result-001"
        conductor_tid = "term-claude-conductor-result-001"
        master_anchor = "session_anchor_grok_master_result_001"
        conductor_anchor = "session_anchor_claude_conductor_result_001"
        master = {
            "terminal_id": master_tid,
            "project_id": "universe",
            "mode": "MASTER",
            "provider": "GROK",
            "state": "LIVE",
            "session_anchor_ref": master_anchor,
            "active_session_anchor_ref": master_anchor,
        }
        conductor = {
            "terminal_id": conductor_tid,
            "project_id": "universe",
            "mode": "CONDUCTOR",
            "provider": "CLAUDE",
            "state": "LIVE",
            "session_anchor_ref": conductor_anchor,
            "active_session_anchor_ref": conductor_anchor,
        }
        host = Mock()
        host.list_sessions.return_value = [master, conductor]
        host.list_hosts.return_value = []
        host.get.side_effect = lambda terminal_id: (
            master if terminal_id == master_tid else conductor
        )
        self.server.terminal_host = host
        posted = self.server.session_bus.deliver_to_terminal(
            host,
            terminal=master,
            to={
                "project_id": "universe",
                "mode": "MASTER",
                "provider": "GROK",
                "terminal_id": master_tid,
                "session_anchor_ref": master_anchor,
            },
            source={
                "project_id": "universe",
                "mode": "CONDUCTOR",
                "provider": "CLAUDE",
            },
            kind="INSTRUCTION",
            notify="NONE",
            body="report A/B on the Conductor thread",
        )
        self.server.session_bus.claim_instruction(
            host,
            terminal_id=master_tid,
            session_anchor_ref=master_anchor,
        )
        self.server.session_bus.complete_instruction_claim(
            terminal_id=master_tid,
            message_id=posted["message_id"],
            session_anchor_ref=master_anchor,
        )
        status, reply = self.request(
            "POST",
            f"/v1/session-bus/messages/{posted['message_id']}/reply",
            {
                "session_anchor_ref": master_anchor,
                "body_text": "A/B roundtrip RESULT",
                "outcome": "COMPLETED",
            },
            self.token,
        )
        self.assertEqual(201, status, reply)
        self.assertEqual("REPLIED", reply["status"])
        self.assertEqual("RESULT", reply["result"]["kind"])
        self.assertEqual(conductor_anchor, reply["result"]["recipient_anchor_ref"])
        status, inbox = self.request(
            "GET",
            "/v1/session-bus/inbox?session_anchor_ref=" + conductor_anchor,
            token=self.token,
        )
        self.assertEqual(200, status)
        result_ids = [item["message_id"] for item in inbox["messages"] if item.get("kind") == "RESULT"]
        self.assertIn(reply["result"]["message_id"], result_ids)

    def test_catalog_retry_resumes_only_hook_verified_waiting_allocation(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        delegation, created = self.server.store.create_conductor_delegation(
            {
                "project_id": "GCS",
                "summary": "Retry delivery after the verified chat appears in the catalog.",
                "idempotency_key": "allocation-catalog-retry-001",
                "worker_role": "PROJECT_MASTER",
                "target_session_action": "NEW",
                "origin_session_anchor_ref": "session-anchor-conductor-001",
                "origin_session_chat_key": "provider_chat_1234567890abcdef12345678",
            }
        )
        self.assertTrue(created)
        self.server.store.start_conductor_delegation(delegation["delegation_id"])
        self.server.store.update_conductor_delegation_progress(
            delegation["delegation_id"],
            {
                "summary": "Hook verified the target; catalog entry is pending.",
                "step": "WAITING_FOR_VENDOR_CHAT",
                "target_session_anchor_ref": "session-anchor-new-master-001",
            },
        )
        self.server.session_anchor_transport.deliver = Mock(
            return_value={
                "progress": {
                    "summary": "Delivered after catalog visibility was confirmed.",
                    "step": "TARGET_ACCEPTED",
                }
            }
        )

        self.server._resume_hook_verified_conductor_allocations(
            "GCS",
            "session-anchor-new-master-001",
            retry_catalog_visibility=True,
        )

        self.server.session_anchor_transport.deliver.assert_called_once()
        resumed = self.server.store.get_conductor_delegation(
            delegation["delegation_id"]
        )
        self.assertEqual("TARGET_ACCEPTED", resumed["progress"]["step"])

    def test_provider_tail_retries_only_catalog_waiting_anchor(self) -> None:
        self.server.session_supervisor.list_sessions = Mock(
            return_value=[
                {
                    "session_id": "supervisor-new-master-001",
                    "project_id": "GCS",
                    "provider": "CODEX",
                    "provider_session_ref": "vendor-session-001",
                    "session_anchor_ref": "session-anchor-new-master-001",
                }
            ]
        )
        self.server.store.discover_provider_session_sources = Mock(return_value=[])
        self.server.store.scan_registered_provider_sources = Mock(return_value=[])
        self.server._resume_hook_verified_conductor_allocations = Mock()

        scans = self.server.tail_bound_provider_sessions()

        self.assertEqual([], scans)
        self.server._resume_hook_verified_conductor_allocations.assert_called_once_with(
            "GCS",
            "session-anchor-new-master-001",
            retry_catalog_visibility=True,
        )

    def test_semantic_graph_extracts_room_decisions_and_todo_candidates(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        decision, decision_created = self.server.store.create_room_message(
            "GCS",
            {
                "kind": "DECISION",
                "sender": "USER",
                "body": "Use the anchor graph as the currentness source.",
                "idempotency_key": "graph-decision-001",
            },
        )
        candidate, candidate_created = self.server.store.create_room_message(
            "GCS",
            {
                "kind": "TASK_DRAFT",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "Add durable cursors to the automatic collector.",
                "idempotency_key": "graph-todo-candidate-001",
            },
        )
        self.assertTrue(decision_created)
        self.assertTrue(candidate_created)
        graph = self.server.store.semantic_project_graph("GCS")
        decision_nodes = [
            item for item in graph["nodes"] if item["entity_type"] == "ROOM_DECISION"
        ]
        candidate_nodes = [
            item for item in graph["nodes"] if item["entity_type"] == "TODO_CANDIDATE"
        ]
        self.assertEqual(decision["message_id"], decision_nodes[0]["data"]["message_id"])
        self.assertEqual(candidate["message_id"], candidate_nodes[0]["data"]["message_id"])
        self.assertNotIn("body", decision_nodes[0]["data"])
        self.assertEqual(
            "USER_SELECTION_REQUIRED",
            candidate_nodes[0]["data"]["promotion_state"],
        )
        edge_types = {item["edge_type"] for item in graph["edges"]}
        self.assertIn("ROOM_MESSAGE_DERIVES_DECISION", edge_types)
        self.assertIn("ROOM_MESSAGE_DERIVES_TODO_CANDIDATE", edge_types)

    def test_session_inject_hook_becomes_deduplicated_redacted_graph_input(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        body = {
            "project_id": "GCS",
            "node": "GCS",
            "mode": "MASTER",
            "room_type": "PROJECT",
            "slot_role": "MASTER",
            "provider": "CODEX",
            "provider_session_ref": "vendor-session-secret-001",
            "hook_observation": {
                "schema": "universe.hook-session-observation.v1",
                "trigger": "session_start",
                "observed_at": "2026-08-21T00:00:00Z",
            },
        }
        status, first = self.request("POST", "/v1/sessions/inject", body, self.token)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(first["hook_observation"]["created"])
        status, second = self.request("POST", "/v1/sessions/inject", body, self.token)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertFalse(second["hook_observation"]["created"])

        graph = self.server.store.semantic_project_graph("GCS")
        hooks = [item for item in graph["nodes"] if item["entity_type"] == "HOOK_OBSERVATION"]
        self.assertEqual(1, len(hooks))
        hook = hooks[0]
        self.assertEqual("Hook · SESSION_START", hook["label"])
        self.assertEqual("REDACTED", hook["data"]["redaction_state"])
        self.assertNotIn("vendor-session-secret-001", json.dumps(hook))
        self.assertIn(
            "HOOK_OBSERVATION_FOR_SESSION",
            {item["edge_type"] for item in graph["edges"]},
        )

    def test_generic_session_start_preserves_existing_current_mode(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        existing, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "generic-hook-current-conductor",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "generic-hook-provider-ref",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        status, injected = self.request(
            "POST",
            "/v1/sessions/inject",
            {
                "project_id": "GCS",
                "room_type": "PROJECT",
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "generic-hook-provider-ref",
                "state": "STARTING",
                "hook_observation": {
                    "schema": "universe.hook-session-observation.v1",
                    "trigger": "session_start",
                    "observed_at": "2026-08-27T00:00:00Z",
                },
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        supervised = injected["supervisor_session"]
        self.assertEqual(existing["session_id"], supervised["session_id"])
        self.assertEqual("CONDUCTOR", supervised["mode"])
        self.assertEqual(existing["session_anchor_ref"], supervised["session_anchor_ref"])

    def test_cross_session_delegation_fails_closed_without_project_room_delivery(
        self,
    ) -> None:
        record = {
            "delegation_id": "delegation-session-transport-001",
            "project_id": "GCS",
            "request": {
                "worker_role": "PROJECT_MASTER",
                "provider": "AUTO",
                "model_ref": None,
                "summary": "Ask the target session to review the diff.",
                "idempotency_key": "delegation-session-transport-001",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            },
        }
        with patch.object(self.server, "send_project_room_message") as room_send:
            with self.assertRaises(UniverseError) as raised:
                self.server._dispatch_project_master_delegation(record)
        self.assertEqual(
            "ORIGIN_SESSION_ANCHOR_NOT_FOUND", raised.exception.code
        )
        room_send.assert_not_called()

    def test_cross_session_delegation_requires_local_operator_token(self) -> None:
        status, response = self.request(
            "POST",
            "/v1/conductor/delegations",
            {
                "project_id": "GCS",
                "summary": "Do not accept an unauthenticated origin claim",
                "idempotency_key": "delegation-unauthenticated-001",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            },
        )
        self.assertEqual(HTTPStatus.UNAUTHORIZED, status)
        self.assertEqual("CONDUCTOR_DELEGATION_TOKEN_REQUIRED", response["error_code"])
        connection = sqlite3.connect(self.server.store.database_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM conductor_delegation WHERE idempotency_key = ?",
                ("delegation-unauthenticated-001",),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(0, count)

    def test_cross_session_delegation_uses_internal_session_anchor_transport(self) -> None:
        record = {
            "delegation_id": "delegation-session-transport-success-001",
            "project_id": "GCS",
            "request": {
                "worker_role": "PROJECT_MASTER",
                "provider": "AUTO",
                "model_ref": None,
                "summary": "Ask the exact target session to review the diff.",
                "idempotency_key": "delegation-session-transport-success-001",
                "origin_session_anchor_ref": "session_anchor_origin",
                "target_session_anchor_ref": "session_anchor_target",
                "origin_session_chat_key": "provider_chat_aaaaaaaaaaaaaaaaaaaaaaaa",
            },
        }
        transport = Mock()
        transport.deliver.return_value = {
            "progress": {
                "step": "WAITING_FOR_TARGET_SESSION_RESULT",
                "room_queue_used": False,
            }
        }
        self.server.session_anchor_transport = transport
        with patch.object(self.server, "send_project_room_message") as room_send:
            result = self.server._dispatch_project_master_delegation(record)
        self.assertEqual(
            "WAITING_FOR_TARGET_SESSION_RESULT", result["progress"]["step"]
        )
        transport.deliver.assert_called_once_with(
            {
                **record,
                "request": {
                    **record["request"],
                    "target_session_action": "EXISTING",
                },
            }
        )
        room_send.assert_not_called()

    def test_delegation_dispatch_rejects_legacy_record_without_session_lineage(
        self,
    ) -> None:
        record = {
            "delegation_id": "delegation-missing-lineage-001",
            "project_id": "GCS",
            "request": {
                "worker_role": "PROJECT_MASTER",
                "provider": "AUTO",
                "model_ref": None,
                "summary": "Legacy unscoped queue record.",
            },
        }
        with patch.object(self.server, "send_project_room_message") as room_send:
            with self.assertRaises(UniverseError) as raised:
                self.server._dispatch_project_master_delegation(record)
        self.assertEqual(
            "CONDUCTOR_DELEGATION_SESSION_LINEAGE_INVALID", raised.exception.code
        )
        room_send.assert_not_called()

    def test_runtime_audit_retains_hidden_session_anchor_history(self) -> None:
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "hidden-anchor-history",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-hidden-anchor-history",
            }
        )
        self.server.session_supervisor.set_visibility(
            session["session_id"],
            visibility="HIDDEN",
            expected_version=session["row_version"],
        )

        status, audit = self.request("GET", "/v1/runtime/audit", None, self.token)
        self.assertEqual(HTTPStatus.OK, status)
        card = next(
            item
            for item in audit["sessions"]
            if item["session_id"] == "hidden-anchor-history"
        )
        self.assertEqual(session["session_anchor_ref"], card["session_anchor_ref"])
        self.assertEqual("HIDDEN", card["visibility"])

    def test_project_anchor_store_is_source_for_supervisor_projection(self) -> None:
        status, _ = self.request(
            "POST", "/v1/projects/register", self.registration(), self.token
        )
        self.assertIn(status, {HTTPStatus.OK, HTTPStatus.CREATED})
        database = (
            self.project_root
            / ".ai"
            / "runtime"
            / "anchor_store"
            / "mode-master.sqlite3"
        )
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE anchor_snapshot(
                    singleton INTEGER PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    frame_id TEXT NOT NULL,
                    anchor_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE beyond_anchor_footprints(
                    anchor_id TEXT PRIMARY KEY,
                    frame_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    retired_at TEXT NOT NULL,
                    retirement_reason TEXT NOT NULL
                );
                """
            )
            current_snapshot = json.dumps(
                {
                    "observer_session_ref": "claude-code:claude-current",
                    "coordinates": {"mode": "MASTER"},
                }
            )
            beyond_snapshot = json.dumps(
                {
                    "observer_session_ref": "codex-beyond",
                    "coordinates": {"mode": "MASTER"},
                }
            )
            connection.execute(
                "INSERT INTO anchor_snapshot VALUES (1, 1, 'current', ?, 'READY', ?, 'test', ?)",
                ("MASTER-CURRENT-GCS", "2026-08-09T01:00:00Z", current_snapshot),
            )
            connection.execute(
                "INSERT INTO beyond_anchor_footprints VALUES (?, 'current', 'RETIRED', ?, 'test', ?, ?, 'REPLACED')",
                (
                    "MASTER-CURRENT-GCS-OLD",
                    "2026-08-08T01:00:00Z",
                    beyond_snapshot,
                    "2026-08-09T01:00:00Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        discovered = [
            {
                "provider": "CLAUDE",
                "provider_session_id": "claude-current",
                "session_kind": "CHAT",
                "identity_state": "VERIFIED",
            },
            {
                "provider": "CODEX",
                "provider_session_id": "codex-beyond",
                "session_kind": "CHAT",
                "identity_state": "VERIFIED",
            },
        ]
        observations = self.server._project_anchor_observations(discovered)
        self.assertEqual(2, len(observations))
        by_provider = {item["provider"]: item for item in observations}
        self.assertEqual("CURRENT", by_provider["CLAUDE"]["temporality"])
        self.assertEqual("CURRENT", by_provider["CLAUDE"]["anchor_session_currentness"])
        self.assertEqual("BEYOND", by_provider["CODEX"]["temporality"])
        self.assertEqual("PAST", by_provider["CODEX"]["anchor_session_currentness"])
        self.assertEqual(
            "MASTER-CURRENT-GCS", by_provider["CLAUDE"]["anchor_ref"]
        )
        self.assertEqual(
            "MASTER-CURRENT-GCS", by_provider["CODEX"]["anchor_ref"]
        )
        self.assertEqual(
            "MASTER-CURRENT-GCS-OLD",
            by_provider["CODEX"]["observed_anchor_ref"],
        )

        live_codex, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-live-codex",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-beyond",
                "anchor_ref": "MASTER-CURRENT-GCS-OLD",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        before_version = live_codex["row_version"]

        projection = self.server._project_anchor_supervisor_projection(observations)
        self.assertEqual(
            "PROJECT_ANCHORS_OBSERVED_READ_ONLY", projection["status"]
        )
        self.assertEqual(0, projection["projected"])
        after = self.server.session_supervisor.get_session("session-live-codex")
        self.assertEqual(before_version, after["row_version"])
        self.assertEqual("MASTER-CURRENT-GCS-OLD", after["anchor_ref"])
        self.assertEqual("CURRENT", after["currentness"])
        self.assertFalse(
            any(
                item.get("provider_session_ref") == "claude-current"
                for item in self.server.session_supervisor.list_sessions(
                    include_hidden=True
                )
            )
        )

    def test_project_anchor_sessions_list_current_and_recent_from_store(self) -> None:
        status, _ = self.request(
            "POST", "/v1/projects/register", self.registration(), self.token
        )
        self.assertIn(status, {HTTPStatus.OK, HTTPStatus.CREATED})
        runtime_state = self.project_root / ".ai" / "runtime" / "state"
        session_dir = self.project_root / ".ai" / "runtime" / "session_store"
        runtime_state.mkdir(parents=True, exist_ok=True)
        session_dir.mkdir(parents=True, exist_ok=True)
        database = runtime_state / "project_runtime.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE mode_current_anchor (
                    mode TEXT PRIMARY KEY,
                    frame_id TEXT NOT NULL,
                    anchor_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    registry_revision INTEGER NOT NULL,
                    registry_digest TEXT NOT NULL,
                    mode_definition_digest TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE beyond_anchor (
                    mode TEXT NOT NULL,
                    anchor_id TEXT NOT NULL,
                    frame_id TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    retired_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    PRIMARY KEY (mode, anchor_id)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO mode_current_anchor VALUES (
                    'MASTER', 'current', 'MASTER-CURRENT-NOW', 'CURRENT', 'test',
                    '2026-08-17T10:00:00Z', 1, 'digest', 'digest', ?
                )
                """,
                (
                    json.dumps(
                        {
                            "observer_session_ref": "grok-acp:current-vendor",
                            "coordinates": {"mode": "MASTER"},
                            "state": "CURRENT",
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO beyond_anchor VALUES (
                    'MASTER', 'MASTER-CURRENT-OLD-ING', 'current', 'test',
                    '2026-08-16T10:00:00Z', '2026-08-17T10:00:00Z', ?
                )
                """,
                (
                    json.dumps(
                        {
                            "observer_session_ref": "UNIVERSE-MASTER-TEST-001",
                            "session_id": "UNIVERSE-MASTER-TEST-001",
                            "coordinates": {"mode": "MASTER"},
                            "state": "EXECUTING",
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO beyond_anchor VALUES (
                    'MASTER', 'MASTER-CURRENT-OLD-STOP', 'current', 'test',
                    '2026-08-15T10:00:00Z', '2026-08-16T10:00:00Z', ?
                )
                """,
                (
                    json.dumps(
                        {
                            "session_id": "UNIVERSE-MASTER-STOPPED-001",
                            "coordinates": {"mode": "MASTER"},
                            "state": "STOPPED",
                        }
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        def write_session_store(
            session_id: str, anchor_id: str, state: str, snapshot: dict[str, Any]
        ) -> None:
            digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
            path = session_dir / f"session-{digest}.sqlite3"
            store = sqlite3.connect(path)
            try:
                store.execute(
                    """
                    CREATE TABLE anchor_snapshot (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        revision INTEGER NOT NULL,
                        frame_id TEXT NOT NULL,
                        anchor_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        source_ref TEXT NOT NULL,
                        snapshot_json TEXT NOT NULL
                    )
                    """
                )
                store.execute(
                    """
                    INSERT INTO anchor_snapshot VALUES (
                        1, 1, 'current', ?, ?, '2026-08-16T10:00:00Z', 'test', ?
                    )
                    """,
                    (anchor_id, state, json.dumps(snapshot)),
                )
                store.commit()
            finally:
                store.close()

        write_session_store(
            "UNIVERSE-MASTER-TEST-001",
            "MASTER-CURRENT-OLD-ING",
            "EXECUTING",
            {
                "session_id": "UNIVERSE-MASTER-TEST-001",
                "observer_session_ref": "UNIVERSE-MASTER-TEST-001",
                "coordinates": {"mode": "MASTER"},
                "state": "EXECUTING",
            },
        )
        write_session_store(
            "UNIVERSE-MASTER-READY-001",
            "MASTER-SESSION-READY",
            "READY",
            {
                "session_id": "UNIVERSE-MASTER-READY-001",
                "coordinates": {"mode": "MASTER"},
                "state": "READY",
            },
        )
        write_session_store(
            "UNIVERSE-MASTER-STOPPED-001",
            "MASTER-CURRENT-OLD-STOP",
            "STOPPED",
            {
                "session_id": "UNIVERSE-MASTER-STOPPED-001",
                "coordinates": {"mode": "MASTER"},
                "state": "STOPPED",
            },
        )

        status, payload = self.request(
            "GET", "/v1/projects/GCS/anchor-sessions", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROJECT_ANCHOR_SESSIONS_COLLECTED", payload["status"])
        by_id = {item["session_id"]: item for item in payload["sessions"]}
        current = next(
            item for item in payload["sessions"] if item["currentness"] == "CURRENT"
        )
        self.assertEqual("MASTER-CURRENT-NOW", current["current_anchor_ref"])
        self.assertEqual("CURRENT", current["temporality"])
        self.assertEqual("MODE_CURRENT_ANCHOR", current["currentness_source"])
        self.assertIn("UNIVERSE-MASTER-TEST-001", by_id)
        self.assertEqual("PAST", by_id["UNIVERSE-MASTER-TEST-001"]["currentness"])
        self.assertEqual("BEYOND", by_id["UNIVERSE-MASTER-TEST-001"]["temporality"])
        self.assertEqual(
            "SESSION_STORE",
            by_id["UNIVERSE-MASTER-TEST-001"]["currentness_source"],
        )
        self.assertTrue(by_id["UNIVERSE-MASTER-TEST-001"]["active_ing"])
        self.assertEqual("EXECUTING", by_id["UNIVERSE-MASTER-TEST-001"]["state"])
        self.assertIn("UNIVERSE-MASTER-READY-001", by_id)
        self.assertFalse(by_id["UNIVERSE-MASTER-READY-001"]["active_ing"])
        self.assertEqual("SESSION", by_id["UNIVERSE-MASTER-READY-001"]["temporality"])
        self.assertIn("UNIVERSE-MASTER-STOPPED-001", by_id)
        self.assertFalse(by_id["UNIVERSE-MASTER-STOPPED-001"]["active_ing"])
        self.assertEqual("BEYOND", by_id["UNIVERSE-MASTER-STOPPED-001"]["temporality"])
        catalog = self.server.provider_chat_catalog()
        self.assertTrue(
            any(
                item["session_id"] == "UNIVERSE-MASTER-TEST-001"
                for item in catalog["anchor_sessions"]
            )
        )
        rendered = json.dumps(catalog)
        self.assertNotIn("provider_session_id", rendered)
        self.assertNotIn("provider_session_ref", rendered)


    def test_tail_auto_registers_only_bound_verified_provider_sessions(self) -> None:
        bound_path = self.temp_root / "rollout-bound-tail.jsonl"
        bound_path.write_text(
            json.dumps({"type": "turn_started", "message": "historical private"})
            + "\n",
            encoding="utf-8",
        )
        unbound_path = self.temp_root / "rollout-unbound-tail.jsonl"
        unbound_path.write_text(
            json.dumps({"type": "turn_started", "message": "unbound private"})
            + "\n",
            encoding="utf-8",
        )
        status, _ = self.request(
            "POST",
            "/v1/supervisor/sessions",
            {
                "session_id": "session-bound-tail",
                "node": "universe",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-bound-tail",
                "anchor_ref": "MASTER-CURRENT-TAIL-001",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)

        def discovered(provider: str) -> list[dict[str, Any]]:
            if provider != "CODEX":
                return []
            return [
                {
                    "schema": "universe.provider-session-source.v1",
                    "status": "DISCOVERED",
                    "provider": "CODEX",
                    "provider_session_id": "codex-bound-tail",
                    "source_path": str(bound_path),
                    "source_kind": "CODEX_ROLLOUT_JSONL",
                    "source_version": "v1",
                    "last_modified_at": "2026-08-09T00:00:00Z",
                    "workspace": r"C:\workspace\universe",
                    "workspace_name": "universe",
                    "display_name": "Bound live tail",
                    "session_kind": "CHAT",
                    "parent_provider_session_id": None,
                    "identity_state": "VERIFIED",
                    "transcript_content": "EXCLUDED",
                },
                {
                    "schema": "universe.provider-session-source.v1",
                    "status": "DISCOVERED",
                    "provider": "CODEX",
                    "provider_session_id": "codex-unbound-tail",
                    "source_path": str(unbound_path),
                    "source_kind": "CODEX_ROLLOUT_JSONL",
                    "source_version": "v1",
                    "last_modified_at": "2026-08-09T00:00:00Z",
                    "workspace": r"C:\workspace\universe",
                    "workspace_name": "universe",
                    "display_name": "Unbound live tail",
                    "session_kind": "CHAT",
                    "parent_provider_session_id": None,
                    "identity_state": "VERIFIED",
                    "transcript_content": "EXCLUDED",
                },
            ]

        with (
            patch.object(
                self.server.store,
                "discover_provider_session_sources",
                side_effect=discovered,
            ),
            patch.object(
                self.server,
                "_project_anchor_observations",
                return_value=[],
            ),
            patch.object(
                self.server,
                "list_all_project_anchor_sessions",
                return_value=[],
            ),
        ):
            status, first = self.request("POST", "/v1/session-observer/tail", {})
            self.assertEqual(HTTPStatus.OK, status)
            self.assertEqual(1, len(first["scans"]))
            self.assertEqual(0, first["scans"][0]["added"])

            with bound_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"type": "turn_completed", "text": "new private"})
                    + "\n"
                )
            status, second = self.request("POST", "/v1/session-observer/tail", {})

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(second["scans"]))
        self.assertEqual(1, second["scans"][0]["added"])
        observed_session = self.server.session_supervisor.get_session(
            "session-bound-tail"
        )
        self.assertEqual("CURRENT", observed_session["currentness"])
        self.assertEqual("COMPLETED", observed_session["current_activity_state"])
        self.assertIn(
            "PROVIDER_ACTIVITY_OBSERVED",
            {
                item["event_type"]
                for item in self.server.session_supervisor.list_events(limit=20)
            },
        )
        registered = self.server.store.list_provider_session_sources()
        self.assertEqual(1, len(registered))
        self.assertEqual("codex-bound-tail", registered[0]["provider_session_id"])
        rendered = json.dumps(second)
        self.assertNotIn(str(bound_path), rendered)
        self.assertNotIn(str(unbound_path), rendered)
        self.assertNotIn("codex-bound-tail", rendered)
        self.assertNotIn("codex-unbound-tail", rendered)
        self.assertNotIn("private", rendered)

    def test_linked_project_master_result_advances_only_its_todo(self) -> None:
        self.server.store.register_project(self.registration())
        todo = self.server.store.create_todo(
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Exercise Master result transition",
                "detail": "",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 0,
            }
        )
        message, created = self.server.store.create_room_message(
            "GCS",
            {
                "kind": "TASK_DRAFT",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "Run the linked task.",
                "todo_id": todo["todo_id"],
                "idempotency_key": "linked-master-todo-1",
            },
        )
        self.assertTrue(created)
        delivered = self.server.store.apply_master_message_todo_transition(
            "GCS", message["message_id"], outcome="DELIVERED"
        )
        self.assertEqual("IN_PROGRESS", delivered["state"])
        completed = self.server.store.apply_master_message_todo_transition(
            "GCS", message["message_id"], outcome="COMPLETED"
        )
        self.assertEqual("DONE", completed["state"])
        self.assertEqual("DONE", self.server.store.get_todo(todo["todo_id"])["state"])
        repeated = self.server.store.apply_master_message_todo_transition(
            "GCS", message["message_id"], outcome="COMPLETED"
        )
        self.assertEqual("TODO_TRANSITION_NOT_REQUIRED", repeated["status"])
        stale_delivery = self.server.store.apply_master_message_todo_transition(
            "GCS", message["message_id"], outcome="DELIVERED"
        )
        self.assertEqual("TODO_TRANSITION_NOT_REQUIRED", stale_delivery["status"])
        stale_failure = self.server.store.apply_master_message_todo_transition(
            "GCS", message["message_id"], outcome="FAILED"
        )
        self.assertEqual("TODO_TRANSITION_NOT_REQUIRED", stale_failure["status"])

        failure_todo = self.server.store.create_todo(
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Exercise Master failure transition",
                "detail": "",
                "priority": "P1",
                "state": "READY",
                "source_kind": "USER",
                "sort_order": 1,
            }
        )
        failure_message, _ = self.server.store.create_room_message(
            "GCS",
            {
                "kind": "TASK_DRAFT",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "Run the linked failure task.",
                "todo_id": failure_todo["todo_id"],
                "idempotency_key": "linked-master-todo-failure-1",
            },
        )
        self.server.store.apply_master_message_todo_transition(
            "GCS", failure_message["message_id"], outcome="DELIVERED"
        )
        failed = self.server.store.apply_master_message_todo_transition(
            "GCS", failure_message["message_id"], outcome="FAILED"
        )
        self.assertEqual("BLOCKED", failed["state"])
        repeated_failure = self.server.store.apply_master_message_todo_transition(
            "GCS", failure_message["message_id"], outcome="FAILED"
        )
        self.assertEqual("TODO_TRANSITION_NOT_REQUIRED", repeated_failure["status"])
        stale_after_failure = self.server.store.apply_master_message_todo_transition(
            "GCS", failure_message["message_id"], outcome="DELIVERED"
        )
        self.assertEqual("TODO_TRANSITION_NOT_REQUIRED", stale_after_failure["status"])

        unlinked, _ = self.server.store.create_room_message(
            "GCS",
            {
                "kind": "STATUS",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "No Todo relation.",
                "idempotency_key": "unlinked-master-todo-1",
            },
        )
        result = self.server.store.apply_master_message_todo_transition(
            "GCS", unlinked["message_id"], outcome="COMPLETED"
        )
        self.assertEqual("TODO_NOT_LINKED", result["status"])

    def test_server_state_carries_the_database_universe_identity(self) -> None:
        identity = self.server.store.identity()
        state_path = self.temp_root / "server.json"

        write_server_state(
            state_path,
            endpoint=self.endpoint,
            token=self.token,
            database_path=self.server.store.database_path,
            universe_identity=identity,
        )

        self.assertEqual(identity, load_server_state(state_path)["universe"])
        self.assertEqual(
            "Universe Server",
            load_server_state(state_path)["program"]["name"],
        )

    def test_desktop_ui_is_public_static_but_api_data_is_not_embedded(self) -> None:
        with urlopen(self.endpoint + "/", timeout=5) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(200, response.status)
            self.assertIn("Universe", body)
            self.assertNotIn(self.token, body)
            self.assertEqual("DENY", response.headers["X-Frame-Options"])
            self.assertIn(
                "default-src 'self'",
                response.headers["Content-Security-Policy"],
            )
            self.assertIn('id="fresh-project-dialog"', body)
            self.assertIn('id="start-project-button"', body)
            self.assertIn('id="planning-provider"', body)
            self.assertIn('id="execute-planning-proposal"', body)
            self.assertIn('id="refinement-comparison"', body)
            self.assertIn('id="mode-status"', body)
            self.assertIn('id="composer-action-button"', body)
            self.assertIn('id="project-master-actions"', body)
            self.assertIn('id="settings-dialog"', body)
            self.assertIn('id="provider-model-catalog"', body)
            self.assertIn('id="refresh-provider-models-button"', body)
            self.assertIn('id="worker-binding-scope"', body)
            self.assertIn('id="worker-binding-settings"', body)
            self.assertIn('id="host-tool-settings"', body)
            self.assertIn('id="runtime-preflight-summary"', body)
            self.assertIn('id="runtime-preflight-list"', body)
            self.assertIn('id="remote-access-status"', body)
            self.assertIn('id="create-pairing-button"', body)
            self.assertIn('id="remote-device-list"', body)
            self.assertIn('id="discover-host-tools-button"', body)
            self.assertIn('id="session-observatory-dialog"', body)
            self.assertIn('id="session-observatory-topbar-button"', body)
            self.assertIn('id="session-observatory-list"', body)
            self.assertIn('id="session-rail-list"', body)
            self.assertIn('id="session-rail-search"', body)
            self.assertIn('id="session-rail-show-workers"', body)
            self.assertIn(">Sessions<", body)
            self.assertIn('id="session-rail-show-hidden"', body)
            self.assertIn("work-spine-", body)
            self.assertIn('id="runtime-audit-grid"', body)
            self.assertIn('id="session-event-list"', body)
        with urlopen(self.endpoint + "/app.js", timeout=5) as response:
            script = response.read().decode("utf-8")
            self.assertEqual(200, response.status)
            self.assertIn("/v1/projects", script)
            self.assertIn("/v1/future-paths", script)
            self.assertIn("/v1/fresh-project-compositions", script)
            self.assertIn("/v1/fresh-project-refinement-requests", script)
            self.assertIn("/v1/runtime/planning-binding", script)
            self.assertIn("/v1/conductor-room/messages", script)
            self.assertIn("/v1/fresh-project-refinement-runs", script)
            self.assertIn("/execute", script)
            self.assertIn("/v1/fresh-project-refinement-adoptions", script)
            self.assertIn("/v1/fresh-project-composition-adoptions", script)
            self.assertIn("UNIVERSE_CONDUCTOR", script)
            self.assertIn("callProjectMaster", script)
            self.assertIn("Review project draft", script)
            self.assertIn("openConductorFreshProjectDraft", script)
            self.assertIn("/v1/settings/providers", script)
            self.assertIn("/v1/settings/worker-bindings", script)
            self.assertIn("renderWorkerBindingSettings", script)
            self.assertIn("/v1/settings/host-tools", script)
            self.assertIn("/v1/runtime/preflight", script)
            self.assertIn("/v1/runtime/audit", script)
            self.assertIn("group_by=worker", script)
            self.assertIn("renderRuntimePreflight", script)
            self.assertIn("renderRuntimeAudit", script)
            self.assertIn("renderSessionRail", script)
            self.assertIn("/v1/session-observer/chat-rooms", script)
            self.assertIn("providerChatShowWorkers", script)
            self.assertIn("currentAnchorLabel", script)
            self.assertIn("dedupeRoomMessages", script)
            self.assertIn("(?:AUTO|CODEX|CLAUDE|GROK)", script)
            self.assertIn("/v1/settings/remote-access", script)
            self.assertIn("renderRemoteAccessSettings", script)
            self.assertIn("SSH_REVERSE_TUNNEL", script)
            self.assertIn("remote-public-url", body)
            self.assertIn("remote-identity-file", body)
            self.assertIn("requestedPermissionSummary", script)
            self.assertIn(
                "/v1/conductor-room/agent-session/permissions/",
                script,
            )
            self.assertIn("/v1/settings/provider-models", script)
            self.assertIn("/v1/actions", script)
            self.assertIn('invokeServerAction("session.resume"', script)
            self.assertIn('target: "PROJECT_MASTER"', script)
            self.assertIn('target: "UNIVERSE_CONDUCTOR"', script)
            self.assertNotIn("prepareBody.project_id", script)
            self.assertNotIn("prepareBody.cwd", script)
            self.assertNotIn("prepareBody.requested_mode", script)
            self.assertIn('state.modeContract?.mode === "CONDUCTOR"', script)
            self.assertIn("sessionConnectionText", script)
            self.assertIn("/v1/supervisor/sessions", script)
            self.assertIn("refreshSupervisorSessions", script)
            self.assertIn("/v1/session-graph", script)
            self.assertNotIn(self.token, script)
        with urlopen(self.endpoint + "/styles.css", timeout=5) as response:
            styles = response.read().decode("utf-8")
            self.assertEqual(200, response.status)
            self.assertIn(
                ".app-shell.mockup-shell > .status-bar { grid-column: 1; grid-row: 4; }",
                styles,
            )

    def test_remote_access_control_is_local_operator_only(self) -> None:
        status, current = self.request("GET", "/v1/settings/remote-access")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("OFFLINE", current["gateway"]["status"])

        status, blocked = self.request(
            "POST",
            "/v1/settings/remote-access/pairings",
            {"ttl_seconds": 600},
            extra_headers={"X-Universe-Access-Surface": "REMOTE_BROWSER"},
        )
        self.assertEqual(HTTPStatus.FORBIDDEN, status)
        self.assertEqual("LOCAL_OPERATOR_REQUIRED", blocked["error_code"])

    def test_internet_access_starts_loopback_gateway_then_ssh_connector(self) -> None:
        identity = self.temp_root / "universe_ed25519"
        known_hosts = self.temp_root / "known_hosts"
        identity.write_text("private key placeholder", encoding="utf-8")
        known_hosts.write_text("known host placeholder", encoding="utf-8")
        body = {
            "transport_kind": "SSH_REVERSE_TUNNEL",
            "public_base_url": "https://universe.example.test",
            "ssh_host": "server.example.test",
            "ssh_port": 22,
            "ssh_user": "universe-tunnel",
            "remote_port": 18443,
            "identity_file": str(identity),
            "known_hosts_file": str(known_hosts),
        }
        gateway = {
            "schema": "universe.remote-gateway.v1",
            "status": "READY",
            "listen_host": "127.0.0.1",
            "port": 52742,
            "control_endpoint": "http://127.0.0.1:52742",
            "public_base_url": body["public_base_url"],
            "control_token": "redacted",
        }
        connector = {
            "schema": "universe.remote-connector.v1",
            "status": "READY",
            "transport_kind": "SSH_REVERSE_TUNNEL",
            "public_base_url": body["public_base_url"],
        }
        with (
            patch("universe_server.gateway_status", return_value={"status": "OFFLINE"}),
            patch(
                "universe_server.connector_status", return_value={"status": "OFFLINE"}
            ),
            patch(
                "universe_server.start_gateway", return_value=gateway
            ) as start_gateway,
            patch(
                "universe_server.start_connector", return_value=connector
            ) as start_connector,
        ):
            status, result = self.request(
                "POST", "/v1/settings/remote-access/start", body
            )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("REMOTE_ACCESS_STARTED", result["status"])
        self.assertEqual("READY", result["connector"]["status"])
        self.assertNotIn("control_token", result["gateway"])
        self.assertEqual("127.0.0.1", start_gateway.call_args.kwargs["listen_host"])
        self.assertEqual(
            "http://127.0.0.1:52742",
            start_connector.call_args.kwargs["gateway_endpoint"],
        )
        self.assertEqual(
            "SSH_REVERSE_TUNNEL",
            start_connector.call_args.kwargs["config"]["transport_kind"],
        )

    def test_room_transcripts_accept_full_provider_replies(self) -> None:
        long_body = "section\n" + ("provider transcript line\n" * 700)
        self.assertGreater(len(long_body), 12000)
        project_message = normalize_room_message(
            "GCS",
            {
                "kind": "RESULT",
                "body": long_body,
                "sender": "PROJECT_MASTER",
                "idempotency_key": "long-project-master-reply",
            },
        )
        conductor_message = normalize_conductor_room_message(
            {
                "kind": "RESULT",
                "body": long_body,
                "sender": "UNIVERSE_CONDUCTOR",
                "provider": "CLAUDE",
                "idempotency_key": "long-conductor-reply",
            }
        )
        self.assertEqual(long_body.strip(), project_message["body"])
        self.assertEqual(long_body.strip(), conductor_message["body"])

    def test_room_transcripts_reject_unbounded_provider_replies(self) -> None:
        too_large = "x" * 200001
        with self.assertRaisesRegex(UniverseError, "body is too long"):
            normalize_room_message(
                "GCS",
                {
                    "kind": "RESULT",
                    "body": too_large,
                    "sender": "PROJECT_MASTER",
                    "idempotency_key": "oversize-project-master-reply",
                },
            )
        with self.assertRaisesRegex(UniverseError, "body is too long"):
            normalize_conductor_room_message(
                {
                    "kind": "RESULT",
                    "body": too_large,
                    "sender": "UNIVERSE_CONDUCTOR",
                    "provider": "CLAUDE",
                    "idempotency_key": "oversize-conductor-reply",
                }
            )
    def test_conductor_fresh_project_draft_is_partial_and_review_only(self) -> None:
        action = normalize_conductor_ui_action(
            {
                "kind": "FRESH_PROJECT_DRAFT",
                "project": "",
                "goal": "Coordinate several development projects.",
                "target_users": "",
                "technologies": ["python", "sqlite"],
                "constraints": ["local-first"],
            }
        )

        self.assertEqual("FRESH_PROJECT_DRAFT", action["kind"])
        self.assertEqual("", action["intent"]["project"])
        self.assertEqual("", action["intent"]["kind"])
        self.assertEqual(
            ["python", "sqlite"],
            action["intent"]["technologies"],
        )
        self.assertEqual([], self.server.store.list_fresh_project_compositions())

    def test_host_tool_settings_are_discoverable_and_verifiable(self) -> None:
        status, profile = self.request("GET", "/v1/settings/host-tools")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("ai-career.host-profile.v1", profile["schema"])
        self.assertEqual(
            str((self.temp_root / "host.json").resolve()),
            profile["profile_path"],
        )
        self.assertEqual("AVAILABLE", profile["tools"]["python"]["status"])

        status, discovered = self.request(
            "POST",
            "/v1/settings/host-tools/discover",
            {},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("HOST_PROFILE_READY", discovered["status"])

        status, selected = self.request(
            "POST",
            "/v1/settings/host-tools/python/select",
            {"executable": sys.executable},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AVAILABLE", selected["tools"]["python"]["status"])

        status, verified = self.request(
            "POST",
            "/v1/settings/host-tools/python/verify",
            {},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AVAILABLE", verified["tools"]["python"]["status"])

        status, model_selected = self.request(
            "POST",
            "/v1/settings/host-tools/claude/model",
            {"model": "opus"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("opus", model_selected["tools"]["claude"]["model"])

    def test_cli_provider_settings_default_to_auto_and_persist(self) -> None:
        class FakeRuntimeHost:
            @staticmethod
            def provider_capabilities() -> list[dict[str, str]]:
                return [
                    {"provider": "GROK", "status": "AVAILABLE"},
                    {
                        "provider": "CODEX",
                        "status": "AVAILABLE",
                        "model": "gpt-5.6-luna",
                    },
                ]

            @staticmethod
            def provider_capability(provider: str) -> dict[str, str]:
                return next(
                    item
                    for item in FakeRuntimeHost.provider_capabilities()
                    if item["provider"] == provider
                )

        self.server.runtime_host = FakeRuntimeHost()
        self.request("POST", "/v1/projects/register", self.registration())

        status, defaults = self.request("GET", "/v1/settings/providers")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AUTO", defaults["universe_conductor"]["provider"])
        self.assertEqual(
            "AUTO",
            defaults["project_masters"][0]["provider"],
        )
        self.assertEqual("", defaults["project_masters"][0]["model_ref"])
        self.assertEqual("AUTO", defaults["universe_conductor"]["effort"])
        self.assertEqual("AUTO", defaults["project_masters"][0]["effort"])
        self.assertEqual("GROK", defaults["universe_conductor"]["resolved_provider"])
        self.assertEqual(
            "NOT_OPENED",
            defaults["universe_conductor"]["session_connection"]["connection_state"],
        )
        self.assertEqual(
            "CONDUCTOR",
            defaults["universe_conductor"]["session_connection"]["requested_mode"],
        )
        self.assertNotIn(
            "authority",
            defaults["universe_conductor"]["session_connection"],
        )
        self.assertNotIn(
            "currentness",
            defaults["universe_conductor"]["session_connection"],
        )

        conductor_session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "sticky-conductor-codex",
                "node": "CONDUCTOR",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "codex-sticky-session",
                "anchor_ref": "CONDUCTOR-CURRENT-STICKY",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        self.server.session_supervisor.set_default(
            conductor_session["session_id"], expected_pointer_version=0
        )
        sticky = self.server.provider_settings()
        self.assertEqual(
            "CODEX", sticky["universe_conductor"]["resolved_provider"]
        )

        status, universe = self.request(
            "POST",
            "/v1/settings/providers/universe",
            {"provider": "CODEX", "model_ref": "gpt-test", "effort": "HIGH"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("CODEX", universe["setting"]["provider"])
        self.assertEqual("gpt-test", universe["setting"]["model_ref"])
        self.assertEqual("HIGH", universe["setting"]["effort"])
        status, project = self.request(
            "POST",
            "/v1/projects/GCS/provider-setting",
            {"provider": "GROK", "model_ref": "grok-4.5", "effort": "MAX"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GROK", project["setting"]["provider"])
        self.assertEqual("grok-4.5", project["setting"]["model_ref"])
        self.assertEqual("MAX", project["setting"]["effort"])

        status, switched = self.request(
            "POST",
            "/v1/projects/GCS/provider-setting",
            {"provider": "CLAUDE"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("CLAUDE", switched["setting"]["provider"])
        self.assertEqual(
            "claude-sonnet-5", switched["setting"]["model_ref"]
        )

        status, explicit = self.request(
            "POST",
            "/v1/projects/GCS/provider-setting",
            {"provider": "CLAUDE", "model_ref": "custom-claude-model"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("custom-claude-model", explicit["setting"]["model_ref"])

        status, bare_mismatch = self.request(
            "POST",
            "/v1/projects/GCS/provider-setting",
            {"provider": "CLAUDE", "model_ref": "gpt-5.6-luna"},
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual(
            "PROVIDER_MODEL_PROVIDER_MISMATCH", bare_mismatch["error_code"]
        )

        status, mismatch = self.request(
            "POST",
            "/v1/projects/GCS/provider-setting",
            {
                "provider": "CLAUDE",
                "model_ref": "provider://CODEX/model/gpt-test",
            },
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual("PROVIDER_MODEL_PROVIDER_MISMATCH", mismatch["error_code"])

        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            "CODEX",
            reopened.provider_setting(
                "UNIVERSE_CONDUCTOR",
                "CONDUCTOR",
            )["provider"],
        )
        self.assertEqual(
            "CLAUDE",
            reopened.provider_setting("PROJECT_MASTER", "GCS")["provider"],
        )
        self.assertEqual(
            "custom-claude-model",
            reopened.provider_setting("PROJECT_MASTER", "GCS")["model_ref"],
        )
        self.assertEqual(
            "HIGH",
            reopened.provider_setting(
                "UNIVERSE_CONDUCTOR", "CONDUCTOR"
            )["effort"],
        )
        self.assertEqual(
            "MAX",
            reopened.provider_setting("PROJECT_MASTER", "GCS")["effort"],
        )

    def test_room_messages_drop_platform_ambient_context_before_persistence(self) -> None:
        ambient = (
            '<in-app-browser-context source="ambient-ui-state">'
            "hidden host metadata"
            "</in-app-browser-context>"
        )
        project_message = normalize_room_message(
            "GCS",
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": f"Keep this\n{ambient}",
                "idempotency_key": "ambient-project-message-001",
            },
        )
        conductor_message = normalize_conductor_room_message(
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": f"Keep this\n{ambient}",
                "idempotency_key": "ambient-conductor-message-001",
            }
        )
        self.assertEqual("Keep this", project_message["body"])
        self.assertEqual("Keep this", conductor_message["body"])
        self.assertNotIn("in-app-browser-context", project_message["body"])
        self.assertNotIn("in-app-browser-context", conductor_message["body"])

    def test_conductor_host_is_lazy_and_setting_changes_require_prepare(self) -> None:
        fake_host = Mock()
        fake_host.prepare.return_value = {
            "schema": "universe.provider-session-connection.v1",
            "target_kind": "UNIVERSE_CONDUCTOR",
            "target_id": "CONDUCTOR",
            "requested_mode": "CONDUCTOR",
            "last_provider": "CODEX",
            "last_session_ref": "codex:conductor",
            "model_ref": "gpt-test",
            "effort": "HIGH",
            "connection_state": "NEW",
            "session_persistence": "LAST_COORDINATE",
            "resident": True,
        }
        with (
            patch(
                "universe_server.ResidentModeSessionHost",
                return_value=fake_host,
            ) as host_factory,
            patch.object(
                self.server,
                "_resolve_conductor_provider",
                return_value="CODEX",
            ),
        ):
            self.assertIsNone(self.server.conductor_session_host)
            prepared = self.server.prepare_conductor_session(
                {"provider": "CODEX", "model_ref": "gpt-test", "effort": "HIGH"}
            )
            self.assertTrue(prepared["resident"])
            host_factory.assert_called_once()
            fake_host.prepare.assert_called_once_with(
                "CODEX", model="gpt-test", effort="HIGH"
            )

            changed = self.server.set_universe_provider_setting(
                {"provider": "GROK"}
            )
            self.assertEqual("PREPARE_REQUIRED", changed["resident_host"])
            self.assertEqual(
                "NOT_OPENED",
                changed["session_connection"]["connection_state"],
            )
            fake_host.close.assert_called_once()
            self.assertIsNone(self.server.conductor_session_host)

    def test_detached_conductor_can_lazily_bind_planning_runtime(self) -> None:
        class FakeRuntime:
            def __init__(self, _root: Path) -> None:
                self.start_count = 0
                self.stop_count = 0

            def start(self) -> dict[str, str]:
                self.start_count += 1
                return {
                    "schema": "universe.planning-runtime-binding.v1",
                    "endpoint": "http://127.0.0.1:41991",
                    "token": "runtime-token",
                    "session_id": "conductor-runtime-session",
                    "origin_anchor_ref": "conductor-anchor",
                    "origin_frame_id": "conductor-frame",
                    "parent_actor_ref": "universe-conductor",
                    "parent_evidence_ref": "host://conductor/current",
                    "binding_evidence_ref": "host://conductor/runtime",
                    "runtime_currentness_observation": "CURRENT",
                }

            def stop(self) -> None:
                self.stop_count += 1

        runtime = FakeRuntime(self.project_root)
        self.server._conductor_runtime_factory = lambda _root: runtime

        self.assertEqual("UNBOUND", self.server.planning_binding_status()["status"])
        first = self.server._ensure_conductor_planning_runtime()
        second = self.server._ensure_conductor_planning_runtime()

        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(1, runtime.start_count)
        self.assertEqual("BOUND", self.server.planning_binding_status()["status"])

    def test_conductor_new_session_action_reaches_resident_host(self) -> None:
        fake_host = Mock()
        fake_host.prepare.return_value = {
            "schema": "universe.provider-session-connection.v1",
            "target_kind": "UNIVERSE_CONDUCTOR",
            "target_id": "CONDUCTOR",
            "requested_mode": "CONDUCTOR",
            "last_provider": "CODEX",
            "last_session_ref": "codex:new-conductor-session",
            "model_ref": "",
            "effort": "AUTO",
            "connection_state": "NEW",
            "session_persistence": "LAST_COORDINATE",
            "resident": True,
        }
        with (
            patch(
                "universe_server.ResidentModeSessionHost",
                return_value=fake_host,
            ),
            patch.object(
                self.server,
                "_resolve_conductor_provider",
                return_value="CODEX",
            ),
        ):
            prepared = self.server.prepare_conductor_session(
                {
                    "session_action": "NEW",
                    "project_id": "universe",
                    "cwd": str(ROOT),
                    "requested_mode": "CONDUCTOR",
                }
            )

        self.assertEqual("NEW", prepared["connection_state"])
        self.assertEqual(
            {
                "project_id": "universe",
                "cwd": str(ROOT),
                "requested_mode": "CONDUCTOR",
            },
            prepared["session_coordinates"],
        )
        fake_host.prepare.assert_called_once_with(
            "CODEX",
            model="",
            effort="AUTO",
            session_action="NEW",
        )

    def test_new_conductor_session_requires_explicit_coordinates(self) -> None:
        prepared = self.server.prepare_conductor_session(
            {"session_action": "NEW"}
        )

        self.assertEqual("UNAVAILABLE", prepared["connection_state"])
        self.assertEqual("SESSION_PROJECT_ID_REQUIRED", prepared["error_code"])

    def test_new_master_session_requires_matching_project_coordinates(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        prepared = self.server.prepare_project_master_session(
            "GCS",
            {
                "session_action": "NEW",
                "project_id": "GCS",
                "cwd": str(self.project_root),
                "requested_mode": "MASTER",
            },
        )
        self.assertEqual(
            {
                "project_id": "GCS",
                "cwd": str(self.project_root),
                "requested_mode": "MASTER",
            },
            prepared["session_coordinates"],
        )

        with self.assertRaises(UniverseError) as raised:
            self.server.prepare_project_master_session(
                "GCS",
                {
                    "session_action": "NEW",
                    "project_id": "GCS",
                    "cwd": str(self.temp_root),
                    "requested_mode": "MASTER",
                },
            )
        self.assertEqual("SESSION_CWD_MISMATCH", raised.exception.code)

    def test_session_new_action_resolves_project_coordinates_server_side(self) -> None:
        self.server.store.register_project(self.registration())
        terminal_host = Mock()
        terminal_host.find_live.return_value = None
        terminal_host.create.return_value = {
            "terminal_id": "term_action_new",
            "state": "LIVE",
        }
        self.server.terminal_host = terminal_host

        status, result = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "session.new",
                "request": {
                    "target": "PROJECT_MASTER",
                    "project_id": "GCS",
                    "provider": "CODEX",
                    "model_ref": "",
                    "effort": "AUTO",
                },
            },
            self.token,
        )

        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("SESSION_NEW_COMPLETED", result["status"])
        self.assertEqual("session.new", result["action_id"])
        self.assertEqual("term_action_new", result["terminal"]["terminal_id"])
        call = terminal_host.create.call_args.kwargs
        self.assertEqual("GCS", call["project_id"])
        self.assertEqual("MASTER", call["mode"])
        self.assertEqual(str(self.project_root), call["cwd"])
        self.assertEqual("CODEX", call["provider"])

    def test_session_new_action_resolves_conductor_coordinates_server_side(self) -> None:
        terminal_host = Mock()
        terminal_host.find_live.return_value = None
        terminal_host.create.return_value = {
            "terminal_id": "term_action_new_conductor",
            "state": "LIVE",
        }
        self.server.terminal_host = terminal_host

        result = self.server.execute_action(
            "session.new",
            {"target": "UNIVERSE_CONDUCTOR", "provider": "CODEX"},
            source="TEST",
        )

        self.assertEqual("SESSION_NEW_COMPLETED", result["status"])
        self.assertEqual("term_action_new_conductor", result["terminal"]["terminal_id"])
        call = terminal_host.create.call_args.kwargs
        self.assertEqual("universe", call["project_id"])
        self.assertEqual("CONDUCTOR", call["mode"])
        self.assertEqual(str(ROOT), call["cwd"])
        self.assertEqual("CODEX", call["provider"])

    def test_session_resume_action_resolves_supervisor_coordinate(self) -> None:
        self.server.store.register_project(self.registration())
        supervised, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session_action_resume",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-app-server:vendor-thread-action",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
            }
        )
        terminal_host = Mock()
        terminal_host.find_live.return_value = None
        terminal_host.create.return_value = {
            "terminal_id": "term_action_resume",
            "state": "LIVE",
        }
        self.server.terminal_host = terminal_host

        result = self.server.execute_action(
            "session.resume",
            {
                "target": "CLI_TERMINAL",
                "supervisor_session_id": supervised["session_id"],
                "provider": "CODEX",
            },
            source="TEST",
        )

        self.assertEqual("SESSION_RESUME_COMPLETED", result["status"])
        self.assertEqual("session.resume", result["action_id"])
        self.assertEqual("term_action_resume", result["terminal"]["terminal_id"])
        call = terminal_host.create.call_args.kwargs
        self.assertEqual("GCS", call["project_id"])
        self.assertEqual("MASTER", call["mode"])
        self.assertEqual("vendor-thread-action", call["resume_session_ref"])

    def test_session_resume_action_routes_project_master_prepare(self) -> None:
        prepared = {
            "schema": "universe.project-master-session-prepared.v1",
            "session_connection": {"connection_state": "READY"},
        }
        with patch.object(
            self.server,
            "prepare_project_master_session",
            return_value=prepared,
        ) as prepare:
            result = self.server.execute_action(
                "session.resume",
                {
                    "target": "PROJECT_MASTER",
                    "project_id": "GCS",
                    "provider": "CODEX",
                    "model_ref": "provider://CODEX/model/test",
                    "effort": "MAX",
                },
                source="TEST",
            )

        self.assertEqual("SESSION_RESUME_COMPLETED", result["status"])
        self.assertEqual({"connection_state": "READY"}, result["session_connection"])
        prepare.assert_called_once_with(
            "GCS",
            {
                "provider": "CODEX",
                "model_ref": "provider://CODEX/model/test",
                "effort": "MAX",
            },
        )

    def test_session_resume_action_requires_a_server_owned_terminal_selector(self) -> None:
        with self.assertRaises(UniverseError) as raised:
            self.server.execute_action(
                "session.resume",
                {"target": "CLI_TERMINAL"},
                source="TEST",
            )
        self.assertEqual("SESSION_RESUME_SELECTOR_REQUIRED", raised.exception.code)

    def test_cli_terminal_resolves_vendor_ref_from_supervisor_session(self) -> None:
        supervised, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session_internal_coordinate",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-app-server:vendor-thread-123",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
            }
        )
        terminal_host = Mock()
        terminal_host.find_live.return_value = None
        terminal_host.create.return_value = {
            "terminal_id": "term_vendor_resume",
            "state": "LIVE",
        }
        self.server.terminal_host = terminal_host

        created = self.server.create_cli_terminal(
            {
                "project_id": "GCS",
                "mode": "MASTER",
                "cwd": str(self.project_root),
                "provider": "CODEX",
                "supervisor_session_id": supervised["session_id"],
                "resume_session_ref": supervised["session_id"],
            }
        )

        self.assertEqual("CLI_TERMINAL_CREATED", created["status"])
        terminal_host.create.assert_called_once_with(
            project_id="GCS",
            mode="MASTER",
            cwd=str(self.project_root),
            provider="CODEX",
            model_ref="",
            effort="AUTO",
            supervisor_session_id="session_internal_coordinate",
            session_anchor_ref=ANY,
            resume_session_ref="vendor-thread-123",
            cols=120,
            rows=32,
        )

    def test_cli_terminal_rebinds_only_the_exact_compatible_host_ref(self) -> None:
        supervised, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session_host_rebind",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-app-server:vendor-thread-host",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
            }
        )
        hosted = {
            "terminal_id": "term_host_rebind",
            "host_session_ref": "host-exact",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CODEX",
            "supervisor_session_id": supervised["session_id"],
            "session_anchor_ref": supervised["session_anchor_ref"],
            "host_compatibility": "CURRENT",
            "host_reconnect_eligible": True,
            "state": "LIVE",
        }
        managed = Mock()
        managed.public.return_value = hosted
        terminal_host = Mock()
        terminal_host.get_host.return_value = managed
        self.server.terminal_host = terminal_host

        attached = self.server.create_cli_terminal(
            {
                "project_id": "GCS",
                "mode": "MASTER",
                "cwd": str(self.project_root),
                "provider": "CODEX",
                "supervisor_session_id": supervised["session_id"],
                "host_session_ref": "host-exact",
            }
        )

        self.assertEqual("CLI_TERMINAL_ATTACHED", attached["status"])
        self.assertEqual(hosted, attached["terminal"])
        terminal_host.get_host.assert_called_once_with("host-exact")
        terminal_host.find_live.assert_not_called()
        terminal_host.create.assert_not_called()

    def test_cli_terminal_pty_binding_resolves_verified_anchor_provider_ref(self) -> None:
        terminal_host = Mock()
        terminal_host.find_live.return_value = None
        terminal_host.create.return_value = {
            "terminal_id": "term_anchor_binding",
            "state": "LIVE",
        }
        self.server.terminal_host = terminal_host
        anchor_sessions = {
            "sessions": [
                {
                    "project_id": "GCS",
                    "mode": "MASTER",
                    "session_id": "codex:vendor-thread-123",
                    "session_anchor_ref": "MASTER-CURRENT-PTY-BINDING",
                    "observer_session_ref": "codex:vendor-thread-123",
                }
            ]
        }
        verified = [
            {
                "provider": "CODEX",
                "provider_session_id": "vendor-thread-123",
                "session_kind": "CHAT",
                "identity_state": "VERIFIED",
            }
        ]
        with (
            patch.object(
                self.server,
                "list_project_anchor_sessions",
                return_value=anchor_sessions,
            ),
            patch.object(
                self.server.store,
                "discover_provider_session_sources",
                side_effect=lambda provider: verified if provider == "CODEX" else [],
            ),
        ):
            created = self.server.create_cli_terminal(
                {
                    "project_id": "GCS",
                    "mode": "MASTER",
                    "cwd": str(self.project_root),
                    "provider": "CODEX",
                    "pty_binding_anchor_ref": "MASTER-CURRENT-PTY-BINDING",
                }
            )

        self.assertEqual("CLI_TERMINAL_CREATED", created["status"])
        call = terminal_host.create.call_args.kwargs
        self.assertEqual("CODEX", call["provider"])
        self.assertEqual("vendor-thread-123", call["resume_session_ref"])
        self.assertRegex(call["supervisor_session_id"], r"^session_[0-9a-f]{24}$")

    def test_cli_terminal_pty_binding_rejects_unverified_internal_observer(self) -> None:
        terminal_host = Mock()
        self.server.terminal_host = terminal_host
        anchor_sessions = {
            "sessions": [
                {
                    "project_id": "GCS",
                    "mode": "CONDUCTOR",
                    "session_id": "codex:session-2",
                    "session_anchor_ref": "CONDUCTOR-CURRENT-PTY-BINDING",
                    "observer_session_ref": "codex:session-2",
                }
            ]
        }
        with (
            patch.object(
                self.server,
                "list_project_anchor_sessions",
                return_value=anchor_sessions,
            ),
            patch.object(
                self.server.store,
                "discover_provider_session_sources",
                return_value=[],
            ),
        ):
            with self.assertRaises(UniverseError) as raised:
                self.server.create_cli_terminal(
                    {
                        "project_id": "GCS",
                        "mode": "CONDUCTOR",
                        "cwd": str(self.project_root),
                        "provider": "CODEX",
                        "pty_binding_anchor_ref": "CONDUCTOR-CURRENT-PTY-BINDING",
                    }
                )

        self.assertEqual(
            "TERMINAL_PTY_BINDING_PROVIDER_SESSION_UNAVAILABLE",
            raised.exception.code,
        )
        terminal_host.create.assert_not_called()

    def test_passive_provider_reobservation_preserves_session_anchor_location(self) -> None:
        original, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session_anchor_location_passive_001",
                "node": "universe",
                "project_id": "universe",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "codex-anchor-location-passive-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

        reobserved, created = self.server.session_supervisor.register_session(
            {
                "session_id": "session_requested_master_passive_001",
                "node": "universe",
                "project_id": "universe",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-anchor-location-passive-001",
                "state": "DISCONNECTED",
                "currentness": "CURRENT",
            }
        )

        self.assertFalse(created)
        self.assertEqual(original["session_id"], reobserved["session_id"])
        self.assertEqual(original["session_anchor_ref"], reobserved["session_anchor_ref"])
        self.assertEqual("CONDUCTOR", reobserved["mode"])
        current_locations = [
            item for item in reobserved["location_history"] if item["is_current"]
        ]
        self.assertEqual(["CONDUCTOR"], [item["mode"] for item in current_locations])
        events = self.server.session_supervisor.list_events(
            session_id=original["session_id"]
        )
        latest = events[0]
        self.assertEqual("SESSION_REOBSERVED", latest["event_type"])
        self.assertTrue(latest["details"]["identity_reused"])
        self.assertTrue(latest["details"]["passive_location_preserved"])
        self.assertEqual("MASTER", latest["details"]["requested_location"]["mode"])

    def test_supervisor_session_sweep_uses_exact_live_pty_binding(self) -> None:
        registered, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session_live_pty_sweep_001",
                "node": "universe",
                "project_id": "universe",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "state": "DISCONNECTED",
                "currentness": "STALE",
            }
        )
        terminal_host = Mock()
        terminal_host.list_sessions.return_value = [
            {
                "terminal_id": "term_live_pty_sweep_001",
                "project_id": "universe",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "state": "LIVE",
                "supervisor_session_id": registered["session_id"],
                "session_anchor_ref": registered["session_anchor_ref"],
            }
        ]
        self.server.terminal_host = terminal_host

        status, payload = self.request(
            "GET", "/v1/supervisor/sessions?include_hidden=true", token=self.token
        )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, payload["live_session_sweep"]["restored_live_count"])
        self.assertEqual(1, payload["live_session_sweep"]["pty_kept_live_count"])
        session = next(
            item
            for item in payload["sessions"]
            if item["universe_session_id"] == registered["session_id"]
        )
        self.assertEqual("LIVE", session["state"])
        self.assertEqual("STALE", session["currentness"])

    def test_live_terminal_and_inbox_follow_explicit_session_anchor_location(self) -> None:
        original, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session_anchor_location_001",
                "node": "universe",
                "project_id": "universe",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "codex-anchor-location-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        terminal = {
            "terminal_id": "term_anchor_location_001",
            "project_id": "universe",
            "mode": "CONDUCTOR",
            "provider": "CODEX",
            "state": "LIVE",
            "created_at": "2026-08-23T03:19:38Z",
            "supervisor_session_id": original["session_id"],
        }
        terminal_host = Mock()
        terminal_host.list_sessions.return_value = [terminal]
        terminal_host.get.return_value = terminal
        self.server.terminal_host = terminal_host

        conductor_message = self.server.post_session_bus_message(
            {
                "to": {
                    "project_id": "universe",
                    "mode": "CONDUCTOR",
                    "provider": "CODEX",
                },
                "from": {
                    "project_id": "universe",
                    "mode": "MASTER",
                    "provider": "UI",
                },
                "kind": "NOTE",
                "body_text": "follow this terminal when its Session Anchor moves",
            }
        )

        relocated = self.server.session_supervisor.bind_current_location(
            original["session_id"],
            project_id="universe",
            node="universe",
            mode="MASTER",
            evidence_ref="universe://mode-change/test",
            expected_version=original["row_version"],
        )

        self.assertEqual(original["session_id"], relocated["session_id"])
        self.assertNotEqual(
            original["session_anchor_ref"], relocated["session_anchor_ref"]
        )
        projected = self.server.list_cli_terminals()["terminals"][0]
        self.assertEqual("CONDUCTOR", projected["created_mode"])
        self.assertEqual("MASTER", projected["mode"])
        self.assertEqual("SESSION_ANCHOR", projected["location_source"])
        self.assertTrue(projected["location_rebound"])
        self.assertEqual(
            relocated["session_anchor_ref"], projected["active_session_anchor_ref"]
        )
        directory = self.server.session_bus_directory()["terminals"]
        self.assertEqual("MASTER", directory[0]["mode"])
        moved_inbox = self.server.session_bus_inbox(
            {
                "project_id": "universe",
                "mode": "MASTER",
                "provider": "CODEX",
            }
        )
        self.assertEqual(
            conductor_message["message_id"], moved_inbox["messages"][0]["message_id"]
        )

        with self.assertRaises(UniverseError) as raised:
            self.server.post_session_bus_message(
                {
                    "to": {
                        "project_id": "universe",
                        "mode": "CONDUCTOR",
                        "provider": "CODEX",
                    },
                    "from": {
                        "project_id": "universe",
                        "mode": "MASTER",
                        "provider": "UI",
                    },
                    "kind": "NOTE",
                    "body_text": "must not follow the PTY birth coordinate",
                }
            )
        self.assertEqual("BUS_TARGET_NOT_FOUND", raised.exception.code)

        posted = self.server.post_session_bus_message(
            {
                "to": {
                    "project_id": "universe",
                    "mode": "MASTER",
                    "provider": "CODEX",
                },
                "from": {
                    "project_id": "universe",
                    "mode": "CONDUCTOR",
                    "provider": "UI",
                },
                "kind": "NOTE",
                "body_text": "follow the active Session Anchor",
            }
        )
        self.assertEqual("MASTER", posted["messages"][0]["to"]["mode"])
        self.assertEqual(
            terminal["terminal_id"], posted["messages"][0]["to"]["terminal_id"]
        )

    def test_list_cli_terminals_keeps_public_activity_fields(self) -> None:
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "todo-activity-fields-session",
                "project_id": "universe",
                "node": "universe",
                "mode": "MASTER",
                "provider": "GROK",
                "provider_session_ref": "grok-activity-fields",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        populated = {
            "terminal_id": "term_activity_fields_001",
            "project_id": "universe",
            "mode": "MASTER",
            "provider": "GROK",
            "state": "LIVE",
            "created_at": "2026-09-04T00:00:00Z",
            "supervisor_session_id": session["session_id"],
            "prompt_delivery": "delivered",
            "provider_cli": "GROK",
            "provider_cli_process": "grok.exe",
            "provider_cli_alive": True,
            "output_sequence": 12,
            "prompt_activity": {
                "generation": 1,
                "working_sequence": 2,
                "permission_sequence": 0,
                "output_sequence": 12,
                "status": "idle",
            },
        }
        terminal_host = Mock()
        terminal_host.list_sessions.return_value = [populated]
        terminal_host.list_hosts.return_value = []
        terminal_host.get.return_value = populated
        self.server.terminal_host = terminal_host

        listed = self.server.list_cli_terminals()["terminals"][0]
        self.assertEqual("delivered", listed["prompt_delivery"])
        self.assertEqual("GROK", listed["provider_cli"])
        self.assertEqual("grok.exe", listed["provider_cli_process"])
        self.assertTrue(listed["provider_cli_alive"])
        self.assertEqual(12, listed["output_sequence"])
        self.assertEqual("idle", listed["prompt_activity"]["status"])

        missing = {
            "terminal_id": "term_activity_fields_002",
            "project_id": "universe",
            "mode": "MASTER",
            "provider": "GROK",
            "state": "LIVE",
            "created_at": "2026-09-04T00:00:01Z",
            "supervisor_session_id": session["session_id"],
        }
        terminal_host.list_sessions.return_value = [missing]
        filled = self.server.list_cli_terminals()["terminals"][0]
        self.assertEqual("", filled["prompt_delivery"])
        self.assertEqual("GROK", filled["provider_cli"])
        self.assertEqual("", filled["provider_cli_process"])
        self.assertTrue(filled["provider_cli_alive"])
        self.assertIn("prompt_activity", filled)

    def test_list_resumable_sessions_splits_reattach_and_resume(self) -> None:
        self.server._managed_shell_identities = lambda: {}  # type: ignore[method-assign]
        live_host = {
            "host_session_ref": "host-live-current",
            "session_anchor_ref": "anchor-live",
            "runtime_state": "LIVE",
            "reconnect_eligible": True,
            "compatibility": "CURRENT",
            "provider": "GROK",
        }
        incompatible_host = {
            "host_session_ref": "host-incompatible",
            "session_anchor_ref": "anchor-incompatible",
            "runtime_state": "LIVE",
            "reconnect_eligible": True,
            "compatibility": "INCOMPATIBLE",
            "provider": "CLAUDE",
        }
        terminal_host = Mock()
        terminal_host.list_sessions.return_value = []
        terminal_host.list_hosts.return_value = [live_host, incompatible_host]
        self.server.terminal_host = terminal_host
        self.server.list_all_project_anchor_sessions = lambda: [  # type: ignore[method-assign]
            {
                "session_anchor_ref": "anchor-live",
                "universe_session_id": "sess-live",
                "project_id": "universe",
                "mode": "MASTER",
                "provider": "GROK",
                "currentness": "CURRENT",
                "last_seen_at": "2026-09-04T03:00:00Z",
            },
            {
                "session_anchor_ref": "anchor-incompatible",
                "universe_session_id": "sess-incompatible",
                "project_id": "universe",
                "mode": "CONDUCTOR",
                "provider": "CLAUDE",
                "currentness": "CURRENT",
                "last_seen_at": "2026-09-04T02:30:00Z",
            },
            {
                "session_anchor_ref": "anchor-dead-new",
                "universe_session_id": "sess-dead-new",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "currentness": "CURRENT",
                "last_seen_at": "2026-09-04T02:00:00Z",
            },
            {
                "session_anchor_ref": "anchor-dead-old",
                "universe_session_id": "sess-dead-old",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "currentness": "CURRENT",
                "last_seen_at": "2026-09-04T01:00:00Z",
            },
            {
                "session_anchor_ref": "anchor-stale",
                "universe_session_id": "sess-stale",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "currentness": "STALE",
                "last_seen_at": "2026-09-03T00:00:00Z",
            },
        ]

        listed = self.server.list_resumable_sessions({"limit": 7})
        self.assertEqual("SESSIONS_RESUMABLE_COLLECTED", listed["status"])
        self.assertEqual(["host-live-current"], [row["host_session_ref"] for row in listed["reattach"]])
        self.assertEqual(["REATTACH"], [row["kind"] for row in listed["reattach"]])
        self.assertEqual(["sess-dead-new"], [row["session_id"] for row in listed["resume"]])
        self.assertEqual(["RESUME"], [row["kind"] for row in listed["resume"]])
        self.assertEqual(
            ["anchor-incompatible"],
            [row["session_anchor_ref"] for row in listed["incompatible"]],
        )
        self.assertIn("런타임 바뀜", listed["incompatible"][0]["reason"])
        self.assertFalse(listed["resume_truncated"])

        expanded = self.server.list_resumable_sessions(
            {"project_id": "GCS", "mode": "MASTER", "limit": 7}
        )
        self.assertEqual(
            ["anchor-dead-new", "anchor-dead-old", "anchor-stale"],
            [row["session_anchor_ref"] for row in expanded["resume"]],
        )
        paged = self.server.list_resumable_sessions(
            {
                "project_id": "GCS",
                "mode": "MASTER",
                "before": "2026-09-04T02:00:00Z",
                "limit": 7,
            }
        )
        self.assertEqual(
            ["anchor-dead-old", "anchor-stale"],
            [row["session_anchor_ref"] for row in paged["resume"]],
        )

        status, payload = self.request(
            "GET", "/v1/sessions/resumable?limit=7", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("SESSIONS_RESUMABLE_COLLECTED", payload["status"])
        self.assertEqual(["host-live-current"], [row["host_session_ref"] for row in payload["reattach"]])

    def test_reattach_row_provider_falls_back_to_managed_shell_identity(self) -> None:
        # After a server restart the anchor-session projection can lose provider
        # before the live terminal re-registers; the persisted managed-shell
        # identity file still carries it, so the re-attach label stays specific.
        live_host = {
            "host_session_ref": "host-live",
            "session_anchor_ref": "anchor-live",
            "runtime_state": "LIVE",
            "reconnect_eligible": True,
            "compatibility": "CURRENT",
        }
        terminal_host = Mock()
        terminal_host.list_sessions.return_value = []
        terminal_host.list_hosts.return_value = [live_host]
        self.server.terminal_host = terminal_host
        self.server.list_all_project_anchor_sessions = lambda: [  # type: ignore[method-assign]
            {
                "session_anchor_ref": "anchor-live",
                "universe_session_id": "sess-live",
                "project_id": "",
                "mode": "",
                "provider": "",
                "currentness": "CURRENT",
                "last_seen_at": "2026-09-04T03:00:00Z",
            }
        ]
        self.server._managed_shell_identities = lambda: {  # type: ignore[method-assign]
            "anchor-live": {
                "provider": "CODEX",
                "mode": "MASTER",
                "project_id": "universe",
                "supervisor_session_id": "sess-live",
            }
        }

        listed = self.server.list_resumable_sessions({"limit": 7})
        self.assertEqual(1, len(listed["reattach"]))
        row = listed["reattach"][0]
        self.assertEqual("CODEX", row["provider"])
        self.assertEqual("MASTER", row["mode"])
        self.assertEqual("universe", row["project_id"])
        self.assertEqual("universe MASTER CODEX", row["label"])
        self.assertNotIn("UNKNOWN", row["label"])

    def test_provider_quota_endpoint_returns_three_rows_and_absorbs_a_sweep(
        self,
    ) -> None:
        # Isolate from the developer's real ~/.codex transcript.
        self.server._sweep_transcript_quota = lambda: []  # type: ignore[method-assign]
        # Nothing observed yet: three stable rows, all UNKNOWN.
        status, payload = self.request(
            "GET", "/v1/provider-quota", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            ["CLAUDE", "GROK", "CODEX"],
            [row["provider"] for row in payload["providers"]],
        )
        self.assertTrue(all(row["state"] == "UNKNOWN" for row in payload["providers"]))

        # A live conductor connection carrying a quota reading is picked up by
        # the endpoint's own sweep.
        self.server.conductor_session_status = lambda: {  # type: ignore[method-assign]
            "schema": "universe.provider-session-connection.v1",
            "connection_state": "OPEN",
            "session_ref": "session_anchor_conductor",
            "runtime_observation": {
                "schema": "universe.provider-runtime-observation.v1",
                "provider": "CLAUDE",
                "quota": {
                    "schema": "universe.provider-quota-snapshot.v1",
                    "provider": "CLAUDE",
                    "source": "rate_limit_event",
                    "state": "WARNING",
                    "windows": [
                        {"name": "FIVE_HOUR", "used_percent": 83.0},
                    ],
                },
            },
        }
        status, payload = self.request(
            "GET", "/v1/provider-quota", token=self.token
        )
        self.assertEqual(200, status)
        claude = payload["providers"][0]
        self.assertEqual("WARNING", claude["state"])
        self.assertEqual(83.0, claude["windows"][0]["used_percent"])
        self.assertEqual("session_anchor_conductor", claude["session_ref"])

        # A transcript-sourced Codex reading flows through the same endpoint.
        self.server._sweep_transcript_quota = lambda: [  # type: ignore[method-assign]
            {
                "schema": "universe.provider-quota-snapshot.v1",
                "provider": "CODEX",
                "source": "codex-rollout-transcript",
                "state": "EXHAUSTED",
                "windows": [{"name": "PRIMARY", "used_percent": 100.0}],
                "rate_limit_reached_type": "primary",
                "observed_at": "2026-09-04T07:00:00Z",
            }
        ]
        status, payload = self.request(
            "GET", "/v1/provider-quota", token=self.token
        )
        codex = payload["providers"][2]
        self.assertEqual("EXHAUSTED", codex["state"])
        self.assertEqual("codex-rollout-transcript", codex["source"])
        self.assertEqual("2026-09-04T07:00:00Z", codex["observed_at"])

    def test_server_disables_nagle_on_every_accepted_connection(self) -> None:
        # A terminal WebSocket sends the smallest possible frame as fast as a
        # user can type or a PTY can echo — exactly the pattern Nagle plus
        # delayed-ACK interacts badly with. The remote gateway's own proxy
        # legs already disable it (universe_remote_gateway.py); a direct
        # local connection to this server had no equivalent, so a local
        # browser could see worse per-keystroke latency than one going
        # through the gateway.
        request = Mock()
        client_address = ("127.0.0.1", 51234)
        with patch.object(ThreadingHTTPServer, "finish_request") as base_finish:
            self.server.finish_request(request, client_address)
        request.setsockopt.assert_called_once_with(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1
        )
        base_finish.assert_called_once_with(request, client_address)

    def test_server_nagle_disable_tolerates_a_closed_socket(self) -> None:
        request = Mock()
        request.setsockopt.side_effect = OSError("socket already closed")
        with patch.object(ThreadingHTTPServer, "finish_request") as base_finish:
            self.server.finish_request(request, ("127.0.0.1", 51234))
        base_finish.assert_called_once()

    def test_new_cli_terminal_waits_for_provider_hook_not_runtime_boot(self) -> None:
        terminal_host = Mock()
        terminal_host.find_live.return_value = None
        terminal_host.create.return_value = {
            "terminal_id": "term_new_session",
            "state": "LIVE",
        }
        self.server.terminal_host = terminal_host

        created = self.server.create_cli_terminal(
            {
                "project_id": "GCS",
                "mode": "MASTER",
                "cwd": str(self.project_root),
                "provider": "CODEX",
            }
        )

        self.assertEqual("CLI_TERMINAL_CREATED", created["status"])
        terminal_host.create.assert_called_once_with(
            project_id="GCS",
            mode="MASTER",
            cwd=str(self.project_root),
            provider="CODEX",
            model_ref="",
            effort="AUTO",
            supervisor_session_id=ANY,
            session_anchor_ref=ANY,
            resume_session_ref="",
            cols=120,
            rows=32,
        )

    def test_conductor_prepare_rejects_model_from_another_provider(self) -> None:
        prepared = self.server.prepare_conductor_session(
            {
                "provider": "CODEX",
                "model_ref": "provider://CLAUDE/model/opus",
            }
        )
        self.assertEqual("UNAVAILABLE", prepared["connection_state"])
        self.assertEqual(
            "PROVIDER_MODEL_PROVIDER_MISMATCH", prepared["error_code"]
        )
        self.assertIn(
            "CLAUDE",
            prepared["reason"],
        )
        self.assertIn("CODEX", prepared["reason"])

    def test_conductor_prepare_rejects_legacy_bare_model_from_another_provider(
        self,
    ) -> None:
        self.server.store.set_provider_setting(
            "UNIVERSE_CONDUCTOR",
            "CONDUCTOR",
            {"provider": "CODEX", "model_ref": "opus", "effort": "AUTO"},
        )
        prepared = self.server.prepare_conductor_session()
        self.assertEqual("UNAVAILABLE", prepared["connection_state"])
        self.assertEqual(
            "PROVIDER_MODEL_PROVIDER_MISMATCH", prepared["error_code"]
        )
        self.assertIn("CLAUDE", prepared["reason"])
        self.assertIsNone(self.server.conductor_session_host)

    def test_conductor_prepare_route_uses_lazy_host_boundary(self) -> None:
        connection = {
            "target_kind": "UNIVERSE_CONDUCTOR",
            "requested_mode": "CONDUCTOR",
            "connection_state": "NEW",
            "resident": True,
        }
        with patch.object(
            self.server,
            "prepare_conductor_session",
            return_value=connection,
        ) as prepare:
            status, result = self.request(
                "POST",
                "/v1/conductor-session/prepare",
                {"provider": "CODEX"},
                token=self.token,
            )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("CONDUCTOR_SESSION_PREPARED", result["status"])
        self.assertEqual(connection, result["session_connection"])
        prepare.assert_called_once_with({"provider": "CODEX"})

    def test_worker_binding_profiles_resolve_by_scope_and_revision(self) -> None:
        status, _ = self.request("POST", "/v1/projects/register", self.registration())
        self.assertEqual(HTTPStatus.CREATED, status)

        status, empty = self.request("GET", "/v1/settings/worker-bindings")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual([], empty["profiles"])

        universe_profile = {
            "scope_kind": "UNIVERSE",
            "scope_id": "UNIVERSE",
            "worker_role": "REVIEWER",
            "task_type": "*",
            "provider": "CLAUDE",
            "model_ref": "opus",
            "effort": "HIGH",
            "skill_refs": ["webapp-testing"],
            "enabled": True,
        }
        status, created = self.request(
            "POST", "/v1/settings/worker-bindings", universe_profile
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, created["profile"]["revision"])
        status, inherited = self.request(
            "POST",
            "/v1/settings/worker-bindings/resolve",
            {"project_id": "GCS", "worker_role": "REVIEWER"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "universe.worker-binding-snapshot.v1",
            inherited["snapshot"]["schema"],
        )
        self.assertEqual("CLAUDE", inherited["snapshot"]["provider"])
        first_digest = inherited["snapshot"]["binding_digest"]
        self.assertEqual(64, len(first_digest))

        universe_profile["effort"] = "MAX"
        status, updated = self.request(
            "POST", "/v1/settings/worker-bindings", universe_profile
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(2, updated["profile"]["revision"])
        status, revised = self.request(
            "POST",
            "/v1/settings/worker-bindings/resolve",
            {"project_id": "GCS", "worker_role": "REVIEWER"},
        )
        self.assertNotEqual(first_digest, revised["snapshot"]["binding_digest"])

        project_profile = {
            **universe_profile,
            "scope_kind": "PROJECT",
            "scope_id": "GCS",
            "provider": "CODEX",
            "model_ref": "sol",
            "effort": "MEDIUM",
        }
        status, _ = self.request(
            "POST", "/v1/settings/worker-bindings", project_profile
        )
        self.assertEqual(HTTPStatus.OK, status)
        status, project = self.request(
            "POST",
            "/v1/settings/worker-bindings/resolve",
            {"project_id": "GCS", "worker_role": "REVIEWER"},
        )
        self.assertEqual("PROJECT", project["snapshot"]["scope_kind"])
        self.assertEqual("CODEX", project["snapshot"]["provider"])

    def test_runtime_executor_adopt_and_stop_routes_are_managed(self) -> None:
        identity = {
            "pid": 4242,
            "process_created_at": "2026-08-11T12:00:00Z",
            "executable": "C:/Tools/python.exe",
            "command": ["C:/Tools/python.exe", "session-boot", "serve"],
            "endpoint": "http://127.0.0.1:58333",
            "handshake_fingerprint": hashlib.sha256(b"runtime-handshake").hexdigest(),
        }
        observations = []

        def observe(pid: int, created: str) -> dict[str, object]:
            observations.append((pid, created))
            if len(observations) <= 2:
                return {
                    "status": "PROCESS_PRESENT_EXACT",
                    "pid": pid,
                    "process_created_at": created,
                }
            return {
                "status": "ORIGINAL_PROCESS_ABSENT",
                "reason": "TEST_PROCESS_EXITED",
                "pid": pid,
                "expected_process_created_at": created,
            }

        self.server.session_supervisor.process_observer = observe
        status, adopted = self.request(
            "POST",
            "/v1/supervisor/executors/adopt",
            {
                "session": {
                    "session_id": "runtime-executor-api",
                    "node": "universe",
                    "mode": "MASTER",
                    "provider": "RUNTIME",
                    "provider_session_ref": "runtime-boot-api",
                    "anchor_ref": "MASTER-CURRENT-API-001",
                },
                "process_identity": identity,
                "stop_capability": "runtime-stop-secret",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("RUNTIME_EXECUTOR_ADOPTED", adopted["status"])
        with patch(
            "universe_service_control.request_graceful_shutdown",
            return_value={"status": "SERVICE_SHUTDOWN_ACCEPTED"},
        ):
            status, stopped = self.request(
                "POST",
                "/v1/supervisor/executors/stop",
                {"session_id": "runtime-executor-api", "timeout_seconds": 1},
                self.token,
            )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("RUNTIME_EXECUTOR_STOPPED", stopped["status"])
        self.assertFalse(stopped["result"]["destructive_fallback_performed"])

    def test_supervisor_session_registry_and_reconcile_api(self) -> None:
        status, registered = self.request(
            "POST",
            "/v1/supervisor/sessions",
            {
                "session_id": "session-gcs-master",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "provider-session-1",
                "anchor_ref": "MASTER-CURRENT-GCS-001",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual(
            "PERSISTENT_MODE_SESSION", registered["session"]["session_kind"]
        )

        status, defaulted = self.request(
            "POST",
            "/v1/supervisor/sessions/session-gcs-master/default",
            {"expected_pointer_version": 0},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, defaulted["result"]["pointer_version"])

        identity = {
            "pid": 4242,
            "process_created_at": "2026-08-02T12:00:00Z",
            "executable": "C:\\Tools\\codex.exe",
            "command": ["C:\\Tools\\codex.exe", "resume", "provider-session-1"],
            "endpoint": "http://127.0.0.1:51702",
            "handshake_fingerprint": hashlib.sha256(b"handshake").hexdigest(),
        }
        status, leased = self.request(
            "POST",
            "/v1/supervisor/sessions/session-gcs-master/lease",
            {"process_identity": identity, "expected_lease_version": 0},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertIn("lease_token", leased["result"])

        mismatch = dict(identity)
        mismatch["endpoint"] = "http://127.0.0.1:59999"
        status, reconciled = self.request(
            "POST",
            "/v1/supervisor/sessions/session-gcs-master/reconcile",
            {"process_identity": mismatch, "expected_lease_version": 1},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertFalse(reconciled["result"]["destructive_action_permitted"])

        status, sessions = self.request(
            "GET", "/v1/supervisor/sessions?node=GCS&mode=MASTER", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("UNKNOWN", sessions["sessions"][0]["state"])
        self.assertTrue(sessions["sessions"][0]["is_default"])

        status, audit = self.request("GET", "/v1/runtime/audit", token=self.token)
        self.assertEqual(HTTPStatus.OK, status)
        card = next(
            item
            for item in audit["sessions"]
            if item["session_id"] == "session-gcs-master"
        )
        self.assertEqual(
            "MASTER-CURRENT-GCS-001",
            card["anchor_session"]["current_anchor_ref"],
        )
        self.assertEqual("GCS MASTER", card["anchor_session"]["alias"])
        self.assertNotIn("session_id", card["identity"])
        self.assertNotIn("provider_session_ref", card["identity"])
        self.assertNotIn("provider_session_ref", card)

        status, aliased = self.request(
            "POST",
            "/v1/supervisor/sessions/session-gcs-master/alias",
            {
                "alias": "GCS Control Room",
                "expected_version": sessions["sessions"][0]["row_version"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GCS Control Room", aliased["result"]["alias"])

        status, events = self.request(
            "GET",
            "/v1/supervisor/events?session_id=session-gcs-master",
            token=self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertIn(
            "PROCESS_IDENTITY_MISMATCH",
            [event["event_type"] for event in events["events"]],
        )

        self.server.session_supervisor.process_observer = lambda pid, created: {
            "status": "ORIGINAL_PROCESS_ABSENT",
            "reason": "PID_NOT_RUNNING",
            "pid": pid,
            "expected_process_created_at": created,
        }
        status, recovered = self.request(
            "POST",
            "/v1/supervisor/sessions/session-gcs-master/recover-absent",
            {
                "expected_lease_version": 2,
                "operator_evidence_ref": "operator:test-api",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("DISCONNECTED", recovered["result"]["state"])
        self.assertEqual(
            "NOT_PERFORMED",
            recovered["result"]["recovery"]["process_termination"],
        )

    def test_passive_attach_preserves_current_codex_thread_conductor_location(self) -> None:
        status, attached = attach_supervisor_session(
            endpoint=self.endpoint,
            token=self.token,
            node="CONDUCTOR",
            mode="CONDUCTOR",
            provider="CODEX",
            alias="Universe Main Conductor",
            environment={"CODEX_THREAD_ID": "thread-current-codex"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("SUPERVISOR_SESSION_ATTACHED", attached["status"])
        self.assertEqual("CODEX_THREAD_ID", attached["provider_session_ref_source"])
        self.assertEqual("REQUIRED", attached["resident_runtime_reload"])
        self.assertTrue(attached["session"]["is_default"])

        status, sessions = self.request(
            "GET",
            "/v1/supervisor/sessions?node=CONDUCTOR&mode=CONDUCTOR",
            token=self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        selected = next(item for item in sessions["sessions"] if item["is_default"])
        self.assertEqual(attached["session"]["session_id"], selected["universe_session_id"])
        self.assertNotIn("provider_session_ref", selected)
        self.assertEqual("Universe Main Conductor", selected["alias"])

        status, repeated = attach_supervisor_session(
            endpoint=self.endpoint,
            token=self.token,
            node="CONDUCTOR",
            mode="CONDUCTOR",
            provider="CODEX",
            alias="Universe Main Conductor",
            environment={"CODEX_THREAD_ID": "thread-current-codex"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("NOT_REQUIRED", repeated["resident_runtime_reload"])

        status, reobserved = attach_supervisor_session(
            endpoint=self.endpoint,
            token=self.token,
            node="universe",
            mode="MASTER",
            provider="CODEX",
            provider_session_ref="thread-current-codex",
            alias="Universe Main Master",
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            attached["session"]["session_id"], reobserved["session"]["session_id"]
        )
        self.assertEqual("CONDUCTOR", reobserved["session"]["current_project_id"])
        self.assertEqual("CONDUCTOR", reobserved["session"]["mode"])
        self.assertEqual("Universe Main Master", reobserved["session"]["alias"])
        status, all_sessions = self.request(
            "GET", "/v1/sessions?include_hidden=true", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(all_sessions["sessions"]))

    def test_canonical_session_api_moves_location_without_exposing_raw_refs(self) -> None:
        status, registered = self.request(
            "POST",
            "/v1/sessions",
            {
                "session_id": "session-canonical-1",
                "project_id": "universe",
                "node": "universe",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "private-provider-ref",
                "anchor_ref": "CONDUCTOR-CURRENT-1",
                "workspace_key": "origin-workspace",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        session = registered["session"]
        self.assertNotIn("private-provider-ref", json.dumps(session))
        self.assertNotIn("provider_session_ref", json.dumps(session))
        status, moved = self.request(
            "POST",
            "/v1/sessions/session-canonical-1/location",
            {
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "anchor_ref": "MASTER-CURRENT-GCS-2",
                "evidence_ref": "test://location-move",
                "expected_version": session["row_version"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GCS", moved["session"]["current_location"]["project_id"])
        self.assertEqual(
            session["universe_session_id"], moved["session"]["universe_session_id"]
        )
        status, hidden = self.request(
            "POST",
            "/v1/sessions/session-canonical-1/visibility",
            {
                "visibility": "HIDDEN",
                "expected_version": moved["session"]["row_version"],
            },
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("HIDDEN", hidden["session"]["visibility"])
        status, visible = self.request("GET", "/v1/sessions")
        self.assertEqual([], visible["sessions"])
        status, all_sessions = self.request("GET", "/v1/sessions?include_hidden=true")
        self.assertEqual(1, len(all_sessions["sessions"]))

    def test_working_directory_api_uses_host_confirmed_session_move(self) -> None:
        target_root = self.temp_root / "TARGET"
        target_root.mkdir()
        (target_root / "REPOSITORY_MANIFEST.md").write_text(
            "# Target Repository Manifest\n", encoding="utf-8"
        )
        self.server.store.register_project(
            self.registration(project_id="TARGET", project_root=str(target_root))
        )
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "session-cwd-1",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "thread-cwd-1",
            }
        )

        class ConfirmingHost:
            def close(_self):
                return None

            def rebind_working_directory(_self, session_id, project, *, expected_version):
                moved = self.server.session_supervisor.bind_current_location(
                    session_id,
                    project_id=project["project_id"],
                    node=project["project_id"],
                    mode="MASTER",
                    evidence_ref="test://provider-confirmed-cwd",
                    expected_version=expected_version,
                )
                return {
                    "status": "PROVIDER_WORKING_DIRECTORY_REBOUND",
                    "project_id": project["project_id"],
                    "session": moved,
                    "session_connection": {"connection_state": "REUSED"},
                }

        self.server.project_master_hosts = ConfirmingHost()
        card = self.server._session_observatory_card(
            session,
            continuity_by_project={},
            projects_by_id={},
        )
        self.assertTrue(card["provider_session_attached"])
        self.assertNotIn("provider_session_ref", card)

        status, denied = self.request(
            "POST",
            "/v1/sessions/session-cwd-1/working-directory",
            {"project_id": "TARGET", "expected_version": session["row_version"]},
        )
        self.assertEqual(HTTPStatus.UNAUTHORIZED, status)
        self.assertEqual("SUPERVISOR_CONTROL_TOKEN_REQUIRED", denied["error_code"])

        status, result = self.request(
            "POST",
            "/v1/sessions/session-cwd-1/working-directory",
            {"project_id": "TARGET", "expected_version": session["row_version"]},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROVIDER_WORKING_DIRECTORY_REBOUND", result["status"])
        self.assertEqual("TARGET", result["session"]["current_location"]["project_id"])
        self.assertNotIn("thread-cwd-1", json.dumps(result))

    def test_attach_session_requires_explicit_ref_outside_codex_desktop(self) -> None:
        with self.assertRaises(UniverseError) as raised:
            attach_supervisor_session(
                endpoint=self.endpoint,
                token=self.token,
                node="CONDUCTOR",
                mode="CONDUCTOR",
                provider="CLAUDE",
                environment={},
            )
        self.assertEqual("PROVIDER_SESSION_REF_REQUIRED", raised.exception.code)

    def test_supervisor_privileged_mutations_require_service_token(self) -> None:
        status, denied = self.request(
            "POST",
            "/v1/supervisor/sessions",
            {
                "session_id": "session-denied",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "provider-session-denied",
            },
        )
        self.assertEqual(HTTPStatus.UNAUTHORIZED, status)
        self.assertEqual("SUPERVISOR_CONTROL_TOKEN_REQUIRED", denied["error_code"])

        status, registered = self.request(
            "POST",
            "/v1/supervisor/sessions",
            {
                "session_id": "session-ui-safe",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "provider-session-ui",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)

        status, defaulted = self.request(
            "POST",
            "/v1/supervisor/sessions/session-ui-safe/default",
            {"expected_pointer_version": 0},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, defaulted["result"]["pointer_version"])

        status, denied_lease = self.request(
            "POST",
            "/v1/supervisor/sessions/session-ui-safe/lease",
            {"process_identity": {}, "expected_lease_version": 0},
        )
        self.assertEqual(HTTPStatus.UNAUTHORIZED, status)
        self.assertEqual(
            "SUPERVISOR_CONTROL_TOKEN_REQUIRED", denied_lease["error_code"]
        )

        status, denied_recovery = self.request(
            "POST",
            "/v1/supervisor/sessions/session-ui-safe/recover-absent",
            {"expected_lease_version": 1, "operator_evidence_ref": "operator:test"},
        )
        self.assertEqual(HTTPStatus.UNAUTHORIZED, status)
        self.assertEqual(
            "SUPERVISOR_CONTROL_TOKEN_REQUIRED", denied_recovery["error_code"]
        )

    def test_legacy_executor_inventory_is_read_only_and_never_auto_adopts(self) -> None:
        with patch(
            "universe_server.collect_windows_session_boot_executors",
            return_value={
                "status": "HOST_INVENTORY_OBSERVED",
                "observations": [
                    {
                        "pid": 99,
                        "process_created_at": "2026-08-02T12:00:00Z",
                        "executable": sys.executable,
                        "command": [
                            sys.executable,
                            "cli.py",
                            "session-boot",
                            "serve",
                        ],
                        "endpoint": None,
                        "handshake_fingerprint": None,
                    }
                ],
            },
        ):
            status, result = self.request(
                "GET",
                "/v1/supervisor/legacy-executors",
                token=self.token,
            )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("UNMANAGED", result["executors"][0]["status"])
        observation = result["executors"][0]["observation"]
        self.assertNotIn("command", observation)
        self.assertEqual("SESSION_BOOT_SERVE", observation["command_profile"])
        self.assertFalse(result["destructive_action_performed"])

    def test_service_shutdown_endpoint_requires_auth_and_requests_clean_stop(
        self,
    ) -> None:
        status, _ = self.request("POST", "/v1/service/shutdown", {})
        self.assertEqual(HTTPStatus.UNAUTHORIZED, status)
        with patch.object(self.server, "shutdown") as shutdown:
            status, result = self.request(
                "POST",
                "/v1/service/shutdown",
                {},
                self.token,
            )
            deadline = time.monotonic() + 2
            while not shutdown.called and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(HTTPStatus.ACCEPTED, status)
        self.assertEqual("SERVICE_SHUTDOWN_ACCEPTED", result["status"])
        shutdown.assert_called_once_with()

    def test_server_close_continues_after_component_cleanup_failure(self) -> None:
        class FailingSessionHost:
            def close(self) -> None:
                raise RuntimeError("continuity save failed")

        class RuntimeProbe:
            stopped = False

            def stop(self) -> None:
                self.stopped = True

        self.server.shutdown()
        self.thread.join(timeout=5)
        runtime = RuntimeProbe()
        self.server.conductor_session_host = FailingSessionHost()
        self.server.conductor_runtime = runtime

        with patch("builtins.print") as output:
            self.server.server_close()

        self.assertTrue(runtime.stopped)
        self.assertIsNone(self.server.conductor_session_host)
        self.assertIsNone(self.server.conductor_runtime)
        self.assertEqual(
            "conductor_session_host", self.server._shutdown_errors[0]["component"]
        )
        output.assert_called_once()

    def test_provider_setting_migrates_existing_database_for_claude(self) -> None:
        database = self.temp_root / "legacy-provider-setting.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                """
                CREATE TABLE cli_provider_setting (
                    scope_kind TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    provider TEXT NOT NULL
                        CHECK(provider IN ('AUTO', 'GROK', 'CODEX')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope_kind, scope_id)
                )
                """
            )
            connection.execute(
                "INSERT INTO cli_provider_setting VALUES (?, ?, ?, ?)",
                ("UNIVERSE_CONDUCTOR", "CONDUCTOR", "GROK", "before"),
            )
            connection.commit()
        finally:
            connection.close()

        store = UniverseStore(database)
        self.assertEqual(
            "GROK",
            store.provider_setting("UNIVERSE_CONDUCTOR", "CONDUCTOR")["provider"],
        )
        self.assertEqual(
            "AUTO",
            store.provider_setting("UNIVERSE_CONDUCTOR", "CONDUCTOR")["effort"],
        )
        updated = store.set_provider_setting(
            "UNIVERSE_CONDUCTOR",
            "CONDUCTOR",
            {"provider": "CLAUDE", "effort": "HIGH"},
        )
        self.assertEqual("CLAUDE", updated["provider"])
        self.assertEqual("HIGH", updated["effort"])

    def test_server_restart_reuses_one_conductor_provider_coordinate(self) -> None:
        class FakeRuntimeHost:
            @staticmethod
            def provider_capabilities() -> list[dict[str, str]]:
                return [
                    {"provider": "GROK", "status": "AVAILABLE"},
                    {"provider": "CODEX", "status": "AVAILABLE"},
                ]

            @staticmethod
            def provider_capability(provider: str) -> dict[str, str]:
                return {"provider": provider, "status": "AVAILABLE"}

        class FakeConductorRuntime:
            instances: list["FakeConductorRuntime"] = []

            def __init__(self, _root: Path) -> None:
                self.stopped = False
                self.instances.append(self)

            def start(self) -> dict[str, str]:
                return {
                    "schema": "universe.planning-runtime-binding.v1",
                    "endpoint": "http://127.0.0.1:41991",
                    "token": "runtime-token",
                    "session_id": "universe-conductor-session",
                    "origin_anchor_ref": "universe-anchor",
                    "origin_frame_id": "current",
                    "parent_actor_ref": "universe-conductor",
                    "parent_evidence_ref": "host://parent/current",
                    "binding_evidence_ref": "host://runtime/binding",
                    "runtime_currentness_observation": "CURRENT",
                }

            def stop(self) -> None:
                self.stopped = True

        serial = {"value": 0}

        class StoreAwareProvider:
            def __init__(self, provider, store, requested_mode) -> None:
                self.provider = provider
                self.store = store
                self.requested_mode = requested_mode
                self.connection_state = "UNKNOWN"
                self.session_id = store.session_ref_for(provider)
                self.session_ref = f"{provider.lower()}:pending"

            def set_permission_requester(self, _requester) -> None:
                return

            def prepare_session(self) -> None:
                if self.session_id is None:
                    serial["value"] += 1
                    self.session_id = f"session-{serial['value']}"
                self.connection_state = self.store.observe_provider_session(
                    self.provider,
                    self.session_id,
                )
                self.session_ref = f"{self.provider.lower()}:{self.session_id}"

            def reply(self, _message) -> str:
                return "ok"

            def close(self) -> None:
                return

        def provider_factory(provider, _root, _target, store, mode, _actor):
            return StoreAwareProvider(provider, store, mode)

        database = self.temp_root / "restart-universe.sqlite3"
        common = {
            "database_path": database,
            "token": "restart-token",
            "runtime_host": FakeRuntimeHost(),
            "mode_contract": self.server.mode_contract,
            "auto_start_conductor_runtime": True,
            "conductor_runtime_factory": FakeConductorRuntime,
            "conductor_session_provider_factory": provider_factory,
            "auto_start_project_masters": False,
            "host_profile": HostProfileStore(self.temp_root / "restart-host.json"),
        }
        first = create_server(**common)
        try:
            planning = first.planning_binding_status()
            initial = first.provider_settings()["universe_conductor"][
                "session_connection"
            ]
            first.set_universe_provider_setting({"provider": "CODEX"})
            switched = first.prepare_conductor_session()
        finally:
            first.server_close()

        second = create_server(**common)
        try:
            restored = second.provider_settings()["universe_conductor"][
                "session_connection"
            ]
        finally:
            second.server_close()

        self.assertEqual("NEW", initial["connection_state"])
        self.assertEqual("BOUND", planning["status"])
        self.assertEqual("CURRENT", planning["runtime_currentness_observation"])
        self.assertEqual("GROK", initial["last_provider"])
        self.assertEqual("REPLACED", switched["connection_state"])
        self.assertEqual("CODEX", switched["last_provider"])
        self.assertEqual("REUSED", restored["connection_state"])
        self.assertEqual("CODEX", restored["last_provider"])
        self.assertEqual(switched["last_session_ref"], restored["last_session_ref"])
        self.assertEqual("CONDUCTOR", restored["requested_mode"])
        self.assertTrue(restored["resident"])
        self.assertTrue(
            all(instance.stopped for instance in FakeConductorRuntime.instances)
        )

    def test_planning_runtime_binding_requires_currentness_observation(self) -> None:
        binding = {
            "schema": "universe.planning-runtime-binding.v1",
            "endpoint": "http://127.0.0.1:41991",
            "token": "runtime-token",
            "session_id": "session-one",
            "origin_anchor_ref": "anchor-one",
            "origin_frame_id": "conductor",
            "parent_actor_ref": "universe-conductor",
            "parent_evidence_ref": "host://parent/current",
            "binding_evidence_ref": "host://runtime/binding",
            "runtime_currentness_observation": "UNKNOWN",
            "source_ref": "universe-release-db://core-test@" + "a" * 64,
            "source_commit": "b" * 40,
            "source_repository": "fixture/universe-private",
        }
        normalized = normalize_planning_runtime_binding(binding)
        self.assertEqual("UNKNOWN", normalized["runtime_currentness_observation"])
        self.assertEqual(binding["source_ref"], normalized["source_ref"])
        self.assertEqual(binding["source_commit"], normalized["source_commit"])
        self.assertEqual(binding["source_repository"], normalized["source_repository"])
        del binding["runtime_currentness_observation"]
        with self.assertRaisesRegex(UniverseError, "missing"):
            normalize_planning_runtime_binding(binding)

    def test_explicit_unavailable_provider_does_not_fall_back(self) -> None:
        class FakeRuntimeHost:
            @staticmethod
            def provider_capability(provider: str) -> dict[str, str]:
                return {
                    "provider": provider,
                    "status": "AVAILABLE" if provider == "GROK" else "UNAVAILABLE",
                    "reason": "CODEX_CLI_LAUNCH_FAILED",
                }

        self.server.runtime_host = FakeRuntimeHost()
        self.server.store.set_provider_setting(
            "UNIVERSE_CONDUCTOR",
            "CONDUCTOR",
            {"provider": "CODEX"},
        )
        with self.assertRaisesRegex(Exception, "CODEX_CLI_LAUNCH_FAILED"):
            self.server._resolve_conductor_provider({"requested_provider": "AUTO"})

    def test_conductor_permission_round_trip_unblocks_resident_session(self) -> None:
        message, _created = self.server.store.create_conductor_room_message(
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "Inspect a guarded operation.",
                "idempotency_key": "conductor-permission-parent-001",
            }
        )
        selected: dict[str, str | None] = {"option_id": None}
        request = {
            "schema": "universe.agent-permission-request.v1",
            "request_id": "permission_conductor_001",
            "provider": "CODEX",
            "session_id": "codex-thread-001",
            "tool_call": {
                "toolCallId": "tool-001",
                "title": "item/permissions/requestApproval",
                "requestedPermissions": {"network": {"enabled": True}},
            },
            "options": [
                {
                    "optionId": "grantForTurn",
                    "name": "Allow for this turn",
                    "kind": "allow_once",
                },
                {
                    "optionId": "decline",
                    "name": "Reject",
                    "kind": "reject_once",
                },
            ],
        }

        def wait_for_decision() -> None:
            with self.server.conductor_permissions.message_context(
                message["message_id"]
            ):
                selected["option_id"] = self.server.conductor_permissions.request(
                    request
                )

        worker = threading.Thread(target=wait_for_decision, daemon=True)
        worker.start()
        deadline = time.monotonic() + 2
        permissions: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            _status, collected = self.request(
                "GET", "/v1/conductor-room/messages", token=self.token
            )
            permissions = collected["permissions"]
            if permissions:
                break
            time.sleep(0.01)

        self.assertEqual("PENDING", permissions[0]["state"])
        self.assertEqual("UNIVERSE_CONDUCTOR", permissions[0]["scope_kind"])
        self.assertEqual(message["message_id"], permissions[0]["in_reply_to"])
        status, resolved = self.request(
            "POST",
            (
                "/v1/conductor-room/agent-session/permissions/"
                "permission_conductor_001/decision"
            ),
            {"option_id": "grantForTurn"},
            self.token,
        )
        worker.join(timeout=2)

        self.assertEqual(HTTPStatus.OK, status)
        self.assertFalse(worker.is_alive())
        self.assertEqual("grantForTurn", selected["option_id"])
        self.assertEqual("RESOLVED", resolved["permission"]["state"])

    def test_conductor_permission_timeout_is_fail_closed(self) -> None:
        bridge = ConductorPermissionBridge(timeout_seconds=0.01)
        with bridge.message_context("conductor-message-timeout"):
            selected = bridge.request(
                {
                    "schema": "universe.agent-permission-request.v1",
                    "request_id": "permission_conductor_timeout",
                    "provider": "GROK",
                    "session_id": "grok-session-001",
                    "tool_call": {"toolCallId": "tool-timeout"},
                    "options": [
                        {
                            "optionId": "allow-once",
                            "name": "Allow once",
                            "kind": "allow_once",
                        },
                        {
                            "optionId": "reject-once",
                            "name": "Reject",
                            "kind": "reject_once",
                        },
                    ],
                }
            )

        self.assertIsNone(selected)
        self.assertEqual("CANCELLED", bridge.list_requests()[0]["state"])

    def test_conductor_room_message_is_durable_and_idempotent(self) -> None:
        request = {
            "kind": "QUESTION",
            "sender": "USER",
            "body": "Show the active project risks.",
            "idempotency_key": "conductor-question-001",
        }

        status, result = self.request(
            "POST",
            "/v1/conductor-room/messages",
            request,
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("CONDUCTOR_ROOM_MESSAGE_RECORDED", result["status"])
        self.assertEqual("QUEUED", result["message"]["delivery_state"])

        status, repeated = self.request(
            "POST",
            "/v1/conductor-room/messages",
            request,
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("CONDUCTOR_ROOM_MESSAGE_ALREADY_RECORDED", repeated["status"])
        self.assertEqual(
            result["message"]["message_id"], repeated["message"]["message_id"]
        )

        status, collected = self.request(
            "GET", "/v1/conductor-room/messages", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("CONDUCTOR_ROOM_MESSAGES_COLLECTED", collected["status"])
        self.assertEqual(
            [result["message"]["message_id"]],
            [message["message_id"] for message in collected["messages"]],
        )
        self.assertIn(
            collected["messages"][0]["delivery_state"],
            {"QUEUED", "WAITING_FOR_RUNTIME_BINDING"},
        )
        self.assertEqual("UNBOUND", collected["runtime_binding"]["status"])

        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            result["message"]["message_id"],
            reopened.list_conductor_room_messages()[0]["message_id"],
        )

    def test_conductor_intent_wait_is_durable_and_replaced_after_release(self) -> None:
        message, _created = self.server.store.create_conductor_room_message(
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "Inspect the current routing state.",
                "idempotency_key": "conductor-intent-wait-001",
            }
        )
        message_id = message["message_id"]
        self.assertIsNotNone(
            self.server.store.claim_conductor_room_message(
                message_id,
                provider="GROK",
            )
        )
        waiting = {
            "schema": "ai-career.intent-gate-decision.v1",
            "session_id": "conductor-session",
            "frame_id": "conductor-chat:" + message_id,
            "anchor_id": "conductor-anchor",
            "message_id": message_id,
            "status": "INTENT_GATE_WAITING_COMMANDER",
            "stage": "COMMANDER_WAIT",
            "routing_allowed": False,
            "reason": "COMMANDER_WAIT_ACTIVE",
            "classification": None,
            "authority": "UNASSIGNED",
            "execution_assignment": "UNASSIGNED",
            "mutation_permission": "NONE",
        }
        self.server.store.record_conductor_room_intent_gate(message_id, waiting)
        self.server.store.wait_conductor_room_message(
            message_id,
            intent_gate=waiting,
        )
        stored = self.server.store.get_conductor_room_message(message_id)
        self.assertEqual("WAITING_FOR_COMMANDER", stored["delivery_state"])
        self.assertEqual(
            "INTENT_GATE_WAITING_COMMANDER",
            stored["intent_gate"]["status"],
        )

        self.assertIsNotNone(
            self.server.store.claim_conductor_room_message(
                message_id,
                provider="GROK",
            )
        )
        passed = dict(waiting)
        passed.update(
            {
                "status": "INTENT_GATE_PASSED",
                "stage": "INTENT",
                "routing_allowed": True,
                "reason": "NONE",
            }
        )
        self.server.store.record_conductor_room_intent_gate(message_id, passed)
        stored = self.server.store.get_conductor_room_message(message_id)
        self.assertEqual("INTENT_GATE_PASSED", stored["intent_gate"]["status"])
        self.assertEqual(1, len(stored["intent_gate_history"]))
        self.assertEqual(
            "INTENT_GATE_WAITING_COMMANDER",
            stored["intent_gate_history"][0]["status"],
        )

    def test_conductor_room_invokes_bound_runtime_asynchronously(self) -> None:
        class FakeConductorCoordinator:
            def __init__(self) -> None:
                self.observed: list[str] = []
                self.stopped = False

            def observe(self, message_id: str) -> dict[str, str]:
                self.observed.append(message_id)
                return {"status": "COMMANDER_INPUT_OBSERVED"}

            def stop(self) -> None:
                self.stopped = True

        class FakeConductorRuntimeHost:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            @staticmethod
            def provider_capability(provider: str) -> dict[str, object]:
                return {
                    "provider": provider,
                    "status": "AVAILABLE" if provider == "GROK" else "UNAVAILABLE",
                    "reason": "test capability",
                }

            def invoke_conductor_message(
                self,
                *,
                runtime_binding: dict[str, object],
                message: dict[str, object],
                history: list[dict[str, object]],
                provider: str,
                intent_gate_observer: Callable[[Mapping[str, Any]], None] | None = None,
            ) -> dict[str, object]:
                self.calls.append(
                    {
                        "binding": runtime_binding,
                        "message": message,
                        "history": history,
                        "provider": provider,
                    }
                )
                intent_gate = {
                    "schema": "ai-career.intent-gate-decision.v1",
                    "session_id": runtime_binding["session_id"],
                    "frame_id": "conductor-chat:" + str(message["message_id"]),
                    "anchor_id": runtime_binding["origin_anchor_ref"],
                    "message_id": message["message_id"],
                    "status": "INTENT_GATE_PASSED",
                    "stage": "INTENT",
                    "routing_allowed": True,
                    "reason": "NONE",
                    "classification": {
                        "classifier_kind": "HOST",
                        "classifier_ref": "host://test/conductor",
                        "intent_class": "QUESTION",
                        "route": "READ_ONLY_RESPONSE",
                        "effect_class": "NONE",
                        "explicit_imperative": False,
                        "target_state": "EXACT",
                        "permission_shaped": False,
                        "token_match_only": False,
                        "mentioned_runtime_tokens": [],
                    },
                    "authority": "UNASSIGNED",
                    "execution_assignment": "UNASSIGNED",
                    "mutation_permission": "NONE",
                }
                if intent_gate_observer is not None:
                    intent_gate_observer(intent_gate)
                return {
                    "status": "TURN_COMPLETED",
                    "provider": provider,
                    "worker_id": "grok-cli:conductor-001",
                    "result_receipt_ref": "grok-cli:conductor-001:result-001",
                    "repository_write": False,
                    "intent_gate": intent_gate,
                    "structured_result": {
                        "reply": "현재 프로젝트 위험을 정리했습니다.",
                        "action": {
                            "kind": "TODO_DRAFT",
                            "todo": {
                                "scope_kind": "PROJECT",
                                "project_id": "GCS",
                                "node_ref": "strategy-model",
                                "title": "Review project risks",
                                "detail": "Confirm the current risk boundaries.",
                                "priority": "P1",
                                "state": "BACKLOG",
                            },
                        },
                    },
                }

        fake = FakeConductorRuntimeHost()
        self.server.runtime_host = fake
        project_status, _ = self.request(
            "POST", "/v1/projects/register", self.registration(), self.token
        )
        self.assertEqual(HTTPStatus.CREATED, project_status)
        status, bound = self.request(
            "POST",
            "/v1/runtime/planning-binding",
            {
                "schema": "universe.planning-runtime-binding.v1",
                "endpoint": "http://127.0.0.1:41991",
                "token": "runtime-token",
                "session_id": "universe-conductor-session",
                "origin_anchor_ref": "universe-anchor",
                "origin_frame_id": "current",
                "parent_actor_ref": "universe-conductor",
                "parent_evidence_ref": "host://parent/current",
                "binding_evidence_ref": "host://runtime/binding",
                "runtime_currentness_observation": "CURRENT",
                "source_ref": "universe-release-db://core-test@" + "a" * 64,
                "source_commit": "b" * 40,
                "source_repository": "fixture/universe-private",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("BOUND", bound["status"])
        coordinator = FakeConductorCoordinator()
        self.server.conductor_runtime = coordinator

        status, queued = self.request(
            "POST",
            "/v1/conductor-room/messages",
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "현재 프로젝트 위험을 보여줘.",
                "provider": "AUTO",
                "ui_context": {"selected_project_id": "GCS"},
                "idempotency_key": "conductor-async-001",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        message_id = queued["message"]["message_id"]

        messages: list[dict[str, object]] = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            _, collected = self.request(
                "GET", "/v1/conductor-room/messages", token=self.token
            )
            messages = collected["messages"]
            if any(message.get("in_reply_to") == message_id for message in messages):
                break
            time.sleep(0.02)

        original = next(
            message for message in messages if message["message_id"] == message_id
        )
        reply = next(
            message for message in messages if message.get("in_reply_to") == message_id
        )
        self.assertEqual("ANSWERED", original["delivery_state"])
        self.assertEqual("GROK", original["provider"])
        self.assertEqual(
            "INTENT_GATE_PASSED",
            original["intent_gate"]["status"],
        )
        self.assertEqual("UNIVERSE_CONDUCTOR", reply["sender"])
        self.assertEqual("현재 프로젝트 위험을 정리했습니다.", reply["body"])
        self.assertEqual("TODO_DRAFT", reply["ui_action"]["kind"])
        self.assertEqual("GCS", reply["ui_action"]["todo"]["project_id"])
        self.assertIsNone(reply["ui_action"]["todo"]["node_ref"])
        self.assertEqual("CONDUCTOR", reply["ui_action"]["todo"]["source_kind"])
        self.assertEqual([], self.server.store.list_todos())
        self.assertEqual(
            "grok-cli:conductor-001:result-001",
            reply["result_receipt_ref"],
        )
        self.assertEqual(1, len(fake.calls))
        self.assertEqual("GROK", fake.calls[0]["provider"])
        self.assertEqual(
            "GCS",
            fake.calls[0]["message"]["available_projects"][0]["project_id"],
        )
        self.assertEqual([message_id], coordinator.observed)
        self.assertEqual(
            f"universe://conductor-room/messages/{message_id}",
            fake.calls[0]["binding"]["parent_evidence_ref"],
        )

    def test_conductor_room_reuses_resident_mode_session_when_available(self) -> None:
        class CapabilityHost:
            @staticmethod
            def provider_capability(provider: str) -> dict[str, object]:
                return {
                    "provider": provider,
                    "status": "AVAILABLE" if provider == "GROK" else "UNAVAILABLE",
                    "reason": "test capability",
                }

            def invoke_conductor_message(self, **_kwargs):
                raise AssertionError("ordinary Conductor chat must not use Task Frame")

        class SessionHost:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def reply(self, provider: str, message: dict[str, object]):
                self.calls.append((provider, message))
                return {
                    "provider": provider,
                    "session_ref": "grok-acp:conductor-session-1",
                    "connection_state": "REUSED",
                    "requested_mode": "CONDUCTOR",
                    "session_persistence": "LAST_COORDINATE",
                    "text": "Conductor response",
                }

            def reply_stream(self, provider: str, message, on_delta):
                self.calls.append((provider, message))
                on_delta("Conductor ")
                on_delta("response")
                return {
                    "provider": provider,
                    "session_ref": "grok-acp:conductor-session-1",
                    "connection_state": "REUSED",
                    "requested_mode": "CONDUCTOR",
                    "session_persistence": "LAST_COORDINATE",
                    "text": "Conductor response",
                }

            def close(self) -> None:
                return

        self.server.runtime_host = CapabilityHost()
        session_host = SessionHost()
        self.server.conductor_session_host = session_host
        self.assertEqual("UNBOUND", self.server.planning_binding_status()["status"])
        status, queued = self.request(
            "POST",
            "/v1/conductor-room/messages",
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "Show current projects.",
                "provider": "AUTO",
                "idempotency_key": "conductor-resident-session-001",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        message_id = queued["message"]["message_id"]

        deadline = time.monotonic() + 3
        messages: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            _, collected = self.request(
                "GET", "/v1/conductor-room/messages", token=self.token
            )
            messages = collected["messages"]
            if any(item.get("in_reply_to") == message_id for item in messages):
                break
            time.sleep(0.02)

        reply = next(item for item in messages if item.get("in_reply_to") == message_id)
        self.assertEqual("Conductor response", reply["body"])
        self.assertEqual(
            "grok-acp:conductor-session-1",
            reply["result_receipt_ref"],
        )
        self.assertEqual(
            "CONDUCTOR",
            session_host.calls[0][1]["runtime_context"]["requested_mode"],
        )
        self.assertNotIn("history", session_host.calls[0][1]["runtime_context"])
        stream_events = self.server.conductor_room_events.wait(
            after_event_id=0,
            timeout_seconds=0.01,
        )
        self.assertEqual(
            ["STARTED", "DELTA", "DELTA", "COMPLETED"],
            [item["payload"]["event"] for item in stream_events],
        )

    def test_project_master_call_prepares_master_session(self) -> None:
        class FakeManager:
            def __init__(self) -> None:
                self.prepared: list[str] = []

            def is_resident(self, _project_id: str) -> bool:
                return False

            def ensure(self, project: dict[str, object]) -> dict[str, object]:
                project_id = str(project["project_id"])
                self.prepared.append(project_id)
                return {
                    "status": "STARTED",
                    "project_id": project_id,
                    "provider": "GROK",
                }

            @staticmethod
            def connection_status(project_id: str) -> dict[str, object]:
                return {
                    "schema": "universe.provider-session-connection.v1",
                    "target_kind": "PROJECT_MASTER",
                    "target_id": project_id,
                    "requested_mode": "MASTER",
                    "last_provider": "GROK",
                    "last_session_ref": "project-master-session",
                    "connection_state": "NEW",
                    "session_persistence": "LAST_COORDINATE",
                    "resident": True,
                }

            def close(self) -> None:
                return

        self.request("POST", "/v1/projects/register", self.registration())
        manager = FakeManager()
        self.server.project_master_hosts = manager

        status, prepared = self.request(
            "POST",
            "/v1/projects/GCS/master-session/prepare",
            {},
            self.token,
        )

        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROJECT_MASTER_SESSION_PREPARED", prepared["status"])
        self.assertEqual(["GCS"], manager.prepared)
        self.assertEqual(
            "MASTER",
            prepared["session_connection"]["requested_mode"],
        )
        self.assertEqual(
            "NEW",
            prepared["session_connection"]["connection_state"],
        )
        self.assertNotIn("authority", prepared["session_connection"])
        self.assertNotIn("currentness", prepared["session_connection"])

    def test_project_master_prepare_reports_runtime_update_requirement(self) -> None:
        class MissingRuntimeBindingManager:
            @staticmethod
            def is_resident(_project_id: str) -> bool:
                return False

            @staticmethod
            def ensure(_project: dict[str, object]) -> dict[str, object]:
                raise ProjectMasterHostError(
                    "PROJECT_MASTER_MODE_BOOT_BINDING_UNAVAILABLE"
                )

            @staticmethod
            def close() -> None:
                return

        self.request("POST", "/v1/projects/register", self.registration())
        self.server.project_master_hosts = MissingRuntimeBindingManager()

        status, response = self.request(
            "POST",
            "/v1/projects/GCS/master-session/prepare",
            {},
            self.token,
        )

        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("ERROR", response["status"])
        self.assertEqual("PROJECT_RUNTIME_UPDATE_REQUIRED", response["error_code"])
        self.assertIn("install or update", response["detail"])

    def test_conductor_room_persists_fresh_project_draft_action(self) -> None:
        message, created = self.server.store.create_conductor_room_message(
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "Create a fresh project.",
                "provider": "AUTO",
                "idempotency_key": "conductor-fresh-project-001",
            }
        )
        self.assertTrue(created)
        claimed = self.server.store.claim_conductor_room_message(
            message["message_id"], provider="GROK"
        )
        self.assertIsNotNone(claimed)

        _, reply = self.server.store.complete_conductor_room_message(
            message["message_id"],
            provider="GROK",
            body="Review the fresh project draft.",
            result_receipt_ref="grok-cli:conductor-001:result-fresh-001",
            ui_action={
                "kind": "FRESH_PROJECT_DRAFT",
                "intent": {
                    "project": "Orbit Notes",
                    "kind": "",
                    "goal": "Connect project notes.",
                    "target_users": ["Developers"],
                    "technologies": ["Python", "SQLite"],
                    "constraints": ["Offline operation"],
                },
            },
        )

        self.assertEqual("FRESH_PROJECT_DRAFT", reply["ui_action"]["kind"])
        self.assertEqual("Orbit Notes", reply["ui_action"]["intent"]["project"])
        stored_reply = next(
            item
            for item in self.server.store.list_conductor_room_messages()
            if item.get("in_reply_to") == message["message_id"]
        )
        self.assertEqual("FRESH_PROJECT_DRAFT", stored_reply["ui_action"]["kind"])

    def test_conductor_worker_survives_unavailable_provider(self) -> None:
        class UnavailableRuntimeHost:
            @staticmethod
            def provider_capability(provider: str) -> dict[str, object]:
                return {
                    "provider": provider,
                    "status": "UNAVAILABLE",
                    "reason": "provider disabled for test",
                }

        self.server.runtime_host = UnavailableRuntimeHost()
        self.request(
            "POST",
            "/v1/runtime/planning-binding",
            {
                "schema": "universe.planning-runtime-binding.v1",
                "endpoint": "http://127.0.0.1:41991",
                "token": "runtime-token",
                "session_id": "universe-conductor-session",
                "origin_anchor_ref": "universe-anchor",
                "origin_frame_id": "current",
                "parent_actor_ref": "universe-conductor",
                "parent_evidence_ref": "host://parent/current",
                "binding_evidence_ref": "host://runtime/binding",
                "runtime_currentness_observation": "CURRENT",
            },
            self.token,
        )
        _, queued = self.request(
            "POST",
            "/v1/conductor-room/messages",
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "This provider is unavailable.",
                "provider": "AUTO",
                "idempotency_key": "conductor-unavailable-001",
            },
            self.token,
        )
        message_id = queued["message"]["message_id"]
        deadline = time.monotonic() + 3
        state = ""
        failure: dict[str, object] = {}
        while time.monotonic() < deadline:
            _, collected = self.request(
                "GET", "/v1/conductor-room/messages", token=self.token
            )
            original = next(
                message
                for message in collected["messages"]
                if message["message_id"] == message_id
            )
            state = str(original["delivery_state"])
            failure = original.get("failure", {})
            if state == "FAILED":
                break
            time.sleep(0.02)
        self.assertEqual("FAILED", state)
        self.assertEqual("WORKER_PROVIDER_UNAVAILABLE", failure["code"])
        self.assertTrue(all(worker.is_alive() for worker in self.server._conductor_workers))

    def test_registration_refresh_and_listing_are_idempotent(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(),
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_REGISTERED", result["status"])
        self.assertEqual("GCS", result["project"]["project_id"])

        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(metadata={"label": "Trading"}),
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_REFRESHED", result["status"])

        status, result = self.request("GET", "/v1/projects", token=self.token)
        self.assertEqual(200, status)
        gcs_projects = [
            item for item in result["projects"] if item["project_id"] == "GCS"
        ]
        self.assertEqual(1, len(gcs_projects))
        self.assertEqual("Trading", gcs_projects[0]["metadata"]["label"])

    def test_project_attachment_contract_defaults_and_legacy_mapping(self) -> None:
        self.assertEqual(
            {
                "schema": "universe.project-attachment.v1",
                "install_origin": "PROJECT_STANDALONE",
                "universe_membership": "DETACHED",
                "runtime_host": "PROJECT_LOCAL",
            },
            normalize_project_attachment(),
        )
        self.assertEqual(
            "LINKED",
            normalize_project_attachment(
                install_mode="UNIVERSE_ATTACHED",
            )["universe_membership"],
        )
        with self.assertRaises(UniverseError) as context:
            normalize_project_attachment({"runtime_host": "REMOTE"})
        self.assertEqual("PROJECT_ATTACHMENT_VALUE_INVALID", context.exception.code)
        with self.assertRaises(UniverseError) as context:
            normalize_project_attachment({"unsupported": True})
        self.assertEqual("PROJECT_ATTACHMENT_INVALID", context.exception.code)

    def test_registration_exposes_attachment_and_preserves_origin_on_refresh(
        self,
    ) -> None:
        explicit = {
            "schema": "universe.project-attachment.v1",
            "install_origin": "UNIVERSE_CREATED",
            "universe_membership": "LINKED",
            "runtime_host": "PROJECT_LOCAL",
        }
        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(
                attachment=explicit,
                metadata={"label": "Trading", "current_anchor": "anchor-1"},
            ),
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual(explicit, result["project"]["attachment"])
        original_refs = result["project"]["refs"]
        original_metadata = result["project"]["metadata"]

        status, refreshed = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(metadata={"label": "Trading Updated"}),
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            "UNIVERSE_CREATED", refreshed["project"]["attachment"]["install_origin"]
        )
        self.assertEqual("GCS", refreshed["project"]["project_id"])
        self.assertEqual(original_refs, refreshed["project"]["refs"])
        self.assertEqual(
            {"label": "Trading Updated", "node_tag": "GCS"},
            refreshed["project"]["metadata"],
        )
        self.assertNotEqual(original_metadata, refreshed["project"]["metadata"])

        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(
                attachment={
                    **explicit,
                    "install_origin": "PROJECT_STANDALONE",
                }
            ),
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("PROJECT_ATTACHMENT_ORIGIN_IMMUTABLE", result["error_code"])

    def test_registration_defaults_attachment_to_linked_project_local(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(),
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual(
            {
                "schema": "universe.project-attachment.v1",
                "install_origin": "PROJECT_STANDALONE",
                "universe_membership": "LINKED",
                "runtime_host": "PROJECT_LOCAL",
            },
            result["project"]["attachment"],
        )

    def test_legacy_project_record_without_attachment_normalizes_on_read(self) -> None:
        metadata = {"label": "Legacy", "current_anchor": "anchor-legacy"}
        status, registered = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(metadata=metadata),
            self.token,
        )
        self.assertEqual(201, status)
        original = registered["project"]
        connection = sqlite3.connect(self.server.store.database_path)
        try:
            connection.execute(
                "UPDATE project_connection SET attachment_json = NULL WHERE project_id = ?",
                ("GCS",),
            )
        finally:
            connection.close()

        project = self.server.store.get_project("GCS")
        self.assertEqual(original["project_id"], project["project_id"])
        self.assertEqual(original["refs"], project["refs"])
        self.assertEqual({**metadata, "node_tag": "GCS"}, project["metadata"])
        self.assertEqual("LINKED", project["attachment"]["universe_membership"])
        self.assertEqual("PROJECT_LOCAL", project["attachment"]["runtime_host"])

    def test_registration_rejects_invalid_attachment_value(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(
                attachment={"universe_membership": "UNKNOWN"},
            ),
            self.token,
        )
        self.assertEqual(400, status)
        self.assertEqual("PROJECT_ATTACHMENT_VALUE_INVALID", result["error_code"])

    def test_room_message_stays_in_room_without_a_master_bridge(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/room/messages",
            {
                "kind": "QUESTION",
                "body": "What should the project Master review next?",
                "idempotency_key": "room-question-fallback-001",
            },
            self.token,
        )

        self.assertEqual(201, status)
        self.assertEqual("RECORDED", result["message"]["delivery_state"])
        self.assertEqual({}, result["message"]["delivery"])

    def test_room_accepts_a_long_master_report_without_truncation(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        report = "implementation evidence\n" * 900

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/room/messages",
            {
                "kind": "RESULT",
                "body": report,
                "idempotency_key": "room-long-master-report-001",
            },
            self.token,
        )

        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual(report.rstrip(), result["message"]["body"])

    def test_master_bridge_delivers_room_message_and_accepts_bound_reply(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        bridge_request = {
            "endpoint": "http://127.0.0.1:9011",
            "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
            "master_session_ref": "opaque-project-master-session",
            "binding_evidence_ref": "project-host://GCS/master-session/registered",
        }
        status, registered = self.request(
            "POST", "/v1/projects/GCS/master-bridge", bridge_request, self.token
        )
        self.assertEqual(201, status)
        bridge = registered["bridge"]
        self.assertEqual("REGISTERED", bridge["status"])
        self.assertEqual("opaque-project-master-session", bridge["master_session_ref"])

        receipt = {
            "status": "DELIVERED",
            "bridge_id": bridge["bridge_id"],
            "project_id": "GCS",
            "message_id": "room-placeholder",
            "delivered_at": "2026-07-28T04:45:00Z",
        }
        with patch(
            "universe_server.HttpProjectMasterBridge.deliver",
            return_value=receipt,
        ) as deliver:
            status, delivered = self.request(
                "POST",
                "/v1/projects/GCS/room/messages",
                {
                    "kind": "REVIEW",
                    "body": "Review the proposed route before dispatch.",
                    "idempotency_key": "room-review-bridge-001",
                },
                self.token,
            )
        self.assertEqual(201, status)
        message = delivered["message"]
        self.assertEqual("QUEUED_FOR_MASTER", message["delivery_state"])
        self.assertEqual("QUEUED_FOR_MASTER", message["delivery"]["status"])
        self.assertEqual(
            bridge["bridge_id"], deliver.call_args.kwargs["bridge"]["bridge_id"]
        )
        self.assertEqual(
            "opaque-project-master-session",
            deliver.call_args.kwargs["bridge"]["master_session_ref"],
        )

        status, rejected = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge/replies",
            {
                "bridge_id": bridge["bridge_id"],
                "in_reply_to": message["message_id"],
                "kind": "STATUS",
                "body": "Master received the review request.",
                "idempotency_key": "room-review-reply-001",
            },
            self.token,
        )
        self.assertEqual(503, status)
        self.assertEqual("MASTER_BRIDGE_CREDENTIAL_UNAVAILABLE", rejected["error_code"])

        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        try:
            status, replied = self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/replies",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": message["message_id"],
                    "kind": "STATUS",
                    "body": "Master received the review request.",
                    "idempotency_key": "room-review-reply-001",
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_MASTER_REPLY_RECORDED", replied["status"])
        self.assertEqual("PROJECT_MASTER", replied["message"]["sender"])
        self.assertEqual(message["message_id"], replied["message"]["in_reply_to"])

        status, listing = self.request(
            "GET", "/v1/projects/GCS/room/messages", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(2, len(listing["messages"]))
        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual("AVAILABLE", reopened.get_master_bridge("GCS")["status"])
        self.assertEqual(2, len(reopened.list_room_messages("GCS")))

    def test_master_bridge_requires_a_loopback_endpoint(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, result = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "https://example.com",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "opaque-project-master-session",
                "binding_evidence_ref": "project-host://GCS/master-session/registered",
            },
            self.token,
        )
        self.assertEqual(400, status)
        self.assertEqual("MASTER_BRIDGE_INVALID", result["error_code"])

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "not-an-environment-name",
                "master_session_ref": "opaque-project-master-session",
                "binding_evidence_ref": "project-host://GCS/master-session/registered",
            },
            self.token,
        )
        self.assertEqual(400, status)
        self.assertEqual("MASTER_BRIDGE_INVALID", result["error_code"])

    def test_master_bridge_started_marks_queued_delivery_accepted(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, registered = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "opaque-project-master-session",
                "binding_evidence_ref": "project-host://GCS/master-session/registered",
            },
            self.token,
        )
        self.assertEqual(201, status)
        bridge = registered["bridge"]
        receipt = {
            "status": "DELIVERED",
            "bridge_id": bridge["bridge_id"],
            "project_id": "GCS",
            "message_id": "room-placeholder",
            "delivered_at": "2026-08-10T07:00:00Z",
        }
        with patch(
            "universe_server.HttpProjectMasterBridge.deliver",
            return_value=receipt,
        ):
            status, delivered = self.request(
                "POST",
                "/v1/projects/GCS/room/messages",
                {
                    "kind": "TASK_DRAFT",
                    "body": "Run the bounded Master turn.",
                    "idempotency_key": "room-master-started-001",
                },
                self.token,
            )
        self.assertEqual(201, status)
        message = delivered["message"]
        self.assertEqual("QUEUED_FOR_MASTER", message["delivery_state"])

        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        try:
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/stream",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": message["message_id"],
                    "event": "STARTED",
                    "sequence": 0,
                    "delta": "",
                    "detail": "",
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)

        self.assertEqual(202, status)
        self.assertEqual("PROJECT_MASTER_STREAM_EVENT_ACCEPTED", result["status"])
        accepted = next(
            item
            for item in self.server.store.list_room_messages("GCS")
            if item["message_id"] == message["message_id"]
        )
        self.assertEqual("ACCEPTED_BY_MASTER", accepted["delivery_state"])
        self.assertEqual("ACCEPTED_BY_MASTER", accepted["delivery"]["status"])

        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        try:
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/stream",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": message["message_id"],
                    "event": "DELTA",
                    "sequence": 1,
                    "delta": "partial answer",
                    "detail": "",
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)
        self.assertEqual(202, status)
        self.assertEqual(
            {
                "in_reply_to": message["message_id"],
                "body": "partial answer",
                "state": "RESPONDING",
                "sequence": 1,
            },
            {
                key: value
                for key, value in self.server.project_master_stream_snapshot("GCS").items()
                if key != "updated_at"
            },
        )

        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        try:
            self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/stream",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": message["message_id"],
                    "event": "COMPLETED",
                    "sequence": 2,
                    "delta": "",
                    "detail": "",
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)
        self.assertIsNone(self.server.project_master_stream_snapshot("GCS"))

    def test_master_bridge_failed_stream_persists_terminal_delivery(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, registered = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "opaque-project-master-session",
                "binding_evidence_ref": "project-host://GCS/master-session/registered",
            },
            self.token,
        )
        self.assertEqual(201, status)
        bridge = registered["bridge"]
        receipt = {
            "status": "DELIVERED",
            "bridge_id": bridge["bridge_id"],
            "project_id": "GCS",
            "message_id": "room-placeholder",
            "delivered_at": "2026-08-10T07:00:00Z",
        }
        with patch("universe_server.HttpProjectMasterBridge.deliver", return_value=receipt):
            _, delivered = self.request(
                "POST",
                "/v1/projects/GCS/room/messages",
                {
                    "kind": "TASK_DRAFT",
                    "body": "Run the bounded Master turn.",
                    "idempotency_key": "room-master-failed-001",
                },
                self.token,
            )
        message = delivered["message"]
        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        try:
            self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/stream",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": message["message_id"],
                    "event": "STARTED",
                    "sequence": 0,
                    "delta": "",
                    "detail": "",
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
            self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/stream",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": message["message_id"],
                    "event": "FAILED",
                    "sequence": 1,
                    "delta": "",
                    "detail": "ProjectMasterHostError: AGENT_RPC_TIMEOUT:session/prompt",
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)

        failed = next(
            item
            for item in self.server.store.list_room_messages("GCS")
            if item["message_id"] == message["message_id"]
        )
        self.assertEqual("FAILED", failed["delivery_state"])
        self.assertEqual("FAILED", failed["delivery"]["status"])
        self.assertEqual(
            "ProjectMasterHostError: AGENT_RPC_TIMEOUT:session/prompt",
            failed["delivery"]["detail"],
        )
        self.assertIsNone(self.server.project_master_stream_snapshot("GCS"))

    def test_master_completion_failure_persists_room_delivery_without_stream(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        message, _created = self.server.store.create_room_message(
            "GCS",
            {
                "kind": "TASK_DRAFT",
                "body": "Run the bounded Master turn.",
                "idempotency_key": "room-master-completion-failed-001",
            },
            delivery_state="ACCEPTED_BY_MASTER",
        )

        self.server._observe_project_master_completion(
            {
                "status": "FAILED",
                "project_id": "GCS",
                "message_id": message["message_id"],
                "provider_session_ref": "opaque-project-master-session",
                "reason": "PROVIDER_REPLY_FAILED:ClaudeResidentError",
            }
        )

        failed = next(
            item
            for item in self.server.store.list_room_messages("GCS")
            if item["message_id"] == message["message_id"]
        )
        self.assertEqual("FAILED", failed["delivery_state"])
        self.assertEqual("FAILED", failed["delivery"]["status"])
        self.assertEqual(
            "PROVIDER_REPLY_FAILED:ClaudeResidentError",
            failed["delivery"]["detail"],
        )

    def test_master_bridge_stream_event_is_authenticated_and_published(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, registered = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "opaque-project-master-session",
                "binding_evidence_ref": "project-host://GCS/master-session/registered",
            },
            self.token,
        )
        self.assertEqual(201, status)
        bridge = registered["bridge"]
        cursor = self.server.project_room_events.cursor()
        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        try:
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/stream",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": "room_1234567890abcdef1234567890abcdef",
                    "event": "DELTA",
                    "sequence": 2,
                    "delta": "partial answer",
                    "detail": "",
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)

        self.assertEqual(202, status)
        self.assertEqual("PROJECT_MASTER_STREAM_EVENT_ACCEPTED", result["status"])
        events = self.server.project_room_events.wait(
            "GCS",
            after_event_id=cursor,
            timeout_seconds=0.1,
        )
        self.assertEqual(1, len(events))
        self.assertEqual("MASTER_STREAM", events[0]["payload"]["type"])
        self.assertEqual("partial answer", events[0]["payload"]["delta"])

    def test_feature_node_proposals_are_reviewable_and_never_materialize_work(self) -> None:
        self.server.store.register_project(self.registration())
        status, memory_payload = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "rag.record-decision",
                "request": {
                    "project_id": "GCS",
                    "decision_ref": "semantic-editor-protocol",
                    "title": "Rust native semantic editor protocol",
                    "body": "Use compact symbol IR for agent editing.",
                    "node_ref": "universe",
                    "graph": "functional",
                },
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("RAG_DECISION_RECORDED", memory_payload["status"])
        memory_id = memory_payload["memory"]["memory_id"]

        status, generated = self.request(
            "POST",
            "/v1/projects/GCS/feature-node-proposals/generate",
            {},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual(1, generated["created_count"])
        self.assertEqual(0, generated["replayed_count"])
        proposal = generated["proposals"][0]
        self.assertEqual(FEATURE_NODE_PROPOSAL_SCHEMA, proposal["schema"])
        self.assertEqual("NEW_FEATURE", proposal["proposal_kind"])
        self.assertEqual("PROPOSAL_ONLY", proposal["state"])
        self.assertEqual(
            [f"universe://memories/{memory_id}"], proposal["evidence_refs"]
        )
        self.assertTrue(all(value is False for value in generated["effects"].values()))

        app = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/feature-node-proposals", app)
        self.assertIn("renderFeatureNodeProposalDetails", app)
        self.assertIn("featureProposalEvidenceItems", app)
        self.assertIn("Why this was proposed", app)
        self.assertIn("Source evidence is candidate-only", app)
        self.assertIn("appendFeatureProposalReview", app)
        self.assertIn("proposal-review-editor", app)
        self.assertIn("Submit rejection", app)
        self.assertNotIn("window.prompt(", app)
        self.assertIn("Start planning", app)
        self.assertIn("/explorations", app)

        status, listed = self.request(
            "GET", "/v1/projects/GCS/feature-node-proposals", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            [proposal["proposal_id"]],
            [item["proposal_id"] for item in listed["proposals"]],
        )
        status, detail = self.request(
            "GET",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}",
            None,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(proposal["proposal_digest"], detail["proposal"]["proposal_digest"])

        graph = self.server.store.semantic_project_graph("GCS")
        proposal_nodes = [
            node
            for node in graph["nodes"]
            if node["entity_type"] == "FEATURE_NODE_PROPOSAL"
        ]
        self.assertEqual(1, len(proposal_nodes))
        self.assertEqual(1, proposal_nodes[0]["data"]["evidence_count"])
        self.assertFalse(proposal_nodes[0]["data"]["body_in_graph"])
        self.assertIn(
            "PROJECT_HAS_FEATURE_NODE_PROPOSAL",
            {edge["edge_type"] for edge in graph["edges"]},
        )

        status, review_required = self.request(
            "POST",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/explorations",
            {"expected_proposal_digest": proposal["proposal_digest"]},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "FEATURE_NODE_PROPOSAL_EXPLORATION_REVIEW_REQUIRED",
            review_required["error_code"],
        )

        review_request = {
            "decision": "EXPLORE",
            "rationale": "Generate alternative Expected Paths",
        }
        status, reviewed = self.request(
            "POST",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/reviews",
            review_request,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("EXPLORE", reviewed["proposal"]["state"])
        self.assertEqual("USER", reviewed["proposal"]["review"]["reviewed_by_role"])
        self.assertFalse(reviewed["feature_node_created"])
        self.assertFalse(reviewed["goal_created"])
        self.assertFalse(reviewed["todo_created"])
        self.assertEqual([], self.server.store.list_feature_nodes("GCS"))
        self.assertEqual([], self.server.store.list_project_goals("GCS"))
        self.assertEqual([], [todo for todo in self.server.store.list_todos() if todo["project_id"] == "GCS"])

        status, replayed_review = self.request(
            "POST",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/reviews",
            review_request,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "FEATURE_NODE_PROPOSAL_REVIEW_REPLAYED", replayed_review["status"]
        )
        status, conflict = self.request(
            "POST",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/reviews",
            {"decision": "REJECT", "rationale": "Changed decision"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "FEATURE_NODE_PROPOSAL_REVIEW_CONFLICT", conflict["error_code"]
        )

        status, stale = self.request(
            "POST",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/explorations",
            {"expected_proposal_digest": "0" * 64},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("FEATURE_NODE_PROPOSAL_DIGEST_CONFLICT", stale["error_code"])

        exploration_request = {
            "expected_proposal_digest": proposal["proposal_digest"]
        }
        status, started = self.request(
            "POST",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/explorations",
            exploration_request,
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("FEATURE_NODE_EXPLORATION_STARTED", started["status"])
        context = started["planning_context"]
        feature = started["feature"]
        room = started["room"]
        self.assertEqual("universe.node-planning-context.v1", context["schema"])
        self.assertEqual(proposal["proposal_id"], context["proposal_id"])
        self.assertEqual(proposal["proposal_digest"], context["proposal_digest"])
        self.assertEqual(feature["feature_id"], context["feature_id"])
        self.assertEqual(room["room_id"], context["room_id"])
        self.assertEqual("REFERENCES_AND_PROPOSAL_SUMMARY_ONLY", context["redaction"])
        self.assertNotIn("Use compact symbol IR for agent editing.", json.dumps(context))
        self.assertEqual("EXPLORING", feature["state"])
        self.assertEqual("MEETING", room["room_type"])
        self.assertTrue(started["feature_node_created"])
        self.assertTrue(started["meeting_room_created"])
        self.assertTrue(started["planning_context_created"])
        for key in (
            "goal_created",
            "todo_created",
            "task_frame_created",
            "authority_created",
            "execution_assignment_created",
            "rag_adopted",
        ):
            self.assertFalse(started[key])
        bindings = self.server.multi_rooms.list_bindings(room["room_id"])
        self.assertEqual(
            1,
            len(
                [
                    item
                    for item in bindings
                    if item["slot_role"] == "CONDUCTOR" and item["state"] == "ACTIVE"
                ]
            ),
        )

        status, collected_context = self.request(
            "GET",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/planning-context",
            None,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            context["context_digest"],
            collected_context["planning_context"]["context_digest"],
        )
        status, replayed_exploration = self.request(
            "POST",
            f"/v1/feature-node-proposals/{proposal['proposal_id']}/explorations",
            exploration_request,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "FEATURE_NODE_EXPLORATION_REPLAYED", replayed_exploration["status"]
        )
        self.assertEqual(
            context["context_id"], replayed_exploration["planning_context"]["context_id"]
        )
        self.assertEqual(feature["feature_id"], replayed_exploration["feature"]["feature_id"])
        self.assertEqual(room["room_id"], replayed_exploration["room"]["room_id"])
        self.assertEqual(1, len(self.server.store.list_feature_nodes("GCS")))
        graph_after = self.server.store.semantic_project_graph("GCS")
        context_nodes = [
            node
            for node in graph_after["nodes"]
            if node["entity_type"] == "NODE_PLANNING_CONTEXT"
        ]
        self.assertEqual(1, len(context_nodes))
        self.assertFalse(context_nodes[0]["data"]["body_in_graph"])
        edge_types = {edge["edge_type"] for edge in graph_after["edges"]}
        self.assertTrue(
            {
                "PROJECT_HAS_NODE_PLANNING_CONTEXT",
                "FEATURE_NODE_PROPOSAL_STARTS_PLANNING",
                "NODE_PLANNING_CONTEXT_FOR_FEATURE",
                "NODE_PLANNING_CONTEXT_OPENS_MEETING_ROOM",
            }.issubset(edge_types)
        )
        self.assertEqual([], self.server.store.list_project_goals("GCS"))
        self.assertEqual(
            [],
            [
                todo
                for todo in self.server.store.list_todos()
                if todo["project_id"] == "GCS"
            ],
        )

        status, replayed_generation = self.request(
            "POST",
            "/v1/projects/GCS/feature-node-proposals/generate",
            {},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(0, replayed_generation["created_count"])
        self.assertEqual(1, replayed_generation["replayed_count"])
        self.assertEqual("EXPLORE", replayed_generation["proposals"][0]["state"])

    def test_existing_feature_proposal_opens_planning_without_duplicate_feature(self) -> None:
        self.server.store.register_project(self.registration())
        existing, created = self.server.store.create_feature_node(
            "GCS",
            {
                "idempotency_key": "existing-semantic-editor",
                "title": "Native semantic editor protocol",
                "intent_text": "Compact symbol IR for agent editing",
                "created_by_role": "USER",
                "evidence_refs": [],
            },
        )
        self.assertTrue(created)
        self.server.store.create_project_memory(
            "GCS",
            {
                "title": "Native semantic editor protocol",
                "body": "Do not include this raw body in planning context.",
                "state": "DECISION_NOTE",
            },
        )
        generated = self.server.store.generate_feature_node_proposals("GCS")
        self.assertEqual(1, generated["created_count"])
        proposal = generated["proposals"][0]
        self.assertEqual("LINK_EXISTING", proposal["proposal_kind"])
        self.assertEqual(existing["feature_id"], proposal["target_node_ref"])
        self.server.store.review_feature_node_proposal(
            proposal["proposal_id"],
            {"decision": "EXPLORE", "rationale": "Extend existing node"},
        )
        context, feature, room, context_created, feature_created, room_created = (
            self.server.store.explore_feature_node_proposal(
                proposal["proposal_id"],
                {"expected_proposal_digest": proposal["proposal_digest"]},
            )
        )
        self.assertTrue(context_created)
        self.assertFalse(feature_created)
        self.assertTrue(room_created)
        self.assertEqual(existing["feature_id"], feature["feature_id"])
        self.assertEqual(2, feature["revision"])
        self.assertEqual("EXPLORING", feature["state"])
        self.assertEqual(room["room_id"], feature["meeting_room_id"])
        self.assertEqual([existing["feature_id"]], context["neighbor_feature_refs"])
        replay = self.server.store.explore_feature_node_proposal(
            proposal["proposal_id"],
            {"expected_proposal_digest": proposal["proposal_digest"]},
        )
        self.assertEqual(context["context_id"], replay[0]["context_id"])
        self.assertEqual((False, False, False), replay[3:])
        self.assertEqual(1, len(self.server.store.list_feature_nodes("GCS")))

    def test_feature_node_expected_path_adoption_http_and_graph(self) -> None:
        self.server.store.register_project(self.registration())
        created = self.server.multi_rooms.create_meeting_room(
            {"title": "Feature planning", "topic": "Expected Paths", "project_id": "GCS"}
        )
        room_id = created["room"]["room_id"]
        status, feature_payload = self.request(
            "POST",
            "/v1/projects/GCS/feature-nodes",
            {
                "idempotency_key": "meeting-room-feature-v1",
                "title": "Meeting Room coordination",
                "intent_text": "Agents produce several implementation specifications for user adoption.",
                "meeting_room_id": room_id,
                "evidence_refs": ["docs://multi-room-chat-architecture"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        feature = feature_payload["feature"]
        self.assertEqual("EXPLORING", feature["state"])
        self.assertFalse(feature["effects"]["goal_created"])
        self.assertFalse(feature["effects"]["todo_created"])

        artifacts = []
        for title, body in (("Path A", "Use event-first orchestration"), ("Path B", "Use artifact-first orchestration")):
            artifact = self.server.multi_rooms.create_artifact(
                room_id,
                {
                    "artifact_type": "SPECIFICATION",
                    "title": title,
                    "body_text": body,
                    "state": "CANDIDATE",
                    "author_role": "USER",
                    "evidence_refs": [f"evidence://{title.lower().replace(' ', '-')}"],
                },
            )
            artifacts.append(artifact)

        def route(prefix: str) -> dict[str, Any]:
            return {
                "steps": [
                    {"step_id": f"{prefix}-design", "title": "Design", "summary": "Pin the contract", "phase": "Design"},
                    {"step_id": f"{prefix}-build", "title": "Build", "summary": "Implement the slice", "phase": "Delivery"},
                ],
                "dependencies": [
                    {"from_step_id": f"{prefix}-design", "to_step_id": f"{prefix}-build", "kind": "PRECEDES"}
                ],
                "branches": [],
                "architecture_decisions": ["Use the existing room artifact contract"],
                "implementation_phases": [
                    {"title": "Design", "step_ids": [f"{prefix}-design"]},
                    {"title": "Delivery", "step_ids": [f"{prefix}-build"]},
                ],
                "risks": [{"risk": "Schema drift", "mitigation": "Pin the route digest"}],
                "acceptance_conditions": ["The route is visible in Galaxy"],
                "estimates": {"effort": "SMALL", "cost": "LOCAL", "quota": "LOW"},
                "evidence_refs": [f"evidence://{prefix}"],
            }

        status, first_payload = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/expected-paths",
            {"room_id": room_id, "artifact_id": artifacts[0]["artifact_id"], "summary": "Event-first candidate", "route": route("event")},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        first_path = first_payload["expected_path"]
        self.assertEqual(artifacts[0]["content_digest"], first_path["specification_digest"])
        self.assertEqual(1, first_path["artifact_revision"])
        self.assertEqual(2, len(first_path["route"]["steps"]))
        self.assertTrue(first_path["route_digest"])

        status, blocked = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/adoptions",
            {"expected_path_id": first_path["expected_path_id"], "expected_feature_revision": 2, "rationale": "Select A"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("EXPECTED_PATH_ALTERNATIVES_REQUIRED", blocked["error_code"])

        status, second_payload = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/expected-paths",
            {"room_id": room_id, "artifact_id": artifacts[1]["artifact_id"], "summary": "Artifact-first candidate", "route": route("artifact")},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        second_path = second_payload["expected_path"]

        status, goal_before_adoption = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goals",
            {"expected_feature_revision": 3},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("FEATURE_PATH_ADOPTION_REQUIRED", goal_before_adoption["error_code"])

        status, adopted = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/adoptions",
            {
                "expected_path_id": second_path["expected_path_id"],
                "expected_feature_revision": 3,
                "rationale": "Better evidence lineage",
                "evidence_refs": ["decision://user-selection"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("USER", adopted["adoption"]["adopted_by_role"])
        self.assertEqual("ADOPTED", adopted["feature"]["state"])
        states = {item["expected_path_id"]: item["state"] for item in adopted["feature"]["expected_paths"]}
        self.assertEqual("NOT_SELECTED", states[first_path["expected_path_id"]])
        self.assertEqual("ADOPTED", states[second_path["expected_path_id"]])
        self.assertFalse(adopted["adoption"]["effects"]["goal_created"])
        self.assertFalse(adopted["adoption"]["effects"]["todo_created"])

        adoption_request = {
            "expected_path_id": second_path["expected_path_id"],
            "expected_feature_revision": 3,
            "rationale": "Better evidence lineage",
            "evidence_refs": ["decision://user-selection"],
        }
        status, adoption_replay = self.request(
            "POST", f"/v1/feature-nodes/{feature['feature_id']}/adoptions", adoption_request, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("EXPECTED_PATH_ADOPTION_REPLAYED", adoption_replay["status"])
        status, adoption_conflict = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/adoptions",
            {**adoption_request, "rationale": "Changed rationale"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("FEATURE_PATH_ADOPTION_CONFLICT", adoption_conflict["error_code"])

        status, replay = self.request(
            "POST",
            "/v1/projects/GCS/feature-nodes",
            {
                "idempotency_key": "meeting-room-feature-v1",
                "title": "Meeting Room coordination",
                "intent_text": "Agents produce several implementation specifications for user adoption.",
                "meeting_room_id": room_id,
                "evidence_refs": ["docs://multi-room-chat-architecture"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FEATURE_NODE_REPLAYED", replay["status"])

        status, feature_list = self.request(
            "GET", "/v1/projects/GCS/feature-nodes", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual([feature["feature_id"]], [item["feature_id"] for item in feature_list["features"]])
        status, feature_detail = self.request(
            "GET", f"/v1/feature-nodes/{feature['feature_id']}", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(2, len(feature_detail["feature"]["expected_paths"]))
        self.assertEqual([], self.server.store.list_project_goals("GCS"))
        self.assertEqual([], [todo for todo in self.server.store.list_todos() if todo["project_id"] == "GCS"])

        status, rejected = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/adoptions",
            {"expected_path_id": first_path["expected_path_id"], "expected_feature_revision": 4, "rationale": "Change selection"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("FEATURE_PATH_ALREADY_ADOPTED", rejected["error_code"])

        status, materialized = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goals",
            {"expected_feature_revision": 4},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("FEATURE_GOAL_MATERIALIZED", materialized["status"])
        self.assertEqual("DESIGNING", materialized["goal"]["state"])
        self.assertEqual("UNASSIGNED", materialized["goal"]["owner"])
        self.assertEqual("NODE", materialized["goal"]["scope_kind"])
        self.assertEqual(feature["feature_id"], materialized["goal"]["node_ref"])
        self.assertTrue(materialized["goal_created"])
        self.assertEqual(second_path["expected_path_id"], materialized["derivation"]["expected_path_id"])
        self.assertEqual(second_path["artifact_revision"], materialized["derivation"]["artifact_revision"])
        self.assertEqual(second_path["specification_digest"], materialized["derivation"]["specification_digest"])
        self.assertFalse(materialized["todo_created"])
        self.assertFalse(materialized["milestone_created"])
        self.assertFalse(materialized["task_frame_created"])
        self.assertFalse(materialized["authority_created"])
        self.assertFalse(materialized["execution_assignment_created"])

        status, replayed_goal = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goals",
            {"expected_feature_revision": 4},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FEATURE_GOAL_REPLAYED", replayed_goal["status"])
        self.assertFalse(replayed_goal["goal_created"])
        self.assertEqual(materialized["goal"]["goal_id"], replayed_goal["goal"]["goal_id"])
        self.assertEqual(1, len(self.server.store.list_project_goals("GCS")))
        self.assertEqual([], [todo for todo in self.server.store.list_todos() if todo["project_id"] == "GCS"])

        status, feature_after_goal = self.request(
            "GET", f"/v1/feature-nodes/{feature['feature_id']}", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            materialized["goal"]["goal_id"],
            feature_after_goal["feature"]["goal_derivation"]["goal_id"],
        )

        graph = self.server.store.semantic_project_graph("GCS")
        node_types = {node["entity_type"] for node in graph["nodes"]}
        edge_types = {edge["edge_type"] for edge in graph["edges"]}
        self.assertIn("FEATURE_NODE", node_types)
        self.assertIn("EXPECTED_PATH", node_types)
        self.assertIn("EXPECTED_PATH_STEP", node_types)
        self.assertIn("GOAL", node_types)
        self.assertIn("FEATURE_NODE_ADOPTS_EXPECTED_PATH", edge_types)
        self.assertIn("FEATURE_NODE_HAS_GOAL", edge_types)
        self.assertIn("FEATURE_NODE_DERIVES_GOAL", edge_types)
        self.assertIn("EXPECTED_PATH_DERIVES_GOAL", edge_types)
        self.assertIn("EXPECTED_PATH_HAS_STEP", edge_types)
        self.assertIn("EXPECTED_PATH_STEP_PRECEDES", edge_types)
        expected_nodes = [node for node in graph["nodes"] if node["entity_type"] == "EXPECTED_PATH"]
        self.assertTrue(all("body_text" not in node["data"] for node in expected_nodes))

        for provider, session_ref, chat_key in (
            ("CODEX", "codex-work-plan-session", "codex-work-plan-chat"),
            ("CLAUDE", "claude-work-plan-session", "claude-work-plan-chat"),
        ):
            self.server.multi_rooms.attach_session(
                room_id,
                {
                    "slot_role": "MODEL",
                    "provider": provider,
                    "provider_session_ref": session_ref,
                    "provider_chat_key": chat_key,
                    "display_name": provider.title(),
                },
            )

        def invoke_work_plan(binding, turn):
            provider = str(binding["provider"])
            return {
                "status": "COMPLETED",
                "body_text": json.dumps(
                    {
                        "title": f"{provider} delivery plan",
                        "summary": f"Bounded {provider} plan for the adopted Expected Path.",
                        "milestones": [
                            {
                                "title": f"{provider} foundation",
                                "description": "Build the first reviewable planning slice.",
                                "todos": [
                                    {
                                        "title": f"Implement {provider} slice",
                                        "detail": "Create the bounded implementation increment.",
                                        "acceptance": "Targeted tests pass and provenance remains visible.",
                                        "priority": "AUTO",
                                    },
                                    {
                                        "title": f"Verify {provider} slice",
                                        "detail": "Review the increment without starting execution.",
                                        "acceptance": "No Task Frame, authority, or assignment exists.",
                                        "priority": "P2",
                                    },
                                ],
                            }
                        ],
                    }
                ),
                "provider_event_id": f"work-plan-{provider.lower()}-{turn['turn_number']}",
            }

        self.server.multi_room_meetings.invoke_provider = invoke_work_plan
        goal_id = materialized["goal"]["goal_id"]
        status, plans = self.request(
            "POST",
            f"/v1/goals/{goal_id}/work-plan-runs",
            {"run_id": "goal-work-plan-http-1", "max_turns": 4},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GOAL_WORK_PLAN_CANDIDATES_READY", plans["status"])
        self.assertEqual(2, len(plans["candidates"]))
        self.assertFalse(plans["milestone_created"])
        self.assertFalse(plans["todo_created"])
        self.assertEqual([], [todo for todo in self.server.store.list_todos() if todo["project_id"] == "GCS"])

        status, apply_blocked = self.request(
            "POST",
            f"/v1/goals/{goal_id}/work-plan-applications",
            {"expected_goal_revision": 1},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("GOAL_WORK_PLAN_ADOPTION_REQUIRED", apply_blocked["error_code"])

        selected = plans["candidates"][1]
        status, plan_adoption = self.request(
            "POST",
            f"/v1/goals/{goal_id}/work-plan-adoptions",
            {
                "work_plan_id": selected["work_plan_id"],
                "expected_goal_revision": 1,
                "rationale": "User selected the clearer bounded plan.",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("USER", plan_adoption["adoption"]["adopted_by_role"])
        self.assertFalse(plan_adoption["milestone_created"])
        self.assertFalse(plan_adoption["todo_created"])
        plan_states = {item["state"] for item in plan_adoption["surface"]["candidates"]}
        self.assertEqual({"ADOPTED", "NOT_SELECTED"}, plan_states)

        status, applied = self.request(
            "POST",
            f"/v1/goals/{goal_id}/work-plan-applications",
            {"expected_goal_revision": 1},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("GOAL_WORK_PLAN_APPLIED", applied["status"])
        self.assertTrue(applied["milestone_created"])
        self.assertTrue(applied["todo_created"])
        self.assertFalse(applied["task_frame_created"])
        self.assertFalse(applied["authority_created"])
        self.assertFalse(applied["execution_assignment_created"])
        self.assertEqual(1, applied["application"]["created_items"]["milestone_count"])
        self.assertEqual(2, applied["application"]["created_items"]["todo_count"])
        created_todos = [todo for todo in self.server.store.list_todos() if todo["project_id"] == "GCS"]
        self.assertEqual({"BACKLOG"}, {todo["state"] for todo in created_todos})
        self.assertEqual({"NODE"}, {todo["scope_kind"] for todo in created_todos})
        self.assertEqual(
            {feature["feature_id"]}, {todo["node_ref"] for todo in created_todos}
        )

        status, apply_replay = self.request(
            "POST",
            f"/v1/goals/{goal_id}/work-plan-applications",
            {"expected_goal_revision": 1},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GOAL_WORK_PLAN_APPLICATION_REPLAYED", apply_replay["status"])
        self.assertFalse(apply_replay["milestone_created"])
        self.assertFalse(apply_replay["todo_created"])
        self.assertEqual(2, len([todo for todo in self.server.store.list_todos() if todo["project_id"] == "GCS"]))

        status, automation_ready = self.request(
            "GET", f"/v1/goals/{goal_id}/automation", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "READY_FOR_MASTER_HANDOFF", automation_ready["automation_state"]
        )
        self.assertEqual(
            "CREATE_AND_DELIVER_MASTER_HANDOFF",
            automation_ready["next_operation"],
        )

        status, handoff_result = self.request(
            "POST",
            "/v1/projects/GCS/master-handoffs",
            {
                "source": {
                    "kind": "GOAL_WORK_PLAN",
                    "application_id": applied["application"]["application_id"],
                },
                "purpose": "Execute the adopted Goal Work Plan.",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        handoff = handoff_result["handoff"]
        self.assertEqual("PROPOSAL_ONLY", handoff["delivery_state"])
        self.assertEqual("GOAL_WORK_PLAN", handoff["source"]["kind"])
        self.assertEqual(
            "universe://projects/GCS/goal-work-plan-applications/"
            + applied["application"]["application_id"],
            handoff["instruction_ref"],
        )
        self.assertEqual(goal_id, handoff["source"]["goal"]["goal_id"])
        self.assertEqual(
            applied["application"]["application_id"],
            handoff["source"]["application"]["application_id"],
        )
        self.assertEqual(
            selected["work_plan_id"], handoff["source"]["work_plan"]["work_plan_id"]
        )
        self.assertEqual(
            applied["application"]["created_items"]["todo_ids"],
            [todo["todo_id"] for todo in handoff["source"]["todos"]],
        )
        self.assertTrue(all(value == "NONE" for value in handoff["effects"].values()))

        status, repeated_handoff = self.request(
            "POST",
            "/v1/projects/GCS/master-handoffs",
            {
                "source": {
                    "kind": "GOAL_WORK_PLAN",
                    "application_id": applied["application"]["application_id"],
                },
                "purpose": "Execute the adopted Goal Work Plan.",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(handoff["handoff_id"], repeated_handoff["handoff"]["handoff_id"])

        status, handoff_ready = self.request(
            "GET", f"/v1/goals/{goal_id}/automation", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("MASTER_HANDOFF_READY", handoff_ready["automation_state"])

        proposal = self.create_task_proposal_fixture(
            request_ref=handoff["instruction_ref"]
        )
        frame_request = {
            "handoff_digest": handoff["handoff_digest"],
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "task_frame": {"frame_id": "goal-plan-frame-001"},
        }
        status, not_delivered = self.request(
            "POST",
            f"/v1/projects/GCS/master-handoffs/{handoff['handoff_id']}"
            "/instruction-task-frame",
            frame_request,
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("MASTER_HANDOFF_DELIVERY_REQUIRED", not_delivered["error_code"])

        status, advanced_handoff = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/advance",
            {"approval": "ADVANCE", "expected_goal_revision": 1},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "GOAL_AUTOMATION_TASK_FRAME_INPUT_REQUIRED",
            advanced_handoff["status"],
        )
        self.assertEqual(
            ["MASTER_HANDOFF_DELIVERED"], advanced_handoff["operations"]
        )
        self.assertEqual(
            "MASTER_PROPOSAL_READY",
            advanced_handoff["surface"]["automation_state"],
        )
        proposal_database = (
            self.project_root
            / ".ai"
            / "runtime"
            / "task_frames"
            / "task-proposals.sqlite3"
        )
        connection = sqlite3.connect(proposal_database)
        try:
            wrong_lineage = {**proposal, "request_ref": "universe://wrong-handoff"}
            connection.execute(
                "UPDATE proposal SET proposal_json = ? WHERE proposal_id = ?",
                (
                    json.dumps(wrong_lineage, sort_keys=True, separators=(",", ":")),
                    proposal["proposal_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        status, wrong_proposal = self.request(
            "POST",
            f"/v1/projects/GCS/master-handoffs/{handoff['handoff_id']}"
            "/instruction-task-frame",
            frame_request,
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "MASTER_HANDOFF_PROPOSAL_LINEAGE_MISMATCH",
            wrong_proposal["error_code"],
        )
        connection = sqlite3.connect(proposal_database)
        try:
            connection.execute(
                "UPDATE proposal SET proposal_json = ? WHERE proposal_id = ?",
                (
                    json.dumps(proposal, sort_keys=True, separators=(",", ":")),
                    proposal["proposal_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()
        created_frame = {
            "schema": "universe.local-service.v1",
            "status": "INSTRUCTION_TASK_FRAME_CREATED",
            "task_frame": {"task_frame_id": "goal-plan-frame-001"},
        }
        with patch.object(
            self.server,
            "create_instruction_authorized_task_frame",
            return_value=created_frame,
        ) as create_frame:
            status, bound = self.request(
                "POST",
                f"/v1/goals/{goal_id}/automation/advance",
                {
                    "approval": "ADVANCE",
                    "expected_goal_revision": 1,
                    "task_frame": frame_request["task_frame"],
                },
                self.token,
            )
            replay_status, replay_binding = self.request(
                "POST",
                f"/v1/goals/{goal_id}/automation/advance",
                {
                    "approval": "ADVANCE",
                    "expected_goal_revision": 1,
                    "task_frame": frame_request["task_frame"],
                },
                self.token,
            )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GOAL_AUTOMATION_ADVANCED", bound["status"])
        self.assertEqual(["TASK_FRAME_BOUND"], bound["operations"])
        self.assertEqual(
            "goal-plan-frame-001", bound["surface"]["binding"]["task_frame_id"]
        )
        self.assertEqual(
            handoff["instruction_ref"],
            bound["surface"]["binding"]["instruction_ref"],
        )
        self.assertEqual(HTTPStatus.OK, replay_status)
        self.assertEqual("GOAL_AUTOMATION_ADVANCED", replay_binding["status"])
        self.assertEqual([], replay_binding["operations"])
        self.assertEqual(
            "TASK_FRAME_READY", replay_binding["surface"]["automation_state"]
        )
        create_frame.assert_called_once()

        actor_session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "goal-automation-conductor-session",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "goal-automation-conductor-provider",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        actor = {
            "provider": "CODEX",
            "provider_session_ref": "goal-automation-conductor-provider",
            "session_id": actor_session["session_id"],
            "session_anchor_ref": actor_session["session_anchor_ref"],
            "expected_goal_revision": 1,
        }
        todo_ids = applied["application"]["created_items"]["todo_ids"]
        status, before_selection = self.request(
            "GET", f"/v1/goals/{goal_id}/automation", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "SELECT_TODOS_FOR_EXECUTION", before_selection["next_operation"]
        )
        self.assertEqual(
            todo_ids, before_selection["todo_execution"]["eligible_todo_ids"]
        )

        status, selected_todos = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/todo-selection",
            {**actor, "approval": "SELECT_TODOS", "todo_ids": todo_ids},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "GOAL_TODOS_SELECTED_FOR_EXECUTION", selected_todos["status"]
        )
        self.assertEqual(todo_ids, selected_todos["selection"]["todo_ids"])
        self.assertEqual(
            {"IN_PROGRESS"},
            {item["todo"]["state"] for item in selected_todos["actions"]},
        )
        self.assertTrue(
            all(item["receipt"]["status"] == "CONSUMED" for item in selected_todos["actions"])
        )

        status, selection_replay = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/todo-selection",
            {**actor, "approval": "SELECT_TODOS", "todo_ids": todo_ids},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "GOAL_TODO_EXECUTION_SELECTION_REPLAYED", selection_replay["status"]
        )
        self.assertTrue(all(item["replayed"] for item in selection_replay["actions"]))

        status, selection_conflict = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/todo-selection",
            {**actor, "approval": "SELECT_TODOS", "todo_ids": todo_ids[:1]},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "GOAL_TODO_SELECTION_CONFLICT", selection_conflict["error_code"]
        )

        self.server.task_frame_lineage.create_task_frame(
            frame_ref="goal-plan-frame-001",
            origin_session_anchor_ref=actor_session["session_anchor_ref"],
        )
        result, _ = self.server.task_frame_lineage.attach_result(
            result_ref="goal-plan-result-partial-001",
            frame_ref="goal-plan-frame-001",
            origin_session_anchor_ref=actor_session["session_anchor_ref"],
            result={
                "todo_actions": [
                    {
                        "todo_id": todo_ids[0],
                        "outcome": "COMPLETED",
                        "evidence_ref": "task-frame://goal-plan-frame-001/result/first",
                        "validation": {
                            "status": "PASSED",
                            "evidence_ref": "test-run://goal-plan-frame-001/first/pass",
                        },
                    }
                ]
            },
        )
        status, projected = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/todo-results",
            {**actor, "approval": "APPLY_RESULT", "result_ref": result["result_ref"]},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GOAL_TASK_FRAME_TODO_RESULT_APPLIED", projected["status"])
        self.assertEqual("DONE", self.server.store.get_todo(todo_ids[0])["state"])
        self.assertEqual(
            "IN_PROGRESS", self.server.store.get_todo(todo_ids[1])["state"]
        )
        self.assertEqual(3, len(projected["surface"]["todo_execution"]["action_receipts"]))
        self.assertEqual(
            "APPLY_TASK_FRAME_RESULT", projected["surface"]["next_operation"]
        )

        status, result_replay = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/todo-results",
            {**actor, "approval": "APPLY_RESULT", "result_ref": result["result_ref"]},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertTrue(result_replay["actions"][0]["replayed"])

        invalid_result, _ = self.server.task_frame_lineage.attach_result(
            result_ref="goal-plan-result-invalid-002",
            frame_ref="goal-plan-frame-001",
            origin_session_anchor_ref=actor_session["session_anchor_ref"],
            result={
                "todo_actions": [
                    {
                        "todo_id": todo_ids[1],
                        "outcome": "COMPLETED",
                        "evidence_ref": "task-frame://goal-plan-frame-001/result/second",
                    }
                ]
            },
        )
        status, validation_blocked = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/todo-results",
            {
                **actor,
                "approval": "APPLY_RESULT",
                "result_ref": invalid_result["result_ref"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "TODO_COMPLETION_VALIDATION_REQUIRED",
            validation_blocked["error_code"],
        )
        self.assertEqual(
            "IN_PROGRESS", self.server.store.get_todo(todo_ids[1])["state"]
        )

        outside_result, _ = self.server.task_frame_lineage.attach_result(
            result_ref="goal-plan-result-outside-selection-003",
            frame_ref="goal-plan-frame-001",
            origin_session_anchor_ref=actor_session["session_anchor_ref"],
            result={
                "todo_actions": [
                    {
                        "todo_id": "todo_outside_goal_selection",
                        "outcome": "FAILED",
                        "evidence_ref": "task-frame://goal-plan-frame-001/result/outside",
                    }
                ]
            },
        )
        status, outside_blocked = self.request(
            "POST",
            f"/v1/goals/{goal_id}/automation/todo-results",
            {
                **actor,
                "approval": "APPLY_RESULT",
                "result_ref": outside_result["result_ref"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "GOAL_TASK_FRAME_RESULT_TODO_MISMATCH",
            outside_blocked["error_code"],
        )
        self.assertEqual(
            "IN_PROGRESS", self.server.store.get_todo(todo_ids[1])["state"]
        )

        status, work_plan_surface = self.request(
            "GET", f"/v1/goals/{goal_id}/work-plans", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(selected["work_plan_id"], work_plan_surface["adoption"]["work_plan_id"])
        self.assertEqual(selected["work_plan_id"], work_plan_surface["application"]["work_plan_id"])

        planned_graph = self.server.store.semantic_project_graph("GCS")
        planned_node_types = {node["entity_type"] for node in planned_graph["nodes"]}
        planned_edge_types = {edge["edge_type"] for edge in planned_graph["edges"]}
        self.assertIn("GOAL_WORK_PLAN", planned_node_types)
        self.assertIn("GOAL_HAS_WORK_PLAN", planned_edge_types)
        self.assertIn("GOAL_ADOPTS_WORK_PLAN", planned_edge_types)
        work_plan_nodes = [node for node in planned_graph["nodes"] if node["entity_type"] == "GOAL_WORK_PLAN"]
        self.assertEqual(2, len(work_plan_nodes))
        self.assertTrue(all("plan" not in node["data"] for node in work_plan_nodes))
        self.assertTrue(all(node["data"]["plan_in_graph"] is False for node in work_plan_nodes))

    def test_action_server_rejects_client_context_claims(self) -> None:
        with self.assertRaises(UniverseError) as raised:
            self.server.execute_action(
                "feature.goal.start",
                {"feature_id": "feature-for-test", "mode": "MASTER"},
                source="TEST_CALLER",
            )
        self.assertEqual("ACTION_CALLER_CONTEXT_FORBIDDEN", raised.exception.code)
        context = self.server.resolve_action_context("feature.goal.start", "TEST")
        self.assertEqual("USER", context["actor"]["role"])
        self.assertNotEqual("TEST_CALLER", context["source"])

    def test_rag_adopt_requires_keep_review_and_is_digest_pinned(self) -> None:
        self.server.store.register_project(self.registration())
        candidate, created = self.server.store.create_memory_candidate(
            "GCS",
            {
                "stage": "FAST_EXTRACT",
                "kind": "MEMORY",
                "summary": "Keep this bounded project memory.",
                "source_session": {
                    "provider": "CODEX",
                    "provider_session_id": "codex-rag-adopt-test",
                },
                "source_range": {"start": 4, "end": 4},
                "ref_digests": ["activity-rag-adopt-test"],
            },
        )
        self.assertTrue(created)
        action_request = {
            "action_id": "rag.adopt",
            "request": {
                "candidate_id": candidate["candidate_id"],
                "expected_candidate_digest": candidate["candidate_digest"],
            },
        }

        status, blocked = self.request("POST", "/v1/actions", action_request, self.token)
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("RAG_ADOPT_REVIEW_REQUIRED", blocked["error_code"])
        self.assertEqual([], self.server.store.list_project_memories("GCS"))

        kept, changed = self.server.store.review_memory_candidate(
            candidate["candidate_id"], {"decision": "KEEP"}
        )
        self.assertTrue(changed)
        self.assertEqual("KEEP", kept["state"])

        status, adopted = self.request("POST", "/v1/actions", action_request, self.token)
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("RAG_MEMORY_ADOPTED", adopted["status"])
        self.assertEqual("universe.rag-adopt-receipt.v1", adopted["schema"])
        self.assertTrue(adopted["adopted"])
        self.assertTrue(adopted["canonical_memory_created"])
        self.assertFalse(adopted["authority_created"])
        self.assertFalse(adopted["execution_assignment_created"])
        self.assertFalse(adopted["task_frame_created"])
        self.assertFalse(adopted["repository_pushed"])
        memory = adopted["memory"]
        self.assertEqual("OBSERVED", memory["state"])
        self.assertEqual("UNLINKED", memory["link_state"])
        self.assertEqual(
            f"universe://memory-candidates/{candidate['candidate_digest']}/{candidate['candidate_id']}",
            memory["origin_ref"],
        )
        self.assertEqual(1, len(self.server.store.list_project_memories("GCS")))

        status, replay = self.request("POST", "/v1/actions", action_request, self.token)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("RAG_MEMORY_ADOPTION_REPLAYED", replay["status"])
        self.assertFalse(replay["adopted"])
        self.assertEqual(memory["memory_id"], replay["memory"]["memory_id"])
        self.assertEqual(1, len(self.server.store.list_project_memories("GCS")))

        status, digest_conflict = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "rag.adopt",
                "request": {
                    "candidate_id": candidate["candidate_id"],
                    "expected_candidate_digest": "0" * 64,
                },
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "RAG_ADOPT_CANDIDATE_DIGEST_CONFLICT",
            digest_conflict["error_code"],
        )

        idea, _ = self.server.store.create_memory_candidate(
            "GCS",
            {
                "stage": "SYNTHESIZE",
                "kind": "IDEA",
                "summary": "An idea remains a candidate until another product decision.",
            },
        )
        self.server.store.review_memory_candidate(
            idea["candidate_id"], {"decision": "KEEP"}
        )
        status, non_memory = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "rag.adopt",
                "request": {
                    "candidate_id": idea["candidate_id"],
                    "expected_candidate_digest": idea["candidate_digest"],
                },
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("RAG_ADOPT_KIND_INVALID", non_memory["error_code"])
        self.assertEqual(1, len(self.server.store.list_project_memories("GCS")))

    def test_rag_adopt_does_not_trust_candidate_keep_state_without_review(self) -> None:
        self.server.store.register_project(self.registration())
        candidate, created = self.server.store.create_memory_candidate(
            "GCS",
            {
                "stage": "FAST_EXTRACT",
                "kind": "MEMORY",
                "state": "KEEP",
                "summary": "A state-only candidate is not a review.",
            },
        )
        self.assertTrue(created)

        status, payload = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "rag.adopt",
                "request": {
                    "candidate_id": candidate["candidate_id"],
                    "expected_candidate_digest": candidate["candidate_digest"],
                },
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("RAG_ADOPT_REVIEW_REQUIRED", payload["error_code"])
        self.assertEqual([], self.server.store.list_project_memories("GCS"))

    def test_rag_record_decision_is_linked_idempotent_and_conflict_safe(self) -> None:
        self.server.store.register_project(self.registration())
        direct_status, direct_blocked = self.request(
            "POST",
            "/v1/projects/GCS/memories",
            {
                "title": "Unrouted decision",
                "body": "Use the Action Gateway.",
                "state": "DECISION_NOTE",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, direct_status)
        self.assertEqual(
            "MEMORY_DECISION_ACTION_REQUIRED", direct_blocked["error_code"]
        )

        action_request = {
            "action_id": "rag.record-decision",
            "request": {
                "project_id": "GCS",
                "decision_ref": "universe-governance-loop",
                "title": "Universe governance loop",
                "body": "Conductor coordinates the user-approved project loop.",
                "node_ref": "universe",
                "graph": "functional",
            },
        }
        status, recorded = self.request(
            "POST", "/v1/actions", action_request, self.token
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("RAG_DECISION_RECORDED", recorded["status"])
        self.assertEqual(
            "universe.rag-record-decision-receipt.v1", recorded["schema"]
        )
        self.assertTrue(recorded["recorded"])
        self.assertFalse(recorded["authority_created"])
        self.assertFalse(recorded["execution_assignment_created"])
        self.assertFalse(recorded["task_frame_created"])
        memory = recorded["memory"]
        self.assertEqual("DECISION_NOTE", memory["state"])
        self.assertEqual("LINKED", memory["link_state"])
        self.assertEqual("universe", memory["node_ref"])
        self.assertEqual("functional", memory["graph"])
        self.assertEqual("universe-governance-loop", memory["decision_ref"])
        self.assertEqual(
            "universe://projects/GCS/decision-notes/universe-governance-loop",
            memory["origin_ref"],
        )
        self.assertEqual("RETRIEVAL_READY", memory["next_operation"])
        self.assertEqual(1, len(self.server.store.list_project_memories("GCS")))

        status, replay = self.request(
            "POST", "/v1/actions", action_request, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("RAG_DECISION_RECORD_REPLAYED", replay["status"])
        self.assertFalse(replay["recorded"])
        self.assertEqual(memory["memory_id"], replay["memory"]["memory_id"])
        self.assertEqual(1, len(self.server.store.list_project_memories("GCS")))

        changed_request = {
            "action_id": "rag.record-decision",
            "request": {
                **action_request["request"],
                "body": "Replace the approved decision silently.",
            },
        }
        status, conflict = self.request(
            "POST", "/v1/actions", changed_request, self.token
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("RAG_DECISION_STORAGE_CONFLICT", conflict["error_code"])
        self.assertEqual(1, len(self.server.store.list_project_memories("GCS")))

    def test_feature_goal_start_receipt_combines_path_adoption_and_goal_authority(self) -> None:
        self.server.store.register_project(self.registration())
        room = self.server.multi_rooms.create_meeting_room(
            {"title": "Goal start", "topic": "Choose one route", "project_id": "GCS"}
        )["room"]
        status, feature_payload = self.request(
            "POST",
            "/v1/projects/GCS/feature-nodes",
            {
                "idempotency_key": "goal-start-feature-v1",
                "title": "Bounded Goal Start",
                "intent_text": "Choose a route and start one governed Goal.",
                "meeting_room_id": room["room_id"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        feature = feature_payload["feature"]

        paths = []
        for index in (1, 2):
            artifact = self.server.multi_rooms.create_artifact(
                room["room_id"],
                {
                    "artifact_type": "SPECIFICATION",
                    "title": f"Route {index}",
                    "body_text": f"Implementation route {index}",
                    "state": "CANDIDATE",
                    "author_role": "USER",
                },
            )
            route = {
                "steps": [
                    {
                        "step_id": f"route-{index}-build",
                        "title": f"Build route {index}",
                        "summary": "Deliver the bounded slice",
                        "phase": "Delivery",
                    }
                ],
                "dependencies": [],
                "branches": [],
                "architecture_decisions": ["Use the shared Goal Start endpoint"],
                "implementation_phases": [
                    {"title": "Delivery", "step_ids": [f"route-{index}-build"]}
                ],
                "risks": [{"risk": "Scope drift", "mitigation": "Pin the receipt"}],
                "acceptance_conditions": ["The receipt pins the selected route"],
                "estimates": {"effort": "SMALL", "cost": "LOCAL", "quota": "LOW"},
                "evidence_refs": [f"evidence://route-{index}"],
            }
            status, path_payload = self.request(
                "POST",
                f"/v1/feature-nodes/{feature['feature_id']}/expected-paths",
                {
                    "room_id": room["room_id"],
                    "artifact_id": artifact["artifact_id"],
                    "summary": f"Route {index}",
                    "route": route,
                },
                self.token,
            )
            self.assertEqual(HTTPStatus.CREATED, status)
            paths.append(path_payload["expected_path"])

        request = {
            "expected_path_id": paths[1]["expected_path_id"],
            "expected_feature_revision": 3,
            "expected_path_digest": paths[1]["route_digest"],
            "approved_scope": {"project_id": "GCS", "node_refs": [], "write_roots": []},
            "constraints": ["Stay inside the adopted route", "Stop on scope expansion"],
            "validation": ["The receipt pins the selected route"],
            "local_commit_policy": "LOCAL_COMMITS_ALLOWED",
            "push_policy": "PUSH_PROHIBITED",
            "rationale": "This route has the clearest bounded delivery path",
            "evidence_refs": [f"universe://chat-rooms/{room['room_id']}"],
        }
        status, bad_digest = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goal-start-receipts",
            {**request, "expected_path_digest": "0" * 64},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("EXPECTED_PATH_DIGEST_CONFLICT", bad_digest["error_code"])

        status, started = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goal-start-receipts",
            request,
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("FEATURE_GOAL_STARTED", started["status"])
        self.assertEqual("ACTIVE", started["goal_start_receipt"]["status"])
        self.assertEqual(
            "universe.feature-goal-start-receipt.v1",
            started["goal_start_receipt"]["schema"],
        )
        expected_receipt_id = "goal_start_" + hashlib.sha256(
            json.dumps(
                {
                    "feature_id": feature["feature_id"],
                    "expected_path_id": paths[1]["expected_path_id"],
                    "expected_path_digest": paths[1]["route_digest"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        self.assertEqual(expected_receipt_id, started["goal_start_receipt"]["receipt_id"])
        self.assertEqual(paths[1]["route_digest"], started["goal_start_receipt"]["expected_path_digest"])
        self.assertEqual("PUSH_PROHIBITED", started["goal_start_receipt"]["push_policy"])
        self.assertTrue(started["goal_created"])
        self.assertTrue(started["authority_created"])
        self.assertFalse(started["execution_assignment_created"])
        self.assertFalse(started["repository_pushed"])
        self.assertEqual("GOAL_START_WORK_PLAN_MATERIALIZED", started["plan_materialization"]["status"])
        self.assertEqual(1, started["plan_materialization"]["application"]["created_items"]["todo_count"])
        self.assertEqual("WAITING_MASTER_PROPOSAL", started["automation"]["surface"]["automation_state"])
        self.assertEqual("WAIT", started["next_operation"])
        self.assertEqual(1, len([todo for todo in self.server.store.list_todos() if todo["goal_id"] == started["goal"]["goal_id"]]))
        self.assertEqual("ADOPTED", started["feature"]["state"])
        self.assertEqual("DESIGNING", started["goal"]["state"])

        status, replay = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goal-start-receipts",
            request,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FEATURE_GOAL_START_REPLAYED", replay["status"])
        self.assertEqual("GOAL_START_WORK_PLAN_REPLAYED", replay["plan_materialization"]["status"])
        self.assertFalse(replay["goal_created"])
        self.assertFalse(replay["authority_created"])
        self.assertEqual(started["goal"]["goal_id"], replay["goal"]["goal_id"])

        status, action_replay = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "feature.goal.start",
                "request": {"feature_id": feature["feature_id"], **request},
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FEATURE_GOAL_START_REPLAYED", action_replay["status"])
        self.assertEqual(
            started["goal_start_receipt"]["receipt_id"],
            action_replay["goal_start_receipt"]["receipt_id"],
        )
        self.assertEqual(
            started["goal_start_receipt"]["expected_path_digest"],
            action_replay["goal_start_receipt"]["expected_path_digest"],
        )
        status, legacy_equivalent = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goal-start-receipts",
            request,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(action_replay["status"], legacy_equivalent["status"])
        self.assertEqual(
            action_replay["goal_start_receipt"],
            legacy_equivalent["goal_start_receipt"],
        )

        status, push_rejected = self.request(
            "POST",
            "/v1/actions",
            {
                "action_id": "feature.goal.start",
                "request": {
                    "feature_id": feature["feature_id"],
                    **request,
                    "push_policy": "PUSH_ALLOWED",
                },
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual("GOAL_START_PUSH_POLICY_INVALID", push_rejected["error_code"])

        status, conflict = self.request(
            "POST",
            f"/v1/feature-nodes/{feature['feature_id']}/goal-start-receipts",
            {**request, "rationale": "A changed decision"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("GOAL_START_RECEIPT_CONFLICT", conflict["error_code"])
        detail = self.server.store.get_feature_node(feature["feature_id"])
        self.assertEqual(started["goal_start_receipt"]["receipt_id"], detail["goal_start_receipt"]["receipt_id"])

    def test_feature_meeting_run_materializes_last_specification_per_model(self) -> None:
        self.server.store.register_project(self.registration())
        created = self.server.multi_rooms.create_meeting_room(
            {
                "title": "Automated feature meeting",
                "topic": "Generate alternatives",
                "project_id": "GCS",
            }
        )
        room_id = created["room"]["room_id"]
        for model in (
            {
                "provider": "CODEX",
                "provider_session_ref": "codex-meeting-session",
                "provider_chat_key": "codex-chat-key",
                "display_name": "Codex",
            },
            {
                "provider": "CLAUDE",
                "provider_session_ref": "claude-meeting-session",
                "provider_chat_key": "claude-chat-key",
                "display_name": "Claude",
            },
            {
                "provider": "GROK",
                "provider_session_ref": "legacy-placeholder-session",
                "display_name": "Unverified placeholder",
            },
        ):
            self.server.multi_rooms.attach_session(
                room_id, {"slot_role": "MODEL", **model}
            )
        status, feature_payload = self.request(
            "POST",
            "/v1/projects/GCS/feature-nodes",
            {
                "idempotency_key": "automated-feature-meeting-v1",
                "title": "Automated path generation",
                "intent_text": "Agents debate and leave reviewable specifications.",
                "meeting_room_id": room_id,
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        feature_id = feature_payload["feature"]["feature_id"]
        seen: list[dict[str, object]] = []

        def invoke(binding, turn):
            seen.append(
                {
                    "binding_id": binding["binding_id"],
                    "turn_number": turn["turn_number"],
                    "delta": turn["delta"]["body_text"],
                    "phase": turn["phase"],
                    "meeting_role": turn["meeting_role"],
                }
            )
            return {
                "status": "COMPLETED",
                "body_text": (
                    f"Specification {binding['display_name']} final turn "
                    f"{turn['turn_number']}"
                ),
                "provider_event_id": f"provider-{turn['turn_number']}",
            }

        self.server.multi_room_meetings.invoke_provider = invoke
        status, result = self.request(
            "POST",
            f"/v1/feature-nodes/{feature_id}/meeting-runs",
            {
                "run_id": "feature-meeting-http-1",
                "max_turns": 4,
                "prompt": "Compare event-first and artifact-first designs.",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FEATURE_MEETING_COMPLETED", result["status"])
        self.assertEqual(4, result["meeting"]["turn_count"])
        self.assertEqual(2, len(result["candidates"]))
        self.assertEqual(2, len(result["feature"]["expected_paths"]))
        self.assertTrue(all(item["expected_path"]["state"] == "CANDIDATE" for item in result["candidates"]))
        self.assertFalse(result["goal_created"])
        self.assertFalse(result["todo_created"])
        self.assertFalse(result["task_frame_created"])
        self.assertFalse(result["authority_created"])
        self.assertFalse(result["execution_assignment_created"])
        self.assertEqual(4, len(seen))
        self.assertIn("Feature: Automated path generation", str(seen[0]["delta"]))
        self.assertIn("Feature: Automated path generation", str(seen[1]["delta"]))
        self.assertNotIn("Specification Codex", str(seen[1]["delta"]))
        self.assertEqual(["PROPOSAL", "PROPOSAL", "REVIEW", "REVIEW"], [item["phase"] for item in seen])
        self.assertEqual(2, len({item["meeting_role"] for item in seen[:2]}))
        self.assertIn("final turn 0", str(seen[2]["delta"]))
        self.assertIn("final turn 1", str(seen[2]["delta"]))
        self.assertEqual("INDEPENDENT_PROPOSAL_REVIEW", result["meeting"]["protocol"])
        self.assertEqual(2, len(result["participant_briefs"]))
        self.assertEqual([], result["candidate_failures"])
        self.assertEqual([], result["duplicate_candidates"])
        artifact_bodies = {
            item["artifact"]["body_text"] for item in result["candidates"]
        }
        self.assertEqual(2, len(artifact_bodies))
        self.assertTrue(any("Specification Codex final turn" in item for item in artifact_bodies))
        self.assertTrue(any("Specification Claude final turn" in item for item in artifact_bodies))
        self.assertEqual(
            {"2", "3"},
            {item.rsplit(" ", 1)[-1] for item in artifact_bodies},
        )
        self.assertTrue(
            all(item["artifact"]["current_revision"] == 1 for item in result["candidates"])
        )
        self.assertEqual([], self.server.store.list_project_goals("GCS"))
        self.assertEqual(
            [],
            [todo for todo in self.server.store.list_todos() if todo["project_id"] == "GCS"],
        )

        status, summary = self.request(
            "GET",
            f"/v1/feature-nodes/{feature_id}/meeting-runs/feature-meeting-http-1",
            None,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("FEATURE_MEETING_SUMMARY_COLLECTED", summary["status"])
        self.assertEqual("COMPLETED", summary["meeting"]["status"])

    def test_feature_meeting_collapses_semantically_identical_paths(self) -> None:
        self.server.store.register_project(self.registration())
        created = self.server.multi_rooms.create_meeting_room(
            {"title": "Duplicate council", "project_id": "GCS"}
        )
        room_id = created["room"]["room_id"]
        for provider in ("CODEX", "CLAUDE"):
            self.server.multi_rooms.attach_session(
                room_id,
                {
                    "slot_role": "MODEL",
                    "provider": provider,
                    "provider_session_ref": f"{provider.lower()}-duplicate-session",
                    "provider_chat_key": f"{provider.lower()}-duplicate-chat",
                    "display_name": provider.title(),
                },
            )
        status, feature_payload = self.request(
            "POST",
            "/v1/projects/GCS/feature-nodes",
            {
                "idempotency_key": "duplicate-feature-meeting-v1",
                "title": "Duplicate candidate detection",
                "intent_text": "Keep only semantically distinct expected paths.",
                "meeting_room_id": room_id,
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        candidate = {
            "title": "Same path",
            "summary": "Same summary",
            "specification": "Same specification",
            "route": {
                "steps": [
                    {
                        "step_id": "step-1",
                        "title": "One step",
                        "summary": "Do the same thing",
                        "phase": "delivery",
                    }
                ],
                "dependencies": [],
                "branches": [],
                "architecture_decisions": ["Same decision"],
                "implementation_phases": [{"title": "delivery", "step_ids": ["step-1"]}],
                "risks": [{"risk": "same", "mitigation": "same"}],
                "acceptance_conditions": ["same result"],
                "estimates": {"effort": "S", "cost": "low", "quota": "bounded"},
                "evidence_refs": [],
            },
        }

        def invoke(_binding, turn):
            return {
                "status": "COMPLETED",
                "body_text": json.dumps(candidate),
                "provider_event_id": f"duplicate-provider-{turn['turn_number']}",
            }

        self.server.multi_room_meetings.invoke_provider = invoke
        status, result = self.request(
            "POST",
            f"/v1/feature-nodes/{feature_payload['feature']['feature_id']}/meeting-runs",
            {"run_id": "duplicate-council-1", "max_turns": 4},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(result["candidates"]))
        self.assertEqual(1, len(result["duplicate_candidates"]))
        self.assertEqual(1, len(result["feature"]["expected_paths"]))
        self.assertEqual(
            result["candidates"][0]["artifact"]["author_binding_id"],
            result["duplicate_candidates"][0]["duplicate_of_binding_id"],
        )

    def test_provider_chat_attach_materializes_supervisor_and_project_binding(self) -> None:
        self.server.store.register_project(self.registration())
        identity = {
            "chat_key": "provider_chat_attach_materialized",
            "provider": "CODEX",
            "provider_session_id": "provider-session-materialized",
            "source_path": str(self.project_root / "rollout-provider-session.jsonl"),
            "source_kind": "CODEX_ROLLOUT_JSONL",
            "source_version": "v1",
            "display_name": "Codex materialized session",
        }
        with patch.object(
            self.server, "resolve_provider_chat_identity", return_value=identity
        ):
            attached = self.server.attach_provider_chat_room(
                identity["chat_key"],
                {"project_id": "GCS", "mode": "MASTER", "make_default": False},
            )

        self.assertEqual("PROVIDER_CHAT_ROOM_ATTACHED", attached["status"])
        session = attached["supervisor_session"]
        self.assertEqual("GCS", session["node"])
        self.assertEqual("MASTER", session["mode"])
        self.assertEqual("CODEX", session["provider"])
        self.assertEqual(
            "provider-session-materialized", session["provider_session_ref"]
        )
        self.assertEqual(
            "provider-session-materialized",
            attached["room_binding"]["provider_session_ref"],
        )
        self.assertEqual("MASTER", attached["room_binding"]["slot_role"])

    def test_provider_chat_resolution_uses_exact_supervisor_anchor_when_catalog_lags(self) -> None:
        self.server.store.register_project(self.registration())
        provider_ref = "provider-session-catalog-lag"
        chat_key = "provider_chat_" + _provider_source_key(
            {
                "provider": "CODEX",
                "provider_session_id": provider_ref,
                "source_path": "",
            }
        ).removeprefix("provider_source_")
        identity = {
            "chat_key": chat_key,
            "provider": "CODEX",
            "provider_session_id": provider_ref,
            "source_path": str(self.project_root / "rollout-catalog-lag.jsonl"),
            "source_kind": "CODEX_ROLLOUT_JSONL",
            "source_version": "v1",
            "display_name": "Codex catalog lag",
        }
        with patch.object(
            self.server, "resolve_provider_chat_identity", return_value=identity
        ):
            attached = self.server.attach_provider_chat_room(
                chat_key, {"project_id": "GCS", "mode": "MASTER", "make_default": False}
            )
        self.assertTrue(attached["supervisor_session"]["session_anchor_ref"])
        lagging_room = {
            "chat_key": chat_key,
            "provider": "CODEX",
            "session_kind": "CHAT",
            "display_name": "Codex catalog lag",
            "binding": {"state": "INDEPENDENT"},
        }
        discovered = {
            "provider": "CODEX",
            "provider_session_id": provider_ref,
            "source_path": identity["source_path"],
            "session_kind": "CHAT",
            "identity_state": "VERIFIED",
            "last_modified_at": "2026-08-28T00:00:00Z",
        }
        with patch.object(
            self.server, "provider_chat_catalog", return_value={"rooms": [lagging_room]}
        ), patch.object(
            self.server.store,
            "discover_provider_session_sources",
            return_value=[discovered],
        ):
            descriptor = self.server.resolve_provider_chat_session(chat_key)

        self.assertEqual(provider_ref, descriptor["provider_session_ref"])
        self.assertEqual("GCS", descriptor["project_id"])
        self.assertEqual(
            attached["supervisor_session"]["session_anchor_ref"],
            descriptor["origin_session_anchor_ref"],
        )

    def test_meeting_provider_session_attach_uses_opaque_verified_chat_key(self) -> None:
        self.server.store.register_project(self.registration())
        room_id = self.server.multi_rooms.create_meeting_room(
            {"title": "Attach providers", "project_id": "GCS"}
        )["room"]["room_id"]
        catalog_room = {
            "chat_key": "provider_chat_attach_1",
            "provider": "CODEX",
            "display_name": "Codex design session",
            "binding": {
                "state": "BOUND",
                "current_project_id": "GCS",
                "universe_session_id": "session-supervisor-1",
            },
        }
        descriptor = {
            "provider": "CODEX",
            "provider_session_ref": "private-provider-ref-1",
            "project_id": "GCS",
            "origin_session_anchor_ref": "anchor-provider-1",
            "alias": "GCS MASTER",
        }
        with patch.object(
            self.server, "resolve_provider_chat_session", return_value=descriptor
        ), patch.object(
            self.server,
            "provider_chat_catalog",
            return_value={"rooms": [catalog_room]},
        ):
            status, attached = self.request(
                "POST",
                f"/v1/rooms/{room_id}/provider-sessions",
                {"chat_key": "provider_chat_attach_1"},
                self.token,
            )
            replay_status, replay = self.request(
                "POST",
                f"/v1/rooms/{room_id}/provider-sessions",
                {"chat_key": "provider_chat_attach_1"},
                self.token,
            )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("MEETING_PROVIDER_SESSION_ATTACHED", attached["status"])
        self.assertEqual("MODEL", attached["binding"]["slot_role"])
        self.assertEqual("provider_chat_attach_1", attached["binding"]["metadata"]["provider_chat_key"])
        self.assertEqual("private-provider-ref-1", attached["binding"]["provider_session_ref"])
        self.assertEqual(HTTPStatus.CREATED, replay_status)
        self.assertEqual("MEETING_PROVIDER_SESSION_ALREADY_ATTACHED", replay["status"])

    def test_fresh_meeting_sessions_are_archived_when_room_closes(self) -> None:
        self.server.store.register_project(self.registration())
        room_id = self.server.multi_rooms.create_meeting_room(
            {"title": "Fresh reviewers", "project_id": "GCS"}
        )["room"]["room_id"]
        created_refs = iter(["codex-fresh", "claude-fresh"])

        def create_session(descriptor):
            return {
                "status": "SESSION_BROKER_SESSION_CREATED",
                "chat_key": descriptor["chat_key"],
                "provider": descriptor["provider"],
                "provider_session_ref": next(created_refs),
            }

        with patch.object(
            self.server.session_broker, "create_session", side_effect=create_session
        ), patch.object(
            self.server.session_broker,
            "archive_session",
            side_effect=lambda chat_key: {"status": "SESSION_BROKER_SESSION_ARCHIVED", "chat_key": chat_key},
        ) as archive:
            created = self.server.create_fresh_meeting_sessions(
                room_id, {"providers": ["CODEX", "CLAUDE"]}
            )
            closed = self.server.close_multi_room(room_id)

        self.assertEqual("MEETING_FRESH_SESSIONS_CREATED", created["status"])
        self.assertEqual(2, len(created["sessions"]))
        self.assertTrue(
            all(item["binding"]["metadata"]["meeting_session"] for item in created["sessions"])
        )
        self.assertEqual(2, archive.call_count)
        self.assertEqual("CLOSED", closed["room"]["state"])
        self.assertEqual([], self.server.multi_rooms.list_bindings(room_id))

    def test_meeting_provider_adapter_waits_for_verified_terminal_result(self) -> None:
        terminal_body = json.dumps(
            {
                "title": "Verified specification",
                "summary": "One bounded route",
                "specification": "Self-contained verified specification",
                "route": {
                    "steps": [
                        {
                            "step_id": "step-1",
                            "title": "Implement",
                            "summary": "Implement the route",
                            "phase": "delivery",
                        }
                    ],
                    "dependencies": [],
                    "branches": [],
                    "architecture_decisions": [],
                    "implementation_phases": [
                        {"title": "delivery", "step_ids": ["step-1"]}
                    ],
                    "risks": [],
                    "acceptance_conditions": ["route works"],
                    "estimates": {"effort": "S", "cost": "low", "quota": "bounded"},
                    "evidence_refs": [],
                },
            }
        )

        def turn(descriptor, body, message_id):
            self.assertEqual("CODEX", descriptor["provider"])
            self.assertIn("Incoming delta", body)
            self.assertEqual(
                "feature-meeting:meeting-adapter-1:0:bind-provider-1",
                message_id,
            )
            return {"body": terminal_body}

        with patch.object(
            self.server,
            "resolve_provider_chat_session",
            return_value={
                "provider": "CODEX",
                "provider_session_ref": "provider-session-1",
            },
        ), patch.object(self.server.session_broker, "turn", side_effect=turn):
            result = self.server._invoke_multi_room_meeting_provider(
                {
                    "binding_id": "bind-provider-1",
                    "provider": "CODEX",
                    "provider_session_ref": "provider-session-1",
                    "metadata": {"provider_chat_key": "provider_chat_verified"},
                },
                {
                    "run_id": "meeting-adapter-1",
                    "turn_number": 0,
                    "delta": {"body_text": "Feature intent"},
                },
            )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(terminal_body, result["body_text"])
        self.assertEqual(
            "feature-meeting:meeting-adapter-1:0:bind-provider-1",
            result["provider_event_id"],
        )

    def test_meeting_provider_adapter_reasks_for_oversized_output(self) -> None:
        calls: list[tuple[str, str]] = []

        compact = json.dumps(
            {
                "title": "compact",
                "summary": "compact route",
                "specification": "compact specification",
                "route": {
                    "steps": [
                        {
                            "step_id": "step-1",
                            "title": "Implement",
                            "summary": "Implement compactly",
                            "phase": "delivery",
                        }
                    ],
                    "dependencies": [],
                    "branches": [],
                    "architecture_decisions": [],
                    "implementation_phases": [
                        {"title": "delivery", "step_ids": ["step-1"]}
                    ],
                    "risks": [],
                    "acceptance_conditions": ["compact route works"],
                    "estimates": {"effort": "S", "cost": "low", "quota": "bounded"},
                    "evidence_refs": [],
                },
            }
        )

        def turn(_descriptor, body, message_id):
            calls.append((body, message_id))
            if len(calls) == 1:
                return {"body": "x" * 20001}
            return {"body": compact}

        with patch.object(
            self.server,
            "resolve_provider_chat_session",
            return_value={
                "provider": "CLAUDE",
                "provider_session_ref": "provider-session-1",
            },
        ), patch.object(self.server.session_broker, "turn", side_effect=turn):
            result = self.server._invoke_multi_room_meeting_provider(
                {
                    "binding_id": "bind-provider-1",
                    "provider": "CLAUDE",
                    "provider_session_ref": "provider-session-1",
                    "metadata": {"provider_chat_key": "provider_chat_verified"},
                },
                {
                    "run_id": "meeting-adapter-large",
                    "turn_number": 0,
                    "delta": {"body_text": "Feature intent"},
                },
            )

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(compact, result["body_text"])
        self.assertEqual(2, len(calls))
        self.assertIn("under 18000 characters", calls[0][0])
        self.assertTrue(calls[1][1].endswith(":repair"))

    def test_meeting_provider_adapter_preserves_invalid_output_as_warning(self) -> None:
        with patch.object(
            self.server,
            "resolve_provider_chat_session",
            return_value={
                "provider": "CLAUDE",
                "provider_session_ref": "provider-session-1",
            },
        ), patch.object(
            self.server.session_broker,
            "turn",
            return_value={"body": '{"title":"truncated"'},
        ) as turn:
            result = self.server._invoke_multi_room_meeting_provider(
                {
                    "binding_id": "bind-provider-1",
                    "provider": "CLAUDE",
                    "provider_session_ref": "provider-session-1",
                    "metadata": {"provider_chat_key": "provider_chat_verified"},
                },
                {
                    "run_id": "meeting-adapter-invalid",
                    "turn_number": 0,
                    "delta": {"body_text": "Feature intent"},
                },
            )

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual(
            "MEETING_PROVIDER_OUTPUT_INVALID:JSON_INVALID",
            result["output_warning"],
        )
        self.assertEqual(2, turn.call_count)

    def test_meeting_room_finding_http_records_and_collects_source_links(self) -> None:
        created = self.server.multi_rooms.create_meeting_room(
            {"title": "HTTP finding room", "topic": "research"}
        )
        room_id = created["room"]["room_id"]
        status, recorded = self.request(
            "POST",
            f"/v1/rooms/{room_id}/findings",
            {
                "finding_type": "CROSS_FEATURE_DEPENDENCY",
                "summary": "The meeting room depends on graph projection",
                "detail_text": "Detailed source material",
                "author_role": "CONDUCTOR",
                "evidence_refs": ["universe://evidence/meeting-graph"],
                "feature_refs": ["feature://meeting-room", "feature://graph"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("ROOM_FINDING_RECORDED", recorded["status"])
        self.assertEqual("UNASSIGNED", recorded["finding"]["authority"])
        self.assertEqual("USER", recorded["finding"]["reporter_role"])
        status, resolved = self.request(
            "POST",
            f"/v1/rooms/{room_id}/findings/{recorded['finding']['finding_id']}/state",
            {"state": "RESOLVED", "author_role": "CONDUCTOR"},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("ROOM_FINDING_STATE_CHANGED", resolved["status"])
        self.assertEqual("RESOLVED", resolved["finding"]["resolution_state"])

        status, collected = self.request(
            "GET", f"/v1/rooms/{room_id}/findings", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("ROOM_FINDINGS_COLLECTED", collected["status"])
        self.assertEqual(1, len(collected["findings"]))
        self.assertEqual(2, len(collected["findings"][0]["feature_refs"]))

    def test_meeting_room_artifact_http_create_revise_and_collect(self) -> None:
        created = self.server.multi_rooms.create_meeting_room(
            {"title": "HTTP artifact room", "topic": "specification"}
        )
        room_id = created["room"]["room_id"]

        status, recorded = self.request(
            "POST",
            f"/v1/rooms/{room_id}/artifacts",
            {
                "artifact_type": "SPECIFICATION",
                "title": "Path A",
                "body_text": "First specification",
                "author_role": "USER",
                "evidence_refs": ["universe://evidence/path-a"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("ROOM_ARTIFACT_RECORDED", recorded["status"])
        self.assertEqual("USER", recorded["artifact"]["author_role"])
        artifact = recorded["artifact"]

        status, revised = self.request(
            "POST",
            f"/v1/rooms/{room_id}/artifacts/{artifact['artifact_id']}/revisions",
            {
                "expected_revision": 1,
                "body_text": "Second specification",
                "state": "CANDIDATE",
                "author_role": "CONDUCTOR",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("ROOM_ARTIFACT_REVISED", revised["status"])
        self.assertEqual(2, revised["artifact"]["current_revision"])
        self.assertEqual("USER", revised["artifact"]["author_role"])

        status, collected = self.request(
            "GET", f"/v1/rooms/{room_id}/artifacts", None, self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("ROOM_ARTIFACTS_COLLECTED", collected["status"])
        self.assertEqual(1, len(collected["artifacts"]))

    def test_multi_room_http_fans_out_one_event_and_records_native_result(
        self,
    ) -> None:
        room = self.server.multi_rooms.ensure_project_room("native-http")
        binding = self.server.multi_rooms.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "thread-native-http",
            },
        )["binding"]
        queued: list[dict[str, object]] = []
        self.server.multi_room_native_controls.register(
            binding["binding_id"],
            provider="CODEX",
            provider_session_ref="thread-native-http",
            send_input=lambda _binding, event: queued.append(dict(event)) or True,
        )

        status, posted = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/messages",
            {"author_role": "USER", "body_text": "incremental native input"},
            self.token,
        )

        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("ROOM_MESSAGE_RECORDED", posted["status"])
        self.assertEqual(1, len(queued))
        room_event_id = posted["message"]["room_event_id"]
        self.assertEqual(room_event_id, queued[0]["room_event_id"])
        self.assertEqual(
            "QUEUED",
            posted["delivery"]["participants"][0]["blocker"]["status"],
        )

        base_observation = {
            "project_id": "native-http",
            "room_id": room["room_id"],
            "room_event_id": room_event_id,
            "binding_id": binding["binding_id"],
            "provider_session_ref": "thread-native-http",
        }
        self.server._observe_native_room_event(
            {"event": "DELIVERY_ACCEPTED", **base_observation}
        )
        self.server._observe_native_room_event(
            {"event": "DELTA", "delta": "native ", **base_observation}
        )
        self.server._observe_native_room_event(
            {"event": "COMPLETED", "body": "native answer", **base_observation}
        )
        self.server._observe_native_room_event(
            {"event": "COMPLETED", "body": "duplicate", **base_observation}
        )

        snapshot = self.server.multi_rooms.room_snapshot(room["room_id"])
        self.assertEqual(
            ["incremental native input", "native answer"],
            [message["body_text"] for message in snapshot["messages"]],
        )
        self.assertEqual(
            1,
            snapshot["participant_cursors"][0]["delivery_sequence"],
        )
        self.assertTrue(
            snapshot["participant_cursors"][0]["provider_observation_cursor"].startswith(
                "native-final-"
            )
        )

    def test_meeting_room_native_control_connects_routes_and_disconnects(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        supervised, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "room-pty-session",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-session-existing",
                "session_anchor_ref": "MASTER-CURRENT-ROOM-PTY",
                "state": "LIVE",
            }
        )
        room = self.server.multi_rooms.create_room(
            room_type="MEETING",
            title="Native review",
            host_role="MODEL",
            project_id="GCS",
        )
        binding = self.server.multi_rooms.attach_session(
            room["room_id"],
            {
                "slot_role": "MODEL",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-session-existing",
                "supervisor_session_id": supervised["session_id"],
                "session_anchor_ref": supervised["session_anchor_ref"],
                "display_name": "Claude reviewer",
            },
        )["binding"]
        terminal = {
            "terminal_id": "term-room-001",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CLAUDE",
            "supervisor_session_id": supervised["session_id"],
            "active_session_anchor_ref": supervised["session_anchor_ref"],
            "state": "LIVE",
            "created_at": "2026-08-24T00:00:00Z",
        }
        terminal_host = Mock()
        terminal_host.find_live.return_value = terminal
        terminal_host.list_sessions.return_value = [terminal]
        terminal_host.get.return_value = terminal
        terminal_host.channel_state.return_value = "PENDING"
        self.server.terminal_host = terminal_host
        self.assertFalse(hasattr(self.server, "room_participant_hosts"))
        self.assertIsNone(self.server.room_participant_permission_resolver)

        status, connected = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/bindings/{binding['binding_id']}/control",
            {"action": "CONNECT"},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("ROOM_PARTICIPANT_CONTROL_CONNECTED", connected["status"])
        self.assertEqual("term-room-001", connected["live_pty"]["terminal_id"])
        self.assertEqual(
            "CONTROLLED",
            self.server.multi_rooms.participant_cursor(binding["binding_id"])[
                "participant_state"
            ],
        )

        status, posted = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/messages",
            {"author_role": "USER", "body_text": "Review this increment"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        terminal_host.write.assert_not_called()
        inbox = self.server.session_bus.inbox(
            terminal_host,
            terminal_id="term-room-001",
            room_id=room["room_id"],
        )
        self.assertEqual(1, len(inbox["messages"]))
        instruction = inbox["messages"][0]
        self.assertEqual("Review this increment", instruction["body_text"])
        self.assertEqual(room["room_id"], instruction["room_id"])
        self.server.session_bus.transition(
            instruction["message_id"],
            state="ACCEPTED",
            terminal_id="term-room-001",
            session_anchor_ref=supervised["session_anchor_ref"],
        )
        self.server.session_bus.transition(
            instruction["message_id"],
            state="STARTED",
            terminal_id="term-room-001",
            session_anchor_ref=supervised["session_anchor_ref"],
        )
        self.server.session_bus.reply(
            instruction["message_id"],
            terminal_id="term-room-001",
            session_anchor_ref=supervised["session_anchor_ref"],
            body_text="Session Bus answer",
        )
        self.assertEqual(
            ["Review this increment", "Session Bus answer"],
            [
                message["body_text"]
                for message in self.server.multi_rooms.list_messages(room["room_id"])
            ],
        )
        self.assertEqual(
            "DEFERRED", posted["delivery"]["participants"][0]["delivered"][0]["status"]
        )

        status, disconnected = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/bindings/{binding['binding_id']}/control",
            {"action": "DISCONNECT"},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "ROOM_PARTICIPANT_CONTROL_DISCONNECTED", disconnected["status"]
        )
        self.assertTrue(disconnected["pty_control_detached"])
        self.assertFalse(disconnected["resident_host_stopped"])
        terminal_host.close.assert_not_called()
        self.assertEqual(
            "DISCONNECTED",
            self.server.multi_rooms.participant_cursor(binding["binding_id"])[
                "participant_state"
            ],
        )

        replacement = {**terminal, "terminal_id": "term-room-002"}
        terminal_host.find_live.return_value = replacement
        terminal_host.list_sessions.return_value = [replacement]
        terminal_host.get.return_value = replacement
        status, reconnected = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/bindings/{binding['binding_id']}/control",
            {"action": "CONNECT"},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("term-room-002", reconnected["live_pty"]["terminal_id"])

        terminal_host.get.return_value = {**replacement, "state": "EXITED"}
        status, exited_delivery = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/messages",
            {"author_role": "USER", "body_text": "Do not route after exit"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual(
            "PARTICIPANT_DELIVERY_BLOCKED",
            exited_delivery["delivery"]["participants"][0]["status"],
        )
        self.assertEqual(0, terminal_host.write.call_count)

    def test_meeting_room_native_control_fails_closed_without_exact_live_pty(self) -> None:
        supervised, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "room-pty-missing",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-room-missing",
                "session_anchor_ref": "MASTER-CURRENT-ROOM-MISSING",
                "state": "LIVE",
            }
        )
        room = self.server.multi_rooms.create_room(
            room_type="MEETING", title="Missing PTY", host_role="MODEL", project_id="GCS"
        )
        binding = self.server.multi_rooms.attach_session(
            room["room_id"],
            {
                "slot_role": "MODEL",
                "provider": "CODEX",
                "provider_session_ref": "codex-room-missing",
                "supervisor_session_id": supervised["session_id"],
                "session_anchor_ref": supervised["session_anchor_ref"],
            },
        )["binding"]
        terminal_host = Mock()
        terminal_host.find_live.return_value = None
        terminal_host.list_sessions.return_value = []
        self.server.terminal_host = terminal_host

        status, result = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/bindings/{binding['binding_id']}/control",
            {"action": "CONNECT"},
            self.token,
        )

        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("ROOM_PARTICIPANT_PTY_UNAVAILABLE", result["error_code"])
        terminal_host.create.assert_not_called()
        self.assertEqual(
            "OBSERVED",
            self.server.multi_rooms.participant_cursor(binding["binding_id"])[
                "participant_state"
            ],
        )

    def test_project_master_native_control_uses_dedicated_host_only(self) -> None:
        room = self.server.multi_rooms.ensure_project_room("dedicated-master")
        binding = self.server.multi_rooms.attach_session(
            room["room_id"],
            {
                "slot_role": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "thread-dedicated-master",
            },
        )["binding"]

        status, result = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/bindings/{binding['binding_id']}/control",
            {"action": "CONNECT"},
            self.token,
        )

        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("PROJECT_MASTER_CONTROL_DEDICATED", result["error_code"])

    def test_agent_permission_round_trip_uses_project_room_stream(self) -> None:
        class PermissionHost:
            def resolve_permission(
                self,
                project_id: str,
                request_id: str,
                option_id: str,
            ) -> bool:
                return (
                    project_id,
                    request_id,
                    option_id,
                ) == ("GCS", "permission_test_001", "allow-once")

            def close(self) -> None:
                return

        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        _, room_result = self.request(
            "POST",
            "/v1/projects/GCS/room/messages",
            {
                "kind": "QUESTION",
                "body": "Run the guarded check.",
                "idempotency_key": "permission-room-001",
            },
            self.token,
        )
        _, registered = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "grok-acp:session-001",
                "binding_evidence_ref": "project-host://GCS/acp/session-001",
            },
            self.token,
        )
        bridge = registered["bridge"]
        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        cursor = self.server.project_room_events.cursor()
        try:
            status, requested = self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/permissions",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": room_result["message"]["message_id"],
                    "permission": {
                        "request_id": "permission_test_001",
                        "provider": "GROK",
                        "session_id": "session-001",
                        "tool_call": {
                            "toolCallId": "tool-001",
                            "title": "Read repository status",
                        },
                        "options": [
                            {
                                "optionId": "allow-once",
                                "name": "Allow once",
                                "kind": "allow_once",
                            },
                            {
                                "optionId": "reject-once",
                                "name": "Reject",
                                "kind": "reject_once",
                            },
                        ],
                    },
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)

        self.assertEqual(201, status)
        self.assertEqual("PENDING", requested["permission"]["state"])
        events = self.server.project_room_events.wait(
            "GCS",
            after_event_id=cursor,
            timeout_seconds=0.1,
        )
        self.assertEqual("AGENT_PERMISSION", events[0]["payload"]["type"])

        self.server.project_master_hosts = PermissionHost()
        status, resolved = self.request(
            "POST",
            ("/v1/projects/GCS/agent-session/permissions/permission_test_001/decision"),
            {"option_id": "allow-once"},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("RESOLVED", resolved["permission"]["state"])
        self.assertEqual(
            "allow-once",
            resolved["permission"]["selected_option_id"],
        )

    def test_orphaned_agent_permission_reject_persists_without_live_session(self) -> None:
        class DeadPermissionHost:
            def resolve_permission(self, project_id, request_id, option_id) -> bool:
                return False

            def close(self) -> None:
                return

        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        _, room_result = self.request(
            "POST",
            "/v1/projects/GCS/room/messages",
            {
                "kind": "QUESTION",
                "body": "Run the guarded check.",
                "idempotency_key": "permission-orphan-001",
            },
            self.token,
        )
        _, registered = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "grok-acp:session-orphan",
                "binding_evidence_ref": "project-host://GCS/acp/session-orphan",
            },
            self.token,
        )
        bridge = registered["bridge"]
        os.environ["UNIVERSE_GCS_MASTER_BRIDGE_TOKEN"] = "bridge-test-token"
        try:
            status, requested = self.request(
                "POST",
                "/v1/projects/GCS/master-bridge/permissions",
                {
                    "bridge_id": bridge["bridge_id"],
                    "in_reply_to": room_result["message"]["message_id"],
                    "permission": {
                        "request_id": "permission_orphan_001",
                        "provider": "GROK",
                        "session_id": "session-orphan",
                        "tool_call": {
                            "toolCallId": "tool-orphan-001",
                            "title": "Stale command",
                        },
                        "options": [
                            {
                                "optionId": "allow-once",
                                "name": "Allow once",
                                "kind": "allow_once",
                            },
                            {
                                "optionId": "reject-once",
                                "name": "Reject",
                                "kind": "reject_once",
                            },
                        ],
                    },
                },
                self.token,
                extra_headers={"X-Universe-Bridge-Token": "bridge-test-token"},
            )
        finally:
            os.environ.pop("UNIVERSE_GCS_MASTER_BRIDGE_TOKEN", None)

        self.assertEqual(201, status)
        self.assertEqual("PENDING", requested["permission"]["state"])
        self.server.project_master_hosts = DeadPermissionHost()

        allow_status, allow_result = self.request(
            "POST",
            "/v1/projects/GCS/agent-session/permissions/permission_orphan_001/decision",
            {"option_id": "allow-once"},
            self.token,
        )
        self.assertEqual(409, allow_status)
        self.assertEqual(
            "AGENT_PERMISSION_SESSION_UNAVAILABLE",
            allow_result["error_code"],
        )

        reject_status, rejected = self.request(
            "POST",
            "/v1/projects/GCS/agent-session/permissions/permission_orphan_001/decision",
            {"option_id": "reject-once"},
            self.token,
        )
        self.assertEqual(200, reject_status)
        self.assertEqual("RESOLVED", rejected["permission"]["state"])
        self.assertEqual("reject-once", rejected["permission"]["selected_option_id"])

    def test_room_participant_permission_round_trip_is_room_scoped(self) -> None:
        class PermissionParticipantHosts:
            def __init__(self) -> None:
                self.decisions: list[tuple[str, str, str]] = []

            def resolve_permission(
                self,
                binding_id: str,
                request_id: str,
                option_id: str,
            ) -> bool:
                self.decisions.append((binding_id, request_id, option_id))
                return True

            def close(self) -> None:
                return

        room = self.server.multi_rooms.create_room(
            room_type="MEETING",
            title="Permission review",
            host_role="MODEL",
            project_id="GCS",
        )
        binding = self.server.multi_rooms.attach_session(
            room["room_id"],
            {
                "slot_role": "MODEL",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-permission-session",
            },
        )["binding"]
        participant_hosts = PermissionParticipantHosts()
        self.server.room_participant_permission_resolver = (
            participant_hosts.resolve_permission
        )
        self.server._observe_room_participant_permission(
            binding,
            {
                "room_id": room["room_id"],
                "room_event_id": "room-event-permission",
            },
            {
                "request_id": "permission_room_server_001",
                "provider": "CLAUDE",
                "session_id": "claude-permission-session",
                "tool_call": {
                    "toolCallId": "tool-room-server-001",
                    "title": "Inspect repository status",
                },
                "options": [
                    {
                        "optionId": "allow-once",
                        "name": "Allow once",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                ],
            },
        )

        status, snapshot = self.request(
            "GET",
            f"/v1/rooms/{room['room_id']}",
            token=self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(snapshot["permissions"]))
        self.assertEqual("ROOM_PARTICIPANT", snapshot["permissions"][0]["scope_kind"])
        self.assertEqual("PENDING", snapshot["permissions"][0]["state"])

        status, resolved = self.request(
            "POST",
            (
                f"/v1/rooms/{room['room_id']}/bindings/"
                f"{binding['binding_id']}/permissions/"
                "permission_room_server_001/decision"
            ),
            {"option_id": "allow-once"},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AGENT_PERMISSION_RESOLVED", resolved["status"])
        self.assertEqual("RESOLVED", resolved["permission"]["state"])
        self.assertEqual(
            [
                (
                    binding["binding_id"],
                    "permission_room_server_001",
                    "allow-once",
                )
            ],
            participant_hosts.decisions,
        )

        status, repeated = self.request(
            "POST",
            (
                f"/v1/rooms/{room['room_id']}/bindings/"
                f"{binding['binding_id']}/permissions/"
                "permission_room_server_001/decision"
            ),
            {"option_id": "allow-once"},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AGENT_PERMISSION_ALREADY_RESOLVED", repeated["status"])
        self.assertEqual(1, len(participant_hosts.decisions))

    def test_room_participant_permission_rejects_cross_binding_decision(self) -> None:
        room = self.server.multi_rooms.create_room(
            room_type="MEETING",
            title="Scoped permission",
            host_role="MODEL",
            project_id="GCS",
        )
        binding = self.server.multi_rooms.attach_session(
            room["room_id"],
            {
                "slot_role": "MODEL",
                "provider": "GROK",
                "provider_session_ref": "grok-permission-session",
            },
        )["binding"]
        self.server._observe_room_participant_permission(
            binding,
            {
                "room_id": room["room_id"],
                "room_event_id": "room-event-scoped",
            },
            {
                "request_id": "permission_room_scoped_001",
                "provider": "GROK",
                "session_id": "grok-permission-session",
                "tool_call": {"toolCallId": "tool-room-scoped-001"},
                "options": [
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    }
                ],
            },
        )

        status, result = self.request(
            "POST",
            (
                f"/v1/rooms/{room['room_id']}/bindings/other-binding/permissions/"
                "permission_room_scoped_001/decision"
            ),
            {"option_id": "reject-once"},
            self.token,
        )

        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("AGENT_PERMISSION_SCOPE_MISMATCH", result["error_code"])
        self.assertEqual(
            "PENDING",
            self.server.room_participant_permissions.get(
                "permission_room_scoped_001"
            )["state"],
        )

    def test_project_room_stream_starts_with_durable_snapshot(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request(
            "POST",
            "/v1/projects/GCS/room/messages",
            {
                "kind": "QUESTION",
                "body": "Stream this room.",
                "idempotency_key": "room-stream-snapshot-001",
            },
            self.token,
        )
        request = Request(
            self.endpoint + "/v1/projects/GCS/room/stream",
            method="GET",
        )
        with urlopen(request, timeout=5) as response:
            lines = []
            while True:
                line = response.readline().decode("utf-8").rstrip("\r\n")
                if not line:
                    break
                lines.append(line)

        data_line = next(line for line in lines if line.startswith("data: "))
        envelope = json.loads(data_line.removeprefix("data: "))
        self.assertEqual("SNAPSHOT", envelope["payload"]["type"])
        self.assertEqual(1, len(envelope["payload"]["messages"]))
        self.assertEqual([], envelope["payload"]["permissions"])
        self.assertEqual([], envelope["payload"]["governance_proposals"])
        self.assertEqual(
            "Stream this room.",
            envelope["payload"]["messages"][0]["body"],
        )

    def test_terminal_history_http_route_preserves_cursor_bounds(self) -> None:
        self.server.terminal_host.history = Mock(
            return_value={
                "schema": "universe.terminal-output-history.v1",
                "status": "TERMINAL_HISTORY_COLLECTED",
                "terminal_id": "term-history-http-001",
                "before_cursor": 20,
                "next_before_cursor": 11,
                "has_more": True,
                "chunks": [],
            }
        )
        status, result = self.request(
            "GET",
            "/v1/terminals/term-history-http-001/history?before_cursor=20&limit=10",
            token=self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("TERMINAL_HISTORY_COLLECTED", result["status"])
        self.server.terminal_host.history.assert_called_once_with(
            "term-history-http-001",
            before_cursor=20,
            limit=10,
        )

    def test_provider_git_action_projects_to_anchor_results(self) -> None:
        anchor_ref = "anchor-provider-git-result-001"
        self.server.resolve_provider_chat_session = Mock(
            return_value={
                "current_anchor_ref": anchor_ref,
                "project_id": "GCS",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
            }
        )

        projected = self.server._observe_provider_session_action(
            "provider_chat_0123456789abcdef01234567",
            {
                "action_id": "provider_action_0123456789abcdef01234567",
                "message_id": "provider-message-git-result-001",
                "operation": "COMMIT",
                "state": "COMPLETED",
                "summary": "abc1234 · Define projection contracts · 6 files",
                "details": {
                    "commit_sha": "a" * 40,
                    "task_frame_ref": "task-frame://provider-git-result",
                },
            },
        )

        results = self.server.session_bus.inbox(
            self.server._session_anchor_terminal_host(),
            session_anchor_ref=anchor_ref,
            projection="RESULTS",
        )["messages"]
        self.assertEqual("EVENT_PROJECTED", projected["status"])
        self.assertEqual(1, len(results))
        self.assertEqual("RESULT", results[0]["kind"])
        self.assertEqual("COMPLETED", results[0]["lifecycle_state"])
        self.assertEqual("provider-message-git-result-001", results[0]["thread_id"])
        self.assertEqual("git://commit/" + "a" * 40, projected["result_ref"])
        self.assertEqual(
            ["git://commit/" + "a" * 40],
            results[0]["event_context"]["artifact_refs"],
        )

    def test_provider_session_action_can_be_deleted_by_one_api(self) -> None:
        action = {
            "schema": "universe.provider-session-action.v1",
            "action_id": "provider_action_0123456789abcdef01234567",
            "kind": "INFORMATIONAL",
            "operation": "COMMIT",
            "state": "COMPLETED",
        }
        chat_key = "provider_chat_0123456789abcdef01234567"
        action["created_at"] = "2026-08-15T00:00:00Z"
        self.server.store.record_provider_session_action(chat_key, action, retain=200)
        status, result = self.request(
            "DELETE",
            f"/v1/provider-sessions/{chat_key}/actions/{action['action_id']}",
            token=self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("PROVIDER_SESSION_ACTION_DELETED", result["status"])
        self.assertEqual(action["action_id"], result["action"]["action_id"])
        self.assertEqual([], self.server.store.list_provider_session_actions(chat_key, limit=200))

    def test_global_governance_inbox_skips_missing_project_root(self) -> None:
        missing_root = self.temp_root / "missing-project"
        missing_root.mkdir()
        (missing_root / "REPOSITORY_MANIFEST.md").write_text(
            "# missing-project\n", encoding="utf-8"
        )
        self.server.store.register_project(
            {
                "project_id": "missing-project",
                "project_root": str(missing_root),
            }
        )
        shutil.rmtree(missing_root)

        proposals = self.server.list_governance_proposal_inbox()
        self.assertNotIn(
            "missing-project", {proposal["project_id"] for proposal in proposals}
        )

    def test_governance_proposal_is_durable_visible_and_approved_by_one_api(
        self,
    ) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        class ApprovingAdapter(ProjectTaskProposalAdapter):
            def approve(
                inner_self,
                project_root: Path,
                *,
                proposal_id: str,
                proposal_digest: str,
                evidence_ref: str,
            ) -> JsonObject:
                database_path = (
                    project_root
                    / ".ai"
                    / "runtime"
                    / "task_frames"
                    / "task-proposals.sqlite3"
                )
                approval = {
                    "schema": "ai-career.task-proposal-approval.v1",
                    "status": "APPROVED",
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal_digest,
                    "evidence_ref": evidence_ref,
                    "approved_at": "2026-08-03T00:01:00Z",
                }
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        """
                        UPDATE proposal
                        SET state = 'APPROVED', approved_at = ?, approval_json = ?
                        WHERE proposal_id = ? AND proposal_digest = ?
                        """,
                        (
                            approval["approved_at"],
                            json.dumps(approval, sort_keys=True, separators=(",", ":")),
                            proposal_id,
                            proposal_digest,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()
                return {
                    "status": "TASK_PROPOSAL_APPROVED",
                    "approval": approval,
                }

        self.server.project_task_proposals = ApprovingAdapter()
        status, listed = self.request(
            "GET",
            "/v1/projects/GCS/governance-proposals",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("PROPOSED", listed["proposals"][0]["state"])
        self.assertFalse(listed["proposals"][0]["platform_permission"])
        status, inbox = self.request(
            "GET",
            "/v1/governance/proposals/pending",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_INBOX_COLLECTED", inbox["status"])
        gcs_proposal = next(
            item
            for item in inbox["proposals"]
            if item["project_id"] == "GCS"
            and item["proposal_id"] == proposal["proposal_id"]
        )
        self.assertEqual("PROPOSED", gcs_proposal["state"])

        status, approved = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": proposal["proposal_digest"],
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_APPROVED", approved["status"])
        self.assertEqual("APPROVED", approved["proposal"]["state"])
        self.assertEqual("APPLIED", approved["decision"]["state"])
        self.assertIn(
            "governance-proposals/task_proposal_test_001/decisions/",
            approved["decision"]["evidence_ref"],
        )
        self.assertEqual("NATURAL_LANGUAGE", approved["decision"]["source"])
        self.assertEqual("UNIVERSE_UI", approved["decision"]["commander_surface"])
        self.assertEqual("LOCAL_BROWSER", approved["decision"]["access_surface"])
        self.assertIn(
            "universe.project-master-governance-approval.v1",
            approved["message"]["body"],
        )
        packet = json.loads(approved["message"]["body"].splitlines()[-1])
        self.assertEqual("UNIVERSE_UI", packet["commander_surface"])
        self.assertEqual("LOCAL_BROWSER", packet["access_surface"])
        self.assertFalse(
            packet["descendant_task_frame_policy"]["commander_reapproval_required"]
        )
        self.assertEqual(
            proposal["proposal_id"],
            packet["descendant_task_frame_policy"]["parent_proposal_id"],
        )

        status, repeated = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": proposal["proposal_digest"],
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_ALREADY_APPROVED", repeated["status"])

        status, rejected_bridge = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": proposal["proposal_digest"],
            },
            self.token,
            extra_headers={"X-Universe-Bridge-Token": "provider-bridge"},
        )
        self.assertEqual(HTTPStatus.FORBIDDEN, status)
        self.assertEqual("DIRECT_COMMANDER_REQUIRED", rejected_bridge["error_code"])

    def test_governance_proposal_can_be_cancelled_by_one_api(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        prior, created = self.server.store.record_governance_proposal_decision(
            "GCS",
            proposal,
            {
                "decision": "CANCEL",
                "proposal_digest": proposal["proposal_digest"],
                "source": "NATURAL_LANGUAGE",
                "commander_surface": "UNIVERSE_UI",
                "access_surface": "LOCAL_BROWSER",
                "idempotency_key": "failed-cancel-attempt",
            },
        )
        self.assertTrue(created)
        failed = self.server.store.fail_governance_proposal_decision(
            prior["decision_id"], "CLI_USAGE_ERROR"
        )
        self.assertEqual("FAILED", failed["state"])

        class CancellationMustStayLocal(ProjectTaskProposalAdapter):
            def cancel(self, *args: object, **kwargs: object) -> JsonObject:
                raise AssertionError("Runtime Task Proposal journal must stay unchanged")

        self.server.project_task_proposals = CancellationMustStayLocal()
        status, cancelled = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {
                "decision": "CANCEL",
                "proposal_digest": proposal["proposal_digest"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_CANCELLED", cancelled["status"])
        self.assertEqual("PROPOSED", cancelled["proposal"]["state"])
        self.assertEqual("CANCEL", cancelled["decision"]["decision"])
        self.assertEqual("APPLIED", cancelled["decision"]["state"])
        self.assertEqual(prior["decision_id"], cancelled["decision"]["decision_id"])
        self.assertEqual(
            "TASK_PROPOSAL_DISMISSED", cancelled["decision"]["result"]["status"]
        )
        self.assertTrue(
            cancelled["decision"]["result"]["reference_proposal_preserved"]
        )
        self.assertIsNone(cancelled["message"])

        status, pending = self.request(
            "GET", "/v1/governance/proposals/pending", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertFalse(
            any(
                item["proposal_id"] == proposal["proposal_id"]
                for item in pending["proposals"]
            )
        )

        status, repeated = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {
                "decision": "CANCEL",
                "proposal_digest": proposal["proposal_digest"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_ALREADY_CANCELLED", repeated["status"])

        status, conflicting = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": proposal["proposal_digest"],
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "GOVERNANCE_PROPOSAL_DECISION_CONFLICT", conflicting["error_code"]
        )

    def test_approved_primary_creates_one_exact_descendant_task_frame(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        class ApprovingAdapter(ProjectTaskProposalAdapter):
            def approve(
                inner_self,
                project_root: Path,
                *,
                proposal_id: str,
                proposal_digest: str,
                evidence_ref: str,
            ) -> JsonObject:
                approval = {
                    "schema": "ai-career.task-proposal-approval.v1",
                    "status": "APPROVED",
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal_digest,
                    "evidence_ref": evidence_ref,
                    "approved_at": "2026-08-10T00:01:00Z",
                }
                database_path = (
                    project_root
                    / ".ai"
                    / "runtime"
                    / "task_frames"
                    / "task-proposals.sqlite3"
                )
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        """
                        UPDATE proposal
                        SET state = 'APPROVED', approved_at = ?, approval_json = ?
                        WHERE proposal_id = ? AND proposal_digest = ?
                        """,
                        (
                            approval["approved_at"],
                            json.dumps(
                                approval, sort_keys=True, separators=(",", ":")
                            ),
                            proposal_id,
                            proposal_digest,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()
                return {"status": "TASK_PROPOSAL_APPROVED", "approval": approval}

        self.server.project_task_proposals = ApprovingAdapter()
        status, approved = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {"decision": "APPROVE", "proposal_digest": proposal["proposal_digest"]},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        evidence_ref = approved["decision"]["evidence_ref"]
        request = {
            "proposal_digest": proposal["proposal_digest"],
            "source_work": {
                "scope_kind": "PROJECT_SOURCE_WORK",
                "write_roots": [
                    str(self.project_root / "tools"),
                    str(self.project_root / "tests"),
                ],
                "write_operations": ["CREATE", "MODIFY"],
                "boundary": proposal["boundary"],
                "task_summary": proposal["task_summary"],
                "instruction_ref": evidence_ref,
            },
            "task_frame": {
                "frame_id": "gcs-bootstrap-frame-001",
                "parent_actor_ref": "project-master/GCS",
                "candidate_source_ref": "git:GCS@" + "2" * 40,
                "source_review_result": {
                    "schema": "ai-career.source-review-result.v1",
                    "status": "SOURCE_REVIEW_PERMITTED",
                    "policy_source": {
                        "ref": "installed-runtime://policy@" + "1" * 40,
                        "commit": "1" * 40,
                        "kind": "INSTALLED_DISTRIBUTION",
                        "evidence_ref": "installed-runtime://manifest/verified",
                        "use": "REVIEWER_POLICY",
                    },
                    "candidate_source": {
                        "ref": "git:GCS@" + "2" * 40,
                        "commit": "2" * 40,
                        "policy_activation": "FORBIDDEN",
                        "classification": "DATA_ONLY",
                    },
                    "review_mode": "STATIC_REVIEW",
                    "repository_write": False,
                    "authority_created": False,
                    "execution_assignment_created": False,
                    "candidate_execution": "FORBIDDEN",
                },
                "mutation_scope": {
                    "operations": ["CREATE", "MODIFY"],
                    "targets": [str(self.project_root / "tools" / "bootstrap.py")],
                },
                "turns": [{"turn_id": "/root/boss", "role": "BOSS"}],
                "instruction_id": "instruction-bootstrap-001",
                "instruction_text": "Create the bounded bootstrap file.",
                "constraints": ["No commit or push."],
                "expected_output": {"kind": "implementation"},
            },
        }
        bridge = {
            "project_id": "GCS",
            "endpoint": "http://127.0.0.1:50123",
            "credential_env": "TEST_MASTER_BRIDGE_TOKEN",
        }
        host_result = {
            "status": "APPROVED_DESCENDANT_TASK_FRAME_READY",
            "project_id": "GCS",
            "primary_proposal_id": proposal["proposal_id"],
            "primary_proposal_digest": proposal["proposal_digest"],
            "approval_evidence_ref": evidence_ref,
            "task_frame_id": "gcs-bootstrap-frame-001",
            "origin_session_anchor_ref": "session-anchor-approved-001",
            "turns": [
                {"turn_id": "/root/boss", "role": "BOSS"},
                {"turn_id": "/root/implement", "role": "IMPLEMENTER"},
                {"turn_id": "/root/review", "role": "QA_REVIEWER"},
            ],
            "repository_write": False,
        }
        client = Mock()
        client.create_approved_descendant_task_frame.return_value = {
            "host_response": host_result
        }
        with (
            patch.object(self.server, "ensure_project_master", return_value={}),
            patch.object(self.server.store, "get_master_bridge", return_value=bridge),
            patch("universe_server.HttpProjectMasterBridge", return_value=client),
        ):
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/governance-proposals/"
                "task_proposal_test_001/task-frame",
                request,
                self.token,
            )

        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("APPROVED_DESCENDANT_TASK_FRAME_CREATED", result["status"])
        self.assertEqual(host_result, result["task_frame"])
        self.assertEqual("BOSS", result["task_frame_room"]["room"]["room_type"])
        self.assertEqual(
            "gcs-bootstrap-frame-001",
            result["task_frame_room"]["room"]["task_frame_id"],
        )
        master_binding = next(
            binding
            for binding in result["task_frame_room"]["bindings"]
            if binding["slot_role"] == "MASTER"
        )
        self.assertEqual(
            "session-anchor-approved-001",
            master_binding["session_anchor_ref"],
        )
        self.assertEqual(
            "session-anchor-approved-001",
            self.server.task_frame_lineage.get_task_frame(
                "gcs-bootstrap-frame-001"
            )["origin_session_anchor_ref"],
        )
        bindings = result["task_frame_room"]["bindings"]
        self.assertEqual(
            {"MASTER", "BOSS", "WORKER", "REVIEWER"},
            {binding["slot_role"] for binding in bindings},
        )
        run_refs = {
            binding["provider_session_ref"]
            for binding in bindings
            if binding.get("provider") == "TASK_FRAME_RUN"
        }
        self.assertEqual(
            {
                "task-frame-run:gcs-bootstrap-frame-001:/root/boss",
                "task-frame-run:gcs-bootstrap-frame-001:/root/implement",
                "task-frame-run:gcs-bootstrap-frame-001:/root/review",
            },
            run_refs,
        )
        graph = self.server.store.semantic_project_graph("GCS")
        graph_run_refs = {
            item["data"].get("task_frame_run_ref")
            for item in graph["nodes"]
            if item["entity_type"] == "ROOM_BINDING"
            and item["data"].get("task_frame_run_ref")
        }
        self.assertEqual(run_refs, graph_run_refs)
        forwarded = client.create_approved_descendant_task_frame.call_args.kwargs
        self.assertEqual(proposal["proposal_id"], forwarded["primary_proposal"]["proposal_id"])
        self.assertEqual("UNIVERSE_UI", forwarded["governance_approval"]["commander_surface"])
        self.assertEqual(evidence_ref, forwarded["governance_approval"]["evidence_ref"])
        self.assertEqual(request["task_frame"], forwarded["task_frame"])
        self.assertEqual(
            request["task_frame"]["source_review_result"],
            forwarded["task_frame"]["source_review_result"],
        )

    def test_task_frame_result_outcome_rejects_failed_workers(self) -> None:
        failed = {
            "result": {
                "child_results": [
                    {
                        "result": {
                            "outcome": "FAILED",
                            "validation": [{"status": "FAIL"}],
                        }
                    }
                ]
            }
        }
        passed = {
            "result": {
                "child_results": [
                    {
                        "result": {
                            "outcome": "SUCCEEDED",
                            "validation": [{"status": "PASS"}],
                        }
                    }
                ]
            }
        }
        self.assertEqual("FAILED", self.server._task_frame_result_outcome(failed))
        self.assertEqual("SUCCEEDED", self.server._task_frame_result_outcome(passed))

    def test_goal_only_result_application_completes_goal_atomically(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        goal = self.server.store.create_goal(
            "GCS",
            {
                "title": "Goal-only automation",
                "description": "Complete without generated Todos.",
                "owner": "CONDUCTOR",
                "state": "ACTIVE",
                "sort_order": 0,
            },
        )
        application, created = self.server.store.apply_goal_only_task_frame_result(
            goal_id=goal["goal_id"],
            expected_goal_revision=goal["revision"],
            task_frame_id="goal-only-frame-001",
            result_ref="task-frame-terminal:goal-only-001",
            result_digest="a" * 64,
        )
        self.assertTrue(created)
        self.assertEqual("SUCCEEDED", application["outcome"])
        completed = self.server.store.get_goal(goal["goal_id"])
        self.assertEqual("DONE", completed["state"])
        self.assertEqual(goal["revision"] + 1, completed["revision"])
        replay, replay_created = self.server.store.apply_goal_only_task_frame_result(
            goal_id=goal["goal_id"],
            expected_goal_revision=completed["revision"],
            task_frame_id="goal-only-frame-001",
            result_ref="task-frame-terminal:goal-only-001",
            result_digest="a" * 64,
        )
        self.assertFalse(replay_created)
        self.assertEqual(application["application_id"], replay["application_id"])

    def test_instruction_task_frame_http_route_uses_proposal_reference_only(
        self,
    ) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        request = {
            "proposal_digest": proposal["proposal_digest"],
            "task_frame": {"frame_id": "instruction-frame-001"},
        }
        bridge = {
            "project_id": "GCS",
            "endpoint": "http://127.0.0.1:50123",
            "credential_env": "TEST_MASTER_BRIDGE_TOKEN",
        }
        host_result = {
            "status": "INSTRUCTION_TASK_FRAME_READY",
            "project_id": "GCS",
            "proposal_reference": {
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["proposal_digest"],
                "request_ref": proposal["request_ref"],
            },
            "task_frame_id": "instruction-frame-001",
            "origin_session_anchor_ref": "session-anchor-instruction-001",
            "repository_write": False,
        }
        client = Mock()
        client.create_instruction_authorized_task_frame.return_value = {
            "host_response": host_result
        }
        with (
            patch.object(self.server, "ensure_project_master", return_value={}),
            patch.object(self.server.store, "get_master_bridge", return_value=bridge),
            patch("universe_server.HttpProjectMasterBridge", return_value=client),
        ):
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/governance-proposals/"
                "task_proposal_test_001/instruction-task-frame",
                request,
                self.token,
            )

        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("INSTRUCTION_TASK_FRAME_CREATED", result["status"])
        self.assertEqual(proposal["request_ref"], result["parent_instruction_ref"])
        self.assertEqual("BOSS", result["task_frame_room"]["room"]["room_type"])
        self.assertEqual(
            "session-anchor-instruction-001",
            result["task_frame_room"]["bindings"][0]["session_anchor_ref"],
        )
        self.assertEqual(
            "session-anchor-instruction-001",
            self.server.task_frame_lineage.get_task_frame(
                "instruction-frame-001"
            )["origin_session_anchor_ref"],
        )
        forwarded = client.create_instruction_authorized_task_frame.call_args.kwargs
        self.assertEqual(
            {
                "proposal_id": proposal["proposal_id"],
                "proposal_digest": proposal["proposal_digest"],
                "request_ref": proposal["request_ref"],
                "source_ref": proposal["source_ref"],
            },
            forwarded["proposal_reference"],
        )
        self.assertEqual(request["task_frame"], forwarded["task_frame"])
        self.assertNotIn("approval", forwarded)

        completion = {
            "status": "INSTRUCTION_TASK_FRAME_COMPLETED",
            "project_id": "GCS",
            "primary_proposal_id": proposal["proposal_id"],
            "task_frame_id": "instruction-frame-001",
            "boss_turn_id": "boss-review",
            "child_results": [
                {
                    "turn_id": "independent-review",
                    "role": "SUB_REVIEWER",
                    "status": "TURN_COMPLETED",
                    "result": {
                        "outcome": "SUCCEEDED",
                        "summary": "Independent review passed with no findings.",
                        "evidence_refs": ["review://receipt-001"],
                        "validation": [
                            {
                                "plane": "independent-review",
                                "state": "PASS",
                                "evidence_refs": ["review://receipt-001"],
                            }
                        ],
                    },
                }
            ],
            "repository_write": False,
        }
        client.run_instruction_authorized_task_frame.return_value = {
            "host_response": completion
        }
        with (
            patch.object(self.server, "ensure_project_master", return_value={}),
            patch.object(self.server.store, "get_master_bridge", return_value=bridge),
            patch("universe_server.HttpProjectMasterBridge", return_value=client),
        ):
            run_status, run_result = self.request(
                "POST",
                "/v1/projects/GCS/governance-proposals/"
                "task_proposal_test_001/instruction-task-frames/"
                "instruction-frame-001/run",
                {},
                self.token,
            )

        self.assertEqual(HTTPStatus.OK, run_status)
        self.assertEqual("INSTRUCTION_TASK_FRAME_COMPLETED", run_result["status"])
        self.assertTrue(run_result["task_frame_result"]["created"])
        self.assertEqual(
            1,
            len(
                self.server.task_frame_lineage.get_task_frame(
                    "instruction-frame-001"
                )["results"]
            ),
        )
        self.assertEqual("CLOSED", run_result["task_frame_room"]["room"]["state"])
        self.assertFalse(run_result["task_frame_room"]["user_may_write"])
        room_messages = self.server.multi_rooms.list_messages(
            run_result["task_frame_room"]["room"]["room_id"]
        )
        terminal_message = next(
            message
            for message in room_messages
            if message["provider_event_id"]
            == "task-frame-result:instruction-frame-001"
        )
        visible_result = json.loads(terminal_message["body_text"])
        self.assertEqual(
            "Independent review passed with no findings.",
            visible_result["child_results"][0]["result"]["summary"],
        )
        self.assertEqual(
            "STRUCTURED_SUMMARY_ONLY", visible_result["redaction_state"]
        )
        run_forwarded = (
            client.run_instruction_authorized_task_frame.call_args.kwargs
        )
        self.assertNotIn("approval_evidence_ref", run_forwarded)

        with (
            patch.object(
                self.server.store,
                "find_master_handoff_task_frame_binding_for_frame",
                return_value={
                    "proposal_id": proposal["proposal_id"],
                    "proposal_digest": proposal["proposal_digest"],
                    "task_frame_id": "instruction-frame-001",
                },
            ),
            patch.object(self.server, "ensure_project_master", return_value={}),
            patch.object(self.server.store, "get_master_bridge", return_value=bridge),
            patch("universe_server.HttpProjectMasterBridge", return_value=client),
        ):
            retry_status, retry_result = self.request(
                "POST",
                "/v1/projects/GCS/governance-proposals/"
                "task_proposal_test_001/instruction-task-frames/"
                "instruction-frame-001/run",
                {},
                self.token,
            )

        self.assertEqual(HTTPStatus.OK, retry_status)
        self.assertFalse(retry_result["task_frame_result"]["created"])
        self.assertEqual(
            "TERMINAL_RESULT_REUSED",
            retry_result["task_frame"]["redaction_state"],
        )
        self.assertEqual(1, client.run_instruction_authorized_task_frame.call_count)

    def test_task_frame_room_message_compacts_oversized_result(self) -> None:
        visible_result = {
            "schema": "universe.task-frame-room-result.v1",
            "task_frame_id": "oversized-frame-001",
            "status": "INSTRUCTION_TASK_FRAME_COMPLETED",
            "boss_turn_id": "boss",
            "child_results": [
                {
                    "turn_id": f"qa-{index}",
                    "role": "QA_REVIEWER",
                    "status": "TURN_COMPLETED",
                    "result": {
                        "outcome": "SUCCEEDED",
                        "summary": "evidence " * 2000,
                        "evidence_refs": ["test://" + ("x" * 600)] * 32,
                        "validation": [
                            {
                                "plane": "focused-tests-" + ("x" * 300),
                                "state": "PASS",
                                "evidence_refs": ["test://" + ("y" * 600)] * 8,
                            }
                        ]
                        * 32,
                    },
                }
                for index in range(8)
            ],
            "redaction_state": "STRUCTURED_SUMMARY_ONLY",
        }

        body_text = self.server._task_frame_room_message_body(
            "oversized-frame-001", visible_result
        )
        compact = json.loads(body_text)

        self.assertLessEqual(len(body_text), 20000)
        self.assertEqual("COMPACT_SUMMARY_ONLY", compact["redaction_state"])
        self.assertEqual(
            "task-frame-lineage://oversized-frame-001",
            compact["full_result_ref"],
        )
        self.assertEqual(6, len(compact["child_results"]))

    def test_closed_boss_room_history_projects_task_frame_timeline(self) -> None:
        self.server.runtime_host.repository_root = self.project_root
        frames_root = self.project_root / ".ai" / "runtime" / "task_frames"
        frames_root.mkdir(parents=True, exist_ok=True)
        frame_id = "history-frame-001"
        frame_db = frames_root / "history.sqlite3"
        connection = sqlite3.connect(frame_db)
        try:
            connection.executescript(
                """
                CREATE TABLE task_frame_context (
                    singleton INTEGER PRIMARY KEY, frame_id TEXT NOT NULL, task_state TEXT NOT NULL
                );
                CREATE TABLE task_journal (
                    event_ordinal INTEGER PRIMARY KEY, event_type TEXT NOT NULL,
                    turn_id TEXT NOT NULL, details_json TEXT NOT NULL, observed_at TEXT NOT NULL
                );
                CREATE TABLE boss_allocations (
                    allocation_ordinal INTEGER PRIMARY KEY, turn_id TEXT NOT NULL,
                    task_text TEXT NOT NULL, recorded_at TEXT NOT NULL
                );
                CREATE TABLE task_turns (
                    turn_ordinal INTEGER PRIMARY KEY, turn_id TEXT NOT NULL, role TEXT NOT NULL,
                    state TEXT NOT NULL, result_json TEXT NOT NULL, review_decision TEXT NOT NULL,
                    claimed_at TEXT NOT NULL, completed_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO task_frame_context VALUES (1, ?, 'COMPLETED')", (frame_id,)
            )
            connection.execute(
                "INSERT INTO task_journal VALUES (1, 'TURN_CLAIMED', 'review', '{}', '2026-08-26T00:00:00Z')"
            )
            connection.execute(
                "INSERT INTO boss_allocations VALUES (1, 'review', 'Review the implementation.', '2026-08-26T00:00:01Z')"
            )
            connection.execute(
                "INSERT INTO task_turns VALUES (1, 'review', 'QA_REVIEWER', 'COMPLETED', ?, '', '', '2026-08-26T00:00:02Z', '2026-08-26T00:00:00Z')",
                (json.dumps({"outcome": "SUCCEEDED", "summary": "Review passed."}),),
            )
            connection.commit()
        finally:
            connection.close()

        room = self.server.multi_rooms.create_boss_room(
            project_id="GCS", task_frame_id=frame_id
        )
        self.server.multi_rooms.close_room(room["room_id"])

        open_status, open_rooms = self.request("GET", "/v1/rooms", None, self.token)
        history_status, all_rooms = self.request(
            "GET", "/v1/rooms?state=ALL", None, self.token
        )
        snapshot_status, snapshot = self.request(
            "GET", f"/v1/rooms/{room['room_id']}", None, self.token
        )

        self.assertEqual(HTTPStatus.OK, open_status)
        self.assertEqual(HTTPStatus.OK, history_status)
        self.assertEqual(HTTPStatus.OK, snapshot_status)
        self.assertNotIn(room["room_id"], {item["room_id"] for item in open_rooms["rooms"]})
        self.assertIn(room["room_id"], {item["room_id"] for item in all_rooms["rooms"]})
        timeline = snapshot["task_frame_timeline"]
        self.assertEqual("TASK_FRAME_TIMELINE_AVAILABLE", timeline["status"])
        self.assertEqual("COMPLETED", timeline["task_state"])
        self.assertEqual(
            {"LIFECYCLE", "ALLOCATION", "RESULT"},
            {entry["entry_kind"] for entry in timeline["entries"]},
        )
        self.assertIn(
            "Review passed.",
            [entry["body_text"] for entry in timeline["entries"]],
        )

    def test_conductor_approval_uses_durable_governance_decision(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        class ApprovingAdapter(ProjectTaskProposalAdapter):
            def approve(
                inner_self,
                project_root: Path,
                *,
                proposal_id: str,
                proposal_digest: str,
                evidence_ref: str,
            ) -> JsonObject:
                approval = {
                    "schema": "ai-career.task-proposal-approval.v1",
                    "status": "APPROVED",
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal_digest,
                    "evidence_ref": evidence_ref,
                    "approved_at": "2026-08-13T00:00:00Z",
                }
                database_path = (
                    project_root
                    / ".ai"
                    / "runtime"
                    / "task_frames"
                    / "task-proposals.sqlite3"
                )
                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        "UPDATE proposal SET state = 'APPROVED', approved_at = ?, "
                        "approval_json = ? WHERE proposal_id = ? AND proposal_digest = ?",
                        (
                            approval["approved_at"],
                            json.dumps(
                                approval, sort_keys=True, separators=(",", ":")
                            ),
                            proposal_id,
                            proposal_digest,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()
                return {"status": "TASK_PROPOSAL_APPROVED", "approval": approval}

        self.server.project_task_proposals = ApprovingAdapter()
        status, response = self.request(
            "POST",
            "/v1/conductor-room/messages",
            {
                "kind": "QUESTION",
                "sender": "USER",
                "body": "승인",
                "provider": "AUTO",
                "ui_context": {"selected_project_id": "GCS"},
                "idempotency_key": "conductor-governance-approval-001",
            },
            self.token,
        )
        self.assertEqual(201, status, response)
        self.assertEqual(
            "GOVERNANCE_PROPOSAL_APPROVED_FROM_COMMANDER_TEXT",
            response["status"],
        )
        self.assertEqual(proposal["proposal_id"], response["proposal"]["proposal_id"])
        self.assertIsNotNone(response["governance_decision"])
        decision = self.server.store.find_governance_proposal_decision(
            "GCS", proposal["proposal_id"]
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual("APPLIED", decision["state"])
        self.assertEqual("APPROVE", decision["decision"])
        self.assertEqual("UNIVERSE_UI", decision["commander_surface"])
        completed = self.server.store.get_conductor_room_message(
            response["message"]["message_id"]
        )
        self.assertEqual("ANSWERED", completed["delivery_state"])
        self.assertEqual("AUTO", completed["provider"])

    def test_governance_approval_command_accepts_korean_and_ascii(self) -> None:
        self.assertTrue(is_governance_approval_command("승인"))
        self.assertTrue(is_governance_approval_command("승인해"))
        self.assertTrue(is_governance_approval_command(" approve "))
        self.assertTrue(
            is_governance_approval_command("approve task_proposal_test_001")
        )
        self.assertFalse(is_governance_approval_command("승인해줘"))

        for progress_phrase in (
            "진행",
            "진행해",
            "고고",
            "고고해",
            "진행해야겠다",
            "진행하자",
        ):
            self.assertFalse(is_governance_approval_command(progress_phrase))

    def test_conductor_approval_reconciles_legacy_runtime_approval(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        approval = {
            "schema": "ai-career.task-proposal-approval.v1",
            "status": "APPROVED",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "evidence_ref": "universe://conductor-room/messages/original-approval",
            "approved_at": "2026-08-13T00:00:00Z",
        }
        database_path = (
            self.project_root
            / ".ai"
            / "runtime"
            / "task_frames"
            / "task-proposals.sqlite3"
        )
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                "UPDATE proposal SET state = 'APPROVED', approved_at = ?, "
                "approval_json = ? WHERE proposal_id = ?",
                (
                    approval["approved_at"],
                    json.dumps(approval, sort_keys=True, separators=(",", ":")),
                    proposal["proposal_id"],
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with patch.object(
            self.server.project_task_proposals,
            "approve",
            side_effect=AssertionError("legacy approval must not run twice"),
        ):
            status, response = self.request(
                "POST",
                "/v1/conductor-room/messages",
                {
                    "kind": "QUESTION",
                    "sender": "USER",
                    "body": "approve task_proposal_test_001",
                    "provider": "AUTO",
                    "ui_context": {"selected_project_id": "GCS"},
                    "idempotency_key": "conductor-governance-reconcile-001",
                },
                self.token,
            )

        self.assertEqual(201, status, response)
        self.assertEqual(
            "GOVERNANCE_PROPOSAL_APPROVED_FROM_COMMANDER_TEXT",
            response["status"],
        )
        decision = self.server.store.find_governance_proposal_decision(
            "GCS", proposal["proposal_id"]
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual("APPLIED", decision["state"])
        self.assertTrue(decision["result"]["reconciled"])

        request = {
            "proposal_digest": proposal["proposal_digest"],
            "source_work": {"scope_kind": "PROJECT_SOURCE_WORK"},
            "task_frame": {
                "frame_id": "gcs-legacy-reconcile-frame-001",
                "parent_actor_ref": "project-master/GCS",
                "mutation_scope": {"operations": ["MODIFY"], "targets": []},
                "turns": [{"turn_id": "/root/boss", "role": "BOSS"}],
                "instruction_id": "instruction-legacy-reconcile-001",
                "instruction_text": "Reconcile the approved lineage.",
                "constraints": ["No commit or push."],
                "expected_output": {"kind": "implementation"},
            },
        }
        bridge = {
            "project_id": "GCS",
            "endpoint": "http://127.0.0.1:50124",
            "credential_env": "TEST_MASTER_BRIDGE_TOKEN",
        }
        host_result = {
            "status": "APPROVED_DESCENDANT_TASK_FRAME_READY",
            "project_id": "GCS",
            "primary_proposal_id": proposal["proposal_id"],
            "primary_proposal_digest": proposal["proposal_digest"],
            "approval_evidence_ref": approval["evidence_ref"],
            "task_frame_id": request["task_frame"]["frame_id"],
            "origin_session_anchor_ref": "session-anchor-legacy-reconcile-001",
            "repository_write": False,
        }
        client = Mock()
        client.create_approved_descendant_task_frame.return_value = {
            "host_response": host_result
        }
        with (
            patch.object(self.server, "ensure_project_master", return_value={}),
            patch.object(self.server.store, "get_master_bridge", return_value=bridge),
            patch("universe_server.HttpProjectMasterBridge", return_value=client),
        ):
            result = self.server.create_approved_descendant_task_frame(
                "GCS", proposal["proposal_id"], request
            )

        self.assertEqual("APPROVED_DESCENDANT_TASK_FRAME_CREATED", result["status"])
        forwarded = client.create_approved_descendant_task_frame.call_args.kwargs
        self.assertEqual(
            approval["evidence_ref"], forwarded["governance_approval"]["evidence_ref"]
        )

        completion = {
            "status": "APPROVED_DESCENDANT_TASK_FRAME_COMPLETED",
            "project_id": "GCS",
            "primary_proposal_id": proposal["proposal_id"],
            "task_frame_id": request["task_frame"]["frame_id"],
            "repository_write": False,
        }
        client.run_approved_descendant_task_frame.return_value = {
            "host_response": completion
        }
        with (
            patch.object(self.server, "ensure_project_master", return_value={}),
            patch.object(self.server.store, "get_master_bridge", return_value=bridge),
            patch("universe_server.HttpProjectMasterBridge", return_value=client),
        ):
            run_result = self.server.run_approved_descendant_task_frame(
                "GCS", proposal["proposal_id"], request["task_frame"]["frame_id"]
            )

        self.assertEqual(
            "APPROVED_DESCENDANT_TASK_FRAME_COMPLETED", run_result["status"]
        )
        run_forwarded = client.run_approved_descendant_task_frame.call_args.kwargs
        self.assertEqual(approval["evidence_ref"], run_forwarded["approval_evidence_ref"])

    def test_descendant_task_frame_requires_an_approved_primary(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, result = self.request(
            "POST",
            "/v1/projects/GCS/governance-proposals/"
            "task_proposal_test_001/task-frame",
            {
                "proposal_digest": proposal["proposal_digest"],
                "source_work": {},
                "task_frame": {},
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("PRIMARY_TASK_PROPOSAL_NOT_APPROVED", result["error_code"])

    def test_commander_text_approves_the_only_pending_task_proposal(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        def approve(
            project_root: Path,
            *,
            proposal_id: str,
            proposal_digest: str,
            evidence_ref: str,
        ) -> JsonObject:
            database_path = (
                project_root
                / ".ai"
                / "runtime"
                / "task_frames"
                / "task-proposals.sqlite3"
            )
            approval = {
                "schema": "ai-career.task-proposal-approval.v1",
                "status": "APPROVED",
                "proposal_id": proposal_id,
                "proposal_digest": proposal_digest,
                "evidence_ref": evidence_ref,
                "approved_at": "2026-08-03T00:02:00Z",
            }
            connection = sqlite3.connect(database_path)
            try:
                connection.execute(
                    """
                    UPDATE proposal
                    SET state = 'APPROVED', approved_at = ?, approval_json = ?
                    WHERE proposal_id = ? AND proposal_digest = ?
                    """,
                    (
                        approval["approved_at"],
                        json.dumps(approval, sort_keys=True, separators=(",", ":")),
                        proposal_id,
                        proposal_digest,
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            return {"status": "TASK_PROPOSAL_APPROVED", "approval": approval}

        with patch.object(
            self.server.project_task_proposals,
            "approve",
            side_effect=approve,
        ):
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/room/messages",
                {
                    "kind": "QUESTION",
                    "sender": "UNIVERSE_CONDUCTOR",
                    "body": "\uc2b9\uc778",
                    "idempotency_key": "commander-text-approve-001",
                },
                self.token,
            )

        self.assertEqual(200, status)
        self.assertEqual(
            "GOVERNANCE_PROPOSAL_APPROVED_FROM_COMMANDER_TEXT",
            result["status"],
        )
        decision = result["governance_decision"]["decision"]
        self.assertEqual("NATURAL_LANGUAGE", decision["source"])
        self.assertEqual("UNIVERSE_UI", decision["commander_surface"])
        self.assertEqual("LOCAL_BROWSER", decision["access_surface"])
        self.assertEqual(proposal["proposal_id"], decision["proposal_id"])
        messages = self.server.store.list_room_messages("GCS")
        self.assertTrue(any(message["body"] == "\uc2b9\uc778" for message in messages))
        self.assertTrue(
            any(
                "descendant_task_frame_policy" in message["body"]
                for message in messages
            )
        )

    def test_governance_proposal_decision_rejects_digest_mismatch(self) -> None:
        self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, response = self.request(
            "POST",
            "/v1/projects/GCS/governance-proposals/task_proposal_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": "c" * 64,
                "source": "BUTTON",
                "idempotency_key": "approve-task-proposal-test-mismatch",
            },
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_DIGEST_MISMATCH", response["error_code"])
        self.assertTrue(self.server.wait_for_request_workers(timeout=1))

    def test_parent_work_approval_records_current_active_work_lineage(self) -> None:
        proposal = self.create_task_proposal_fixture(
            scope={
                "parent_work_unit": {"cardinality": "EXACTLY_ONE"},
                "anchor_and_approval_lineage": {"required_active_work_reference": True},
            }
        )
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.session_supervisor.register_session(
            {
                "session_id": "session-gcs-master-current",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "provider_session_ref": "codex-app-server:current",
                "anchor_ref": "MASTER-CURRENT-GCS-ACTIVE",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        with patch.object(
            self.server.project_task_proposals,
            "approve",
            return_value={"status": "TASK_PROPOSAL_APPROVED"},
        ):
            status, response = self.request(
                "POST",
                "/v1/governance/proposals/task_proposal_test_001/decision",
                {"decision": "APPROVE", "proposal_digest": proposal["proposal_digest"]},
                self.token,
                {"X-Universe-Access-Surface": "CODEX_DESKTOP"},
            )

        self.assertEqual(HTTPStatus.OK, status)
        active_work = response["decision"]["active_work"]
        self.assertEqual("universe.active-work-reference.v1", active_work["schema"])
        self.assertEqual(proposal["proposal_id"], active_work["proposal_id"])
        self.assertEqual(
            "MASTER-CURRENT-GCS-ACTIVE", active_work["anchor"]["anchor_ref"]
        )
        self.assertEqual("CODEX", active_work["anchor"]["provider"])
        self.assertTrue(active_work["work_batch_id"].startswith("work_batch_"))

    def test_parent_work_approval_rejects_proposal_prebound_to_history(self) -> None:
        self.create_task_proposal_fixture(
            scope={
                "parent_work_unit": {"cardinality": "EXACTLY_ONE"},
                "anchor_and_approval_lineage": {
                    "observed_anchor_reference": {
                        "session_id": "historical-grok",
                        "anchor_id": "MASTER-CURRENT-HISTORICAL",
                    }
                },
            }
        )
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.session_supervisor.register_session(
            {
                "session_id": "session-gcs-master-current",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "anchor_ref": "MASTER-CURRENT-GCS-ACTIVE",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

        status, response = self.request(
            "POST",
            "/v1/governance/proposals/task_proposal_test_001/decision",
            {"decision": "APPROVE", "proposal_digest": "a" * 64},
            self.token,
        )

        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("PROPOSAL_ANCHOR_PREBIND_FORBIDDEN", response["error_code"])

    def test_active_work_requires_realignment_after_master_provider_switch(self) -> None:
        self.server.session_supervisor.register_session(
            {
                "session_id": "session-gcs-master-codex",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "anchor_ref": "MASTER-CURRENT-GCS-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        active_work = {
            "anchor": {
                "session_id": "session-gcs-master-codex",
                "anchor_ref": "MASTER-CURRENT-GCS-001",
                "provider": "CODEX",
                "currentness": "CURRENT",
            }
        }
        self.server.session_supervisor.register_session(
            {
                "session_id": "session-gcs-master-claude",
                "node": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "anchor_ref": "MASTER-CURRENT-GCS-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        self.server.session_supervisor.observe_session_activity(
            "session-gcs-master-claude",
            event_type="PROVIDER_ACTIVITY_OBSERVED",
            activity_state="ACTIVE",
            evidence_ref="observation://test/provider-switch",
            observed_at="2026-08-12T23:00:00Z",
        )

        with self.assertRaises(UniverseError) as raised:
            self.server._require_active_work_current("GCS", active_work)
        self.assertEqual("ACTIVE_WORK_REALIGNMENT_REQUIRED", raised.exception.code)

    def test_active_work_anchor_rejects_tied_latest_observations(self) -> None:
        observed_at = "2026-08-12T23:00:00Z"
        for session_id, provider in (
            ("session-gcs-master-codex", "CODEX"),
            ("session-gcs-master-claude", "CLAUDE"),
        ):
            self.server.session_supervisor.register_session(
                {
                    "session_id": session_id,
                    "node": "GCS",
                    "mode": "MASTER",
                    "provider": provider,
                    "anchor_ref": "MASTER-CURRENT-GCS-001",
                    "state": "LIVE",
                    "currentness": "CURRENT",
                    "last_seen_at": observed_at,
                }
            )

        with self.assertRaises(UniverseError) as raised:
            self.server._current_active_work_anchor("GCS")

        self.assertEqual("ACTIVE_WORK_ANCHOR_UNAVAILABLE", raised.exception.code)

    def test_legacy_direct_surface_decision_is_projected_to_universe_ui(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        with self.server.store._connection() as connection:
            connection.execute(
                """
                INSERT INTO governance_proposal_decision(
                    decision_id, project_id, proposal_id, proposal_digest,
                    decision, source, commander_surface, access_surface,
                    idempotency_key, evidence_ref, state, result_json,
                    error_code, created_at, applied_at
                ) VALUES (?, ?, ?, ?, 'APPROVE', 'BUTTON', ?, NULL, ?, ?,
                          'APPLIED', '{}', NULL, ?, ?)
                """,
                (
                    "governance_decision_legacy_surface_001",
                    "GCS",
                    proposal["proposal_id"],
                    proposal["proposal_digest"],
                    "LOCAL_BROWSER",
                    "legacy-direct-surface-001",
                    "universe://legacy/decision/001",
                    "2026-08-10T00:00:00Z",
                    "2026-08-10T00:00:01Z",
                ),
            )

        decision = self.server.store.find_governance_proposal_decision(
            "GCS", proposal["proposal_id"]
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual("UNIVERSE_UI", decision["commander_surface"])
        self.assertEqual("LOCAL_BROWSER", decision["access_surface"])

    def test_governance_approval_does_not_resolve_platform_permission(self) -> None:
        proposal = self.create_task_proposal_fixture()
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, response = self.request(
            "POST",
            "/v1/projects/GCS/governance-proposals/permission_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": proposal["proposal_digest"],
                "source": "BUTTON",
                "idempotency_key": "do-not-cross-permission-boundary",
            },
            self.token,
        )
        self.assertEqual(404, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_NOT_FOUND", response["error_code"])

    def test_release_import_and_project_plan_are_durable_and_read_only(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        database, manifest = self.build_release_fixture()
        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        status, imported = self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "MASTER",
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("RELEASE_IMPORTED", imported["status"])
        release = imported["release"]
        self.assertEqual("PRESENT", release["profile_catalog"]["status"])

        status, repeated = self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "MASTER",
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("RELEASE_ALREADY_IMPORTED", repeated["status"])
        database.write_bytes(b"source artifact changed after import")

        status, listed = self.request(
            "GET",
            "/v1/releases",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [release["release_id"]], [item["release_id"] for item in listed["releases"]]
        )

        status, proposed = self.request(
            "POST",
            "/v1/projects/GCS/release-proposals",
            {"release_id": release["release_id"], "mode": "MASTER"},
            self.token,
        )
        self.assertEqual(201, status)
        proposal = proposed["proposal"]
        self.assertEqual("PROJECT_HOST", proposal["execution_owner"])
        self.assertEqual("NONE", proposal["effects"]["project_write"])
        self.assertEqual("FRESH_INSTALL", proposal["plan"]["operation"])
        self.assertEqual("OS_INSTALL", proposal["plan"]["user_command"])
        self.assertEqual(
            "PROJECT_RELEASE_PROPOSAL_READY",
            proposal["status"],
        )

        status, proposals = self.request(
            "GET",
            "/v1/projects/GCS/release-proposals",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            proposal["proposal_id"],
            proposals["proposals"][0]["proposal_id"],
        )
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_project_connection_installs_from_direct_command_without_proposal(self) -> None:
        project_root = self.temp_root / "direct-project"
        project_root.mkdir()
        (project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# DIRECT Repository Manifest\n",
            encoding="utf-8",
        )
        database, manifest = self.build_release_fixture()
        _, imported = self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "MASTER",
            },
            self.token,
        )
        release_id = imported["release"]["release_id"]

        status, prepared = self.request(
            "POST",
            "/v1/project-connections/prepare",
            {
                "project_id": "DIRECT",
                "project_root": str(project_root),
                "release_id": release_id,
            },
            self.token,
        )

        self.assertEqual(200, status)
        self.assertEqual("INSTALL_RUNTIME_AND_ADD", prepared["action"])
        self.assertNotIn("proposal", prepared)
        receipt = {
            "schema": "universe.project-runtime-lifecycle-receipt.v1",
            "status": "PROJECT_RUNTIME_LIFECYCLE_APPLIED",
            "project_id": "DIRECT",
            "release_id": release_id,
            "plan_digest": prepared["plan_digest"],
            "instruction_ref": "universe://direct-command/project-connections/test",
            "receipt_digest": "a" * 64,
        }
        with patch(
            "universe_server.apply_project_release_plan",
            return_value=receipt,
        ) as apply_release:
            applied_status, applied = self.request(
                "POST",
                "/v1/project-connections/apply",
                {
                    "project_id": "DIRECT",
                    "project_root": str(project_root),
                    "release_id": release_id,
                    "plan_digest": prepared["plan_digest"],
                    "command": "CONNECT_PROJECT",
                },
                self.token,
            )

        self.assertEqual(200, applied_status, applied)
        self.assertEqual("PROJECT_CONNECTED", applied["status"])
        self.assertEqual(receipt, applied["runtime_receipt"])
        call = apply_release.call_args.kwargs
        self.assertEqual(prepared["plan"], call["plan"])
        self.assertNotIn("proposal", call)
        self.assertNotIn("approval", call)

    def test_project_release_apply_is_approved_durable_and_idempotent(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        database, manifest = self.build_release_fixture()
        _, imported = self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "MASTER",
            },
            self.token,
        )
        _, proposed = self.request(
            "POST",
            "/v1/projects/GCS/release-proposals",
            {
                "release_id": imported["release"]["release_id"],
                "mode": "MASTER",
            },
            self.token,
        )
        proposal = proposed["proposal"]
        receipt = {
            "schema": "universe.project-release-apply-receipt.v1",
            "status": "PROJECT_RELEASE_APPLIED",
            "project_id": "GCS",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "release_id": proposal["release_id"],
            "receipt_digest": "a" * 64,
        }
        request = {
            "approval": "APPROVED",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
        }
        with patch(
            "universe_server.apply_project_release_proposal",
            return_value=receipt,
        ) as apply_release:
            status, applied = self.request(
                "POST",
                "/v1/projects/GCS/release-proposals/apply",
                request,
                self.token,
            )
            status_repeated, repeated = self.request(
                "POST",
                "/v1/projects/GCS/release-proposals/apply",
                request,
                self.token,
            )

        self.assertEqual(200, status)
        self.assertEqual("PROJECT_RELEASE_APPLICATION_COMPLETED", applied["status"])
        self.assertEqual(200, status_repeated)
        self.assertEqual(
            "PROJECT_RELEASE_APPLICATION_ALREADY_COMPLETED",
            repeated["status"],
        )
        self.assertEqual(
            applied["receipt"]["application_id"],
            repeated["receipt"]["application_id"],
        )
        selection_status, selection = self.request(
            "GET",
            "/v1/projects/GCS/release-selection",
            token=self.token,
        )
        self.assertEqual(200, selection_status)
        self.assertEqual("PROJECT_RELEASE_SELECTION_COLLECTED", selection["status"])
        self.assertEqual("SELECTED", selection["selection"]["status"])
        self.assertEqual(
            imported["release"]["release_id"], selection["selection"]["release_id"]
        )
        self.assertEqual(
            applied["receipt"]["application_id"],
            selection["selection"]["application_id"],
        )
        apply_release.assert_called_once()
        call = apply_release.call_args.kwargs
        self.assertEqual(self.project_root, call["project_root"])
        self.assertEqual("APPROVED", call["approval"]["status"])

        stale_request = {**request, "proposal_digest": "0" * 64}
        stale_status, stale = self.request(
            "POST",
            "/v1/projects/GCS/release-proposals/apply",
            stale_request,
            self.token,
        )
        self.assertEqual(409, stale_status)
        self.assertEqual("PROJECT_RELEASE_APPROVAL_STALE", stale["error_code"])

    def test_rboot_text_is_an_ordinary_project_room_message(self) -> None:
        ordinary = ({"message_id": "message-001"}, True)
        with patch.object(
            self.server, "send_project_room_message", return_value=ordinary
        ) as send_message:
            result = self.server.handle_project_room_input(
                "GCS",
                {
                    "kind": "QUESTION",
                    "body": "/rboot",
                    "idempotency_key": "room-rboot-ordinary-001",
                },
                commander_context={"authenticated": True},
            )

        self.assertEqual("PROJECT_ROOM_MESSAGE_RECORDED", result["status"])
        send_message.assert_called_once()

    def test_os_update_room_command_routes_to_release_lifecycle_not_master(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        database, manifest = self.build_release_fixture()
        _, imported = self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "MASTER",
            },
            self.token,
        )
        release_id = imported["release"]["release_id"]

        with patch.object(self.server, "ensure_project_master") as ensure_master:
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/room/messages",
                {
                    "kind": "QUESTION",
                    "body": f"OS_UPDATE {release_id} 기준으로 진행해",
                    "idempotency_key": "room-os-update-release-001",
                },
                self.token,
            )

        self.assertEqual(201, status)
        self.assertEqual(
            "PROJECT_RELEASE_PROPOSAL_RECORDED_FROM_COMMANDER_TEXT",
            result["status"],
        )
        self.assertEqual(release_id, result["release_proposal"]["release_id"])
        self.assertEqual(
            "ROUTED_TO_RELEASE_LIFECYCLE", result["message"]["delivery_state"]
        )
        self.assertEqual(
            result["release_proposal"]["proposal_id"],
            result["message"]["delivery"]["proposal_id"],
        )
        ensure_master.assert_not_called()

    def test_os_update_room_command_requires_one_imported_release_id(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        database, manifest = self.build_release_fixture()
        self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "MASTER",
            },
            self.token,
        )

        with patch.object(self.server, "ensure_project_master") as ensure_master:
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/room/messages",
                {
                    "kind": "QUESTION",
                    "body": "OS_UPDATE 진행해",
                    "idempotency_key": "room-os-update-release-selection-001",
                },
                self.token,
            )

        self.assertEqual(200, status)
        self.assertEqual("PROJECT_RELEASE_SELECTION_REQUIRED", result["status"])
        self.assertIsNone(result["release_proposal"])
        self.assertEqual(
            "RELEASE_SELECTION_REQUIRED", result["message"]["delivery_state"]
        )
        self.assertEqual(1, len(result["available_releases"]))
        ensure_master.assert_not_called()

    def test_release_lifecycle_requires_master_and_rejects_tampering(self) -> None:
        database, manifest = self.build_release_fixture()
        status, blocked = self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "UNIVERSE",
            },
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("MASTER_MODE_REQUIRED", blocked["error_code"])

        content = bytearray(database.read_bytes())
        content[-1] ^= 0x01
        database.write_bytes(bytes(content))
        status, rejected = self.request(
            "POST",
            "/v1/releases/import",
            {
                "database_path": str(database),
                "manifest_path": str(manifest),
                "mode": "MASTER",
            },
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("RELEASE_VERIFICATION_FAILED", rejected["error_code"])

    def test_event_append_is_idempotent_and_detach_cascades(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        event = {
            "event_id": "gcs-status-001",
            "event_type": "STATUS_OBSERVED",
            "payload": {"repository_runtime": "VERIFIED"},
        }
        status, result = self.request(
            "POST", "/v1/projects/GCS/events", event, self.token
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_EVENT_APPENDED", result["status"])

        status, result = self.request(
            "POST", "/v1/projects/GCS/events", event, self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_EVENT_ALREADY_RECORDED", result["status"])

        status, result = self.request(
            "GET", "/v1/projects/GCS/events", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            ["gcs-status-001"], [item["event_id"] for item in result["events"]]
        )

        status, result = self.request("DELETE", "/v1/projects/GCS", token=self.token)
        self.assertEqual(200, status)
        self.assertTrue(result["detached"])
        status, _ = self.request("GET", "/v1/projects/GCS", token=self.token)
        self.assertEqual(404, status)

    def test_skill_observation_ingest_is_redacted_idempotent_and_bench_queryable(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        candidate = self.skill_observation_candidate()
        candidate["candidate"]["observations"][0]["execution_context"] = {
            "provider_ref": "OPENAI",
            "worker_role": "SUB_REVIEWER",
            "task_kind": "READ",
            "node_ref": "broker-client",
            "failure_kind": "NONE",
            "quota_state": "AVAILABLE",
        }
        status, result = self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            candidate,
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("SKILL_OBSERVATIONS_INGESTED", result["status"])
        self.assertEqual("REDACTED", result["redaction_state"])
        self.assertEqual(1, len(result["observations"]))
        self.assertNotIn("skill_ref", result["observations"][0]["skill"])

        status, repeated = self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            candidate,
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("SKILL_OBSERVATIONS_ALREADY_INGESTED", repeated["status"])

        status, observations = self.request(
            "GET", "/v1/projects/GCS/skill-observations", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(observations["observations"]))
        self.assertNotIn("skill_ref", observations["observations"][0]["skill"])
        self.assertEqual(
            "SUB_REVIEWER",
            observations["observations"][0]["execution_context"]["worker_role"],
        )
        self.assertEqual(
            "broker-client",
            observations["observations"][0]["execution_context"]["node_ref"],
        )

        status, bench = self.request("GET", "/v1/bench/skills", token=self.token)
        self.assertEqual(200, status)
        self.assertEqual("SKILL_BENCH_COLLECTED", bench["status"])
        self.assertEqual(1, len(bench["bench"]))
        entry = bench["bench"][0]
        self.assertEqual("source-review", entry["skill"]["skill_id"])
        self.assertEqual("OPENAI", entry["provider_ref"])
        self.assertEqual(1, entry["observation_count"])
        self.assertEqual(1, entry["outcomes"]["SUCCEEDED"])
        self.assertEqual(12, entry["metric_totals"]["duration_ms"])
        self.assertEqual("SUB_REVIEWER", entry["worker_role"])
        self.assertEqual(1, entry["quota_states"]["AVAILABLE"])

        status, worker_bench = self.request(
            "GET", "/v1/bench/compare?group_by=worker", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("worker", worker_bench["group_by"])
        worker_entry = worker_bench["comparisons"][0]
        self.assertEqual("SUB_REVIEWER", worker_entry["label"]["worker_role"])
        self.assertEqual(1.0, worker_entry["success_rate"])
        self.assertEqual(1, worker_entry["failure_kinds"]["NONE"])

        status, audit = self.request("GET", "/v1/runtime/audit", token=self.token)
        self.assertEqual(200, status)
        self.assertEqual("RUNTIME_AUDIT_COLLECTED", audit["status"])
        self.assertEqual("worker", audit["worker_bench"]["group_by"])
        self.assertEqual(1, len(audit["worker_bench"]["comparisons"]))
        self.assertEqual(0, audit["platform_approvals"]["pending_count"])

        status, preflight = self.request(
            "GET", "/v1/runtime/preflight", token=self.token
        )
        self.assertEqual(200, status)
        self.assertIn(preflight["status"], {"READY", "CONFIGURATION_REQUIRED"})
        self.assertEqual(3, len(preflight["providers"]))

        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(1, len(reopened.list_skill_observations("GCS")))

        unsafe = self.skill_observation_candidate()
        unsafe["candidate"] = dict(unsafe["candidate"])
        observation = dict(unsafe["candidate"]["observations"][0])
        skill = dict(observation["skill"])
        skill["skill_ref"] = ".ai/skills/private/source-review/SKILL.md"
        observation["skill"] = skill
        unsafe["candidate"]["observations"] = [observation]
        status, rejected = self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            unsafe,
            self.token,
        )
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", rejected["error_code"])

    def test_prepared_skill_observation_publisher_returns_durable_queue_receipt(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        prepared = {
            "status": "PREPARED",
            "command": "SKILL_OBSERVATION",
            **self.skill_observation_candidate(),
        }
        approval = self.skill_observation_publication_approval(prepared)
        status, receipt = publish_skill_observation(
            project_id="GCS",
            prepared=prepared,
            publication_approval=approval,
            endpoint=self.endpoint,
            token=self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("UNIVERSE_SKILL_OBSERVATION_QUEUED", receipt["status"])
        self.assertEqual("UNIVERSE_OBSERVATION_QUEUE", receipt["operation_class"])
        self.assertEqual("UNIVERSE_LOCAL_HTTP", receipt["provider"])
        self.assertEqual("NOT_PERFORMED", receipt["project_archive_write"])
        self.assertEqual("QUEUED", receipt["queue_state"])
        self.assertEqual(approval["evidence_ref"], receipt["approval_evidence_ref"])
        self.assertEqual("PROJECT_MASTER", receipt["approved_by"])
        self.assertIn(
            self.server.store.identity()["universe_id"], receipt["result_ref"]
        )

        status, repeated = publish_skill_observation(
            project_id="GCS",
            prepared=prepared,
            publication_approval=approval,
            endpoint=self.endpoint,
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            "UNIVERSE_SKILL_OBSERVATION_ALREADY_QUEUED", repeated["status"]
        )
        self.assertEqual(receipt["result_ref"], repeated["result_ref"])

        rejected_approval = dict(approval)
        rejected_approval["candidate_digest"] = "f" * 64
        with self.assertRaisesRegex(
            UniverseError, "does not match the prepared candidate"
        ):
            publish_skill_observation(
                project_id="GCS",
                prepared=prepared,
                publication_approval=rejected_approval,
                endpoint=self.endpoint,
                token=self.token,
            )

        status, queued = self.request(
            "GET", "/v1/projects/GCS/skill-observation-queue", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(["QUEUED"], [item["status"] for item in queued["items"]])
        self.assertEqual(
            approval["evidence_ref"],
            queued["items"][0]["publication_approval"]["evidence_ref"],
        )
        reopened_queue = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            approval["evidence_ref"],
            reopened_queue.list_skill_observation_queue("GCS")[0][
                "publication_approval"
            ]["evidence_ref"],
        )

        status, drained = self.request(
            "POST", "/v1/skill-observation-queue/drain", {"limit": 10}, self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("SKILL_OBSERVATION_QUEUE_DRAINED", drained["status"])
        self.assertEqual(1, len(drained["items"]))
        self.assertEqual("INGESTED", drained["items"][0]["status"])

        status, observations = self.request(
            "GET", "/v1/projects/GCS/skill-observations", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(observations["observations"]))
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        archive_candidate = prepare_skill_observation_archive(
            project_id="GCS",
            receipt=receipt,
            selection_ref="user-selection-gcs-archive-001",
            archive_path=".ai/archive/universe/skill-observation-gcs-001.json",
        )
        self.assertEqual(
            "PROJECT_ARCHIVE_RECEIPT_CANDIDATE_READY", archive_candidate["status"]
        )
        self.assertEqual("HANDOFF_APPEND", archive_candidate["operation_class"])
        self.assertEqual("NOT_PERFORMED", archive_candidate["project_archive_write"])
        self.assertEqual("NOT_OBSERVED", archive_candidate["provider_write_evidence"])
        self.assertEqual(
            "PROJECT_OWNED_HANDOFF_APPEND", archive_candidate["next_operation"]
        )
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

        with self.assertRaisesRegex(UniverseError, "normalized file path"):
            prepare_skill_observation_archive(
                project_id="GCS",
                receipt=receipt,
                selection_ref="user-selection-gcs-archive-001",
                archive_path=".ai/archive/../core/forbidden.json",
            )

    def test_fresh_project_intent_returns_seed_routes_without_project_state(
        self,
    ) -> None:
        baseline_projects = self.server.store.list_projects()
        status, result = self.request(
            "POST",
            "/v1/future-paths",
            {
                "project": "Local trading workstation",
                "kind": "desktop-app",
                "technologies": ["python", "pyside6", "sqlite"],
                "goal": "stable unattended operation with recoverable state",
                "limit": 2,
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("FRESH_PROJECT_ROUTE_CANDIDATES", result["status"])
        self.assertEqual(baseline_projects, self.server.store.list_projects())
        proposal = result["proposal"]
        self.assertEqual("FUTURE_PATH_CANDIDATES", proposal["status"])
        self.assertEqual("NOT_AVAILABLE", proposal["probabilities"])
        self.assertEqual("USER_SELECTION_REQUIRED", proposal["next_operation"])
        self.assertEqual("durable-desktop-state", proposal["candidates"][0]["route_id"])
        self.assertEqual("NONE", proposal["effects"]["execution"])

        status, rejected = self.request(
            "POST",
            "/v1/future-paths",
            {
                "project": "Local trading workstation",
                "kind": "desktop-app",
                "technologies": ["python"],
                "goal": "stable state",
                "unexpected": "raw prompt material",
            },
            self.token,
        )
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", rejected["error_code"])

    def test_fresh_project_composition_requires_explicit_adoption(self) -> None:
        baseline_projects = self.server.store.list_projects()
        request = {
            "intent": {
                "project": "Local trading workstation",
                "project_root": str(self.project_root),
                "kind": "desktop-app",
                "technologies": ["python", "pyside6", "sqlite"],
                "goal": "stable unattended operation with recoverable state",
                "constraints": ["No live order mutation during discovery."],
                "target_users": "Individual trading operator",
            },
            "route_id": "durable-desktop-state",
        }
        status, result = self.request(
            "POST", "/v1/fresh-project-compositions", request, self.token
        )
        self.assertEqual(201, status)
        self.assertEqual("FRESH_PROJECT_COMPOSITION_PROPOSAL_READY", result["status"])
        composition = result["composition"]
        self.assertEqual(str(self.project_root), composition["intent"]["project_root"])
        self.assertEqual("USER_SELECTION_REQUIRED", composition["selection_state"])
        self.assertEqual(
            "durable-desktop-state", composition["selected_route"]["route_id"]
        )
        self.assertTrue(composition["specification"]["functional_nodes"])
        self.assertEqual(
            {"SPECIFICATION", "DESIGN", "ARCHITECTURE", "REFERENCE", "CONTRACT"},
            {document["role"] for document in composition["document_plan"]},
        )
        self.assertTrue(
            all(value == "NONE" for value in composition["effects"].values())
        )
        self.assertEqual(baseline_projects, self.server.store.list_projects())

        status, repeated = self.request(
            "POST", "/v1/fresh-project-compositions", request, self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            "FRESH_PROJECT_COMPOSITION_ALREADY_RECORDED", repeated["status"]
        )
        self.assertEqual(
            composition["composition_id"], repeated["composition"]["composition_id"]
        )

        status, adopted = self.request(
            "POST",
            "/v1/fresh-project-composition-adoptions",
            {
                "composition_id": composition["composition_id"],
                "approval": "ADOPTED",
                "user_notes": "Use the proposed document plan as the initial scope.",
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("FRESH_PROJECT_COMPOSITION_ADOPTED", adopted["status"])
        adoption = adopted["adoption"]
        self.assertEqual("PROJECT_MASTER_HANDOFF_CANDIDATE", adoption["next_operation"])
        self.assertTrue(all(value == "NONE" for value in adoption["effects"].values()))
        self.assertEqual(baseline_projects, self.server.store.list_projects())

        status, compositions = self.request(
            "GET", "/v1/fresh-project-compositions", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [composition["composition_id"]],
            [item["composition_id"] for item in compositions["compositions"]],
        )
        status, adoptions = self.request(
            "GET", "/v1/fresh-project-composition-adoptions", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [adoption["adoption_id"]],
            [item["adoption_id"] for item in adoptions["adoptions"]],
        )

        invalid = dict(request)
        invalid["route_id"] = "not-a-route"
        status, rejected = self.request(
            "POST", "/v1/fresh-project-compositions", invalid, self.token
        )
        self.assertEqual(409, status)
        self.assertEqual("FRESH_PROJECT_ROUTE_NOT_SELECTABLE", rejected["error_code"])

    def test_fresh_project_refinement_requires_bound_candidate_and_user_adoption(
        self,
    ) -> None:
        baseline_projects = self.server.store.list_projects()
        status, created = self.request(
            "POST",
            "/v1/fresh-project-compositions",
            {
                "intent": {
                    "project": "Local trading workstation",
                    "kind": "desktop-app",
                    "technologies": ["python", "pyside6", "sqlite"],
                    "goal": "stable unattended operation with recoverable state",
                    "constraints": ["Local-first only."],
                    "target_users": "Individual trading operator",
                },
                "route_id": "durable-desktop-state",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        composition = created["composition"]

        status, prepared = self.request(
            "POST",
            "/v1/fresh-project-refinement-requests",
            {"composition_id": composition["composition_id"]},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        refinement_request = prepared["request"]
        self.assertEqual("FRESH_PROJECT_REFINEMENT_REQUEST_READY", prepared["status"])
        self.assertEqual(
            "UNIVERSE_PLANNING_FRAME_REQUIRED",
            refinement_request["runtime_boundary"]["task_frame"],
        )
        self.assertEqual(
            "NOT_REQUESTED",
            refinement_request["runtime_boundary"]["provider_invocation"],
        )
        self.assertEqual(
            "FORBIDDEN", refinement_request["output_contract"]["raw_worker_text"]
        )
        self.assertTrue(
            all(value == "NONE" for value in refinement_request["effects"].values())
        )

        candidate = {
            "schema": "universe.fresh-project-refinement-candidate.v1",
            "request_id": refinement_request["request_id"],
            "request_digest": refinement_request["request_digest"],
            "composition_id": composition["composition_id"],
            "composition_digest": composition["composition_digest"],
            "producer": {
                "provider": "GROK",
                "model_ref": "provider://GROK/model/grok-build",
                "worker_id": "grok-cli:planning-001",
                "result_receipt_ref": "grok-cli:planning-001:result-001",
            },
            "refinement": {
                "problem_statement": "Provide a recoverable local trading workspace.",
                "target_users": "Individual trading operator",
                "constraints": [
                    "Local-first only.",
                    "No order mutation during discovery.",
                ],
                "design_direction": "Keep recovery state visible beside live observations.",
                "technology_recommendations": [
                    {
                        "technology": "structured-local-events",
                        "rationale": "Preserve recovery evidence without a remote dependency.",
                    }
                ],
                "document_additions": [
                    {
                        "document_id": "project-observability",
                        "role": "EVIDENCE",
                        "title": "Operational observation boundaries",
                    }
                ],
                "risk_additions": ["Recovery state can diverge from the displayed UI."],
            },
        }
        invalid = dict(candidate)
        invalid["request_digest"] = "different-request"
        status, rejected = self.request(
            "POST", "/v1/fresh-project-refinement-candidates", invalid, self.token
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "FRESH_PROJECT_REFINEMENT_REQUEST_MISMATCH", rejected["error_code"]
        )

        status, recorded = self.request(
            "POST", "/v1/fresh-project-refinement-candidates", candidate, self.token
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        refinement_candidate = recorded["candidate"]
        self.assertNotIn("raw_text", json.dumps(refinement_candidate, sort_keys=True))
        self.assertEqual("FRESH_PROJECT_REFINEMENT_CANDIDATE_READY", recorded["status"])

        status, adoption_result = self.request(
            "POST",
            "/v1/fresh-project-refinement-adoptions",
            {
                "candidate_id": refinement_candidate["candidate_id"],
                "approval": "ADOPTED",
                "user_notes": "Use this as the next composition revision.",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("FRESH_PROJECT_REFINEMENT_ADOPTED", adoption_result["status"])
        refined = adoption_result["composition"]
        self.assertNotEqual(composition["composition_id"], refined["composition_id"])
        self.assertEqual(
            candidate["refinement"]["problem_statement"],
            refined["specification"]["problem_statement"],
        )
        self.assertEqual(
            candidate["refinement"]["technology_recommendations"],
            refined["technology"]["recommendations"],
        )
        self.assertIn(
            "project-observability",
            {item["document_id"] for item in refined["document_plan"]},
        )
        self.assertTrue(all(value == "NONE" for value in refined["effects"].values()))
        self.assertEqual(baseline_projects, self.server.store.list_projects())

        status, requests = self.request(
            "GET", "/v1/fresh-project-refinement-requests", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            [refinement_request["request_id"]],
            [item["request_id"] for item in requests["requests"]],
        )
        status, candidates = self.request(
            "GET", "/v1/fresh-project-refinement-candidates", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            [refinement_candidate["candidate_id"]],
            [item["candidate_id"] for item in candidates["candidates"]],
        )
        status, adoptions = self.request(
            "GET", "/v1/fresh-project-refinement-adoptions", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            [adoption_result["adoption"]["adoption_id"]],
            [item["adoption_id"] for item in adoptions["adoptions"]],
        )

    def test_planning_frame_run_is_bound_proposed_approved_and_redacted(self) -> None:
        class FakePlanningRuntimeHost:
            def __init__(self) -> None:
                self.invocations = 0

            def provider_capabilities(self) -> list[dict[str, str]]:
                return [{"provider": "GROK", "status": "AVAILABLE"}]

            def build_planning_proposal(
                self,
                *,
                runtime_binding: dict[str, object],
                refinement_request: dict[str, object],
                provider: str,
                run_id: str,
            ) -> dict[str, object]:
                self.binding = runtime_binding
                self.request = refinement_request
                proposal_id = "task_frame_proposal_" + run_id[-12:]
                return {
                    "provider": provider,
                    "model_ref": "provider://GROK/model/grok-build",
                    "frame_id": "fresh-project-planning:" + run_id,
                    "turn_id": "planning-boss",
                    "execution_proposal": {
                        "schema": "ai-career.task-frame-execution-proposal.v2",
                        "status": "TASK_FRAME_EXECUTION_PROPOSED",
                        "proposal_id": proposal_id,
                        "plan_digest": "a" * 64,
                        "approval_required": True,
                        "execution_plan": {
                            "repository_write_scope": "NONE",
                            "mutation_scope": {"operations": [], "targets": []},
                        },
                        "authority_created": False,
                        "task_frame_started": False,
                    },
                }

            def invoke_structured_planning(
                self,
                *,
                runtime_binding: dict[str, object],
                run: dict[str, object],
                refinement_request: dict[str, object],
                approval: dict[str, object],
            ) -> dict[str, object]:
                self.invocations += 1
                self.execution = {
                    "binding": runtime_binding,
                    "run": run,
                    "request": refinement_request,
                    "approval": approval,
                }
                return {
                    "status": "TURN_COMPLETED",
                    "provider": "GROK",
                    "worker_id": "grok-cli:planning-001",
                    "result_receipt_ref": "grok-cli:planning-001:result-001",
                    "model_ref": "provider://GROK/model/grok-build",
                    "repository_write": False,
                    "structured_result": {
                        "schema": "universe.fresh-project-refinement-worker-output.v1",
                        "refinement": {
                            "problem_statement": "Keep local recovery state explicit.",
                            "target_users": "Individual trading operator",
                            "constraints": [
                                "Local-first only.",
                                "No raw Worker text persistence.",
                            ],
                            "design_direction": (
                                "Place recoverable state beside live observations."
                            ),
                            "technology_recommendations": [
                                {
                                    "technology": "structured-local-events",
                                    "rationale": "Keep evidence queryable.",
                                }
                            ],
                            "document_additions": [
                                {
                                    "document_id": "project-observability",
                                    "role": "EVIDENCE",
                                    "title": "Operational observation boundaries",
                                }
                            ],
                            "risk_additions": [
                                "Displayed state can diverge from durable state."
                            ],
                        },
                    },
                }

        fake = FakePlanningRuntimeHost()
        self.server.runtime_host = fake
        _, composition_result = self.request(
            "POST",
            "/v1/fresh-project-compositions",
            {
                "intent": {
                    "project": "Local trading workstation",
                    "kind": "desktop-app",
                    "technologies": ["python", "pyside6", "sqlite"],
                    "goal": "stable unattended operation with recoverable state",
                    "constraints": ["Local-first only."],
                    "target_users": "Individual trading operator",
                },
                "route_id": "durable-desktop-state",
            },
            self.token,
        )
        composition = composition_result["composition"]
        _, request_result = self.request(
            "POST",
            "/v1/fresh-project-refinement-requests",
            {"composition_id": composition["composition_id"]},
            self.token,
        )
        refinement_request = request_result["request"]
        self.assertEqual(
            "universe.fresh-project-refinement-worker-output.v1",
            refinement_request["output_contract"]["schema"],
        )
        json_schema = refinement_request["output_contract"]["json_schema"]
        self.assertEqual("object", json_schema["type"])
        self.assertFalse(json_schema["additionalProperties"])
        self.assertEqual(
            [
                "universe.fresh-project-refinement-worker-output.v1",
            ],
            json_schema["properties"]["schema"]["enum"],
        )
        self.assertEqual(
            32,
            json_schema["properties"]["refinement"]["properties"][
                "technology_recommendations"
            ]["maxItems"],
        )

        status, blocked = self.request(
            "POST",
            "/v1/fresh-project-refinement-runs",
            {"request_id": refinement_request["request_id"], "provider": "GROK"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual("PLANNING_RUNTIME_BINDING_REQUIRED", blocked["error_code"])

        runtime_token = "runtime-token-never-persist-41aab"
        runtime_endpoint = "http://127.0.0.1:41991"
        release_source_ref = "universe-release-db://core-test@" + "a" * 64
        status, bound = self.request(
            "POST",
            "/v1/runtime/planning-binding",
            {
                "schema": "universe.planning-runtime-binding.v1",
                "endpoint": runtime_endpoint,
                "token": runtime_token,
                "session_id": "universe-planning-session",
                "origin_anchor_ref": "universe-anchor",
                "origin_frame_id": "current",
                "parent_actor_ref": "universe-conductor",
                "parent_evidence_ref": "host://parent/current",
                "binding_evidence_ref": "host://runtime/binding",
                "runtime_currentness_observation": "CURRENT",
                "source_ref": release_source_ref,
                "source_commit": "b" * 40,
                "source_repository": "fixture/universe-private",
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("BOUND", bound["status"])
        self.assertNotIn("token", bound)
        self.assertNotIn("endpoint", bound)
        self.assertEqual(release_source_ref, bound["source_ref"])
        self.assertEqual("b" * 40, bound["source_commit"])
        self.assertEqual("fixture/universe-private", bound["source_repository"])
        status, observed_binding = self.request(
            "GET", "/v1/runtime/planning-binding", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(bound["binding_digest"], observed_binding["binding_digest"])

        status, proposed = self.request(
            "POST",
            "/v1/fresh-project-refinement-runs",
            {"request_id": refinement_request["request_id"], "provider": "GROK"},
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        run = proposed["run"]
        self.assertEqual("PROPOSED", run["state"])
        self.assertEqual("NONE", run["repository_write_scope"])
        self.assertEqual([], run["mutation_scope"]["targets"])
        self.assertTrue(run["approval_required"])

        status, rejected = self.request(
            "POST",
            f"/v1/fresh-project-refinement-runs/{run['run_id']}/execute",
            {
                "approval": "APPROVED",
                "proposal_id": run["proposal_id"],
                "plan_digest": "b" * 64,
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "FRESH_PROJECT_REFINEMENT_RUN_APPROVAL_MISMATCH",
            rejected["error_code"],
        )

        execution_approval = {
            "approval": "APPROVED",
            "proposal_id": run["proposal_id"],
            "plan_digest": run["plan_digest"],
        }
        status, completed = self.request(
            "POST",
            f"/v1/fresh-project-refinement-runs/{run['run_id']}/execute",
            execution_approval,
            self.token,
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        self.assertEqual("COMPLETED", completed["run"]["state"])
        candidate = completed["candidate"]
        self.assertEqual(
            "FRESH_PROJECT_REFINEMENT_CANDIDATE_READY",
            candidate["status"],
        )
        self.assertEqual(1, fake.invocations)

        status, repeated = self.request(
            "POST",
            f"/v1/fresh-project-refinement-runs/{run['run_id']}/execute",
            execution_approval,
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "FRESH_PROJECT_REFINEMENT_RUN_ALREADY_COMPLETED",
            repeated["status"],
        )
        self.assertEqual(
            candidate["candidate_id"], repeated["candidate"]["candidate_id"]
        )
        self.assertEqual(1, fake.invocations)

        database_bytes = self.server.store.database_path.read_bytes()
        self.assertNotIn(runtime_token.encode("utf-8"), database_bytes)
        self.assertNotIn(runtime_endpoint.encode("utf-8"), database_bytes)
        self.assertNotIn(b"raw worker text marker", database_bytes)
        status, runs = self.request(
            "GET", "/v1/fresh-project-refinement-runs", token=self.token
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual([run["run_id"]], [item["run_id"] for item in runs["runs"]])

    def test_master_handoff_is_proposed_then_explicitly_delivered(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, composition_result = self.request(
            "POST",
            "/v1/fresh-project-compositions",
            {
                "intent": {
                    "project": "Local trading workstation",
                    "kind": "desktop-app",
                    "technologies": ["python", "pyside6", "sqlite"],
                    "goal": "stable unattended operation with recoverable state",
                },
                "route_id": "durable-desktop-state",
            },
            self.token,
        )
        self.assertEqual(201, status)
        composition = composition_result["composition"]
        status, adoption_result = self.request(
            "POST",
            "/v1/fresh-project-composition-adoptions",
            {"composition_id": composition["composition_id"], "approval": "ADOPTED"},
            self.token,
        )
        self.assertEqual(201, status)
        adoption = adoption_result["adoption"]

        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        status, proposed = self.request(
            "POST",
            "/v1/projects/GCS/master-handoffs",
            {
                "source": {
                    "kind": "FRESH_PROJECT_COMPOSITION",
                    "adoption_id": adoption["adoption_id"],
                },
                "purpose": "Have the Project Master review the initial scope.",
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_MASTER_HANDOFF_PROPOSAL_RECORDED", proposed["status"])
        handoff = proposed["handoff"]
        self.assertEqual("PROPOSAL_ONLY", handoff["delivery_state"])
        self.assertEqual(
            "USER_APPROVAL_REQUIRED_FOR_MASTER_DELIVERY", handoff["next_operation"]
        )
        self.assertTrue(all(value == "NONE" for value in handoff["effects"].values()))
        self.assertEqual([], self.server.store.list_room_messages("GCS"))

        status, rejected = self.request(
            "POST",
            f"/v1/projects/GCS/master-handoffs/{handoff['handoff_id']}/deliver",
            {"approval": "ADOPTED"},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "MASTER_HANDOFF_DELIVERY_APPROVAL_REQUIRED", rejected["error_code"]
        )

        status, bridge_result = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "opaque-project-master-session",
                "binding_evidence_ref": "project-host://GCS/master-session/registered",
            },
            self.token,
        )
        self.assertEqual(201, status)
        bridge = bridge_result["bridge"]
        receipt = {
            "status": "DELIVERED",
            "bridge_id": bridge["bridge_id"],
            "project_id": "GCS",
            "message_id": "bridge-created-placeholder",
            "delivered_at": "2026-07-28T04:45:00Z",
        }
        with patch(
            "universe_server.HttpProjectMasterBridge.deliver", return_value=receipt
        ) as deliver:
            status, delivered = self.request(
                "POST",
                f"/v1/projects/GCS/master-handoffs/{handoff['handoff_id']}/deliver",
                {"approval": "DELIVER"},
                self.token,
            )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_MASTER_HANDOFF_DELIVERED", delivered["status"])
        self.assertEqual("QUEUED_FOR_MASTER", delivered["handoff"]["delivery_state"])
        self.assertIsNotNone(delivered["handoff"]["room_message_id"])
        self.assertEqual(
            bridge["bridge_id"], deliver.call_args.kwargs["bridge"]["bridge_id"]
        )

        status, repeated = self.request(
            "POST",
            f"/v1/projects/GCS/master-handoffs/{handoff['handoff_id']}/deliver",
            {"approval": "DELIVER"},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_MASTER_HANDOFF_ALREADY_DELIVERED", repeated["status"])

        status, listed = self.request(
            "GET", "/v1/projects/GCS/master-handoffs", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [handoff["handoff_id"]], [item["handoff_id"] for item in listed["handoffs"]]
        )
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_goal_work_plan_master_handoff_requires_application_reference(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, missing = self.request(
            "POST",
            "/v1/projects/GCS/master-handoffs",
            {"source": {"kind": "GOAL_WORK_PLAN", "adoption_id": "wrong-ref"}},
            self.token,
        )
        self.assertEqual(HTTPStatus.BAD_REQUEST, status)
        self.assertEqual("MASTER_HANDOFF_SOURCE_REFERENCE_INVALID", missing["error_code"])

        status, unknown = self.request(
            "POST",
            "/v1/projects/GCS/master-handoffs",
            {
                "source": {
                    "kind": "GOAL_WORK_PLAN",
                    "application_id": "work_plan_application_missing",
                }
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.NOT_FOUND, status)
        self.assertEqual("MASTER_HANDOFF_SOURCE_NOT_FOUND", unknown["error_code"])

    def test_goal_automation_surface_treats_failed_handoff_as_retryable(self) -> None:
        goal = {"goal_id": "goal_retry_001", "project_id": "GCS", "revision": 1}
        application = {
            "application_id": "work_plan_application_retry_001",
            "created_items": {"todo_ids": ["todo_retry_001"]},
        }
        failed_handoff = {
            "handoff_id": "handoff_retry_0123456789abcdef",
            "delivery_state": "DELIVERY_FAILED",
            "source": {"goal": {"revision": 1}},
        }
        with (
            patch.object(
                self.server.store,
                "goal_work_plan_surface",
                return_value={"goal": goal, "application": application},
            ),
            patch.object(
                self.server.store,
                "find_goal_start_receipt_for_goal",
                return_value=None,
            ),
            patch.object(
                self.server.store, "get_goal_automation_scheduler", return_value=None
            ),
            patch.object(
                self.server.store,
                "find_goal_work_plan_handoff",
                return_value=failed_handoff,
            ),
        ):
            surface = self.server.goal_automation_surface(goal["goal_id"])
        self.assertEqual("MASTER_HANDOFF_READY", surface["automation_state"])
        self.assertEqual("DELIVER_MASTER_HANDOFF", surface["next_operation"])
        self.assertEqual(["todo_retry_001"], surface["todo_execution"]["eligible_todo_ids"])

    def test_goal_automation_surface_retries_failed_master_room_turn(self) -> None:
        goal = {"goal_id": "goal_room_retry_001", "project_id": "GCS", "revision": 1}
        application = {
            "application_id": "work_plan_application_room_retry_001",
            "created_items": {"todo_ids": ["todo_room_retry_001"]},
        }
        handoff = {
            "handoff_id": "handoff_room_retry_0123456789",
            "delivery_state": "QUEUED_FOR_MASTER",
            "room_message_id": "room_failed_master_turn_001",
            "source": {"goal": {"revision": 1}},
        }
        with (
            patch.object(
                self.server.store,
                "goal_work_plan_surface",
                return_value={"goal": goal, "application": application},
            ),
            patch.object(
                self.server.store, "find_goal_start_receipt_for_goal", return_value=None
            ),
            patch.object(
                self.server.store, "get_goal_automation_scheduler", return_value=None
            ),
            patch.object(
                self.server.store, "find_goal_work_plan_handoff", return_value=handoff
            ),
            patch.object(
                self.server.store,
                "list_room_messages",
                return_value=[
                    {
                        "message_id": "room_failed_master_turn_001",
                        "delivery_state": "FAILED",
                    }
                ],
            ),
        ):
            surface = self.server.goal_automation_surface(goal["goal_id"])
        self.assertEqual("MASTER_HANDOFF_READY", surface["automation_state"])
        self.assertEqual("DELIVER_MASTER_HANDOFF", surface["next_operation"])

    def test_goal_automation_surface_delivers_latest_goal_revision_update(self) -> None:
        goal = {"goal_id": "goal_stale_001", "project_id": "GCS", "revision": 2}
        application = {
            "application_id": "work_plan_application_stale_001",
            "created_items": {"todo_ids": ["todo_stale_001"]},
        }
        stale_handoff = {
            "handoff_id": "handoff_stale_0123456789abcdef",
            "delivery_state": "QUEUED_FOR_MASTER",
            "source": {"goal": {"revision": 1}},
        }
        with (
            patch.object(
                self.server.store,
                "goal_work_plan_surface",
                return_value={"goal": goal, "application": application},
            ),
            patch.object(
                self.server.store,
                "find_goal_start_receipt_for_goal",
                return_value=None,
            ),
            patch.object(
                self.server.store, "get_goal_automation_scheduler", return_value=None
            ),
            patch.object(
                self.server.store,
                "find_goal_work_plan_handoff",
                return_value=stale_handoff,
            ),
        ):
            surface = self.server.goal_automation_surface(goal["goal_id"])
        self.assertEqual("MASTER_HANDOFF_UPDATE_READY", surface["automation_state"])
        self.assertEqual(
            "DELIVER_MASTER_HANDOFF_UPDATE", surface["next_operation"]
        )
        self.assertEqual(
            [], surface["todo_execution"]["eligible_todo_ids"]
        )

    def test_goal_automation_surface_redelivers_legacy_unbounded_projection(self) -> None:
        goal = {"goal_id": "goal_projection_001", "project_id": "GCS", "revision": 2}
        application = {
            "application_id": "work_plan_application_projection_001",
            "created_items": {"todo_ids": ["todo_projection_001"]},
        }
        handoff = {
            "handoff_id": "handoff_projection_0123456789",
            "delivery_state": "QUEUED_FOR_MASTER",
            "source": {"goal": {"revision": 2}},
        }
        with (
            patch.object(
                self.server.store,
                "goal_work_plan_surface",
                return_value={"goal": goal, "application": application},
            ),
            patch.object(
                self.server.store, "find_goal_start_receipt_for_goal", return_value=None
            ),
            patch.object(
                self.server.store, "get_goal_automation_scheduler", return_value=None
            ),
            patch.object(
                self.server.store, "find_goal_work_plan_handoff", return_value=handoff
            ),
            patch.object(
                self.server.store,
                "_master_handoff_delivered_goal_revision",
                return_value=2,
            ),
            patch.object(
                self.server.store,
                "_master_handoff_delivery_projection_requires_update",
                return_value=True,
            ),
        ):
            surface = self.server.goal_automation_surface(goal["goal_id"])
        self.assertEqual("MASTER_HANDOFF_UPDATE_READY", surface["automation_state"])
        self.assertEqual("DELIVER_MASTER_HANDOFF_UPDATE", surface["next_operation"])

    def test_goal_automation_advance_creates_and_delivers_handoff_once(self) -> None:
        goal = {"goal_id": "goal_auto_001", "project_id": "GCS", "revision": 1}
        application = {"application_id": "work_plan_application_auto_001"}
        ready_handoff = {
            "handoff_id": "handoff_0123456789abcdef01234567",
            "delivery_state": "DELIVERY_FAILED",
        }
        delivered_handoff = {
            **ready_handoff,
            "delivery_state": "QUEUED_FOR_MASTER",
            "room_message_id": "room_0123456789abcdef0123456789abcdef",
        }
        base = {
            "goal": goal,
            "application": application,
            "matching_proposals": [],
            "binding": None,
        }
        surfaces = [
            {
                **base,
                "handoff": None,
                "automation_state": "READY_FOR_MASTER_HANDOFF",
                "next_operation": "CREATE_AND_DELIVER_MASTER_HANDOFF",
            },
            {
                **base,
                "handoff": ready_handoff,
                "automation_state": "MASTER_HANDOFF_READY",
                "next_operation": "DELIVER_MASTER_HANDOFF",
            },
            {
                **base,
                "handoff": delivered_handoff,
                "automation_state": "WAITING_MASTER_PROPOSAL",
                "next_operation": "WAIT",
            },
        ]
        with (
            patch.object(
                self.server, "goal_automation_surface", side_effect=surfaces
            ),
            patch.object(
                self.server.store,
                "create_master_handoff",
                return_value=(ready_handoff, True),
            ) as create_handoff,
            patch.object(
                self.server,
                "deliver_master_handoff",
                return_value=(delivered_handoff, True, None),
            ) as deliver_handoff,
        ):
            result = self.server.advance_goal_automation(
                goal["goal_id"],
                {"approval": "ADVANCE", "expected_goal_revision": 1},
            )
        self.assertEqual("GOAL_AUTOMATION_ADVANCED", result["status"])
        self.assertEqual(
            ["MASTER_HANDOFF_CREATED", "MASTER_HANDOFF_DELIVERED"],
            result["operations"],
        )
        self.assertEqual(
            "WAITING_MASTER_PROPOSAL", result["surface"]["automation_state"]
        )
        create_handoff.assert_called_once()
        deliver_handoff.assert_called_once_with(
            "GCS", ready_handoff["handoff_id"], {"approval": "DELIVER"}
        )

    def test_goal_automation_scheduler_stops_and_recovers_expired_lease(self) -> None:
        self.server.store.register_project(self.registration())
        session, _ = self.server.session_supervisor.register_session(
            {
                "session_id": "goal-scheduler-current-session",
                "project_id": "GCS",
                "node": "GCS",
                "mode": "CONDUCTOR",
                "provider": "CODEX",
                "provider_session_ref": "goal-scheduler-provider-ref",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )
        gateway = TodoMutationGateway(self.endpoint, self.token)
        goal = self.server.store.create_goal(
            "GCS",
            {
                "title": "Schedule the bounded Goal loop",
                "description": "Stop at every governed input boundary.",
                "owner": "Project Master",
                "state": "ACTIVE",
                "sort_order": 0,
            },
        )
        status, started = self.request(
            "POST",
            f"/v1/goals/{goal['goal_id']}/automation/scheduler",
            {
                "action": "START",
                "expected_goal_revision": goal["revision"],
                "interval_seconds": 5,
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.CONFLICT, status)
        self.assertEqual(
            "GOAL_AUTOMATION_SCHEDULER_MUTATION_RECEIPT_REQUIRED",
            started["error_code"],
        )
        started = gateway.configure_scheduler(
            provider="CODEX",
            provider_session_ref="goal-scheduler-provider-ref",
            session_id=session["session_id"],
            session_anchor_ref=session["session_anchor_ref"],
            instruction_ref="conversation://test/goal-scheduler-start",
            goal_id=goal["goal_id"],
            action="START",
            expected_goal_revision=goal["revision"],
            interval_seconds=5,
        )
        self.assertEqual(
            "GOAL_AUTOMATION_SCHEDULER_MUTATION_APPLIED", started["status"]
        )
        self.assertEqual("CONSUMED", started["receipt"]["status"])
        self.assertEqual("READY", started["scheduler"]["status"])
        self.assertTrue(started["scheduler"]["enabled"])

        ready = {
            "goal": goal,
            "automation_state": "READY_FOR_MASTER_HANDOFF",
            "next_operation": "CREATE_AND_DELIVER_MASTER_HANDOFF",
        }
        waiting = {
            "goal": goal,
            "automation_state": "WAITING_MASTER_PROPOSAL",
            "next_operation": "WAIT",
        }
        with (
            patch.object(
                self.server,
                "goal_automation_surface",
                side_effect=[ready, waiting],
            ),
            patch.object(
                self.server,
                "advance_goal_automation",
                return_value={
                    "status": "GOAL_AUTOMATION_ADVANCED",
                    "operations": [
                        "MASTER_HANDOFF_CREATED",
                        "MASTER_HANDOFF_DELIVERED",
                    ],
                    "surface": waiting,
                },
            ) as advance,
        ):
            stopped = self.server.run_goal_automation_scheduler_once()
        self.assertEqual("GOAL_AUTOMATION_SCHEDULER_STOPPED", stopped["status"])
        self.assertEqual(
            ["MASTER_HANDOFF_CREATED", "MASTER_HANDOFF_DELIVERED"],
            stopped["operations"],
        )
        self.assertEqual(
            "WAITING_MASTER_PROPOSAL",
            stopped["scheduler"]["last_stop_reason"],
        )
        self.assertFalse(stopped["scheduler"]["enabled"])
        self.assertEqual(1, stopped["scheduler"]["tick_count"])
        advance.assert_called_once_with(
            goal["goal_id"],
            {"approval": "ADVANCE", "expected_goal_revision": goal["revision"]},
        )

        self.server.configure_goal_automation_scheduler(
            goal["goal_id"],
            {
                "action": "START",
                "expected_goal_revision": goal["revision"],
                "interval_seconds": 5,
            },
        )
        claimed = self.server.store.claim_due_goal_automation_scheduler(
            "expired-service-owner"
        )
        self.assertIsNotNone(claimed)
        connection = sqlite3.connect(self.server.store.database_path)
        try:
            connection.execute(
                "UPDATE goal_automation_scheduler SET lease_expires_at = ? WHERE goal_id = ?",
                ("2000-01-01T00:00:00Z", goal["goal_id"]),
            )
            connection.commit()
        finally:
            connection.close()
        with patch.object(
            self.server, "goal_automation_surface", return_value=waiting
        ):
            recovered = self.server.run_goal_automation_scheduler_once()
        self.assertEqual(
            "GOAL_AUTOMATION_SCHEDULER_STOPPED", recovered["status"]
        )
        self.assertEqual(2, recovered["scheduler"]["tick_count"])
        self.assertIsNone(recovered["scheduler"]["lease_owner"])

        resumed = gateway.configure_scheduler(
            provider="CODEX",
            provider_session_ref="goal-scheduler-provider-ref",
            session_id=session["session_id"],
            session_anchor_ref=session["session_anchor_ref"],
            instruction_ref="conversation://test/goal-scheduler-resume",
            goal_id=goal["goal_id"],
            action="START",
            expected_goal_revision=goal["revision"],
        )
        self.assertTrue(resumed["scheduler"]["enabled"])
        replayed = gateway.configure_scheduler(
            provider="CODEX",
            provider_session_ref="goal-scheduler-provider-ref",
            session_id=session["session_id"],
            session_anchor_ref=session["session_anchor_ref"],
            instruction_ref="conversation://test/goal-scheduler-resume",
            goal_id=goal["goal_id"],
            action="START",
            expected_goal_revision=goal["revision"],
        )
        self.assertTrue(replayed["replayed"])
        with self.assertRaises(TodoMutationGatewayError) as conflict:
            gateway.configure_scheduler(
                provider="CODEX",
                provider_session_ref="goal-scheduler-provider-ref",
                session_id=session["session_id"],
                session_anchor_ref=session["session_anchor_ref"],
                instruction_ref="conversation://test/goal-scheduler-resume",
                goal_id=goal["goal_id"],
                action="PAUSE",
                expected_goal_revision=goal["revision"],
            )
        self.assertEqual(
            "GOAL_AUTOMATION_SCHEDULER_MUTATION_RECEIPT_CONFLICT",
            conflict.exception.code,
        )
        paused = gateway.configure_scheduler(
            provider="CODEX",
            provider_session_ref="goal-scheduler-provider-ref",
            session_id=session["session_id"],
            session_anchor_ref=session["session_anchor_ref"],
            instruction_ref="conversation://test/goal-scheduler-pause",
            goal_id=goal["goal_id"],
            action="PAUSE",
            expected_goal_revision=goal["revision"],
        )
        self.assertEqual("PAUSED", paused["scheduler"]["status"])
        self.assertEqual("USER_PAUSED", paused["scheduler"]["last_stop_reason"])
        self.assertFalse(paused["scheduler"]["enabled"])

        with self.assertRaises(TodoMutationGatewayError) as wrong_anchor:
            gateway.configure_scheduler(
                provider="CODEX",
                provider_session_ref="goal-scheduler-provider-ref",
                session_id=session["session_id"],
                session_anchor_ref="session_anchor_wrong",
                instruction_ref="conversation://test/goal-scheduler-wrong-anchor",
                goal_id=goal["goal_id"],
                action="PAUSE",
                expected_goal_revision=goal["revision"],
            )
        self.assertEqual(
            "GOAL_AUTOMATION_SCHEDULER_MUTATION_ANCHOR_MISMATCH",
            wrong_anchor.exception.code,
        )
        with self.server.store._connection() as connection:
            connection.execute(
                "UPDATE project_goal SET revision = revision + 1 WHERE goal_id = ?",
                (goal["goal_id"],),
            )
        replay_after_goal_change = gateway.configure_scheduler(
            provider="CODEX",
            provider_session_ref="goal-scheduler-provider-ref",
            session_id=session["session_id"],
            session_anchor_ref=session["session_anchor_ref"],
            instruction_ref="conversation://test/goal-scheduler-pause",
            goal_id=goal["goal_id"],
            action="PAUSE",
            expected_goal_revision=goal["revision"],
        )
        self.assertTrue(replay_after_goal_change["replayed"])

    def test_experience_case_contains_only_recorded_observations(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            self.skill_observation_candidate(),
            self.token,
        )
        observation = self.server.store.list_skill_observations("GCS")[0]
        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        status, result = self.request(
            "POST",
            "/v1/projects/GCS/experience-cases",
            {
                "title": "Source review evidence case",
                "observation_ids": [observation["observation_id"]],
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("EXPERIENCE_CASE_RECORDED", result["status"])
        case = result["case"]
        self.assertEqual("OBSERVED", case["case_state"])
        self.assertEqual("NOT_INFERRED", case["causal_state"])
        self.assertEqual("NOT_EVALUATED", case["pattern_state"])
        self.assertEqual([observation["observation_id"]], case["observation_ids"])
        self.assertEqual(
            observation["evidence_refs"], case["observations"][0]["evidence_refs"]
        )
        self.assertTrue(all(value == "NONE" for value in case["effects"].values()))

        status, repeated = self.request(
            "POST",
            "/v1/projects/GCS/experience-cases",
            {
                "title": "Source review evidence case",
                "observation_ids": [observation["observation_id"]],
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("EXPERIENCE_CASE_ALREADY_RECORDED", repeated["status"])

        status, listed = self.request(
            "GET", "/v1/projects/GCS/experience-cases", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [case["case_id"]], [item["case_id"] for item in listed["cases"]]
        )

        status, rejected = self.request(
            "POST",
            "/v1/projects/GCS/experience-cases",
            {"observation_ids": ["observation_unknown"]},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "EXPERIENCE_CASE_OBSERVATION_NOT_FOUND", rejected["error_code"]
        )
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_experience_case_match_is_local_and_non_causal(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        first = self.skill_observation_candidate()
        status, first_result = self.request(
            "POST", "/v1/projects/GCS/skill-observations", first, self.token
        )
        self.assertEqual(201, status)
        first_observation = first_result["observations"][0]["observation_id"]
        second = json.loads(json.dumps(self.skill_observation_candidate()))
        second["candidate_id"] = "skill-observation-gcs-002"
        second["candidate"]["observations"][0]["observation_digest"] = "d" * 64
        second["candidate"]["observations"][0]["evidence_refs"] = [
            "receipt://gcs/test-002"
        ]
        status, second_result = self.request(
            "POST", "/v1/projects/GCS/skill-observations", second, self.token
        )
        self.assertEqual(201, status)
        second_observation = second_result["observations"][0]["observation_id"]

        status, first_case_result = self.request(
            "POST",
            "/v1/projects/GCS/experience-cases",
            {"observation_ids": [first_observation]},
            self.token,
        )
        self.assertEqual(201, status)
        first_case = first_case_result["case"]
        status, second_case_result = self.request(
            "POST",
            "/v1/projects/GCS/experience-cases",
            {"observation_ids": [second_observation]},
            self.token,
        )
        self.assertEqual(201, status)
        second_case = second_case_result["case"]

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/experience-matches",
            {"case_id": first_case["case_id"]},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("EXPERIENCE_CASE_MATCHES_COLLECTED", result["status"])
        self.assertEqual("PROJECT_LOCAL_OBSERVED_CASES", result["match_scope"])
        self.assertEqual(
            [second_case["case_id"]], [item["case_id"] for item in result["matches"]]
        )
        match = result["matches"][0]
        self.assertEqual("OBSERVED_SIMILARITY", match["relation"])
        self.assertEqual("NOT_INFERRED", match["causal_state"])
        self.assertEqual("NOT_EVALUATED", match["pattern_state"])
        self.assertEqual("source-review", match["shared_skills"][0]["skill_id"])

        status, proposal_result = self.request(
            "POST",
            "/v1/projects/GCS/experience-pattern-proposals",
            {"case_id": first_case["case_id"]},
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual(
            "EXPERIENCE_PATTERN_PROPOSAL_RECORDED", proposal_result["status"]
        )
        proposal = proposal_result["proposal"]
        self.assertEqual(2, proposal["support_case_count"])
        self.assertEqual("PROPOSAL_ONLY", proposal["promotion_state"])
        self.assertEqual("NOT_INFERRED", proposal["causal_state"])
        self.assertEqual("NOT_EVALUATED", proposal["predictive_state"])
        self.assertEqual(
            "source-review", proposal["observed_signature"]["skills"][0]["skill_id"]
        )
        self.assertTrue(all(value == "NONE" for value in proposal["effects"].values()))

        status, collected = self.request(
            "GET",
            "/v1/projects/GCS/experience-pattern-proposals",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [proposal["proposal_id"]],
            [item["proposal_id"] for item in collected["proposals"]],
        )

        status, queued = self.request(
            "POST",
            "/v1/projects/GCS/career-promotion-queue",
            {"pattern_proposal_id": proposal["proposal_id"]},
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("CAREER_PROMOTION_CANDIDATE_QUEUED", queued["status"])
        item = queued["item"]
        candidate = item["candidate"]
        self.assertEqual("QUEUED", item["status"])
        self.assertEqual("universe.career-promotion-candidate.v1", candidate["schema"])
        self.assertEqual("CANDIDATE_ONLY", candidate["promotion_state"])
        self.assertEqual("REDACTED", candidate["redaction_state"])
        self.assertEqual(
            proposal["proposal_digest"], candidate["source"]["pattern_proposal_digest"]
        )
        self.assertTrue(all(value == "NONE" for value in candidate["effects"].values()))

        status, repeated = self.request(
            "POST",
            "/v1/projects/GCS/career-promotion-queue",
            {"pattern_proposal_id": proposal["proposal_id"]},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            "CAREER_PROMOTION_CANDIDATE_ALREADY_QUEUED", repeated["status"]
        )

        status, queue = self.request(
            "GET", "/v1/career-promotion-queue", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [item["queue_id"]], [entry["queue_id"] for entry in queue["items"]]
        )

    def test_work_loop_predictions_are_reviewable_and_never_auto_adopted(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, empty = self.request(
            "POST",
            "/v1/projects/GCS/work-loop/predictions",
            {},
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual([], empty["prediction"]["suggestions"])
        self.assertEqual("UNSUPPORTED", empty["prediction"]["rejected"][0]["reason"])

        self.request(
            "POST",
            "/v1/projects/GCS/goals",
            {
                "title": "Ship the operator spine",
                "description": "",
                "owner": "Project Master",
                "state": "DESIGNING",
                "sort_order": 0,
            },
            self.token,
        )
        self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Ship the operator spine",
                "detail": "",
                "priority": "P1",
                "state": "DONE",
                "source_kind": "USER",
                "sort_order": 0,
            },
            self.token,
        )
        self.request(
            "POST",
            "/v1/projects/GCS/memories",
            {
                "title": "Ship the operator spine",
                "body": "Keep the operator spine reviewable.",
                "state": "OBSERVED",
            },
            self.token,
        )
        first = self.skill_observation_candidate()
        self.request(
            "POST", "/v1/projects/GCS/skill-observations", first, self.token
        )
        observation = self.server.store.list_skill_observations("GCS")[0]
        self.request(
            "POST",
            "/v1/projects/GCS/experience-cases",
            {
                "title": "Ship the operator spine",
                "observation_ids": [observation["observation_id"]],
            },
            self.token,
        )

        status, predicted = self.request(
            "POST",
            "/v1/projects/GCS/work-loop/predictions",
            {},
            self.token,
        )
        self.assertIn(status, {200, 201})
        prediction = predicted["prediction"]
        self.assertTrue(prediction["suggestions"])
        self.assertFalse(prediction["adoption_policy"]["auto_adopt"])
        self.assertEqual("PROPOSAL_ONLY", prediction["review_state"])
        goals_before = self.request("GET", "/v1/projects/GCS/goals", token=self.token)[1]

        status, reviewed = self.request(
            "POST",
            "/v1/projects/GCS/work-loop/predictions/review",
            {"proposal_id": prediction["proposal_id"], "decision": "KEEP"},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("KEPT", reviewed["prediction"]["review_state"])
        self.assertEqual("HIT", reviewed["prediction"]["feedback"]["state"])
        self.assertEqual(
            "USER_REVIEW", reviewed["prediction"]["feedback"]["basis"]
        )
        self.assertFalse(reviewed["prediction"]["goal_created"])
        self.assertFalse(reviewed["prediction"]["todo_created"])
        goals_after = self.request("GET", "/v1/projects/GCS/goals", token=self.token)[1]
        self.assertEqual(
            [item["goal_id"] for item in goals_before["goals"]],
            [item["goal_id"] for item in goals_after["goals"]],
        )
        status, conflict = self.request(
            "POST",
            "/v1/projects/GCS/work-loop/predictions/review",
            {"proposal_id": prediction["proposal_id"], "decision": "REJECT"},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "WORK_LOOP_PREDICTION_REVIEW_CONFLICT", conflict["error_code"]
        )

        failed = json.loads(json.dumps(self.skill_observation_candidate()))
        failed["candidate_id"] = "skill-observation-gcs-fail"
        failed["candidate"]["observations"][0]["observation_digest"] = "e" * 64
        failed["candidate"]["observations"][0]["outcome"] = "FAILED"
        failed["candidate"]["observations"][0]["validation_state"] = "FAIL"
        failed["candidate"]["observations"][0]["evidence_refs"] = [
            "receipt://gcs/fail-001"
        ]
        status, failed_obs = self.request(
            "POST", "/v1/projects/GCS/skill-observations", failed, self.token
        )
        self.assertEqual(201, status)
        fail_id = failed_obs["observations"][0]["observation_id"]
        self.request(
            "POST",
            "/v1/projects/GCS/experience-cases",
            {
                "title": "source review operator spine",
                "observation_ids": [fail_id],
            },
            self.token,
        )
        status, risked = self.request(
            "POST",
            "/v1/projects/GCS/work-loop/predictions",
            {},
            self.token,
        )
        self.assertIn(status, {200, 201})
        risk_titles = [
            item["title"]
            for item in risked["prediction"]["suggestions"]
            if item["kind"] == "RISK"
        ]
        self.assertTrue(risk_titles)

    def test_work_loop_recovers_interrupted_todos_and_fans_out_results(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        _, unlinked = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Legitimate in-flight work",
                "detail": "",
                "priority": "P1",
                "state": "IN_PROGRESS",
                "source_kind": "USER",
                "sort_order": 0,
            },
            self.token,
        )
        _, todo_result = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Recover interrupted work",
                "detail": "",
                "priority": "P1",
                "state": "IN_PROGRESS",
                "source_kind": "USER",
                "sort_order": 0,
            },
            self.token,
        )
        todo_id = todo_result["todo"]["todo_id"]
        self.server.store.create_room_message(
            "GCS",
            {
                "kind": "QUESTION",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "do the work",
                "todo_id": todo_id,
                "idempotency_key": "recover-todo-001",
            },
            delivery_state="FAILED",
        )
        status, recovered = self.request(
            "POST",
            "/v1/projects/GCS/work-loop/recover",
            {},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("WORK_LOOP_TODOS_RECOVERED", recovered["status"])
        self.assertEqual([todo_id], [item["todo_id"] for item in recovered["recovered"]])
        refreshed = self.server.store.get_todo(todo_id)
        self.assertEqual("READY", refreshed["state"])
        self.assertEqual(
            "IN_PROGRESS", self.server.store.get_todo(unlinked["todo"]["todo_id"])["state"]
        )

        _, done = self.request(
            "POST",
            "/v1/todos",
            {
                "scope_kind": "PROJECT",
                "project_id": "GCS",
                "title": "Completed fan-out",
                "detail": "",
                "priority": "P2",
                "state": "IN_PROGRESS",
                "source_kind": "USER",
                "sort_order": 1,
            },
            self.token,
        )
        done_id = done["todo"]["todo_id"]
        message, _ = self.server.store.create_room_message(
            "GCS",
            {
                "kind": "QUESTION",
                "sender": "UNIVERSE_CONDUCTOR",
                "body": "finish it",
                "todo_id": done_id,
                "idempotency_key": "fanout-todo-001",
            },
        )
        room_message_count = len(self.server.store.list_room_messages("GCS"))
        transition = self.server.store.apply_master_message_todo_transition(
            "GCS",
            message["message_id"],
            outcome="COMPLETED",
        )
        self.assertEqual("TODO_TRANSITION_APPLIED", transition["status"])
        self.assertEqual("DONE", transition["state"])
        self.assertFalse(transition["result_fanout"]["auto_adopted"])
        repeated_transition = self.server.store.apply_master_message_todo_transition(
            "GCS",
            message["message_id"],
            outcome="COMPLETED",
        )
        self.assertEqual("TODO_TRANSITION_NOT_REQUIRED", repeated_transition["status"])
        repeated_fanout = self.server.store.record_todo_result_fanout(
            "GCS", done_id, "COMPLETED", "DONE"
        )
        self.assertEqual(
            "WORK_LOOP_RESULT_FANOUT_ALREADY_RECORDED", repeated_fanout["status"]
        )
        self.assertEqual(
            transition["result_fanout"]["fanout_id"], repeated_fanout["fanout_id"]
        )
        snapshot = self.request(
            "GET", "/v1/projects/GCS/work-loop", token=self.token
        )[1]
        self.assertEqual("WORK_LOOP_COLLECTED", snapshot["status"])
        self.assertTrue(snapshot["result_fanouts"])
        self.assertEqual(1, len(snapshot["result_fanouts"]))
        self.assertEqual(5, len(snapshot["review_candidates"]))
        self.assertEqual(
            {
                "GOAL_PLAN",
                "EXPERIENCE",
                "MEMORY",
                "BENCH",
                "DOCUMENT_AUTOMATION",
            },
            {item["sink_kind"] for item in snapshot["review_candidates"]},
        )
        self.assertTrue(
            all(not item["auto_adopt"] for item in snapshot["review_candidates"])
        )
        self.assertEqual(
            room_message_count, len(self.server.store.list_room_messages("GCS"))
        )
        review_target = snapshot["review_candidates"][0]
        status, reviewed = self.request(
            "POST",
            "/v1/projects/GCS/work-loop/review-candidates/review",
            {"candidate_id": review_target["candidate_id"], "decision": "KEEP"},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("KEPT", reviewed["candidate"]["review_state"])
        self.assertFalse(reviewed["candidate"]["auto_adopt"])
        self.assertFalse(snapshot["adoption_policy"]["auto_adopt"])

    def test_memory_batch_schedule_claim_retry_recovery_and_stale_lease(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        config = self.server.store.upsert_memory_batch_config(
            "GCS",
            {
                "stage": "CONSOLIDATE",
                "provider": "CODEX",
                "model_ref": "gpt-5.6-luna",
                "effort": "MAX",
                "schedule": {"kind": "HOURLY"},
                "quota_or_budget": {"max_runs": 2, "window_hours": 6},
                "fallback": "DETERMINISTIC",
                "enabled": True,
                "dry_run": True,
            },
        )
        schedule = self.server.store.list_memory_batch_schedule_states("GCS")[0]
        self.assertEqual(config["config_id"], schedule["schedule_id"])
        self.assertEqual("READY", schedule["state"])

        due = schedule["next_due_at"]
        claim = self.server.store.claim_due_memory_batch_schedule(
            now=due,
            lease_owner="host-a",
            lease_seconds=60,
        )
        self.assertIsNotNone(claim)
        self.assertEqual("CLAIMED", claim["state"])
        self.assertIsNone(
            self.server.store.claim_due_memory_batch_schedule(
                now=due,
                lease_owner="host-b",
                lease_seconds=60,
            )
        )
        with self.assertRaises(UniverseError) as stale:
            self.server.store.finish_memory_batch_schedule(
                {**claim, "lease_generation": claim["lease_generation"] + 1},
                now=due,
                outcome="SUCCEEDED",
                run_id="run-stale",
            )
        self.assertEqual("MEMORY_BATCH_SCHEDULE_LEASE_STALE", stale.exception.code)

        lease_expired_at = (
            datetime.fromisoformat(claim["lease_expires_at"].replace("Z", "+00:00"))
            + timedelta(seconds=1)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        recovered = self.server.store.recover_expired_memory_batch_schedules(
            lease_expired_at
        )
        self.assertEqual([schedule["schedule_id"]], recovered)
        self.assertEqual(
            [], self.server.store.recover_expired_memory_batch_schedules(lease_expired_at)
        )
        retry = self.server.store.list_memory_batch_schedule_states("GCS")[0]
        self.assertEqual("RETRY_WAIT", retry["state"])
        self.assertEqual(1, retry["attempt_count"])

        second = self.server.store.claim_due_memory_batch_schedule(
            now=retry["next_due_at"],
            lease_owner="host-b",
            lease_seconds=60,
        )
        failed = self.server.store.finish_memory_batch_schedule(
            second,
            now=retry["next_due_at"],
            outcome="FAILED",
            error_code="EXPECTED_FAILURE",
        )
        self.assertEqual("RETRY_WAIT", failed["state"])
        self.assertEqual(2, failed["attempt_count"])

    def test_memory_batch_schedule_recovers_claim_after_store_restart(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.store.upsert_memory_batch_config(
            "GCS",
            {
                "stage": "SYNTHESIZE",
                "provider": "CODEX",
                "model_ref": "gpt-5.6-luna",
                "effort": "MAX",
                "schedule": {"kind": "HOURLY"},
                "quota_or_budget": None,
                "fallback": "DETERMINISTIC",
                "enabled": True,
                "dry_run": True,
            },
        )
        schedule = next(
            item
            for item in self.server.store.list_memory_batch_schedule_states("GCS")
            if item["stage"] == "SYNTHESIZE"
        )
        claim = self.server.store.claim_due_memory_batch_schedule(
            now=schedule["next_due_at"],
            lease_owner="host-before-restart",
            lease_seconds=60,
        )
        self.assertIsNotNone(claim)

        restarted_store = UniverseStore(self.server.store.database_path)
        expired_at = (
            datetime.fromisoformat(claim["lease_expires_at"].replace("Z", "+00:00"))
            + timedelta(seconds=1)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.assertEqual(
            [schedule["schedule_id"]],
            restarted_store.recover_expired_memory_batch_schedules(expired_at),
        )
        recovered = next(
            item
            for item in restarted_store.list_memory_batch_schedule_states("GCS")
            if item["stage"] == "SYNTHESIZE"
        )
        self.assertEqual("RETRY_WAIT", recovered["state"])
        self.assertEqual(claim["due_slot_key"], recovered["due_slot_key"])
        self.assertEqual(1, recovered["attempt_count"])
        self.assertEqual(
            [], restarted_store.recover_expired_memory_batch_schedules(expired_at)
        )

    def test_memory_batch_scheduler_tick_executes_due_stage_once(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.server.store.upsert_memory_batch_config(
            "GCS",
            {
                "stage": "CONSOLIDATE",
                "provider": "CODEX",
                "model_ref": "gpt-5.6-luna",
                "effort": "MAX",
                "schedule": {"kind": "HOURLY"},
                "quota_or_budget": None,
                "fallback": "DETERMINISTIC",
                "enabled": True,
                "dry_run": True,
            },
        )
        schedule = self.server.store.list_memory_batch_schedule_states("GCS")[0]
        due = datetime.fromisoformat(schedule["next_due_at"].replace("Z", "+00:00"))
        clock = Mock()
        clock.now.return_value = due
        calls: list[tuple[str, str]] = []
        self.server.memory_batch_scheduler.clock = clock
        self.server.memory_batch_scheduler.execute = lambda project_id, stage: (
            calls.append((project_id, stage)) or {"run_id": "scheduled-run-1"}
        )

        tick = self.server.memory_batch_scheduler.tick()
        self.assertEqual([("GCS", "CONSOLIDATE")], calls)
        self.assertEqual("SUCCEEDED", tick["executions"][0]["outcome"])
        terminal = self.server.store.list_memory_batch_schedule_states("GCS")[0]
        self.assertEqual("SUCCEEDED", terminal["last_outcome"])
        self.assertEqual("scheduled-run-1", terminal["last_run_id"])
        self.assertEqual(0, terminal["attempt_count"])

        repeated = self.server.memory_batch_scheduler.tick()
        self.assertEqual([], repeated["executions"])
        self.assertEqual([("GCS", "CONSOLIDATE")], calls)

    def test_context_pack_skill_plan_and_adoption_remain_non_executing(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.request("POST", "/v1/projects/GCS/seed", self.project_seed(), self.token)
        self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            self.skill_observation_candidate(),
            self.token,
        )

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/context-packs",
            {
                "purpose": "Review the broker integration contract.",
                "node_ids": ["broker-client"],
                "bench_limit": 10,
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("CONTEXT_PACK_READY", result["status"])
        pack = result["context_pack"]
        self.assertEqual(["broker-client"], pack["node_ids"])
        self.assertEqual(
            {"architecture", "broker-contract"},
            {document["document_id"] for document in pack["documents"]},
        )
        self.assertEqual("PROJECT_LOCAL_ONLY", pack["bench"]["scope"])
        self.assertEqual(1, pack["bench"]["observation_count"])
        self.assertEqual("NONE", pack["effects"]["task_frame"])

        status, repeated = self.request(
            "POST",
            "/v1/projects/GCS/context-packs",
            {
                "purpose": "Review the broker integration contract.",
                "node_ids": ["broker-client"],
                "bench_limit": 10,
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("CONTEXT_PACK_ALREADY_RECORDED", repeated["status"])

        status, plan_result = self.request(
            "POST",
            "/v1/projects/GCS/skill-plan-proposals",
            {
                "context_pack_id": pack["context_pack_id"],
                "purpose": "Review the broker integration contract.",
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("SKILL_PLAN_PROPOSAL_READY", plan_result["status"])
        proposal = plan_result["proposal"]
        self.assertEqual("PROJECT_LOCAL_BENCH_AVAILABLE", proposal["evidence_state"])
        self.assertEqual(1, len(proposal["candidates"]))
        candidate = proposal["candidates"][0]
        self.assertEqual("source-review", candidate["skill"]["skill_id"])
        self.assertEqual(1, candidate["rank"])
        self.assertEqual("OPENAI", candidate["provider_ref"])
        self.assertEqual("CANDIDATE_ONLY", candidate["recommendation_state"])
        self.assertEqual("PROJECT_MASTER_BINDING_REQUIRED", candidate["binding_state"])

        status, adoption_result = self.request(
            "POST",
            "/v1/projects/GCS/skill-plan-adoptions",
            {
                "proposal_id": proposal["proposal_id"],
                "candidate_ids": [candidate["candidate_id"]],
                "approval": "ADOPTED",
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("SKILL_PLAN_ADOPTED", adoption_result["status"])
        adoption = adoption_result["adoption"]
        self.assertEqual("PROJECT_MASTER_HANDOFF_CANDIDATE", adoption["next_operation"])
        self.assertEqual("NONE", adoption["effects"]["task_frame"])

        status, collected = self.request(
            "GET", "/v1/projects/GCS/skill-plan-adoptions", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            [adoption["adoption_id"]],
            [item["adoption_id"] for item in collected["adoptions"]],
        )
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

        status, rejected = self.request(
            "POST",
            "/v1/projects/GCS/context-packs",
            {"purpose": "Unknown node", "node_ids": ["does-not-exist"]},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("CONTEXT_PACK_NODE_UNKNOWN", rejected["error_code"])

    def test_llm_retrieval_injects_linked_memory_and_bench_skill_candidates(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request("POST", "/v1/projects/GCS/seed", self.project_seed(), self.token)
        linked = self.server.store.create_project_memory(
            "GCS",
            {
                "title": "Broker source review",
                "body": "Reuse the validated source review flow for broker changes.",
                "state": "OBSERVED",
            },
        )
        unlinked = self.server.store.create_project_memory(
            "GCS",
            {
                "title": "Broker source review draft",
                "body": "This unreviewed note must not reach an LLM context.",
                "state": "OBSERVED",
            },
        )
        self.server.store.link_project_memory(
            "GCS",
            linked["memory_id"],
            {
                "node_ref": "broker-client",
                "graph": "functional",
                "link_state": "LINKED",
            },
        )
        observation = self.skill_observation_candidate()
        observation["candidate"]["observations"][0]["execution_context"] = {
            "provider_ref": "OPENAI",
            "worker_role": "SUB_REVIEWER",
            "task_kind": "SOURCE_REVIEW",
            "node_ref": "broker-client",
            "failure_kind": "NONE",
            "quota_state": "AVAILABLE",
        }
        self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            observation,
            self.token,
        )

        retrieval = self.server.store.build_project_llm_retrieval_context(
            "GCS",
            query="Review the broker source changes",
            node_ids=["broker-client"],
        )
        self.assertEqual("LINKED_ONLY", retrieval["memory"]["policy"])
        self.assertEqual(
            [linked["memory_id"]],
            [item["memory_id"] for item in retrieval["memory"]["hits"]],
        )
        self.assertNotIn(
            unlinked["memory_id"],
            {item["memory_id"] for item in retrieval["memory"]["hits"]},
        )
        recommended = retrieval["bench"]["recommended_skills"]
        self.assertEqual(["source-review"], [item["skill"]["skill_id"] for item in recommended])
        self.assertEqual("CANDIDATE_ONLY", recommended[0]["recommendation_state"])
        self.assertEqual("TASK_FRAME_SELECTION_REQUIRED", recommended[0]["binding_state"])
        self.assertEqual("NONE", recommended[0]["authority"])
        self.assertEqual("NONE", retrieval["effects"]["skill_binding"])

        pack, _ = self.server.store.create_context_pack(
            "GCS",
            {
                "purpose": "Review the broker source changes",
                "node_ids": ["broker-client"],
                "bench_limit": 10,
            },
        )
        self.assertEqual(retrieval["memory"]["hits"], pack["retrieval"]["memory"]["hits"])
        self.assertEqual(
            ["source-review"],
            [
                item["skill"]["skill_id"]
                for item in pack["retrieval"]["bench"]["recommended_skills"]
            ],
        )

    def test_semantic_graph_projects_linked_memory_as_redacted_rag_source(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        memory = self.server.store.create_project_memory(
            "GCS",
            {
                "title": "Broker review context",
                "body": "Secret review notes must remain in the source memory store.",
                "state": "OBSERVED",
            },
        )
        self.server.store.link_project_memory(
            "GCS",
            memory["memory_id"],
            {
                "node_ref": "broker-client",
                "graph": "functional",
                "link_state": "LINKED",
            },
        )

        graph = self.server.store.semantic_project_graph("GCS")

        rag_nodes = [item for item in graph["nodes"] if item["entity_type"] == "RAG_SOURCE"]
        self.assertEqual(1, len(rag_nodes))
        self.assertEqual(memory["memory_id"], rag_nodes[0]["data"]["memory_id"])
        self.assertTrue(rag_nodes[0]["data"]["body_in_graph"] is False)
        self.assertNotIn("Secret review notes", json.dumps(graph))
        edge_types = {item["edge_type"] for item in graph["edges"]}
        self.assertIn("PROJECT_HAS_RAG_SOURCE", edge_types)
        self.assertIn("RAG_SOURCE_USES_MEMORY", edge_types)

    def test_selected_skill_plan_is_bound_to_master_context_once(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request("POST", "/v1/projects/GCS/seed", self.project_seed(), self.token)
        self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            self.skill_observation_candidate(),
            self.token,
        )
        _, pack_result = self.request(
            "POST",
            "/v1/projects/GCS/context-packs",
            {
                "purpose": "Review the broker integration contract.",
                "node_ids": ["broker-client"],
            },
            self.token,
        )
        _, plan_result = self.request(
            "POST",
            "/v1/projects/GCS/skill-plan-proposals",
            {
                "context_pack_id": pack_result["context_pack"]["context_pack_id"],
                "purpose": "Review the broker integration contract.",
            },
            self.token,
        )
        proposal = plan_result["proposal"]
        candidate = proposal["candidates"][0]
        _, adoption_result = self.request(
            "POST",
            "/v1/projects/GCS/skill-plan-adoptions",
            {
                "proposal_id": proposal["proposal_id"],
                "candidate_ids": [candidate["candidate_id"]],
                "approval": "ADOPTED",
            },
            self.token,
        )
        adoption = adoption_result["adoption"]
        _, handoff_result = self.request(
            "POST",
            "/v1/projects/GCS/master-handoffs",
            {
                "source": {
                    "kind": "SKILL_PLAN",
                    "adoption_id": adoption["adoption_id"],
                }
            },
            self.token,
        )
        handoff = handoff_result["handoff"]
        _, bridge_result = self.request(
            "POST",
            "/v1/projects/GCS/master-bridge",
            {
                "endpoint": "http://127.0.0.1:9011",
                "credential_env": "UNIVERSE_GCS_MASTER_BRIDGE_TOKEN",
                "master_session_ref": "opaque-project-master-session",
                "binding_evidence_ref": "project-host://GCS/master-session/registered",
            },
            self.token,
        )
        bridge = bridge_result["bridge"]
        application_delivery = {
            "schema": "universe.project-master-skill-plan-apply-delivery-receipt.v1",
            "status": "DELIVERED",
            "project_id": "GCS",
            "handoff_id": handoff["handoff_id"],
            "endpoint": bridge["endpoint"],
            "host_response": {
                "schema": "universe.project-skill-plan-master-receipt.v1",
                "status": "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT",
                "project_id": "GCS",
                "handoff_id": handoff["handoff_id"],
                "adoption_id": adoption["adoption_id"],
                "selection_digest": adoption["selection_digest"],
                "context_digest": "3" * 64,
                "binding_state": "PROJECT_MASTER_CONTEXT_BOUND",
                "skill_ref_resolution": "REQUIRED",
                "task_frame_binding": "NOT_CREATED",
                "repository_write": False,
                "receipt_digest": "4" * 64,
                "binding_proposal": {
                    "schema": "universe.project-master-skill-binding-proposal.v1",
                    "status": "PROJECT_SKILL_BINDING_PROPOSAL_READY",
                    "project_id": "GCS",
                    "handoff_id": handoff["handoff_id"],
                    "adoption_id": adoption["adoption_id"],
                    "context_digest": "3" * 64,
                    "binding_state": "PROJECT_MASTER_BINDING_PROPOSED",
                    "skill_bindings": [
                        {
                            "skill_id": "source-review",
                            "skill_version": "1.0.0",
                            "skill_ref": ".ai/skills/common/source-review/SKILL.md",
                            "context_pack_digest": "c" * 64,
                            "operation_class": "READ",
                        }
                    ],
                    "resolution_evidence": [],
                    "approval_required": True,
                    "task_frame_started": False,
                    "authority_created": False,
                    "execution_assignment_created": False,
                    "repository_write": False,
                    "next_operation": "TASK_FRAME_PROPOSAL_REQUIRED",
                    "proposal_digest": "5" * 64,
                    "proposal_id": "skillbind_" + "5" * 24,
                },
            },
            "delivered_at": "2026-07-30T10:00:00Z",
        }
        message_delivery = {
            "status": "DELIVERED",
            "bridge_id": bridge["bridge_id"],
            "project_id": "GCS",
            "message_id": "bridge-created-placeholder",
            "delivered_at": "2026-07-30T10:00:01Z",
        }

        with (
            patch(
                "universe_server.HttpProjectMasterBridge.apply_skill_plan",
                return_value=application_delivery,
            ) as apply_skill_plan,
            patch(
                "universe_server.HttpProjectMasterBridge.deliver",
                return_value=message_delivery,
            ) as deliver,
        ):
            status, first = self.request(
                "POST",
                f"/v1/projects/GCS/master-handoffs/{handoff['handoff_id']}/deliver",
                {"approval": "DELIVER"},
                self.token,
            )
            status_repeated, repeated = self.request(
                "POST",
                f"/v1/projects/GCS/master-handoffs/{handoff['handoff_id']}/deliver",
                {"approval": "DELIVER"},
                self.token,
            )

        self.assertEqual(200, status)
        self.assertEqual(200, status_repeated)
        self.assertEqual("PROJECT_MASTER_HANDOFF_DELIVERED", first["status"])
        self.assertEqual(
            "PROJECT_SKILL_PLAN_BOUND_TO_MASTER_CONTEXT",
            first["skill_plan_application"]["status"],
        )
        self.assertEqual(
            "PROJECT_MASTER_HANDOFF_ALREADY_DELIVERED",
            repeated["status"],
        )
        self.assertEqual(
            first["skill_plan_application"]["application_digest"],
            repeated["skill_plan_application"]["application_digest"],
        )
        self.assertEqual(1, apply_skill_plan.call_count)
        self.assertEqual(1, deliver.call_count)
        approval = apply_skill_plan.call_args.kwargs["approval"]
        self.assertEqual(handoff["handoff_digest"], approval["handoff_digest"])
        self.assertEqual(adoption["selection_digest"], approval["selection_digest"])
        stored = self.server.store.get_skill_plan_master_application(
            "GCS",
            handoff["handoff_id"],
        )
        self.assertIsNotNone(stored)

    def test_skill_plan_ranks_skill_model_provider_candidates_from_bench(self) -> None:
        self.assertEqual(
            "GROK",
            provider_ref_from_model_ref("provider://GROK/model/grok-build"),
        )
        self.assertEqual("UNKNOWN", provider_ref_from_model_ref("legacy-model-ref"))
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request("POST", "/v1/projects/GCS/seed", self.project_seed(), self.token)

        candidate = self.skill_observation_candidate()
        candidate["candidate_id"] = "skill-observation-ranking-001"
        base = candidate["candidate"]["observations"][0]

        def observation(
            *,
            marker: str,
            model_ref: str,
            outcome: str,
            validation_state: str,
            duration_ms: int,
        ) -> JsonObject:
            return {
                **base,
                "observation_digest": marker * 64,
                "skill_binding_digest": marker * 64,
                "model_ref": model_ref,
                "outcome": outcome,
                "validation_state": validation_state,
                "metrics": {"duration_ms": duration_ms},
            }

        candidate["candidate"]["observations"] = [
            observation(
                marker="1",
                model_ref="provider://GROK/model/grok-build",
                outcome="SUCCEEDED",
                validation_state="PASS",
                duration_ms=80,
            ),
            observation(
                marker="2",
                model_ref="provider://CODEX/model/gpt-5.6",
                outcome="SUCCEEDED",
                validation_state="NOT_RUN",
                duration_ms=30,
            ),
            observation(
                marker="3",
                model_ref="provider://CODEX/model/gpt-5.6",
                outcome="SUCCEEDED",
                validation_state="NOT_RUN",
                duration_ms=40,
            ),
            observation(
                marker="4",
                model_ref="provider://OPENAI/model/gpt-test",
                outcome="FAILED",
                validation_state="FAIL",
                duration_ms=10,
            ),
        ]
        status, _ = self.request(
            "POST",
            "/v1/projects/GCS/skill-observations",
            candidate,
            self.token,
        )
        self.assertEqual(201, status)

        status, context_result = self.request(
            "POST",
            "/v1/projects/GCS/context-packs",
            {
                "purpose": "Rank source review providers.",
                "node_ids": ["broker-client"],
                "bench_limit": 10,
            },
            self.token,
        )
        self.assertEqual(201, status)
        status, plan_result = self.request(
            "POST",
            "/v1/projects/GCS/skill-plan-proposals",
            {
                "context_pack_id": context_result["context_pack"]["context_pack_id"],
                "purpose": "Rank source review providers.",
            },
            self.token,
        )
        self.assertEqual(201, status)
        ranked = plan_result["proposal"]["candidates"]
        self.assertEqual(
            ["GROK", "CODEX", "OPENAI"], [item["provider_ref"] for item in ranked]
        )
        self.assertEqual([1, 2, 3], [item["rank"] for item in ranked])
        self.assertEqual(
            [1, 0, 0],
            [item["bench_rationale"]["validated_success_count"] for item in ranked],
        )
        self.assertTrue(
            all(item["recommendation_state"] == "CANDIDATE_ONLY" for item in ranked)
        )
        self.assertEqual(
            "USER_SELECTION_REQUIRED",
            plan_result["proposal"]["next_operation"],
        )

    def test_dispatch_delivery_wake_and_result_lifecycle_is_durable(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        dispatch_request = {
            "idempotency_key": "gcs-broker-001",
            "title": "Implement broker adapter",
            "instruction": "Add the bounded broker adapter and tests.",
            "constraints": ["Do not change live order execution."],
            "expected_output": {"tests": "passing"},
            "requested_mode": "MASTER",
        }
        status, queued = self.request(
            "POST",
            "/v1/projects/GCS/dispatches",
            dispatch_request,
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("DISPATCH_QUEUED", queued["status"])
        dispatch_id = queued["dispatch"]["dispatch_id"]

        status, repeated = self.request(
            "POST",
            "/v1/projects/GCS/dispatches",
            dispatch_request,
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("DISPATCH_ALREADY_QUEUED", repeated["status"])
        self.assertEqual(dispatch_id, repeated["dispatch"]["dispatch_id"])

        status, unapproved = self.request(
            "POST",
            f"/v1/dispatches/{dispatch_id}/deliver",
            {},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "DISPATCH_DELIVERY_APPROVAL_REQUIRED",
            unapproved["error_code"],
        )
        status, delivered = self.request(
            "POST",
            f"/v1/dispatches/{dispatch_id}/deliver",
            {"approval": "APPROVED"},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("DISPATCH_DELIVERED", delivered["status"])
        inbox_file = (
            self.project_root / ".ai" / "inbox" / "MASTER" / f"{dispatch_id}.json"
        )
        self.assertTrue(inbox_file.is_file())

        status, woken = self.request(
            "POST",
            f"/v1/dispatches/{dispatch_id}/wake",
            {"kind": "NONE"},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_WAKE_RECORDED", woken["status"])

        status, acknowledged = self.request(
            "POST",
            f"/v1/dispatches/{dispatch_id}/acknowledge",
            {"evidence_ref": "project-inbox:ack-001"},
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("ACKNOWLEDGED", acknowledged["dispatch"]["status"])
        status, started = self.request(
            "POST",
            f"/v1/dispatches/{dispatch_id}/start",
            {
                "evidence_ref": "project-master:task-frame-001",
                "details": {"task_frame_ref": "task-frame-001"},
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("STARTED", started["dispatch"]["status"])
        status, completed = self.request(
            "POST",
            f"/v1/dispatches/{dispatch_id}/result",
            {
                "status": "COMPLETED",
                "summary": "Broker adapter completed.",
                "evidence_refs": ["commit:abc", "pytest:pass"],
                "outputs": {"changed": ["src/broker.py"]},
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("COMPLETED", completed["dispatch"]["status"])
        self.assertEqual(
            "COMPLETED",
            completed["result_packet"]["status"],
        )
        self.assertEqual(
            [
                "QUEUED",
                "DELIVERED",
                "DELIVERED",
                "ACKNOWLEDGED",
                "STARTED",
                "COMPLETED",
            ],
            [event["status"] for event in completed["events"]],
        )

        reopened = UniverseStore(self.server.store.database_path)
        persisted = reopened.get_dispatch(dispatch_id)
        self.assertEqual("COMPLETED", persisted["dispatch"]["status"])
        self.assertEqual(
            completed["result_packet"]["result_digest"],
            persisted["result_packet"]["result_digest"],
        )

    def test_dispatch_stays_queued_when_master_inbox_is_unavailable(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        (self.project_root / ".ai" / "inbox" / "MASTER").rmdir()
        status, queued = self.request(
            "POST",
            "/v1/projects/GCS/dispatches",
            {
                "idempotency_key": "offline-001",
                "title": "Offline task",
                "instruction": "Keep this task queued.",
            },
            self.token,
        )
        self.assertEqual(201, status)
        dispatch_id = queued["dispatch"]["dispatch_id"]
        status, blocked = self.request(
            "POST",
            f"/v1/dispatches/{dispatch_id}/deliver",
            {"approval": "APPROVED"},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("DISPATCH_DELIVERY_BLOCKED", blocked["error_code"])
        status, observed = self.request(
            "GET",
            f"/v1/dispatches/{dispatch_id}",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("QUEUED", observed["dispatch"]["status"])

    def test_project_seed_projection_and_incorporation_proposal_are_read_only(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }

        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_SEED_RECORDED", result["status"])
        seed = result["seed"]
        self.assertEqual(64, len(seed["seed_digest"]))
        self.assertTrue(all("content" not in node for node in seed["nodes"]))
        self.assertFalse(seed["verification"]["raw_file_content_stored"])
        self.assertEqual("PROJECT_SUBMITTED", seed["verification"]["source_commit"])
        self.assertEqual("VERIFIED_LOCAL", seed["verification"]["file_digests"])

        status, repeated = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_SEED_ALREADY_RECORDED", repeated["status"])

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/projection",
            {
                "seed_id": "gcs-seed-001",
                "expected_seed_digest": seed["seed_digest"],
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_PROJECTION_BUILT", result["status"])
        projection = result["projection"]
        self.assertEqual(
            {"broker-client", "strategy-viewer"},
            {node["node_id"] for node in projection["nodes"]},
        )
        self.assertEqual(
            {"node:broker-client", "node:strategy-viewer", "document:operations"},
            {item["subject_ref"] for item in projection["missing_connections"]},
        )
        self.assertEqual("NONE", projection["effects"]["project_source_write"])

        # Unified node graph model: additive fields on the projection.
        self.assertIsInstance(projection["unified_graph"], dict)
        unified_ids = {
            node["node_id"] for node in projection["unified_graph"]["nodes"]
        }
        self.assertIn("broker-client", unified_ids)
        self.assertIn("strategy-viewer", unified_ids)
        self.assertEqual(
            {"galaxy", "functional", "structural", "flow", "knowledge", "kanban"},
            {view["view"] for view in projection["views"]},
        )

        status, fetched = self.request(
            "GET", "/v1/projects/GCS/projection", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(
            projection["projection_digest"],
            fetched["projection"]["projection_digest"],
        )

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/document-incorporation-proposals",
            {
                "projection_id": projection["projection_id"],
                "expected_projection_digest": projection["projection_digest"],
            },
            self.token,
        )
        self.assertEqual(201, status)
        self.assertEqual("INCORPORATION_PROPOSAL_READY", result["status"])
        proposal = result["proposal"]
        self.assertEqual("PROJECT", proposal["execution_owner"])
        self.assertEqual(0, proposal["effects"]["documents_moved"])
        self.assertTrue(
            all(
                operation["target_path"].startswith(".ai/universe/")
                for operation in proposal["operations"]
            )
        )
        graph = self.server.store.semantic_project_graph("GCS")
        document_nodes = [
            item
            for item in graph["nodes"]
            if item["entity_type"] == "DOCUMENT_AUTOMATION"
        ]
        self.assertEqual(1, len(document_nodes))
        self.assertEqual(
            proposal["proposal_id"], document_nodes[0]["data"]["proposal_id"]
        )
        self.assertEqual(
            len(proposal["operations"]),
            document_nodes[0]["data"]["operation_count"],
        )
        self.assertNotIn("source_path", document_nodes[0]["data"])
        self.assertIn(
            "PROJECT_HAS_DOCUMENT_AUTOMATION",
            {item["edge_type"] for item in graph["edges"]},
        )
        self.assertFalse((self.project_root / ".ai" / "universe").exists())
        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            projection["projection_digest"],
            reopened.get_project_projection("GCS")["projection_digest"],
        )
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_project_seed_preserves_reference_context_and_document_roles(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        seed_input = self.project_seed()
        seed_input["project"]["summary"] = (
            "Trading system architecture and work context"
        )
        seed_input["project"]["working_rules"] = [
            "Keep source mutations inside an approved Project scope.",
            "Use linked design and contract documents before implementation.",
        ]
        seed_input["documents"][0]["title"] = "System architecture"
        seed_input["documents"][0]["role"] = "specification"
        seed_input["documents"][0]["node_ids"] = []
        seed_input["documents"][0]["project_wide"] = True

        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", seed_input, self.token
        )

        self.assertEqual(201, status)
        seed = result["seed"]
        self.assertEqual(
            "Trading system architecture and work context", seed["project"]["summary"]
        )
        self.assertEqual(
            seed_input["project"]["working_rules"], seed["project"]["working_rules"]
        )
        document = next(
            item for item in seed["documents"] if item["document_id"] == "architecture"
        )
        self.assertEqual("System architecture", document["title"])
        self.assertEqual("SPECIFICATION", document["role"])
        self.assertEqual([], document["node_ids"])
        self.assertTrue(document["project_wide"])

    def test_gcs_project_seed_assets_separate_functional_and_implementation_graphs(
        self,
    ) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        seed_input = self.project_seed(seed_id="gcs-seed-assets-001")
        seed_input["implementation_nodes"] = [
            {
                "implementation_id": "broker-client-class",
                "kind": "class",
                "title": "BrokerClient",
                "refs": [
                    {
                        "path": "src/broker.py",
                        "sha256": self.digest(self.broker_source),
                        "kind": "source",
                        "symbol": "BrokerClient",
                    }
                ],
            }
        ]
        seed_input["implementation_bindings"] = [
            {
                "binding_id": "broker-client-implements",
                "functional_node_id": "broker-client",
                "implementation_node_id": "broker-client-class",
                "relation": "implements",
            }
        ]
        status, seeded = self.request(
            "POST", "/v1/projects/GCS/seed", seed_input, self.token
        )
        self.assertEqual(201, status)
        materialized = materialize_project_seed_assets(
            self.project_root, seeded["seed"]
        )
        self.assertEqual(".ai/universe", materialized["asset_root"])

        status, synced = self.request("POST", "/v1/projects/GCS/sync", {}, self.token)
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_SEED_ASSETS_SYNCED", synced["status"])
        projection = synced["projection"]
        self.assertEqual(
            "broker-client-class",
            projection["implementation"]["nodes"][0]["implementation_id"],
        )
        self.assertEqual(
            "IMPLEMENTS", projection["implementation_bindings"][0]["relation"]
        )

        status, template = self.request(
            "GET", "/v1/templates/project-seed", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual(".ai/universe", template["template"]["asset_root"])

    def test_project_seed_asset_proposal_is_exact_and_read_only(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        before = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        status, seeded = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(201, status)

        status, result = self.request(
            "GET", "/v1/projects/GCS/seed-asset-proposal", token=self.token
        )
        self.assertEqual(200, status)
        self.assertEqual("PROJECT_SEED_ASSET_PROPOSAL_READY", result["status"])
        proposal = result["proposal"]
        self.assertEqual("universe.project-seed-asset-proposal.v1", proposal["schema"])
        self.assertEqual(seeded["seed"]["seed_digest"], proposal["seed_digest"])
        self.assertEqual(".ai/universe", proposal["target_root"])
        self.assertEqual("NONE", proposal["effects"]["project_source_write"])
        self.assertEqual("PROPOSED", proposal["effects"]["project_runtime_state_write"])
        self.assertEqual(
            {
                ".ai/universe/manifest.json",
                ".ai/universe/functional-graph.json",
                ".ai/universe/implementation-graph.json",
                ".ai/universe/bindings.json",
                ".ai/universe/documents.json",
            },
            {asset["target_path"] for asset in proposal["assets"]},
        )
        self.assertTrue(all(len(asset["sha256"]) == 64 for asset in proposal["assets"]))
        self.assertTrue(all(asset["content_base64"] for asset in proposal["assets"]))
        self.assertFalse((self.project_root / ".ai" / "universe").exists())
        after = {
            path.relative_to(self.project_root).as_posix(): self.digest(path)
            for path in self.project_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_seed_asset_apply_routes_exact_approval_to_project_master(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request("POST", "/v1/projects/GCS/seed", self.project_seed(), self.token)
        _, prepared = self.request(
            "GET", "/v1/projects/GCS/seed-asset-proposal", token=self.token
        )
        proposal = prepared["proposal"]
        bridge = {
            "project_id": "GCS",
            "endpoint": "http://127.0.0.1:19091",
            "credential_env": "UNIVERSE_TEST_MASTER_TOKEN",
        }
        host_receipt = {
            "schema": "universe.project-master-seed-apply-delivery-receipt.v1",
            "status": "DELIVERED",
            "project_id": "GCS",
            "proposal_id": proposal["proposal_id"],
            "host_response": {
                "status": "PROJECT_SEED_ASSETS_APPLIED",
                "manifest_ref": ".ai/universe/manifest.json",
            },
        }

        with (
            patch.object(
                self.server,
                "ensure_project_master",
                return_value={"status": "EXISTING_BRIDGE"},
            ),
            patch.object(
                self.server.store,
                "get_master_bridge",
                return_value=bridge,
            ),
            patch(
                "universe_server.HttpProjectMasterBridge.apply_seed_assets",
                return_value=host_receipt,
            ) as apply_seed_assets,
        ):
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/seed-asset-proposal/apply",
                {
                    "approval": "APPROVED",
                    "proposal_id": proposal["proposal_id"],
                    "proposal_digest": proposal["proposal_digest"],
                },
                self.token,
            )

        self.assertEqual(200, status)
        self.assertEqual("PROJECT_SEED_ASSET_APPLICATION_DELIVERED", result["status"])
        self.assertEqual("APPROVED", result["approval"]["status"])
        self.assertEqual(proposal["proposal_id"], result["approval"]["proposal_id"])
        self.assertTrue(
            result["approval"]["evidence_ref"].startswith(
                "universe://projects/GCS/seed-asset-proposals/"
            )
        )
        apply_seed_assets.assert_called_once()

    def test_seed_asset_apply_rejects_stale_proposal_before_host_call(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request("POST", "/v1/projects/GCS/seed", self.project_seed(), self.token)

        with patch.object(self.server, "ensure_project_master") as ensure:
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/seed-asset-proposal/apply",
                {
                    "approval": "APPROVED",
                    "proposal_id": "seed_assets_stale",
                    "proposal_digest": "f" * 64,
                },
                self.token,
            )

        self.assertEqual(409, status)
        self.assertEqual("PROJECT_SEED_ASSET_APPROVAL_STALE", result["error_code"])
        ensure.assert_not_called()

    def test_integration_apply_routes_exact_approval_to_project_master(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        _, proposal = self.request(
            "GET",
            "/v1/projects/GCS/integration-template-proposal",
            token=self.token,
        )
        bridge = {
            "project_id": "GCS",
            "endpoint": "http://127.0.0.1:19091",
            "credential_env": "UNIVERSE_TEST_MASTER_TOKEN",
        }
        host_receipt = {
            "schema": "universe.project-master-integration-apply-delivery-receipt.v1",
            "status": "DELIVERED",
            "project_id": "GCS",
            "proposal_id": proposal["proposal_id"],
            "host_response": {"status": "PROJECT_INTEGRATION_APPLIED"},
        }

        with (
            patch.object(
                self.server,
                "ensure_project_master",
                return_value={"status": "EXISTING_BRIDGE"},
            ),
            patch.object(
                self.server.store,
                "get_master_bridge",
                return_value=bridge,
            ),
            patch(
                "universe_server.HttpProjectMasterBridge.apply_integration_assets",
                return_value=host_receipt,
            ) as apply_integration_assets,
        ):
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/integration-template-proposal/apply",
                {
                    "approval": "APPROVED",
                    "proposal_id": proposal["proposal_id"],
                    "proposal_digest": proposal["proposal_digest"],
                },
                self.token,
            )

        self.assertEqual(200, status)
        self.assertEqual("PROJECT_INTEGRATION_APPLICATION_DELIVERED", result["status"])
        self.assertEqual("APPROVED", result["approval"]["status"])
        self.assertEqual(
            result["approval"]["project_source_evidence_ref"],
            result["approval"]["local_runtime_evidence_ref"],
        )
        self.assertTrue(
            result["approval"]["project_source_evidence_ref"].startswith(
                "universe://projects/GCS/integration-template-proposals/"
            )
        )
        apply_integration_assets.assert_called_once()

    def test_integration_apply_rejects_stale_proposal_before_host_call(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)

        with patch.object(self.server, "ensure_project_master") as ensure:
            status, result = self.request(
                "POST",
                "/v1/projects/GCS/integration-template-proposal/apply",
                {
                    "approval": "APPROVED",
                    "proposal_id": "project_integration_stale",
                    "proposal_digest": "f" * 64,
                },
                self.token,
            )

        self.assertEqual(409, status)
        self.assertEqual("PROJECT_INTEGRATION_APPROVAL_STALE", result["error_code"])
        ensure.assert_not_called()

    def test_gcs_project_seed_discovery_dispatch_is_queued_before_project_write(
        self,
    ) -> None:
        # Migrated (2026-09-05) off project_dispatch onto the Master claim
        # queue - the response now carries a claimable "message", not a
        # one-shot "dispatch" needing a manual Deliver click.
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, result = self.request(
            "POST", "/v1/projects/GCS/discovery-dispatch", {}, self.token
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_DISCOVERY_DISPATCH_QUEUED", result["status"])
        message = result["message"]
        self.assertEqual("QUEUED", message["delivery_state"])
        self.assertEqual(
            "universe.project-discovery-dispatch.v1",
            message["metadata"]["expected_output"]["schema"],
        )
        self.assertFalse((self.project_root / ".ai" / "universe").exists())

        # Same idempotency_key + content on retry returns the same item
        # rather than creating a second one.
        retry_status, retried = self.request(
            "POST", "/v1/projects/GCS/discovery-dispatch", {}, self.token
        )
        self.assertEqual(200, retry_status)
        self.assertEqual("PROJECT_DISCOVERY_DISPATCH_ALREADY_QUEUED", retried["status"])
        self.assertEqual(message["message_id"], retried["message"]["message_id"])

        # It is claimable through the same route every other Master message
        # uses - this is the entire point of the migration.
        claim_status, claimed = self.request(
            "POST",
            "/v1/projects/GCS/master-messages/claim",
            {"provider": "CLAUDE"},
            self.token,
        )
        self.assertEqual(200, claim_status)
        self.assertEqual(message["message_id"], claimed["message"]["message_id"])
        self.assertEqual("PROCESSING", claimed["message"]["delivery_state"])

    def test_master_message_queue_http_lifecycle(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        create_status, created = self.request(
            "POST",
            "/v1/projects/GCS/master-messages",
            {
                "idempotency_key": "seed-v2",
                "title": "Prepare Universe project seed",
                "instruction": "Read the project and publish Seed assets.",
            },
            self.token,
        )
        self.assertEqual(201, create_status)
        self.assertEqual("MASTER_MESSAGE_QUEUED", created["status"])
        self.assertEqual("QUEUED", created["message"]["delivery_state"])

        # Retrying the same idempotency_key + content returns the same item.
        retry_status, retried = self.request(
            "POST",
            "/v1/projects/GCS/master-messages",
            {
                "idempotency_key": "seed-v2",
                "title": "Prepare Universe project seed",
                "instruction": "Read the project and publish Seed assets.",
            },
            self.token,
        )
        self.assertEqual(200, retry_status)
        self.assertEqual("MASTER_MESSAGE_ALREADY_QUEUED", retried["status"])
        self.assertEqual(
            created["message"]["message_id"], retried["message"]["message_id"]
        )

        list_status, listed = self.request(
            "GET", "/v1/projects/GCS/master-messages", None, self.token
        )
        self.assertEqual(200, list_status)
        self.assertEqual(1, len(listed["messages"]))

        claim_status, claimed = self.request(
            "POST",
            "/v1/projects/GCS/master-messages/claim",
            {"provider": "CLAUDE"},
            self.token,
        )
        self.assertEqual(200, claim_status)
        self.assertEqual("MASTER_MESSAGE_CLAIMED", claimed["status"])
        message_id = claimed["message"]["message_id"]
        self.assertEqual("PROCESSING", claimed["message"]["delivery_state"])

        empty_claim_status, empty_claim = self.request(
            "POST",
            "/v1/projects/GCS/master-messages/claim",
            {"provider": "CODEX"},
            self.token,
        )
        self.assertEqual(200, empty_claim_status)
        self.assertEqual("MASTER_MESSAGE_QUEUE_EMPTY", empty_claim["status"])
        self.assertIsNone(empty_claim["message"])

        complete_status, completed = self.request(
            "POST",
            f"/v1/master-messages/{message_id}/complete",
            {"provider": "CLAUDE", "result_ref": "artifact://seed/1"},
            self.token,
        )
        self.assertEqual(200, complete_status)
        self.assertEqual("MASTER_MESSAGE_COMPLETED", completed["status"])
        self.assertEqual("DONE", completed["message"]["delivery_state"])

    def test_master_message_fail_route_and_conflict_status(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        _, created = self.request(
            "POST",
            "/v1/projects/GCS/master-messages",
            {
                "idempotency_key": "seed-v2",
                "title": "Prepare Universe project seed",
                "instruction": "Read the project and publish Seed assets.",
            },
            self.token,
        )
        message_id = created["message"]["message_id"]
        fail_status, failed = self.request(
            "POST",
            f"/v1/master-messages/{message_id}/fail",
            {"code": "BLOCKED", "reason": "no live Master session"},
            self.token,
        )
        self.assertEqual(200, fail_status)
        self.assertEqual("MASTER_MESSAGE_FAILED", failed["status"])
        self.assertEqual("FAILED", failed["message"]["delivery_state"])

        conflict_status, conflict = self.request(
            "POST",
            f"/v1/master-messages/{message_id}/complete",
            {"provider": "CLAUDE"},
            self.token,
        )
        self.assertEqual(409, conflict_status)
        self.assertEqual("MASTER_MESSAGE_STATE_CONFLICT", conflict["error_code"])

    def test_master_message_renew_lease_route(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        self.request(
            "POST",
            "/v1/projects/GCS/master-messages",
            {
                "idempotency_key": "seed-v2",
                "title": "Prepare Universe project seed",
                "instruction": "Read the project and publish Seed assets.",
            },
            self.token,
        )
        _, claimed = self.request(
            "POST",
            "/v1/projects/GCS/master-messages/claim",
            {"provider": "CLAUDE"},
            self.token,
        )
        message_id = claimed["message"]["message_id"]
        renew_status, renewed = self.request(
            "POST", f"/v1/master-messages/{message_id}/renew-lease", {}, self.token
        )
        self.assertEqual(200, renew_status)
        self.assertEqual("MASTER_MESSAGE_LEASE_RENEWED", renewed["status"])
        self.assertGreaterEqual(
            renewed["message"]["lease_expires_at"],
            claimed["message"]["lease_expires_at"],
        )

    def test_wake_live_master_sessions_notifies_every_live_master_terminal(
        self,
    ) -> None:
        """The reason session_bus.post() can't be reused directly for this:
        with 2+ live terminals matching (project_id + mode, no specific
        anchor), resolve_direct_targets treats that as BUS_TARGET_AMBIGUOUS
        and refuses to send anything at all - exactly the case that matters
        once more than one live Master instance exists for a project, which
        is the entire point of this feature. _wake_live_master_sessions
        must reach every one of them instead of erroring out.
        """

        emitted: list[tuple[str, bytes]] = []

        class FakeMasterHost:
            def __init__(self, terminals: list[dict[str, Any]]) -> None:
                self._terminals = terminals

            def list_sessions(self) -> list[dict[str, Any]]:
                return self._terminals

            def get(self, terminal_id: str) -> dict[str, Any]:
                for terminal in self._terminals:
                    if terminal.get("terminal_id") == terminal_id:
                        return terminal
                raise TerminalHostError("TERMINAL_NOT_FOUND", "not found", 404)

            def emit_output(self, terminal_id: str, data: bytes) -> None:
                emitted.append((terminal_id, data))

        live_master_terminals = [
            {
                "terminal_id": "term-a",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "state": "LIVE",
                "active_session_anchor_ref": "session_anchor_a",
            },
            {
                "terminal_id": "term-b",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CODEX",
                "state": "LIVE",
                "active_session_anchor_ref": "session_anchor_b",
            },
        ]
        fake_host = FakeMasterHost(live_master_terminals)

        with patch.object(
            self.server, "_session_anchor_terminal_host", return_value=fake_host
        ):
            # Demonstrate the ambiguity session_bus.post() hits directly with
            # 2+ live matches and no pinned anchor/terminal_id.
            with self.assertRaises(SessionBusError) as ctx:
                self.server.session_bus.post(
                    fake_host,
                    {
                        "to": {"project_id": "GCS", "mode": "MASTER"},
                        "kind": "NOTE",
                        "body_text": "would be ambiguous",
                    },
                )
            self.assertEqual("BUS_TARGET_AMBIGUOUS", ctx.exception.code)

            woken = self.server._wake_live_master_sessions(
                "GCS", reason="new work queued"
            )

        self.assertEqual(2, woken)
        self.assertEqual({"term-a", "term-b"}, {tid for tid, _ in emitted})
        for terminal_id in ("term-a", "term-b"):
            inbox = self.server.session_bus.inbox(fake_host, terminal_id=terminal_id)
            messages = inbox.get("messages") or inbox.get("inbox") or []
            self.assertTrue(messages, f"expected an inbox message for {terminal_id}")
            self.assertEqual("INSTRUCTION", messages[-1]["kind"])
            self.assertIn("Master queue has work waiting", messages[-1]["body_text"])

    def test_project_seed_rejects_digest_mismatch_and_root_escape(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        invalid_digest = self.project_seed()
        invalid_digest["nodes"][0]["refs"][0]["sha256"] = "0" * 64
        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", invalid_digest, self.token
        )
        self.assertEqual(409, status)
        self.assertEqual("PROJECT_FILE_REF_DIGEST_MISMATCH", result["error_code"])

        escaping = self.project_seed()
        escaping["documents"][0]["path"] = "../outside.md"
        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", escaping, self.token
        )
        self.assertEqual(400, status)
        self.assertEqual("PROJECT_REF_INVALID", result["error_code"])

        raw_content = self.project_seed()
        raw_content["nodes"][0]["content"] = "class Injected: pass"
        status, result = self.request(
            "POST", "/v1/projects/GCS/seed", raw_content, self.token
        )
        self.assertEqual(400, status)
        self.assertEqual("REQUEST_INVALID", result["error_code"])

    def test_new_seed_invalidates_projection_until_rebuilt(self) -> None:
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, first = self.request(
            "POST", "/v1/projects/GCS/seed", self.project_seed(), self.token
        )
        self.assertEqual(201, status)
        self.request(
            "POST",
            "/v1/projects/GCS/projection",
            {"expected_seed_digest": first["seed"]["seed_digest"]},
            self.token,
        )

        updated = self.project_seed(
            seed_id="gcs-seed-002",
            source={
                "ref": "local-git:GCS@2222222222222222222222222222222222222222",
                "commit": "2222222222222222222222222222222222222222",
            },
        )
        status, second = self.request(
            "POST", "/v1/projects/GCS/seed", updated, self.token
        )
        self.assertEqual(201, status)
        self.assertNotEqual(first["seed"]["seed_digest"], second["seed"]["seed_digest"])
        status, result = self.request(
            "GET", "/v1/projects/GCS/projection", token=self.token
        )
        self.assertEqual(409, status)
        self.assertEqual("PROJECT_PROJECTION_REBUILD_REQUIRED", result["error_code"])

        status, result = self.request(
            "POST",
            "/v1/projects/GCS/projection",
            {"expected_seed_digest": first["seed"]["seed_digest"]},
            self.token,
        )
        self.assertEqual(409, status)
        self.assertEqual("PROJECT_SEED_DIGEST_MISMATCH", result["error_code"])

    def test_registration_rejects_ref_escape_and_owner_mismatch(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/projects/register",
            self.registration(refs={"manifest": "../outside.md"}),
            self.token,
        )
        self.assertEqual(400, status)
        self.assertEqual("PROJECT_REF_INVALID", result["error_code"])

        registry_path = (
            self.project_root
            / ".ai"
            / "runtime"
            / "project_instance"
            / "mode_registry.json"
        )
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["owner"] = "OTHER"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        status, result = self.request(
            "POST", "/v1/projects/register", self.registration(), self.token
        )
        self.assertEqual(409, status)
        self.assertEqual("PROJECT_IDENTITY_MISMATCH", result["error_code"])

    def test_non_loopback_binding_is_rejected(self) -> None:
        with self.assertRaisesRegex(UniverseError, "loopback"):
            create_server(
                database_path=Path(self.temp.name) / "forbidden.sqlite3",
                token="token",
                host="0.0.0.0",
            )

    def test_http_transport_revalidates_endpoint_and_request_path(self) -> None:
        profile = local_connection_profile(self.endpoint)
        invalid_profile = replace(profile, endpoint="file:///tmp/universe.json")
        transport = HttpUniverseTransport(
            invalid_profile,
            auth_provider_for(invalid_profile, self.token),
        )
        with self.assertRaisesRegex(UniverseError, "absolute HTTP or HTTPS"):
            transport.request_json(method="GET", path="/v1/projects")

        safe_profile = local_connection_profile(self.endpoint)
        safe_transport = HttpUniverseTransport(
            safe_profile,
            auth_provider_for(safe_profile, self.token),
        )
        with self.assertRaisesRegex(UniverseError, "absolute path"):
            safe_transport.request_json(method="GET", path="https://example.com")

    def test_universe_mode_contract_accepts_only_master_and_conductor(self) -> None:
        registry_path = Path(self.temp.name) / "universe-mode-registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema": "ai-career.mode-registry.v2",
                    "owner": "universe",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 3,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                        },
                        "CONDUCTOR": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        registry = load_universe_mode_registry(registry_path)
        self.assertEqual(["CONDUCTOR", "MASTER"], sorted(registry["modes"]))
        self.assertEqual("CONDUCTOR", registry["modes"]["CONDUCTOR"]["role"])
        self.assertEqual(
            {
                "schema": "universe.mode-contract.v1",
                "status": "ACTIVE",
                "mode": "CONDUCTOR",
                "role": "CONDUCTOR",
                "scope": "project-network/navigation/distribution",
                "registry_revision": 3,
            },
            universe_mode_contract(registry),
        )
        for intent in (
            "CONDUCTOR",
            "Conductor mode",
            "\ucee8\ub355\ud130",
            "\ucee8\ub355\ud130\ubaa8\ub4dc",
        ):
            with self.subTest(intent=intent):
                self.assertEqual("CONDUCTOR", resolve_universe_mode_intent(intent))
        for intent in (
            "UNIVERSE",
            "Universe mode",
            "\uc720\ub2c8\ubc84\uc2a4",
            "\uc720\ub2c8\ubc84\uc2a4\ubaa8\ub4dc",
        ):
            with self.subTest(intent=intent):
                with self.assertRaisesRegex(
                    UniverseError, "only Conductor Mode intent"
                ):
                    resolve_universe_mode_intent(intent)

        require_release_lifecycle_mode("MASTER")
        with self.assertRaisesRegex(UniverseError, "require MASTER Mode"):
            require_release_lifecycle_mode("UNIVERSE")

        registry["modes"]["UNIVERSE"] = {
            "role": "CONDUCTOR",
            "scope": "project-network/navigation/distribution",
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(UniverseError, "UNIVERSE"):
            load_universe_mode_registry(registry_path)

        del registry["modes"]["UNIVERSE"]
        registry["modes"]["CONDUCTOR"]["role"] = "NAVIGATOR"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(UniverseError, "CONDUCTOR"):
            load_universe_mode_registry(registry_path)


    def test_legacy_universe_mode_registry_ignores_mode_profile(self) -> None:
        registry_path = Path(self.temp.name) / "legacy-universe-mode-registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "schema": "ai-career.mode-registry.v1",
                    "owner": "universe",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 3,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "EXECUTABLE_PROOF_REQUIRED",
                        },
                        "CONDUCTOR": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                            "mode_profile": "not-a-live-authority-value",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        normalized = load_universe_mode_registry(registry_path)

        self.assertNotIn("mode_profile", normalized["modes"]["MASTER"])
        self.assertNotIn("mode_profile", normalized["modes"]["CONDUCTOR"])
        self.assertEqual(
            ["MODE_REGISTRY_LEGACY_FIELD_IGNORED"],
            universe_mode_contract(normalized)["notes"],
        )

    def test_remote_auth_types_are_reserved_without_runtime_implementation(
        self,
    ) -> None:
        profile = connection_profile(
            connection_id="personal-cloud",
            kind="REMOTE",
            transport_kind="GIT",
            endpoint="https://universe.example.com",
            auth_type="OAUTH2",
            credential_ref="os-keychain://universe/personal-cloud",
            capabilities=ConnectionCapabilities(
                read=True,
                append=True,
                realtime=False,
                bidirectional=True,
                durable=True,
            ),
        )
        self.assertEqual("REMOTE", profile.kind)
        self.assertEqual("GIT", profile.transport_kind)
        self.assertEqual("OAUTH2", profile.auth.auth_type)
        with self.assertRaisesRegex(UniverseError, "reserved but not implemented"):
            auth_provider_for(profile, "not-used")

    def test_local_connection_profile_requires_loopback(self) -> None:
        with self.assertRaisesRegex(UniverseError, "loopback"):
            connection_profile(
                connection_id="local",
                kind="LOCAL",
                transport_kind="HTTP",
                endpoint="https://universe.example.com",
                auth_type="LOCAL_TOKEN",
                credential_ref="server-state://token",
                capabilities=ConnectionCapabilities(
                    read=True,
                    append=True,
                    realtime=True,
                    bidirectional=True,
                    durable=True,
                ),
            )

    def test_mcp_is_an_interface_not_a_transport(self) -> None:
        profile = interface_profile(
            interface_id="mcp-server",
            kind="MCP",
            direction="INBOUND",
            active=False,
        )
        self.assertEqual("MCP", profile.kind)
        self.assertFalse(profile.active)

        with self.assertRaisesRegex(UniverseError, "transport kind"):
            connection_profile(
                connection_id="invalid-mcp-transport",
                kind="REMOTE",
                transport_kind="MCP",
                endpoint="https://universe.example.com",
                auth_type="OAUTH2",
                credential_ref="os-keychain://universe/invalid",
                capabilities=ConnectionCapabilities(
                    read=True,
                    append=True,
                    realtime=True,
                    bidirectional=True,
                    durable=True,
                ),
            )

    def test_reserved_p2p_transport_does_not_inherit_http_address_rules(self) -> None:
        profile = connection_profile(
            connection_id="peer-universe",
            kind="PEER",
            transport_kind="P2P",
            endpoint="peer://universe/example",
            auth_type="PEER_KEY",
            credential_ref="os-keychain://universe/peer/example",
            capabilities=ConnectionCapabilities(
                read=True,
                append=True,
                realtime=True,
                bidirectional=True,
                durable=False,
            ),
        )
        self.assertEqual("peer://universe/example", profile.endpoint)
        with self.assertRaisesRegex(UniverseError, "reserved but not implemented"):
            auth_provider_for(profile, "not-used")

    def test_runtime_worker_invocation_is_redacted_and_idempotent(self) -> None:
        class FakeRuntimeHost:
            def provider_capabilities(self) -> list[dict[str, str]]:
                return [{"provider": "GROK", "status": "AVAILABLE"}]

            def invoke_read_only(self, request: dict[str, object]) -> dict[str, object]:
                self.request = request
                return {
                    "status": "TASK_FRAME_RESULT_RECORDED",
                    "provider": "GROK",
                    "model_ref": "provider://GROK/model/grok-test",
                    "worker_id": "grok-worker-001",
                    "worker_run_ref": "worker-run-001",
                    "result_receipt_ref": "result-001",
                    "terminal_result_verified": True,
                    "task_frame_result_status": "TASK_FRAME_RESULT_RECORDED",
                    "skill_run_observation_count": 1,
                    "repository_write": False,
                    "result": {"text": "provider result must not persist"},
                }

        self.server.runtime_host = FakeRuntimeHost()
        status, _ = self.request("POST", "/v1/projects/register", self.registration())
        self.assertEqual(HTTPStatus.CREATED, status)
        status, providers = self.request("GET", "/v1/runtime/providers")
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("AVAILABLE", providers["providers"][0]["status"])
        payload = {
            "schema": "universe.runtime-worker-invocation-request.v1",
            "invocation_id": "runtime-worker-001",
            "provider": "GROK",
            "endpoint": "http://127.0.0.1:19090",
            "token": "never-store-this-token",
            "session_id": "session-001",
            "frame_id": "frame-001",
            "turn_id": "turn-001",
            "invoker_actor_ref": "universe-host",
            "repository_write_scope": "NONE",
            "mutation_scope": {"operations": [], "targets": []},
            "context_pack": {"prompt": "must not persist"},
            "output_contract": {"format": "review"},
            "max_turns": 1,
        }
        path = "/v1/projects/GCS/runtime-worker-invocations"
        status, created = self.request("POST", path, payload)
        self.assertEqual(HTTPStatus.CREATED, status)
        encoded = json.dumps(created, sort_keys=True)
        self.assertNotIn("never-store-this-token", encoded)
        self.assertNotIn("must not persist", encoded)
        self.assertNotIn("provider result must not persist", encoded)
        self.assertFalse(created["invocation"]["result"]["repository_write"])
        binding_snapshot = created["invocation"]["invocation"][
            "worker_binding_snapshot"
        ]
        self.assertEqual("DEFAULT_AUTO", binding_snapshot["profile_id"])
        self.assertEqual(
            binding_snapshot,
            self.server.runtime_host.request["context_pack"]["worker_binding_snapshot"],
        )
        self.assertEqual(
            binding_snapshot["binding_digest"],
            created["invocation"]["invocation"]["worker_binding_digest"],
        )
        self.assertEqual(
            "result-001",
            created["invocation"]["result"]["result_receipt_ref"],
        )
        self.assertEqual(
            1,
            created["invocation"]["result"]["skill_run_observation_count"],
        )
        terminal = created["invocation"]["result"]["terminal_evidence"]
        self.assertEqual(
            "universe.runtime-worker-terminal-evidence.v1", terminal["schema"]
        )
        self.assertEqual("frame-001", terminal["frame_id"])
        self.assertEqual("turn-001", terminal["turn_id"])
        self.assertEqual("worker-run-001", terminal["worker_run_ref"])
        self.assertEqual("result-001", terminal["result_receipt_ref"])
        self.assertRegex(terminal["evidence_digest"], r"^[0-9a-f]{64}$")
        status, repeated = self.request("POST", path, payload)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "RUNTIME_WORKER_INVOCATION_ALREADY_RECORDED", repeated["status"]
        )
        status, listed = self.request("GET", path)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(listed["invocations"]))

        result_path = (
            "/v1/projects/GCS/runtime-worker-results"
            "?frame_id=frame-001&turn_id=turn-001&worker_run_ref=worker-run-001"
        )
        status, results = self.request("GET", result_path)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual([terminal], results["results"])

        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            [terminal],
            reopened.list_runtime_worker_terminal_evidence(
                "GCS",
                frame_id="frame-001",
                turn_id="turn-001",
                worker_run_ref="worker-run-001",
            ),
        )
        self.assertEqual(
            [],
            reopened.list_runtime_worker_terminal_evidence(
                "GCS", worker_run_ref="worker-run-missing"
            ),
        )

    def test_runtime_worker_terminal_evidence_fails_closed_when_unverified(
        self,
    ) -> None:
        class FakeRuntimeHost:
            def provider_capabilities(self) -> list[dict[str, str]]:
                return [{"provider": "GROK", "status": "AVAILABLE"}]

            def invoke_read_only(self, _request: dict[str, object]) -> dict[str, object]:
                return {
                    "status": "TASK_FRAME_RESULT_RECORDED",
                    "provider": "GROK",
                    "worker_id": "grok-worker-unverified",
                    "result_receipt_ref": "result-unverified",
                    "terminal_result_verified": False,
                    "repository_write": False,
                }

        self.server.runtime_host = FakeRuntimeHost()
        status, _ = self.request("POST", "/v1/projects/register", self.registration())
        self.assertEqual(HTTPStatus.CREATED, status)
        status, created = self.request(
            "POST",
            "/v1/projects/GCS/runtime-worker-invocations",
            {
                "schema": "universe.runtime-worker-invocation-request.v1",
                "invocation_id": "runtime-worker-unverified",
                "provider": "GROK",
                "endpoint": "http://127.0.0.1:19090",
                "token": "never-store-this-token",
                "session_id": "session-unverified",
                "frame_id": "frame-unverified",
                "turn_id": "turn-unverified",
                "invoker_actor_ref": "universe-host",
                "repository_write_scope": "NONE",
                "mutation_scope": {"operations": [], "targets": []},
                "context_pack": {},
                "output_contract": {"format": "review"},
                "max_turns": 1,
            },
        )
        self.assertEqual(HTTPStatus.CREATED, status)
        result = created["invocation"]["result"]
        self.assertEqual(
            "RUNTIME_WORKER_TERMINAL_EVIDENCE_UNVERIFIED", result["status"]
        )
        self.assertFalse(result["terminal_result_verified"])
        self.assertEqual({}, result["terminal_evidence"])
        status, results = self.request(
            "GET", "/v1/projects/GCS/runtime-worker-results"
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual([], results["results"])


class RuntimeLeaseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = UniverseStore(self.root / "universe.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def register_project(
        self,
        project_id: str,
        *,
        origin: str = "PROJECT_STANDALONE",
        metadata: dict[str, Any] | None = None,
        membership: str = "LINKED",
    ) -> dict[str, Any]:
        project_root = self.root / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        (project_root / "REPOSITORY_MANIFEST.md").write_text(
            f"# {project_id}\n",
            encoding="utf-8",
        )
        project, created = self.store.register_project(
            {
                "project_id": project_id,
                "project_root": str(project_root),
                "metadata": metadata or {},
                "attachment": {
                    "install_origin": origin,
                    "universe_membership": membership,
                    "runtime_host": "PROJECT_LOCAL",
                },
            }
        )
        self.assertTrue(created)
        return project

    @staticmethod
    def activate_request(**overrides: Any) -> dict[str, Any]:
        request: dict[str, Any] = {
            "runtime_host_profile": "UNIVERSE_SHARED_DEFAULT",
            "selected_release_id": None,
            "ttl_seconds": 300,
        }
        request.update(overrides)
        return request

    def test_activate_inspect_list_and_renew_shared_runtime_lease(self) -> None:
        project = self.register_project("LEASE-HAPPY")
        activated = self.store.activate_shared_runtime_lease(
            project["project_id"],
            self.activate_request(),
            now="2026-08-07T00:00:00Z",
        )
        lease = activated["lease"]
        self.assertEqual("RUNTIME_LEASE_ACTIVATED", activated["status"])
        self.assertEqual("ACTIVE", lease["status"])
        self.assertEqual(self.store.identity()["universe_id"], lease["universe_id"])
        self.assertEqual("LEASE-HAPPY", lease["project_id"])
        self.assertIsNone(lease["selected_release_id"])
        self.assertNotIn("source_write", lease)
        self.assertEqual(
            "MANAGED", activated["project"]["attachment"]["universe_membership"]
        )
        self.assertEqual(
            "UNIVERSE_SHARED", activated["project"]["attachment"]["runtime_host"]
        )

        inspected = self.store.inspect_shared_runtime_lease(
            "LEASE-HAPPY", now="2026-08-07T00:01:00Z"
        )
        self.assertEqual(lease["lease_id"], inspected["lease"]["lease_id"])
        self.assertEqual("SHARED_RUNTIME_ACTIVE", inspected["fallback"]["status"])
        listed = self.store.list_shared_runtime_leases(
            "LEASE-HAPPY", now="2026-08-07T00:01:00Z"
        )
        self.assertEqual(
            [lease["lease_id"]], [item["lease_id"] for item in listed["leases"]]
        )

        renewed = self.store.renew_shared_runtime_lease(
            "LEASE-HAPPY",
            {"lease_id": lease["lease_id"], "ttl_seconds": 600},
            now="2026-08-07T00:02:00Z",
        )
        self.assertEqual("RUNTIME_LEASE_RENEWED", renewed["status"])
        self.assertEqual("2026-08-07T00:12:00Z", renewed["lease"]["expires_at"])

    def test_release_and_unhealthy_use_explicit_standalone_fallback(self) -> None:
        project = self.register_project("LEASE-STANDALONE")
        activated = self.store.activate_shared_runtime_lease(
            project["project_id"],
            self.activate_request(),
            now="2026-08-07T00:00:00Z",
        )
        released = self.store.release_shared_runtime_lease(
            project["project_id"],
            {"lease_id": activated["lease"]["lease_id"]},
            now="2026-08-07T00:01:00Z",
        )
        self.assertEqual("RELEASED", released["lease"]["status"])
        self.assertEqual("AVAILABLE", released["fallback"]["status"])
        self.assertEqual("PROJECT_LOCAL", released["fallback"]["runtime_host"])
        self.assertEqual(
            "LINKED", released["project"]["attachment"]["universe_membership"]
        )
        self.assertEqual(
            "PROJECT_LOCAL", released["project"]["attachment"]["runtime_host"]
        )

        declared = self.register_project(
            "LEASE-DECLARED",
            origin="UNIVERSE_CREATED",
            metadata={"local_fallback": {"declared": True}},
        )
        managed = self.store.activate_shared_runtime_lease(
            declared["project_id"],
            self.activate_request(),
            now="2026-08-07T00:00:00Z",
        )
        unhealthy = self.store.health_check_shared_runtime_lease(
            declared["project_id"],
            {
                "lease_id": managed["lease"]["lease_id"],
                "health_status": "UNHEALTHY",
            },
            now="2026-08-07T00:01:00Z",
        )
        self.assertEqual("UNHEALTHY", unhealthy["lease"]["status"])
        self.assertEqual("AVAILABLE", unhealthy["fallback"]["status"])
        self.assertEqual(
            "PROJECT_LOCAL", unhealthy["project"]["attachment"]["runtime_host"]
        )

    def test_expiry_releases_to_linked_and_blocks_renewal(self) -> None:
        project = self.register_project("LEASE-EXPIRY")
        activated = self.store.activate_shared_runtime_lease(
            project["project_id"],
            self.activate_request(ttl_seconds=60),
            now="2026-08-07T00:00:00Z",
        )
        expired = self.store.inspect_shared_runtime_lease(
            project["project_id"], now="2026-08-07T00:01:01Z"
        )
        self.assertEqual("EXPIRED", expired["lease"]["status"])
        self.assertEqual(
            "LINKED", expired["project"]["attachment"]["universe_membership"]
        )
        self.assertEqual("AVAILABLE", expired["fallback"]["status"])
        with self.assertRaises(UniverseError) as context:
            self.store.renew_shared_runtime_lease(
                project["project_id"],
                {"lease_id": activated["lease"]["lease_id"]},
                now="2026-08-07T00:02:00Z",
            )
        self.assertEqual("RUNTIME_LEASE_NOT_ACTIVE", context.exception.code)

    def test_universe_created_without_fallback_reports_unavailable(self) -> None:
        project = self.register_project("LEASE-NO-FALLBACK", origin="UNIVERSE_CREATED")
        activated = self.store.activate_shared_runtime_lease(
            project["project_id"],
            self.activate_request(),
            now="2026-08-07T00:00:00Z",
        )
        unhealthy = self.store.health_check_shared_runtime_lease(
            project["project_id"],
            {
                "lease_id": activated["lease"]["lease_id"],
                "health_status": "UNHEALTHY",
            },
            now="2026-08-07T00:01:00Z",
        )
        self.assertEqual("UNAVAILABLE", unhealthy["fallback"]["status"])
        self.assertEqual("LOCAL_FALLBACK_NOT_DECLARED", unhealthy["fallback"]["reason"])
        self.assertEqual(
            "LINKED", unhealthy["project"]["attachment"]["universe_membership"]
        )
        self.assertEqual(
            "UNIVERSE_SHARED", unhealthy["project"]["attachment"]["runtime_host"]
        )

    def test_invalid_attachment_transitions_and_lease_inputs_are_rejected(self) -> None:
        detached = self.register_project(
            "LEASE-DETACHED",
            membership="DETACHED",
        )
        with self.assertRaises(UniverseError) as context:
            self.store.activate_shared_runtime_lease(
                detached["project_id"],
                self.activate_request(),
            )
        self.assertEqual(
            "RUNTIME_LEASE_ATTACHMENT_TRANSITION_INVALID", context.exception.code
        )

        linked = self.register_project("LEASE-INVALID")
        with self.assertRaises(UniverseError) as context:
            self.store.activate_shared_runtime_lease(
                linked["project_id"],
                self.activate_request(selected_release_id="missing-release"),
            )
        self.assertEqual("RELEASE_NOT_FOUND", context.exception.code)

        activated = self.store.activate_shared_runtime_lease(
            linked["project_id"], self.activate_request()
        )
        with self.assertRaises(UniverseError) as context:
            self.store.register_project(
                {
                    "project_id": linked["project_id"],
                    "project_root": linked["project_root"],
                    "metadata": linked["metadata"],
                    "attachment": {
                        "install_origin": "PROJECT_STANDALONE",
                        "universe_membership": "LINKED",
                        "runtime_host": "PROJECT_LOCAL",
                    },
                }
            )
        self.assertEqual(
            "RUNTIME_LEASE_ACTIVE_ATTACHMENT_MUTATION", context.exception.code
        )
        with self.assertRaises(UniverseError) as context:
            self.store.activate_shared_runtime_lease(
                linked["project_id"], self.activate_request()
            )
        self.assertEqual("RUNTIME_LEASE_ALREADY_ACTIVE", context.exception.code)
        with self.assertRaises(UniverseError) as context:
            self.store.health_check_shared_runtime_lease(
                linked["project_id"],
                {
                    "lease_id": activated["lease"]["lease_id"],
                    "health_status": "UNKNOWN",
                },
            )
        self.assertEqual("RUNTIME_LEASE_HEALTH_INVALID", context.exception.code)
        with self.assertRaises(UniverseError) as context:
            self.store.delete_project(linked["project_id"])
        self.assertEqual("RUNTIME_LEASE_RELEASE_REQUIRED", context.exception.code)

        with self.assertRaises(UniverseError) as context:
            self.register_project(
                "LEASE-MANAGED-WITHOUT-LEASE",
                membership="MANAGED",
            )
        self.assertEqual("RUNTIME_LEASE_REQUIRED", context.exception.code)


class UniverseDefaultServerBootTests(unittest.TestCase):
    def test_default_server_does_not_prepare_conductor_from_stale_session(
        self,
    ) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            stale_session = root / "conductor-mode-session.sqlite"
            stale_session.write_bytes(b"not a sqlite database")
            with (
                patch("universe_server.ResidentModeSessionHost") as session_host,
                patch("universe_server.UniverseConductorRuntime") as runtime_cls,
            ):
                server = create_server(
                    database_path=root / "universe.sqlite3",
                    token="boot-token",
                    auto_start_project_masters=False,
                    host_profile=HostProfileStore(root / "host.json"),
                    service_state_path=root / "server.json",
                    remote_gateway_state_path=root / "remote-gateway.json",
                    remote_connector_state_path=root / "remote-connector.json",
                    remote_connector_config_path=root / "remote-connector-config.json",
                )
            try:
                session_host.assert_not_called()
                runtime_cls.assert_not_called()
                self.assertIsNone(server.conductor_session_host)
                self.assertIsNone(server.conductor_runtime)
                self.assertIsNone(server._planning_binding)
                self.assertEqual(
                    "UNBOUND",
                    server.planning_binding_status()["status"],
                )
                self.assertEqual(b"not a sqlite database", stale_session.read_bytes())
            finally:
                server.server_close()
        finally:
            temp.cleanup()

    def test_conductor_worker_pool_spawns_n_workers_and_shuts_down_cleanly(
        self,
    ) -> None:
        """The generalization for N concurrent Conductor instances: this
        in-process automation loop's own queue.Queue is already safe for any
        number of consumer threads calling .get() concurrently, so 'N
        workers' is just N threads running the existing
        _conductor_worker_loop target - no change to that loop or to
        claim_conductor_room_message's own CAS, both already correct under
        concurrent callers."""

        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            server = create_server(
                database_path=root / "universe.sqlite3",
                token="pool-token",
                auto_start_project_masters=False,
                host_profile=HostProfileStore(root / "host.json"),
                service_state_path=root / "server.json",
                remote_gateway_state_path=root / "remote-gateway.json",
                remote_connector_state_path=root / "remote-connector.json",
                remote_connector_config_path=root / "remote-connector-config.json",
                conductor_worker_pool_size=5,
            )
            try:
                self.assertEqual(5, len(server._conductor_workers))
                self.assertEqual(
                    5, len({worker.name for worker in server._conductor_workers})
                )
                self.assertTrue(
                    all(worker.is_alive() for worker in server._conductor_workers)
                )
            finally:
                # One None sentinel must reach every worker, not just one -
                # this is the regression this test guards: a leftover
                # single put(None) would leave N-1 workers blocked forever
                # on their own queue.get().
                server.server_close()
            self.assertTrue(
                all(not worker.is_alive() for worker in server._conductor_workers)
            )
        finally:
            temp.cleanup()

    def test_conductor_worker_pool_size_clamps_below_one_to_one(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            server = create_server(
                database_path=root / "universe.sqlite3",
                token="pool-clamp-token",
                auto_start_project_masters=False,
                host_profile=HostProfileStore(root / "host.json"),
                service_state_path=root / "server.json",
                remote_gateway_state_path=root / "remote-gateway.json",
                remote_connector_state_path=root / "remote-connector.json",
                remote_connector_config_path=root / "remote-connector-config.json",
                conductor_worker_pool_size=0,
            )
            try:
                self.assertEqual(1, len(server._conductor_workers))
            finally:
                server.server_close()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
