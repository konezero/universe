from __future__ import annotations

import hashlib
import json
import os
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
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import ANY, Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

JsonObject = dict[str, Any]


from core_release import build_release  # noqa: E402
from host_profile import HostProfileStore  # noqa: E402
from project_master_host import (  # noqa: E402
    ProjectMasterHostError,
    ProjectTaskProposalAdapter,
)
from project_seed_assets import materialize_project_seed_assets  # noqa: E402
from universe_server import (  # noqa: E402
    ConductorPermissionBridge,
    ConnectionCapabilities,
    HttpUniverseTransport,
    UniverseError,
    UniverseStore,
    _vendor_chat_key,
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
        with self.assertRaisesRegex(UniverseError, "both origin and target"):
            normalize_conductor_delegation(
                {
                    "project_id": "GCS",
                    "summary": "Invalid unscoped delegation.",
                    "idempotency_key": "cross-session-missing",
                }
            )
        with self.assertRaisesRegex(UniverseError, "both origin and target"):
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
                    "schema": "ai-career.mode-registry.v1",
                    "owner": "GCS",
                    "repository_kind": "PROJECT",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 1,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
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
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                        "CONDUCTOR": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                    },
                }
            ),
            host_profile=HostProfileStore(temp_root / "host.json"),
            service_state_path=temp_root / "server.json",
            remote_gateway_state_path=temp_root / "remote-gateway.json",
            remote_connector_state_path=temp_root / "remote-connector.json",
            remote_connector_config_path=temp_root / "remote-connector-config.json",
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
        self, *, scope: JsonObject | None = None
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
            "request_ref": "universe://project-room/messages/request-001",
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
        self.assertEqual(
            {
                "schema": "universe.mode-contract.v1",
                "status": "ACTIVE",
                "mode": "CONDUCTOR",
                "role": "CONDUCTOR",
                "scope": "project-network/navigation/distribution",
                "mode_profile": "GOVERNANCE_ONLY",
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
        status, started = self.request(
            "POST", f"/v1/todos/{todo_id}/actions", started_action
        )
        self.assertEqual(200, status)
        self.assertEqual("TODO_ACTION_APPLIED", started["status"])
        self.assertEqual("IN_PROGRESS", started["todo"]["state"])
        self.assertFalse(started["task_frame_created"])
        self.assertFalse(started["execution_assignment_created"])

        status, repeated = self.request(
            "POST", f"/v1/todos/{todo_id}/actions", started_action
        )
        self.assertEqual(200, status)
        self.assertEqual("TODO_ACTION_ALREADY_APPLIED", repeated["status"])

        completed_action = {
            "action_id": "validation-run-001",
            "outcome": "COMPLETED",
            "source": "TEST_HOOK",
            "evidence_ref": "test-run://validation-run-001",
        }
        status, blocked = self.request(
            "POST", f"/v1/todos/{todo_id}/actions", completed_action
        )
        self.assertEqual(409, status)
        self.assertEqual(
            "TODO_COMPLETION_VALIDATION_REQUIRED", blocked["error_code"]
        )

        completed_action["validation"] = {
            "status": "PASSED",
            "evidence_ref": "test-run://validation-run-001/passed",
        }
        status, completed = self.request(
            "POST", f"/v1/todos/{todo_id}/actions", completed_action
        )
        self.assertEqual(200, status)
        self.assertEqual("DONE", completed["todo"]["state"])
        self.assertIn("result_fanout", completed)

        status, todos = self.request("GET", "/v1/todos")
        self.assertEqual(200, status)
        retained = next(item for item in todos["todos"] if item["todo_id"] == todo_id)
        self.assertEqual("DONE", retained["state"])

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

        bound = [
            room for room in catalog["rooms"] if room["binding"]["state"] == "BOUND"
        ]
        self.assertEqual(3, len(bound))
        self.assertEqual(
            {"CODEX", "CLAUDE", "GROK"}, {room["provider"] for room in bound}
        )
        self.assertTrue(
            all(
                room["binding"]["selection_scope"] == "UI_NAVIGATION_ONLY"
                for room in bound
            )
        )
        self.assertTrue(
            all(room["binding"]["session_anchor_ref"] for room in bound)
        )
        grok = next(room for room in catalog["rooms"] if room["provider"] == "GROK")
        self.assertEqual("BOUND", grok["binding"]["state"])

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
        self.server.terminal_host.push_channel = Mock(
            return_value={"status": "QUEUED", "message_id": "msg-channel-001"}
        )
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

    def test_live_ui_instruction_dispatches_without_waiting_for_next_session_start(self) -> None:
        terminal = {
            "terminal_id": "term-live-session-hook-001",
            "project_id": "GCS",
            "mode": "MASTER",
            "provider": "CLAUDE",
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
                "Reply with exactly: HOOK_DISPATCH_CONFIRMED",
                value["body"],
            )
            self.assertTrue(
                str(value["idempotency_key"]).startswith("session-bus:msg_")
            )
            if on_accepted is not None:
                on_accepted({"message_id": "provider-reply-001"})
            return {
                "status": "PROVIDER_SESSION_INPUT_ACCEPTED",
                "message": {"message_id": "provider-user-001"},
                "reply": {"message_id": "provider-reply-001"},
            }

        self.server.provider_sessions.submit = Mock(side_effect=accept_native_turn)
        self.server.session_supervisor.register_session(
            {
                "session_id": "supervisor-live-session-hook-001",
                "node": "GCS",
                "project_id": "GCS",
                "mode": "MASTER",
                "provider": "CLAUDE",
                "provider_session_ref": "claude-code:live-turn-001",
                "session_anchor_ref": "session-anchor-live-hook-001",
                "state": "LIVE",
                "currentness": "CURRENT",
            }
        )

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
        self.server.provider_sessions.submit.assert_called_once()
        self.server.terminal_host.write.assert_not_called()

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
        transport.deliver.assert_called_once_with(record)
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

    def test_project_anchor_sessions_list_current_and_beyond_ing_from_store(self) -> None:
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
            "MASTER-CURRENT-OLD-ING",
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
        self.assertNotIn("UNIVERSE-MASTER-READY-001", by_id)
        self.assertNotIn("UNIVERSE-MASTER-STOPPED-001", by_id)
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

        with patch.object(
            self.server.store,
            "discover_provider_session_sources",
            side_effect=discovered,
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
            self.assertIn('id="universe-provider-setting"', body)
            self.assertIn('id="project-provider-settings"', body)
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
            self.assertIn('id="legacy-executor-list"', body)
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
            self.assertIn("/provider-setting", script)
            self.assertIn("/master-session/prepare", script)
            self.assertIn("/v1/conductor-session/prepare", script)
            self.assertIn("prepareBody.project_id", script)
            self.assertIn("prepareBody.cwd", script)
            self.assertIn("prepareBody.requested_mode", script)
            self.assertIn('state.modeContract?.mode === "CONDUCTOR"', script)
            self.assertIn("sessionConnectionText", script)
            self.assertIn("/v1/supervisor/sessions", script)
            self.assertIn("refreshSupervisorSessions", script)
            self.assertIn("/v1/supervisor/legacy-executors", script)
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
        self.assertEqual("sonnet", switched["setting"]["model_ref"])

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
            resume_session_ref="vendor-thread-123",
            cols=120,
            rows=32,
        )

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

    def test_attach_current_codex_thread_as_default_conductor_session(self) -> None:
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

        status, moved = attach_supervisor_session(
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
            attached["session"]["session_id"], moved["session"]["session_id"]
        )
        self.assertEqual("universe", moved["session"]["current_project_id"])
        self.assertEqual("MASTER", moved["session"]["mode"])
        self.assertEqual("Universe Main Master", moved["session"]["alias"])
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
            ) -> dict[str, object]:
                self.calls.append(
                    {
                        "binding": runtime_binding,
                        "message": message,
                        "history": history,
                        "provider": provider,
                    }
                )
                return {
                    "status": "TURN_COMPLETED",
                    "provider": provider,
                    "worker_id": "grok-cli:conductor-001",
                    "result_receipt_ref": "grok-cli:conductor-001:result-001",
                    "repository_write": False,
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
        self.assertTrue(self.server._conductor_worker.is_alive())

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
        class FakeParticipantHosts:
            def __init__(self) -> None:
                self.ensure_calls: list[dict[str, Any]] = []
                self.submitted: list[dict[str, Any]] = []
                self.stopped: list[str] = []

            def ensure(self, **values):
                self.ensure_calls.append(dict(values))
                return {
                    "status": "STARTED",
                    "provider": values["binding"]["provider"],
                    "provider_session_ref": values["binding"][
                        "provider_session_ref"
                    ],
                }

            def submit(self, binding, event) -> bool:
                self.submitted.append(
                    {"binding": dict(binding), "event": dict(event)}
                )
                return True

            def stop(self, binding_id: str) -> bool:
                self.stopped.append(binding_id)
                return True

            def close(self) -> None:
                return

        self.request("POST", "/v1/projects/register", self.registration(), self.token)
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
                "display_name": "Claude reviewer",
            },
        )["binding"]
        fake_hosts = FakeParticipantHosts()
        self.server.room_participant_hosts.close()
        self.server.room_participant_hosts = fake_hosts

        status, connected = self.request(
            "POST",
            f"/v1/rooms/{room['room_id']}/bindings/{binding['binding_id']}/control",
            {"action": "CONNECT"},
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("ROOM_PARTICIPANT_CONTROL_CONNECTED", connected["status"])
        self.assertEqual(self.project_root, fake_hosts.ensure_calls[0]["repository_root"])
        self.assertEqual("GCS", fake_hosts.ensure_calls[0]["node"])
        self.assertEqual("MASTER", fake_hosts.ensure_calls[0]["mode"])
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
        self.assertEqual(1, len(fake_hosts.submitted))
        self.assertEqual(
            posted["message"]["room_event_id"],
            fake_hosts.submitted[0]["event"]["room_event_id"],
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
        self.assertEqual([binding["binding_id"]], fake_hosts.stopped)
        self.assertEqual(
            "DISCONNECTED",
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
        self.server.room_participant_hosts.close()
        self.server.room_participant_hosts = participant_hosts
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
        run_forwarded = (
            client.run_instruction_authorized_task_frame.call_args.kwargs
        )
        self.assertNotIn("approval_evidence_ref", run_forwarded)

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
        self.assertTrue(is_governance_approval_command(" approve "))
        self.assertTrue(
            is_governance_approval_command("approve task_proposal_test_001")
        )
        self.assertFalse(is_governance_approval_command("승인해줘"))

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
            {"SPECIFICATION", "DESIGN", "ARCHITECTURE", "DECISION", "CONTRACT"},
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
        self.request("POST", "/v1/projects/register", self.registration(), self.token)
        status, result = self.request(
            "POST", "/v1/projects/GCS/discovery-dispatch", {}, self.token
        )
        self.assertEqual(201, status)
        self.assertEqual("PROJECT_DISCOVERY_DISPATCH_QUEUED", result["status"])
        dispatch = result["dispatch"]
        self.assertEqual("QUEUED", dispatch["status"])
        self.assertEqual(
            "universe.project-discovery-dispatch.v1",
            dispatch["expected_output"]["schema"],
        )
        self.assertFalse((self.project_root / ".ai" / "universe").exists())

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
                    "owner": "universe",
                    "policy": "MASTER_MANAGED",
                    "root_mode": "MASTER",
                    "revision": 3,
                    "modes": {
                        "MASTER": {
                            "role": "MASTER",
                            "scope": "architecture/governance",
                            "mode_profile": "GOVERNANCE_ONLY",
                        },
                        "CONDUCTOR": {
                            "role": "CONDUCTOR",
                            "scope": "project-network/navigation/distribution",
                            "mode_profile": "GOVERNANCE_ONLY",
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
                "mode_profile": "GOVERNANCE_ONLY",
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
            "mode_profile": "GOVERNANCE_ONLY",
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(UniverseError, "UNIVERSE"):
            load_universe_mode_registry(registry_path)

        del registry["modes"]["UNIVERSE"]
        registry["modes"]["CONDUCTOR"]["role"] = "NAVIGATOR"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(UniverseError, "CONDUCTOR"):
            load_universe_mode_registry(registry_path)

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


if __name__ == "__main__":
    unittest.main()
