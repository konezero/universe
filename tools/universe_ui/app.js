"use strict";

const state = {
  projects: [],
  todos: [],
  selectedProject: null,
  projection: null,
  dispatches: [],
  releases: [],
  releaseProposals: [],
  masterHandoffs: [],
  skillPlanAdoptions: [],
  skillObservations: [],
  skillBench: [],
  experienceCases: [],
  benchComparisons: [],
  experiencePatterns: [],
  contextPacks: [],
  memories: [],
  memoryProposals: [],
  selectedNode: null,
  focusedNodeId: null,
  view: "timeline",
  roomMessages: [],
  conductorMessages: [],
  conductorRuntimeBinding: null,
  conductorRefreshInFlight: false,
  todoDraftSourceKind: "USER",
  projectRoomStream: null,
  projectRoomStreamProjectId: null,
  projectStreamReplies: {},
  projectPermissions: [],
  masterBridge: null,
  modeContract: null,
  providerSettings: null,
  hostTools: null,
  supervisorSessions: [],
  supervisorEvents: [],
  legacyExecutors: [],
  conversationTarget: {
    kind: "UNIVERSE_CONDUCTOR",
    projectId: null,
  },
  freshProject: {
    intent: null,
    routes: [],
    composition: null,
    refinementRequest: null,
    planningBinding: null,
    providers: [],
    refinementRun: null,
    refinementCandidate: null,
    refinementAdoption: null,
    adoption: null,
    handoff: null,
  },
  graph: { nodes: [], edges: [], scale: 1, x: 0, y: 0 },
  graphPan: null,
  inspectorDismissed: false,
};

const WORKLIST_SEED_TODOS = [
  {
    title: "Close seed discovery dispatch (QUEUED)",
    detail:
      "GCS project_dispatch is still QUEUED for seed discovery. Confirm Project Master inbox delivery and Project Seed publish path.",
    priority: "P0",
    state: "READY",
  },
  {
    title: "Deliver one Project Master handoff end-to-end",
    detail:
      "Propose and DELIVER one adopted Skill Plan or Fresh Composition handoff. Confirm room message + delivery_state DELIVERED_TO_MASTER.",
    priority: "P0",
    state: "READY",
  },
  {
    title: "Todo work map stays current for Master review",
    detail:
      "Keep PROJECT/NODE todos accurate. Use Send to Master for bounded questions; do not treat Todo as Task Frame execution.",
    priority: "P1",
    state: "IN_PROGRESS",
  },
  {
    title: "Verify Project Master bridge / CLI provider",
    detail:
      "Bridge AVAILABLE or room-only path understood. Provider setting for this Project Master is intentional (AUTO/GROK/CODEX/CLAUDE).",
    priority: "P1",
    state: "READY",
  },
  {
    title: "Capture first Experience Case after a Skill run",
    detail:
      "After a redacted Skill observation is ingested, record one Experience Case. Causal inference stays NOT_INFERRED.",
    priority: "P2",
    state: "BACKLOG",
  },
];

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  modeStatus: document.querySelector("#mode-status"),
  projectList: document.querySelector("#project-list"),
  workspaceTitle: document.querySelector("#workspace-title"),
  workspaceSubtitle: document.querySelector("#workspace-subtitle"),
  canvas: document.querySelector("#universe-graph"),
  graphEmpty: document.querySelector("#graph-empty"),
  graphHint: document.querySelector("#graph-hint"),
  graphZoomIn: document.querySelector("#graph-zoom-in"),
  graphZoomOut: document.querySelector("#graph-zoom-out"),
  graphFit: document.querySelector("#graph-fit"),
  conversationTitle: document.querySelector("#conversation-title"),
  conversationTargetLabel: document.querySelector("#conversation-target-label"),
  details: document.querySelector("#details-panel"),
  activity: document.querySelector("#activity-panel"),
  benchPanel: document.querySelector("#bench-panel"),
  memoryPanel: document.querySelector("#memory-panel"),
  futurePanel: document.querySelector("#future-panel"),
  dispatchForm: document.querySelector("#dispatch-form"),
  dispatchSubmit: document.querySelector("#dispatch-submit"),
  dispatchInstruction: document.querySelector("#dispatch-instruction"),
  composerActionButton: document.querySelector("#composer-action-button"),
  composerActionMenu: document.querySelector("#composer-action-menu"),
  projectMasterActions: document.querySelector("#project-master-actions"),
  returnToConductor: document.querySelector("#return-to-conductor"),
  prepareProject: document.querySelector("#prepare-project-button"),
  projectDialog: document.querySelector("#project-dialog"),
  projectForm: document.querySelector("#project-form"),
  projectFormError: document.querySelector("#project-form-error"),
  settingsButton: document.querySelector("#settings-button"),
  sessionObservatoryButton: document.querySelector("#session-observatory-button"),
  sessionProviderLine: document.querySelector("#session-provider-line"),
  sessionObservatoryDialog: document.querySelector("#session-observatory-dialog"),
  sessionObservatorySummary: document.querySelector("#session-observatory-summary"),
  sessionObservatoryList: document.querySelector("#session-observatory-list"),
  legacyExecutorList: document.querySelector("#legacy-executor-list"),
  sessionEventList: document.querySelector("#session-event-list"),
  refreshSessionsButton: document.querySelector("#refresh-sessions-button"),
  primaryNav: document.querySelector("#primary-nav"),
  metricProjects: document.querySelector("#metric-projects"),
  metricTodos: document.querySelector("#metric-todos"),
  metricDispatches: document.querySelector("#metric-dispatches"),
  metricService: document.querySelector("#metric-service"),
  conductorSummary: document.querySelector("#conductor-summary"),
  conductorSummaryToggle: document.querySelector("#conductor-summary-toggle"),
  conductorSummaryLine: document.querySelector("#conductor-summary-line"),
  conductorClock: document.querySelector("#conductor-clock"),
  conductorClockCompact: document.querySelector("#conductor-clock-compact"),
  inspectorTitle: document.querySelector("#inspector-title"),
  inspectorSubtitle: document.querySelector("#inspector-subtitle"),
  statusBarLeft: document.querySelector("#status-bar-left"),
  statusBarRight: document.querySelector("#status-bar-right"),
  viewModeSelect: document.querySelector("#view-mode-select"),
  lawContract: document.querySelector("#law-contract"),
  lawRuntime: document.querySelector("#law-runtime"),
  lawLocal: document.querySelector("#law-local"),
  settingsDialog: document.querySelector("#settings-dialog"),
  settingsForm: document.querySelector("#settings-form"),
  settingsError: document.querySelector("#settings-error"),
  localServiceStatus: document.querySelector("#local-service-status"),
  universeProviderSetting: document.querySelector("#universe-provider-setting"),
  memoryMaintainInterval: document.querySelector("#memory-maintain-interval"),
  memoryMaintainStatus: document.querySelector("#memory-maintain-status"),
  universeProviderStatus: document.querySelector("#universe-provider-status"),
  projectProviderSettings: document.querySelector("#project-provider-settings"),
  hostProfilePath: document.querySelector("#host-profile-path"),
  hostToolSettings: document.querySelector("#host-tool-settings"),
  discoverHostTools: document.querySelector("#discover-host-tools-button"),
  freshProjectDialog: document.querySelector("#fresh-project-dialog"),
  freshProjectForm: document.querySelector("#fresh-project-form"),
  freshProjectStep: document.querySelector("#fresh-project-step"),
  freshProjectIntent: document.querySelector("#fresh-project-intent"),
  freshProjectRoutes: document.querySelector("#fresh-project-routes"),
  freshProjectRouteList: document.querySelector("#fresh-project-route-list"),
  freshProjectComposition: document.querySelector("#fresh-project-composition"),
  freshProjectCompositionTitle: document.querySelector("#fresh-project-composition-title"),
  freshProjectCompositionOutput: document.querySelector("#fresh-project-composition-output"),
  freshProjectRefinement: document.querySelector("#fresh-project-refinement"),
  freshProjectRefinementRef: document.querySelector("#fresh-project-refinement-ref"),
  planningBindingStatus: document.querySelector("#planning-binding-status"),
  planningProvider: document.querySelector("#planning-provider"),
  createPlanningProposal: document.querySelector("#create-planning-proposal"),
  planningProposal: document.querySelector("#planning-proposal"),
  planningProposalProvider: document.querySelector("#planning-proposal-provider"),
  planningProposalDetails: document.querySelector("#planning-proposal-details"),
  executePlanningProposal: document.querySelector("#execute-planning-proposal"),
  planningRunStatus: document.querySelector("#planning-run-status"),
  refinementCandidate: document.querySelector("#refinement-candidate"),
  refinementComparison: document.querySelector("#refinement-comparison"),
  adoptRefinementButton: document.querySelector("#adopt-refinement-button"),
  freshProjectAdopted: document.querySelector("#fresh-project-adopted"),
  freshProjectAdoptionRef: document.querySelector("#fresh-project-adoption-ref"),
  freshProjectError: document.querySelector("#fresh-project-error"),
  findRoutesButton: document.querySelector("#find-routes-button"),
  prepareRefinementButton: document.querySelector("#prepare-refinement-button"),
  adoptCompositionButton: document.querySelector("#adopt-composition-button"),
  releaseDialog: document.querySelector("#release-dialog"),
  releaseForm: document.querySelector("#release-form"),
  releaseList: document.querySelector("#release-list"),
  releaseFormError: document.querySelector("#release-form-error"),
  releaseProposalOutput: document.querySelector("#release-proposal-output"),
  conversationLayer: document.querySelector("#conversation-layer"),
  conversationToggle: document.querySelector("#conversation-toggle"),
  conversationBadge: document.querySelector("#conversation-badge"),
  conversationOpacity: document.querySelector("#conversation-opacity"),
  roomMessageList: document.querySelector("#room-message-list"),
  roomContext: document.querySelector("#room-context"),
  roomHint: document.querySelector("#room-hint"),
  closeInspector: document.querySelector("#close-inspector"),
  nodeBreadcrumb: document.querySelector("#node-breadcrumb"),
  nodeBreadcrumbProject: document.querySelector("#node-breadcrumb-project"),
  nodeBreadcrumbNode: document.querySelector("#node-breadcrumb-node"),
  exitNodeUniverse: document.querySelector("#exit-node-universe"),
  toasts: document.querySelector("#toast-region"),
  todoButton: document.querySelector("#todo-button"),
  todoDialog: document.querySelector("#todo-dialog"),
  todoForm: document.querySelector("#todo-form"),
  todoTitle: document.querySelector("#todo-title"),
  todoDetail: document.querySelector("#todo-detail"),
  todoScope: document.querySelector("#todo-scope"),
  todoProject: document.querySelector("#todo-project"),
  todoNode: document.querySelector("#todo-node"),
  todoScopeFilter: document.querySelector("#todo-scope-filter"),
  todoStateFilter: document.querySelector("#todo-state-filter"),
  todoPriorityFilter: document.querySelector("#todo-priority-filter"),
  todoList: document.querySelector("#todo-list"),
  todoCount: document.querySelector("#todo-count"),
  todoFormError: document.querySelector("#todo-form-error"),
  seedWorklistButton: document.querySelector("#seed-worklist-button"),
  proposeMasterHandoffButton: document.querySelector(
    "#propose-master-handoff-button"
  ),
  deliverMasterHandoffButton: document.querySelector(
    "#deliver-master-handoff-button"
  ),
  freshProjectHandoffStatus: document.querySelector(
    "#fresh-project-handoff-status"
  ),
};

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}


async function api(path, options = {}) {
  const headers = options.body ? { "Content-Type": "application/json" } : {};
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.error_code || payload.status);
  }
  return payload;
}

async function refreshSupervisorSessions() {
  const [sessions, events, legacy] = await Promise.all([
    api("/v1/supervisor/sessions"),
    api("/v1/supervisor/events?limit=40"),
    api("/v1/supervisor/legacy-executors"),
  ]);
  state.supervisorSessions = sessions.sessions || [];
  state.supervisorEvents = events.events || [];
  state.legacyExecutors = legacy.executors || [];
  renderSessionObservatory();
}

function renderSessionObservatory() {
  if (!elements.sessionObservatoryList) return;
  const sessions = state.supervisorSessions || [];
  const live = sessions.filter((item) => item.state === "LIVE").length;
  const unknown = sessions.filter((item) => item.state === "UNKNOWN").length;
  elements.sessionObservatorySummary.textContent =
    `${sessions.length} sessions · ${live} live · ${unknown} unknown`;
  elements.sessionObservatoryList.replaceChildren();
  if (!sessions.length) {
    elements.sessionObservatoryList.append(
      node("p", "empty-copy", "No persistent Mode session has registered yet.")
    );
  }
  for (const session of sessions) {
    const card = node("article", "supervisor-session-card");
    card.dataset.default = String(Boolean(session.is_default));
    const heading = node("div", "session-card-heading");
    heading.append(
      node("strong", "", session.alias || `${session.node} ${session.mode}`),
      node("span", "session-state-pill", session.state || "UNKNOWN")
    );
    heading.lastElementChild.dataset.state = session.state || "UNKNOWN";
    const meta = node("div", "session-card-meta");
    meta.append(
      node("span", "", `${session.node} / ${session.mode}`),
      node("span", "", session.provider || "UNKNOWN"),
      node("span", "", session.currentness || "UNKNOWN"),
      node("span", "", session.is_default ? "DEFAULT" : "ALTERNATIVE")
    );
    const ref = node(
      "p",
      "session-ref-line",
      session.provider_session_ref || "Provider session not observed"
    );
    const alias = document.createElement("input");
    alias.className = "session-alias-input";
    alias.value = session.alias || "";
    alias.maxLength = 120;
    alias.setAttribute("aria-label", `Alias for ${session.session_id}`);
    const actions = node("div", "session-card-actions");
    const saveAlias = node("button", "secondary-button compact-action", "Save alias");
    saveAlias.type = "button";
    saveAlias.addEventListener("click", async () => {
      try {
        await api(
          `/v1/supervisor/sessions/${encodeURIComponent(session.session_id)}/alias`,
          {
            method: "POST",
            body: { alias: alias.value, expected_version: session.row_version },
          }
        );
        await refreshSupervisorSessions();
      } catch (error) {
        toast(error.message, true);
      }
    });
    const resume = node(
      "button",
      "primary-button compact-action",
      session.is_default ? "Reconnect" : "Use session"
    );
    resume.type = "button";
    resume.addEventListener("click", async () => {
      try {
        if (!session.is_default) {
          await api(
            `/v1/supervisor/sessions/${encodeURIComponent(session.session_id)}/default`,
            {
              method: "POST",
              body: {
                expected_pointer_version: session.default_pointer_version,
              },
            }
          );
        }
        const project = state.projects.find(
          (item) => item.project_id === session.node
        );
        elements.sessionObservatoryDialog.close();
        if (project && session.mode === "MASTER") {
          await callProjectMaster(project.project_id);
        } else {
          returnToUniverseConductor();
        }
        await refreshSupervisorSessions();
      } catch (error) {
        toast(error.message, true);
      }
    });
    actions.append(saveAlias, resume);
    card.append(heading, meta, ref, alias, actions);
    elements.sessionObservatoryList.append(card);
  }

  if (elements.legacyExecutorList) {
    elements.legacyExecutorList.replaceChildren();
    for (const executor of state.legacyExecutors || []) {
      const observation = executor.observation || {};
      const row = node("article", "legacy-executor-row");
      row.dataset.state = executor.status || "UNKNOWN";
      const command = observation.command_profile || "Command unavailable";
      row.append(
        node("strong", "", executor.status || "UNKNOWN"),
        node("span", "", `PID ${observation.pid || "UNKNOWN"}`),
        node("code", "", command),
        node("small", "", executor.reason || executor.required_route || "Observed")
      );
      elements.legacyExecutorList.append(row);
    }
    if (!(state.legacyExecutors || []).length) {
      elements.legacyExecutorList.append(
        node("p", "empty-copy", "No legacy Session Boot executor was observed.")
      );
    }
  }

  elements.sessionEventList.replaceChildren();
  for (const event of (state.supervisorEvents || []).slice(0, 20)) {
    const row = node("div", "session-event-row");
    row.append(
      node("strong", "", event.event_type || "EVENT"),
      node("span", "", event.session_id || "service"),
      node("time", "", event.occurred_at || "")
    );
    elements.sessionEventList.append(row);
  }
  if (!(state.supervisorEvents || []).length) {
    elements.sessionEventList.append(
      node("p", "empty-copy", "No Supervisor event has been recorded.")
    );
  }

  const conductor = sessions.find(
    (session) => session.is_default && session.mode === "CONDUCTOR"
  );
  if (elements.sessionProviderLine) {
    elements.sessionProviderLine.textContent = conductor
      ? `${conductor.provider} · ${conductor.state}`
      : "Provider · not registered";
  }
}

function universeModeIsActive() {
  return (
    state.modeContract?.status === "ACTIVE" &&
    state.modeContract?.mode === "CONDUCTOR" &&
    state.modeContract?.role === "CONDUCTOR"
  );
}

function renderModeStatus() {
  const active = universeModeIsActive();
  elements.modeStatus.dataset.state = active ? "active" : "unknown";
  elements.modeStatus.textContent = active
    ? "UNIVERSE / CONDUCTOR"
    : "MODE / UNKNOWN";
}

function closeComposerActionMenu() {
  elements.composerActionMenu.classList.add("hidden");
  elements.composerActionButton.setAttribute("aria-expanded", "false");
}

function toggleComposerActionMenu(forceOpen = null) {
  const shouldOpen =
    forceOpen === null
      ? elements.composerActionMenu.classList.contains("hidden")
      : forceOpen;
  elements.composerActionMenu.classList.toggle("hidden", !shouldOpen);
  elements.composerActionButton.setAttribute(
    "aria-expanded",
    String(shouldOpen)
  );
}

function renderComposerActions() {
  elements.projectMasterActions.replaceChildren();
  for (const project of state.projects) {
    const action = node("button", "composer-menu-item");
    action.type = "button";
    action.role = "menuitem";
    action.dataset.projectId = project.project_id;
    const isCurrent =
      state.conversationTarget.kind === "PROJECT_MASTER" &&
      state.conversationTarget.projectId === project.project_id;
    action.classList.toggle("selected", isCurrent);
    const bridgeConnected =
      state.selectedProject?.project_id === project.project_id &&
      state.masterBridge?.status === "AVAILABLE";
    const bridgeRegistered =
      state.selectedProject?.project_id === project.project_id &&
      state.masterBridge?.status === "REGISTERED";
    action.append(
      node("span", "", `Call ${project.project_id} Master`),
      node(
        "small",
        "",
        bridgeConnected
          ? "Direct bridge connected"
          : bridgeRegistered
            ? "Bridge registered / awaiting delivery"
          : isCurrent
            ? "Project Room only"
            : "Open Project Room"
      )
    );
    action.addEventListener("click", async () => {
      try {
        await callProjectMaster(project.project_id);
      } catch (error) {
        toast(error.message, true);
      }
    });
    elements.projectMasterActions.append(action);
  }
  elements.returnToConductor.classList.toggle(
    "selected",
    state.conversationTarget.kind === "UNIVERSE_CONDUCTOR"
  );
}

function returnToUniverseConductor() {
  closeProjectRoomStream();
  state.conversationTarget = {
    kind: "UNIVERSE_CONDUCTOR",
    projectId: null,
  };
  closeComposerActionMenu();
  renderComposerActions();
  renderComposerState();
  renderRoomMessages();
  elements.dispatchInstruction.focus();
}

async function callProjectMaster(projectId) {
  closeComposerActionMenu();
  if (state.selectedProject?.project_id !== projectId) {
    await selectProject(projectId);
  }
  await api(
    `/v1/projects/${encodeURIComponent(projectId)}/master-session/prepare`,
    {
      method: "POST",
      body: {},
    }
  );
  state.providerSettings = await api("/v1/settings/providers");
  await selectProject(projectId);
  state.conversationTarget = {
    kind: "PROJECT_MASTER",
    projectId,
  };
  openProjectRoomStream(projectId);
  renderComposerActions();
  renderComposerState();
  renderRoomMessages();
  elements.dispatchInstruction.focus();
}

