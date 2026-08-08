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
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

JsonObject = dict[str, Any]


from core_release import build_release  # noqa: E402
from host_profile import HostProfileStore  # noqa: E402
from project_master_host import ProjectTaskProposalAdapter  # noqa: E402
from project_seed_assets import materialize_project_seed_assets  # noqa: E402
from universe_server import (  # noqa: E402
    ConductorPermissionBridge,
    ConnectionCapabilities,
    HttpUniverseTransport,
    UniverseError,
    UniverseStore,
    attach_supervisor_session,
    load_server_state,
    load_universe_mode_registry,
    auth_provider_for,
    connection_profile,
    create_server,
    interface_profile,
    local_connection_profile,
    normalize_conductor_ui_action,
    normalize_planning_runtime_binding,
    normalize_project_attachment,
    normalize_skill_observation_candidate,
    provider_ref_from_model_ref,
    publish_skill_observation,
    prepare_skill_observation_archive,
    require_release_lifecycle_mode,
    resolve_universe_mode_intent,
    universe_mode_contract,
    write_server_state,
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
                        "UNIVERSE": {
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
            patch(
                "project_release_apply.resolve_host_tool",
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

    def create_task_proposal_fixture(self) -> JsonObject:
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
            "scope": {"operations": ["MODIFY"], "roots": ["tools", "tests"]},
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
                    completed_at TEXT
                );
                """
            )
            connection.execute(
                """
                INSERT INTO proposal(
                    proposal_id, proposal_digest, proposal_json, state,
                    created_at, approved_at, approval_json, completed_at
                ) VALUES (?, ?, ?, 'PROPOSED', ?, NULL, NULL, NULL)
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
        evidence_store = self.server.runtime_host.worker_dispatcher.failure_evidence_store
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
            ["Add boundary test", "Confirm broker contract", "Review nightly seed extraction"],
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

    def test_provider_session_observer_api_projects_redacted_activity(self) -> None:
        rollout = self.temp_root / "rollout-observer.jsonl"
        rollout.write_text(
            json.dumps({"type": "tool_call", "command": "private command"})
            + "\n",
            encoding="utf-8",
        )
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
        self.assertEqual(1, scanned["added"])

        status, activities = self.request(
            "GET", f"/v1/session-observer/activities?source_id={source_id}"
        )
        self.assertEqual(200, status)
        self.assertEqual("TOOL_PHASE", activities["activities"][0]["event_kind"])
        self.assertNotIn("private command", json.dumps(activities))
        status, batch = self.request(
            "GET", f"/v1/session-observer/sources/{source_id}/batch-candidate"
        )
        self.assertEqual(200, status)
        self.assertEqual("ACTIVITY_BATCH_EMPTY", batch["candidate"]["status"])
        status, remote_sources = self.request(
            "GET",
            "/v1/session-observer/sources",
            extra_headers={"X-Universe-Access-Surface": "REMOTE_BROWSER"},
        )
        self.assertEqual(200, status)
        self.assertEqual("REDACTED", remote_sources["sources"][0]["source_path"])
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
            self.assertEqual("PROVIDER_SESSION_SOURCES_DISCOVERED", discovered["status"])
            self.assertEqual([discovered_source], discovered["sources"])
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
            self.assertIn('state.modeContract?.mode === "CONDUCTOR"', script)
            self.assertIn("sessionConnectionText", script)
            self.assertIn("/v1/supervisor/sessions", script)
            self.assertIn("refreshSupervisorSessions", script)
            self.assertIn("/v1/supervisor/legacy-executors", script)
            self.assertNotIn(self.token, script)

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
            patch("universe_server.connector_status", return_value={"status": "OFFLINE"}),
            patch("universe_server.start_gateway", return_value=gateway) as start_gateway,
            patch("universe_server.start_connector", return_value=connector) as start_connector,
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
                        "status": "UNAVAILABLE",
                        "reason": "CODEX_CLI_LAUNCH_FAILED",
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
        self.assertEqual("GROK", defaults["universe_conductor"]["resolved_provider"])
        self.assertEqual(
            "UNAVAILABLE",
            defaults["universe_conductor"]["session_connection"][
                "connection_state"
            ],
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

        status, universe = self.request(
            "POST",
            "/v1/settings/providers/universe",
            {"provider": "CODEX"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("CODEX", universe["setting"]["provider"])
        status, project = self.request(
            "POST",
            "/v1/projects/GCS/provider-setting",
            {"provider": "GROK"},
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("GROK", project["setting"]["provider"])

        reopened = UniverseStore(self.server.store.database_path)
        self.assertEqual(
            "CODEX",
            reopened.provider_setting(
                "UNIVERSE_CONDUCTOR",
                "CONDUCTOR",
            )["provider"],
        )
        self.assertEqual(
            "GROK",
            reopened.provider_setting("PROJECT_MASTER", "GCS")["provider"],
        )

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
        self.assertEqual("thread-current-codex", selected["provider_session_ref"])
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
        self.assertEqual(
            "SUPERVISOR_CONTROL_TOKEN_REQUIRED", denied["error_code"]
        )

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

    def test_service_shutdown_endpoint_requires_auth_and_requests_clean_stop(self) -> None:
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
            store.provider_setting("UNIVERSE_CONDUCTOR", "CONDUCTOR")[
                "provider"
            ],
        )
        updated = store.set_provider_setting(
            "UNIVERSE_CONDUCTOR",
            "CONDUCTOR",
            {"provider": "CLAUDE"},
        )
        self.assertEqual("CLAUDE", updated["provider"])

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
            switched = first.set_universe_provider_setting({"provider": "CODEX"})[
                "session_connection"
            ]
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
        self.assertTrue(all(instance.stopped for instance in FakeConductorRuntime.instances))

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
        }
        normalized = normalize_planning_runtime_binding(binding)
        self.assertEqual("UNKNOWN", normalized["runtime_currentness_observation"])
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

            def close(self) -> None:
                return

        self.server.runtime_host = CapabilityHost()
        session_host = SessionHost()
        self.server.conductor_session_host = session_host
        status, _bound = self.request(
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
        self.assertEqual(HTTPStatus.OK, status)
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

        reply = next(
            item for item in messages if item.get("in_reply_to") == message_id
        )
        self.assertEqual("Conductor response", reply["body"])
        self.assertEqual(
            "grok-acp:conductor-session-1",
            reply["result_receipt_ref"],
        )
        self.assertEqual(
            "CONDUCTOR",
            session_host.calls[0][1]["runtime_context"]["requested_mode"],
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
        self.assertEqual(
            "Orbit Notes", reply["ui_action"]["intent"]["project"]
        )
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
        self.assertEqual(1, len(result["projects"]))
        self.assertEqual("Trading", result["projects"][0]["metadata"]["label"])

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

    def test_registration_exposes_attachment_and_preserves_origin_on_refresh(self) -> None:
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
        self.assertEqual("UNIVERSE_CREATED", refreshed["project"]["attachment"]["install_origin"])
        self.assertEqual("GCS", refreshed["project"]["project_id"])
        self.assertEqual(original_refs, refreshed["project"]["refs"])
        self.assertEqual({"label": "Trading Updated"}, refreshed["project"]["metadata"])
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
        self.assertEqual(metadata, project["metadata"])
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
        self.assertEqual("DELIVERED_TO_MASTER", message["delivery_state"])
        self.assertEqual("DELIVERED", message["delivery"]["status"])
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
            "/v1/governance-proposals",
            token=self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual("GOVERNANCE_PROPOSAL_INBOX_COLLECTED", inbox["status"])
        self.assertEqual("GCS", inbox["proposals"][0]["project_id"])
        self.assertEqual("PROPOSED", inbox["proposals"][0]["state"])

        status, approved = self.request(
            "POST",
            "/v1/projects/GCS/governance-proposals/task_proposal_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": proposal["proposal_digest"],
                "source": "NATURAL_LANGUAGE",
                "commander_surface": "UNIVERSE_UI",
                "idempotency_key": "approve-task-proposal-test-001",
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
        self.assertIn(
            "universe.project-master-governance-approval.v1",
            approved["message"]["body"],
        )

        status, repeated = self.request(
            "POST",
            "/v1/projects/GCS/governance-proposals/task_proposal_test_001/decision",
            {
                "decision": "APPROVE",
                "proposal_digest": proposal["proposal_digest"],
                "source": "NATURAL_LANGUAGE",
                "commander_surface": "UNIVERSE_UI",
                "idempotency_key": "approve-task-proposal-test-001",
            },
            self.token,
        )
        self.assertEqual(200, status)
        self.assertEqual(
            "GOVERNANCE_PROPOSAL_ALREADY_APPROVED", repeated["status"]
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
        self.assertEqual(
            "GOVERNANCE_PROPOSAL_DIGEST_MISMATCH", response["error_code"]
        )
        self.assertTrue(self.server.wait_for_request_workers(timeout=1))

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
        self.assertEqual([], self.server.store.list_projects())
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
        request = {
            "intent": {
                "project": "Local trading workstation",
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
        self.assertEqual([], self.server.store.list_projects())

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
        self.assertEqual([], self.server.store.list_projects())

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
        self.assertEqual([], self.server.store.list_projects())

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
            },
            self.token,
        )
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual("BOUND", bound["status"])
        self.assertNotIn("token", bound)
        self.assertNotIn("endpoint", bound)
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
        self.assertEqual("DELIVERED_TO_MASTER", delivered["handoff"]["delivery_state"])
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

    def test_universe_mode_contract_and_aliases(self) -> None:
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
                        "UNIVERSE": {
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
            "UNIVERSE",
            "Universe mode",
            "CONDUCTOR",
            "Conductor mode",
            "\uc720\ub2c8\ubc84\uc2a4",
            "\uc720\ub2c8\ubc84\uc2a4\ubaa8\ub4dc",
            "\ucee8\ub355\ud130",
            "\ucee8\ub355\ud130\ubaa8\ub4dc",
        ):
            with self.subTest(intent=intent):
                self.assertEqual("CONDUCTOR", resolve_universe_mode_intent(intent))

        require_release_lifecycle_mode("MASTER")
        with self.assertRaisesRegex(UniverseError, "require MASTER Mode"):
            require_release_lifecycle_mode("UNIVERSE")

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
                    "worker_id": "grok-worker-001",
                    "result_receipt_ref": "result-001",
                    "skill_run_observation_count": 1,
                    "repository_write": False,
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
        self.assertFalse(created["invocation"]["result"]["repository_write"])
        binding_snapshot = created["invocation"]["invocation"][
            "worker_binding_snapshot"
        ]
        self.assertEqual("DEFAULT_AUTO", binding_snapshot["profile_id"])
        self.assertEqual(
            binding_snapshot,
            self.server.runtime_host.request["context_pack"][
                "worker_binding_snapshot"
            ],
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
        self.assertNotIn("worker_run_ref", created["invocation"]["result"])
        status, repeated = self.request("POST", path, payload)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(
            "RUNTIME_WORKER_INVOCATION_ALREADY_RECORDED", repeated["status"]
        )
        status, listed = self.request("GET", path)
        self.assertEqual(HTTPStatus.OK, status)
        self.assertEqual(1, len(listed["invocations"]))


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
        self.assertEqual("MANAGED", activated["project"]["attachment"]["universe_membership"])
        self.assertEqual("UNIVERSE_SHARED", activated["project"]["attachment"]["runtime_host"])

        inspected = self.store.inspect_shared_runtime_lease(
            "LEASE-HAPPY", now="2026-08-07T00:01:00Z"
        )
        self.assertEqual(lease["lease_id"], inspected["lease"]["lease_id"])
        self.assertEqual("SHARED_RUNTIME_ACTIVE", inspected["fallback"]["status"])
        listed = self.store.list_shared_runtime_leases(
            "LEASE-HAPPY", now="2026-08-07T00:01:00Z"
        )
        self.assertEqual([lease["lease_id"]], [item["lease_id"] for item in listed["leases"]])

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
        self.assertEqual("LINKED", released["project"]["attachment"]["universe_membership"])
        self.assertEqual("PROJECT_LOCAL", released["project"]["attachment"]["runtime_host"])

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
        self.assertEqual("PROJECT_LOCAL", unhealthy["project"]["attachment"]["runtime_host"])

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
        self.assertEqual("LINKED", expired["project"]["attachment"]["universe_membership"])
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
        self.assertEqual("LINKED", unhealthy["project"]["attachment"]["universe_membership"])
        self.assertEqual("UNIVERSE_SHARED", unhealthy["project"]["attachment"]["runtime_host"])

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
        self.assertEqual("RUNTIME_LEASE_ATTACHMENT_TRANSITION_INVALID", context.exception.code)

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


if __name__ == "__main__":
    unittest.main()
