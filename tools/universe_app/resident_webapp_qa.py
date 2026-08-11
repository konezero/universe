"""Read-only QA for the already running resident Universe service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import ipaddress
import json
from pathlib import Path
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


STATE_SCHEMA = "universe.local-service-state.v1"
REPORT_SCHEMA = "universe.resident-webapp-qa.v1"


class ResidentWebappQaError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass
class QaCheck:
    name: str
    status: str
    detail: str = ""


@dataclass
class ResidentQaReport:
    state_path: str
    endpoint: str = "UNKNOWN"
    service_pid: int | None = None
    universe_id: str = "UNKNOWN"
    database_name: str = "UNKNOWN"
    ownership: str = "RESIDENT_NOT_OWNED"
    cleanup: str = "NOT_STOPPED"
    checks: list[QaCheck] = field(default_factory=list)
    browser_artifacts: list[str] = field(default_factory=list)
    overall: str = "FAIL"

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(QaCheck(name=name, status=status, detail=detail))

    def finalize(self) -> None:
        self.overall = (
            "PASS"
            if self.checks
            and all(item.status == "PASS" for item in self.checks)
            else "FAIL"
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["schema"] = REPORT_SCHEMA
        return value


def load_resident_service_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ResidentWebappQaError("RESIDENT_STATE_UNAVAILABLE", str(error)) from error
    if not isinstance(value, Mapping):
        raise ResidentWebappQaError(
            "RESIDENT_STATE_INVALID", "service state must be an object"
        )
    if value.get("schema") != STATE_SCHEMA:
        raise ResidentWebappQaError(
            "RESIDENT_STATE_SCHEMA_INVALID", "service state schema is unsupported"
        )
    endpoint = str(value.get("endpoint") or "").rstrip("/")
    token = str(value.get("token") or "")
    database = str(value.get("database") or "")
    pid = value.get("pid")
    if not endpoint or not token or not database:
        raise ResidentWebappQaError(
            "RESIDENT_STATE_INCOMPLETE", "endpoint, token, and database are required"
        )
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ResidentWebappQaError(
            "RESIDENT_STATE_PID_INVALID", "service pid must be a positive integer"
        )
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.username or parsed.password or not parsed.hostname:
        raise ResidentWebappQaError(
            "RESIDENT_ENDPOINT_INVALID", "resident endpoint must be a plain loopback HTTP URL"
        )
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as error:
        raise ResidentWebappQaError(
            "RESIDENT_ENDPOINT_INVALID", "resident endpoint host must be an IP literal"
        ) from error
    if not address.is_loopback or parsed.port is None:
        raise ResidentWebappQaError(
            "RESIDENT_ENDPOINT_FORBIDDEN", "resident endpoint must use a loopback port"
        )
    return dict(value)


def redacted_state_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    universe = value.get("universe") if isinstance(value.get("universe"), Mapping) else {}
    return {
        "endpoint": str(value.get("endpoint") or "UNKNOWN"),
        "pid": value.get("pid"),
        "database_name": Path(str(value.get("database") or "UNKNOWN")).name,
        "universe_id": str(universe.get("universe_id") or "UNKNOWN"),
        "token_present": bool(value.get("token")),
    }


def _http_json(
    endpoint: str, token: str, path: str, timeout_seconds: float
) -> tuple[int, dict[str, Any]]:
    request = Request(
        endpoint.rstrip("/") + path,
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            value = json.loads(raw) if raw else {}
            return int(response.status), value if isinstance(value, dict) else {}
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value = {}
        return int(error.code), value if isinstance(value, dict) else {}
    except (URLError, TimeoutError, OSError):
        return 0, {}


def run_http_qa(
    report: ResidentQaReport,
    state: Mapping[str, Any],
    *,
    timeout_seconds: float = 10.0,
) -> None:
    endpoint = str(state["endpoint"]).rstrip("/")
    token = str(state["token"])
    expected_universe = str(
        (state.get("universe") or {}).get("universe_id") or "UNKNOWN"
    )
    checks = (
        ("health", "/health", "READY"),
        ("projects_api", "/v1/projects", "PROJECTS_COLLECTED"),
        ("todos_api", "/v1/todos", "TODOS_COLLECTED"),
        ("bench_api", "/v1/bench/skills", "SKILL_BENCH_COLLECTED"),
        (
            "provider_catalog_api",
            "/v1/session-observer/chat-rooms",
            "PROVIDER_CHAT_CATALOG_COLLECTED",
        ),
    )
    for name, path, expected_status in checks:
        status, payload = _http_json(endpoint, token, path, timeout_seconds)
        actual = str(payload.get("status") or "UNKNOWN")
        passed = status == 200 and actual == expected_status
        detail = f"http={status} status={actual}"
        if name == "health" and passed and expected_universe != "UNKNOWN":
            actual_universe = str(
                (payload.get("universe") or {}).get("universe_id") or "UNKNOWN"
            )
            passed = actual_universe == expected_universe
            detail += f" identity_match={str(passed).lower()}"
        report.add(name, "PASS" if passed else "FAIL", detail)


def run_browser_qa(
    report: ResidentQaReport,
    state: Mapping[str, Any],
    *,
    artifacts_dir: Path,
    timeout_seconds: float = 30.0,
) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        report.add("browser_runtime", "FAIL", "python Playwright is unavailable")
        return

    endpoint = str(state["endpoint"]).rstrip("/")
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    profiles = (
        ("desktop", {"width": 1440, "height": 900}),
        ("mobile", {"width": 390, "height": 844}),
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for label, viewport in profiles:
                console_errors: list[str] = []
                page_errors: list[str] = []
                request_failures: list[str] = []
                page = browser.new_page(viewport=viewport)
                page.on(
                    "console",
                    lambda message, sink=console_errors: (
                        sink.append(message.text) if message.type == "error" else None
                    ),
                )
                page.on("pageerror", lambda error, sink=page_errors: sink.append(str(error)))
                page.on(
                    "requestfailed",
                    lambda request, sink=request_failures: sink.append(
                        request.url.split("?", 1)[0]
                    ),
                )
                try:
                    page.goto(
                        endpoint + "/",
                        wait_until="domcontentloaded",
                        timeout=int(timeout_seconds * 1000),
                    )
                    # Universe keeps SSE connections open, so networkidle is not a
                    # valid completion signal. Wait for the hydrated product shell.
                    page.wait_for_function(
                        """() => {
                          const status = document.querySelector('#service-status');
                          const projects = document.querySelector('#project-list');
                          return status && status.dataset.state !== 'loading' &&
                            projects && projects.children.length > 0;
                        }""",
                        timeout=int(timeout_seconds * 1000),
                    )
                    page.wait_for_timeout(500)
                    missing = page.evaluate(
                        """() => [
                          '#service-status', '#project-list', '#session-rail-list',
                          '#universe-graph', '#conversation-title', '#dispatch-form',
                          '#todo-list'
                        ].filter((selector) => !document.querySelector(selector))"""
                    )
                    status_state = page.locator("#service-status").get_attribute("data-state")
                    projects = page.locator("#project-list").locator(":scope > *").count()
                    layout = page.evaluate(
                        """() => ({
                          width: window.innerWidth,
                          scrollWidth: document.documentElement.scrollWidth,
                          bodyScrollWidth: document.body.scrollWidth
                        })"""
                    )
                    overlaps = page.evaluate(
                        """() => {
                          const selectors = [
                            '.chat-dock-toggle', '.conversation-title-wrap',
                            '.action-inbox-button', '.layer-opacity'
                          ];
                          const entries = selectors.map((selector) => {
                            const element = document.querySelector(selector);
                            if (!element) return null;
                            const style = getComputedStyle(element);
                            const rect = element.getBoundingClientRect();
                            if (style.display === 'none' || style.visibility === 'hidden' ||
                                rect.width <= 0 || rect.height <= 0) return null;
                            return { selector, rect };
                          }).filter(Boolean);
                          const collisions = [];
                          for (let left = 0; left < entries.length; left += 1) {
                            for (let right = left + 1; right < entries.length; right += 1) {
                              const a = entries[left];
                              const b = entries[right];
                              const separated = a.rect.right <= b.rect.left + 1 ||
                                b.rect.right <= a.rect.left + 1 ||
                                a.rect.bottom <= b.rect.top + 1 ||
                                b.rect.bottom <= a.rect.top + 1;
                              if (!separated) collisions.push(`${a.selector}:${b.selector}`);
                            }
                          }
                          return collisions;
                        }"""
                    )
                    screenshot = artifacts_dir / f"resident-{label}.png"
                    page.screenshot(path=str(screenshot), full_page=True)
                    report.browser_artifacts.append(screenshot.name)
                    failures = list(dict.fromkeys(request_failures))
                    passed = (
                        not missing
                        and status_state == "ready"
                        and projects > 0
                        and not console_errors
                        and not page_errors
                        and not failures
                        and not overlaps
                        and int(layout["scrollWidth"]) <= int(layout["width"]) + 2
                        and int(layout["bodyScrollWidth"]) <= int(layout["width"]) + 2
                    )
                    detail = (
                        f"projects={projects} state={status_state} missing={len(missing)} "
                        f"console={len(console_errors)} page={len(page_errors)} "
                        f"requests={len(failures)} overflow="
                        f"{max(int(layout['scrollWidth']), int(layout['bodyScrollWidth'])) - int(layout['width'])} "
                        f"overlaps={len(overlaps)}"
                    )
                    report.add(
                        f"browser_{label}", "PASS" if passed else "FAIL", detail
                    )
                except Exception as error:  # noqa: BLE001 - QA records browser failure
                    report.add(
                        f"browser_{label}",
                        "FAIL",
                        f"{type(error).__name__}: {str(error)[:240]}",
                    )
                finally:
                    page.close()
        finally:
            browser.close()


def run_resident_qa(
    state_path: Path,
    *,
    artifacts_dir: Path,
    timeout_seconds: float = 30.0,
    browser: bool = True,
) -> ResidentQaReport:
    report = ResidentQaReport(state_path=str(state_path.expanduser().resolve()))
    try:
        state = load_resident_service_state(state_path)
    except ResidentWebappQaError as error:
        report.add("service_state", "FAIL", f"{error.code}: {error.detail}")
        report.finalize()
        return report
    summary = redacted_state_summary(state)
    report.endpoint = summary["endpoint"]
    report.service_pid = summary["pid"]
    report.universe_id = summary["universe_id"]
    report.database_name = summary["database_name"]
    report.add("service_state", "PASS", "resident state is valid and loopback-only")
    run_http_qa(report, state, timeout_seconds=min(timeout_seconds, 10.0))
    if browser:
        run_browser_qa(
            report,
            state,
            artifacts_dir=artifacts_dir,
            timeout_seconds=timeout_seconds,
        )
    report.finalize()
    return report


__all__ = [
    "REPORT_SCHEMA",
    "ResidentQaReport",
    "ResidentWebappQaError",
    "load_resident_service_state",
    "redacted_state_summary",
    "run_resident_qa",
]