function sessionConnectionText(connection, fallbackMode) {
  const provider = connection?.last_provider || "UNKNOWN";
  const connectionState = connection?.connection_state || "NOT_OPENED";
  const mode = connection?.requested_mode || fallbackMode;
  return `${provider} / ${connectionState} / ${mode}`;
}

function renderComposerState() {
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    const setting = state.providerSettings?.universe_conductor || null;
    const provider = setting?.resolved_provider || "UNAVAILABLE";
    const autoApprove =
      providerCapability(provider)?.cli_auto_approve || "UNKNOWN";
    const session = setting?.session_connection || null;
    elements.roomContext.textContent =
      `Universe Conductor / ${sessionConnectionText(session, "CONDUCTOR")}`;
    elements.roomHint.textContent =
      state.conductorRuntimeBinding?.status === "BOUND"
        ? `LLM connected / Auto-approve ${autoApprove}`
        : "Waiting for Runtime binding";
    elements.dispatchInstruction.placeholder = "Message Universe Conductor";
    if (elements.conversationTitle) {
      elements.conversationTitle.textContent = "Conversation";
    }
    if (elements.conversationTargetLabel) {
      elements.conversationTargetLabel.textContent = "Universe Conductor";
    }
    return;
  }
  const projectId = state.conversationTarget.projectId;
  const directBridge =
    state.selectedProject?.project_id === projectId &&
    state.masterBridge?.status === "AVAILABLE";
  const registeredBridge =
    state.selectedProject?.project_id === projectId &&
    state.masterBridge?.status === "REGISTERED";
  const setting =
    state.providerSettings?.project_masters?.find(
      (item) => item.scope_id === projectId
    ) || null;
  const provider = setting?.resolved_provider || "UNAVAILABLE";
  const autoApprove =
    providerCapability(provider)?.cli_auto_approve || "UNKNOWN";
  const session = setting?.session_connection || null;
  elements.roomContext.textContent =
    `${projectId} / Project Master / ${sessionConnectionText(session, "MASTER")}`;
  elements.roomHint.textContent = directBridge
    ? `Direct bridge connected / Auto-approve ${autoApprove}`
    : registeredBridge
      ? "Bridge registered / awaiting first delivery"
      : "Project Room only";
  elements.dispatchInstruction.placeholder = `Message ${projectId} Master`;
  if (elements.conversationTitle) {
    elements.conversationTitle.textContent = "Conversation";
  }
  if (elements.conversationTargetLabel) {
    elements.conversationTargetLabel.textContent = `${projectId} Master`;
  }
}

function setGraphScale(nextScale) {
  state.graph.scale = Math.min(2.2, Math.max(0.4, nextScale));
  drawGraph();
}

function fitGraphView() {
  const nodes = state.graph.nodes;
  if (!nodes.length) {
    state.graph.scale = 1;
    state.graph.x = 0;
    state.graph.y = 0;
    drawGraph();
    return;
  }
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const item of nodes) {
    minX = Math.min(minX, item.x);
    maxX = Math.max(maxX, item.x);
    minY = Math.min(minY, item.y);
    maxY = Math.max(maxY, item.y);
  }
  const { ratio, width, height } = canvasMetrics();
  const viewportWidth = width / ratio;
  const viewportHeight = height / ratio;
  const spanX = Math.max(180, maxX - minX + 160);
  const spanY = Math.max(140, maxY - minY + 140);
  const scale = Math.min(1.6, Math.max(0.5, Math.min(viewportWidth / spanX, viewportHeight / spanY)));
  state.graph.scale = scale;
  state.graph.x = -((minX + maxX) / 2) * scale;
  state.graph.y = -((minY + maxY) / 2) * scale;
  drawGraph();
}

function updateGraphChrome() {
  if (elements.graphHint) {
    elements.graphHint.classList.toggle(
      "hidden",
      !state.selectedProject || !state.graph.nodes.length
    );
  }
  if (elements.graphEmpty) {
    // empty state already driven elsewhere
  }
}

async function refresh() {
  try {
    const health = await fetch("/health", { cache: "no-store" }).then((response) =>
      response.json()
    );
    elements.serviceStatus.dataset.state = health.status === "READY" ? "ready" : "error";
    elements.serviceStatus.textContent =
      health.status === "READY" ? "Local service" : health.status;
    state.modeContract = health.mode_contract || null;
    renderModeStatus();

    const [
      projectResult,
      todoResult,
      releaseResult,
      conductorRoomResult,
      providerSettings,
      hostTools,
    ] =
      await Promise.all([
      api("/v1/projects"),
      api("/v1/todos"),
      api("/v1/releases"),
      api("/v1/conductor-room/messages"),
      api("/v1/settings/providers"),
      api("/v1/settings/host-tools"),
    ]);
    state.projects = projectResult.projects;
    state.todos = todoResult.todos;
    state.releases = releaseResult.releases;
    state.conductorMessages = conductorRoomResult.messages || [];
    state.conductorRuntimeBinding =
      conductorRoomResult.runtime_binding || null;
    state.providerSettings = providerSettings;
    state.hostTools = hostTools;
    try {
      await refreshSupervisorSessions();
    } catch (error) {
      state.supervisorSessions = [];
      state.supervisorEvents = [];
      state.legacyExecutors = [];
      renderSessionObservatory();
      console.warn("Session Supervisor refresh failed", error);
    }
    renderProjects();
    renderComposerActions();
    renderReleaseCatalog();
    const preferred =
      state.selectedProject &&
      state.projects.find(
        (project) => project.project_id === state.selectedProject.project_id
      );
    if (preferred) {
      await selectProject(preferred.project_id);
    } else if (state.projects.length) {
      await selectProject(state.projects[0].project_id);
    } else {
      state.selectedProject = null;
      state.projection = null;
      state.dispatches = [];
      state.masterBridge = null;
      renderEmpty();
    }
  } catch (error) {
    elements.serviceStatus.dataset.state = "error";
    elements.serviceStatus.textContent = "Unavailable";
    toast(error.message, true);
  }
}

function renderProjects() {
  elements.projectList.replaceChildren();
  for (const project of state.projects) {
    const button = node("button", "project-item");
    button.type = "button";
    button.role = "option";
    button.dataset.projectId = project.project_id;
    button.ariaSelected = String(
      state.selectedProject?.project_id === project.project_id
    );
    if (state.selectedProject?.project_id === project.project_id) {
      button.classList.add("selected");
    }
    const avatar = node(
      "span",
      "project-avatar",
      project.project_id.slice(0, 2)
    );
    const copy = node("span", "project-copy");
    const openTodoCount = state.todos.filter(
      (todo) =>
        todo.project_id === project.project_id && todo.state !== "DONE"
    ).length;
    copy.append(
      node("span", "project-name", project.project_id),
      node(
        "span",
        "project-meta",
        `${project.metadata.label || project.refs.mode_registry}${
          openTodoCount ? ` / ${openTodoCount} open` : ""
        }`
      )
    );
    button.append(avatar, copy);
    button.addEventListener("click", () => selectProject(project.project_id));
    elements.projectList.append(button);
  }
}

function renderReleaseCatalog() {
  elements.releaseList.replaceChildren();
  if (!state.releases.length) {
    elements.releaseList.append(
      node("p", "empty-copy", "No imported Release DB")
    );
    return;
  }
  for (const release of state.releases) {
    const card = node("article", "release-card");
    card.append(
      node("strong", "", release.release_id),
      node(
        "small",
        "",
        `${release.source_repository} @ ${release.source_commit.slice(0, 12)}`
      ),
      node(
        "small",
        "",
        `DB ${release.database_sha256.slice(0, 16)} / profiles ${
          release.profile_catalog.status
        }`
      )
    );
    const action = node(
      "button",
      "secondary-button",
      state.selectedProject ? "Plan project update" : "Select a project first"
    );
    action.type = "button";
    action.disabled = !state.selectedProject;
    action.addEventListener("click", () =>
      proposeProjectRelease(release.release_id)
    );
    card.append(action);
    elements.releaseList.append(card);
  }
}

function showReleaseProposal(proposal) {
  elements.releaseProposalOutput.replaceChildren();
  elements.releaseProposalOutput.classList.remove("hidden");
  elements.releaseProposalOutput.classList.toggle(
    "blocked",
    proposal.status === "PROJECT_RELEASE_PROPOSAL_BLOCKED"
  );
  const actionCounts = {};
  for (const action of proposal.plan.actions) {
    actionCounts[action.action] = (actionCounts[action.action] || 0) + 1;
  }
  elements.releaseProposalOutput.append(
    node("h3", "", proposal.status),
    node(
      "p",
      "",
      `${proposal.release_id} → ${proposal.project_id} / ${
        proposal.plan.operation
      }`
    ),
    node(
      "p",
      "",
      `Plan ${proposal.plan.plan_digest} / collisions ${
        proposal.plan.collisions.length
      } / actions ${Object.entries(actionCounts)
        .map(([name, count]) => `${name}:${count}`)
        .join(", ")}`
    ),
    node(
      "p",
      "",
      "No project files were changed. Approval and Project Host apply are separate."
    )
  );
}

async function proposeProjectRelease(releaseId) {
  if (!state.selectedProject) {
    toast("Select a project", true);
    return;
  }
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        state.selectedProject.project_id
      )}/release-proposals`,
      {
        method: "POST",
        body: { release_id: releaseId, mode: "MASTER" },
      }
    );
    state.releaseProposals = [
      result.proposal,
      ...state.releaseProposals.filter(
        (item) => item.proposal_id !== result.proposal.proposal_id
      ),
    ];
    showReleaseProposal(result.proposal);
    toast("Project update plan recorded");
  } catch (error) {
    elements.releaseFormError.textContent = error.message;
  }
}

async function selectProject(projectId) {
  const project = state.projects.find((item) => item.project_id === projectId);
  if (!project) return;
  state.selectedProject = project;
  state.selectedNode = null;
  state.focusedNodeId = null;
  state.inspectorDismissed = false;
  renderProjects();
  await api(`/v1/projects/${encodeURIComponent(projectId)}/sync`, {
    method: "POST",
    body: {},
  }).catch(() => null);
  const [
    projectionResult,
    dispatchResult,
    proposalResult,
    roomResult,
    bridgeResult,
    permissionResult,
    handoffResult,
    skillPlanAdoptionResult,
    observationResult,
    benchResult,
    benchCompareResult,
    experienceResult,
    patternResult,
    contextPackResult,
    memoryResult,
    memoryProposalResult,
  ] = await Promise.all([
    api(`/v1/projects/${encodeURIComponent(projectId)}/projection`).catch(
      () => null
    ),
    api(`/v1/projects/${encodeURIComponent(projectId)}/dispatches`),
    api(`/v1/projects/${encodeURIComponent(projectId)}/release-proposals`),
    api(`/v1/projects/${encodeURIComponent(projectId)}/room/messages`).catch(() => ({ messages: [] })),
    api(`/v1/projects/${encodeURIComponent(projectId)}/master-bridge`).catch(() => ({ bridge: null })),
    api(`/v1/projects/${encodeURIComponent(projectId)}/agent-session/permissions`).catch(
      () => ({ permissions: [] })
    ),
    api(`/v1/projects/${encodeURIComponent(projectId)}/master-handoffs`).catch(
      () => ({ handoffs: [] })
    ),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/skill-plan-adoptions`
    ).catch(() => ({ adoptions: [] })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/skill-observations`
    ).catch(() => ({ observations: [] })),
    api("/v1/bench/skills").catch(() => ({ bench: [] })),
    api("/v1/bench/compare?group_by=skill&limit=20").catch(() => ({
      comparisons: [],
    })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/experience-cases`
    ).catch(() => ({ cases: [] })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/experience-pattern-proposals`
    ).catch(() => ({ proposals: [] })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/context-packs`
    ).catch(() => ({ context_packs: [] })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/memories`
    ).catch(() => ({ memories: [] })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/memories/propose-links`
    ).catch(() => ({ proposals: [] })),
  ]);
  state.projection = projectionResult?.projection || null;
  state.dispatches = await Promise.all(
    dispatchResult.dispatches.map((item) =>
      api(
        `/v1/dispatches/${encodeURIComponent(item.dispatch.dispatch_id)}`
      ).catch(() => item)
    )
  );
  state.releaseProposals = proposalResult.proposals;
  state.roomMessages = roomResult.messages || [];
  state.masterBridge = bridgeResult.bridge || null;
  state.projectPermissions = permissionResult.permissions || [];
  state.masterHandoffs = handoffResult.handoffs || [];
  state.skillPlanAdoptions = skillPlanAdoptionResult.adoptions || [];
  state.skillObservations = observationResult.observations || [];
  state.skillBench = benchResult.bench || [];
  state.benchComparisons = benchCompareResult.comparisons || [];
  state.experienceCases = experienceResult.cases || [];
  state.experiencePatterns = patternResult.proposals || [];
  state.contextPacks = contextPackResult.context_packs || [];
  state.memories = memoryResult.memories || [];
  state.memoryProposals = memoryProposalResult.proposals || [];
  elements.workspaceTitle.textContent = project.project_id;
  elements.workspaceSubtitle.textContent =
    state.projection?.project?.goal || project.project_root;
  renderComposerActions();
  renderComposerState();
  buildGraph();
  renderDetails();
  renderActivity();
  renderBench();
  renderMemory();
  renderFuture();
  if (typeof refreshConductorPanel === "function") refreshConductorPanel();
  renderRoomMessages();
  renderReleaseCatalog();
  elements.todoProject.value = projectId;
  renderTodoScopeControls();
  renderTodos();
}


function conversationMessageCount() {
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    return (state.conductorMessages || []).length;
  }
  return (
    (state.roomMessages || []).length +
    (state.projectPermissions || []).filter((item) => item.state === "PENDING").length
  );
}

function updateConversationBadge() {
  if (!elements.conversationBadge) return;
  const count = conversationMessageCount();
  if (count > 0) {
    elements.conversationBadge.textContent = count > 99 ? "99+" : String(count);
    elements.conversationBadge.classList.remove("hidden");
  } else {
    elements.conversationBadge.textContent = "0";
    elements.conversationBadge.classList.add("hidden");
  }
}

function syncConversationToggle(collapsed) {
  if (!elements.conversationToggle) return;
  const title = collapsed ? "Expand conversation" : "Collapse conversation";
  elements.conversationToggle.title = title;
  elements.conversationToggle.setAttribute("aria-label", title);
  elements.conversationToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

function expandConversationLayer() {
  if (!elements.conversationLayer) return;
  if (elements.conversationLayer.classList.contains("collapsed")) {
    elements.conversationLayer.classList.remove("collapsed");
    syncConversationToggle(false);
  }
}

function renderRoomMessages() {
  elements.roomMessageList.replaceChildren();
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    if (!state.conductorMessages.length) {
      const item = node("article", "room-message conductor-message");
      item.append(
        node("strong", "", "UNIVERSE / CONDUCTOR"),
        node("p", "", "Universe control room is active."),
        node("small", "", "Send a message here or use + to call a Project Master.")
      );
      elements.roomMessageList.append(item);
      updateConversationBadge();
      return;
    }
    for (const message of state.conductorMessages.slice(-8)) {
      const item = node("article", "room-message conductor-message");
      const failure = message.failure?.reason
        ? ` / ${message.failure.reason}`
        : "";
      const provider = message.provider ? ` / ${message.provider}` : "";
      item.append(
        node("strong", "", `${message.sender} / ${message.kind}`),
        node("p", "", message.body),
        node(
          "small",
          "",
          `${conductorDeliveryLabel(message.delivery_state)}${provider}${failure}`
        )
      );
      if (message.ui_action?.kind === "TODO_DRAFT") {
        const actions = node("div", "room-message-actions");
        const review = node("button", "secondary-button", "Review Todo draft");
        review.type = "button";
        review.addEventListener("click", () =>
          openConductorTodoDraft(message.ui_action)
        );
        actions.append(review);
        item.append(actions);
      }
      if (message.ui_action?.kind === "FRESH_PROJECT_DRAFT") {
        const actions = node("div", "room-message-actions");
        const review = node("button", "secondary-button", "Review project draft");
        review.type = "button";
        review.addEventListener("click", () =>
          openConductorFreshProjectDraft(message.ui_action)
        );
        actions.append(review);
        item.append(actions);
      }
      elements.roomMessageList.append(item);
    }
    elements.roomMessageList.scrollTop = elements.roomMessageList.scrollHeight;
    updateConversationBadge();
    return;
  }
  for (const permission of state.projectPermissions.filter(
    (item) => item.state === "PENDING"
  )) {
    elements.roomMessageList.append(renderPermissionCard(permission));
  }
  if (!state.roomMessages.length) {
    elements.roomMessageList.append(
      node(
        "p",
        "empty-copy",
        `No messages for ${state.conversationTarget.projectId} Master`
      )
    );
    updateConversationBadge();
    return;
  }
  for (const message of state.roomMessages.slice(-8)) {
    const item = node("article", "room-message");
    item.append(
      node("strong", "", `${message.sender} / ${message.kind}`),
      node("p", "", message.body),
      node("small", "", message.delivery_state)
    );
    elements.roomMessageList.append(item);
  }
  for (const reply of Object.values(state.projectStreamReplies)) {
    const item = node("article", "room-message streaming");
    item.append(
      node("strong", "", "PROJECT_MASTER / LIVE"),
      node("p", "", reply.body || "Thinking..."),
      node("small", "", reply.state)
    );
    elements.roomMessageList.append(item);
  }
  elements.roomMessageList.scrollTop = elements.roomMessageList.scrollHeight;
  updateConversationBadge();
}

function renderPermissionCard(permission) {
  const item = node("article", "room-message permission-request");
  const toolCall = permission.tool_call || {};
  const title =
    toolCall.command ||
    toolCall.title ||
    toolCall.toolCallId ||
    "Agent tool request";
  item.append(
    node("strong", "", `${permission.provider} / APPROVAL REQUIRED`),
    node("p", "permission-title", String(title)),
    node(
      "small",
      "",
      toolCall.reason || "The agent is waiting for your decision."
    )
  );
  const actions = node("div", "permission-actions");
  for (const option of permission.options || []) {
    const button = node("button", `permission-option ${option.kind}`, option.name);
    button.type = "button";
    button.addEventListener("click", () =>
      resolveAgentPermission(permission, option.optionId)
    );
    actions.append(button);
  }
  item.append(actions);
  return item;
}

