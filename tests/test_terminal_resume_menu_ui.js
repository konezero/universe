const fs = require("fs");
const vm = require("vm");
const assert = require("assert/strict");
const source = fs.readFileSync("tools/universe_ui/terminals.js", "utf8");
const menu = { matches: () => false, children: [], replaceChildren() { this.children = []; }, append(x) { this.children.push(x); } };
const created = [];
const apiCalls = [];
const context = {
  state: { projects: [{ project_id: "demo", project_root: "C:/demo" }], resumableSessions: { resume: [
    { session_id: "session_closed", session_anchor_ref: "exact-anchor", project_id: "demo", mode: "MASTER", provider: "CODEX", label: "demo MASTER CODEX", visibility: "VISIBLE", visibility_revision: 0 }
  ] } },
  document: { querySelector: () => menu, createElement: () => ({ dataset: {}, listeners: {}, children: [], setAttribute() {}, append(...items) { this.children.push(...items); }, addEventListener(name, fn) { this.listeners[name] = fn; } }) },
  currentReattachHosts: () => [], closeTerminalNewMenu() {}, renderReattachBanner() {},
  api: async (path, options) => {
    apiCalls.push({ path, options });
    const session = context.state.resumableSessions.resume[0];
    session.visibility = options.body.visibility;
    session.visibility_revision += 1;
    return { status: "RESUMABLE_SESSION_VISIBILITY_UPDATED" };
  },
  loadResumableSessions: async () => context.state.resumableSessions,
  createTerminalTab: async (coordinate, session) => created.push({ coordinate, session }), toast: (message) => { throw new Error(message); }
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("function resumeSessionExcluded("), source.indexOf("function closeTerminalNewMenu()")), context);
vm.runInContext(source.slice(source.indexOf("async function resumeRecordedSession("), source.indexOf("async function reattachLiveHost(")), context);
(async () => {
  context.renderTerminalNewMenu();
  assert.equal(menu.children.length, 2);
  assert.equal(menu.children[1].children[0].textContent, "Resume demo MASTER CODEX");
  menu.children[1].children[0].listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(created.length, 1);
  assert.equal(created[0].session.session_id, "session_closed");
  assert.equal(created[0].session.session_anchor_ref, "exact-anchor");
  assert.equal(created[0].session.host_session_ref, undefined);
  assert.equal(created[0].coordinate.project.project_root, "C:/demo");

  menu.children[1].children[1].listeners.click({stopPropagation() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(JSON.stringify(apiCalls[0]), JSON.stringify({
    path: "/v1/sessions/resumable/visibility",
    options: { method: "POST", body: {
      project_id: "demo", session_id: "session_closed",
      visibility: "HIDDEN", expected_revision: 0,
    } },
  }));
  assert.equal(menu.children[1].textContent, "제외 항목 보기 (1)");
  assert.equal(created.length, 1, "exclude must never resume or terminate a session");
  delete context.state.showExcludedResumeSessions;
  context.renderTerminalNewMenu();
  assert.equal(menu.children[1].textContent, "제외 항목 보기 (1)");
  menu.children[1].listeners.click({stopPropagation() {}});
  assert.equal(menu.children[1].children[0].disabled, true);
  assert.equal(menu.children[1].children[1].textContent, "복원");
  menu.children[1].children[1].listeners.click({stopPropagation() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(apiCalls[1].options.body.visibility, "VISIBLE");
  assert.equal(apiCalls[1].options.body.expected_revision, 1);
  assert.equal(menu.children[1].children[0].textContent, "Resume demo MASTER CODEX");
  assert.equal(source.includes("universe.resume.excluded.v1"), false);
  context.state.resumableSessions.resume = [];
  context.renderTerminalNewMenu();
  assert.equal(menu.children.length, 1);
  console.log("Resume menu uses durable server visibility and preserves the recorded session");
})().catch(error => { console.error(error); process.exitCode = 1; });
