const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/app.js"), "utf8");

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.className = "";
    this.style = {}; this.dataset = {}; this.hidden = false; this.listeners = {};
    this.classList = { add: (...names) => { this.className += ` ${names.join(" ")}`; } };
  }
  append(...items) { this.children.push(...items.filter(Boolean)); }
  prepend(...items) { this.children.unshift(...items.filter(Boolean)); }
  replaceChildren(...items) { this.children = items.filter(Boolean); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  dispatchEvent(event) { this.listeners[event.type]?.(event); }
  get childElementCount() { return this.children.length; }
  set textContent(value) { this._text = String(value ?? ""); this.children = []; }
  get textContent() { return this._text || this.children.map((child) => child.textContent || "").join(""); }
}

function textOf(element) { return element.textContent; }
function buttons(element) { return element.children.flatMap((child) => child.tagName === "BUTTON" ? [child] : buttons(child)); }

function homeContext({ assignments, hosts }) {
  const list = new FakeElement("div");
  const head = new FakeElement("h3");
  const document = { createElement: (tag) => new FakeElement(tag), createTextNode: (text) => new FakeElement(text), querySelector: (selector) => selector === "#home-node-list" ? list : selector === "#home-nodes-head" ? head : null };
  const state = {
    selectedProject: { project_id: "P1" }, homeCollapsed: new Set(), fleetWorkerAssignmentsProject: { projectId: "P1", status: "READY", rows: assignments },
    fleetTaskFrameHosts: { projectId: "P1", status: "READY", rows: hosts }, fleetWorkerAssignmentsByNode: {}, personaAssignmentsStatus: "READY",
  };
  const context = vm.createContext({
    document, state, CustomEvent: class { constructor(type) { this.type = type; this.target = { closest: () => null }; } },
    homeNodes: () => [{ node_id: "A", kind: "FEATURE", state: "ADOPTED", data: {} }, { node_id: "B", kind: "FEATURE", state: "ADOPTED", data: {} }],
    homeNodeRefKey: (id) => id, homeNodeTitleWithId: (item) => `Node ${item.node_id}`, homeNodeTodos: () => [],
    homeTodoExecutor: () => null, homeTodoBlocked: () => false,
    homeSelectedNode: () => null, projectRoomsList: () => [], renderFleetNodeTeamSummary: () => null,
    ensureHomeNodeOwners: () => {}, loadPersonaProjectProjection: async () => {}, isMobileView: () => false, homeChev: () => new FakeElement("span"),
  });
  const nodeStart = source.indexOf("function node(tag, className, text)");
  const nodeEnd = source.indexOf("function compactModelRef", nodeStart);
  const cardStart = source.indexOf("function homeCard(");
  const cardEnd = source.indexOf("function nodeKindLabel", cardStart);
  const rosterStart = source.indexOf("function fleetWorkerAssignmentKey(");
  const rosterEnd = source.indexOf("function renderHomeNodes", rosterStart);
  const renderEnd = source.indexOf("function nodeKindLabel", rosterEnd);
  vm.runInContext(source.slice(nodeStart, nodeEnd) + source.slice(cardStart, cardEnd) + source.slice(rosterStart, rosterEnd) + source.slice(rosterEnd, renderEnd), context);
  return { context, list, head };
}

test("Fleet home renders merged legacy and live Host counts on the owning node only", () => {
  const { context, list, head } = homeContext({
    assignments: [
      { assignment_id: "legacy-a", state: "ACTIVE", node_ref: "A" },
      { assignment_id: "legacy-a", state: "ACTIVE", node_ref: "A" },
      { assignment_id: "unassigned", state: "ACTIVE" },
    ],
    hosts: [
      { task_frame_id: "frame-a", node_ref: "A", alive: true },
      { task_frame_id: "frame-b", node_ref: "B", alive: true },
      { task_frame_id: "frame-dead", node_ref: "A", alive: false },
      { task_frame_id: "frame-none", alive: true },
    ],
  });
  context.renderHomeNodes(null);
  assert.equal(list.children.length, 2, "only node cards are rendered");
  assert.match(textOf(list.children[0]), /Running Worker \/ Reviewer2/);
  assert.match(textOf(list.children[1]), /Running Worker \/ Reviewer1/);
  assert.equal((textOf(list.children[0]).match(/Running Worker \/ Reviewer/g) || []).length, 1);
  assert.match(textOf(head), /Worker\/Reviewer 2/);
});

test("Fleet home omits zero counts and unassigned header when there are no running rows", () => {
  const { context, list, head } = homeContext({ assignments: [], hosts: [] });
  context.renderHomeNodes(null);
  assert.equal(list.children.length, 2);
  assert.doesNotMatch(textOf(list), /Running Worker \/ Reviewer/);
  assert.doesNotMatch(textOf(head), /Worker\/Reviewer/);
});

test("Fleet home deduplicates a legacy assignment and its live Host across sources", () => {
  const { context, list } = homeContext({
    assignments: [
      { assignment_id: "legacy-frame-a", task_frame_id: "frame-a", state: "ACTIVE", node_ref: "A" },
      { assignment_id: "legacy-frame-b", task_frame_id: "frame-b", state: "ACTIVE", node_ref: "A" },
    ],
    hosts: [
      { task_frame_id: "frame-a", node_ref: "A", alive: true },
    ],
  });
  context.renderHomeNodes(null);
  assert.equal(list.children.length, 2);
  assert.match(textOf(list.children[0]), /Running Worker \/ Reviewer2/);
  assert.doesNotMatch(textOf(list.children[1]), /Running Worker \/ Reviewer/);
});

test("Todo Task Frame section renders latest-first Boss-room buttons and opens the room", () => {
  const calls = [];
  const context = homeContext({ assignments: [], hosts: [] }).context;
  context.state.fleetTaskFrameHosts = { status: "READY", rows: [
    { task_frame_id: "older", todo_id: "todo-1", room_id: "room-old", launched_at: "2026-01-01" },
    { task_frame_id: "latest", todo_id: "todo-1", room_id: "room-new", launched_at: "2026-01-02" },
    { task_frame_id: "other", todo_id: "todo-2", room_id: "room-other", launched_at: "2026-01-03" },
  ] };
  context.todoOwnershipProjection = () => null;
  context.openRoomObservation = async (room) => calls.push(room);
  const start = source.indexOf("function renderTodoOwnership(todo)");
  const end = source.indexOf("function todoLineageLabel", start);
  vm.runInContext(source.slice(start, end), context);
  const section = context.renderTodoOwnership({ todo_id: "todo-1", state: "TODO" });
  const rendered = buttons(section);
  assert.deepEqual(rendered.map((button) => button.dataset.taskFrameId), ["latest", "older"]);
  rendered[0].listeners.click();
  return Promise.resolve().then(() => assert.deepEqual(calls, ["room-new"]));
});