async function resolveAgentPermission(permission, optionId) {
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        permission.project_id
      )}/agent-session/permissions/${encodeURIComponent(
        permission.request_id
      )}/decision`,
      {
        method: "POST",
        body: { option_id: optionId },
      }
    );
    state.projectPermissions = state.projectPermissions.map((item) =>
      item.request_id === permission.request_id ? result.permission : item
    );
    renderRoomMessages();
    toast("Agent permission decision delivered");
  } catch (error) {
    toast(error.message, true);
  }
}

function providerCapability(provider) {
  return (
    state.providerSettings?.available_providers?.find(
      (item) => item.provider === provider
    ) || null
  );
}

function providerStatusText(setting) {
  const configured = setting?.provider || "AUTO";
  const resolved = setting?.resolved_provider || "UNAVAILABLE";
  const mode =
    setting?.scope_kind === "UNIVERSE_CONDUCTOR" ? "CONDUCTOR" : "MASTER";
  if (configured === "AUTO") {
    const providerState = resolved === "UNAVAILABLE"
      ? "Auto / no CLI available"
      : `Auto / currently ${resolved}`;
    return `${providerState} / ${sessionConnectionText(
      setting?.session_connection,
      mode
    )}`;
  }
  const capability = providerCapability(configured);
  const providerState = capability?.status === "AVAILABLE"
    ? `${configured} available`
    : `${configured} unavailable / ${capability?.reason || "CLI unavailable"}`;
  return `${providerState} / ${sessionConnectionText(
    setting?.session_connection,
    mode
  )}`;
}

function renderProviderSettings() {
  const settings = state.providerSettings;
  if (!settings) return;
  const conductor = settings.universe_conductor;
  elements.universeProviderSetting.value = conductor?.provider || "AUTO";
  elements.universeProviderStatus.textContent = providerStatusText(conductor);
  elements.projectProviderSettings.replaceChildren();
  for (const project of state.projects) {
    const setting =
      settings.project_masters?.find(
        (item) => item.scope_id === project.project_id
      ) || { provider: "AUTO", resolved_provider: "UNAVAILABLE" };
    const row = node("label", "project-provider-row");
    row.dataset.projectId = project.project_id;
    const copy = node("span", "project-provider-copy");
    copy.append(
      node("strong", "", `${project.project_id} Master`),
      node("small", "", providerStatusText(setting))
    );
    const select = node("select", "project-provider-select");
    select.name = `project_provider_${project.project_id}`;
    select.dataset.projectId = project.project_id;
    for (const [value, label] of [
      ["AUTO", "Auto"],
      ["GROK", "Grok"],
      ["CODEX", "Codex"],
      ["CLAUDE", "Claude"],
    ]) {
      const option = node("option", "", label);
      option.value = value;
      select.append(option);
    }
    select.value = setting.provider || "AUTO";
    row.append(copy, select);
    elements.projectProviderSettings.append(row);
  }
  if (!state.projects.length) {
    elements.projectProviderSettings.append(
      node("p", "empty-copy", "Connect a project to configure its Master CLI.")
    );
  }
}

function renderHostToolSettings() {
  const profile = state.hostTools;
  if (!profile) return;
  elements.hostProfilePath.textContent = profile.profile_path || "Profile unavailable";
  elements.hostToolSettings.replaceChildren();
  for (const tool of ["python", "git", "codex", "grok", "claude"]) {
    const setting = profile.tools?.[tool] || {};
    const providerTool = ["codex", "grok", "claude"].includes(tool);
    const row = node("div", "host-tool-row");
    row.dataset.tool = tool;
    if (!providerTool) row.classList.add("host-tool-row-no-model");
    const heading = node("div", "host-tool-heading");
    heading.append(
      node("strong", "", tool === "python" ? "Python" : tool[0].toUpperCase() + tool.slice(1)),
      node(
        "small",
        setting.status === "AVAILABLE" ? "host-tool-available" : "host-tool-unavailable",
        setting.status === "AVAILABLE"
          ? `${setting.version || "Version unknown"} / ${setting.discovery_source || "Host Profile"}`
          : setting.reason || "Not configured"
      )
    );
    const input = document.createElement("input");
    input.type = "text";
    input.className = "host-tool-path";
    input.dataset.tool = tool;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = `Path to ${tool}.exe`;
    input.value = setting.executable === "UNKNOWN" ? "" : setting.executable || "";
    const actions = node("div", "host-tool-actions");
    const setButton = node("button", "secondary-button host-tool-set", "Set");
    setButton.type = "button";
    setButton.dataset.tool = tool;
    const verifyButton = node("button", "icon-button host-tool-verify", "✓");
    verifyButton.type = "button";
    verifyButton.dataset.tool = tool;
    verifyButton.title = `Verify ${tool}`;
    if (providerTool) {
      const modelInput = document.createElement("input");
      modelInput.type = "text";
      modelInput.className = "host-tool-model";
      modelInput.dataset.tool = tool;
      modelInput.autocomplete = "off";
      modelInput.spellcheck = false;
      modelInput.placeholder = "Model";
      modelInput.value = setting.model || "default";
      const modelButton = node(
        "button",
        "secondary-button host-tool-model-set",
        "Model"
      );
      modelButton.type = "button";
      modelButton.dataset.tool = tool;
      modelButton.title = `Set ${tool} model`;
      actions.append(modelButton, setButton, verifyButton);
      row.append(heading, input, modelInput, actions);
    } else {
      actions.append(setButton, verifyButton);
      row.append(heading, input, actions);
    }
    elements.hostToolSettings.append(row);
  }
}

async function discoverHostTools() {
  elements.settingsError.textContent = "";
  elements.discoverHostTools.disabled = true;
  try {
    state.hostTools = await api("/v1/settings/host-tools/discover", {
      method: "POST",
      body: {},
    });
    renderHostToolSettings();
    toast("Host tools discovered");
  } catch (error) {
    elements.settingsError.textContent = error.message;
  } finally {
    elements.discoverHostTools.disabled = false;
  }
}

async function updateHostTool(tool, operation) {
  elements.settingsError.textContent = "";
  const input = elements.hostToolSettings.querySelector(
    `.host-tool-path[data-tool="${tool}"]`
  );
  const modelInput = elements.hostToolSettings.querySelector(
    `.host-tool-model[data-tool="${tool}"]`
  );
  const options =
    operation === "select"
      ? { method: "POST", body: { executable: input?.value.trim() || "" } }
      : operation === "model"
        ? { method: "POST", body: { model: modelInput?.value.trim() || "" } }
        : { method: "POST", body: {} };
  state.hostTools = await api(
    `/v1/settings/host-tools/${encodeURIComponent(tool)}/${operation}`,
    options
  );
  renderHostToolSettings();
  toast(
    `${tool} ${
      operation === "select"
        ? "saved"
        : operation === "model"
          ? "model saved"
          : "verified"
    }`
  );
}

function renderLocalServiceStatus() {
  if (!elements.localServiceStatus) return;
  elements.localServiceStatus.replaceChildren();
  const health = elements.serviceStatus?.dataset?.state || "loading";
  const ready = health === "ready";
  const status = node("strong", "", ready ? "READY" : elements.serviceStatus?.textContent || "UNKNOWN");
  status.dataset.state = ready ? "ready" : "error";
  elements.localServiceStatus.append(
    status,
    node(
      "span",
      "",
      ready
        ? "Local loopback service is answering /health."
        : "Service is not READY. Start it from CLI or Start Menu."
    ),
    node(
      "small",
      "",
      "Control: python tools/universe_server.py status | start | stop | restart"
    )
  );
  if (typeof refreshLawStrip === "function") refreshLawStrip();
}

async function openProviderSettings() {
  elements.settingsError.textContent = "";
  [state.providerSettings, state.hostTools, state.serviceSettings] =
    await Promise.all([
      api("/v1/settings/providers"),
      api("/v1/settings/host-tools"),
      api("/v1/settings/service").catch(() => null),
    ]);
  if (elements.memoryMaintainInterval && state.serviceSettings?.memory_maintain) {
    elements.memoryMaintainInterval.value = String(
      state.serviceSettings.memory_maintain.interval_hours ?? 0
    );
  }
  if (elements.memoryMaintainStatus) {
    const worker = state.serviceSettings?.worker;
    elements.memoryMaintainStatus.textContent = worker
      ? `Worker ${worker.status} · interval ${worker.interval_hours}h` +
        (worker.last_run?.ran_at ? ` · last ${worker.last_run.ran_at}` : "")
      : "0 = off. Server runs HEURISTIC maintain when > 0.";
  }
  renderProviderSettings();
  renderHostToolSettings();
  renderLocalServiceStatus();
  elements.settingsDialog.showModal();
}

async function submitProviderSettings(event) {
  event.preventDefault();
  elements.settingsError.textContent = "";
  elements.settingsForm.querySelector("button[type='submit']").disabled = true;
  try {
    const requests = [
      api("/v1/settings/providers/universe", {
        method: "POST",
        body: { provider: elements.universeProviderSetting.value },
      }),
    ];
    for (const select of elements.projectProviderSettings.querySelectorAll(
      ".project-provider-select"
    )) {
      requests.push(
        api(`/v1/projects/${encodeURIComponent(select.dataset.projectId)}/provider-setting`, {
          method: "POST",
          body: { provider: select.value },
        })
      );
    }
    await Promise.all(requests);
    if (elements.memoryMaintainInterval) {
      const hours = Number(elements.memoryMaintainInterval.value || 0);
      state.serviceSettings = await api("/v1/settings/service", {
        method: "POST",
        body: { memory_maintain: { interval_hours: Number.isFinite(hours) ? hours : 0 } },
      });
    }
    state.providerSettings = await api("/v1/settings/providers");
    renderProviderSettings();
    renderComposerState();
    elements.settingsDialog.close();
    toast("Settings saved");
  } catch (error) {
    elements.settingsError.textContent = error.message;
  } finally {
    elements.settingsForm.querySelector("button[type='submit']").disabled = false;
  }
}

function conductorDeliveryLabel(deliveryState) {
  return (
    {
      QUEUED: "Queued",
      WAITING_FOR_RUNTIME_BINDING: "Waiting for Runtime",
      PROCESSING: "Thinking",
      ANSWERED: "Answered",
      FAILED: "Failed",
      RECORDED_FOR_CONDUCTOR: "Recorded before LLM connection",
    }[deliveryState] || deliveryState || "Unknown"
  );
}

async function refreshConductorRoom() {
  if (
    state.conductorRefreshInFlight ||
    state.conversationTarget.kind !== "UNIVERSE_CONDUCTOR"
  ) {
    return;
  }
  state.conductorRefreshInFlight = true;
  try {
    const result = await api("/v1/conductor-room/messages");
    state.conductorMessages = result.messages || [];
    state.conductorRuntimeBinding = result.runtime_binding || null;
    renderComposerState();
    renderRoomMessages();
  } catch (error) {
    console.warn("Conductor room refresh failed", error);
  } finally {
    state.conductorRefreshInFlight = false;
  }
}

function closeProjectRoomStream() {
  if (state.projectRoomStream) {
    state.projectRoomStream.close();
  }
  state.projectRoomStream = null;
  state.projectRoomStreamProjectId = null;
  state.projectStreamReplies = {};
}

function openProjectRoomStream(projectId) {
  if (
    state.projectRoomStream &&
    state.projectRoomStreamProjectId === projectId
  ) {
    return;
  }
  closeProjectRoomStream();
  const source = new EventSource(
    `/v1/projects/${encodeURIComponent(projectId)}/room/stream`
  );
  state.projectRoomStream = source;
  state.projectRoomStreamProjectId = projectId;
  source.addEventListener("project-room", (event) => {
    let envelope;
    try {
      envelope = JSON.parse(event.data);
    } catch (error) {
      console.warn("Project Room stream payload is invalid", error);
      return;
    }
    const payload = envelope.payload || {};
    if (payload.type === "SNAPSHOT" || payload.type === "ROOM_CHANGED") {
      state.roomMessages = Array.isArray(payload.messages)
        ? payload.messages
        : [];
      state.projectPermissions = Array.isArray(payload.permissions)
        ? payload.permissions
        : state.projectPermissions;
      if (payload.type === "ROOM_CHANGED") {
        state.projectStreamReplies = {};
      }
      renderRoomMessages();
      return;
    }
    if (payload.type === "AGENT_PERMISSION") {
      const permission = payload.permission;
      if (permission?.request_id) {
        state.projectPermissions = [
          ...state.projectPermissions.filter(
            (item) => item.request_id !== permission.request_id
          ),
          permission,
        ];
        renderRoomMessages();
      }
      return;
    }
    if (payload.type !== "MASTER_STREAM") return;
    const key = payload.in_reply_to;
    if (payload.event === "COMPLETED") {
      delete state.projectStreamReplies[key];
    } else if (payload.event === "FAILED") {
      state.projectStreamReplies[key] = {
        body: state.projectStreamReplies[key]?.body || "",
        state: payload.detail || "Failed",
      };
    } else {
      const current = state.projectStreamReplies[key] || {
        body: "",
        state: "Thinking",
      };
      if (payload.event === "DELTA") {
        current.body += payload.delta || "";
        current.state = "Responding";
      }
      state.projectStreamReplies[key] = current;
    }
    renderRoomMessages();
  });
  source.addEventListener("error", () => {
    if (state.projectRoomStream === source) {
      elements.roomHint.textContent = "Project Master reconnecting";
    }
  });
}

function buildGraph() {
  if (!state.selectedProject) {
    state.graph.nodes = [];
    state.graph.edges = [];
    drawGraph();
    return;
  }
  elements.nodeBreadcrumb.classList.add("hidden");
  if (state.view === "timeline") {
    buildTimelineGraph();
    return;
  }
  const graphNodes = [
    {
      id: `project:${state.selectedProject.project_id}`,
      label: state.selectedProject.project_id,
      kind: "project",
      data: state.selectedProject,
    },
  ];
  const graphEdges = [];
  const projection = state.projection;
  if (projection) {
    if (state.view === "implementation") {
      for (const item of projection.nodes || []) {
        graphNodes.push({
          id: `node:${item.node_id}`,
          label: item.title,
          kind: "system",
          data: item,
        });
        graphEdges.push({
          from: graphNodes[0].id,
          to: `node:${item.node_id}`,
          kind: "contains",
        });
      }
      for (const item of projection.implementation?.nodes || []) {
        graphNodes.push({
          id: `implementation:${item.implementation_id}`,
          label: item.title,
          kind: "implementation",
          data: item,
        });
      }
      for (const binding of projection.implementation_bindings || []) {
        graphEdges.push({
          from: `node:${binding.functional_node_id}`,
          to: `implementation:${binding.implementation_node_id}`,
          kind: "implementation-binding",
        });
      }
      layoutGraph(graphNodes, state.view);
      state.graph.nodes = graphNodes;
      state.graph.edges = graphEdges;
      state.graph.scale = 1;
      state.graph.x = 0;
      state.graph.y = 0;
      elements.graphEmpty.classList.toggle("hidden", graphNodes.length > 0);
      drawGraph();
      return;
    }
    for (const item of projection.nodes || []) {
      graphNodes.push({
        id: `node:${item.node_id}`,
        label: item.title,
        kind: "system",
        data: item,
      });
      graphEdges.push({
        from: graphNodes[0].id,
        to: `node:${item.node_id}`,
        kind: "contains",
      });
    }
    for (const edge of projection.edges || []) {
      graphEdges.push({
        from: `node:${edge.from_node}`,
        to: `node:${edge.to_node}`,
        kind: edge.kind,
      });
    }
    if (state.view === "documents") {
      for (const item of projection.documents || []) {
        graphNodes.push({
          id: `document:${item.document_id}`,
          label: item.title || readableLabel(item.document_id),
          kind: "document",
          data: item,
        });
        const owners = item.node_ids?.length
          ? item.node_ids
          : [state.selectedProject.project_id];
        for (const owner of owners) {
          graphEdges.push({
            from:
              owner === state.selectedProject.project_id
                ? graphNodes[0].id
                : `node:${owner}`,
            to: `document:${item.document_id}`,
            kind: "documents",
          });
        }
      }
    }
    if (state.view === "future") {
      for (const item of projection.predicted_paths || []) {
        graphNodes.push({
          id: `predicted:${item.candidate_id}`,
          label: item.action.replaceAll("_", " "),
          kind: "predicted",
          data: item,
        });
        const target = item.subject_ref?.startsWith("node:")
          ? item.subject_ref
          : graphNodes[0].id;
        graphEdges.push({
          from: target,
          to: `predicted:${item.candidate_id}`,
          kind: "predicts",
        });
      }
    }
  }
  layoutGraph(graphNodes, state.view);
  state.graph.nodes = graphNodes;
  state.graph.edges = graphEdges;
  state.graph.scale = 1;
  state.graph.x = 0;
  state.graph.y = 0;
  elements.graphEmpty.classList.toggle("hidden", graphNodes.length > 0);
  drawGraph();
}

function focusedProjectionNode() {
  return (state.projection?.nodes || []).find(
    (item) => item.node_id === state.focusedNodeId
  );
}

function buildNodeUniverseGraph() {
  const focus = focusedProjectionNode();
  if (!focus) {
    state.focusedNodeId = null;
    buildGraph();
    return;
  }
  const projection = state.projection || {};
  const focusId = `node:${focus.node_id}`;
  const graphNodes = [
    {
      id: focusId,
      label: focus.title,
      kind: "focus",
      data: focus,
      x: 0,
      y: 0,
    },
  ];
  const graphEdges = [];
  const visibleIds = new Set([focus.node_id]);
  for (const edge of projection.edges || []) {
    if (edge.from_node === focus.node_id) visibleIds.add(edge.to_node);
    if (edge.to_node === focus.node_id) visibleIds.add(edge.from_node);
  }
  for (const item of projection.nodes || []) {
    if (item.node_id === focus.node_id || !visibleIds.has(item.node_id)) continue;
    graphNodes.push({
      id: `node:${item.node_id}`,
      label: item.title,
      kind: "related",
      data: item,
    });
  }
  for (const edge of projection.edges || []) {
    if (!visibleIds.has(edge.from_node) || !visibleIds.has(edge.to_node)) continue;
    graphEdges.push({
      from: `node:${edge.from_node}`,
      to: `node:${edge.to_node}`,
      kind: edge.kind,
    });
  }
  for (const binding of projection.implementation_bindings || []) {
    if (binding.functional_node_id !== focus.node_id) continue;
    const implementation = (projection.implementation?.nodes || []).find(
      (item) => item.implementation_id === binding.implementation_node_id
    );
    if (!implementation) continue;
    graphNodes.push({
      id: `implementation:${implementation.implementation_id}`,
      label: implementation.title,
      kind: "implementation",
      data: implementation,
    });
    graphEdges.push({
      from: focusId,
      to: `implementation:${implementation.implementation_id}`,
      kind: "implementation-binding",
    });
  }
  for (const document of projection.documents || []) {
    if (!document.node_ids?.includes(focus.node_id)) continue;
    graphNodes.push({
      id: `document:${document.document_id}`,
      label: document.title || readableLabel(document.document_id),
      kind: "document",
      data: document,
    });
    graphEdges.push({
      from: focusId,
      to: `document:${document.document_id}`,
      kind: "documents",
    });
  }
  for (const candidate of projection.predicted_paths || []) {
    if (candidate.subject_ref !== focusId) continue;
    graphNodes.push({
      id: `predicted:${candidate.candidate_id}`,
      label: readableLabel(candidate.action),
      kind: "predicted",
      data: candidate,
    });
    graphEdges.push({ from: focusId, to: `predicted:${candidate.candidate_id}`, kind: "predicts" });
  }
  layoutNodeUniverseGraph(graphNodes);
  state.graph.nodes = graphNodes;
  state.graph.edges = graphEdges;
  state.graph.scale = 1;
  state.graph.x = 0;
  state.graph.y = 0;
  state.selectedNode = graphNodes.find((item) => item.id === focusId) || null;
  elements.graphEmpty.classList.toggle("hidden", graphNodes.length > 0);
  renderNodeBreadcrumb(focus);
  drawGraph();
}

function layoutNodeUniverseGraph(graphNodes) {
  const focus = graphNodes[0];
  focus.x = 0;
  focus.y = 0;
  const groups = {
    related: graphNodes.filter((item) => item.kind === "related"),
    implementation: graphNodes.filter((item) => item.kind === "implementation"),
    document: graphNodes.filter((item) => item.kind === "document"),
    predicted: graphNodes.filter((item) => item.kind === "predicted"),
  };
  const placements = [
    [groups.related, -148, -86, 78],
    [groups.implementation, 148, -86, 74],
    [groups.document, 148, 94, 70],
    [groups.predicted, -148, 94, 70],
  ];
  for (const [items, x, y, spread] of placements) {
    items.forEach((item, index) => {
      item.x = x + (index - (items.length - 1) / 2) * spread;
      item.y = y + (index % 2 ? 28 : 0);
    });
  }
}

function renderNodeBreadcrumb(focus) {
  const isFocused = Boolean(focus && state.selectedProject);
  elements.nodeBreadcrumb.classList.toggle("hidden", !isFocused);
  if (!isFocused) return;
  elements.nodeBreadcrumbProject.textContent = state.selectedProject.project_id;
  elements.nodeBreadcrumbNode.textContent = focus.title;
}

function exitNodeUniverse() {
  if (!state.focusedNodeId) return;
  state.focusedNodeId = null;
  state.selectedNode = null;
  elements.nodeBreadcrumb.classList.add("hidden");
  buildGraph();
  renderDetails();
}

function closeInspector() {
  state.focusedNodeId = null;
  state.selectedNode = null;
  state.inspectorDismissed = true;
  document.body.classList.remove("inspector-open");
  buildGraph();
  renderDetails();
}

function buildTimelineGraph() {
  const project = state.selectedProject;
  const projection = state.projection || {};
  const projectData = projection.project || project;
  const functional = projection.nodes || [];
  const focus = focusedProjectionNode();
  const rootSelected = state.selectedNode?.kind === "project";
  const spacing = Math.max(138, Math.min(184, 660 / Math.max(functional.length, 1)));
  const startX = -((Math.max(functional.length, 1) - 1) * spacing) / 2;
  const nodes = [
    {
      id: `project:${project.project_id}`,
      label: "Development start",
      kind: "project",
      data: projectData,
      x: startX - spacing * 1.18,
      y: 0,
    },
  ];
  const edges = [];

  functional.forEach((item, index) => {
    const id = `node:${item.node_id}`;
    nodes.push({
      id,
      label: item.title,
      kind: focus?.node_id === item.node_id ? "focus" : "system",
      data: item,
      x: startX + index * spacing,
      y: index % 2 ? -84 : 84,
    });
    edges.push({
      from: index ? `node:${functional[index - 1].node_id}` : `project:${project.project_id}`,
      to: id,
      kind: "adopted",
    });
  });

  if (!functional.length) {
    nodes.push({ id: "seed:pending", label: "Prepare Project Seed", kind: "predicted", data: {}, x: -80, y: 0 });
    edges.push({ from: `project:${project.project_id}`, to: "seed:pending", kind: "predicts" });
  }

  if (!focus && !rootSelected) {
    const documentsByOwner = new Map();
    for (const document of projection.documents || []) {
      const ownerId = document.node_ids?.[0] ? `node:${document.node_ids[0]}` : `project:${project.project_id}`;
      const group = documentsByOwner.get(ownerId) || [];
      group.push(document);
      documentsByOwner.set(ownerId, group);
    }
    for (const [ownerId, documents] of documentsByOwner) {
      const owner = nodes.find((item) => item.id === ownerId) || nodes[0];
      documents.forEach((document, index) => {
        const id = `document:${document.document_id}`;
        const isProjectDocument = owner.kind === "project";
        nodes.push({
          id,
          label: document.title || readableLabel(document.document_id),
          kind: "document",
          data: document,
          x: owner.x + (index - (documents.length - 1) / 2) * 126,
          y: isProjectDocument ? -154 : owner.y + (owner.y >= 0 ? 136 : -136),
        });
        edges.push({ from: owner.id, to: id, kind: "documents" });
      });
    }
  }

  if (focus) {
    const focusGraphId = `node:${focus.node_id}`;
    const focusGraph = nodes.find((item) => item.id === focusGraphId);
    const focusX = focusGraph?.x || 0;
    const focusY = focusGraph?.y || 0;
    const implementationNodes = projection.implementation?.nodes || [];
    const implementationBindings = (projection.implementation_bindings || []).filter(
      (item) => item.functional_node_id === focus.node_id
    );
    implementationBindings.forEach((binding, index) => {
      const implementation = implementationNodes.find(
        (item) => item.implementation_id === binding.implementation_node_id
      );
      if (!implementation) return;
      const id = `implementation:${implementation.implementation_id}`;
      nodes.push({
        id,
        label: implementation.title,
        kind: "implementation",
        data: implementation,
        x: focusX + (index - (implementationBindings.length - 1) / 2) * 126,
        y: focusY - 154,
      });
      edges.push({ from: focusGraphId, to: id, kind: "implementation-binding" });
    });

    const documents = (projection.documents || []).filter((item) =>
      item.node_ids?.includes(focus.node_id)
    );
    documents.forEach((document, index) => {
      const id = `document:${document.document_id}`;
      nodes.push({
        id,
        label: document.title || readableLabel(document.document_id),
        kind: "document",
        data: document,
        x: focusX + (index - (documents.length - 1) / 2) * 126,
        y: focusY + 154,
      });
      edges.push({ from: focusGraphId, to: id, kind: "documents" });
    });

    const predicted = (projection.predicted_paths || []).filter(
      (item) => item.subject_ref === focusGraphId
    );
    predicted.forEach((item, index) => {
      const id = `predicted:${item.candidate_id}`;
      nodes.push({
        id,
        label: readableLabel(item.action),
        kind: "predicted",
        data: item,
        x: focusX + 170 + index * 120,
        y: focusY + 16 + index * 68,
      });
      edges.push({ from: focusGraphId, to: id, kind: "predicts" });
    });
  } else if (rootSelected) {
    const rootDocuments = (projection.documents || []).filter(
      (item) => item.project_wide === true
    );
    rootDocuments.forEach((document, index) => {
      const id = `document:${document.document_id}`;
      nodes.push({
        id,
        label: document.title || readableLabel(document.document_id),
        kind: "document",
        data: document,
        x: nodes[0].x + (index - (rootDocuments.length - 1) / 2) * 126,
        y: -150,
      });
      edges.push({ from: nodes[0].id, to: id, kind: "documents" });
    });
  } else {
    const last = functional.length ? `node:${functional[functional.length - 1].node_id}` : `project:${project.project_id}`;
    (projection.predicted_paths || []).slice(0, 3).forEach((item, index) => {
      const id = `predicted:${item.candidate_id}`;
      nodes.push({
        id,
        label: readableLabel(item.action),
        kind: "predicted",
        data: item,
        x: startX + Math.max(functional.length, 1) * spacing + index * 118,
        y: 150 + index * 72,
      });
      edges.push({ from: last, to: id, kind: "predicts" });
    });
  }

  state.graph.nodes = nodes;
  state.graph.edges = edges;
  state.graph.scale = 1;
  state.graph.x =
    window.innerWidth <= 720 && functional.length
      ? spacing * (functional.length / 2 + 0.2)
      : 0;
  state.graph.y = 0;
  elements.graphEmpty.classList.toggle("hidden", nodes.length > 0);
  elements.nodeBreadcrumb.classList.add("hidden");
  drawGraph();
}
function readableLabel(value) {
  return String(value)
    .replaceAll("-", " ")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function layoutGraph(graphNodes, view) {
  const project = graphNodes[0];
  project.x = 0;
  project.y = 0;
  const systems = graphNodes.filter((item) => item.kind === "system");
  if (elements.canvas.clientWidth <= 540) {
    layoutCompactGraph(project, systems, graphNodes, view);
    return;
  }
  if (view !== "documents") {
    graphNodes.slice(1).forEach((item, index, items) => {
      const angle = (Math.PI * 2 * index) / Math.max(items.length, 1) - Math.PI / 2;
      const radius = 170 + (index % 3) * 32;
      item.x = Math.cos(angle) * radius;
      item.y = Math.sin(angle) * radius;
    });
    return;
  }

  const systemById = new Map();
  systems.forEach((item, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(systems.length, 1) - Math.PI / 2;
    item.x = Math.cos(angle) * 185;
    item.y = Math.sin(angle) * 185;
    systemById.set(item.data.node_id, item);
  });

  const documentsByOwner = new Map();
  for (const item of graphNodes.filter((candidate) => candidate.kind === "document")) {
    const ownerId = item.data.node_ids?.[0] || "";
    const owned = documentsByOwner.get(ownerId) || [];
    owned.push(item);
    documentsByOwner.set(ownerId, owned);
  }
  for (const [ownerId, documents] of documentsByOwner) {
    const owner = systemById.get(ownerId) || project;
    if (owner === project) {
      documents.forEach((item, index) => {
        const spread = (index - (documents.length - 1) / 2) * 132;
        item.x = spread;
        item.y = -292;
      });
      continue;
    }
    const length = Math.hypot(owner.x, owner.y) || 1;
    const outwardX = owner.x / length;
    const outwardY = owner.y / length;
    const perpendicularX = -outwardY;
    const perpendicularY = outwardX;
    documents.forEach((item, index) => {
      const spread = (index - (documents.length - 1) / 2) * 62;
      item.x = owner.x + outwardX * 154 + perpendicularX * spread;
      item.y = owner.y + outwardY * 154 + perpendicularY * spread;
    });
  }
}

function layoutCompactGraph(project, systems, graphNodes, view) {
  const positions = [
    [-104, -92],
    [104, -92],
    [-104, 92],
    [104, 92],
  ];
  const systemById = new Map();
  systems.forEach((item, index) => {
    const [x, y] = positions[index % positions.length];
    item.x = x;
    item.y = y + Math.floor(index / positions.length) * 110;
    systemById.set(item.data.node_id, item);
  });

  if (view === "documents") {
    const documentsByOwner = new Map();
    for (const item of graphNodes.filter((candidate) => candidate.kind === "document")) {
      const ownerId = item.data.node_ids?.[0] || "";
      const owned = documentsByOwner.get(ownerId) || [];
      owned.push(item);
      documentsByOwner.set(ownerId, owned);
    }
    for (const [ownerId, documents] of documentsByOwner) {
      const owner = systemById.get(ownerId) || project;
      if (owner === project) {
        documents.forEach((item, index) => {
          item.x = (index - (documents.length - 1) / 2) * 132;
          item.y = -212;
        });
        continue;
      }
      const direction = owner.y <= project.y ? -1 : 1;
      documents.forEach((item, index) => {
        const spread = (index - (documents.length - 1) / 2) * 56;
        item.x = owner.x + spread;
        item.y = owner.y + direction * 72;
      });
    }
    return;
  }

  graphNodes
    .filter((item) => item.kind !== "project" && item.kind !== "system")
    .forEach((item, index) => {
      item.x = index % 2 === 0 ? -104 : 104;
      item.y = 190 + Math.floor(index / 2) * 78;
    });
}

function canvasMetrics() {
  const rect = elements.canvas.getBoundingClientRect();
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.floor(rect.width * ratio));
  const height = Math.max(1, Math.floor(rect.height * ratio));
  if (elements.canvas.width !== width || elements.canvas.height !== height) {
    elements.canvas.width = width;
    elements.canvas.height = height;
  }
  return { rect, ratio, width, height };
}

function drawGraph() {
  updateGraphChrome();
  const { ratio, width, height } = canvasMetrics();
  const context = elements.canvas.getContext("2d");
  context.clearRect(0, 0, width, height);
  context.save();
  context.scale(ratio, ratio);
  const viewportWidth = width / ratio;
  const viewportHeight = height / ratio;
  // Stable points make the observation surface feel spatial without implying data.
  for (let index = 0; index < 96; index += 1) {
    const x = (index * 149 + 41) % viewportWidth;
    const y = (index * 83 + 67) % viewportHeight;
    const radius = index % 11 === 0 ? 1.4 : 0.65;
    context.fillStyle = index % 11 === 0 ? "rgba(114, 189, 255, 0.58)" : "rgba(178, 205, 244, 0.24)";
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fill();
  }
  const centerX = width / ratio / 2 + state.graph.x;
  const centerY = height / ratio / 2 + state.graph.y;
  context.translate(centerX, centerY);
  context.scale(state.graph.scale, state.graph.scale);
  const byId = new Map(state.graph.nodes.map((item) => [item.id, item]));
  for (const edge of state.graph.edges) {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    if (!from || !to) continue;
    const isDocumentLink = edge.kind === "documents";
    const isPredicted = edge.kind === "predicts";
    const isImplementationLink = edge.kind === "implementation-binding";
    context.lineWidth = isDocumentLink ? 1.4 : 1.2;
    context.strokeStyle = isPredicted
      ? "rgba(155, 124, 255, 0.75)"
      : isDocumentLink
      ? "rgba(240, 184, 74, 0.7)"
      : isImplementationLink
        ? "rgba(122, 106, 212, 0.7)"
        : "rgba(120, 180, 230, 0.35)";
    context.setLineDash(isPredicted ? [7, 6] : isDocumentLink ? [5, 4] : isImplementationLink ? [3, 3] : []);
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.stroke();
  }
  context.setLineDash([]);
  for (const item of state.graph.nodes) {
    const selected = state.selectedNode?.id === item.id;
    const color = {
      project: "#3d7ecf",
      system: "#1769aa",
      focus: "#4ec4ff",
      related: "#61a8ff",
      document: "#c48a2a",
      implementation: "#7a6ad4",
      predicted: "#c45b58",
    }[item.kind];
    const widthValue = ["project", "focus"].includes(item.kind) ? 126 : 108;
    const heightValue = ["project", "focus"].includes(item.kind) ? 42 : 36;
    const x0 = item.x - widthValue / 2;
    const y0 = item.y - heightValue / 2;
    if (selected) {
      context.shadowColor = "rgba(61, 224, 255, 0.55)";
      context.shadowBlur = 18;
    } else {
      context.shadowColor = "rgba(61, 224, 255, 0.12)";
      context.shadowBlur = 8;
    }
    context.fillStyle = selected
      ? "rgba(12, 28, 52, 0.92)"
      : item.kind === "focus"
        ? "rgba(16, 34, 58, 0.9)"
        : "rgba(10, 22, 42, 0.88)";
    context.strokeStyle = selected ? "#3de0ff" : color;
    context.lineWidth = selected ? 2.4 : 1.5;
    roundedRect(context, x0, y0, widthValue, heightValue, 10);
    context.fill();
    context.stroke();
    context.shadowBlur = 0;
    context.fillStyle = selected ? "#eaf8ff" : "#d7e7f8";
    context.font = `${item.kind === "project" ? "600" : "500"} 11px Segoe UI`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(
      truncate(item.label, 18),
      item.x,
      item.y,
      widthValue - 12
    );
    const todoCount = openTodosForGraphNode(item).length;
    if (todoCount) {
      context.fillStyle = "#f6c76a";
      context.beginPath();
      context.arc(
        item.x + widthValue / 2 - 4,
        item.y - heightValue / 2 + 4,
        9,
        0,
        Math.PI * 2
      );
      context.fill();
      context.fillStyle = "#111827";
      context.font = "700 9px Segoe UI";
      context.fillText(String(Math.min(todoCount, 99)), item.x + widthValue / 2 - 4, item.y - heightValue / 2 + 4);
    }
  }
  context.restore();
}

function roundedRect(context, x, y, width, height, radius) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
}

function truncate(value, maximum) {
  const text = String(value || "");
  return text.length > maximum ? `${text.slice(0, maximum - 1)}...` : text;
}

function graphPoint(event) {
  const rect = elements.canvas.getBoundingClientRect();
  return {
    x:
      (event.clientX - rect.left - rect.width / 2 - state.graph.x) /
      state.graph.scale,
    y:
      (event.clientY - rect.top - rect.height / 2 - state.graph.y) /
      state.graph.scale,
  };
}

function selectGraphNode(event) {
  if (state.graphPan?.moved) return;
  const point = graphPoint(event);
  const selected =
    [...state.graph.nodes]
      .reverse()
      .find(
        (item) =>
          Math.abs(point.x - item.x) <= 58 &&
          Math.abs(point.y - item.y) <= 24
      ) || null;
  if (!selected) return;
  state.inspectorDismissed = false;
  if (selected.kind === "project") {
    state.focusedNodeId = null;
    state.selectedNode = selected;
    buildGraph();
    renderDetails();
    showInspectorTab("details");
    return;
  }
  if (["system", "related", "focus"].includes(selected.kind)) {
    state.focusedNodeId =
      state.focusedNodeId === selected.data.node_id ? null : selected.data.node_id;
    state.selectedNode = selected;
    buildGraph();
    renderDetails();
    showInspectorTab("details");
    return;
  }
  state.selectedNode = selected;
  drawGraph();
  renderDetails();
  showInspectorTab("details");
}

function renderDetails() {
  elements.details.replaceChildren();
  if (!state.selectedProject) {
    document.body.classList.remove("inspector-open");
    elements.details.append(node("p", "empty-copy", "No project selected"));
    return;
  }
  const selected = state.selectedNode;
  // Project selection keeps inspector available unless the user dismissed it.
  document.body.classList.toggle(
    "inspector-open",
    Boolean(state.selectedProject) && !state.inspectorDismissed
  );
  const heading = node("div", "detail-group");
  heading.append(
    node(
      "h2",
      "",
      selected?.label || state.selectedProject.project_id
    )
  );
  const grid = node("dl", "detail-grid");
  const data = selected?.data || state.projection?.project || state.selectedProject;
  addDetail(grid, "Type", selected?.kind || "project");
  addDetail(
    grid,
    "State",
    selected?.kind === "predicted" ? "USER_SELECTION_REQUIRED" : "CURRENT"
  );
  if (data.kind) addDetail(grid, "Kind", data.kind);
  if (data.role) addDetail(grid, "Role", data.role);
  if (data.goal) addDetail(grid, "Goal", data.goal);
  if (data.technologies?.length) addDetail(grid, "Technologies", data.technologies.join(", "));
  if (data.node_ids?.length) addDetail(grid, "Related to", data.node_ids.join(", "));
  if (data.path) addDetail(grid, "Path", data.path);
  if (data.project_root) addDetail(grid, "Root", data.project_root);
  if (data.symbol) addDetail(grid, "Symbol", data.symbol);
  if (selected?.kind === "project" && state.projection?.source?.commit) {
    addDetail(grid, "Source commit", state.projection.source.commit);
  }
  heading.append(grid);
  elements.details.append(heading);

  const matchingTodos = todosForSelectedContext().filter(
    (todo) => todo.state !== "DONE"
  );
  const todoGroup = node("div", "detail-group");
  const todoHeading = node("div", "detail-heading-row");
  todoHeading.append(
    node("h3", "", `Todo (${matchingTodos.length})`)
  );
  const addTodo = node("button", "icon-button compact", "+");
  addTodo.type = "button";
  addTodo.title = "Add Todo for this context";
  addTodo.setAttribute("aria-label", addTodo.title);
  addTodo.addEventListener("click", () => openTodoDialog(true));
  todoHeading.append(addTodo);
  todoGroup.append(todoHeading);
  if (!matchingTodos.length) {
    todoGroup.append(node("p", "empty-copy", "No open Todo for this context"));
  } else {
    const list = node("ul", "context-list todo-context-list");
    for (const todo of matchingTodos.slice(0, 6)) {
      const row = node("li", "todo-context-item");
      const label = node(
        "button",
        "todo-context-open",
        `${todo.priority} / ${todo.state} / ${todo.title}`
      );
      label.type = "button";
      label.title = "Open Todo work map";
      label.addEventListener("click", () => openTodoDialog(true));
      row.append(label);
      list.append(row);
    }
    todoGroup.append(list);
  }
  elements.details.append(todoGroup);

  if (state.selectedProject) {
    const handoffGroup = node("div", "detail-group");
    const handoffHeading = node("div", "detail-heading-row");
    handoffHeading.append(
      node("h3", "", `Master handoffs (${state.masterHandoffs.length})`)
    );
    handoffGroup.append(handoffHeading);
    if (!state.masterHandoffs.length) {
      handoffGroup.append(
        node(
          "p",
          "empty-copy",
          "No handoff proposals yet. Adopt a Skill Plan or Fresh Composition, then propose delivery."
        )
      );
    } else {
      const list = node("ul", "context-list");
      for (const handoff of state.masterHandoffs.slice(0, 6)) {
        const row = node("li", "handoff-context-item");
        const sourceKind = handoff.source?.kind || "UNKNOWN";
        row.append(
          node(
            "span",
            "",
            `${handoff.delivery_state} · ${sourceKind} · ${handoff.handoff_id.slice(0, 18)}…`
          )
        );
        if (handoff.delivery_state === "PROPOSAL_ONLY") {
          const deliver = node("button", "handoff-action", "Deliver");
          deliver.type = "button";
          deliver.title = "Deliver handoff to Project Master (approval=DELIVER)";
          deliver.addEventListener("click", () =>
            deliverMasterHandoff(state.selectedProject.project_id, handoff)
          );
          row.append(deliver);
        }
        list.append(row);
      }
      handoffGroup.append(list);
    }
    if (state.skillPlanAdoptions.length) {
      const undelivered = undeliveredSkillPlanAdoptions();
      if (undelivered.length) {
        const propose = node(
          "button",
          "secondary-button compact-action",
          `Propose Skill Plan handoff (${undelivered.length})`
        );
        propose.type = "button";
        propose.addEventListener("click", () =>
          proposeSkillPlanHandoff(undelivered[0])
        );
        handoffGroup.append(propose);
      }
    }
    elements.details.append(handoffGroup);
  }

  const isProjectContext = !selected || selected.kind === "project";
  const relatedDocuments = (state.projection?.documents || []).filter((item) =>
    ["system", "related", "focus"].includes(selected?.kind)
      ? item.node_ids?.includes(data.node_id)
      : item.project_wide === true
  );
  if (isProjectContext || relatedDocuments.length) {
    const contextGroup = node("div", "detail-group");
    contextGroup.append(node("h3", "", isProjectContext ? "Project context" : "Related documents"));
    if (isProjectContext && data.summary) {
      contextGroup.append(node("p", "context-copy", data.summary));
    }
    if (isProjectContext && data.working_rules?.length) {
      const rules = node("ul", "context-list");
      for (const rule of data.working_rules) rules.append(node("li", "", rule));
      contextGroup.append(rules);
    }
    if (relatedDocuments.length) {
      const references = node("ul", "document-references");
      for (const document of relatedDocuments) {
        references.append(
          node(
            "li",
            "",
            `${document.role} · ${document.title || readableLabel(document.document_id)}`
          )
        );
      }
      contextGroup.append(references);
    }
    elements.details.append(contextGroup);
  }

  const projectionGroup = node("div", "detail-group");
  projectionGroup.append(node("h3", "", "Projection"));
  const projectionGrid = node("dl", "detail-grid");
  addDetail(
    projectionGrid,
    "Nodes",
    String(state.projection?.nodes?.length || 0)
  );
  addDetail(
    projectionGrid,
    "Edges",
    String(state.projection?.edges?.length || 0)
  );
  addDetail(
    projectionGrid,
    "Documents",
    String(state.projection?.documents?.length || 0)
  );
  addDetail(
    projectionGrid,
    "Predicted",
    String(state.projection?.predicted_paths?.length || 0)
  );
  projectionGroup.append(projectionGrid);
  elements.details.append(projectionGroup);
}

function addDetail(list, key, value) {
  list.append(node("dt", "", key), node("dd", "", String(value)));
}

function renderActivity() {
  elements.activity.replaceChildren();
  if (!state.selectedProject) {
    elements.activity.append(node("p", "empty-copy", "No project selected"));
    return;
  }
  if (
    !state.dispatches.length &&
    !state.roomMessages.length &&
    !state.masterHandoffs.length
  ) {
    elements.activity.append(node("p", "empty-copy", "No activity yet"));
    return;
  }
  const timeline = node("div", "timeline");
  for (const handoff of state.masterHandoffs) {
    const row = node("div", "timeline-item handoff-item");
    const copy = node("div", "timeline-copy");
    const sourceKind = handoff.source?.kind || "UNKNOWN";
    copy.append(
      node("strong", "", `MASTER_HANDOFF / ${sourceKind}`),
      node(
        "small",
        "",
        `${handoff.delivery_state} · ${handoff.handoff_id}`
      ),
      node(
        "small",
        "",
        handoff.purpose || handoff.next_operation || "Project Master handoff"
      )
    );
    if (handoff.delivery_state === "PROPOSAL_ONLY") {
      const action = node("button", "timeline-action", "Deliver");
      action.type = "button";
      action.addEventListener("click", () =>
        deliverMasterHandoff(state.selectedProject.project_id, handoff)
      );
      copy.append(action);
    }
    row.append(node("span"), copy);
    timeline.append(row);
  }
  for (const message of state.roomMessages) {
    const row = node("div", "timeline-item room-message");
    const copy = node("div", "timeline-copy");
    copy.append(
      node("strong", "", `${message.kind} / ${message.sender}`),
      node("small", "", message.body),
      node("small", "", `${message.delivery_state} / ${message.created_at}`)
    );
    row.append(node("span"), copy);
    timeline.append(row);
  }
  for (const item of state.dispatches) {
    const dispatch = item.dispatch;
    const row = node("div", "timeline-item");
    const copy = node("div", "timeline-copy");
    const title = node("strong", "", dispatch.title);
    const badge = node("span", "status-badge", dispatch.status);
    badge.dataset.status = dispatch.status;
    const meta = node(
      "small",
      "",
      `${dispatch.requested_mode} / ${item.updated_at}`
    );
    copy.append(title, badge, meta);
    if (Array.isArray(item.events)) {
      for (const event of item.events) {
        copy.append(
          node(
            "small",
            "event-evidence",
            `${event.status} · ${event.evidence_ref}`
          )
        );
      }
    }
    if (item.result_packet) {
      copy.append(
        node(
          "small",
          "result-summary",
          `Result · ${item.result_packet.summary}`
        )
      );
    }
    if (dispatch.status === "QUEUED") {
      const action = node("button", "timeline-action", "Deliver");
      action.type = "button";
      action.addEventListener("click", () => deliverDispatch(dispatch.dispatch_id));
      copy.append(action);
    }
    row.append(node("span"), copy);
    timeline.append(row);
  }
  elements.activity.append(timeline);
}

async function deliverDispatch(dispatchId) {
  if (!window.confirm("Write this dispatch to the Project MASTER inbox?")) {
    return;
  }
  try {
    await api(`/v1/dispatches/${encodeURIComponent(dispatchId)}/deliver`, {
      method: "POST",
      body: { approval: "APPROVED" },
    });
    toast("Dispatch delivered");
    await selectProject(state.selectedProject.project_id);
    showInspectorTab("activity");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderEmpty() {
  elements.workspaceTitle.textContent = "Project network";
  elements.workspaceSubtitle.textContent = "No project selected";
  renderComposerActions();
  renderComposerState();
  renderRoomMessages();
  document.body.classList.remove("inspector-open");
  elements.graphEmpty.classList.remove("hidden");
  state.graph.nodes = [];
  state.graph.edges = [];
  state.focusedNodeId = null;
  state.inspectorDismissed = false;
  elements.nodeBreadcrumb.classList.add("hidden");
  drawGraph();
  renderDetails();
  renderActivity();
  if (typeof renderBench === "function") renderBench();
}

async function submitDispatch(event) {
  event.preventDefault();
  const form = new FormData(elements.dispatchForm);
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    elements.dispatchSubmit.disabled = true;
    try {
      const result = await api("/v1/conductor-room/messages", {
        method: "POST",
        body: {
          kind: "QUESTION",
          sender: "USER",
          body: form.get("instruction"),
          provider: "AUTO",
          ui_context: conductorUiContext(),
          idempotency_key: crypto.randomUUID(),
        },
      });
      elements.dispatchForm.reset();
      state.conductorMessages = [
        ...state.conductorMessages.filter(
          (message) => message.message_id !== result.message.message_id
        ),
        result.message,
      ];
      state.conductorRuntimeBinding = result.runtime_binding || null;
      expandConversationLayer();
      renderComposerState();
      renderRoomMessages();
      toast("Message queued for Universe Conductor");
      window.setTimeout(refreshConductorRoom, 100);
    } catch (error) {
      toast(error.message, true);
    } finally {
      elements.dispatchSubmit.disabled = false;
    }
    return;
  }
  const targetProject = state.projects.find(
    (project) => project.project_id === state.conversationTarget.projectId
  );
  if (!targetProject) {
    toast("Project Master target is unavailable", true);
    return;
  }
  if (state.selectedProject?.project_id !== targetProject.project_id) {
    await selectProject(targetProject.project_id);
  }
  elements.dispatchSubmit.disabled = true;
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        targetProject.project_id
      )}/room/messages`,
      {
        method: "POST",
        body: {
          kind: "QUESTION",
          sender: "UNIVERSE_CONDUCTOR",
          body: form.get("instruction"),
          idempotency_key: crypto.randomUUID(),
        },
      }
    );
    elements.dispatchForm.reset();
    state.roomMessages = [
      ...state.roomMessages.filter(
        (message) => message.message_id !== result.message.message_id
      ),
      result.message,
    ];
    renderComposerState();
    renderRoomMessages();
    toast(
      result.message.delivery_state === "DELIVERED_TO_MASTER"
        ? "Delivered to the registered Project Master"
        : "Message saved in the Project Room; no Inbox item was created"
    );
    showInspectorTab("activity");
  } catch (error) {
    toast(error.message, true);
  } finally {
    elements.dispatchSubmit.disabled = false;
  }
}

