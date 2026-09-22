const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/app.js"), "utf8");

function helpers() {
  const start = source.indexOf("function fleetBoundSessionRows(");
  const end = source.indexOf("bindEvents();", start);
  assert.ok(start >= 0 && end > start);
  const nodeStart = source.indexOf("function fleetNodeSessionRows(");
  const nodeEnd = source.indexOf("function renderHomeNodes(", nodeStart);
  assert.ok(nodeStart >= 0 && nodeEnd > nodeStart);
  const context = vm.createContext({});
  vm.runInContext(source.slice(nodeStart, nodeEnd) + source.slice(start, end), context);
  return context;
}

const master = (nodeRef, anchor = "master-a", project = "universe") =>
  ({ project_id: project, node_ref: nodeRef, session_anchor_ref: anchor, state: "ACTIVE" });
const worker = (nodeRef, anchor = "worker-a", todo = "todo-a", project = "universe") =>
  ({ project_id: project, node_ref: nodeRef, session_anchor_ref: anchor,
     todo_id: todo, worker_role: "IMPLEMENTER", state: "ACTIVE" });
const terminal = (anchor, mode, activity = "working") =>
  ({ project_id: "universe", session_anchor_ref: anchor, terminal_id: `term-${anchor}`,
     mode, provider: "CODEX", state: "LIVE", provider_cli_alive: true,
     prompt_activity: { status: activity }, host_turn_state: { input_active: activity === "working" } });

test("node membership comes from active scoped assignments, not a project session list", () => {
  const c = helpers();
  const rows = c.fleetBoundSessionRows("universe", [
    master("node-a"), master("node-b", "master-b"), master(null, "conductor"),
    master("node-a", "other-project", "other"),
    { ...master("node-a", "old"), state: "UNASSIGNED" },
  ], [worker("node-a"), worker("node-b", "review-b", "todo-b")], [
    terminal("master-a", "MASTER"), terminal("master-b", "MASTER", "idle"),
    terminal("worker-a", "WORKER"), terminal("review-b", "WORKER"),
    terminal("unrelated", "MASTER"),
  ], []);
  assert.deepEqual(Array.from(rows.map((row) => [row.nodeRef, row.role, row.sessionAnchorRef])), [
    ["node-a", "MASTER", "master-a"], ["node-b", "MASTER", "master-b"],
    ["node-a", "IMPLEMENTER", "worker-a"], ["node-b", "IMPLEMENTER", "review-b"],
  ]);
  assert.equal(rows[0].activity, "WORKING");
  assert.equal(rows[1].activity, "IDLE");
  assert.equal(rows[2].terminalId, "term-worker-a");
});

test("assigned offline and unreadable terminal states stay distinct", () => {
  const c = helpers();
  const rows = c.fleetBoundSessionRows("universe", [master("node-a")], [], [], []);
  assert.equal(rows[0].presence, "OFFLINE");
  assert.equal(rows[0].activity, "NOT_RUNNING");
  const unknown = c.fleetBoundSessionRows("universe", [master("node-a")], [], null, []);
  assert.equal(unknown[0].presence, "UNKNOWN");
  const wrongMode = c.fleetBoundSessionRows("universe", [master("node-a")], [],
    [terminal("master-a", "WORKER")], []);
  assert.equal(wrongMode.length, 0);
  const masterHostedReview = c.fleetBoundSessionRows("universe", [master("node-a")],
    [{ ...worker("node-a", "master-a"), worker_role: "REVIEWER", execution_shape: "TASK_FRAME" }],
    [terminal("master-a", "MASTER")], []);
  assert.deepEqual(Array.from(masterHostedReview.map((row) => row.role)), ["MASTER"]);
});

test("Todo execution requires its exact Worker assignment and observed activity", () => {
  const c = helpers();
  const rows = c.fleetBoundSessionRows("universe", [master("node-a")],
    [worker("node-a")], [terminal("master-a", "MASTER"), terminal("worker-a", "WORKER")], []);
  const matched = c.fleetTodoExecutorProjection({ todo_id: "todo-a", node_ref: "node-a" }, rows);
  assert.equal(matched.status, "LIVE");
  assert.equal(matched.executor.sessionAnchorRef, "worker-a");
  assert.equal(c.fleetTodoExecutingSessionActivity({ todo_id: "todo-a", state: "READY" }, rows).lane, "EXECUTING");
  assert.equal(c.fleetTodoExecutorProjection({ todo_id: "other", node_ref: "node-a" }, rows).status, "UNASSIGNED");
  assert.equal(c.fleetTodoExecutingSessionActivity({ todo_id: "other", state: "EXECUTING" }, rows).lane, "NOT_EXECUTING");
});

test("multiple exact Todo assignments surface a conflict", () => {
  const c = helpers();
  const rows = c.fleetBoundSessionRows("universe", [],
    [worker("node-a", "worker-a"), worker("node-a", "worker-b")],
    [terminal("worker-a", "WORKER"), terminal("worker-b", "WORKER")], []);
  const match = c.fleetTodoExecutorProjection({ todo_id: "todo-a" }, rows);
  assert.equal(match.status, "CONFLICT");
  assert.equal(match.sessions.length, 2);
});

test("Galaxy presence uses the same node bound rows", () => {
  const c = helpers();
  const rows = c.fleetBoundSessionRows("universe", [master("feature-1")], [],
    [terminal("master-a", "MASTER")], []);
  const presence = c.fleetGalaxySessionPresence({ node_id: "feat:feature-1" }, rows);
  assert.equal(presence.marker, "LIVE");
  assert.equal(presence.live.length, 1);
  assert.equal(c.fleetGalaxySessionPresence({ node_id: "feat:feature-2" }, rows).marker, "NONE");
});

test("node cards receive only sessions bound to their own node", () => {
  const c = helpers();
  const rows = c.fleetBoundSessionRows("universe", [master("node-a"), master("node-b", "master-b")],
    [worker("node-a")], [terminal("master-a", "MASTER"), terminal("master-b", "MASTER"),
      terminal("worker-a", "WORKER")], []);
  assert.deepEqual(Array.from(c.fleetNodeSessionRows("feat:node-a", rows).map((row) => row.sessionAnchorRef)),
    ["master-a", "worker-a"]);
  assert.deepEqual(Array.from(c.fleetNodeSessionRows("node-b", rows).map((row) => row.sessionAnchorRef)),
    ["master-b"]);
});

test("Fleet refreshes terminals and joins them to assignments in its render paths", () => {
  assert.match(source, /api\("\/v1\/terminals"\)/);
  assert.match(source, /function fleetLiveSessionRows\([\s\S]*fleetBoundSessionRows\(/);
  assert.match(source, /function renderLiveSessionProjectionSurfaces\(/);
  assert.match(source, /renderLiveSessionProjectionSurfaces\(\);/);
  assert.match(source, /rows\.push\(renderFleetNodeSessions\(graphNode, boundSessions\)\)/);
  assert.match(source, /section\.dataset\.nodeRef = nodeRef/);
  assert.match(source, /function fleetCardSession\(todo\)/);
  assert.doesNotMatch(source, /function fleetCardSession\(\)\s*\{[\s\S]{0,300}\.find\(\s*\(term\)\s*=>\s*String\(term\.project_id/);
});
