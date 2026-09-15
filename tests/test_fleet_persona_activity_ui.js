const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSource = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/app.js"), "utf8");
const terminalSource = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/terminals.js"), "utf8");

function evaluate(source, startMarker, endMarker, globals) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `${startMarker} should be present`);
  const context = vm.createContext({ ...globals });
  vm.runInContext(source.slice(start, end), context);
  return context;
}

test("terminal ownership projection distinguishes assigned, unassigned and conflicting anchors", () => {
  const context = evaluate(
    terminalSource,
    "function terminalPersonaAssignment(session)",
    "function renderTerminalDock()",
    {
      state: {
        personaAssignmentsStatus: "READY",
        personaAssignments: [
          { session_anchor_ref: "a", state: "ACTIVE", persona_id: "p1", node_ref: "feature-1", assignment_revision: 2 },
          { session_anchor_ref: "b", state: "ACTIVE", persona_id: "p2", node_ref: "feature-2", assignment_revision: 1 },
          { session_anchor_ref: "b", state: "ACTIVE", persona_id: "p3", node_ref: "feature-3", assignment_revision: 1 },
        ],
      },
      providerQuotaStateFor: () => "AVAILABLE",
    },
  );
  const assigned = context.terminalPersonaAssignment({ session_anchor_ref: "a" });
  assert.equal(assigned.state, "ASSIGNED");
  assert.equal(assigned.detail, "p1");
  assert.equal(assigned.personaId, "p1");
  assert.equal(assigned.nodeRef, "feature-1");
  assert.equal(assigned.revision, 2);
  assert.equal(context.terminalPersonaAssignment({ session_anchor_ref: "missing" }).state, "UNASSIGNED");
  assert.equal(context.terminalPersonaAssignment({ session_anchor_ref: "b" }).state, "ERROR");
});

test("terminal attention projection preserves quota, failure, waiting and unknown states", () => {
  const context = evaluate(
    terminalSource,
    "function terminalPersonaAssignment(session)",
    "function renderTerminalDock()",
    { state: {}, providerQuotaStateFor: (provider) => provider === "GROK" ? "EXHAUSTED" : "AVAILABLE" },
  );
  assert.equal(context.terminalAttentionProjection({ provider: "GROK", host_turn_state: "WORKING" }).state, "QUOTA_BLOCKED");
  assert.equal(context.terminalAttentionProjection({ provider: "CODEX", host_turn_state: "FAILED" }).state, "FAILED");
  assert.equal(context.terminalAttentionProjection({ provider: "CODEX", host_turn_state: "WAITING_INPUT" }).state, "WAITING_INPUT");
  assert.equal(context.terminalAttentionProjection({ provider: "CODEX" }).state, "UNKNOWN");
});

test("Fleet binding status never turns an unavailable assignment read into UNASSIGNED", () => {
  const context = evaluate(
    appSource,
    "function fleetAssignmentRows()",
    "function fleetTerminalLabel(terminal)",
    { state: { personaAssignmentsStatus: "ERROR", personaAssignmentsError: "HTTP_503", personaAssignments: null } },
  );
  assert.equal(context.fleetNodeAssignments("feature-1").status, "UNKNOWN");
  context.state.personaAssignmentsStatus = "READY";
  context.state.personaAssignments = [];
  assert.equal(context.fleetNodeAssignments("feature-1").status, "READY");
  assert.equal(context.fleetNodeAssignments("feature-1").active.length, 0);
});

test("Activity labels authoritative lifecycle events and exposes explicit navigation hooks", () => {
  const context = evaluate(
    appSource,
    "function activityEventCategory(event)",
    "function activityEventLineage(payload)",
    {},
  );
  assert.equal(context.activityEventCategory({ event_type: "PROVIDER_QUOTA_STOP", payload: { state: "QUOTA_EXHAUSTED" } }), "QUOTA");
  assert.equal(context.activityEventTitle({ event_type: "PERSONA_ASSIGNMENT_CHANGED", payload: { state: "ACTIVE" } }), "Persona assignment ACTIVE");
  assert.equal(context.activityEventTitle({ event_type: "MASTER_MESSAGE_LIFECYCLE", payload: { outcome: "DONE" } }), "Master message DONE");
  const start = appSource.indexOf("function renderActivityContextLinks(event)");
  const end = appSource.indexOf("function renderProjectActivityRow(entry)", start);
  const helper = appSource.slice(start, end);
  assert.match(helper, /payload\.node_ref/);
  assert.match(helper, /payload\.todo_id/);
  assert.match(helper, /payload\.task_frame_id/);
  assert.match(helper, /payload\.session_anchor_ref/);
});

test("Persona Library source has no operational assignment controls or terminal fallback", () => {
  const start = appSource.indexOf("async function renderPersona()");
  const end = appSource.indexOf("function renderMemoryLegacy()", start);
  const persona = appSource.slice(start, end);
  assert.doesNotMatch(persona, /persona\.assign(?:ments-list)?/);
  assert.doesNotMatch(persona, /persona-automation/);
  assert.match(persona, /Persona Library/);
  const index = fs.readFileSync(path.join(__dirname, "../tools/universe_ui/index.html"), "utf8");
  assert.doesNotMatch(index, /action-inbox-(?:button|dialog)/);
});
