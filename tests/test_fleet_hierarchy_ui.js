"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const app = fs.readFileSync(path.join(root, "tools", "universe_ui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "tools", "universe_ui", "styles.css"), "utf8");

assert.match(app, /renderFleetHierarchyTrail\(selNode, selTodo\)/);
assert.match(app, /fleet-hierarchy-trail/);
assert.match(app, /renderFleetAutomationGuidance\(projection\.error\)/);
assert.match(app, /사용량 한도 또는 제공자 쿼터에 도달했습니다/);
assert.match(app, /Goal 소유 Master\/Conductor 세션으로 전환/);
assert.match(css, /\.fleet-hierarchy-trail/);
assert.match(css, /\.fleet-automation-guidance/);
assert.match(css, /text-overflow: ellipsis/);

// Fleet plan-item panel: reachable from both Todo and node detail, uses the
// shared plan.item.* Actions, CAS + same-request_id retry, and never claims
// project completion.
assert.match(app, /function renderFleetPlanItemsPanel\(selNode, selTodo\)/);
assert.match(app, /renderFleetPlanItemsPanel\(selNode, null\)/);
assert.match(app, /const planItems = renderFleetPlanItemsPanel\(selNode, selTodo\)/);
for (const actionId of ["plan.item.list", "plan.item.save", "plan.item.trace"]) {
  assert.ok(app.includes(`invokeServerAction("${actionId}"`), actionId);
}
assert.match(app, /expected_revision: editor\.expectedRevision/);
assert.match(app, /"universe\.plan-item\.save\."/);
assert.match(app, /PLAN_ITEM_REVISION_CONFLICT/);
assert.match(app, /NOT_AGGREGATED/);
assert.match(css, /\.fleet-plan-items-head/);
assert.match(css, /\.fleet-plan-item-editor/);

async function checkPlanItemSaveBehavior() {
  const vm = require("node:vm");
  const start = app.indexOf("const PLAN_ITEM_KINDS");
  const end = app.indexOf("function renderFleetPlanItemTrace(", start);
  assert.ok(start >= 0 && end > start);
  const store = new Map();
  const calls = [];
  let failure = null;
  class ApiError extends Error {
    constructor(status, errorCode) { super(`HTTP ${status} ${errorCode}`); this.status = status; this.errorCode = errorCode; }
  }
  const context = {
    state: { selectedProject: { project_id: "universe" }, fleetPlanItems: {}, fleetPlanItemTrace: {}, fleetPlanItemEditor: null },
    sessionStorage: {
      get length() { return store.size; },
      key: (i) => [...store.keys()][i] ?? null,
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    crypto: { randomUUID: () => `req-${calls.length}-abcdef`, getRandomValues: (a) => a.fill(171) },
    homeNodeRefKey: (id) => String(id).replace(/^(feat|goal):/, ""),
    toast: () => {},
    invokeServerAction: async (actionId, request) => {
      calls.push({ actionId, request });
      if (failure) throw failure;
      return { replayed: false, plan_item: { plan_item_id: request.plan_item_id } };
    },
  };
  vm.createContext(context);
  vm.runInContext(`${app.slice(start, end)}\nthis.api = { fleetPlanItemScopes, openFleetPlanItemEditor, fleetPlanItemRequest, saveFleetPlanItem, pendingFleetPlanItemSaves };`, context);
  const api = context.api;

  const goalScopes = api.fleetPlanItemScopes({ node_id: "goal:goal_1" }, { todo_id: "todo_1", project_id: "universe" });
  assert.deepEqual([...goalScopes.scopes.map((s) => s.label)], ["Todo"], "Goal nodes are not linkable feature nodes");
  const scopes = api.fleetPlanItemScopes({ node_id: "feat:feature_1" }, null);
  assert.equal(scopes.scopes[0].filter.node_ref, "feature_1");

  api.openFleetPlanItemEditor("panel", null, { todoId: "todo_1", featureId: "feature_1" });
  const editor = context.state.fleetPlanItemEditor;
  assert.equal(editor.planItemId, "plan_abababababababab");
  editor.draft.title = "  Wire panel ";
  editor.draft.depends_on = "plan_a, plan_a  plan_b";
  const request = api.fleetPlanItemRequest("universe", editor);
  assert.equal(request.expected_revision, 0);
  assert.deepEqual({ ...request.item.source }, { kind: "MANUAL" });
  assert.deepEqual([...request.item.depends_on], ["plan_a", "plan_b"]);
  assert.deepEqual([...request.item.node_refs], ["feature_1"]);
  editor.draft.todo_refs = "bad ref!";
  assert.throws(() => api.fleetPlanItemRequest("universe", editor), /invalid id/);

  const pendingKey = `universe.plan-item.save.${request.plan_item_id}`;
  failure = new ApiError(502, "");
  await assert.rejects(api.saveFleetPlanItem(request));
  assert.equal(JSON.parse(store.get(pendingKey)).request_id, request.request_id, "uncertain save keeps its request_id");
  assert.equal(api.pendingFleetPlanItemSaves("universe").length, 1);
  failure = new ApiError(409, "PLAN_ITEM_REVISION_CONFLICT");
  await assert.rejects(api.saveFleetPlanItem(request), /다른 곳에서 먼저 수정/);
  assert.equal(store.has(pendingKey), false, "definite 4xx clears the pending request");
  failure = null;
  context.state.fleetPlanItems["universe|todo|todo_1"] = { status: "READY", items: [] };
  await api.saveFleetPlanItem(request);
  assert.equal(store.has(pendingKey), false);
  assert.equal(context.state.fleetPlanItems["universe|todo|todo_1"].status, "STALE", "save invalidates project lists");
  assert.ok(calls.every((call) => call.actionId === "plan.item.save"));
}

checkPlanItemSaveBehavior()
  .then(() => console.log("fleet hierarchy UI checks passed"))
  .catch((error) => { console.error(error); process.exitCode = 1; });
