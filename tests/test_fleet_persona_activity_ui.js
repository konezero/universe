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
  assert.equal(context.terminalAttentionProjection({
    provider: "CODEX",
    state: "LIVE",
    host_turn_state: {
      state: "WORKING",
      last_event: "PROMPT_SUBMITTED",
      latest_delivery: { phase: "NATIVE_QUEUED", error_code: null },
    },
  }).state, "WORKING");
  assert.equal(context.terminalAttentionProjection({
    provider: "CODEX",
    state: "LIVE",
    host_turn_state: {
      state: "IDLE",
      last_event: "TERMINAL_REATTACHED",
      latest_delivery: null,
    },
  }).state, "RECOVERED");
  assert.equal(context.terminalAttentionProjection({
    provider: "CODEX",
    state: "LIVE",
    host_turn_state: {
      state: "IDLE",
      last_event: "IDLE",
      latest_delivery: { phase: "NATIVE_UNCONFIRMED", error_code: "HOST_TURN_BINDING_MISMATCH" },
    },
  }).state, "FAILED");
  assert.equal(context.terminalAttentionProjection({
    provider: "CODEX",
    state: "LIVE",
    provider_cli_alive: true,
    host_turn_state: {
      state: "SESSION_ENDED",
      last_event: "SESSION_ENDED",
      latest_delivery: { phase: "NATIVE_QUEUED", error_code: null },
    },
  }).state, "ENDED");
  assert.equal(context.terminalAttentionProjection({ provider: "CODEX" }).state, "UNKNOWN");
});