async function prepareProjectSeed() {
  if (!state.selectedProject) {
    toast("Select a project", true);
    return;
  }
  elements.prepareProject.disabled = true;
  try {
    await api(
      `/v1/projects/${encodeURIComponent(
        state.selectedProject.project_id
      )}/discovery-dispatch`,
      { method: "POST", body: {} }
    );
    toast("Project seed preparation queued");
    await selectProject(state.selectedProject.project_id);
    showInspectorTab("activity");
  } catch (error) {
    toast(error.message, true);
  } finally {
    elements.prepareProject.disabled = false;
  }
}

function selectedNodeRef() {
  const selected = state.selectedNode;
  if (!selected || !["system", "related", "focus"].includes(selected.kind)) {
    return null;
  }
  return selected.data?.node_id || null;
}

function conductorUiContext() {
  const context = {};
  if (state.selectedProject) {
    context.selected_project_id = state.selectedProject.project_id;
  }
  const nodeRef = selectedNodeRef();
  if (state.selectedProject && nodeRef) {
    context.selected_node_ref = nodeRef;
    context.selected_node_label =
      state.selectedNode?.label || state.focusedNodeId || nodeRef;
  }
  return context;
}


function todosForSelectedContext() {
  const nodeRef = selectedNodeRef();
  if (nodeRef && state.selectedProject) {
    return state.todos.filter(
      (todo) =>
        todo.scope_kind === "NODE" &&
        todo.project_id === state.selectedProject.project_id &&
        todo.node_ref === nodeRef
    );
  }
  if (state.selectedProject) {
    return state.todos.filter(
      (todo) =>
        todo.project_id === state.selectedProject.project_id &&
        todo.scope_kind !== "UNIVERSE"
    );
  }
  return state.todos.filter((todo) => todo.scope_kind === "UNIVERSE");
}

