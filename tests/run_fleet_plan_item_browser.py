"""Manual isolated Chromium acceptance; run directly, never against production."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_memory_candidates_and_delegations import MemoryCandidateApiTests
from playwright.sync_api import sync_playwright, expect


def main():
    fixture = MemoryCandidateApiTests()
    fixture.setUp()
    output = ROOT / ".artifacts/ui/fleet-plan-item-acceptance"
    output.mkdir(parents=True, exist_ok=True)
    try:
        feature, _ = fixture.server.store.create_feature_node("TEST", {"title": "Browser fixture",
            "intent_text": "isolated", "idempotency_key": "browser-fixture", "created_by_role": "USER"})
        todo = fixture.server.store.create_todo({"project_id": "TEST", "scope_kind": "NODE",
            "node_ref": feature["feature_id"], "title": "Browser Todo", "detail": "isolated",
            "priority": "P2", "state": "READY", "source_kind": "USER", "sort_order": 0})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1200, "height": 1000})
                # Block accidental outbound access. Only this disposable server exists here.
                page.route("**/*", lambda route: route.continue_() if (
                    route.request.url.startswith(fixture.endpoint + "/")
                    and not route.request.url.split("?")[0].endswith("/stream")) else route.abort())
                page.goto(fixture.endpoint)
                page.wait_for_load_state("networkidle")
                page.wait_for_function("typeof renderFleetPlanItemsPanel === 'function'")
                page.evaluate("""({feature, todo}) => {
                    state.selectedProject = {project_id: 'TEST'};
                    state.fleetPlanItems = {}; state.fleetPlanItemTrace = {}; state.fleetPlanItemEditor = null;
                    window.fixtureNode = {node_id: 'feat:' + feature.feature_id, title: feature.title};
                    window.fixtureTodo = todo;
                    // Use production component and CSS with the real isolated Action API.
                    document.body.replaceChildren();
                    const main = document.createElement('main'); main.style.padding = '24px';
                    main.append(renderFleetPlanItemsPanel(fixtureNode, fixtureTodo)); document.body.append(main);
                }""", {"feature": feature, "todo": todo})
                page.screenshot(path=str(output / "empty.png"), full_page=True)
                page.get_by_role("button", name="+ 새 항목", exact=True).click()
                page.get_by_label("Title", exact=True).fill("Browser verified plan")
                page.get_by_role("button", name="저장", exact=True).click()
                expect(page.locator(".fleet-plan-item-title")).to_have_text("Browser verified plan")
                page.get_by_role("button", name="편집", exact=True).click()
                page.get_by_label("Title", exact=True).fill("Edited plan")
                page.get_by_role("button", name="저장", exact=True).click()
                expect(page.locator(".fleet-plan-item-title")).to_have_text("Edited plan")
                page.get_by_role("button", name="추적", exact=True).click()
                expect(page.get_by_role("button", name="추적 닫기", exact=True)).to_be_visible()
                page.screenshot(path=str(output / "saved-trace.png"), full_page=True)
                # Create an actual CAS conflict while the editor holds revision 2.
                page.get_by_role("button", name="편집", exact=True).click()
                status, listed = fixture.request("POST", "/v1/actions", {"action_id": "plan.item.list",
                    "request": {"project_id": "TEST", "todo_id": todo["todo_id"]}})
                assert status == 200, listed
                item = listed["plan_items"][0]
                status, read = fixture.request("POST", "/v1/actions", {"action_id": "plan.item.read",
                    "request": {"plan_item_id": item["plan_item_id"]}})
                assert status == 200, read
                # Obtain the exact contract payload from the production request builder.
                request = page.evaluate("fleetPlanItemRequest('TEST', state.fleetPlanItemEditor)")
                request["item"]["title"] = "Concurrent update"
                status, saved = fixture.request("POST", "/v1/actions", {"action_id": "plan.item.save", "request": request})
                assert status == 200, saved
                page.get_by_label("Title", exact=True).fill("Stale edit")
                page.get_by_role("button", name="저장", exact=True).click()
                expect(page.locator(".fleet-plan-item-error").first).to_be_visible()
                page.screenshot(path=str(output / "conflict.png"), full_page=True)
                page.get_by_role("button", name="취소", exact=True).click()
                # Lost response after commit: intercept only the next save response.
                page.get_by_role("button", name="+ 새 항목", exact=True).click()
                page.get_by_label("Title", exact=True).fill("Retry plan")
                requests = []
                def lose_response(route):
                    body = route.request.post_data_json
                    if body.get("action_id") == "plan.item.save":
                        requests.append(body["request"])
                        response = route.fetch()
                        if len(requests) == 1:
                            route.fulfill(status=502, content_type="application/json", body='{"error_code":"TEST_RESPONSE_LOST"}')
                        else:
                            route.fulfill(response=response)
                    else:
                        route.continue_()
                page.route(fixture.endpoint + "/v1/actions", lose_response)
                page.get_by_role("button", name="저장", exact=True).click()
                page.get_by_role("button", name="이전 요청 확인", exact=True).click()
                expect(page.locator(".fleet-plan-item-title").filter(has_text="Retry plan")).to_have_count(1)
                assert len(requests) == 2 and requests[0] == requests[1], requests
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator(".fleet-plan-item-title").first.bounding_box()["width"] > 100
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "narrow.png"), full_page=True)
                status, listed = fixture.request("POST", "/v1/actions", {"action_id": "plan.item.list",
                    "request": {"project_id": "TEST", "node_ref": feature["feature_id"]}})
                assert status == 200 and len(listed["plan_items"]) == 2, listed
                result = {"status": "PASS", "scope": "production panel + isolated Action HTTP",
                    "checks": ["create", "edit/readback", "trace", "CAS conflict", "same-request retry", "node/Todo links"],
                    "screenshots": str(output), "production_mutations": False}
            finally:
                browser.close()
    finally:
        fixture.tearDown()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
