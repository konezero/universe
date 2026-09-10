import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const app = readFileSync(new URL("../tools/universe_ui/app.js", import.meta.url), "utf8");
const start = app.indexOf("function renderProviderHookTarget(");
const end = app.indexOf("function renderHostToolSettings()", start);
assert.ok(start >= 0 && end > start);
const setupSource = app.slice(start, end);

function harness(targetProject, responder = async () => ({
  providers: { CODEX: { status: "WRITTEN" } },
})) {
  const calls = [];
  const messages = [];
  const elements = {
    setupProviderHooks: { disabled: false },
    setupProviderHooksStatus: { textContent: "" },
    providerHooksError: { textContent: "" },
    providerHooksPath: { textContent: "" },
    providerHooksTarget: { value: targetProject || "", disabled: false },
    providerHooksProviders: {
      disabled: false,
      selected: ["CODEX", "GROK", "CLAUDE"],
      querySelector() { return this.selected.length ? {} : null; },
      querySelectorAll() { return this.selected.map(value => ({ value })); },
    },
  };
  const state = { selectedProject: { project_id: "universe" } };
  const projects = ["career", "rendezvous", "universe"].map(project_id => ({
    project_id, project_root: `C:/workspace/${project_id}`,
  }));
  const context = vm.createContext({
    elements,
    state,
    visibleProjects: () => projects,
    api: async (url, options) => {
      calls.push(JSON.parse(JSON.stringify({ url, ...options })));
      return responder();
    },
    toast: (...args) => messages.push(args),
  });
  vm.runInContext(setupSource, context);
  return { context, elements, state, calls, messages };
}

test("installs hooks in each selected repository, never in global config", async () => {
  const h = harness("career");
  for (const projectId of ["career", "rendezvous", "universe"]) {
    h.elements.providerHooksTarget.value = projectId;
    await h.context.setupProviderHooks();
    assert.deepEqual(h.calls.at(-1), {
      url: "/v1/settings/setup-provider-hooks",
      method: "POST",
      body: {
        providers: ["CODEX", "GROK", "CLAUDE"],
        global: false,
        project_id: projectId,
      },
    });
    assert.match(h.elements.setupProviderHooksStatus.textContent, new RegExp(projectId));
    assert.equal(h.elements.setupProviderHooks.disabled, false);
  }
});

test("requires an explicit connected repository without falling back to Universe", async () => {
  const h = harness(null);
  await h.context.setupProviderHooks();
  assert.equal(h.calls.length, 0);
  assert.match(h.elements.providerHooksError.textContent, /Select a connected repository/);
  h.elements.providerHooksTarget.value = "disconnected";
  await h.context.setupProviderHooks();
  assert.equal(h.calls.length, 0);
});

test("keeps the original installation target while selection changes", async () => {
  let finish;
  const h = harness("career", () => new Promise(resolve => { finish = resolve; }));
  const pending = h.context.setupProviderHooks();
  assert.equal(h.elements.setupProviderHooks.disabled, true);
  assert.equal(h.elements.providerHooksTarget.disabled, true);
  assert.equal(h.elements.providerHooksProviders.disabled, true);
  await h.context.setupProviderHooks();
  assert.equal(h.calls.length, 1);
  h.state.selectedProject = { project_id: "rendezvous" };
  finish({ providers: { CODEX: { status: "CURRENT" } } });
  await pending;
  assert.equal(h.calls[0].body.project_id, "career");
  assert.match(h.elements.setupProviderHooksStatus.textContent, /career/);
  assert.equal(h.elements.setupProviderHooks.disabled, false);
});

test("reports an installation failure and re-enables the button", async () => {
  const h = harness("career", async () => { throw new Error("Project unavailable"); });
  await h.context.setupProviderHooks();
  assert.equal(h.elements.providerHooksError.textContent, "Project unavailable");
  assert.equal(h.elements.setupProviderHooksStatus.textContent, "Project unavailable");
  assert.equal(h.elements.setupProviderHooks.disabled, false);
  assert.deepEqual(h.messages, [["Project unavailable", true]]);
});

test("installs only selected CLIs and rejects an empty selection", async () => {
  const h = harness("career");
  h.elements.providerHooksProviders.selected = [];
  h.context.renderProviderHookTarget();
  assert.equal(h.elements.setupProviderHooks.disabled, true);
  await h.context.setupProviderHooks();
  assert.equal(h.calls.length, 0);
  h.elements.providerHooksProviders.selected = ["CODEX"];
  await h.context.setupProviderHooks();
  assert.deepEqual(h.calls[0].body.providers, ["CODEX"]);
});

test("keeps provider-level installation failures visible", async () => {
  const h = harness("career", async () => ({
    providers: { CODEX: { status: "ERROR", detail: "Access denied" } },
  }));
  await h.context.setupProviderHooks();
  assert.match(h.elements.setupProviderHooksStatus.textContent, /Access denied/);
  assert.match(h.elements.providerHooksError.textContent, /could not be installed/);
  assert.equal(h.messages[0][1], true);
});