function openTodosForGraphNode(graphNode) {
  if (!state.selectedProject) return [];
  if (graphNode.kind === "project") {
    return state.todos.filter(
      (todo) =>
        todo.project_id === state.selectedProject.project_id &&
        todo.state !== "DONE"
    );
  }
  const nodeRef = graphNode.data?.node_id;
  if (!nodeRef) return [];
  return state.todos.filter(
    (todo) =>
      todo.scope_kind === "NODE" &&
      todo.project_id === state.selectedProject.project_id &&
      todo.node_ref === nodeRef &&
      todo.state !== "DONE"
  );
}

function todoNodeOptions(projectId) {
  if (!projectId || state.selectedProject?.project_id !== projectId) return [];
  const seen = new Set();
  return state.graph.nodes
    .filter(
      (item) =>
        ["system", "related", "focus"].includes(item.kind) &&
        item.data?.node_id
    )
    .filter((item) => {
      if (seen.has(item.data.node_id)) return false;
      seen.add(item.data.node_id);
      return true;
    })
    .map((item) => ({
      node_ref: item.data.node_id,
      label: item.label,
    }));
}

function replaceSelectOptions(select, options, emptyLabel) {
  const previous = select.value;
  select.replaceChildren();
  if (emptyLabel) {
    const option = node("option", "", emptyLabel);
    option.value = "";
    select.append(option);
  }
  for (const item of options) {
    const option = node("option", "", item.label);
    option.value = item.value;
    select.append(option);
  }
  if ([...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
}

function renderTodoScopeControls() {
  const previousProject = elements.todoProject.value;
  replaceSelectOptions(
    elements.todoProject,
    state.projects.map((project) => ({
      value: project.project_id,
      label: project.project_id,
    })),
    "Select project"
  );
  if (
    !previousProject &&
    state.selectedProject
  ) {
    elements.todoProject.value = state.selectedProject.project_id;
  }
  const projectId = elements.todoProject.value;
  replaceSelectOptions(
    elements.todoNode,
    todoNodeOptions(projectId).map((item) => ({
      value: item.node_ref,
      label: item.label,
    })),
    "Select node"
  );
  const nodeRef = selectedNodeRef();
  if (!elements.todoNode.value && nodeRef) elements.todoNode.value = nodeRef;
  const scope = elements.todoScope.value;
  elements.todoProject.disabled = scope === "UNIVERSE";
  elements.todoNode.disabled = scope !== "NODE";
}

function prefillTodoScope() {
  if (selectedNodeRef() && state.selectedProject) {
    elements.todoScope.value = "NODE";
  } else if (state.selectedProject) {
    elements.todoScope.value = "PROJECT";
  } else {
    elements.todoScope.value = "UNIVERSE";
  }
  renderTodoScopeControls();
}

function openTodoDialog(prefill = false) {
  state.todoDraftSourceKind = "USER";
  elements.todoFormError.textContent = "";
  if (prefill) prefillTodoScope();
  else renderTodoScopeControls();
  renderTodos();
  elements.todoDialog.showModal();
  elements.todoTitle.focus();
}


function openConductorTodoDraft(action) {
  const todo = action?.todo;
  if (!todo) {
    toast("Conductor Todo draft is unavailable", true);
    return;
  }
  state.todoDraftSourceKind = "CONDUCTOR";
  elements.todoFormError.textContent = "";
  elements.todoScope.value = todo.scope_kind;
  renderTodoScopeControls();
  if (todo.project_id) {
    elements.todoProject.value = todo.project_id;
    renderTodoScopeControls();
  }
  if (todo.node_ref) {
    elements.todoNode.value = todo.node_ref;
  }
  elements.todoTitle.value = todo.title;
  elements.todoDetail.value = todo.detail;
  elements.todoForm.elements.priority.value = todo.priority;
  elements.todoForm.elements.state.value = todo.state;
  renderTodos();
  elements.todoDialog.showModal();
  elements.todoTitle.focus();
}

function openConductorFreshProjectDraft(action) {
  const intent = action?.intent;
  if (!intent) {
    toast("Conductor Fresh Project draft is unavailable", true);
    return;
  }
  openFreshProjectWizard();
  const form = elements.freshProjectForm.elements;
  form.namedItem("project").value = intent.project || "";
  form.namedItem("kind").value = intent.kind || "";
  form.namedItem("goal").value = intent.goal || "";
  form.namedItem("target_users").value = intent.target_users || "";
  form.namedItem("technologies").value = (intent.technologies || []).join(", ");
  form.namedItem("constraints").value = (intent.constraints || []).join(", ");
  const firstMissing = ["project", "kind", "goal"]
    .map((name) => form.namedItem(name))
    .find((field) => !field.value.trim());
  (firstMissing || form.namedItem("project")).focus();
}

function todoScopeLabel(todo) {
  if (todo.scope_kind === "UNIVERSE") return "Universe";
  if (todo.scope_kind === "PROJECT") return todo.project_id;
  return `${todo.project_id} / ${todo.node_ref}`;
}

function visibleTodos() {
  const scope = elements.todoScopeFilter.value;
  const stateFilter = elements.todoStateFilter.value;
  const priorityFilter = elements.todoPriorityFilter?.value || "ALL";
  return state.todos.filter((todo) => {
    if (stateFilter === "OPEN" && todo.state === "DONE") return false;
    if (stateFilter === "DONE" && todo.state !== "DONE") return false;
    if (priorityFilter !== "ALL" && todo.priority !== priorityFilter) return false;
    if (scope === "UNIVERSE" && todo.scope_kind !== "UNIVERSE") return false;
    if (
      scope === "PROJECT" &&
      (!state.selectedProject ||
        todo.project_id !== state.selectedProject.project_id)
    ) {
      return false;
    }
    if (
      scope === "NODE" &&
      (!state.selectedProject ||
        !selectedNodeRef() ||
        todo.scope_kind !== "NODE" ||
        todo.project_id !== state.selectedProject.project_id ||
        todo.node_ref !== selectedNodeRef())
    ) {
      return false;
    }
    return true;
  });
}

function todoSelect(values, selectedValue, className) {
  const select = node("select", className);
  for (const [value, label] of values) {
    const option = node("option", "", label);
    option.value = value;
    option.selected = value === selectedValue;
    select.append(option);
  }
  return select;
}

function renderTodos() {
  if (!elements.todoList) return;
  const todos = visibleTodos();
  elements.todoList.replaceChildren();
  elements.todoCount.textContent = `${todos.length} item${
    todos.length === 1 ? "" : "s"
  }`;
  if (elements.seedWorklistButton) {
    const projectId = state.selectedProject?.project_id;
    const projectTodoCount = projectId
      ? state.todos.filter((todo) => todo.project_id === projectId).length
      : 0;
    elements.seedWorklistButton.hidden = !projectId || projectTodoCount > 0;
  }
  if (!todos.length) {
    const empty = node("div", "todo-empty-block");
    empty.append(
      node("p", "empty-copy todo-empty", "No Todo matches this view")
    );
    if (
      state.selectedProject &&
      !state.todos.some(
        (todo) => todo.project_id === state.selectedProject.project_id
      )
    ) {
      const seed = node(
        "button",
        "secondary-button",
        "Seed P0 worklist for this project"
      );
      seed.type = "button";
      seed.addEventListener("click", seedWorklistTodos);
      empty.append(seed);
    }
    elements.todoList.append(empty);
    return;
  }
  for (const todo of todos) {
    const item = node("article", "todo-item");
    item.dataset.todoId = todo.todo_id;
    const header = node("div", "todo-item-header");
    header.append(
      node("span", `todo-priority ${todo.priority.toLowerCase()}`, todo.priority),
      node("span", "todo-location", todoScopeLabel(todo)),
      node("small", "", `r${todo.revision}`)
    );
    const title = node("input", "todo-item-title");
    title.value = todo.title;
    title.maxLength = 160;
    title.setAttribute("aria-label", "Todo title");
    const detail = node("textarea", "todo-item-detail");
    detail.value = todo.detail;
    detail.maxLength = 4000;
    detail.rows = 2;
    detail.setAttribute("aria-label", "Todo detail");
    const controls = node("div", "todo-item-controls");
    const priority = todoSelect(
      [["P0", "P0"], ["P1", "P1"], ["P2", "P2"], ["P3", "P3"]],
      todo.priority,
      "todo-item-priority"
    );
    const todoState = todoSelect(
      [
        ["BACKLOG", "Backlog"],
        ["READY", "Ready"],
        ["IN_PROGRESS", "In progress"],
        ["BLOCKED", "Blocked"],
        ["DONE", "Done"],
      ],
      todo.state,
      "todo-item-state"
    );
    const save = node("button", "secondary-button todo-save", "Save");
    save.type = "button";
    save.addEventListener("click", () =>
      updateTodo(todo, {
        title: title.value,
        detail: detail.value,
        priority: priority.value,
        state: todoState.value,
      })
    );
    const remove = node("button", "icon-button compact todo-delete", "\u00d7");
    remove.type = "button";
    remove.title = "Delete Todo";
    remove.setAttribute("aria-label", remove.title);
    remove.addEventListener("click", () => deleteTodo(todo));
    controls.append(priority, todoState, save, remove);
    item.append(header, title, detail, controls);
    elements.todoList.append(item);
  }
}

async function submitTodo(event) {
  event.preventDefault();
  elements.todoFormError.textContent = "";
  const form = new FormData(elements.todoForm);
  const scopeKind = String(form.get("scope_kind"));
  const body = {
    scope_kind: scopeKind,
    title: String(form.get("title") || "").trim(),
    detail: String(form.get("detail") || ""),
    priority: String(form.get("priority")),
    state: String(form.get("state")),
    source_kind: state.todoDraftSourceKind,
    sort_order: state.todos.length,
  };
  if (scopeKind !== "UNIVERSE") {
    body.project_id = elements.todoProject.value;
  }
  if (scopeKind === "NODE") {
    body.node_ref = elements.todoNode.value;
  }
  try {
    const result = await api("/v1/todos", { method: "POST", body });
    state.todos = [result.todo, ...state.todos];
    elements.todoTitle.value = "";
    elements.todoDetail.value = "";
    state.todoDraftSourceKind = "USER";
    renderProjects();
    renderTodos();
    renderDetails();
    drawGraph();
    toast("Todo recorded");
  } catch (error) {
    elements.todoFormError.textContent = error.message;
  }
}

async function updateTodo(todo, changes) {
  const body = {
    scope_kind: todo.scope_kind,
    project_id: todo.project_id,
    node_ref: todo.node_ref,
    title: changes.title.trim(),
    detail: changes.detail,
    priority: changes.priority,
    state: changes.state,
    source_kind: todo.source_kind,
    sort_order: todo.sort_order,
    revision: todo.revision,
  };
  if (body.project_id === null) delete body.project_id;
  if (body.node_ref === null) delete body.node_ref;
  try {
    const result = await api(`/v1/todos/${encodeURIComponent(todo.todo_id)}`, {
      method: "PATCH",
      body,
    });
    state.todos = state.todos.map((item) =>
      item.todo_id === todo.todo_id ? result.todo : item
    );
    renderProjects();
    renderTodos();
    renderDetails();
    drawGraph();
    toast("Todo updated");
  } catch (error) {
    elements.todoFormError.textContent = error.message;
  }
}

async function deleteTodo(todo) {
  if (!window.confirm(`Delete Todo: ${todo.title}?`)) return;
  try {
    await api(`/v1/todos/${encodeURIComponent(todo.todo_id)}`, {
      method: "DELETE",
    });
    state.todos = state.todos.filter((item) => item.todo_id !== todo.todo_id);
    renderProjects();
    renderTodos();
    renderDetails();
    drawGraph();
    toast("Todo deleted");
  } catch (error) {
    elements.todoFormError.textContent = error.message;
  }
}

function undeliveredSkillPlanAdoptions() {
  const deliveredAdoptionIds = new Set(
    state.masterHandoffs
      .filter((item) => item.source?.kind === "SKILL_PLAN")
      .map((item) => item.source?.adoption?.adoption_id || item.source?.adoption_id)
      .filter(Boolean)
  );
  return state.skillPlanAdoptions.filter(
    (adoption) => !deliveredAdoptionIds.has(adoption.adoption_id)
  );
}

async function seedWorklistTodos() {
  if (!state.selectedProject) {
    toast("Select a project first", true);
    return;
  }
  const projectId = state.selectedProject.project_id;
  const existing = state.todos.filter((todo) => todo.project_id === projectId);
  if (existing.length) {
    toast("Project already has todos; seed skipped", true);
    return;
  }
  if (
    !window.confirm(
      `Create ${WORKLIST_SEED_TODOS.length} planning todos for ${projectId}?`
    )
  ) {
    return;
  }
  try {
    const created = [];
    for (const [index, seed] of WORKLIST_SEED_TODOS.entries()) {
      const result = await api("/v1/todos", {
        method: "POST",
        body: {
          scope_kind: "PROJECT",
          project_id: projectId,
          title: seed.title,
          detail: seed.detail,
          priority: seed.priority,
          state: seed.state,
          source_kind: "USER",
          sort_order: index,
        },
      });
      created.push(result.todo);
    }
    state.todos = [...created, ...state.todos];
    renderProjects();
    renderTodos();
    renderDetails();
    drawGraph();
    toast(`Seeded ${created.length} worklist todos`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function proposeSkillPlanHandoff(adoption) {
  if (!state.selectedProject || !adoption) return;
  const projectId = state.selectedProject.project_id;
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(projectId)}/master-handoffs`,
      {
        method: "POST",
        body: {
          source: {
            kind: "SKILL_PLAN",
            adoption_id: adoption.adoption_id,
          },
          purpose: "Deliver adopted Skill Plan to Project Master planning context",
        },
      }
    );
    state.masterHandoffs = [
      result.handoff,
      ...state.masterHandoffs.filter(
        (item) => item.handoff_id !== result.handoff.handoff_id
      ),
    ];
    renderDetails();
    renderActivity();
    toast(
      result.status === "PROJECT_MASTER_HANDOFF_PROPOSAL_RECORDED"
        ? "Master handoff proposed"
        : "Existing Master handoff reused"
    );
  } catch (error) {
    toast(error.message, true);
  }
}

async function proposeFreshProjectHandoff() {
  const adoption = state.freshProject.adoption;
  if (!adoption) {
    toast("Adopt a Fresh Project composition first", true);
    return;
  }
  if (!state.selectedProject) {
    toast("Select the target project before proposing Master handoff", true);
    return;
  }
  const projectId = state.selectedProject.project_id;
  if (elements.proposeMasterHandoffButton) {
    elements.proposeMasterHandoffButton.disabled = true;
  }
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(projectId)}/master-handoffs`,
      {
        method: "POST",
        body: {
          source: {
            kind: "FRESH_PROJECT_COMPOSITION",
            adoption_id: adoption.adoption_id,
          },
          purpose: "Deliver adopted Fresh Project plan to Project Master",
        },
      }
    );
    state.freshProject.handoff = result.handoff;
    state.masterHandoffs = [
      result.handoff,
      ...state.masterHandoffs.filter(
        (item) => item.handoff_id !== result.handoff.handoff_id
      ),
    ];
    renderFreshProjectHandoffControls();
    renderDetails();
    renderActivity();
    toast(
      result.status === "PROJECT_MASTER_HANDOFF_PROPOSAL_RECORDED"
        ? "Master handoff proposed"
        : "Existing Master handoff reused"
    );
  } catch (error) {
    if (elements.freshProjectError) {
      elements.freshProjectError.textContent = error.message;
    }
    toast(error.message, true);
  } finally {
    if (elements.proposeMasterHandoffButton) {
      elements.proposeMasterHandoffButton.disabled = false;
    }
  }
}

async function deliverMasterHandoff(projectId, handoff) {
  if (!projectId || !handoff) return;
  if (
    !window.confirm(
      "Deliver this handoff to Project Master?\n\napproval must be DELIVER. This stores planning context only; it does not create a Task Frame or write project source."
    )
  ) {
    return;
  }
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(projectId)}/master-handoffs/${encodeURIComponent(handoff.handoff_id)}/deliver`,
      {
        method: "POST",
        body: { approval: "DELIVER" },
      }
    );
    const updated = result.handoff || handoff;
    state.masterHandoffs = state.masterHandoffs.map((item) =>
      item.handoff_id === updated.handoff_id ? updated : item
    );
    if (state.freshProject.handoff?.handoff_id === updated.handoff_id) {
      state.freshProject.handoff = updated;
      renderFreshProjectHandoffControls();
    }
    if (state.selectedProject?.project_id === projectId) {
      await selectProject(projectId);
      showInspectorTab("activity");
    } else {
      renderDetails();
      renderActivity();
    }
    toast(
      result.status === "PROJECT_MASTER_HANDOFF_ALREADY_DELIVERED"
        ? "Handoff already delivered"
        : "Handoff delivered to Project Master"
    );
  } catch (error) {
    toast(error.message, true);
  }
}

function renderFreshProjectHandoffControls() {
  const handoff = state.freshProject.handoff;
  const adoption = state.freshProject.adoption;
  if (elements.freshProjectHandoffStatus) {
    if (handoff) {
      elements.freshProjectHandoffStatus.textContent = `${handoff.delivery_state} · ${handoff.handoff_id}`;
    } else if (adoption) {
      elements.freshProjectHandoffStatus.textContent =
        "Handoff candidate ready. Select target project, then propose delivery.";
    } else {
      elements.freshProjectHandoffStatus.textContent = "";
    }
  }
  if (elements.proposeMasterHandoffButton) {
    elements.proposeMasterHandoffButton.hidden = !adoption;
    elements.proposeMasterHandoffButton.disabled = Boolean(
      handoff && handoff.delivery_state !== "PROPOSAL_ONLY"
    );
  }
  if (elements.deliverMasterHandoffButton) {
    const canDeliver =
      handoff &&
      handoff.delivery_state === "PROPOSAL_ONLY" &&
      state.selectedProject;
    elements.deliverMasterHandoffButton.hidden = !handoff;
    elements.deliverMasterHandoffButton.disabled = !canDeliver;
  }
}

function commaList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function showFreshProjectPanel(name) {
  const panels = {
    intent: elements.freshProjectIntent,
    routes: elements.freshProjectRoutes,
    composition: elements.freshProjectComposition,
    refinement: elements.freshProjectRefinement,
    adopted: elements.freshProjectAdopted,
  };
  const labels = {
    intent: "01 / Intent",
    routes: "02 / Routes",
    composition: "03 / Composition",
    refinement: "04 / Refinement",
    adopted: "05 / Adopted",
  };
  for (const [key, panel] of Object.entries(panels)) {
    panel.classList.toggle("hidden", key !== name);
  }
  elements.freshProjectStep.textContent = labels[name];
  elements.freshProjectError.textContent = "";
}

function openFreshProjectWizard() {
  state.freshProject = {
    intent: null,
    routes: [],
    composition: null,
    refinementRequest: null,
    planningBinding: null,
    providers: [],
    refinementRun: null,
    refinementCandidate: null,
    refinementAdoption: null,
    adoption: null,
    handoff: null,
  };
  elements.freshProjectForm.reset();
  elements.freshProjectRouteList.replaceChildren();
  elements.freshProjectCompositionOutput.replaceChildren();
  elements.freshProjectRefinementRef.textContent = "";
  elements.planningProvider.replaceChildren();
  elements.planningProposal.classList.add("hidden");
  elements.refinementCandidate.classList.add("hidden");
  elements.planningRunStatus.textContent = "";
  renderFreshProjectHandoffControls();
  showFreshProjectPanel("intent");
  elements.freshProjectDialog.showModal();
}

function renderFreshProjectRoutes() {
  elements.freshProjectRouteList.replaceChildren();
  for (const route of state.freshProject.routes) {
    const card = node("article", "route-card");
    const heading = node("div", "route-card-heading");
    const copy = node("div");
    copy.append(
      node("strong", "", route.title),
      node("small", "", route.support_level || "Seed candidate")
    );
    const choose = node("button", "primary-button", "Build plan");
    choose.type = "button";
    choose.addEventListener("click", () =>
      createFreshProjectComposition(route.route_id, choose)
    );
    heading.append(copy, choose);
    const steps = node("ol", "route-steps");
    for (const step of route.steps || []) {
      steps.append(node("li", "", step.title));
    }
    card.append(heading, node("p", "", route.description), steps);
    elements.freshProjectRouteList.append(card);
  }
  if (!state.freshProject.routes.length) {
    elements.freshProjectRouteList.append(
      node("p", "empty-copy", "No matching Seed route")
    );
  }
}

async function submitFreshProjectIntent(event) {
  event.preventDefault();
  const form = new FormData(elements.freshProjectForm);
  const intent = {
    project: String(form.get("project") || "").trim(),
    kind: String(form.get("kind") || "").trim(),
    technologies: commaList(form.get("technologies")),
    goal: String(form.get("goal") || "").trim(),
    constraints: commaList(form.get("constraints")),
  };
  const targetUsers = String(form.get("target_users") || "").trim();
  if (targetUsers) intent.target_users = targetUsers;
  elements.findRoutesButton.disabled = true;
  elements.freshProjectError.textContent = "";
  try {
    const result = await api("/v1/future-paths", {
      method: "POST",
      body: {
        project: intent.project,
        kind: intent.kind,
        technologies: intent.technologies,
        goal: intent.goal,
        limit: 4,
      },
    });
    state.freshProject.intent = intent;
    state.freshProject.routes = result.proposal.candidates || [];
    renderFreshProjectRoutes();
    showFreshProjectPanel("routes");
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
  } finally {
    elements.findRoutesButton.disabled = false;
  }
}

async function createFreshProjectComposition(routeId, button) {
  button.disabled = true;
  elements.freshProjectError.textContent = "";
  try {
    const result = await api("/v1/fresh-project-compositions", {
      method: "POST",
      body: {
        intent: state.freshProject.intent,
        route_id: routeId,
      },
    });
    state.freshProject.composition = result.composition;
    renderFreshProjectComposition();
    showFreshProjectPanel("composition");
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function renderFreshProjectComposition() {
  const composition = state.freshProject.composition;
  elements.freshProjectCompositionTitle.textContent =
    composition.selected_route.title;
  elements.freshProjectCompositionOutput.replaceChildren();

  const summary = node("section", "composition-section");
  summary.append(
    node("span", "wizard-kicker", "Direction"),
    node("p", "", composition.selected_route.description)
  );

  const capabilities = node("section", "composition-section");
  capabilities.append(node("span", "wizard-kicker", "Functional nodes"));
  const capabilityList = node("div", "composition-node-list");
  for (const item of composition.specification.functional_nodes) {
    const row = node("article", "composition-node");
    row.append(
      node("strong", "", item.title),
      node("p", "", item.purpose),
      node("small", "", item.acceptance_condition)
    );
    capabilityList.append(row);
  }
  capabilities.append(capabilityList);

  const decisions = node("section", "composition-section composition-decisions");
  const technology = node("div");
  technology.append(node("span", "wizard-kicker", "Technology signals"));
  const tags = node("div", "tag-list");
  for (const item of composition.technology.selected_signals) {
    tags.append(node("span", "signal-tag", item));
  }
  if (!composition.technology.selected_signals.length) {
    tags.append(node("span", "empty-copy", "Selection pending"));
  }
  technology.append(tags);
  const documents = node("div");
  documents.append(node("span", "wizard-kicker", "Initial documents"));
  const documentList = node("ul", "composition-documents");
  for (const item of composition.document_plan) {
    documentList.append(node("li", "", `${item.role} · ${item.title}`));
  }
  documents.append(documentList);
  decisions.append(technology, documents);

  elements.freshProjectCompositionOutput.append(
    summary,
    capabilities,
    decisions
  );
}

async function prepareFreshProjectRefinement() {
  const composition = state.freshProject.composition;
  if (!composition) return;
  elements.prepareRefinementButton.disabled = true;
  elements.freshProjectError.textContent = "";
  try {
    const result = await api("/v1/fresh-project-refinement-requests", {
      method: "POST",
      body: { composition_id: composition.composition_id },
    });
    state.freshProject.refinementRequest = result.request;
    elements.freshProjectRefinementRef.textContent =
      `${result.request.request_id} · structured output only`;
    showFreshProjectPanel("refinement");
    await loadFreshProjectPlanningOptions();
    toast("Planning request prepared");
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
  } finally {
    elements.prepareRefinementButton.disabled = false;
  }
}

async function loadFreshProjectPlanningOptions() {
  const [binding, providers] = await Promise.all([
    api("/v1/runtime/planning-binding"),
    api("/v1/runtime/providers"),
  ]);
  state.freshProject.planningBinding = binding;
  state.freshProject.providers = providers.providers || [];
  elements.planningBindingStatus.textContent =
    binding.status === "BOUND" ? "RUNTIME BOUND" : "RUNTIME UNBOUND";
  elements.planningBindingStatus.dataset.status =
    binding.status === "BOUND" ? "READY" : "UNKNOWN";
  elements.planningProvider.replaceChildren();
  for (const provider of state.freshProject.providers) {
    const option = node(
      "option",
      "",
      `${provider.provider} / ${provider.status}`
    );
    option.value = provider.provider;
    option.disabled = provider.status !== "AVAILABLE";
    elements.planningProvider.append(option);
  }
  const available = state.freshProject.providers.find(
    (provider) => provider.status === "AVAILABLE"
  );
  if (available) elements.planningProvider.value = available.provider;
  elements.createPlanningProposal.disabled =
    binding.status !== "BOUND" || !available;
  if (binding.status !== "BOUND") {
    elements.planningRunStatus.textContent =
      "Attach a local Runtime Host before creating a Planning Frame proposal.";
  } else if (!available) {
    elements.planningRunStatus.textContent =
      "No planning provider is currently available.";
  } else {
    elements.planningRunStatus.textContent =
      "Ready to create a proposal. No model has been called.";
  }
}

function appendProposalDetail(label, value) {
  elements.planningProposalDetails.append(
    node("dt", "", label),
    node("dd", "", String(value))
  );
}

function renderPlanningProposal() {
  const run = state.freshProject.refinementRun;
  if (!run) {
    elements.planningProposal.classList.add("hidden");
    return;
  }
  elements.planningProposal.classList.remove("hidden");
  elements.planningProposalProvider.textContent =
    `${run.provider} | ${run.model_ref}`;
  elements.planningProposalDetails.replaceChildren();
  appendProposalDetail("Frame", run.frame_id);
  appendProposalDetail("Turn", `${run.turn_id} / BOSS`);
  appendProposalDetail("Repository write", run.repository_write_scope);
  appendProposalDetail("Mutation targets", run.mutation_scope.targets.length);
  appendProposalDetail("Proposal", run.proposal_id);
  appendProposalDetail("Plan digest", run.plan_digest);
  elements.executePlanningProposal.disabled = run.state !== "PROPOSED";
}

async function createFreshProjectPlanningProposal() {
  const request = state.freshProject.refinementRequest;
  if (!request) return;
  elements.createPlanningProposal.disabled = true;
  elements.freshProjectError.textContent = "";
  elements.planningRunStatus.textContent = "Creating exact proposal...";
  try {
    const result = await api("/v1/fresh-project-refinement-runs", {
      method: "POST",
      body: {
        request_id: request.request_id,
        provider: elements.planningProvider.value,
      },
    });
    state.freshProject.refinementRun = result.run;
    state.freshProject.refinementCandidate = null;
    elements.refinementCandidate.classList.add("hidden");
    renderPlanningProposal();
    elements.planningRunStatus.textContent =
      "Proposal ready. Provider execution still requires approval.";
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
    elements.planningRunStatus.textContent = "Proposal creation failed.";
  } finally {
    elements.createPlanningProposal.disabled = false;
  }
}

function comparisonText(value) {
  if (Array.isArray(value)) {
    if (!value.length) return "None";
    return value
      .map((item) => {
        if (typeof item === "string") return item;
        if (item.technology) return `${item.technology}: ${item.rationale}`;
        if (item.title) return `${item.role}: ${item.title}`;
        return JSON.stringify(item);
      })
      .join("\n");
  }
  return String(value || "Not specified");
}

function appendRefinementComparison(label, baseValue, candidateValue) {
  const row = node("article", "refinement-comparison-row");
  row.append(node("strong", "refinement-comparison-label", label));
  const base = node("div", "refinement-comparison-value");
  base.append(
    node("span", "wizard-kicker", "Current"),
    node("p", "", comparisonText(baseValue))
  );
  const proposed = node("div", "refinement-comparison-value proposed");
  proposed.append(
    node("span", "wizard-kicker", "Proposed"),
    node("p", "", comparisonText(candidateValue))
  );
  row.append(base, proposed);
  elements.refinementComparison.append(row);
}

function renderRefinementCandidate() {
  const composition = state.freshProject.composition;
  const candidate = state.freshProject.refinementCandidate;
  if (!composition || !candidate) {
    elements.refinementCandidate.classList.add("hidden");
    return;
  }
  const refinement = candidate.refinement;
  elements.refinementComparison.replaceChildren();
  appendRefinementComparison(
    "Problem statement",
    composition.specification.problem_statement,
    refinement.problem_statement
  );
  appendRefinementComparison(
    "Target users",
    composition.specification.target_users,
    refinement.target_users
  );
  appendRefinementComparison(
    "Constraints",
    composition.specification.constraints,
    refinement.constraints
  );
  appendRefinementComparison(
    "Design direction",
    composition.design.direction,
    refinement.design_direction
  );
  appendRefinementComparison(
    "Technology",
    composition.technology.selected_signals,
    refinement.technology_recommendations
  );
  appendRefinementComparison(
    "Documents",
    composition.document_plan,
    refinement.document_additions
  );
  appendRefinementComparison(
    "Risks",
    composition.risk_conditions,
    refinement.risk_additions
  );
  elements.refinementCandidate.classList.remove("hidden");
}

async function executeFreshProjectPlanningProposal() {
  const run = state.freshProject.refinementRun;
  if (!run) return;
  elements.executePlanningProposal.disabled = true;
  elements.createPlanningProposal.disabled = true;
  elements.freshProjectError.textContent = "";
  elements.planningRunStatus.textContent =
    `Running ${run.provider} in the approved read-only Planning Frame...`;
  try {
    const result = await api(
      `/v1/fresh-project-refinement-runs/${encodeURIComponent(run.run_id)}/execute`,
      {
        method: "POST",
        body: {
          approval: "APPROVED",
          proposal_id: run.proposal_id,
          plan_digest: run.plan_digest,
        },
      }
    );
    state.freshProject.refinementRun = result.run;
    state.freshProject.refinementCandidate = result.candidate;
    renderPlanningProposal();
    renderRefinementCandidate();
    elements.planningRunStatus.textContent =
      "Structured candidate returned. Review changes before adoption.";
    toast("Planning candidate ready");
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
    elements.planningRunStatus.textContent = "Planning run failed.";
  } finally {
    elements.createPlanningProposal.disabled = false;
  }
}

async function adoptFreshProjectRefinement() {
  const candidate = state.freshProject.refinementCandidate;
  if (!candidate) return;
  elements.adoptRefinementButton.disabled = true;
  elements.freshProjectError.textContent = "";
  try {
    const result = await api("/v1/fresh-project-refinement-adoptions", {
      method: "POST",
      body: {
        candidate_id: candidate.candidate_id,
        approval: "ADOPTED",
      },
    });
    state.freshProject.refinementAdoption = result.adoption;
    state.freshProject.composition = result.composition;
    renderFreshProjectComposition();
    showFreshProjectPanel("composition");
    toast("Revision adopted into the composition");
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
  } finally {
    elements.adoptRefinementButton.disabled = false;
  }
}

async function adoptFreshProjectComposition() {
  const composition = state.freshProject.composition;
  if (!composition) return;
  elements.adoptCompositionButton.disabled = true;
  elements.freshProjectError.textContent = "";
  try {
    const result = await api("/v1/fresh-project-composition-adoptions", {
      method: "POST",
      body: {
        composition_id: composition.composition_id,
        approval: "ADOPTED",
      },
    });
    state.freshProject.adoption = result.adoption;
    state.freshProject.handoff = null;
    elements.freshProjectAdoptionRef.textContent =
      `${result.adoption.adoption_id} · Project Master handoff candidate`;
    renderFreshProjectHandoffControls();
    showFreshProjectPanel("adopted");
    toast("Fresh project plan adopted");
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
  } finally {
    elements.adoptCompositionButton.disabled = false;
  }
}

async function submitProject(event) {
  event.preventDefault();
  elements.projectFormError.textContent = "";
  const form = new FormData(elements.projectForm);
  try {
    await api("/v1/projects/register", {
      method: "POST",
      body: {
        project_id: form.get("project_id"),
        project_root: form.get("project_root"),
      },
    });
    elements.projectDialog.close();
    elements.projectForm.reset();
    toast("Project connected");
    await refresh();
    await selectProject(String(form.get("project_id")));
  } catch (error) {
    elements.projectFormError.textContent = error.message;
  }
}

async function submitRelease(event) {
  event.preventDefault();
  elements.releaseFormError.textContent = "";
  const form = new FormData(elements.releaseForm);
  try {
    await api("/v1/releases/import", {
      method: "POST",
      body: {
        database_path: form.get("database_path"),
        manifest_path: form.get("manifest_path"),
        mode: form.get("mode"),
      },
    });
    elements.releaseForm.reset();
    const result = await api("/v1/releases");
    state.releases = result.releases;
    renderReleaseCatalog();
    toast("Release DB imported");
  } catch (error) {
    elements.releaseFormError.textContent = error.message;
  }
}


function showInspectorTab(name) {
  for (const button of document.querySelectorAll("[data-tab]")) {
    button.classList.toggle("selected", button.dataset.tab === name);
  }
  elements.details.classList.toggle("hidden", name !== "details");
  elements.activity.classList.toggle("hidden", name !== "activity");
  if (elements.benchPanel) {
    elements.benchPanel.classList.toggle("hidden", name !== "bench");
  }
  if (elements.memoryPanel) {
    elements.memoryPanel.classList.toggle("hidden", name !== "memory");
  }
  if (elements.futurePanel) {
    elements.futurePanel.classList.toggle("hidden", name !== "future");
  }
  if (name === "bench") renderBench();
  if (name === "memory") renderMemory();
  if (name === "future") renderFuture();
}

function projectBenchRows() {
  const projectSkillIds = new Set(
    state.skillObservations
      .map((item) => item.skill?.skill_id)
      .filter(Boolean)
  );
  if (!projectSkillIds.size) return state.skillBench.slice(0, 12);
  return state.skillBench
    .filter((item) => projectSkillIds.has(item.skill?.skill_id))
    .slice(0, 12);
}

function renderBench() {
  if (!elements.benchPanel) return;
  elements.benchPanel.replaceChildren();
  if (!state.selectedProject) {
    elements.benchPanel.append(
      node("p", "empty-copy", "Select a project to inspect Bench and Experience")
    );
    return;
  }

  const obsGroup = node("div", "detail-group");
  obsGroup.append(
    node(
      "h3",
      "",
      `Skill observations (${state.skillObservations.length})`
    )
  );
  if (!state.skillObservations.length) {
    obsGroup.append(
      node(
        "p",
        "empty-copy",
        "No redacted Skill observations yet. Publish from a Project Task Frame run."
      )
    );
  } else {
    const list = node("ul", "context-list bench-list");
    for (const observation of state.skillObservations.slice(0, 8)) {
      list.append(
        node(
          "li",
          "",
          `${observation.outcome || "UNKNOWN"} · ${observation.skill?.skill_id || "skill"} · ${observation.validation_state || "NOT_RUN"} · ${observation.observed_at || ""}`
        )
      );
    }
    obsGroup.append(list);
    const record = node(
      "button",
      "secondary-button compact-action",
      "Record Experience Case"
    );
    record.type = "button";
    record.title =
      "Create an Experience Case from the latest observation (causal_state stays NOT_INFERRED)";
    record.addEventListener("click", () =>
      recordExperienceCaseFromLatestObservation()
    );
    obsGroup.append(record);
  }
  elements.benchPanel.append(obsGroup);

  const benchGroup = node("div", "detail-group");
  const benchRows = projectBenchRows();
  benchGroup.append(
    node("h3", "", `Skill bench (${benchRows.length})`)
  );
  if (!benchRows.length) {
    benchGroup.append(
      node(
        "p",
        "empty-copy",
        "No bench aggregates yet. Aggregates appear after Skill observations are ingested."
      )
    );
  } else {
    const list = node("ul", "context-list bench-list");
    for (const row of benchRows) {
      const succeeded = row.outcomes?.SUCCEEDED ?? 0;
      const failed = row.outcomes?.FAILED ?? 0;
      const duration = row.metric_totals?.duration_ms;
      const durationText =
        typeof duration === "number" ? ` · ${Math.round(duration)}ms` : "";
      list.append(
        node(
          "li",
          "",
          `${row.skill?.skill_id || "skill"} · ${row.provider_ref || "UNKNOWN"} · n=${row.observation_count || 0} · ok=${succeeded} fail=${failed}${durationText}`
        )
      );
    }
    benchGroup.append(list);
  }
  if (state.contextPacks.length) {
    const packGroup = node("div", "detail-group");
    packGroup.append(
      node("h3", "", `Context Packs (${state.contextPacks.length})`)
    );
    const list = node("ul", "context-list bench-list");
    for (const pack of state.contextPacks.slice(0, 6)) {
      list.append(
        node(
          "li",
          "",
          `${pack.context_pack_id || pack.pack_id || "pack"} · ${pack.purpose || pack.status || "recorded"}`
        )
      );
    }
    packGroup.append(list);
    elements.benchPanel.append(packGroup);
  }
  elements.benchPanel.append(benchGroup);

  const compareGroup = node("div", "detail-group");
  const comparisons = state.benchComparisons || [];
  compareGroup.append(
    node("h3", "", `Skill/model compare (${comparisons.length})`)
  );
  if (!comparisons.length) {
    compareGroup.append(
      node(
        "p",
        "empty-copy",
        "No comparison rows yet. Comparisons aggregate redacted Skill observations by skill, model, or provider."
      )
    );
  } else {
    const list = node("ul", "context-list bench-list");
    for (const row of comparisons.slice(0, 8)) {
      const rate =
        row.success_rate == null
          ? "n/a"
          : `${Math.round(row.success_rate * 100)}%`;
      const dur =
        typeof row.avg_duration_ms === "number"
          ? ` · avg ${Math.round(row.avg_duration_ms)}ms`
          : "";
      const label =
        row.label?.skill_id ||
        row.label?.model_ref ||
        row.label?.provider_ref ||
        row.label?.project_id ||
        row.group_key ||
        "group";
      list.append(
        node(
          "li",
          "",
          `${label} · n=${row.observation_count || 0} · success=${rate}${dur}`
        )
      );
    }
    compareGroup.append(list);
    const groups = ["skill", "model", "provider", "project"];
    for (const group of groups) {
      const btn = node(
        "button",
        "secondary-button compact-action",
        `By ${group}`
      );
      btn.type = "button";
      btn.addEventListener("click", async () => {
        try {
          const result = await api(
            `/v1/bench/compare?group_by=${encodeURIComponent(group)}&limit=20`
          );
          state.benchComparisons = result.comparisons || [];
          renderBench();
          toast(`Bench compare by ${group}`);
        } catch (error) {
          toast(error.message, true);
        }
      });
      compareGroup.append(btn);
    }
  }
  elements.benchPanel.append(compareGroup);

  const caseGroup = node("div", "detail-group");
  caseGroup.append(
    node("h3", "", `Experience cases (${state.experienceCases.length})`)
  );
  const caseActions = node("div", "section-actions");
  const fromObs = node(
    "button",
    "secondary-button compact-action",
    "Cases from observations"
  );
  fromObs.type = "button";
  fromObs.title = "Create one Experience Case per Skill observation not already linked";
  fromObs.addEventListener("click", async () => {
    try {
      const result = await api(
        `/v1/projects/${encodeURIComponent(
          state.selectedProject.project_id
        )}/experience-cases/from-observations`,
        { method: "POST", body: { limit: 50 } }
      );
      toast(
        `Experience cases: created ${result.created_count || 0}, reused ${
          result.reused_count || 0
        }`
      );
      await selectProject(state.selectedProject.project_id);
      showInspectorTab("bench");
    } catch (error) {
      toast(error.message, true);
    }
  });
  const autoPattern = node(
    "button",
    "secondary-button compact-action",
    "Auto pattern proposals"
  );
  autoPattern.type = "button";
  autoPattern.title =
    "Propose patterns for cases with enough local OBSERVED_SIMILARITY support";
  autoPattern.addEventListener("click", async () => {
    try {
      const result = await api(
        `/v1/projects/${encodeURIComponent(
          state.selectedProject.project_id
        )}/experience-patterns/auto`,
        { method: "POST", body: { minimum_support: 2 } }
      );
      toast(
        `Patterns: recorded ${result.recorded_count || 0}, skipped ${
          result.skipped_count || 0
        }`
      );
      await selectProject(state.selectedProject.project_id);
      showInspectorTab("bench");
    } catch (error) {
      toast(error.message, true);
    }
  });
  caseActions.append(fromObs, autoPattern);
  caseGroup.append(caseActions);
  if (!state.experienceCases.length) {
    caseGroup.append(
      node(
        "p",
        "empty-copy",
        "No Experience Cases. Record one from an observation first."
      )
    );
  } else {
    const list = node("ul", "context-list bench-list");
    for (const item of state.experienceCases.slice(0, 8)) {
      const row = node("li", "bench-case-row");
      row.append(
        node(
          "span",
          "",
          `${item.case_state || "OBSERVED"} · ${item.causal_state || "NOT_INFERRED"} · ${item.title || item.case_id}`
        )
      );
      const match = node("button", "handoff-action", "Match");
      match.type = "button";
      match.title = "Compare this case with other cases in the same Project";
      match.addEventListener("click", () => matchExperienceCase(item.case_id));
      row.append(match);
      list.append(row);
    }
    caseGroup.append(list);
  }
  if (state.experiencePatterns.length) {
    caseGroup.append(
      node(
        "p",
        "context-copy",
        `Pattern proposals: ${state.experiencePatterns.length} (PROPOSAL_ONLY)`
      )
    );
  }
  elements.benchPanel.append(caseGroup);
}

async function recordExperienceCaseFromLatestObservation() {
  if (!state.selectedProject) {
    toast("Select a project", true);
    return;
  }
  const observation = state.skillObservations[0];
  if (!observation?.observation_id) {
    toast("No Skill observation available", true);
    return;
  }
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        state.selectedProject.project_id
      )}/experience-cases`,
      {
        method: "POST",
        body: {
          observation_ids: [observation.observation_id],
          title: `Case for ${observation.skill?.skill_id || observation.observation_id}`,
        },
      }
    );
    const created = result.case || result;
    state.experienceCases = [
      created,
      ...state.experienceCases.filter(
        (item) => item.case_id !== created.case_id
      ),
    ];
    renderBench();
    toast("Experience Case recorded (NOT_INFERRED)");
    showInspectorTab("bench");
  } catch (error) {
    toast(error.message, true);
  }
}

