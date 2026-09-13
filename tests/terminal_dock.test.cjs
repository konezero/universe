const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function harness() {
  const state = { terminals: [], terminalSurfaces: {}, activeTerminalId: null };
  const context = vm.createContext({
    state, elements: { terminalStage: { classList: { toggle() {} } } },
    window: { location: { search: "" }, clearTimeout() {}, clearInterval() {}, cancelAnimationFrame() {} },
    navigator: { userAgent: "Windows", platform: "Win32" },
    document: {}, URLSearchParams, setTimeout() {}, TextEncoder, performance,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../tools/universe_ui/terminals.js"), "utf8"), context);
  const disposed = [];
  context.ensureTerminalSurface = session => {
    state.terminalSurfaces[session.terminal_id] ||= {
      element: { hidden: true, remove() {} },
      term: { dispose() { disposed.push(session.terminal_id); } },
      renderQueue: [], renderIdleWaiters: [],
    };
    return state.terminalSurfaces[session.terminal_id];
  };
  for (const name of ["renderTerminalDock", "startProviderQuotaPolling", "applyCliDockTitle",
      "loadResumableSessions", "renderReattachBanner", "renderTerminalNewMenu"]) {
    context[name] = () => {};
  }
  return { context, state, disposed };
}

test("stream reconnect waits for its live Host, then opens a new output socket", async () => {
  const { context } = harness();
  const timers = [];
  const rendered = [];
  let connections = 0;
  let payload = {
    terminals: [],
    hosts: [{ host_session_ref: "host-a", runtime_state: "LIVE",
      reconnect_eligible: true, compatibility: "CURRENT" }],
  };
  context.window.setTimeout = callback => { timers.push(callback); return timers.length; };
  context.api = async route => route === "/health" ? { status: "READY" } : payload;
  context.session = { terminal_id: "term-a", host_session_ref: "host-a" };
  context.surface = {};
  context.queueTerminalRender = (_surface, text) => rendered.push(text);
  context.connectSocket = () => { connections += 1; };
  // Exercise the production reconnect callback with controlled API state and timers.
  const source = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/terminals.js"), "utf8");
  const start = source.indexOf("  const waitForServiceHealth =");
  const end = source.indexOf("  const connectSocket =", start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext("let socketDisposed = false; let reconnectTimer = 0; let reconnectAttempts = 0;\n"
    + source.slice(start, end) + "\nthis.reconnect = scheduleSocketReconnect;", context);
  context.reconnect({ serviceRestart: true });
  await timers.shift()();
  assert.equal(connections, 0);
  assert.equal(timers.length, 1);
  assert.deepEqual(rendered, []);
  payload.terminals = [{ terminal_id: "term-a", state: "LIVE" }];
  await timers.shift()();
  assert.equal(connections, 1);
  assert.equal(timers.length, 0);

  // A different live Host must not keep a terminated session retrying forever.
  payload = { terminals: [], hosts: [{ host_session_ref: "host-other", runtime_state: "LIVE",
    reconnect_eligible: true, compatibility: "CURRENT" }] };
  context.reconnect({ serviceRestart: true });
  await timers.shift()();
  assert.equal(timers.length, 0);
  assert.equal(connections, 1);
  assert.match(rendered.at(-1), /session closed/);
});

test("grid uses the tab membership, creates every current pane, and hides stale panes", () => {
  const { context, state } = harness();
  state.terminals = ["codex-conductor", "claude-master", "codex-master"].map(terminal_id => ({ terminal_id }));
  context.ensureTerminalSurface({ terminal_id: "stale" });
  state.activeTerminalId = "codex-master";
  state.terminalGrid = true;
  context.applyTerminalGridLayout();
  assert.equal(state.terminalSurfaces.stale.element.hidden, true);
  for (const session of state.terminals) assert.equal(state.terminalSurfaces[session.terminal_id].element.hidden, false);
  state.terminalGrid = false;
  context.applyTerminalGridLayout();
  assert.equal(state.terminalSurfaces["codex-master"].element.hidden, false);
  assert.equal(state.terminalSurfaces["codex-conductor"].element.hidden, true);
  assert.equal(state.terminalSurfaces.stale.element.hidden, true);
});

test("polling removes stale surfaces without stealing an active Codex master focus", async () => {
  const { context, state, disposed } = harness();
  state.terminals = [{ terminal_id: "codex-master", host_session_ref: "master" }];
  state.activeTerminalId = "codex-master";
  context.ensureTerminalSurface({ terminal_id: "stale" });
  context.ensureTerminalSurface({ terminal_id: "codex-master" });
  const incoming = [{ terminal_id: "codex-conductor", host_session_ref: "conductor" }, ...state.terminals];
  context.api = async () => ({
    terminals: incoming,
    hosts: ["master", "conductor"].map(host_session_ref => ({
      host_session_ref, runtime_state: "LIVE", reconnect_eligible: true, compatibility: "CURRENT",
    })),
  });
  const selections = [];
  context.selectTerminalTab = id => { selections.push(id); state.activeTerminalId = id; };
  await context.loadTerminalTabs();
  assert.equal(state.activeTerminalId, "codex-master");
  assert.deepEqual(selections, []);
  assert.deepEqual(disposed, ["stale"]);
  assert.equal(state.terminalSurfaces.stale, undefined);
  assert.equal(state.terminals.length, 2);
});
