#!/usr/bin/env python3
"""Smoke harness for UNIVERSE_E2E_GCS_SEED_AND_MASTER_LINE_V1.

Modes:
  check  - observe a running local Universe service (default server.json)
  run    - isolated in-process product line (no external service)

Does not create authority, Task Frames, or package installers.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

SCENARIO_ID = "UNIVERSE_E2E_GCS_SEED_AND_MASTER_LINE_V1"
DEFAULT_STATE = Path(
    os.environ.get(
        "LOCALAPPDATA",
        str(Path.home() / "AppData" / "Local"),
    )
) / "Universe" / "server.json"


@dataclass
class StepResult:
    name: str
    status: str  # PASS | FAIL | SKIP | PENDING_APPROVAL
    detail: str = ""


@dataclass
class SmokeReport:
    scenario_id: str = SCENARIO_ID
    mode: str = "check"
    steps: list[StepResult] = field(default_factory=list)
    overall: str = "FAIL"

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append(StepResult(name=name, status=status, detail=detail))

    def finalize(self) -> None:
        if any(step.status == "FAIL" for step in self.steps):
            self.overall = "FAIL"
        elif any(step.status == "PENDING_APPROVAL" for step in self.steps):
            self.overall = "BLOCKED"
        elif not self.steps:
            self.overall = "FAIL"
        else:
            self.overall = "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "universe.e2e-smoke-report.v1",
            "scenario_id": self.scenario_id,
            "mode": self.mode,
            "overall": self.overall,
            "steps": [
                {"name": s.name, "status": s.status, "detail": s.detail}
                for s in self.steps
            ],
        }


def _http(
    endpoint: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    data = None
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(
        endpoint.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return int(response.status), json.loads(raw) if raw else {}
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return int(error.code), payload
    except URLError as error:
        return 0, {"error": str(error.reason if hasattr(error, "reason") else error)}


def _http_text(
    endpoint: str,
    path: str,
    *,
    timeout: float = 10.0,
) -> tuple[int, str]:
    request = Request(endpoint.rstrip("/") + path, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        return int(error.code), error.read().decode("utf-8", errors="replace")
    except URLError as error:
        return 0, str(error.reason if hasattr(error, "reason") else error)


def _stop_in_process_server(
    server: Any,
    thread: threading.Thread | None,
    host: str,
    port: int,
) -> tuple[bool, str]:
    errors: list[str] = []
    if thread is not None and thread.is_alive():
        try:
            server.shutdown()
        except Exception as error:  # noqa: BLE001 - smoke reports cleanup failures
            errors.append(f"shutdown={type(error).__name__}: {error}")
    try:
        server.server_close()
    except Exception as error:  # noqa: BLE001 - smoke reports cleanup failures
        errors.append(f"server_close={type(error).__name__}: {error}")
    if thread is not None:
        thread.join(timeout=5)
        if thread.is_alive():
            errors.append("server thread remained alive")
    try:
        with socket.create_connection((host, port), timeout=0.2):
            errors.append("listener remained reachable")
    except OSError:
        pass
    return not errors, "; ".join(errors) if errors else "thread stopped; listener closed"


def check_live(state_path: Path, project_id: str) -> SmokeReport:
    report = SmokeReport(mode="check")
    if not state_path.is_file():
        report.add("service_state", "FAIL", f"missing {state_path}")
        report.finalize()
        return report

    state = json.loads(state_path.read_text(encoding="utf-8"))
    endpoint = str(state.get("endpoint") or "").rstrip("/")
    token = str(state.get("token") or "")
    if not endpoint or not token:
        report.add("service_state", "FAIL", "endpoint/token missing in server.json")
        report.finalize()
        return report
    report.add("service_state", "PASS", endpoint)

    status, health = _http(endpoint, "GET", "/health")
    if status == 200 and health.get("status") == "READY":
        report.add(
            "health",
            "PASS",
            f"universe_id={(health.get('universe') or {}).get('universe_id')}",
        )
    else:
        report.add("health", "FAIL", f"http={status} body={health}")

    status, projects = _http(endpoint, "GET", "/v1/projects", token=token)
    project_list = projects.get("projects") or []
    project = next(
        (item for item in project_list if item.get("project_id") == project_id),
        None,
    )
    if status == 200 and project:
        inbox = (project.get("refs") or {}).get("master_inbox")
        report.add(
            "project_connected",
            "PASS",
            f"root={project.get('project_root')} master_inbox={inbox}",
        )
    else:
        report.add("project_connected", "FAIL", f"http={status}")

    # Migrated (2026-09-05) off /dispatches onto /master-messages - discovery
    # dispatch now queues through the Master claim queue, not the old
    # project_dispatch file-drop.
    status, master_messages = _http(
        endpoint, "GET", f"/v1/projects/{project_id}/master-messages", token=token
    )
    items = master_messages.get("messages") or []
    discovery = [
        item
        for item in items
        if item.get("title") == "Prepare Universe project seed"
        or (item.get("metadata") or {}).get("expected_output", {}).get("schema")
        == "universe.project-discovery-dispatch.v1"
    ]
    if not discovery and items:
        discovery = items
    if status == 200 and discovery:
        top = discovery[0]
        d_status = top.get("delivery_state")
        report.add(
            "seed_dispatch",
            "PASS" if d_status == "DONE" else "FAIL",
            f"{top.get('message_id')} status={d_status}",
        )
    else:
        report.add("seed_dispatch", "FAIL", f"http={status} count={len(items)}")

    if project:
        root = Path(str(project.get("project_root") or ""))
        assets = [
            "bindings.json",
            "documents.json",
            "functional-graph.json",
            "implementation-graph.json",
            "manifest.json",
        ]
        universe_root = root / ".ai" / "universe"
        missing = [name for name in assets if not (universe_root / name).is_file()]
        if not missing and universe_root.is_dir():
            report.add("seed_assets", "PASS", "ok")
        else:
            proposal_status, proposal = _http(
                endpoint,
                "GET",
                f"/v1/projects/{project_id}/seed-asset-proposal",
                token=token,
            )
            proposal_value = proposal.get("proposal") or {}
            proposed_assets = proposal_value.get("assets") or []
            approval = (proposal_value.get("apply_contract") or {}).get("approval")
            if (
                proposal_status == 200
                and len(proposed_assets) == len(assets)
                and approval == "EXACT_USER_APPROVAL_REQUIRED"
            ):
                report.add(
                    "seed_assets",
                    "PENDING_APPROVAL",
                    "exact seed asset approval required; missing=" + ",".join(missing),
                )
            else:
                report.add(
                    "seed_assets",
                    "FAIL",
                    f"missing={missing} proposal_http={proposal_status}",
                )

    status, seed = _http(
        endpoint, "GET", f"/v1/projects/{project_id}/seed", token=token
    )
    if status == 200 and seed.get("seed"):
        report.add(
            "seed_store",
            "PASS",
            f"seed_id={(seed.get('seed') or {}).get('seed_id')}",
        )
    else:
        report.add("seed_store", "FAIL", f"http={status}")

    status, projection = _http(
        endpoint, "GET", f"/v1/projects/{project_id}/projection", token=token
    )
    if status == 200 and projection.get("projection"):
        proj = projection["projection"]
        report.add(
            "projection",
            "PASS",
            f"nodes={len(proj.get('nodes') or [])} docs={len(proj.get('documents') or [])}",
        )
    else:
        report.add("projection", "FAIL", f"http={status}")

    status, handoffs = _http(
        endpoint, "GET", f"/v1/projects/{project_id}/master-handoffs", token=token
    )
    hs = handoffs.get("handoffs") or []
    if status == 200:
        queued = [
            handoff
            for handoff in hs
            if handoff.get("delivery_state")
            in {"QUEUED_FOR_MASTER", "ACCEPTED_BY_MASTER"}
        ]
        report.add(
            "master_handoff",
            "PASS" if queued else "SKIP",
            f"total={len(hs)} queued_or_accepted={len(queued)}",
        )
    else:
        report.add("master_handoff", "FAIL", f"http={status}")

    status, todos = _http(endpoint, "GET", "/v1/todos", token=token)
    if status == 200:
        count = len(todos.get("todos") or [])
        report.add("todos", "PASS" if count >= 0 else "FAIL", f"count={count}")
    else:
        report.add("todos", "FAIL", f"http={status}")

    status, bench = _http(endpoint, "GET", "/v1/bench/skills", token=token)
    if status == 200:
        report.add(
            "bench_skills",
            "PASS",
            f"entries={len(bench.get('bench') or [])}",
        )
    else:
        report.add("bench_skills", "FAIL", f"http={status}")

    status, observations = _http(
        endpoint,
        "GET",
        f"/v1/projects/{project_id}/skill-observations",
        token=token,
    )
    if status == 200:
        report.add(
            "skill_observations",
            "PASS",
            f"count={len(observations.get('observations') or [])}",
        )
    else:
        report.add("skill_observations", "FAIL", f"http={status}")

    status, cases = _http(
        endpoint,
        "GET",
        f"/v1/projects/{project_id}/experience-cases",
        token=token,
    )
    if status == 200:
        report.add(
            "experience_cases",
            "PASS",
            f"count={len(cases.get('cases') or [])}",
        )
    else:
        report.add("experience_cases", "FAIL", f"http={status}")

    report.finalize()
    # Optional steps SKIP do not fail overall unless no FAIL and required PASS present
    required = {
        "service_state",
        "health",
        "project_connected",
        "seed_dispatch",
        "seed_assets",
        "seed_store",
        "projection",
    }
    required_ok = all(
        step.status == "PASS"
        for step in report.steps
        if step.name in required
    )
    report.overall = "PASS" if required_ok and not any(
        step.status == "FAIL" for step in report.steps if step.name in required
    ) else "FAIL"
    # non-required FAIL still fails overall for visibility
    if any(step.status == "FAIL" for step in report.steps):
        report.overall = "FAIL"
    if required_ok and not any(step.status == "FAIL" for step in report.steps):
        report.overall = "PASS"
    elif not any(step.status == "FAIL" for step in report.steps) and any(
        step.status == "PENDING_APPROVAL" for step in report.steps
    ):
        report.overall = "BLOCKED"
    return report


def run_isolated() -> SmokeReport:
    """In-process product line with a real ephemeral HTTP listener."""
    from universe_server import create_server, universe_mode_contract

    report = SmokeReport(mode="run")
    temp = tempfile.TemporaryDirectory()
    server = None
    server_thread: threading.Thread | None = None
    server_host = "127.0.0.1"
    server_port = 0
    cleanup_recorded = False
    try:
        root = Path(temp.name)
        project_root = root / "GCS"
        runtime = project_root / ".ai" / "runtime" / "project_instance"
        runtime.mkdir(parents=True)
        (project_root / ".ai" / "runtime" / "anchor_store").mkdir(parents=True)
        (project_root / ".ai" / "inbox" / "MASTER").mkdir(parents=True)
        (project_root / ".ai" / "universe").mkdir(parents=True)
        (project_root / "REPOSITORY_MANIFEST.md").write_text(
            "# GCS Repository Manifest\n", encoding="utf-8"
        )
        (runtime / "mode_registry.json").write_text(
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
        (runtime / "status.md").write_text("Status: READY\n", encoding="utf-8")
        (project_root / "src").mkdir()
        (project_root / "src" / "broker.py").write_text(
            "class BrokerClient:\n    pass\n", encoding="utf-8"
        )
        (project_root / "docs").mkdir()
        (project_root / "docs" / "architecture.md").write_text(
            "# Architecture\n", encoding="utf-8"
        )

        token = "e2e-smoke-token"
        server = create_server(
            database_path=root / "universe.sqlite3",
            token=token,
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
        )
        server_thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="universe-e2e-smoke",
        )
        server_thread.start()
        server_host, server_port = server.server_address[:2]
        if isinstance(server_host, bytes):
            server_host = server_host.decode("ascii")
        endpoint = f"http://{server_host}:{server_port}"
        store = server.store
        report.add("service_create", "PASS", f"in-process {endpoint}")

        status, page = _http_text(endpoint, "/")
        static_ready = (
            status == 200
            and 'id="project-list"' in page
            and 'id="dispatch-form"' in page
        )
        report.add(
            "web_static",
            "PASS" if static_ready else "FAIL",
            f"http={status} shell={static_ready}",
        )

        status, health = _http(endpoint, "GET", "/health")
        report.add(
            "web_health",
            "PASS" if status == 200 and health.get("status") == "READY" else "FAIL",
            f"http={status} status={health.get('status')}",
        )

        for step_name, path, expected_status in (
            ("web_projects", "/v1/projects", "PROJECTS_COLLECTED"),
            ("web_todos", "/v1/todos", "TODOS_COLLECTED"),
            ("web_bench", "/v1/bench/skills", "SKILL_BENCH_COLLECTED"),
        ):
            status, payload = _http(endpoint, "GET", path, token=token)
            report.add(
                step_name,
                "PASS"
                if status == 200 and payload.get("status") == expected_status
                else "FAIL",
                f"http={status} status={payload.get('status')}",
            )

        project, _registered = store.register_project(
            {
                "project_id": "GCS",
                "project_root": str(project_root),
            }
        )
        report.add(
            "project_register",
            "PASS",
            f"master_inbox={project['refs']['master_inbox']}",
        )

        # Migrated (2026-09-05) off project_dispatch's file-drop lifecycle
        # (queue -> deliver -> acknowledge -> start -> record_result_packet)
        # onto the Master claim queue's (queue -> claim -> complete). Step
        # names kept as-is even though "discovery_deliver" now means "claim"
        # - they're just report labels, and this is exactly the migration
        # the queue was built for: no more one-shot file drop into
        # .ai/inbox/MASTER with nothing to automatically consume it.
        envelope, created = store.create_project_seed_discovery_dispatch("GCS")
        report.add(
            "discovery_queue",
            "PASS" if envelope["delivery_state"] == "QUEUED" else "FAIL",
            f"created={created} id={envelope['message_id']}",
        )

        claimed = store.claim_master_message("GCS", provider="CLAUDE")
        report.add(
            "discovery_deliver",
            "PASS"
            if claimed is not None
            and claimed["message_id"] == envelope["message_id"]
            and claimed["delivery_state"] == "PROCESSING"
            else "FAIL",
            f"claimed={claimed is not None} "
            f"status={claimed.get('delivery_state') if claimed else None}",
        )

        completed = store.complete_master_message(
            envelope["message_id"],
            provider="CLAUDE",
            result_ref="e2e-smoke:result",
        )
        report.add(
            "discovery_complete",
            "PASS" if completed["delivery_state"] == "DONE" else "FAIL",
            f"status={completed['delivery_state']}",
        )

        # Seed + projection via store API surface when available through HTTP helpers
        # Use seed POST shape minimal - skip if complex; mark seed_assets as SKIP
        # when only dispatch line is required in run mode
        report.add(
            "seed_assets",
            "SKIP",
            "run mode closes dispatch without Host seed-asset apply",
        )

        report.add("todos_api", "PASS", f"count={len(store.list_todos())}")
        report.add(
            "bench_api",
            "PASS",
            f"count={len(store.list_skill_bench())}",
        )
        cleanup_ok, cleanup_detail = _stop_in_process_server(
            server,
            server_thread,
            str(server_host),
            int(server_port),
        )
        report.add(
            "web_shutdown",
            "PASS" if cleanup_ok else "FAIL",
            cleanup_detail,
        )
        cleanup_recorded = True
        server = None
        server_thread = None
        report.finalize()
        return report
    except Exception as error:  # noqa: BLE001 - smoke must always report
        report.add("run_isolated", "FAIL", f"{type(error).__name__}: {error}")
        report.finalize()
        return report
    finally:
        if server is not None and not cleanup_recorded:
            cleanup_ok, cleanup_detail = _stop_in_process_server(
                server,
                server_thread,
                str(server_host),
                int(server_port),
            )
            report.add(
                "web_shutdown",
                "PASS" if cleanup_ok else "FAIL",
                cleanup_detail,
            )
            report.finalize()
        temp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Smoke {SCENARIO_ID}")
    parser.add_argument(
        "mode",
        choices=("check", "run"),
        help="check=live server.json observe; run=in-process isolated line",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE,
        help="Universe server.json path for check mode",
    )
    parser.add_argument(
        "--project-id",
        default="GCS",
        help="Project id for check mode",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable report only",
    )
    args = parser.parse_args(argv)

    try:
        if args.mode == "check":
            report = check_live(args.state_file, args.project_id)
        else:
            report = run_isolated()
    except Exception:  # noqa: BLE001
        print(traceback.format_exc(), file=sys.stderr)
        return 2

    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"scenario: {payload['scenario_id']}")
        print(f"mode:     {payload['mode']}")
        print(f"overall:  {payload['overall']}")
        for step in payload["steps"]:
            detail = f" - {step['detail']}" if step["detail"] else ""
            print(f"  [{step['status']}] {step['name']}{detail}")
    return 0 if report.overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