async function matchExperienceCase(caseId) {
  if (!state.selectedProject || !caseId) return;
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        state.selectedProject.project_id
      )}/experience-matches`,
      {
        method: "POST",
        body: { case_id: caseId, limit: 10 },
      }
    );
    const matches = result.matches || result.candidates || [];
    toast(
      matches.length
        ? `Experience match: ${matches.length} similar case(s) (OBSERVED_SIMILARITY)`
        : "No similar Experience Cases yet"
    );
    if (matches.length && elements.benchPanel) {
      const box = node("div", "detail-group");
      box.append(node("h3", "", "Why / match dimensions"));
      const list = node("ul", "context-list bench-list");
      for (const match of matches.slice(0, 5)) {
        list.append(
          node(
            "li",
            "",
            (() => {
              const skills = (match.shared_skills || [])
                .map((s) => s.skill_id || s)
                .filter(Boolean)
                .slice(0, 3)
                .join(",");
              const outcomes = (match.shared_outcomes || []).slice(0, 3).join(",");
              const validation = (match.shared_validation_states || [])
                .slice(0, 2)
                .join(",");
              return `${match.case_id || "case"} · dims=${
                match.observed_dimension_count || 0
              } · ${
                match.relation || match.relationship || "OBSERVED_SIMILARITY"
              } · skills=${skills || "-"} · outcomes=${outcomes || "-"} · val=${
                validation || "-"
              }`;
            })()
          )
        );
      }
      box.append(list);
      elements.benchPanel.append(box);
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function renderMemory() {
  if (!elements.memoryPanel) return;
  elements.memoryPanel.replaceChildren();
  if (!state.selectedProject) {
    elements.memoryPanel.append(
      node("p", "empty-copy", "Select a project for Memory RAG")
    );
    return;
  }
  const selectedNode = selectedNodeRef();
  const intro = node("div", "detail-group");
  intro.append(
    node("h3", "", "Memory RAG"),
    node(
      "p",
      "context-copy",
      "Reference context only. Not a Candidate, Seed write, or Task Frame."
    )
  );
  elements.memoryPanel.append(intro);

  const maintainGroup = node("div", "detail-group");
  maintainGroup.append(node("h3", "", "Maintain batch"));
  maintainGroup.append(
    node(
      "p",
      "context-copy",
      "Deterministic propose-links batch. Optional apply writes PROPOSED only (never auto LINKED, no Seed write). LLM nightly remains NOT_RUN."
    )
  );
  const maintainBtn = node(
    "button",
    "secondary-button compact-action",
    "Run maintain (propose only)"
  );
  maintainBtn.type = "button";
  maintainBtn.addEventListener("click", async () => {
    try {
      const result = await api(
        `/v1/projects/${encodeURIComponent(
          state.selectedProject.project_id
        )}/memories/maintain`,
        {
          method: "POST",
          body: { apply_proposals: false, limit: 20, scorer: "HEURISTIC" },
        }
      );
      state.memoryProposals = result.proposals || [];
      toast(
        `Maintain: ${result.proposal_count || 0} · ${result.batch_kind || "batch"} · llm=${
          result.llm_batch || "NOT_RUN"
        }`
      );
      renderMemory();
    } catch (error) {
      toast(error.message, true);
    }
  });
  const applyBtn = node(
    "button",
    "secondary-button compact-action",
    "Apply top as PROPOSED"
  );
  applyBtn.type = "button";
  applyBtn.addEventListener("click", async () => {
    try {
      const result = await api(
        `/v1/projects/${encodeURIComponent(
          state.selectedProject.project_id
        )}/memories/maintain`,
        {
          method: "POST",
          body: {
            apply_proposals: true,
            limit: 20,
            per_memory: 1,
            scorer: "AUTO",
          },
        }
      );
      toast(
        `Applied PROPOSED: ${result.applied_count || 0} · ${
          result.batch_kind || "batch"
        }`
      );
      await selectProject(state.selectedProject.project_id);
      showInspectorTab("memory");
    } catch (error) {
      toast(error.message, true);
    }
  });
  maintainGroup.append(maintainBtn, applyBtn);
  elements.memoryPanel.append(maintainGroup);

  const create = node("div", "detail-group memory-create");
  create.append(node("h3", "", "Add note"));
  const title = node("input", "memory-title-input");
  title.placeholder = "Title";
  title.maxLength = 160;
  const body = node("textarea", "memory-body-input");
  body.placeholder = "Brainstorm, question, observation, or decision note";
  body.rows = 3;
  body.maxLength = 8000;
  const add = node("button", "primary-button compact-action", "Save memory");
  add.type = "button";
  add.addEventListener("click", async () => {
    try {
      const payload = {
        title: title.value.trim() || body.value.trim().slice(0, 80),
        body: body.value.trim(),
        state: "BRAINSTORM",
      };
      if (selectedNode) {
        payload.node_ref = selectedNode;
        payload.graph = "functional";
      }
      const result = await api(
        `/v1/projects/${encodeURIComponent(
          state.selectedProject.project_id
        )}/memories`,
        { method: "POST", body: payload }
      );
      state.memories = [result.memory, ...state.memories];
      title.value = "";
      body.value = "";
      renderMemory();
      toast(
        selectedNode
          ? "Memory linked to selected node"
          : "Unlinked memory recorded"
      );
    } catch (error) {
      toast(error.message, true);
    }
  });
  create.append(title, body, add);
  elements.memoryPanel.append(create);

  const unlinked = state.memories.filter((item) => item.link_state === "UNLINKED");
  const linked = state.memories.filter((item) => item.link_state !== "UNLINKED");
  const unlinkedGroup = node("div", "detail-group");
  unlinkedGroup.append(
    node("h3", "", `Unlinked (${unlinked.length})`)
  );
  if (!unlinked.length) {
    unlinkedGroup.append(node("p", "empty-copy", "No unlinked memories"));
  } else {
    const list = node("ul", "context-list bench-list");
    for (const memory of unlinked.slice(0, 8)) {
      const row = node("li", "bench-case-row");
      row.append(
        node("span", "", `${memory.state} · ${memory.title}`)
      );
      if (selectedNode) {
        const link = node("button", "handoff-action", "Link");
        link.type = "button";
        link.addEventListener("click", () =>
          linkMemory(memory.memory_id, selectedNode, "functional", "LINKED")
        );
        row.append(link);
      }
      list.append(row);
    }
    unlinkedGroup.append(list);
  }
  if (state.memoryProposals.length) {
    const propose = node(
      "button",
      "secondary-button compact-action",
      `Apply first proposal (${state.memoryProposals.length})`
    );
    propose.type = "button";
    propose.addEventListener("click", () => {
      const first = state.memoryProposals[0];
      if (!first) return;
      linkMemory(
        first.memory_id,
        first.node_ref,
        first.graph || "functional",
        "PROPOSED"
      );
    });
    unlinkedGroup.append(
      node(
        "p",
        "context-copy",
        `Deterministic proposals: ${state.memoryProposals.length} (token overlap; no Seed write)`
      ),
      propose
    );
  }
  const refreshProposals = node(
    "button",
    "secondary-button compact-action",
    "Refresh link proposals"
  );
  refreshProposals.type = "button";
  refreshProposals.addEventListener("click", async () => {
    try {
      const result = await api(
        `/v1/projects/${encodeURIComponent(
          state.selectedProject.project_id
        )}/memories/propose-links`
      );
      state.memoryProposals = result.proposals || [];
      renderMemory();
      toast(`Proposals: ${state.memoryProposals.length}`);
    } catch (error) {
      toast(error.message, true);
    }
  });
  unlinkedGroup.append(refreshProposals);
  elements.memoryPanel.append(unlinkedGroup);

  const linkedGroup = node("div", "detail-group");
  const nodeScoped = selectedNode
    ? linked.filter((item) => item.node_ref === selectedNode)
    : linked;
  linkedGroup.append(
    node(
      "h3",
      "",
      selectedNode
        ? `Node memory (${nodeScoped.length})`
        : `Linked memory (${linked.length})`
    )
  );
  if (!nodeScoped.length) {
    linkedGroup.append(node("p", "empty-copy", "No linked memory in this view"));
  } else {
    const list = node("ul", "context-list bench-list");
    for (const memory of nodeScoped.slice(0, 8)) {
      list.append(
        node(
          "li",
          "",
          `${memory.link_state} · ${memory.node_ref || "-"} · ${memory.title}`
        )
      );
    }
    linkedGroup.append(list);
  }
  elements.memoryPanel.append(linkedGroup);
}

async function linkMemory(memoryId, nodeRef, graph, linkState) {
  if (!state.selectedProject) return;
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        state.selectedProject.project_id
      )}/memories/link`,
      {
        method: "POST",
        body: {
          memory_id: memoryId,
          node_ref: nodeRef,
          graph,
          link_state: linkState,
        },
      }
    );
    state.memories = state.memories.map((item) =>
      item.memory_id === memoryId ? result.memory : item
    );
    renderMemory();
    toast(`Memory ${linkState.toLowerCase()} → ${nodeRef}`);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderFuture() {
  if (!elements.futurePanel) return;
  elements.futurePanel.replaceChildren();
  if (!state.selectedProject) {
    elements.futurePanel.append(
      node("p", "empty-copy", "Select a project for Future routes")
    );
    return;
  }
  const heading = node("div", "detail-group");
  heading.append(
    node("h3", "", "Future plane"),
    node(
      "p",
      "context-copy",
      "Seed structure · Bench · Experience · predicted paths. Planning only."
    )
  );
  elements.futurePanel.append(heading);

  const seedGroup = node("div", "detail-group");
  seedGroup.append(node("h3", "", "Seed / structure"));
  const seedGrid = node("dl", "detail-grid");
  addDetail(seedGrid, "Project", state.selectedProject.project_id);
  addDetail(
    seedGrid,
    "Nodes",
    String(state.projection?.nodes?.length || 0)
  );
  addDetail(
    seedGrid,
    "Predicted",
    String(state.projection?.predicted_paths?.length || 0)
  );
  addDetail(
    seedGrid,
    "Documents",
    String(state.projection?.documents?.length || 0)
  );
  seedGroup.append(seedGrid);
  const predicted = state.projection?.predicted_paths || [];
  if (predicted.length) {
    const list = node("ul", "context-list bench-list");
    for (const path of predicted.slice(0, 6)) {
      list.append(
        node(
          "li",
          "",
          path.title || path.path_id || path.label || JSON.stringify(path).slice(0, 80)
        )
      );
    }
    seedGroup.append(list);
  } else {
    seedGroup.append(
      node("p", "empty-copy", "No predicted paths on current projection")
    );
  }
  elements.futurePanel.append(seedGroup);

  const benchGroup = node("div", "detail-group");
  benchGroup.append(
    node(
      "h3",
      "",
      `Bench · Experience (${state.skillBench.length} / ${state.experienceCases.length})`
    )
  );
  benchGroup.append(
    node(
      "p",
      "context-copy",
      `Observations ${state.skillObservations.length} · Context packs ${state.contextPacks.length} · Patterns ${state.experiencePatterns.length}`
    )
  );
  const openBench = node("button", "secondary-button compact-action", "Open Bench tab");
  openBench.type = "button";
  openBench.addEventListener("click", () => showInspectorTab("bench"));
  benchGroup.append(openBench);
  elements.futurePanel.append(benchGroup);

  const memoryGroup = node("div", "detail-group");
  const unlinked = state.memories.filter((item) => item.link_state === "UNLINKED");
  memoryGroup.append(
    node(
      "h3",
      "",
      `Memory (${state.memories.length}, unlinked ${unlinked.length})`
    )
  );
  const openMemory = node("button", "secondary-button compact-action", "Open Memory tab");
  openMemory.type = "button";
  openMemory.addEventListener("click", () => showInspectorTab("memory"));
  memoryGroup.append(openMemory);
  elements.futurePanel.append(memoryGroup);

  const handoffGroup = node("div", "detail-group");
  handoffGroup.append(
    node("h3", "", `Master handoffs (${state.masterHandoffs.length})`)
  );
  if (!state.masterHandoffs.length) {
    handoffGroup.append(node("p", "empty-copy", "No handoff proposals"));
  } else {
    const list = node("ul", "context-list bench-list");
    for (const handoff of state.masterHandoffs.slice(0, 4)) {
      list.append(
        node(
          "li",
          "",
          `${handoff.delivery_state} · ${handoff.source?.kind || "source"}`
        )
      );
    }
    handoffGroup.append(list);
  }
  elements.futurePanel.append(handoffGroup);
}