test("Fleet Worker state preserves an ended Host turn over LIVE terminal metadata", () => {
  const context = evaluate(
    appSource,
    "function fleetWorkerTerminalState(terminal)",
    "function bindFleetNodeMaster",
    {
      state: {},
      terminalAttentionProjection: () => ({ state: "ENDED", detail: "SESSION_ENDED" }),
    },
  );
  assert.equal(context.fleetWorkerTerminalState({ state: "LIVE" }), "ENDED");
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

test("Project Conductor projection stays project-scoped and separates saved and live state", () => {
  const context = evaluate(
    appSource,
    "function fleetAssignmentRows()",
    "function fleetTerminalLabel(terminal)",
    {
      state: {
        personaAssignmentsStatus: "READY",
        personaAssignments: [
          {
            project_id: "universe", node_ref: null, state: "ACTIVE",
            session_anchor_ref: "conductor-anchor", persona_id: "conductor-persona",
            assignment_revision: 7, persona_revision: 3,
          },
          {
            project_id: "universe", node_ref: "feature-1", state: "ACTIVE",
            session_anchor_ref: "master-anchor", persona_id: "master-persona",
          },
          {
            project_id: "other", node_ref: null, state: "ACTIVE",
            session_anchor_ref: "other-conductor", persona_id: "other-persona",
          },
          // Fleet Worker/Reviewer assignments are also node_ref-less and
          // must NOT be counted as competing Conductor bindings (2026-09-17
          // regression: this shape alone made the projection read as
          // "2+ active assignments -> ERROR" with only one real Conductor
          // assignment present).
          {
            project_id: "universe", node_ref: null, state: "ACTIVE",
            session_anchor_ref: "worker-anchor", persona_id: "worker-persona",
            scope: "FLEET_IMPLEMENTER",
          },
        ],
        supervisorTerminalsStatus: "READY",
        supervisorTerminals: [
          { project_id: "universe", mode: "CONDUCTOR", state: "LIVE", session_anchor_ref: "conductor-anchor" },
          { project_id: "universe", mode: "MASTER", state: "LIVE", session_anchor_ref: "master-anchor" },
          { project_id: "other", mode: "CONDUCTOR", state: "LIVE", session_anchor_ref: "other-conductor" },
        ],
        supervisorSessions: [
          { node: "universe", mode: "CONDUCTOR", state: "LIVE", session_anchor_ref: "conductor-anchor" },
          { node: "universe", mode: "MASTER", state: "LIVE", session_anchor_ref: "master-anchor" },
          { node: "universe", mode: "WORKER", state: "LIVE", session_anchor_ref: "worker-anchor" },
          { node: "other", mode: "CONDUCTOR", state: "LIVE", session_anchor_ref: "other-conductor" },
        ],
      },
    },
  );
  const projection = context.fleetProjectConductorProjection("universe");
  assert.equal(projection.status, "READY");
  assert.equal(projection.assignment.session_anchor_ref, "conductor-anchor");
  assert.equal(projection.liveAnchor, "conductor-anchor");
  assert.equal(projection.terminals.live.length, 1);
  assert.equal(projection.terminals.live[0].session_anchor_ref, "conductor-anchor");

  context.state.supervisorTerminalsStatus = "ERROR";
  context.state.supervisorTerminalsError = "HTTP_503";
  const failed = context.fleetProjectConductorProjection("universe");
  assert.equal(failed.status, "ERROR");
  assert.equal(failed.assignment.session_anchor_ref, "conductor-anchor");
  assert.equal(failed.terminals.error, "HTTP_503");
});

test("Project Conductor actions use typed project scope without node_ref or terminal fallback", () => {
  const assignStart = appSource.indexOf("function assignFleetProjectConductorPersona(");
  const assignEnd = appSource.indexOf("function unassignFleetProjectConductor(", assignStart);
  assert.ok(assignStart >= 0 && assignEnd > assignStart);
  const assignSource = appSource.slice(assignStart, assignEnd);
  assert.match(assignSource, /invokeServerAction\("persona\.assign"/);
  assert.match(assignSource, /session_anchor_ref: terminalAnchor/);
  assert.match(assignSource, /expected_assignment_revision/);
  assert.doesNotMatch(assignSource, /node_ref\s*:/);

  const unassignStart = appSource.indexOf("function unassignFleetProjectConductor(");
  const unassignEnd = appSource.indexOf("async function retryFleetProjectConductorProjection", unassignStart);
  assert.ok(unassignStart >= 0 && unassignEnd > unassignStart);
  const unassignSource = appSource.slice(unassignStart, unassignEnd);
  assert.match(unassignSource, /invokeServerAction\("persona\.unassign"/);
  assert.doesNotMatch(unassignSource, /state\.terminals|first|recent|cache/);

  const renderStart = appSource.indexOf("function renderFleetProjectConductor(");
  const renderEnd = appSource.indexOf("// Zero-or-more Worker", renderStart);
  assert.ok(renderStart >= 0 && renderEnd > renderStart);
  const renderSource = appSource.slice(renderStart, renderEnd);
  assert.match(renderSource, /Live Conductor Anchor/);
  assert.match(renderSource, /Host apply/);
  assert.match(renderSource, /Retry authoritative projection/);
  assert.match(renderSource, /Unassign Conductor Persona/);
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

test("Fleet Worker controls use live project Worker/Reviewer sessions and active Personas", () => {
  const start = appSource.indexOf("function renderFleetNodeWorkerRoster(featureId, owner)");
  const end = appSource.indexOf("function goToNodeMasterBinding", start);
  assert.ok(start >= 0 && end > start);
  const roster = appSource.slice(start, end);
  assert.match(roster, /terminal\.project_id/);
  assert.match(roster, /terminal\.state \|\| terminal\.lifecycle_state/);
  assert.match(roster, /\["MASTER", "CONDUCTOR"\]/);
  assert.match(roster, /activePersonas/);
  assert.match(roster, /workerPersonaSelect/);
  assert.match(roster, /personaId: workerPersonaSelect\.value/);
  assert.match(roster, /UNKNOWN: no live Worker\/Reviewer session available to assign/);
  assert.match(roster, /assignment\.execution_shape/);
  assert.match(roster, /Task Frame \/ Master Host/);
  assert.match(roster, /Fleet Session Host/);
  assert.match(appSource, /function fleetAuthoritativeTerminals\(\)/);
  assert.match(appSource, /state\.supervisorTerminals/);
});

test("Fleet Worker session-start controls use the typed Action and authoritative lineage", () => {
  const start = appSource.indexOf("function fleetTaskFrameOptionsForTodo(featureId, todo)");
  const end = appSource.indexOf("function goToNodeMasterBinding", start);
  assert.ok(start >= 0 && end > start);
  const fleet = appSource.slice(start, end);
  assert.match(fleet, /invokeServerAction\("fleet\.worker-session-start"/);
  assert.match(fleet, /assigned_by_session_anchor_ref/);
  assert.match(fleet, /task_frame_id/);
  assert.match(fleet, /await ensureFleetWorkerAssignments\(featureId\)/);
  assert.match(fleet, /loadTerminalTabs/);
  assert.match(fleet, /Start Worker\/Reviewer session/);
  assert.match(fleet, /workerRole: startRoleSelect\.value/);
  assert.match(fleet, /provider: startProviderSelect\.value/);
  assert.match(fleet, /personaId: startPersonaSelect\.value/);
  assert.doesNotMatch(fleet, /GROK/);
  assert.match(fleet, /Todo scope \(no authoritative Task Frame\)/);
});

test("Fleet node automation, coordination and orphan controls stay on typed authoritative routes", () => {
  const start = appSource.indexOf("async function ensureFleetAutomation(featureId, owner)");
  const end = appSource.indexOf("function assignFleetWorker", start);
  assert.ok(start >= 0 && end > start);
  const controls = appSource.slice(start, end);
  assert.match(controls, /persona\.automation\.status/);
  assert.match(controls, /node_ref: featureId/);
  assert.match(controls, /session_anchor_ref: anchor/);
  assert.match(controls, /operation === "pause"/);
  assert.match(controls, /stateLabel === "PAUSED"/);
  assert.match(controls, /operation === "stop"/);
  assert.match(controls, /persona\.collaboration\.open/);
  assert.match(controls, /persona\.collaboration\.read/);
  assert.match(controls, /master-message\.orphan-cancel/);
  assert.match(controls, /master-message\.orphan-reissue/);
  assert.match(controls, /expected_owner_assignment_revision/);
  assert.match(controls, /current_owner_assignment_revision/);
  assert.match(controls, /delivery_state.*QUEUED/);
});

test("Fleet project selection renders the core projection before slow optional observers", () => {
  const start = appSource.indexOf("async function selectProject(");
  const end = appSource.indexOf("function mergeGovernanceProposalInbox", start);
  assert.ok(start >= 0 && end > start);
  const selection = appSource.slice(start, end);
  const core = selection.indexOf("const projectionResultPromise");
  const optional = selection.indexOf("/semantic-graph`", core);
  assert.ok(core >= 0 && optional > core);
  assert.match(selection, /apiWithTimeout\(/);
  assert.match(selection.slice(core, optional), /renderGoalPlan\(\)/);
});

test("Fleet renderGoalPlan clears stale graph-mode so Galaxy legend cannot leak", () => {
  const start = appSource.indexOf("function renderGoalPlan()");
  const end = appSource.indexOf("function renderFleetFeatureSummaries", start);
  assert.ok(start >= 0 && end > start);
  const body = appSource.slice(start, end);
  assert.match(body, /classList\.remove\("graph-mode",\s*"galaxy-view"\)/);
  assert.match(body, /classList\.add\("fleet-mode"\)/);
  // Galaxy entry removes home/fleet; Fleet entry must symmetrically clear graph-mode.
  const galaxy = appSource.indexOf("function showGraphView(");
  const galaxyEnd = appSource.indexOf("function showGoalPlanView", galaxy);
  assert.ok(galaxy >= 0 && galaxyEnd > galaxy);
  assert.match(
    appSource.slice(galaxy, galaxyEnd),
    /classList\.remove\("home-mode",\s*"fleet-mode"\)/
  );
});

test("Fleet home soft refresh polls while visible and skips edit/dialog focus", () => {
  assert.match(appSource, /async function refreshFleetHomeSoft\(/);
  assert.match(appSource, /function fleetHomeEditingGuarded\(/);
  assert.match(appSource, /function fleetHomeSoftRefreshActive\(/);
  const start = appSource.indexOf("async function refreshFleetHomeSoft(");
  const end = appSource.indexOf("function renderIntegratedHome()", start);
  assert.ok(start >= 0 && end > start);
  const body = appSource.slice(start, end);
  assert.match(body, /fleetHomeSoftRefreshActive\(\)/);
  assert.match(body, /\/v1\/todos/);
  assert.match(body, /\/goals/);
  assert.match(body, /delete state\.fleetAutomationByNode\[featureId\]/);
  assert.match(body, /delete state\.fleetWorkerAssignmentsByNode\[featureId\]/);
  assert.match(body, /refreshFleetProjectConductorProjection/);
  assert.match(appSource, /function invalidateFleetAuthoritativeCaches\(\)/);
  assert.match(appSource, /state\.fleetCoordinationByNode = \{\}/);
  assert.match(appSource, /fleetHomeRefreshTimer[\s\S]{0,120}setInterval\(\(\)\s*=>\s*\{\s*void refreshFleetHomeSoft\(\)/);
  assert.match(appSource, /dialog\[open\]/);
  assert.match(appSource, /TEXTAREA/);
});

test("Fleet terminal refresh re-renders Team rows after authoritative Host discovery", () => {
  const start = terminalSource.indexOf("async function loadTerminalTabs()");
  const end = terminalSource.indexOf("function hostSessionRefOf", start);
  assert.ok(start >= 0 && end > start);
  assert.match(terminalSource.slice(start, end), /state\.supervisorTerminals = incoming/);
  assert.match(terminalSource.slice(start, end), /renderIntegratedHome\(\)/);
  assert.match(terminalSource.slice(start, end), /supervisorTerminalsStatus = "ERROR"/);
  assert.match(terminalSource.slice(start, end), /supervisorTerminalsError/);
  assert.match(terminalSource.slice(start, end), /SUPERVISOR_TERMINALS_SCHEMA_INVALID/);
});

test("Host reconnect invalidates Fleet projections before soft refresh", () => {
  const start = terminalSource.indexOf("async function noteServiceReconnect()");
  const end = terminalSource.indexOf("\n}", start);
  assert.ok(start >= 0 && end > start);
  const body = terminalSource.slice(start, end);
  assert.match(body, /invalidateFleetAuthoritativeCaches/);
  assert.match(body, /refreshFleetHomeSoft/);
});

test("persona projection reload keeps the previous READY data and skips unchanged renders", async () => {
  let resolveAssignments;
  const calls = { nodeModes: 0, dock: 0, home: 0 };
  const seenDuringReload = [];
  const context = evaluate(
    appSource,
    "async function loadPersonaProjectProjection(",
    "async function selectProject(",
    {
      state: {
        selectedProject: { project_id: "p" },
        personaAssignmentsProjectId: "p",
        personaAssignmentsStatus: "READY",
        personaAssignments: [{ assignment_id: "a1" }],
        personaLibraryStatus: "READY",
        personaLibrary: [{ persona_id: "x" }],
      },
      invokeServerAction: (action) => action === "persona.list"
        ? Promise.resolve({ personas: [{ persona_id: "x" }] })
        : new Promise((resolve) => { resolveAssignments = resolve; }),
      renderNodeModes: () => { calls.nodeModes += 1; },
      renderTerminalDock: () => { calls.dock += 1; },
      renderIntegratedHome: () => { calls.home += 1; },
    },
  );
  const pending = context.loadPersonaProjectProjection("p");
  seenDuringReload.push(context.state.personaAssignmentsStatus, context.state.personaAssignments);
  assert.equal(seenDuringReload[0], "READY");
  assert.equal(seenDuringReload[1].length, 1);
  resolveAssignments({ assignments: [{ assignment_id: "a1" }] });
  await pending;
  assert.deepEqual([calls.nodeModes, calls.dock, calls.home], [0, 0, 0]);

  const changed = context.loadPersonaProjectProjection("p");
  resolveAssignments({ assignments: [{ assignment_id: "a1" }, { assignment_id: "a2" }] });
  await changed;
  assert.equal(context.state.personaAssignments.length, 2);
  assert.deepEqual([calls.nodeModes, calls.dock, calls.home], [1, 1, 1]);

  const otherProject = context.loadPersonaProjectProjection("q");
  assert.equal(context.state.personaAssignmentsStatus, "LOADING");
  assert.equal(context.state.personaAssignments, null);
  resolveAssignments({ assignments: [] });
  await otherProject;
});

test("session options say where each session is bound and keep the operator's pick", () => {
  const context = evaluate(
    appSource,
    "function fleetAssignmentRows()",
    "function fleetTerminalLabel(terminal)",
    { state: { personaAssignmentsStatus: "READY", personaAssignments: [
      { session_anchor_ref: "a", state: "ACTIVE", node_ref: "feature_1" },
      { session_anchor_ref: "b", state: "ACTIVE", node_ref: "" },
      { session_anchor_ref: "c", state: "UNASSIGNED", node_ref: "feature_2" },
    ] }, homeNodes: () => [{ node_id: "feat:feature_1", title: "Login flow" }] },
  );
  const start = appSource.indexOf("function fleetSessionBindingLabel(");
  const end = appSource.indexOf("function renderFleetNodeTeamDialogContent(", start);
  const idStart = appSource.indexOf("function homeNodeShortId(");
  const idEnd = appSource.indexOf("function homeNodeRefKey(", idStart);
  vm.runInContext(
    appSource.slice(idStart, idEnd) + "\nfunction homeNodeRefKey(n){return String(n||'').replace(/^feat:/,'')}\n" + appSource.slice(start, end),
    context,
  );
  assert.equal(context.fleetSessionBindingLabel("a"), "bound: Login flow #1");
  assert.equal(context.fleetSessionBindingLabel("b"), "bound: project-wide");
  assert.equal(context.fleetSessionBindingLabel("c"), "unbound");
  context.state.personaAssignmentsStatus = "LOADING";
  assert.equal(context.fleetSessionBindingLabel("a"), "binding unknown");
  const draft = context.fleetNodeBindDraft("feature_1");
  draft.session = "x";
  assert.equal(context.fleetNodeBindDraft("feature_1").session, "x");
});
