const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/app.js"), "utf8");

class El {
  constructor(tag, text = "") { this.tagName = tag.toUpperCase(); this.children = []; this._text = text; this.listeners = {}; this.disabled = false; this.className = ""; }
  append(...xs) { this.children.push(...xs.filter(Boolean)); }
  replaceChildren(...xs) { this.children = xs.filter(Boolean); }
  addEventListener(n, f) { this.listeners[n] = f; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(""); }
}
function buttons(e) { return e.children.flatMap(x => x.tagName === "BUTTON" ? [x] : buttons(x)); }
function make(run) {
  const context = vm.createContext({
    state: { fleetAutomationByNode: { A: { status: "READY", run: run && { run_id: "run-1", revision: 1, ...run } } }, selectedProject: { project_id: "P" } },
    node: (tag, cls, text = "") => { const e = new El(tag, text); e.className = cls; return e; },
    fleetStallAge: () => 12, ensureFleetAutomation: async () => {}, toast: (...args) => { context.toastArgs = args; },
    crypto: { randomUUID: () => "test-request" },
    invokeServerAction: async (...args) => { context.invokeArgs = args; return {}; },
  });
  const start = source.indexOf("function fleetAutomationRequestId");
  const end = source.indexOf("// Project-wide Conductor automation", start);
  vm.runInContext(source.slice(start, end), context);
  return { context, view: context.renderFleetAutomationControls("A", { session_anchor_ref: "a" }) };
}

test("renders each stall kind and clears the reported queue blocker", async () => {
  const { context, view } = make({ state: "RUNNING", stall: { items: [
    { kind: "QUEUE_BLOCKED", message_id: "message-123456789", body: "hello", waiting_count: 2 },
    { kind: "HOST_PERMISSION_PENDING", request_id: "req" },
    { kind: "HOST_ORPHANED", task_frame_id: "frame", phase: "WAITING" },
  ] } });
  assert.match(view.textContent, /STALLED/);
  assert.match(view.textContent, /notification/);
  assert.match(view.textContent, /permission/);
  assert.match(view.textContent, /frame/);
  assert.match(view.textContent, /message-1234/);
  assert.match(view.textContent, /hello/);
  const clear = buttons(view).find(b => /Clear blocked/.test(b.textContent));
  assert.ok(clear); clear.listeners.click(); await new Promise(setImmediate);
  assert.equal(context.invokeArgs[1].message_id, "message-123456789");
});

test("shows Resume for WAITING and requires inline confirmation for STOPPED recovery", async () => {
  const waiting = make({ state: "WAITING" });
  assert.ok(buttons(waiting.view).some(b => b.textContent === "Resume"));
  const stopped = make({ state: "STOPPED" });
  const recover = buttons(stopped.view).find(b => /recover stopped/.test(b.textContent));
  assert.ok(recover); recover.listeners.click();
  const yes = buttons(stopped.view).find(b => b.textContent === "Confirm recover");
  assert.ok(yes); yes.listeners.click(); await new Promise(setImmediate);
  assert.ok(stopped.context.invokeArgs, JSON.stringify(stopped.context.toastArgs));
  assert.equal(stopped.context.invokeArgs[0], "persona.automation.resume");
  assert.equal(stopped.context.invokeArgs[1].recover_stopped, true);
});

test("renders no stall UI when diagnosis is null", () => {
  const { view } = make({ state: "RUNNING", stall: null });
  assert.doesNotMatch(view.textContent, /STALLED|Clear blocked/);
});
