const fs = require("fs");
const vm = require("vm");
const assert = require("assert/strict");
const source = fs.readFileSync("tools/universe_ui/terminals.js", "utf8");
const menu = { matches: () => false, children: [], replaceChildren() { this.children = []; }, append(x) { this.children.push(x); } };
const created = [];
const apiCalls = [];
const visible = { session_id: "session_closed", session_anchor_ref: "exact-anchor", project_id: "demo", mode: "MASTER", provider: "CODEX", label: "demo MASTER CODEX", visibility: "VISIBLE", visibility_revision: 0 };
const context = {
  state: { projects: [{ project_id: "demo", project_root: "C:/demo" }], resumableSessions: { resume: [visible], excluded: [] } },
  document: { querySelector: () => menu, createElement: () => ({ dataset: {}, listeners: {}, children: [], setAttribute() {}, append(...items) { this.children.push(...items); }, addEventListener(name, fn) { this.listeners[name] = fn; } }) },
  currentReattachHosts: () => [], closeTerminalNewMenu() {}, renderReattachBanner() {},
  api: async (path, options) => {
    apiCalls.push({ path, options });
    return {
      status: "RESUMABLE_SESSION_VISIBILITY_UPDATED",
      visibility: options.body.visibility,
      revision: options.body.expected_revision + 1,
    };
  },
  loadResumableSessions: async () => new Promise(() => {}),
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
  assert.equal(menu.children[1].textContent, "\uc81c\uc678 \ud56d\ubaa9 \ubcf4\uae30 (1)");
  assert.equal(created.length, 1, "exclude must never resume or terminate a session");
  delete context.state.showExcludedResumeSessions;
  context.renderTerminalNewMenu();
  assert.equal(menu.children[1].textContent, "\uc81c\uc678 \ud56d\ubaa9 \ubcf4\uae30 (1)");
  menu.children[1].listeners.click({stopPropagation() {}});
  assert.equal(menu.children[1].children[0].disabled, true);
  assert.equal(menu.children[1].children[1].textContent, "\ubcf5\uc6d0");
  menu.children[1].children[1].listeners.click({stopPropagation() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(apiCalls[1].options.body.visibility, "VISIBLE");
  assert.equal(apiCalls[1].options.body.expected_revision, 1);
  assert.equal(menu.children[1].children[0].textContent, "Resume demo MASTER CODEX");
  assert.equal(source.includes('localStorage.setItem("universe.resume.excluded.v1")'), false);
  assert.equal(source.includes('localStorage.removeItem("universe.resume.excluded.v1")'), true);
  assert.equal(source.includes("async function refreshAfterHostTermination(hostId)"), true);
  assert.equal(source.includes("openHosts.has(href)"), true, "server Host-file rows must be filtered against open dock tabs");
  assert.equal(source.includes("openAnchors.has(anchor)"), true, "open Anchors must not reappear as Re-attach rows");
  assert.equal(source.includes("function joinReattachHost("), false, "UI must not fill missing Host identity from other projections");
  assert.equal(source.includes("Host binding is incomplete; Re-attach is refused."), true);
  assert.equal(source.includes("Session binding is incomplete; Resume is refused."), true);
  assert.equal(source.includes("Host unavailable \\u00b7"), true, "bound invalid Hosts remain typed diagnostics and can be excluded");
  assert.equal(source.includes("state.resumableSessionsPromise"), true, "concurrent menu and startup loads must share one request");
  assert.equal(source.includes("Resume \ubaa9\ub85d \ubd88\ub7ec\uc624\ub294 \uc911..."), true, "slow initial loads must render an explicit loading row");
  assert.equal(source.includes("selectTerminalTab(visible[0].terminal_id);\n      return;"), false);
  const liveHost = {
    kind: "REATTACH", host_session_ref: "host-live", session_id: "session-live",
    session_anchor_ref: "anchor-live", project_id: "demo", label: "demo MASTER CODEX",
    visibility: "VISIBLE", visibility_revision: 0,
  };
  const invalidHost = {
    kind: "INCOMPATIBLE", host_session_ref: "host-invalid", session_id: "session-invalid",
    session_anchor_ref: "anchor-invalid", project_id: "demo", label: "Host binding invalid",
    visibility: "VISIBLE", visibility_revision: 0,
  };
  context.state.showExcludedResumeSessions = false;
  context.state.resumableSessions = { reattach: [liveHost], resume: [], excluded: [], incompatible: [invalidHost] };
  context.currentReattachHosts = () => context.state.resumableSessions.reattach;
  context.renderTerminalNewMenu();
  assert.equal(menu.children.length, 3);
  assert.equal(menu.children[1].children[2].textContent, "\ubaa9\ub85d \uc81c\uc678");
  menu.children[1].children[2].listeners.click({stopPropagation() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(apiCalls[2].options.body.session_id, "session-live");
  assert.equal(context.state.resumableSessions.excluded[0].kind, "REATTACH");
  assert.equal(menu.children.length, 3, "excluded live Host disappears while the incompatible Host and toggle remain");
  menu.children[2].listeners.click({stopPropagation() {}});
  assert.equal(menu.children[2].children[1].textContent, "\ubcf5\uc6d0");
  menu.children[2].children[1].listeners.click({stopPropagation() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(context.state.resumableSessions.reattach[0].host_session_ref, "host-live");
  context.state.resumableSessions.reattach = [];
  context.renderTerminalNewMenu();
  assert.equal(menu.children[1].children[1].textContent, "\ubaa9\ub85d \uc81c\uc678");
  menu.children[1].children[1].listeners.click({stopPropagation() {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(apiCalls[4].options.body.session_id, "session-invalid");
  assert.equal(context.state.resumableSessions.incompatible.length, 0);

  context.state.showExcludedResumeSessions = false;
  context.state.resumableSessions.resume = [];
  context.renderTerminalNewMenu();
  assert.equal(menu.children.length, 2, "only New session and the excluded-items toggle remain");
  console.log("Resume menu loads on startup and restores server-backed excluded sessions");
})().catch(error => { console.error(error); process.exitCode = 1; });