function toast(message, error = false) {
  const item = node("div", "toast", message);
  if (error) item.style.borderLeftColor = "#b13b36";
  elements.toasts.append(item);
  setTimeout(() => item.remove(), 4200);
}

function bindEvents() {
  document
    .querySelector("#refresh-button")
    .addEventListener("click", refresh);
  elements.todoButton.addEventListener("click", () => openTodoDialog(false));
  elements.todoForm.addEventListener("submit", submitTodo);
  elements.todoScope.addEventListener("change", renderTodoScopeControls);
  elements.todoProject.addEventListener("change", renderTodoScopeControls);
  elements.todoScopeFilter.addEventListener("change", renderTodos);
  elements.todoStateFilter.addEventListener("change", renderTodos);
  if (elements.todoPriorityFilter) {
    elements.todoPriorityFilter.addEventListener("change", renderTodos);
  }
  if (elements.seedWorklistButton) {
    elements.seedWorklistButton.addEventListener("click", seedWorklistTodos);
  }
  if (elements.proposeMasterHandoffButton) {
    elements.proposeMasterHandoffButton.addEventListener(
      "click",
      proposeFreshProjectHandoff
    );
  }
  if (elements.deliverMasterHandoffButton) {
    elements.deliverMasterHandoffButton.addEventListener("click", () => {
      const handoff = state.freshProject.handoff;
      const projectId = state.selectedProject?.project_id;
      if (!handoff || !projectId) {
        toast("Select target project and propose handoff first", true);
        return;
      }
      deliverMasterHandoff(projectId, handoff);
    });
  }

  document
    .querySelector("#release-button")
    .addEventListener("click", () => {
      elements.releaseFormError.textContent = "";
      renderReleaseCatalog();
      elements.releaseDialog.showModal();
    });
  elements.settingsButton.addEventListener("click", () => {
    openProviderSettings().catch((error) => toast(error.message, true));
  });
  elements.sessionObservatoryButton.addEventListener("click", async () => {
    try {
      await refreshSupervisorSessions();
      elements.sessionObservatoryDialog.showModal();
    } catch (error) {
      toast(error.message, true);
    }
  });
  elements.refreshSessionsButton.addEventListener("click", () => {
    refreshSupervisorSessions().catch((error) => toast(error.message, true));
  });
  elements.discoverHostTools.addEventListener("click", () => {
    discoverHostTools().catch((error) => toast(error.message, true));
  });
  elements.hostToolSettings.addEventListener("click", (event) => {
    const action = event.target.closest(
      ".host-tool-set, .host-tool-verify, .host-tool-model-set"
    );
    if (!action) return;
    const operation = action.classList.contains("host-tool-set")
      ? "select"
      : action.classList.contains("host-tool-model-set")
        ? "model"
        : "verify";
    action.disabled = true;
    updateHostTool(action.dataset.tool, operation)
      .catch((error) => {
        elements.settingsError.textContent = error.message;
      })
      .finally(() => {
        action.disabled = false;
      });
  });
  document
    .querySelector("#add-project-button")
    .addEventListener("click", () => elements.projectDialog.showModal());
  document
    .querySelector("#start-project-button")
    .addEventListener("click", openFreshProjectWizard);
  document
    .querySelector("#start-project-topbar-button")
    .addEventListener("click", openFreshProjectWizard);
  elements.dispatchForm.addEventListener("submit", submitDispatch);
  elements.composerActionButton.addEventListener("click", () =>
    toggleComposerActionMenu()
  );
  elements.returnToConductor.addEventListener(
    "click",
    returnToUniverseConductor
  );
  document.addEventListener("click", (event) => {
    if (
      !elements.composerActionMenu.contains(event.target) &&
      !elements.composerActionButton.contains(event.target)
    ) {
      closeComposerActionMenu();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeComposerActionMenu();
  });
  elements.prepareProject.addEventListener("click", prepareProjectSeed);
  elements.exitNodeUniverse.addEventListener("click", exitNodeUniverse);
  elements.closeInspector.addEventListener("click", closeInspector);
  if (elements.conductorSummaryToggle && elements.conductorSummary) {
    elements.conductorSummaryToggle.addEventListener("click", () => {
      const collapsed = elements.conductorSummary.classList.toggle("collapsed");
      syncConductorSummaryToggle(collapsed);
    });
  }
  elements.conversationToggle.addEventListener("click", () => {
    const collapsed = elements.conversationLayer.classList.toggle("collapsed");
    syncConversationToggle(collapsed);
  });
  elements.conversationOpacity.addEventListener("input", () => {
    elements.conversationLayer.style.setProperty(
      "--conversation-opacity",
      String(Number(elements.conversationOpacity.value) / 100)
    );
  });
  elements.projectForm.addEventListener("submit", submitProject);
  elements.settingsForm.addEventListener("submit", submitProviderSettings);

  if (elements.viewModeSelect) {
    elements.viewModeSelect.addEventListener("change", () => {
      const value = elements.viewModeSelect.value;
      const btn = document.querySelector(`.segmented-control [data-view="${value}"]`);
      if (btn) btn.click();
    });
  }
  document.querySelectorAll(".rail-view[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".rail-view[data-view]").forEach((b) => b.classList.remove("selected"));
      button.classList.add("selected");
      const value = button.getAttribute("data-view");
      const seg = document.querySelector(`.segmented-control [data-view="${value}"]`);
      if (seg) seg.click();
      if (elements.viewModeSelect) elements.viewModeSelect.value = value;
    });
  });
  document.querySelectorAll(".rail-view[data-primary-view], .ghost-action[data-primary-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.getAttribute("data-primary-view");
      const nav = elements.primaryNav?.querySelector(`[data-primary-view="${view}"]`);
      if (nav) nav.click();
    });
  });
  const fitBtn = document.querySelector("#action-fit-map");
  if (fitBtn) fitBtn.addEventListener("click", () => document.querySelector("#graph-fit")?.click());
  const refreshAct = document.querySelector("#action-refresh");
  if (refreshAct) refreshAct.addEventListener("click", () => document.querySelector("#refresh-button")?.click());
  const todoAct = document.querySelector("#action-todo");
  if (todoAct) todoAct.addEventListener("click", () => document.querySelector("#todo-button")?.click());
  setInterval(() => {
    const now = new Date().toLocaleTimeString();
    if (elements.conductorClock) elements.conductorClock.textContent = now;
    if (elements.conductorClockCompact) elements.conductorClockCompact.textContent = now;
  }, 1000);
  if (typeof refreshConductorPanel === "function") refreshConductorPanel();

  if (elements.primaryNav) {
    elements.primaryNav.addEventListener("click", (event) => {
      const button = event.target.closest("[data-primary-view]");
      if (!button) return;
      const view = button.getAttribute("data-primary-view");
      for (const item of elements.primaryNav.querySelectorAll("[data-primary-view]")) {
        item.classList.toggle("selected", item === button);
      }
      if (view === "ecosystem") {
        toast("Ecosystem · project list");
        return;
      }
      if (view === "project") {
        if (typeof showGraphView === "function") showGraphView("universe");
        else if (document.querySelector('[data-view="universe"]')) {
          document.querySelector('[data-view="universe"]').click();
        }
        toast("Project topology");
        return;
      }
      if (view === "network") {
        toast("Network peer map is a later surface (placeholder)");
        return;
      }
      const tabMap = {
        experience: "bench",
        memory: "memory",
        future: "future",
        bench: "bench",
      };
      const tab = tabMap[view];
      if (tab && typeof showInspectorTab === "function") {
        document.body.classList.add("inspector-open");
        showInspectorTab(tab);
      }
    });
  }
  

