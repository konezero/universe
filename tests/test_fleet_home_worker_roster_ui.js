const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/app.js"), "utf8");

test("Fleet home fetches project-wide assignments and deduplicates node rows", async () => {
  const calls = [];
  const context = vm.createContext({
    state: {
      selectedProject: { project_id: "P1" },
      fleetWorkerAssignmentsByNode: {
        feature1: { status: "READY", projectId: "P1", rows: [
          { assignment_id: "same", state: "ACTIVE", node_ref: "feature1", session_anchor_ref: "s1" },
        ] },
      },
      fleetWorkerAssignmentsProject: null,
    },
    invokeServerAction: async (name, payload) => {
      calls.push([name, payload]);
      return { assignments: [
        { assignment_id: "same", state: "ACTIVE", node_ref: "feature1", session_anchor_ref: "s1" },
        { assignment_id: "project-only", state: "ACTIVE", session_anchor_ref: "s2" },
        { assignment_id: "ended", state: "ENDED", session_anchor_ref: "s3" },
      ] };
    },
    renderIntegratedHome() {},
    homeNodeRefKey: (id) => id,
  });
  const start = source.indexOf("function fleetWorkerAssignments(featureId)");
  const end = source.indexOf("async function ensureFleetAutomation", start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);

  await context.ensureFleetWorkerProjectAssignments();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "fleet.worker-assignments-list");
  assert.equal(calls[0][1].project_id, "P1");
  const rows = context.fleetWorkerAssignmentProjectRows([
    { kind: "FEATURE", node_id: "feature1" },
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows.find((row) => row.assignment.assignment_id === "project-only").graphNode, null);
});

test("Fleet Peek attaches an excluded Worker/Reviewer terminal", () => {
  const selected = [];
  const context = vm.createContext({
    state: { terminals: [] },
    selectTerminalTab: (id) => selected.push(id),
    expandConversationLayer() {},
  });
  const start = source.indexOf("function peekFleetWorkerTerminal(terminal)");
  const end = source.indexOf("function bindFleetNodeMaster", start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);
  const terminal = { terminal_id: "term-worker-2", session_anchor_ref: "s2" };
  assert.equal(context.peekFleetWorkerTerminal(terminal), true);
  assert.deepEqual(selected, ["term-worker-2"]);
  assert.equal(context.state.terminals.length, 1);
  assert.equal(context.state.terminals[0].terminal_id, terminal.terminal_id);
});

test("Fleet home soft refresh updates the running Worker/Reviewer count", async () => {
  let assignments = [
    { assignment_id: "worker-1", state: "ACTIVE", session_anchor_ref: "s1" },
    { assignment_id: "reviewer-1", state: "ACTIVE", session_anchor_ref: "s2" },
  ];
  const context = vm.createContext({
    state: {
      selectedProject: { project_id: "P1" },
      fleetHomeRefreshInFlight: false,
      fleetWorkerAssignmentsByNode: {},
      fleetWorkerAssignmentsProject: { projectId: "P1", status: "READY", rows: [] },
      fleetHomeRefreshWorkerSignature: null,
      personaAssignmentsProjectId: "P1",
      personaAssignments: [],
      personaAssignmentsStatus: "READY",
      fleetHomeRefreshTodoSignature: null,
      fleetHomeRefreshGoalSignature: null,
      fleetHomeRefreshAssignmentSignature: null,
      todos: [],
      goals: [],
      unassignedTodos: [],
    },
    fleetHomeSoftRefreshActive: () => true,
    api: async () => ({ todos: [] }),
    apiWithTimeout: async () => ({ goals: [], unassigned_todos: [] }),
    invokeServerAction: async (name) => {
      if (name === "fleet.worker-assignments-list") return { assignments };
      if (name === "persona.assignments-list") return { assignments: [] };
      return {};
    },
    homeNodes: () => [],
    homeNodeRefKey: (id) => id,
    homeSelectedNode: () => null,
    refreshFleetProjectConductorProjection: async () => {},
    renderIntegratedHome: () => {},
  });
  const start = source.indexOf("async function refreshFleetHomeSoft()");
  const end = source.indexOf("function renderIntegratedHome()", start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end), context);

  await context.refreshFleetHomeSoft();
  assert.equal(context.state.fleetWorkerAssignmentsProject.rows.length, 2);
  assignments = [assignments[0]];
  await context.refreshFleetHomeSoft();
  assert.equal(context.state.fleetWorkerAssignmentsProject.rows.length, 1);
  assert.equal(context.state.fleetWorkerAssignmentsProject.rows[0].assignment_id, "worker-1");
});

const css = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/styles.css"), "utf8");
assert.match(css, /\.fleet-home-worker-summary/);
assert.match(css, /\.fleet-home-worker-row/);
