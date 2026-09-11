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