function syncConductorSummaryToggle(collapsed) {
  if (!elements.conductorSummaryToggle) return;
  const title = collapsed ? "Expand overview" : "Collapse overview";
  elements.conductorSummaryToggle.title = title;
  elements.conductorSummaryToggle.setAttribute("aria-label", title);
  elements.conductorSummaryToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
}

function refreshConductorPanel() {
  const projectCount = (state.projects || []).length;
  const todoCount = (state.todos || []).length;
  const dispatchCount = (state.dispatches || []).length;
  if (elements.metricProjects) elements.metricProjects.textContent = String(projectCount);
  if (elements.metricTodos) elements.metricTodos.textContent = String(todoCount);
  if (elements.metricDispatches) elements.metricDispatches.textContent = String(dispatchCount);
  if (elements.metricService) {
    const ready = elements.serviceStatus?.dataset?.state === "ready";
    elements.metricService.textContent = ready ? "READY" : "…";
  }
  if (elements.conductorSummaryLine) {
    const service = elements.metricService?.textContent || "—";
    elements.conductorSummaryLine.textContent =
      `P ${projectCount} · T ${todoCount} · D ${dispatchCount} · ${service}`;
  }
  if (elements.workspaceTitle && elements.workspaceTitle.classList.contains("conductor-greeting")) {
    const hour = new Date().getHours();
    const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    elements.workspaceTitle.textContent = greet + ", Conductor.";
  }
  if (elements.workspaceSubtitle) {
    if (state.selectedProject) {
      elements.workspaceSubtitle.textContent =
        `Observing ${state.selectedProject.project_id} · ${projectCount} project(s) registered`;
    } else if (projectCount) {
      elements.workspaceSubtitle.textContent =
        `You're connected to ${projectCount} observed project(s).`;
    } else {
      elements.workspaceSubtitle.textContent = "Connect a project to begin observation.";
    }
  }
  if (elements.inspectorTitle) {
    elements.inspectorTitle.textContent = state.selectedProject
      ? state.selectedProject.project_id
      : "Local Instance";
  }
  if (elements.inspectorSubtitle) {
    elements.inspectorSubtitle.textContent = state.selectedProject
      ? state.selectedProject.project_root || "Connected project"
      : "Select a project or node";
  }
  if (elements.statusBarLeft) {
    elements.statusBarLeft.textContent = state.selectedProject
      ? `Project ${state.selectedProject.project_id}`
      : `${projectCount} projects · local control plane`;
  }
  if (elements.statusBarRight) {
    const ready = elements.serviceStatus?.dataset?.state === "ready";
    elements.statusBarRight.textContent = ready ? "Network Health · READY" : "Network Health · …";
  }
  if (elements.conductorClock || elements.conductorClockCompact) {
    const now = new Date().toLocaleTimeString();
    if (elements.conductorClock) elements.conductorClock.textContent = now;
    if (elements.conductorClockCompact) elements.conductorClockCompact.textContent = now;
  }
  if (typeof refreshLawStrip === "function") refreshLawStrip();
}

function refreshLawStrip() {
    if (elements.lawContract) {
      elements.lawContract.textContent =
        state.health?.mode_contract?.status === "ACTIVE" ? "COMPATIBLE" : "UNKNOWN";
    }
    if (elements.lawRuntime) {
      const ready = elements.serviceStatus?.dataset?.state === "ready";
      elements.lawRuntime.textContent = ready ? "VERIFIED" : "STANDBY";
    }
    if (elements.lawLocal) {
      elements.lawLocal.textContent = "INSTANCE";
    }
  }
  const _setServiceStatus = typeof setServiceStatus === "function" ? setServiceStatus : null;
  elements.freshProjectForm.addEventListener("submit", submitFreshProjectIntent);
  document
    .querySelector("#edit-project-intent")
    .addEventListener("click", () => showFreshProjectPanel("intent"));
  document
    .querySelector("#back-to-routes")
    .addEventListener("click", () => showFreshProjectPanel("routes"));
  document
    .querySelector("#back-to-composition")
    .addEventListener("click", () => showFreshProjectPanel("composition"));
  elements.prepareRefinementButton.addEventListener(
    "click",
    prepareFreshProjectRefinement
  );
  elements.createPlanningProposal.addEventListener(
    "click",
    createFreshProjectPlanningProposal
  );
  elements.executePlanningProposal.addEventListener(
    "click",
    executeFreshProjectPlanningProposal
  );
  elements.adoptRefinementButton.addEventListener(
    "click",
    adoptFreshProjectRefinement
  );
  elements.adoptCompositionButton.addEventListener(
    "click",
    adoptFreshProjectComposition
  );
  elements.releaseForm.addEventListener("submit", submitRelease);
  for (const button of document.querySelectorAll("[data-close-dialog]")) {
    button.addEventListener("click", () => button.closest("dialog").close());
  }

  for (const button of document.querySelectorAll("[data-view]")) {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      for (const candidate of document.querySelectorAll("[data-view]")) {
        candidate.classList.toggle("selected", candidate === button);
      }
      state.selectedNode = null;
      state.focusedNodeId = null;
      elements.nodeBreadcrumb.classList.add("hidden");
      buildGraph();
      renderDetails();
    });
  }
  for (const button of document.querySelectorAll("[data-tab]")) {
    button.addEventListener("click", () => showInspectorTab(button.dataset.tab));
  }
  elements.canvas.addEventListener("click", selectGraphNode);
  elements.canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    state.graphPan = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: state.graph.x,
      originY: state.graph.y,
      moved: false,
    };
    try {
      elements.canvas.setPointerCapture(event.pointerId);
    } catch (_error) {
      // Older hosts without capture still pan via window listeners.
    }
  });
  elements.canvas.addEventListener("pointermove", (event) => {
    const pan = state.graphPan;
    if (!pan || pan.pointerId !== event.pointerId) return;
    const dx = event.clientX - pan.startX;
    const dy = event.clientY - pan.startY;
    if (Math.hypot(dx, dy) > 3) {
      pan.moved = true;
      elements.canvas.classList.add("is-panning");
    }
    if (!pan.moved) return;
    state.graph.x = pan.originX + dx;
    state.graph.y = pan.originY + dy;
    drawGraph();
  });
  const endPan = (event) => {
    const pan = state.graphPan;
    if (!pan || pan.pointerId !== event.pointerId) return;
    elements.canvas.classList.remove("is-panning");
    // Keep moved flag until click handler runs, then clear.
    window.setTimeout(() => {
      if (state.graphPan === pan) state.graphPan = null;
    }, 0);
  };
  elements.canvas.addEventListener("pointerup", endPan);
  elements.canvas.addEventListener("pointercancel", endPan);
  elements.canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      setGraphScale(state.graph.scale * (event.deltaY > 0 ? 0.9 : 1.1));
    },
    { passive: false }
  );
  if (elements.graphZoomIn) {
    elements.graphZoomIn.addEventListener("click", () =>
      setGraphScale(state.graph.scale * 1.12)
    );
  }
  if (elements.graphZoomOut) {
    elements.graphZoomOut.addEventListener("click", () =>
      setGraphScale(state.graph.scale * 0.9)
    );
  }
  if (elements.graphFit) {
    elements.graphFit.addEventListener("click", fitGraphView);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (elements.composerActionMenu && !elements.composerActionMenu.classList.contains("hidden")) {
      closeComposerActionMenu();
      return;
    }
    if (
      elements.conversationLayer &&
      !elements.conversationLayer.classList.contains("collapsed")
    ) {
      elements.conversationLayer.classList.add("collapsed");
      syncConversationToggle(true);
      return;
    }
    if (
      elements.conductorSummary &&
      !elements.conductorSummary.classList.contains("collapsed")
    ) {
      elements.conductorSummary.classList.add("collapsed");
      syncConductorSummaryToggle(true);
      return;
    }
    if (document.body.classList.contains("inspector-open")) {
      closeInspector();
    }
  });
  const resize = new ResizeObserver(drawGraph);
  resize.observe(elements.canvas);
}

bindEvents();
refresh();
window.setInterval(refreshConductorRoom, 1200);
