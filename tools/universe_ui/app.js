"use strict";

const state = {
  projects: [],
  todos: [],
  selectedProject: null,
  projection: null,
  /** project_id -> projection; multiverse always expands from this cache */
  projectionsByProject: {},
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
  view: "universe",
  roomMessages: [],
  conductorMessages: [],
  conductorPermissions: [],
  conductorRuntimeBinding: null,
  conductorRefreshInFlight: false,
  conductorRoomStream: null,
  conductorRoomStreamState: "IDLE",
  conductorStreamReplies: {},
  todoDraftSourceKind: "USER",
  projectRoomStream: null,
  projectRoomStreamProjectId: null,
  projectRoomStreamState: "IDLE",
  projectStreamReplies: {},
  projectPermissions: [],
  governanceProposals: [],
  governanceProposalInbox: [],
  masterBridge: null,
  modeContract: null,
  providerSettings: null,
  /** CLI+preset model catalog from /v1/settings/provider-models */
  providerModels: null,
  workerBindings: null,
  hostTools: null,
  runtimePreflight: null,
  runtimeAudit: null,
  remoteAccess: null,
  accessSurface: "LOCAL_BROWSER",
  supervisorSessions: [],
  roomSessionBindings: [],
  selectedSupervisorAnchorKey: null,
  observatoryShowAll: false,
  /** Expanded (node|mode) groups so operators can pick an alternate 1:1 session. */
  observatoryExpandedCoords: {},
  settingsTab: "service",
  observatoryTab: "sessions",
  todoTab: "board",
  supervisorEvents: [],
  legacyExecutors: [],
  providerActivitySources: [],
  providerActivityDiscoveries: [],
  providerChatRooms: [],
  providerChatSearch: "",
  providerChatShowWorkers: false,
  providerChatShowHidden: false,
  providerChatExpandedProjects: {},
  providerChatExpandedBranches: {},
  selectedProviderChatKey: null,
  multiRooms: [],
  activeMultiRoomId: null,
  activeMultiRoomSnapshot: null,
  multiRoomStream: null,
  multiRoomLiveOutput: {},
  providerTailTimer: null,
  providerTailInFlight: false,
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
  /** Hovered graph node id (for icon map tooltips). */
  hoveredNodeId: null,
  inspectorDismissed: false,
};

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  modeStatus: document.querySelector("#mode-status"),
  projectList: document.querySelector("#project-list"),
  workspaceTitle: document.querySelector("#workspace-title"),
  workspaceSubtitle: document.querySelector("#workspace-subtitle"),
  canvas: document.querySelector("#universe-graph"),
  graphEmpty: document.querySelector("#graph-empty"),
  graphHint: document.querySelector("#graph-hint"),
  graphTooltip: document.querySelector("#graph-node-tooltip"),
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
  sessionObservatoryTopbarButton: document.querySelector(
    "#session-observatory-topbar-button"
  ),
  sessionObservatoryDialog: document.querySelector("#session-observatory-dialog"),
  sessionObservatorySummary: document.querySelector("#session-observatory-summary"),
  sessionObservatoryList: document.querySelector("#session-observatory-list"),
  sessionRailList: document.querySelector("#session-rail-list"),
  sessionRailSearch: document.querySelector("#session-rail-search"),
  sessionRailShowWorkers: document.querySelector("#session-rail-show-workers"),
  sessionRailShowHidden: document.querySelector("#session-rail-show-hidden"),
  sessionSummaryDialog: document.querySelector("#session-summary-dialog"),
  sessionSummaryTitle: document.querySelector("#session-summary-title"),
  sessionSummarySubtitle: document.querySelector("#session-summary-subtitle"),
  sessionSummaryFacts: document.querySelector("#session-summary-facts"),
  sessionSummaryOpen: document.querySelector("#session-summary-open"),
  sessionSummaryManage: document.querySelector("#session-summary-manage"),
  sessionObservatoryDetail: document.querySelector("#session-observatory-detail"),
  sessionObservatoryDetailMeta: document.querySelector(
    "#session-observatory-detail-meta"
  ),
  sessionObservatoryDetailPreview: document.querySelector(
    "#session-observatory-detail-preview"
  ),
  observatoryShowAllToggle: document.querySelector("#observatory-show-all"),
  cleanupSessionsButton: document.querySelector("#cleanup-sessions-button"),
  legacyExecutorList: document.querySelector("#legacy-executor-list"),
  sessionEventList: document.querySelector("#session-event-list"),
  runtimeAuditGrid: document.querySelector("#runtime-audit-grid"),
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
  workerBindingScope: document.querySelector("#worker-binding-scope"),
  workerBindingSettings: document.querySelector("#worker-binding-settings"),
  providerModelCatalog: document.querySelector("#provider-model-catalog"),
  refreshProviderModels: document.querySelector("#refresh-provider-models-button"),
  hostProfilePath: document.querySelector("#host-profile-path"),
  hostToolSettings: document.querySelector("#host-tool-settings"),
  discoverHostTools: document.querySelector("#discover-host-tools-button"),
  runtimePreflightSummary: document.querySelector("#runtime-preflight-summary"),
  runtimePreflightList: document.querySelector("#runtime-preflight-list"),
  remoteAccessStatus: document.querySelector("#remote-access-status"),
  remoteAccessEndpoint: document.querySelector("#remote-access-endpoint"),
  remoteAccessTransport: document.querySelector("#remote-access-transport"),
  remoteConnectorFields: document.querySelector("#remote-connector-fields"),
  remotePublicUrl: document.querySelector("#remote-public-url"),
  remoteSshHost: document.querySelector("#remote-ssh-host"),
  remoteSshPort: document.querySelector("#remote-ssh-port"),
  remoteSshUser: document.querySelector("#remote-ssh-user"),
  remoteForwardPort: document.querySelector("#remote-forward-port"),
  remoteIdentityFile: document.querySelector("#remote-identity-file"),
  remoteKnownHostsFile: document.querySelector("#remote-known-hosts-file"),
  remotePairingInvite: document.querySelector("#remote-pairing-invite"),
  remotePairingList: document.querySelector("#remote-pairing-list"),
  remoteDeviceList: document.querySelector("#remote-device-list"),
  startRemoteAccess: document.querySelector("#start-remote-access-button"),
  stopRemoteAccess: document.querySelector("#stop-remote-access-button"),
  createPairing: document.querySelector("#create-pairing-button"),
  rendezvousStatus: document.querySelector("#rendezvous-status"),
  rendezvousSummary: document.querySelector("#rendezvous-summary"),
  rendezvousPendingList: document.querySelector("#rendezvous-pending-list"),
  refreshRendezvous: document.querySelector("#refresh-rendezvous-button"),
  stopRendezvous: document.querySelector("#stop-rendezvous-button"),
  multiRoomList: document.querySelector("#multi-room-list"),
  multiRoomDetail: document.querySelector("#multi-room-detail"),
  multiRoomMessage: document.querySelector("#multi-room-message"),
  refreshRooms: document.querySelector("#refresh-rooms-button"),
  createMeetingRoom: document.querySelector("#create-meeting-room-button"),
  postRoomMessage: document.querySelector("#post-room-message-button"),
  callMasterButton: document.querySelector("#call-master-button"),
  injectProjectId: document.querySelector("#inject-project-id"),
  injectProvider: document.querySelector("#inject-provider"),
  injectSessionRef: document.querySelector("#inject-session-ref"),
  injectSessionRefButton: document.querySelector("#inject-session-ref-button"),
  observatoryInjectProvider: document.querySelector("#observatory-inject-provider"),
  observatoryInjectRef: document.querySelector("#observatory-inject-ref"),
  observatoryInjectProject: document.querySelector("#observatory-inject-project"),
  observatoryInjectMode: document.querySelector("#observatory-inject-mode"),
  observatoryInjectButton: document.querySelector("#observatory-inject-button"),
  observatoryInjectStatus: document.querySelector("#observatory-inject-status"),
  providerActivitySummary: document.querySelector("#provider-activity-summary"),
  providerActivityList: document.querySelector("#provider-activity-list"),
  providerActivityDiscovery: document.querySelector("#provider-activity-discovery"),
  discoverProviderActivity: document.querySelector("#discover-provider-activity-button"),
  roomSessionBindingList: document.querySelector("#room-session-binding-list"),
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

function compactModelRef(value) {
  const modelRef = typeof value === "string" ? value.trim() : "";
  if (!modelRef) return "UNKNOWN";
  const segments = modelRef.split("/").filter(Boolean);
  return segments.at(-1) || modelRef;
}

function sessionDisplayName(session) {
  const alias = String(
    session.anchor_session?.alias || session.alias || ""
  ).trim();
  if (alias) {
    return alias.replace(/\s+\|\s+(?:AUTO|CODEX|CLAUDE|GROK)\s*$/i, "");
  }
  const parts = [session.node, session.mode].filter(Boolean);
  return [...new Set(parts)].join(" ") || "Unnamed session";
}

function anchorSessionKey(session) {
  if (session?.anchor_session?.anchor_key) {
    return session.anchor_session.anchor_key;
  }
  return [
    session?.project_id || session?.node || "UNKNOWN",
    session?.node || "UNKNOWN",
    session?.mode || "UNKNOWN",
    session?.anchor_ref || "UNKNOWN",
  ].join("|");
}

function currentAnchorLabel(session) {
  const ref = String(
    session?.anchor_session?.current_anchor_ref ||
      session?.anchor_ref ||
      "UNKNOWN"
  );
  if (ref === "UNKNOWN") return "Anchor pending";
  const compact = ref.replace(/^.*CURRENT-/i, "");
  return `Anchor ${compact.length > 18 ? compact.slice(-18) : compact}`;
}

function sessionCoordinateLabel(session) {
  if (session.node === session.mode) return session.node || "UNKNOWN";
  return `${session.node || "UNKNOWN"} / ${session.mode || "UNKNOWN"}`;
}

function sessionStateLabel(session) {
  return session.state === "LIVE" ? "SESSION LIVE" : (session.state || "UNKNOWN");
}

function parseSessionDate(value) {
  if (!value || value === "UNKNOWN") return null;
  const raw = String(value).trim();
  const normalized = /Z$|[+-]\d{2}:\d{2}$/.test(raw) ? raw : `${raw}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatSessionTime(value, options = {}) {
  const { withSeconds = true, relative = false } = options;
  if (!value || value === "UNKNOWN") return "—";
  const date = parseSessionDate(value);
  const text = String(value);
  const match = text.match(
    /(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d+))?/
  );
  let absolute = text.length > 22 ? `${text.slice(0, 19)}…` : text;
  if (match) {
    absolute = withSeconds
      ? `${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6] || "00"}`
      : `${match[2]}-${match[3]} ${match[4]}:${match[5]}`;
    if (match[7] && withSeconds) {
      // sub-second when bulk rows share the same second
      absolute += `.${match[7].slice(0, 3)}`;
    }
  }
  if (!relative || !date) return absolute;
  const deltaMs = Date.now() - date.getTime();
  const abs = Math.abs(deltaMs);
  const future = deltaMs < 0;
  let rel = "just now";
  if (abs >= 60_000 && abs < 3_600_000) {
    rel = `${Math.round(abs / 60_000)}m`;
  } else if (abs >= 3_600_000 && abs < 86_400_000) {
    rel = `${Math.round(abs / 3_600_000)}h`;
  } else if (abs >= 86_400_000) {
    rel = `${Math.round(abs / 86_400_000)}d`;
  } else if (abs >= 1_000 && abs < 60_000) {
    rel = `${Math.round(abs / 1_000)}s`;
  }
  if (future && rel !== "just now") rel = `in ${rel}`;
  else if (!future && rel !== "just now") rel = `${rel} ago`;
  return `${rel} · ${absolute}`;
}

function sessionFingerprint(session) {
  return `${sessionCoordinateLabel(session)} · ${currentAnchorLabel(session)}`;
}

/** Resolve registered project_root for session.node (project_id). */
function sessionProjectPathLabel(session) {
  if (session.project_root) {
    const name = session.project_display_name || session.node || "project";
    return `📁 ${name} · ${session.project_root}`;
  }
  // Fallback: client-side project list if audit not yet enriched.
  const project = (state.projects || []).find(
    (item) => item.project_id === session.node
  );
  if (project?.project_root) {
    return `📁 ${projectDisplayName(project)} · ${project.project_root}`;
  }
  if (String(session.node || "").toUpperCase() === "CONDUCTOR") {
    return "📁 CONDUCTOR mode session · (not a project root)";
  }
  return session.project_bind_note
    ? `📁 ${session.project_bind_note}`
    : `📁 unbound · node=${session.node || "—"}`;
}

function sessionCoordinateKey(session) {
  return anchorSessionKey(session);
}

function sessionActivityMs(session) {
  const date = parseSessionDate(
    session?.last_activity_at || session?.updated_at || session?.created_at
  );
  return date ? date.getTime() : 0;
}

function sessionObservatoryRank(session) {
  const stateName = String(session?.state || "").toUpperCase();
  const live = stateName === "LIVE" ? 3 : stateName === "STARTING" ? 2 : 1;
  const isDefault = session?.is_default ? 1 : 0;
  return live * 1e15 + isDefault * 1e14 + sessionActivityMs(session);
}

function observatoryEligibleSessions(sessions) {
  const list = Array.isArray(sessions) ? sessions.slice() : [];
  if (state.observatoryShowAll) return list;
  // Hide STOPPED noise; keep LIVE / default / recent DISCONNECTED.
  return list.filter((session) => {
    const stateName = String(session.state || "").toUpperCase();
    if (stateName === "STOPPED") return false;
    if (stateName === "LIVE" || session.is_default) return true;
    const activity = parseSessionDate(
      session.last_activity_at || session.updated_at
    );
    if (!activity) return stateName !== "DISCONNECTED";
    return Date.now() - activity.getTime() < 7 * 86_400_000;
  });
}

/**
 * Collapse to last-active (prefer LIVE/default) per (node, mode).
 * Expanded coords show the full alternate 1:1 list for that slot.
 */
function observatorySessionGroups(sessions) {
  const eligible = observatoryEligibleSessions(sessions);
  const byCoord = new Map();
  for (const session of eligible) {
    const key = sessionCoordinateKey(session);
    if (!byCoord.has(key)) byCoord.set(key, []);
    byCoord.get(key).push(session);
  }
  const groups = [];
  for (const [key, members] of byCoord.entries()) {
    const ranked = members
      .slice()
      .sort((a, b) => sessionObservatoryRank(b) - sessionObservatoryRank(a));
    const expanded = Boolean(state.observatoryExpandedCoords[key]);
    groups.push({
      key,
      primary: ranked[0],
      alternatives: ranked.slice(1),
      members: ranked,
      expanded,
    });
  }
  groups.sort(
    (a, b) => sessionObservatoryRank(b.primary) - sessionObservatoryRank(a.primary)
  );
  return groups;
}

function observatoryVisibleSessions(sessions) {
  if (state.observatoryShowAll) {
    return observatoryEligibleSessions(sessions);
  }
  const cards = [];
  for (const group of observatorySessionGroups(sessions)) {
    if (group.expanded) {
      cards.push(...group.members);
    } else {
      cards.push(group.primary);
    }
  }
  return cards;
}

function toggleObservatoryCoordExpand(coordKey) {
  state.observatoryExpandedCoords = {
    ...state.observatoryExpandedCoords,
    [coordKey]: !state.observatoryExpandedCoords[coordKey],
  };
  renderSessionObservatory();
}

function sessionPreviewSnippet(session) {
  const lines = session?.preview?.lines || [];
  // Only show chat when server marked it as tied to this exact session.
  if (!session?.preview?.tied_to_session || !lines.length) {
    return "";
  }
  const last = lines[lines.length - 1];
  const role = last.author_role || "?";
  const text = String(last.text || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  return `${role}: ${text.length > 90 ? `${text.slice(0, 90)}…` : text}`;
}

function uniqueTimeRows(session) {
  const rows = [
    {
      label: "Activity",
      value: session.last_activity_at || session.updated_at,
    },
    { label: "Anchor", value: session.last_anchor_at },
    { label: "Last message", value: session.last_message_at },
    { label: "Updated", value: session.updated_at },
  ];
  const seen = new Set();
  const out = [];
  for (const row of rows) {
    if (!row.value || row.value === "UNKNOWN") continue;
    const key = String(row.value);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  if (!out.length && session.updated_at) {
    out.push({ label: "Updated", value: session.updated_at });
  }
  return out;
}

function selectSupervisorSession(session) {
  state.selectedSupervisorAnchorKey = session ? anchorSessionKey(session) : null;
  renderSessionObservatory();
}

function renderSelectedSessionDetail() {
  const detail = elements.sessionObservatoryDetail;
  const meta = elements.sessionObservatoryDetailMeta;
  const preview = elements.sessionObservatoryDetailPreview;
  if (!detail || !meta || !preview) return;
  const session = (state.supervisorSessions || []).find(
    (item) => anchorSessionKey(item) === state.selectedSupervisorAnchorKey
  );
  if (!session) {
    detail.classList.add("hidden");
    meta.replaceChildren();
    preview.replaceChildren();
    return;
  }
  detail.classList.remove("hidden");
  meta.replaceChildren();
  meta.append(
    node("div", "session-detail-title", sessionFingerprint(session)),
    node(
      "div",
      "",
      `${session.state || "?"} · ${session.is_default ? "DEFAULT" : "alt"} · ${session.currentness || "UNKNOWN"}`
    )
  );
  const pathLine = sessionProjectPathLabel(session);
  meta.append(
    node(
      "div",
      session.project_bound ? "session-detail-path bound" : "session-detail-path unbound",
      pathLine
    )
  );
  for (const row of uniqueTimeRows(session)) {
    meta.append(
      node(
        "div",
        "",
        `${row.label} · ${formatSessionTime(row.value, {
          withSeconds: true,
          relative: true,
        })}`
      )
    );
  }
  meta.append(node("div", "session-detail-ref", currentAnchorLabel(session)));
  preview.replaceChildren();
  const lines = session.preview?.lines || [];
  if (!lines.length || !session.preview?.tied_to_session) {
    preview.append(
      node(
        "p",
        "empty-copy",
        "No durable turns are bound to this Anchor Session yet. " +
          "Shared project chat is intentionally not shown (avoids identical previews)."
      )
    );
    return;
  }
  preview.append(
    node(
      "small",
      "session-preview-source",
      `Preview · ${session.preview?.source || "UNKNOWN"} · match ${session.preview?.match || "?"}`
    )
  );
  for (const line of lines.slice(-2)) {
    const row = node("div", "session-preview-line");
    row.append(
      node("strong", "", line.author_role || "?"),
      node("span", "", String(line.text || "").replace(/\s+/g, " ").trim())
    );
    if (line.created_at) {
      row.append(node("time", "", formatSessionTime(line.created_at)));
    }
    preview.append(row);
  }
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
  const [audit, legacy, activity, chatCatalog] = await Promise.all([
    api("/v1/runtime/audit"),
    api("/v1/supervisor/legacy-executors"),
    api("/v1/session-observer/sources"),
    api("/v1/session-observer/chat-rooms"),
  ]);
  state.runtimeAudit = audit;
  state.runtimePreflight = audit.preflight || null;
  state.supervisorSessions = audit.sessions || [];
  if (
    !state.selectedSupervisorAnchorKey ||
    !state.supervisorSessions.some(
      (session) =>
        anchorSessionKey(session) === state.selectedSupervisorAnchorKey
    )
  ) {
    const preferred =
      state.supervisorSessions.find(
        (session) =>
          session.is_default &&
          ((state.conversationTarget.kind === "PROJECT_MASTER" &&
            session.node === state.conversationTarget.projectId &&
            session.mode === "MASTER") ||
            (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR" &&
              session.mode === "CONDUCTOR"))
      ) ||
      state.supervisorSessions.find((session) => session.is_default) ||
      state.supervisorSessions[0];
    state.selectedSupervisorAnchorKey = preferred
      ? anchorSessionKey(preferred)
      : null;
  }
  state.roomSessionBindings = audit.room_session_bindings || [];
  state.supervisorEvents = audit.recent_events || [];
  state.legacyExecutors = legacy.executors || [];
  state.providerActivitySources = activity.sources || [];
  state.providerChatRooms = chatCatalog.rooms || [];
  prefillsObservatoryInjectForm();
  renderRuntimePreflight();
  renderSessionObservatory();
  renderSessionRail();
  renderProviderActivitySources();
}

async function tailProviderSessions() {
  if (state.providerTailInFlight || document.visibilityState !== "visible") return;
  state.providerTailInFlight = true;
  try {
    const result = await api("/v1/session-observer/tail", {
      method: "POST",
      body: {},
    });
    state.providerChatRooms = result.catalog?.rooms || state.providerChatRooms;
    renderSessionRail();
  } catch (_error) {
    // The next bounded poll retries. UNKNOWN remains visible instead of being
    // promoted to a guessed live state.
  } finally {
    state.providerTailInFlight = false;
  }
}

async function discoverProviderActivitySources() {
  if (elements.discoverProviderActivity) {
    elements.discoverProviderActivity.disabled = true;
  }
  try {
    const results = await Promise.all(
      ["CODEX", "CLAUDE", "GROK"].map((provider) =>
        api(`/v1/session-observer/discover?provider=${provider}`)
      )
    );
    state.providerActivityDiscoveries = results.flatMap(
      (result) => result.sources || []
    );
    renderProviderActivitySources();
  } finally {
    if (elements.discoverProviderActivity) {
      elements.discoverProviderActivity.disabled = false;
    }
  }
}

async function registerProviderActivitySource(source) {
  await api("/v1/session-observer/sources", {
    method: "POST",
    body: {
      provider: source.provider,
      source_key: source.source_key,
    },
  });
  await refreshSupervisorSessions();
  toast("Activity source registered locally");
}

async function scanProviderActivitySource(sourceId) {
  const result = await api(
    `/v1/session-observer/sources/${encodeURIComponent(sourceId)}/scan`,
    { method: "POST", body: {} }
  );
  await refreshSupervisorSessions();
  toast(`Activity scan: ${result.added || 0} new events`);
}

async function showProviderActivityBatch(sourceId) {
  const result = await api(
    `/v1/session-observer/sources/${encodeURIComponent(sourceId)}/batch-candidate`
  );
  const candidate = result.candidate || {};
  toast(
    candidate.status === "REVIEW_REQUIRED"
      ? `${candidate.activity_refs?.length || 0} activity references are ready for review`
      : "No reviewable activity boundary yet"
  );
}

async function recordProviderActivityMemory(sourceId) {
  const projectId = state.selectedProject?.project_id;
  if (!projectId) {
    throw new Error("Select a project before recording activity memory");
  }
  const result = await api(
    `/v1/session-observer/sources/${encodeURIComponent(sourceId)}/record-memory`,
    { method: "POST", body: { project_id: projectId } }
  );
  await selectProject(projectId, { revealInspector: true });
  showInspectorTab("memory");
  toast(
    result.status === "PROVIDER_ACTIVITY_MEMORY_RECORDED"
      ? "Activity batch recorded as unlinked project memory"
      : "Activity batch memory already exists"
  );
}

function renderProviderActivitySources() {
  if (!elements.providerActivityList || !elements.providerActivityDiscovery) return;
  const sources = state.providerActivitySources || [];
  if (elements.providerActivitySummary) {
    const active = sources.filter((source) => source.status === "ACTIVE").length;
    const unknown = sources.filter((source) => source.status === "UNKNOWN").length;
    elements.providerActivitySummary.textContent =
      `${sources.length} registered - ${active} active - ${unknown} needs attention`;
  }
  elements.providerActivityList.replaceChildren();
  if (!sources.length) {
    elements.providerActivityList.append(
      node("p", "empty-copy", "No provider source is registered yet.")
    );
  }
  for (const source of sources) {
    const card = node("article", "supervisor-session-card");
    const heading = node("div", "session-card-heading");
    heading.append(
      node("strong", "", `${source.provider} activity`),
      node("span", "session-state-pill", source.status || "UNKNOWN")
    );
    heading.lastElementChild.dataset.state = source.status || "UNKNOWN";
    const meta = node("div", "session-card-meta");
    meta.append(
      node("span", "", "Provider history"),
      node("span", "", `cursor ${source.cursor?.ordinal || 0}`),
      node("span", "", source.last_seen_at ? formatSessionTime(source.last_seen_at) : "not scanned")
    );
    const sourceLabel = "Local provider source";
    const reason = source.reason
      ? node("p", "session-path-line unbound", source.reason)
      : node("p", "session-path-line bound", sourceLabel);
    const actions = node("div", "session-card-actions");
    const scan = node("button", "secondary-button compact-action", "Scan now");
    scan.type = "button";
    scan.addEventListener("click", () => {
      scanProviderActivitySource(source.source_id).catch((error) => toast(error.message, true));
    });
    const batch = node("button", "secondary-button compact-action", "Review batch");
    batch.type = "button";
    batch.addEventListener("click", () => {
      showProviderActivityBatch(source.source_id).catch((error) => toast(error.message, true));
    });
    const record = node("button", "secondary-button compact-action", "Record memory");
    record.type = "button";
    record.addEventListener("click", () => {
      recordProviderActivityMemory(source.source_id).catch((error) => toast(error.message, true));
    });
    actions.append(scan, batch, record);
    card.append(heading, meta, reason, actions);
    elements.providerActivityList.append(card);
  }

  elements.providerActivityDiscovery.replaceChildren();
  const registeredSourceKeys = new Set(
    sources.map((source) => source.source_key).filter(Boolean)
  );
  const discovered = (state.providerActivityDiscoveries || []).filter(
    (source) => !registeredSourceKeys.has(source.source_key)
  );
  if (!discovered.length) return;
  elements.providerActivityDiscovery.append(
    node("p", "section-note", `${discovered.length} local source candidates`)
  );
  for (const source of discovered) {
    const row = node("div", "provider-activity-discovery-row");
    row.append(
      node("span", "", `${source.provider} activity source`),
      node("code", "", source.display_name || source.workspace_name || "Local session")
    );
    const add = node("button", "secondary-button compact-action", "Register");
    add.type = "button";
    add.addEventListener("click", () => {
      registerProviderActivitySource(source).catch((error) => toast(error.message, true));
    });
    add.disabled = source.identity_state !== "VERIFIED";
    add.title = add.disabled
      ? "Provider metadata has not established a stable session identity"
      : "Register this local source";
    row.append(add);
    elements.providerActivityDiscovery.append(row);
  }
}

function prefillsObservatoryInjectForm() {
  if (!elements.observatoryInjectProject) return;
  if (!elements.observatoryInjectProject.value.trim()) {
    const selected =
      state.selectedProjectId ||
      state.projects?.[0]?.project_id ||
      "CONDUCTOR";
    elements.observatoryInjectProject.value = selected;
  }
  if (elements.observatoryInjectMode && selectedIsConductorProject()) {
    // leave mode as user set; default MASTER for project ids
  }
}

function selectedIsConductorProject() {
  const project = (elements.observatoryInjectProject?.value || "").trim();
  return project === "CONDUCTOR" || project === "universe";
}

async function cleanupSupervisorSessions() {
  const confirmed = window.confirm(
    "Clean inactive Supervisor sessions?\n\n" +
      "Removes DISCONNECTED / STOPPED / REGISTERED / UNKNOWN rows.\n" +
      "Keeps LIVE (and STARTING). Does not kill processes.\n" +
      "Mode-change / boot / inject can re-register coords later."
  );
  if (!confirmed) return;
  if (elements.cleanupSessionsButton) {
    elements.cleanupSessionsButton.disabled = true;
  }
  try {
    // Sweep zombies to DISCONNECTED, then purge non-LIVE.
    const result = await api("/v1/supervisor/sessions/cleanup", {
      method: "POST",
      body: { keep_live_only: true, include_unknown: true },
    });
    const removed = result.cleanup?.removed_count ?? 0;
    const kept = result.cleanup?.kept_count ?? 0;
    state.selectedSupervisorAnchorKey = null;
    await refreshSupervisorSessions();
    toast(`Sessions cleaned · removed ${removed} · kept ${kept}`);
  } finally {
    if (elements.cleanupSessionsButton) {
      elements.cleanupSessionsButton.disabled = false;
    }
  }
}

async function injectSessionFromObservatory() {
  const provider = elements.observatoryInjectProvider?.value || "CODEX";
  const sessionRef = elements.observatoryInjectRef?.value?.trim() || "";
  const projectId =
    elements.observatoryInjectProject?.value?.trim() ||
    state.selectedProjectId ||
    "CONDUCTOR";
  const mode = elements.observatoryInjectMode?.value || "MASTER";
  if (!sessionRef) {
    throw new Error("Session / thread ref is required");
  }
  if (elements.observatoryInjectStatus) {
    elements.observatoryInjectStatus.textContent = "Injecting…";
  }
  const body = {
    project_id: projectId,
    node: projectId,
    mode,
    room_type: mode === "CONDUCTOR" ? "PROJECT" : "PROJECT",
    slot_role: mode === "CONDUCTOR" ? "MASTER" : "MASTER",
    provider,
    session_ref: sessionRef,
    make_default: true,
  };
  const result = await api("/v1/sessions/inject", {
    method: "POST",
    body,
  });
  if (elements.observatoryInjectStatus) {
    const created = result.supervisor_session_created
      ? "registered"
      : "already registered";
    elements.observatoryInjectStatus.textContent =
      `Injected (${created}). ` +
      (result.bridge_line || result.status || "");
  }
  elements.observatoryInjectRef.value = "";
  await refreshSupervisorSessions();
  // Product path: open Project Master room, not only Session Observatory.
  const masterTarget =
    result.project_master?.conversation_target ||
    (mode === "MASTER" && projectId && projectId.toUpperCase() !== "CONDUCTOR"
      ? { kind: "PROJECT_MASTER", projectId }
      : null);
  if (masterTarget?.kind === "PROJECT_MASTER" && masterTarget.projectId) {
    try {
      await callProjectMaster(masterTarget.projectId);
      toast(`Session attached → ${masterTarget.projectId} Project Master room`);
      return;
    } catch (error) {
      console.warn("Open Project Master room after inject failed", error);
    }
  }
  toast("Session injected into Universe");
}

async function activateAnchorSession(session) {
  state.selectedSupervisorAnchorKey = anchorSessionKey(session);
  renderSessionRail();
  const project = state.projects.find(
    (item) => item.project_id === session.node
  );
  if (project && session.mode === "MASTER") {
    await callProjectMaster(project.project_id, {
      anchorKey: state.selectedSupervisorAnchorKey,
    });
    expandConversationLayer();
    return;
  }
  returnToUniverseConductor();
  expandConversationLayer();
}

function sessionRailActivityLabel(room) {
  const provider = String(room.provider || "UNKNOWN").toUpperCase();
  const observedAt = String(room.last_activity_at || "").trim();
  if (!observedAt) return provider;
  const parsed = new Date(observedAt);
  if (Number.isNaN(parsed.getTime())) return provider;
  const activity = `${provider} · ${new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed)}`;
  const historyCount = Number(room.provider_history_count || 1);
  return historyCount > 1 ? `${activity} · ${historyCount} providers` : activity;
}

function supervisorSessionForRoom(room) {
  const binding = room?.binding || {};
  return (state.supervisorSessions || []).find(
    (session) =>
      session.session_id === binding.universe_session_id ||
      session.universe_session_id === binding.universe_session_id ||
      anchorSessionKey(session) === binding.anchor_key
  );
}

function sessionRailProjectIdentity(room) {
  const binding = room?.binding || {};
  const anchored = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state);
  const boundProject = String(
    binding.current_project_id || binding.node || ""
  ).trim();
  if (anchored && boundProject) {
    const project = (state.projects || []).find(
      (item) => String(item.project_id).toLowerCase() === boundProject.toLowerCase()
    );
    return {
      key: `project:${boundProject.toLowerCase()}`,
      projectId: boundProject,
      label: project ? projectDisplayName(project) : boundProject,
      registered: Boolean(project),
    };
  }

  const origin = String(room?.workspace_name || "Unassigned").trim();
  const originKey = origin.toLowerCase();
  const project = (state.projects || []).find((item) => {
    const projectId = String(item.project_id || "").toLowerCase();
    const label = String(projectDisplayName(item) || "").toLowerCase();
    const rootName = String(item.project_root || "")
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .at(-1)
      ?.toLowerCase();
    return [projectId, label, rootName].filter(Boolean).includes(originKey);
  });
  if (project) {
    return {
      key: `project:${String(project.project_id).toLowerCase()}`,
      projectId: project.project_id,
      label: projectDisplayName(project),
      registered: true,
    };
  }
  return {
    key: "unassigned",
    projectId: null,
    label: "Unassigned sessions",
    registered: false,
  };
}

function providerChatAnchorLabel(room) {
  const binding = room?.binding || {};
  const anchored = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state);
  if (!anchored) return "Not attached";
  const anchor = String(binding.current_anchor_ref || "UNKNOWN");
  if (anchor === "UNKNOWN") return "Anchor pending";
  return anchor;
}

function renderProviderChatSummary() {
  if (!elements.sessionSummaryDialog) return;
  const room = (state.providerChatRooms || []).find(
    (item) => item.chat_key === state.selectedProviderChatKey
  );
  if (!room) {
    if (elements.sessionSummaryDialog.open) elements.sessionSummaryDialog.close();
    return;
  }
  const binding = room.binding || { state: "UNBOUND" };
  const project = sessionRailProjectIdentity(room);
  const boundSession = supervisorSessionForRoom(room);
  const temporality = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state)
    ? binding.observer_currentness !== "CURRENT"
      ? "Past"
      : "Current"
    : "Unbound";
  elements.sessionSummaryTitle.textContent =
    binding.alias || room.display_name || "Session";
  elements.sessionSummarySubtitle.textContent = `${project.label} · ${temporality}`;
  elements.sessionSummaryFacts.replaceChildren();
  const facts = [
    ["Project", project.label],
    ["Position", temporality],
    ["Mode", binding.mode || "Unassigned"],
    ["Provider", String(room.provider || "UNKNOWN").toUpperCase()],
    ["Activity", String(room.activity_state || "UNKNOWN").toUpperCase()],
    ["Last activity", formatSessionTime(room.last_activity_at, { withSeconds: false, relative: true })],
    ["Anchor", providerChatAnchorLabel(room)],
    ["Opened from", room.workspace_name || "Unknown"],
  ];
  const historyCount = Number(room.provider_history_count || 1);
  if (historyCount > 1) facts.push(["Provider history", String(historyCount)]);
  for (const [label, value] of facts) {
    const fact = node("div", "session-summary-fact");
    fact.append(node("span", "", label), node("strong", "", value));
    elements.sessionSummaryFacts.append(fact);
  }
  elements.sessionSummaryOpen.disabled = !boundSession;
  elements.sessionSummaryOpen.textContent = boundSession
    ? binding.mode === "MASTER"
      ? "Open Master"
      : "Open session"
    : "Not attached";
  elements.sessionSummaryManage.textContent = boundSession
    ? "View in Observatory"
    : "Register session";
}

function openProviderChatSummary(room) {
  state.selectedProviderChatKey = room.chat_key;
  renderSessionRail();
  renderProviderChatSummary();
  if (!elements.sessionSummaryDialog.open) {
    elements.sessionSummaryDialog.showModal();
  }
}

function renderSessionRail() {
  if (!elements.sessionRailList) return;
  const query = String(state.providerChatSearch || "").trim().toLowerCase();
  const rooms = (state.providerChatRooms || []).filter((room) => {
    if (!state.providerChatShowWorkers && room.session_kind === "WORKER") {
      return false;
    }
    const visibility = room.binding?.visibility || "VISIBLE";
    if (state.providerChatShowHidden !== (visibility === "HIDDEN")) return false;
    if (!query) return true;
    const binding = room.binding || {};
    return [
      room.provider,
      room.workspace_name,
      room.display_name,
      binding.current_project_id,
      binding.node,
      binding.mode,
      binding.alias,
    ].some((value) => String(value || "").toLowerCase().includes(query));
  });
  elements.sessionRailList.replaceChildren();
  if (!rooms.length) {
    elements.sessionRailList.append(
      node("p", "session-rail-empty", "No provider chats found")
    );
    return;
  }

  const projectGroups = new Map();
  for (const room of rooms) {
    const binding = room.binding || { state: "UNBOUND" };
    const project = sessionRailProjectIdentity(room);
    if (!projectGroups.has(project.key)) {
      projectGroups.set(project.key, {
        ...project,
        current: [],
        past: [],
        unbound: [],
      });
    }
    const group = projectGroups.get(project.key);
    if (!["BOUND", "ANCHOR_OBSERVED"].includes(binding.state)) {
      group.unbound.push(room);
    } else if (binding.observer_currentness === "CURRENT") {
      group.current.push(room);
    } else {
      group.past.push(room);
    }
  }

  const appendRoom = (target, room) => {
    const binding = room.binding || { state: "UNBOUND" };
    const isAnchored = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state);
    const boundSession = supervisorSessionForRoom(room);
    const row = node("div", "session-rail-row");
    const item = node("button", "session-rail-item provider-chat-item");
    item.type = "button";
    item.dataset.bound = String(isAnchored);
    item.dataset.kind = room.session_kind || "CHAT";
    item.classList.toggle(
      "selected",
      Boolean(
        room.chat_key === state.selectedProviderChatKey ||
          (boundSession &&
            anchorSessionKey(boundSession) === state.selectedSupervisorAnchorKey)
      )
    );
    const activityState = String(room.activity_state || "UNKNOWN").toUpperCase();
    item.dataset.state = activityState;
    const copy = node("span", "session-rail-copy");
    const anchorLabel =
      isAnchored && binding.current_anchor_ref !== "UNKNOWN"
        ? `${binding.observer_currentness === "CURRENT" ? "" : "Past · "}${binding.current_anchor_ref}`
        : isAnchored
          ? binding.alias || `${binding.node} ${binding.mode}`
          : `${room.provider} origin`;
    const roomLabel =
      binding.alias || room.display_name || "Untitled session";
    copy.append(
      node("strong", "", roomLabel),
      node("small", "", `${sessionRailActivityLabel(room)} · ${anchorLabel}`)
    );
    const status = node(
      "span",
      "session-rail-status",
      room.session_kind === "WORKER" ? "WORKER" : activityState
    );
    status.dataset.state = activityState;
    item.append(node("i", "session-rail-dot"), copy, status);
    item.title = `${room.provider} | ${anchorLabel}`;
    item.addEventListener("click", () => openProviderChatSummary(room));
    row.append(item);
    if (binding.state === "BOUND" && binding.universe_session_id) {
      const visibility = binding.visibility || "VISIBLE";
      const toggle = node(
        "button",
        "session-rail-visibility",
        visibility === "HIDDEN" ? "Show" : "Hide"
      );
      toggle.type = "button";
      toggle.title = visibility === "HIDDEN" ? "Show session" : "Hide session";
      toggle.addEventListener("click", async () => {
        try {
          await api(
            `/v1/sessions/${encodeURIComponent(binding.universe_session_id)}/visibility`,
            {
              method: "POST",
              body: {
                visibility: visibility === "HIDDEN" ? "VISIBLE" : "HIDDEN",
                expected_version: binding.row_version,
              },
            }
          );
          await refreshSupervisorSessions();
        } catch (error) {
          toast(error.message, true);
        }
      });
      row.append(toggle);
    }
    target.append(row);
  };

  const projectOrder = new Map(
    (state.projects || []).map((project, index) => [
      String(project.project_id).toLowerCase(),
      index,
    ])
  );
  const groups = [...projectGroups.values()].sort((left, right) => {
    const leftOrder = projectOrder.get(String(left.projectId || "").toLowerCase());
    const rightOrder = projectOrder.get(String(right.projectId || "").toLowerCase());
    if (leftOrder !== undefined || rightOrder !== undefined) {
      return (leftOrder ?? Number.MAX_SAFE_INTEGER) - (rightOrder ?? Number.MAX_SAFE_INTEGER);
    }
    return left.label.localeCompare(right.label);
  });
  for (const group of groups) {
    const total = group.current.length + group.past.length + group.unbound.length;
    const hasSelected = [...group.current, ...group.past, ...group.unbound].some(
      (room) =>
        room.chat_key === state.selectedProviderChatKey ||
        supervisorSessionForRoom(room) &&
          anchorSessionKey(supervisorSessionForRoom(room)) ===
            state.selectedSupervisorAnchorKey
    );
    const hasExplicitState = Object.hasOwn(
      state.providerChatExpandedProjects,
      group.key
    );
    const expanded = query
      ? true
      : hasExplicitState
        ? Boolean(state.providerChatExpandedProjects[group.key])
        : hasSelected;
    const tree = node("section", "session-project-tree");
    tree.dataset.expanded = String(expanded);
    const toggle = node("button", "session-project-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", String(expanded));
    const chevron = node("span", "session-tree-chevron");
    chevron.setAttribute("aria-hidden", "true");
    const title = node("span", "session-project-copy");
    title.append(
      node("strong", "", group.label),
      node(
        "small",
        "",
        `${group.current.length} current · ${group.past.length} past · ${group.unbound.length} unbound`
      )
    );
    toggle.append(chevron, title, node("span", "session-project-count", String(total)));
    toggle.addEventListener("click", () => {
      state.providerChatExpandedProjects[group.key] = !expanded;
      renderSessionRail();
    });
    tree.append(toggle);
    const body = node("div", "session-project-body");
    body.hidden = !expanded;
    const appendBranch = (label, branchRooms) => {
      if (!branchRooms.length) return;
      const branch = node("section", "session-project-branch");
      const branchKey = `${group.key}:${label.toLowerCase()}`;
      const branchHasExplicitState = Object.hasOwn(
        state.providerChatExpandedBranches,
        branchKey
      );
      const branchExpanded = query
        ? true
        : branchHasExplicitState
          ? Boolean(state.providerChatExpandedBranches[branchKey])
          : label === "Current";
      branch.dataset.expanded = String(branchExpanded);
      const branchToggle = node("button", "session-project-branch-toggle");
      branchToggle.type = "button";
      branchToggle.setAttribute("aria-expanded", String(branchExpanded));
      const branchChevron = node("span", "session-tree-chevron");
      branchChevron.setAttribute("aria-hidden", "true");
      branchToggle.append(
        branchChevron,
        node("span", "", label),
        node("span", "session-project-count", String(branchRooms.length))
      );
      branchToggle.addEventListener("click", () => {
        state.providerChatExpandedBranches[branchKey] = !branchExpanded;
        renderSessionRail();
      });
      branch.append(branchToggle);
      const lines = node("div", "session-project-branch-lines");
      lines.hidden = !branchExpanded;
      branchRooms.forEach((room) => appendRoom(lines, room));
      branch.append(lines);
      body.append(branch);
    };
    appendBranch("Current", group.current);
    appendBranch("Past", group.past);
    appendBranch("Unbound", group.unbound);
    tree.append(body);
    elements.sessionRailList.append(tree);
  }
}

function renderSessionObservatory() {
  if (!elements.sessionObservatoryList) return;
  const allSessions = state.supervisorSessions || [];
  const groups = observatorySessionGroups(allSessions);
  const sessions = observatoryVisibleSessions(allSessions);
  const live = allSessions.filter((item) => item.state === "LIVE").length;
  const unknown = allSessions.filter((item) => item.state === "UNKNOWN").length;
  const altCount = groups.reduce((n, g) => n + g.alternatives.length, 0);
  const collapsedHint = state.observatoryShowAll
    ? ""
    : altCount
      ? ` · ${groups.length} slots · ${altCount} older hidden`
      : ` · ${groups.length} slots`;
  elements.sessionObservatorySummary.textContent =
    `${sessions.length} shown / ${allSessions.length} total · ${live} live · ${unknown} unknown` +
    collapsedHint +
    ` · ${state.runtimeAudit?.platform_approvals?.pending_count || 0} approvals`;
  if (elements.observatoryShowAllToggle) {
    elements.observatoryShowAllToggle.checked = Boolean(state.observatoryShowAll);
  }
  elements.sessionObservatoryList.replaceChildren();
  if (!sessions.length) {
    elements.sessionObservatoryList.append(
      node(
        "p",
        "empty-copy",
        state.observatoryShowAll
          ? "No persistent Mode session in Supervisor yet. Use “Register this session” above."
          : "No recent/live sessions in the filtered list. Turn on “Show all” or inject a session."
      )
    );
  }

  const groupBySessionId = new Map();
  for (const group of groups) {
    for (const member of group.members) {
      groupBySessionId.set(member.session_id, group);
    }
  }

  for (const session of sessions) {
    const group = groupBySessionId.get(session.session_id);
    const isPrimary =
      group && group.primary && group.primary.session_id === session.session_id;
    const card = node("article", "supervisor-session-card");
    card.dataset.default = String(Boolean(session.is_default));
    card.dataset.selected = String(
      anchorSessionKey(session) === state.selectedSupervisorAnchorKey
    );
    card.dataset.primary = String(Boolean(isPrimary));
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.title = "Select to show last activity and recent turns";
    card.addEventListener("click", (event) => {
      if (event.target.closest("button, input, textarea, select, a")) return;
      selectSupervisorSession(session);
    });
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectSupervisorSession(session);
      }
    });
    const heading = node("div", "session-card-heading");
    heading.append(
      node("strong", "", sessionDisplayName(session)),
      node("span", "session-token-pill", currentAnchorLabel(session)),
      node("span", "session-state-pill", sessionStateLabel(session))
    );
    heading.lastElementChild.dataset.state = session.state || "UNKNOWN";
    if (isPrimary && !state.observatoryShowAll) {
      heading.append(node("span", "session-token-pill active", "ACTIVE"));
    } else if (!isPrimary && group) {
      heading.append(node("span", "session-token-pill alt", "OTHER"));
    }
    const meta = node("div", "session-card-meta");
    meta.append(
      node("span", "", sessionCoordinateLabel(session)),
      node("span", "", session.provider || "UNKNOWN"),
      node("span", "", session.is_default ? "DEFAULT" : "ALTERNATIVE"),
      node(
        "span",
        "session-activity-time",
        formatSessionTime(session.last_activity_at || session.updated_at, {
          withSeconds: true,
          relative: true,
        })
      )
    );
    const path = node(
      "p",
      session.project_bound ||
        (state.projects || []).some((item) => item.project_id === session.node)
        ? "session-path-line bound"
        : "session-path-line unbound",
      sessionProjectPathLabel(session)
    );
    const snippetText = sessionPreviewSnippet(session);
    const snippet = node(
      "p",
      "session-preview-snippet",
      snippetText ||
        "No chat tied to this session yet (other sessions' room history is not shared)"
    );
    const ref = node("p", "session-ref-line", currentAnchorLabel(session));
    const alias = document.createElement("input");
    alias.className = "session-alias-input";
    alias.value = session.alias || "";
    alias.maxLength = 120;
    alias.setAttribute("aria-label", `Alias for ${sessionDisplayName(session)}`);
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
        // Collapse group after choosing so the list stays one-active-per-slot.
        if (group) {
          state.observatoryExpandedCoords = {
            ...state.observatoryExpandedCoords,
            [group.key]: false,
          };
        }
        const project = state.projects.find(
          (item) => item.project_id === session.node
        );
        state.selectedSupervisorAnchorKey = anchorSessionKey(session);
        elements.sessionObservatoryDialog.close();
        if (project && session.mode === "MASTER") {
          await callProjectMaster(project.project_id, {
            anchorKey: state.selectedSupervisorAnchorKey,
          });
        } else {
          returnToUniverseConductor();
        }
        await refreshSupervisorSessions();
      } catch (error) {
        toast(error.message, true);
      }
    });
    actions.append(saveAlias, resume);
    if (
      group &&
      isPrimary &&
      group.alternatives.length > 0 &&
      !state.observatoryShowAll
    ) {
      const more = node(
        "button",
        "secondary-button compact-action",
        group.expanded
          ? "Hide older sessions"
          : `Other sessions (${group.alternatives.length})`
      );
      more.type = "button";
      more.title =
        "Same node/mode can have several 1:1 threads. Show older ones to switch.";
      more.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleObservatoryCoordExpand(group.key);
      });
      actions.append(more);
    } else if (group && group.expanded && !isPrimary && !state.observatoryShowAll) {
      const hide = node(
        "button",
        "secondary-button compact-action",
        "Back to active"
      );
      hide.type = "button";
      hide.addEventListener("click", (event) => {
        event.stopPropagation();
        toggleObservatoryCoordExpand(group.key);
      });
      actions.append(hide);
    }
    card.append(heading, meta, path, snippet, ref, alias, actions);
    elements.sessionObservatoryList.append(card);
  }
  renderSelectedSessionDetail();
  renderSessionRail();

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
      node("span", "", "Supervisor"),
      node("time", "", event.occurred_at || "")
    );
    elements.sessionEventList.append(row);
  }
  if (!(state.supervisorEvents || []).length) {
    elements.sessionEventList.append(
      node("p", "empty-copy", "No Supervisor event has been recorded.")
    );
  }
  renderRuntimeAudit();

  if (elements.roomSessionBindingList) {
    elements.roomSessionBindingList.replaceChildren();
    const bindings = state.roomSessionBindings || [];
    if (!bindings.length) {
      elements.roomSessionBindingList.append(
        node("p", "empty-copy", "No active multi-room session attachments.")
      );
    }
    for (const binding of bindings) {
      const card = node("article", "supervisor-session-card");
      const heading = node("div", "session-card-heading");
      heading.append(
        node(
          "strong",
          "",
          `${binding.room_type || "ROOM"} · ${binding.slot_role || "?"}`
        ),
        node("span", "session-state-pill", binding.state || "ACTIVE")
      );
      const meta = node("div", "session-card-meta");
      meta.append(
        node(
          "span",
          "",
          binding.room_title || binding.room_id || "room"
        ),
        node("span", "", binding.provider || "UNKNOWN"),
        node(
          "span",
          "",
          binding.room_project_id || binding.project_id || "—"
        )
      );
      const ref = node(
        "p",
        "session-ref-line",
        binding.anchor_ref || "Anchor Session transport attached"
      );
      card.append(heading, meta, ref);
      elements.roomSessionBindingList.append(card);
    }
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

async function callProjectMaster(projectId, options = {}) {
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
  if (options.anchorKey) {
    state.selectedSupervisorAnchorKey = options.anchorKey;
  }
  openProjectRoomStream(projectId);
  try {
    await refreshSupervisorSessions();
  } catch (error) {
    console.warn("Anchor Session refresh after Project Master prepare failed", error);
  }
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

/** Graph canvas modes only (not inspector tabs). */
function showGraphView(view) {
  const allowed = new Set(["universe", "timeline", "documents", "implementation"]);
  if (!allowed.has(view)) view = "universe";
  state.view = view;
  state.selectedNode = null;
  state.focusedNodeId = null;
  elements.nodeBreadcrumb?.classList.add("hidden");
  syncPrimaryNavSelection(
    view === "universe" ? "map" : view
  );
  buildGraph();
  renderDetails();
}

/** Highlight top nav without toast placeholders. */
function syncPrimaryNavSelection(primaryView) {
  if (!elements.primaryNav) return;
  for (const item of elements.primaryNav.querySelectorAll("[data-primary-view]")) {
    item.classList.toggle(
      "selected",
      item.getAttribute("data-primary-view") === primaryView
    );
  }
}

/** Open inspector tab (Memory / Future / Bench / Activity / Details). */
function openInspectorSurface(tab) {
  const allowed = new Set(["details", "activity", "bench", "memory", "future"]);
  if (!allowed.has(tab)) tab = "details";
  state.inspectorDismissed = false;
  document.body.classList.add("inspector-open");
  showInspectorTab(tab);
  if (["memory", "future", "bench"].includes(tab)) {
    syncPrimaryNavSelection(tab);
  }
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
    // Multiverse map can show Universe hub with zero projects selected.
    elements.graphHint.classList.toggle("hidden", !state.graph.nodes.length);
  }
  if (elements.graphEmpty) {
    // Multiverse hub alone is still a valid map — don't cover it forever.
    if (state.view === "universe" && state.graph.nodes.length) {
      elements.graphEmpty.classList.add("hidden");
    }
  }
}

async function refresh({ syncSelectedProject = false } = {}) {
  try {
    const healthResponse = await fetch("/health", { cache: "no-store" });
    const health = await healthResponse.json();
    state.accessSurface =
      healthResponse.headers.get("X-Universe-Access-Surface") || "LOCAL_BROWSER";
    state.health = health;
    elements.serviceStatus.dataset.state = health.status === "READY" ? "ready" : "error";
    elements.serviceStatus.textContent =
      health.status === "READY"
        ? state.accessSurface === "REMOTE_BROWSER"
          ? "Paired mobile"
          : "Local service"
        : health.status;
    state.modeContract = health.mode_contract || null;
    renderModeStatus();
    const listLink = document.querySelector("#universe-list-link");
    if (listLink) {
      // Always offer public list; remote users need a way back to other universes.
      listLink.href = "/join";
      listLink.hidden = false;
      listLink.textContent =
        state.accessSurface === "REMOTE_BROWSER" ? "목록 · 다른 유니버스" : "목록";
    }

    const [
      projectResult,
      todoResult,
      releaseResult,
      conductorRoomResult,
      governanceProposalInboxResult,
      providerSettings,
      hostTools,
    ] =
      await Promise.all([
      api("/v1/projects"),
      api("/v1/todos"),
      api("/v1/releases"),
      api("/v1/conductor-room/messages"),
      api("/v1/governance-proposals"),
      api("/v1/settings/providers"),
      api("/v1/settings/host-tools"),
    ]);
    state.projects = projectResult.projects;
    state.todos = todoResult.todos;
    state.releases = releaseResult.releases;
    state.conductorMessages = conductorRoomResult.messages || [];
    state.conductorPermissions = conductorRoomResult.permissions || [];
    state.conductorRuntimeBinding =
      conductorRoomResult.runtime_binding || null;
    state.governanceProposalInbox =
      governanceProposalInboxResult.proposals || [];
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
    // Multiverse keeps every project tree expanded — load all projections first.
    await loadAllProjectProjections();
    renderProjects();
    renderComposerActions();
    renderReleaseCatalog();
    const preferred =
      state.selectedProject &&
      state.projects.find(
        (project) => project.project_id === state.selectedProject.project_id
      );
    if (preferred) {
      await selectProject(preferred.project_id, {
        revealInspector: !state.inspectorDismissed,
        syncAssets: syncSelectedProject,
      });
    } else if (state.projects.length) {
      await selectProject(state.projects[0].project_id, {
        revealInspector: !window.matchMedia("(max-width: 720px)").matches,
        syncAssets: syncSelectedProject,
      });
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

function projectDisplayName(project) {
  const meta = project?.metadata || {};
  return (
    meta.display_name ||
    meta.label ||
    project?.project_id ||
    "project"
  );
}

function projectSortKey(project) {
  const role = String(project?.metadata?.network_role || "");
  if (role === "UNIVERSE_HOME") return "0";
  if (role === "CAREER_SOURCE") return "1";
  return `2:${project?.project_id || ""}`;
}

function renderProjects() {
  elements.projectList.replaceChildren();
  const projects = (state.projects || [])
    .slice()
    .sort((a, b) => projectSortKey(a).localeCompare(projectSortKey(b)));
  for (const project of projects) {
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
    if (project.metadata?.network_role) {
      button.dataset.networkRole = project.metadata.network_role;
    }
    const label = projectDisplayName(project);
    const avatar = node(
      "span",
      "project-avatar",
      String(label).slice(0, 2)
    );
    const copy = node("span", "project-copy");
    const openTodoCount = state.todos.filter(
      (todo) =>
        todoBelongsToProject(todo, project.project_id) && todo.state !== "DONE"
    ).length;
    const pendingApprovalCount = state.governanceProposalInbox.filter(
      (proposal) =>
        proposal.project_id === project.project_id &&
        proposal.state === "PROPOSED"
    ).length;
    const roleTag =
      project.metadata?.network_role === "UNIVERSE_HOME"
        ? "home"
        : project.metadata?.network_role === "CAREER_SOURCE"
          ? "career"
          : "";
    copy.append(
      node("span", "project-name", label),
      node(
        "span",
        "project-meta",
        `${roleTag ? `${roleTag} · ` : ""}${
          project.metadata.label || project.project_id
        }${openTodoCount ? ` / ${openTodoCount} open` : ""}${
          pendingApprovalCount ? ` / ${pendingApprovalCount} approval` : ""
        }`
      )
    );
    button.append(avatar, copy);
    if (pendingApprovalCount) {
      button.append(
        node(
          "span",
          "project-approval-badge",
          pendingApprovalCount > 99 ? "99+" : String(pendingApprovalCount)
        )
      );
      button.title = `${pendingApprovalCount} governance Proposal approval required`;
    }
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

async function selectProject(
  projectId,
  { revealInspector = true, syncAssets = false } = {}
) {
  const project = state.projects.find((item) => item.project_id === projectId);
  if (!project) return;
  state.selectedProject = project;
  state.selectedNode = null;
  state.focusedNodeId = null;
  state.inspectorDismissed = !revealInspector;
  renderProjects();
  if (syncAssets) {
    await api(`/v1/projects/${encodeURIComponent(projectId)}/sync`, {
      method: "POST",
      body: {},
    }).catch((error) => toast(error.message, true));
  }
  const [
    projectionResult,
    dispatchResult,
    proposalResult,
    roomResult,
    bridgeResult,
    permissionResult,
    governanceProposalResult,
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
    api(`/v1/projects/${encodeURIComponent(projectId)}/governance-proposals`).catch(
      () => ({ proposals: [] })
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
    api("/v1/bench/compare?group_by=worker&limit=20").catch(() => ({
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
  if (state.projection) {
    state.projectionsByProject = {
      ...state.projectionsByProject,
      [projectId]: state.projection,
    };
  }
  state.dispatches = await Promise.all(
    dispatchResult.dispatches.map((item) =>
      api(
        `/v1/dispatches/${encodeURIComponent(item.dispatch.dispatch_id)}`
      ).catch(() => item)
    )
  );
  state.releaseProposals = proposalResult.proposals;
  state.roomMessages = dedupeRoomMessages(roomResult.messages);
  state.masterBridge = bridgeResult.bridge || null;
  state.projectPermissions = permissionResult.permissions || [];
  state.governanceProposals = governanceProposalResult.proposals || [];
  mergeGovernanceProposalInbox(projectId, state.governanceProposals);
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
  // Always pin board filter to the selected project so foreign PROJECT todos
  // (e.g. GCS worklist) never remain visible under "All scopes".
  if (elements.todoScopeFilter) {
    elements.todoScopeFilter.value = "PROJECT";
  }
  renderTodos();
}

function mergeGovernanceProposalInbox(projectId, proposals) {
  state.governanceProposalInbox = [
    ...(proposals || []),
    ...state.governanceProposalInbox.filter(
      (item) => item.project_id !== projectId
    ),
  ];
}


function conversationMessageCount() {
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    return (
      (state.conductorMessages || []).length +
      (state.conductorPermissions || []).filter(
        (item) => item.state === "PENDING"
      ).length +
      (state.governanceProposalInbox || []).filter(
        (item) => item.state === "PROPOSED"
      ).length
    );
  }
  return (
    (state.roomMessages || []).length +
    (state.projectPermissions || []).filter((item) => item.state === "PENDING").length +
    (state.governanceProposals || []).filter(
      (item) => item.state === "PROPOSED"
    ).length
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

function scrollRoomToPendingAction() {
  const pending = elements.roomMessageList.querySelector(
    ".governance-proposal-request, .permission-request"
  );
  if (!pending) {
    elements.roomMessageList.scrollTop = elements.roomMessageList.scrollHeight;
    return;
  }
  const listBox = elements.roomMessageList.getBoundingClientRect();
  const pendingBox = pending.getBoundingClientRect();
  elements.roomMessageList.scrollTop += pendingBox.top - listBox.top;
}

function renderRoomMessages() {
  elements.roomMessageList.replaceChildren();
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    const pendingPermissions = state.conductorPermissions.filter(
      (item) => item.state === "PENDING"
    );
    const pendingProposals = state.governanceProposalInbox.filter(
      (item) => item.state === "PROPOSED"
    );
    if (
      !state.conductorMessages.length &&
      !pendingPermissions.length &&
      !pendingProposals.length
    ) {
      const item = node("article", "room-message conductor-message");
      item.append(
        node("strong", "", "UNIVERSE / CONDUCTOR"),
        node("p", "", "Universe control room is active."),
        node("small", "", "Send a message here or use + to call a Project Master.")
      );
      elements.roomMessageList.append(item);
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
    for (const stream of Object.values(state.conductorStreamReplies)) {
      const item = node("article", "room-message conductor-message streaming");
      item.append(
        node("strong", "", "CONDUCTOR / LIVE"),
        node("p", "", stream.body || stream.state || "Thinking"),
        node("small", "", stream.state || "Responding")
      );
      elements.roomMessageList.append(item);
    }
    for (const proposal of pendingProposals) {
      elements.roomMessageList.append(renderGovernanceProposalCard(proposal));
    }
    for (const permission of pendingPermissions) {
      elements.roomMessageList.append(renderPermissionCard(permission));
    }
    scrollRoomToPendingAction();
    updateConversationBadge();
    return;
  }
  const pendingProposals = state.governanceProposals.filter(
    (item) => item.state === "PROPOSED"
  );
  if (
    !state.roomMessages.length &&
    !pendingProposals.length &&
    !state.projectPermissions.some((item) => item.state === "PENDING")
  ) {
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
  for (const proposal of pendingProposals) {
    elements.roomMessageList.append(renderGovernanceProposalCard(proposal));
  }
  for (const permission of state.projectPermissions.filter(
    (item) => item.state === "PENDING"
  )) {
    elements.roomMessageList.append(renderPermissionCard(permission));
  }
  scrollRoomToPendingAction();
  updateConversationBadge();
}

function renderGovernanceProposalCard(proposal) {
  const item = node("article", "room-message governance-proposal-request");
  const summary = proposal.task_summary || "Project task proposal";
  const digest = String(proposal.proposal_digest || "UNKNOWN");
  const scope = proposal.scope && Object.keys(proposal.scope).length
    ? JSON.stringify(proposal.scope)
    : proposal.boundary || "Project scope";
  item.append(
    node(
      "strong",
      "",
      `GOVERNANCE / ${proposal.project_id} / APPROVAL REQUIRED`
    ),
    node("p", "proposal-title", summary),
    node("small", "proposal-boundary", proposal.boundary || "Scope recorded"),
    node("small", "proposal-coordinate", `ID ${proposal.proposal_id}`),
    node("small", "proposal-coordinate", `Digest ${digest.slice(0, 16)}...`),
    node("small", "proposal-scope", scope)
  );
  const actions = node("div", "proposal-actions");
  const approve = node("button", "proposal-approve", "Approve");
  approve.type = "button";
  approve.title = `Approve ${proposal.proposal_id}`;
  approve.addEventListener("click", () =>
    decideGovernanceProposal(proposal, "BUTTON")
  );
  actions.append(approve);
  item.append(actions);
  return item;
}

async function decideGovernanceProposal(proposal, source) {
  if (!state.projects.some((project) => project.project_id === proposal.project_id)) {
    toast("Proposal project is no longer attached", true);
    return false;
  }
  try {
    const result = await api(
      `/v1/governance/proposals/${encodeURIComponent(
        proposal.proposal_id
      )}/decision`,
      {
        method: "POST",
        body: {
          decision: "APPROVE",
          proposal_digest: proposal.proposal_digest,
        },
      }
    );
    state.governanceProposals = [
      ...(state.selectedProject?.project_id === result.proposal.project_id
        ? [result.proposal]
        : []),
      ...state.governanceProposals.filter(
        (item) => item.proposal_id !== result.proposal.proposal_id
      ),
    ];
    mergeGovernanceProposalInbox(result.proposal.project_id, [
      result.proposal,
      ...state.governanceProposalInbox.filter(
        (item) =>
          item.project_id === result.proposal.project_id &&
          item.proposal_id !== result.proposal.proposal_id
      ),
    ]);
    if (
      result.message &&
      state.selectedProject?.project_id === result.proposal.project_id
    ) {
      state.roomMessages = dedupeRoomMessages([
        ...state.roomMessages.filter(
          (message) => message.message_id !== result.message.message_id
        ),
        result.message,
      ]);
    }
    renderProjects();
    renderRoomMessages();
    toast("Governance Proposal approved and delivered to Project Master");
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

function requestedPermissionSummary(value) {
  if (!value || typeof value !== "object") return "";
  const parts = [];
  if (value.network?.enabled === true) {
    parts.push("Network access");
  }
  const fileSystem = value.fileSystem;
  if (fileSystem && typeof fileSystem === "object") {
    for (const entry of fileSystem.entries || []) {
      const pathValue = entry?.path || {};
      const label =
        pathValue.path ||
        pathValue.pattern ||
        pathValue.value?.subpath ||
        pathValue.value?.kind ||
        "filesystem";
      parts.push(`${entry?.access || "access"}: ${label}`);
    }
    for (const path of fileSystem.read || []) {
      parts.push(`read: ${path}`);
    }
    for (const path of fileSystem.write || []) {
      parts.push(`write: ${path}`);
    }
  }
  return parts.join(" · ") || "Additional permissions requested";
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
  if (toolCall.requestedPermissions) {
    item.append(
      node(
        "small",
        "permission-scope",
        requestedPermissionSummary(toolCall.requestedPermissions)
      )
    );
  }
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
    const isConductor = permission.scope_kind === "UNIVERSE_CONDUCTOR";
    const endpoint = isConductor
      ? `/v1/conductor-room/agent-session/permissions/${encodeURIComponent(
          permission.request_id
        )}/decision`
      : `/v1/projects/${encodeURIComponent(
          permission.project_id
        )}/agent-session/permissions/${encodeURIComponent(
          permission.request_id
        )}/decision`;
    const result = await api(endpoint, {
      method: "POST",
      body: { option_id: optionId },
    });
    const collection = isConductor
      ? state.conductorPermissions
      : state.projectPermissions;
    const updated = collection.map((item) =>
      item.request_id === permission.request_id ? result.permission : item
    );
    if (isConductor) {
      state.conductorPermissions = updated;
    } else {
      state.projectPermissions = updated;
    }
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

function providerCatalogModels(provider) {
  const key = String(provider || "").toUpperCase();
  if (!key || key === "AUTO") return [];
  const entry = state.providerModels?.providers?.[key];
  return Array.isArray(entry?.models) ? entry.models : [];
}

function fillWorkerBindingModelSelect(select, provider, selectedValue) {
  select.replaceChildren();
  const hostDefault = node("option", "", "Host default");
  hostDefault.value = "";
  select.append(hostDefault);
  const models = providerCatalogModels(provider);
  const selected = String(selectedValue || "");
  for (const modelId of models) {
    const option = node("option", "", modelId);
    option.value = modelId;
    select.append(option);
  }
  if (selected && !models.includes(selected)) {
    const option = node("option", "", `${selected} (current)`);
    option.value = selected;
    select.append(option);
  }
  select.value = selected && (models.includes(selected) || selected)
    ? selected
    : "";
}

function renderWorkerBindingSettings() {
  if (!elements.workerBindingScope || !elements.workerBindingSettings) return;
  const previousScope = elements.workerBindingScope.value || "UNIVERSE:UNIVERSE";
  elements.workerBindingScope.replaceChildren();
  const scopes = [
    { value: "UNIVERSE:UNIVERSE", label: "Universe defaults" },
    ...state.projects.map((project) => ({
      value: `PROJECT:${project.project_id}`,
      label: `${project.project_id} project`,
    })),
  ];
  for (const scope of scopes) {
    const option = node("option", "", scope.label);
    option.value = scope.value;
    elements.workerBindingScope.append(option);
  }
  elements.workerBindingScope.value = scopes.some(
    (scope) => scope.value === previousScope
  )
    ? previousScope
    : "UNIVERSE:UNIVERSE";
  const [scopeKind, scopeId] = elements.workerBindingScope.value.split(":", 2);
  const profiles = state.workerBindings?.profiles || [];
  elements.workerBindingSettings.replaceChildren();
  for (const role of ["IMPLEMENTER", "REVIEWER", "QA", "SCOUT", "ROUTINE"]) {
    const profile = profiles.find(
      (item) =>
        item.scope_kind === scopeKind &&
        item.scope_id === scopeId &&
        item.worker_role === role &&
        item.task_type === "*"
    );
    const row = node("div", "worker-binding-row");
    row.dataset.role = role;
    const copy = node("div", "worker-binding-copy");
    copy.append(
      node("strong", "", role[0] + role.slice(1).toLowerCase()),
      node(
        "small",
        "",
        profile ? `Revision ${profile.revision}` : "Inherited AUTO"
      )
    );
    const provider = node("select", "worker-binding-provider");
    for (const value of ["AUTO", "GROK", "CODEX", "CLAUDE"]) {
      const option = node("option", "", value[0] + value.slice(1).toLowerCase());
      option.value = value;
      provider.append(option);
    }
    provider.value = profile?.provider || "AUTO";
    const model = node("select", "worker-binding-model");
    fillWorkerBindingModelSelect(
      model,
      provider.value,
      profile?.model_ref || ""
    );
    provider.addEventListener("change", () => {
      fillWorkerBindingModelSelect(model, provider.value, model.value);
    });
    const modelCustom = node("input", "worker-binding-model-custom");
    modelCustom.type = "text";
    modelCustom.placeholder = "custom model id (optional)";
    modelCustom.autocomplete = "off";
    modelCustom.spellcheck = false;
    const effort = node("select", "worker-binding-effort");
    for (const value of ["AUTO", "LOW", "MEDIUM", "HIGH", "MAX"]) {
      const option = node("option", "", value[0] + value.slice(1).toLowerCase());
      option.value = value;
      effort.append(option);
    }
    effort.value = profile?.effort || "AUTO";
    const skills = node("input", "worker-binding-skills");
    skills.type = "text";
    skills.placeholder = "skill-a, skill-b";
    skills.value = (profile?.skill_refs || []).join(", ");
    row.append(copy, provider, model, modelCustom, effort, skills);
    elements.workerBindingSettings.append(row);
  }
  renderProviderModelCatalog();
}

function renderProviderModelCatalog() {
  const root = elements.providerModelCatalog;
  if (!root) return;
  root.replaceChildren();
  const catalog = state.providerModels;
  if (!catalog?.providers) {
    root.append(
      node(
        "p",
        "empty-copy",
        "No model catalog yet — Discover host tools or Refresh models"
      )
    );
    return;
  }
  const meta = node("small", "provider-setting-status");
  meta.textContent = [
    catalog.catalog_path || "",
    catalog.discovered_at ? `discovered ${catalog.discovered_at}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  root.append(meta);
  for (const name of ["GROK", "CODEX", "CLAUDE"]) {
    const entry = catalog.providers[name] || {};
    const block = node("div", "provider-model-block");
    block.dataset.provider = name;
    const head = node("div", "settings-section-heading");
    head.append(
      node("strong", "", name),
      node(
        "small",
        "",
        `${entry.status || "UNKNOWN"} · ${entry.source || "—"} · default ${
          entry.default || "—"
        }`
      )
    );
    const list = node("div", "provider-model-chips");
    for (const modelId of entry.models || []) {
      const chip = node("span", "provider-model-chip", modelId);
      if ((entry.user_models || []).includes(modelId)) {
        chip.classList.add("is-user");
        chip.title = "User-added (kept on rediscover)";
      }
      list.append(chip);
    }
    const edit = node("div", "provider-model-edit");
    const input = node("input", "provider-model-add-input");
    input.type = "text";
    input.placeholder = `Add ${name} model id`;
    input.dataset.provider = name;
    const add = node("button", "secondary-button", "Add");
    add.type = "button";
    add.addEventListener("click", () => {
      const value = input.value.trim();
      if (!value) return;
      const providers = {
        ...(state.providerModels?.providers || {}),
      };
      const current = {
        ...(providers[name] || {}),
        user_models: [
          ...new Set([...(providers[name]?.user_models || []), value]),
        ],
      };
      providers[name] = current;
      saveProviderModelCatalog({ providers }).catch((error) =>
        toast(error.message, true)
      );
    });
    edit.append(input, add);
    if (entry.note) {
      block.append(head, node("small", "provider-setting-status", entry.note), list, edit);
    } else {
      block.append(head, list, edit);
    }
    root.append(block);
  }
}

async function saveProviderModelCatalog(body) {
  const result = await api("/v1/settings/provider-models", {
    method: "POST",
    body,
  });
  state.providerModels = result.catalog || result;
  renderProviderModelCatalog();
  renderWorkerBindingSettings();
  toast("Model catalog updated");
  return state.providerModels;
}

async function refreshProviderModels() {
  elements.settingsError.textContent = "";
  try {
    const result = await api("/v1/settings/provider-models/discover", {
      method: "POST",
      body: {},
    });
    state.providerModels = result.catalog || result;
    renderProviderModelCatalog();
    renderWorkerBindingSettings();
    toast("Provider models refreshed");
  } catch (error) {
    elements.settingsError.textContent = error.message;
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

function renderRuntimePreflight() {
  if (!elements.runtimePreflightList || !elements.runtimePreflightSummary) return;
  const preflight = state.runtimePreflight;
  elements.runtimePreflightList.replaceChildren();
  if (!preflight) {
    elements.runtimePreflightSummary.textContent = "Runtime preflight unavailable";
    return;
  }
  elements.runtimePreflightSummary.textContent =
    `${preflight.status} / ${preflight.suggestions?.length || 0} suggestions`;
  for (const provider of preflight.providers || []) {
    const row = node("div", "runtime-preflight-row");
    row.dataset.state = provider.runtime_status || "UNAVAILABLE";
    row.append(
      node("strong", "", provider.provider),
      node("span", "", provider.model || "default"),
      node(
        "small",
        "",
        `${provider.runtime_status || "UNKNOWN"} / approval ${provider.cli_auto_approve || "UNKNOWN"}`
      )
    );
    elements.runtimePreflightList.append(row);
  }
  for (const suggestion of preflight.suggestions || []) {
    elements.runtimePreflightList.append(
      node(
        "p",
        "runtime-preflight-suggestion",
        `${suggestion.target}: ${suggestion.action} (${suggestion.reason})`
      )
    );
  }
}

function renderRuntimeAudit() {
  if (!elements.runtimeAuditGrid) return;
  elements.runtimeAuditGrid.replaceChildren();
  const audit = state.runtimeAudit;
  if (!audit) {
    elements.runtimeAuditGrid.append(
      node("p", "empty-copy", "Runtime audit is not available.")
    );
    return;
  }
  const approvals = audit.platform_approvals || {};
  const approvalRows = [
    ...(approvals.conductor || []),
    ...(approvals.projects || []),
  ].slice(0, 4);
  const approvalPanel = node("article", "runtime-audit-panel");
  approvalPanel.append(
    node("strong", "", `Platform approvals (${approvals.pending_count || 0} pending)`)
  );
  for (const approval of approvalRows) {
    approvalPanel.append(
      node(
        "small",
        "",
        `${approval.provider || "UNKNOWN"} / ${approval.state || "UNKNOWN"} / ${approval.selected_option_kind || "waiting"}`
      )
    );
  }
  if (!approvalRows.length) {
    approvalPanel.append(node("small", "", "No approval events"));
  }

  const continuityPanel = node("article", "runtime-audit-panel");
  continuityPanel.append(node("strong", "", "Automatic continuity"));
  for (const item of (audit.continuity || []).slice(0, 5)) {
    continuityPanel.append(
      node(
        "small",
        "",
        `${item.project_id}: ${item.state?.last_status || "NOT_SAVED"} / ${item.state?.last_trigger || "NONE"}`
      )
    );
  }
  if (!(audit.continuity || []).length) {
    continuityPanel.append(node("small", "", "No connected project continuity state"));
  }

  const workerPanel = node("article", "runtime-audit-panel");
  workerPanel.append(node("strong", "", "Worker performance"));
  for (const row of (audit.worker_bench?.comparisons || []).slice(0, 5)) {
    const rate =
      row.success_rate == null ? "n/a" : `${Math.round(row.success_rate * 100)}%`;
    workerPanel.append(
      node(
        "small",
        "",
        `${row.label?.provider_ref || "UNKNOWN"} / ${compactModelRef(row.label?.model_ref)} / ${row.label?.worker_role || "UNKNOWN"}: ${rate} (${row.observation_count || 0})`
      )
    );
  }
  if (!(audit.worker_bench?.comparisons || []).length) {
    workerPanel.append(node("small", "", "No Worker observations"));
  }

  const preflightPanel = node("article", "runtime-audit-panel");
  preflightPanel.append(
    node("strong", "", `Preflight ${audit.preflight?.status || "UNKNOWN"}`),
    node(
      "small",
      "",
      `${audit.preflight?.providers?.filter((item) => item.runtime_status === "AVAILABLE").length || 0} providers available`
    ),
    node(
      "small",
      "",
      `${audit.preflight?.suggestions?.length || 0} configuration suggestions`
    )
  );
  elements.runtimeAuditGrid.append(
    preflightPanel,
    approvalPanel,
    continuityPanel,
    workerPanel
  );
}

async function discoverHostTools() {
  elements.settingsError.textContent = "";
  elements.discoverHostTools.disabled = true;
  try {
    state.hostTools = await api("/v1/settings/host-tools/discover", {
      method: "POST",
      body: {},
    });
    if (state.hostTools?.provider_models) {
      state.providerModels = state.hostTools.provider_models;
    } else {
      const models = await api("/v1/settings/provider-models").catch(() => null);
      if (models?.catalog) state.providerModels = models.catalog;
    }
    renderHostToolSettings();
    renderProviderModelCatalog();
    renderWorkerBindingSettings();
    toast("Host tools + provider models discovered");
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

function renderRemoteAccessSettings() {
  if (!elements.remoteAccessStatus) return;
  const remote = state.remoteAccess || {};
  const gateway = remote.gateway || { status: "OFFLINE" };
  const connector = remote.connector || { status: "OFFLINE" };
  const online = gateway.status === "READY" || gateway.status === "HOST_OFFLINE";
  const internetReady = connector.status === "READY";
  const connectorFailed =
    connector.status === "FAILED" ||
    connector.status === "TUNNEL_FAILED" ||
    Boolean(connector.error_code);
  const localOperator = state.accessSurface !== "REMOTE_BROWSER";
  const configuration = connector.configuration || null;
  const transport =
    connector.transport_kind === "SSH_REVERSE_TUNNEL" || configuration
      ? "SSH_REVERSE_TUNNEL"
      : elements.remoteAccessTransport.value || "LAN";
  elements.remoteAccessTransport.value = transport;
  elements.remoteConnectorFields.classList.toggle(
    "hidden",
    transport !== "SSH_REVERSE_TUNNEL"
  );
  if (configuration) {
    elements.remotePublicUrl.value = configuration.public_base_url || "";
    elements.remoteSshHost.value = configuration.ssh_host || "";
    elements.remoteSshPort.value = configuration.ssh_port || 22;
    elements.remoteSshUser.value = configuration.ssh_user || "";
    elements.remoteForwardPort.value = configuration.remote_port || 18443;
    elements.remoteIdentityFile.value = configuration.identity_file || "";
    elements.remoteKnownHostsFile.value = configuration.known_hosts_file || "";
  }
  const visibleStatus =
    transport === "SSH_REVERSE_TUNNEL"
      ? internetReady
        ? "READY"
        : online
          ? connectorFailed
            ? "GATEWAY_UP / TUNNEL_FAILED"
            : gateway.status || "OFFLINE"
          : connector.status || gateway.status || "OFFLINE"
      : gateway.status;
  elements.remoteAccessStatus.textContent = visibleStatus || "OFFLINE";
  elements.remoteAccessStatus.dataset.state =
    internetReady || online ? "READY" : visibleStatus || "OFFLINE";
  const publicUrl =
    gateway.public_base_url ||
    configuration?.public_base_url ||
    "";
  const lines = [];
  if (publicUrl) {
    lines.push(
      `${internetReady ? "Internet" : online ? "Gateway (tunnel down)" : "Saved"} URL: ${publicUrl}`
    );
  } else {
    lines.push("Mobile gateway is stopped.");
  }
  if (gateway.control_endpoint) {
    lines.push(`Gateway control: ${gateway.control_endpoint}`);
  }
  if (gateway.status) {
    lines.push(`Gateway: ${gateway.status}`);
  }
  if (connector.status) {
    lines.push(`Tunnel: ${connector.status}`);
  }
  if (connector.detail || connector.error_code) {
    lines.push(
      `Error: ${connector.error_code || "FAILED"} · ${connector.detail || ""}`
    );
  }
  if (remote.resume?.status && remote.resume.status !== "REMOTE_ACCESS_STARTED") {
    lines.push(
      `Resume: ${remote.resume.status}${
        remote.resume.detail ? ` · ${remote.resume.detail}` : ""
      }`
    );
  }
  if (connectorFailed && (configuration?.remote_port || 18443)) {
    lines.push(
      `Hint: remote port ${configuration?.remote_port || 18443} may still be held on the VPS from a prior tunnel (ssh log: remote port forwarding failed). Free that port or change remote_port, then Start again.`
    );
  }
  elements.remoteAccessEndpoint.textContent = lines.join("\n");
  // Allow restart when gateway is up but tunnel failed.
  elements.startRemoteAccess.disabled =
    (online && internetReady) || !localOperator;
  elements.stopRemoteAccess.disabled = !online || !localOperator;
  elements.createPairing.disabled =
    !online ||
    !localOperator ||
    (transport === "SSH_REVERSE_TUNNEL" && !internetReady);
  elements.remoteAccessTransport.disabled =
    (online && internetReady) || !localOperator;

  elements.remotePairingList.replaceChildren();
  const pending = (remote.pairings || []).filter(
    (item) => item.state === "AWAITING_APPROVAL"
  );
  if (!pending.length) {
    elements.remotePairingList.append(node("p", "empty-copy", "No pending devices"));
  }
  for (const pairing of pending) {
    const row = node("div", "remote-access-row");
    const copy = node("div", "remote-access-copy");
    copy.append(
      node("strong", "", pairing.device_name || "Unnamed browser"),
      node("small", "", `Requested ${pairing.requested_at || "UNKNOWN"}`)
    );
    const actions = node("div", "remote-access-row-actions");
    const approve = node("button", "primary-button compact-action", "Approve");
    approve.type = "button";
    approve.dataset.pairingId = pairing.pairing_id;
    approve.dataset.decision = "approve";
    const deny = node("button", "icon-button remote-pairing-decision", "×");
    deny.type = "button";
    deny.title = "Deny pairing";
    deny.dataset.pairingId = pairing.pairing_id;
    deny.dataset.decision = "deny";
    actions.append(approve, deny);
    row.append(copy, actions);
    elements.remotePairingList.append(row);
  }

  elements.remoteDeviceList.replaceChildren();
  const devices = (remote.devices || []).filter((item) => item.state !== "REVOKED");
  if (!devices.length) {
    elements.remoteDeviceList.append(node("p", "empty-copy", "No paired devices"));
  }
  for (const device of devices) {
    const row = node("div", "remote-access-row");
    const copy = node("div", "remote-access-copy");
    copy.append(
      node("strong", "", device.display_name),
      node("small", "", `Last seen ${device.last_seen_at}`)
    );
    const revoke = node("button", "secondary-button compact-action", "Revoke");
    revoke.type = "button";
    revoke.dataset.deviceId = device.device_id;
    row.append(copy, revoke);
    elements.remoteDeviceList.append(row);
  }
}

async function refreshRemoteAccessSettings() {
  state.remoteAccess = await api("/v1/settings/remote-access");
  renderRemoteAccessSettings();
}

async function setRemoteAccess(operation) {
  elements.settingsError.textContent = "";
  const transport = elements.remoteAccessTransport.value;
  let body;
  if (operation !== "start") {
    body = { transport_kind: transport };
  } else if (transport === "LAN") {
    body = { transport_kind: "LAN" };
  } else if (
    !elements.remotePublicUrl.value.trim() &&
    state.remoteAccess?.connector?.configuration_available
  ) {
    // Prefer saved Gabia/SSH config when form fields are empty after reboot.
    body = { transport_kind: "SAVED" };
  } else {
    body = {
      transport_kind: "SSH_REVERSE_TUNNEL",
      public_base_url: elements.remotePublicUrl.value.trim(),
      ssh_host: elements.remoteSshHost.value.trim(),
      ssh_port: Number(elements.remoteSshPort.value),
      ssh_user: elements.remoteSshUser.value.trim(),
      remote_port: Number(elements.remoteForwardPort.value),
      identity_file: elements.remoteIdentityFile.value.trim(),
      known_hosts_file: elements.remoteKnownHostsFile.value.trim(),
    };
  }
  const result = await api(`/v1/settings/remote-access/${operation}`, {
    method: "POST",
    body,
  });
  await refreshRemoteAccessSettings();
  if (operation !== "start") {
    toast("Mobile access stopped");
    return;
  }
  if (result.status === "REMOTE_ACCESS_PARTIAL") {
    toast(
      result.connector?.detail ||
        "Gateway up, tunnel failed (often remote port in use)",
      true
    );
    return;
  }
  toast("Mobile access started");
}

async function createRemotePairing() {
  elements.settingsError.textContent = "";
  const result = await api("/v1/settings/remote-access/pairings", {
    method: "POST",
    body: { ttl_seconds: 600 },
  });
  const pairing = result.pairing;
  elements.remotePairingInvite.classList.remove("hidden");
  elements.remotePairingInvite.replaceChildren(
    node("strong", "", pairing.code),
    node("span", "", pairing.pair_url),
    node("small", "", `Expires ${pairing.expires_at}`)
  );
  await refreshRemoteAccessSettings();
}

async function decideRemotePairing(pairingId, decision) {
  await api(
    `/v1/settings/remote-access/pairings/${encodeURIComponent(pairingId)}/${decision}`,
    { method: "POST", body: {} }
  );
  await refreshRemoteAccessSettings();
  toast(decision === "approve" ? "Device approved" : "Pairing denied");
}

async function revokeRemoteDevice(deviceId) {
  await api(
    `/v1/settings/remote-access/devices/${encodeURIComponent(deviceId)}/revoke`,
    { method: "POST", body: {} }
  );
  await refreshRemoteAccessSettings();
  toast("Device revoked");
}

function renderRendezvousSettings() {
  if (!elements.rendezvousStatus || !elements.rendezvousPendingList) {
    return;
  }
  const payload = state.rendezvous || {};
  const client = payload.client || {};
  const active = client.status === "RENDEZVOUS_ACTIVE";
  elements.rendezvousStatus.dataset.state = active ? "READY" : "OFFLINE";
  elements.rendezvousStatus.textContent = active ? "Online" : "Inactive";
  if (elements.rendezvousSummary) {
    const parts = [];
    if (client.universe_id) {
      parts.push(`id ${client.universe_id}`);
    }
    if (client.endpoint_url) {
      parts.push(client.endpoint_url);
    }
    if (client.last_error?.code) {
      parts.push(`${client.last_error.code}: ${client.last_error.detail || ""}`);
    }
    elements.rendezvousSummary.textContent = parts.join(" · ") || "Not registered";
  }
  if (elements.stopRendezvous) {
    elements.stopRendezvous.disabled = !active;
  }
  elements.rendezvousPendingList.replaceChildren();
  const pending = client.pending || [];
  if (!pending.length) {
    elements.rendezvousPendingList.append(
      node("p", "empty-copy", "No pending join requests")
    );
  }
  for (const item of pending) {
    const row = node("div", "remote-access-row");
    const copy = node("div", "remote-access-copy");
    copy.append(
      node("strong", "", item.connect_request_id || "request"),
      node(
        "small",
        "",
        `expires ${item.expires_at != null ? item.expires_at : "UNKNOWN"}`
      )
    );
    const actions = node("div", "remote-access-row-actions");
    const approve = node("button", "primary-button compact-action", "Approve");
    approve.type = "button";
    approve.dataset.rendezvousRequestId = item.connect_request_id;
    approve.dataset.decision = "approve";
    const deny = node("button", "icon-button remote-pairing-decision", "×");
    deny.type = "button";
    deny.title = "Deny join";
    deny.dataset.rendezvousRequestId = item.connect_request_id;
    deny.dataset.decision = "deny";
    actions.append(approve, deny);
    row.append(copy, actions);
    elements.rendezvousPendingList.append(row);
  }
}

async function refreshRendezvousSettings() {
  state.rendezvous = await api("/v1/settings/rendezvous");
  renderRendezvousSettings();
}

async function stopRendezvousClient() {
  await api("/v1/settings/rendezvous/stop", { method: "POST", body: {} });
  await refreshRendezvousSettings();
  toast("Rendezvous stopped");
}

async function decideRendezvousRequest(requestId, decision) {
  await api(
    `/v1/settings/rendezvous/connect-requests/${encodeURIComponent(requestId)}/${decision}`,
    { method: "POST", body: {} }
  );
  await refreshRendezvousSettings();
  toast(decision === "approve" ? "Join approved" : "Join denied");
}

let rendezvousRefreshTimer = null;

function stopRendezvousRefreshTimer() {
  if (rendezvousRefreshTimer != null) {
    clearInterval(rendezvousRefreshTimer);
    rendezvousRefreshTimer = null;
  }
}

function startRendezvousRefreshTimer() {
  stopRendezvousRefreshTimer();
  rendezvousRefreshTimer = setInterval(() => {
    if (!elements.settingsDialog?.open) {
      stopRendezvousRefreshTimer();
      return;
    }
    refreshRendezvousSettings().catch(() => {});
  }, 4000);
}

async function refreshMultiRooms() {
  if (!elements.multiRoomList) return;
  const data = await api("/v1/rooms");
  state.multiRooms = data.rooms || [];
  elements.multiRoomList.replaceChildren();
  if (!state.multiRooms.length) {
    elements.multiRoomList.append(node("p", "empty-copy", "No multi-rooms yet"));
    return;
  }
  for (const room of state.multiRooms) {
    const row = node("div", "remote-access-row");
    const copy = node("div", "remote-access-copy");
    copy.append(
      node("strong", "", `${room.room_type} · ${room.title}`),
      node("small", "", room.room_id)
    );
    const open = node("button", "secondary-button compact-action", "Open");
    open.type = "button";
    open.addEventListener("click", () => {
      openMultiRoom(room.room_id).catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
    row.append(copy, open);
    elements.multiRoomList.append(row);
  }
}

async function openMultiRoom(roomId) {
  state.activeMultiRoomId = roomId;
  state.multiRoomLiveOutput = {};
  const snap = await api(`/v1/rooms/${encodeURIComponent(roomId)}`);
  state.activeMultiRoomSnapshot = snap;
  renderActiveMultiRoom();
  openMultiRoomStream(roomId);
}

function renderActiveMultiRoom() {
  const snap = state.activeMultiRoomSnapshot;
  if (!snap || !elements.multiRoomDetail) return;
  const cursors = snap.participant_cursors || [];
  const cursorByBinding = new Map(
    cursors.map((cursor) => [cursor.binding_id, cursor])
  );
  const room = snap.room || {};
  const summary = node("div", "remote-access-copy");
  summary.append(
    node("strong", "", room.title || room.room_type || "Room"),
    node(
      "small",
      "",
      `${room.room_type || "ROOM"} | participants=${(snap.bindings || []).length} | events=${(snap.events || []).length}`
    )
  );
  const participantList = node("div", "remote-access-list");
  for (const binding of snap.bindings || []) {
    const cursor = cursorByBinding.get(binding.binding_id) || {};
    const participantState = cursor.participant_state || "OBSERVED";
    const row = node("div", "remote-access-row");
    const copy = node("div", "remote-access-copy");
    const label =
      binding.display_name ||
      [binding.provider, binding.slot_role].filter(Boolean).join(" ") ||
      binding.slot_role ||
      "Participant";
    const liveOutput = state.multiRoomLiveOutput?.[binding.binding_id] || "";
    copy.append(
      node("strong", "", label),
      node(
        "small",
        "",
        `${participantState} | delivered=${cursor.delivery_sequence || 0}`
      )
    );
    if (liveOutput) copy.append(node("small", "", liveOutput));
    row.append(copy);
    const dedicatedProjectMaster =
      room.room_type === "PROJECT" && binding.slot_role === "MASTER";
    const controllable =
      binding.slot_role !== "USER" &&
      !dedicatedProjectMaster &&
      Boolean(binding.provider && binding.provider_session_ref);
    if (controllable) {
      const connected = ["CONTROLLED", "LIVE"].includes(participantState);
      const control = node(
        "button",
        "secondary-button compact-action",
        connected ? "Disconnect" : "Connect native"
      );
      control.type = "button";
      control.addEventListener("click", () => {
        setRoomParticipantControl(
          room.room_id,
          binding.binding_id,
          connected ? "DISCONNECT" : "CONNECT"
        ).catch((error) => {
          elements.settingsError.textContent = error.message;
        });
      });
      row.append(control);
    }
    participantList.append(row);
  }
  const transcript = node("pre", "remote-access-endpoint");
  transcript.textContent = (snap.messages || [])
    .slice(-5)
    .map((message) => `${message.author_role}: ${message.body_text}`)
    .join("\n");
  elements.multiRoomDetail.replaceChildren(summary, participantList, transcript);
}

async function setRoomParticipantControl(roomId, bindingId, action) {
  await api(
    `/v1/rooms/${encodeURIComponent(roomId)}/bindings/${encodeURIComponent(bindingId)}/control`,
    { method: "POST", body: { action } }
  );
  await openMultiRoom(roomId);
  toast(action === "CONNECT" ? "Native session connected" : "Native session disconnected");
}

function closeMultiRoomStream() {
  if (state.multiRoomStream) state.multiRoomStream.close();
  state.multiRoomStream = null;
}

function openMultiRoomStream(roomId) {
  closeMultiRoomStream();
  const source = new EventSource(
    `/v1/rooms/${encodeURIComponent(roomId)}/stream`
  );
  state.multiRoomStream = source;
  source.addEventListener("snapshot", (event) => {
    if (state.activeMultiRoomId !== roomId) return;
    const payload = JSON.parse(event.data);
    state.activeMultiRoomSnapshot = {
      status: "ROOM_SNAPSHOT",
      room: payload.room,
      bindings: payload.bindings || [],
      messages: payload.messages || [],
      events: payload.events || [],
      participant_cursors: payload.participant_cursors || [],
      bridge_line: payload.bridge_line || "",
      write_roles: state.activeMultiRoomSnapshot?.write_roles || [],
      user_may_write: state.activeMultiRoomSnapshot?.user_may_write || false,
    };
    renderActiveMultiRoom();
  });
  source.addEventListener("room", (event) => {
    if (state.activeMultiRoomId !== roomId) return;
    const envelope = JSON.parse(event.data);
    const payload = envelope.payload || {};
    if (payload.type === "ROOM_MESSAGE" && payload.message) {
      const snapshot = state.activeMultiRoomSnapshot || { messages: [], events: [] };
      snapshot.messages = dedupeRoomMessages([
        ...(snapshot.messages || []),
        payload.message,
      ]);
      if (payload.room_event) {
        const byId = new Map(
          (snapshot.events || []).map((item) => [item.room_event_id, item])
        );
        byId.set(payload.room_event.room_event_id, payload.room_event);
        snapshot.events = [...byId.values()].sort(
          (left, right) => left.room_sequence - right.room_sequence
        );
      }
      state.activeMultiRoomSnapshot = snapshot;
      renderActiveMultiRoom();
      return;
    }
    if (payload.type === "PARTICIPANT_DELTA") {
      const bindingId = payload.binding_id || "provider";
      const current = state.multiRoomLiveOutput[bindingId] || "";
      state.multiRoomLiveOutput[bindingId] = `${current}${payload.delta || ""}`.slice(
        -12000
      );
      renderActiveMultiRoom();
      return;
    }
    if (
      payload.type === "PARTICIPANT_COMPLETED" ||
      payload.type === "PARTICIPANT_FAILED"
    ) {
      if (payload.binding_id) delete state.multiRoomLiveOutput[payload.binding_id];
      renderActiveMultiRoom();
      return;
    }
    api(`/v1/rooms/${encodeURIComponent(roomId)}`)
      .then((snapshot) => {
        if (state.activeMultiRoomId === roomId) {
          state.activeMultiRoomSnapshot = snapshot;
          renderActiveMultiRoom();
        }
      })
      .catch(() => {});
  });
}

async function createMeetingRoomThin() {
  const project = state.selectedProjectId || state.projects?.[0]?.project_id || null;
  await api("/v1/rooms", {
    method: "POST",
    body: {
      room_type: "MEETING",
      title: "Meeting",
      topic: "function-first debate",
      project_id: project,
      models: [
        { provider: "GROK", display_name: "Grok" },
        { provider: "CLAUDE", display_name: "Claude" },
      ],
    },
  });
  await refreshMultiRooms();
  toast("Meeting room created");
}

async function postActiveRoomAsUser() {
  if (!state.activeMultiRoomId) {
    throw new Error("Open a room first");
  }
  const text = elements.multiRoomMessage?.value?.trim() || "";
  if (!text) throw new Error("Message required");
  await api(`/v1/rooms/${encodeURIComponent(state.activeMultiRoomId)}/messages`, {
    method: "POST",
    body: { author_role: "USER", body_text: text },
  });
  elements.multiRoomMessage.value = "";
  await openMultiRoom(state.activeMultiRoomId);
  toast("Posted");
}

async function callMasterOnActiveRoom() {
  if (!state.activeMultiRoomId) {
    throw new Error("Open a boss room first");
  }
  await api(`/v1/rooms/${encodeURIComponent(state.activeMultiRoomId)}/call-master`, {
    method: "POST",
    body: { reason: "operator call", auto_attach_master: true },
  });
  await openMultiRoom(state.activeMultiRoomId);
  toast("Master called");
}

async function injectSessionRefThin() {
  const projectId =
    elements.injectProjectId?.value?.trim() ||
    state.selectedProjectId ||
    state.projects?.[0]?.project_id ||
    "";
  const provider = elements.injectProvider?.value || "CODEX";
  const sessionRef = elements.injectSessionRef?.value?.trim() || "";
  if (!projectId) {
    throw new Error("project id required (field or selected project)");
  }
  if (!sessionRef) {
    throw new Error("session ref required");
  }
  const result = await api("/v1/sessions/inject", {
    method: "POST",
    body: {
      project_id: projectId,
      room_type: "PROJECT",
      slot_role: "MASTER",
      provider,
      session_ref: sessionRef,
      make_default: true,
      display_name: "Project Master",
    },
  });
  const roomId = result.room?.room_id || result.binding?.room_id;
  await refreshMultiRooms();
  // Prefer Project Master conversation dock over multi-room list only.
  try {
    await callProjectMaster(projectId);
    toast(
      result.supervisor_session_created
        ? `Injected → ${projectId} Project Master (registered)`
        : `Injected → ${projectId} Project Master (reused)`
    );
    return;
  } catch (error) {
    console.warn("callProjectMaster after inject failed", error);
  }
  if (roomId) {
    await openMultiRoom(roomId);
  }
  toast(
    result.supervisor_session_created
      ? "Session ref injected (registered)"
      : "Session ref injected (reused)"
  );
}

async function openProviderSettings() {
  elements.settingsError.textContent = "";
  [
    state.providerSettings,
    state.workerBindings,
    state.hostTools,
    state.serviceSettings,
    state.remoteAccess,
    state.rendezvous,
    state.runtimePreflight,
    state.providerModels,
  ] = await Promise.all([
    api("/v1/settings/providers"),
    api("/v1/settings/worker-bindings"),
    api("/v1/settings/host-tools"),
    api("/v1/settings/service").catch(() => null),
    api("/v1/settings/remote-access"),
    api("/v1/settings/rendezvous").catch(() => null),
    api("/v1/runtime/preflight").catch(() => null),
    api("/v1/settings/provider-models")
      .then((result) => result.catalog || result)
      .catch(() => null),
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
  renderWorkerBindingSettings();
  renderHostToolSettings();
  renderRuntimePreflight();
  renderLocalServiceStatus();
  renderRemoteAccessSettings();
  renderRendezvousSettings();
  refreshMultiRooms().catch(() => {});
  setSettingsTab(state.settingsTab || "service");
  elements.settingsDialog.showModal();
  startRendezvousRefreshTimer();
}

function setDialogCategoryTab(root, { tabAttr, panelAttr, stateKey, allowed, fallback }) {
  if (!root) return;
  const activeId = allowed.has(state[stateKey]) ? state[stateKey] : fallback;
  state[stateKey] = activeId;
  for (const tab of root.querySelectorAll(`[${tabAttr}]`)) {
    const active = tab.getAttribute(tabAttr) === activeId;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const panel of root.querySelectorAll(`[${panelAttr}]`)) {
    const active = panel.getAttribute(panelAttr) === activeId;
    panel.classList.toggle("is-active", active);
    if (active) panel.removeAttribute("hidden");
    else panel.setAttribute("hidden", "");
  }
}

function setSettingsTab(tabId) {
  const allowed = new Set(["service", "remote", "rooms", "providers", "host"]);
  if (allowed.has(tabId)) state.settingsTab = tabId;
  setDialogCategoryTab(elements.settingsDialog, {
    tabAttr: "data-settings-tab",
    panelAttr: "data-settings-panel",
    stateKey: "settingsTab",
    allowed,
    fallback: "service",
  });
}

function setObservatoryTab(tabId) {
  const allowed = new Set(["sessions", "register", "activity", "attachments", "audit"]);
  if (allowed.has(tabId)) state.observatoryTab = tabId;
  setDialogCategoryTab(elements.sessionObservatoryDialog, {
    tabAttr: "data-observatory-tab",
    panelAttr: "data-observatory-panel",
    stateKey: "observatoryTab",
    allowed,
    fallback: "sessions",
  });
}

function setTodoTab(tabId) {
  const allowed = new Set(["board", "create"]);
  if (allowed.has(tabId)) state.todoTab = tabId;
  setDialogCategoryTab(elements.todoDialog, {
    tabAttr: "data-todo-tab",
    panelAttr: "data-todo-panel",
    stateKey: "todoTab",
    allowed,
    fallback: "board",
  });
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
    const [scopeKind, scopeId] = elements.workerBindingScope.value.split(":", 2);
    for (const row of elements.workerBindingSettings.querySelectorAll(
      ".worker-binding-row"
    )) {
      requests.push(
        api("/v1/settings/worker-bindings", {
          method: "POST",
          body: {
            scope_kind: scopeKind,
            scope_id: scopeId,
            worker_role: row.dataset.role,
            task_type: "*",
            provider: row.querySelector(".worker-binding-provider").value,
            model_ref: (
              row.querySelector(".worker-binding-model-custom")?.value ||
              row.querySelector(".worker-binding-model")?.value ||
              ""
            ).trim(),
            effort: row.querySelector(".worker-binding-effort").value,
            skill_refs: row
              .querySelector(".worker-binding-skills")
              .value.split(",")
              .map((value) => value.trim())
              .filter(Boolean),
            enabled: true,
          },
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
    [state.providerSettings, state.workerBindings] = await Promise.all([
      api("/v1/settings/providers"),
      api("/v1/settings/worker-bindings"),
    ]);
    renderProviderSettings();
    renderWorkerBindingSettings();
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
    state.conductorPermissions = result.permissions || [];
    state.conductorRuntimeBinding = result.runtime_binding || null;
    renderComposerState();
    renderRoomMessages();
  } catch (error) {
    console.warn("Conductor room refresh failed", error);
  } finally {
    state.conductorRefreshInFlight = false;
  }
}

function openConductorRoomStream() {
  if (state.conductorRoomStream) return;
  const source = new EventSource("/v1/conductor-room/stream");
  state.conductorRoomStream = source;
  state.conductorRoomStreamState = "CONNECTING";
  source.addEventListener("conductor-room", (event) => {
    let envelope;
    try {
      envelope = JSON.parse(event.data);
    } catch (error) {
      console.warn("Conductor Room stream payload is invalid", error);
      return;
    }
    state.conductorRoomStreamState = "LIVE";
    const payload = envelope.payload || {};
    if (payload.type === "SNAPSHOT") {
      state.conductorMessages = payload.messages || [];
      state.conductorPermissions = payload.permissions || [];
      state.conductorRuntimeBinding = payload.runtime_binding || null;
      renderComposerState();
      renderRoomMessages();
      return;
    }
    if (payload.type !== "CONDUCTOR_STREAM" || !payload.message_id) return;
    const key = payload.message_id;
    if (payload.event === "COMPLETED") {
      delete state.conductorStreamReplies[key];
      window.setTimeout(refreshConductorRoom, 0);
    } else if (payload.event === "FAILED") {
      state.conductorStreamReplies[key] = {
        body: state.conductorStreamReplies[key]?.body || "",
        state: payload.detail || "Failed",
      };
    } else {
      const current = state.conductorStreamReplies[key] || {
        body: "",
        state: "Thinking",
      };
      if (payload.event === "DELTA") {
        current.body += payload.delta || "";
        current.state = "Responding";
      }
      state.conductorStreamReplies[key] = current;
    }
    if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
      renderRoomMessages();
    }
  });
  source.addEventListener("error", () => {
    if (state.conductorRoomStream === source) {
      state.conductorRoomStreamState = "RECONNECTING";
    }
  });
}

function closeProjectRoomStream() {
  if (state.projectRoomStream) {
    state.projectRoomStream.close();
  }
  state.projectRoomStream = null;
  state.projectRoomStreamProjectId = null;
  state.projectRoomStreamState = "IDLE";
  state.projectStreamReplies = {};
  renderSessionRail();
}

function dedupeRoomMessages(messages) {
  const byId = new Map();
  for (const message of Array.isArray(messages) ? messages : []) {
    if (!message?.message_id) continue;
    byId.set(message.message_id, message);
  }
  return [...byId.values()].sort((left, right) =>
    String(left.created_at || "").localeCompare(String(right.created_at || ""))
  );
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
  state.projectRoomStreamState = "CONNECTING";
  renderSessionRail();
  source.addEventListener("project-room", (event) => {
    let envelope;
    try {
      envelope = JSON.parse(event.data);
    } catch (error) {
      console.warn("Project Room stream payload is invalid", error);
      return;
    }
    state.projectRoomStreamState = "LIVE";
    renderSessionRail();
    const payload = envelope.payload || {};
    if (payload.type === "SNAPSHOT" || payload.type === "ROOM_CHANGED") {
      state.roomMessages = dedupeRoomMessages(payload.messages);
      state.projectPermissions = Array.isArray(payload.permissions)
        ? payload.permissions
        : state.projectPermissions;
      state.governanceProposals = Array.isArray(payload.governance_proposals)
        ? payload.governance_proposals
        : state.governanceProposals;
      mergeGovernanceProposalInbox(projectId, state.governanceProposals);
      renderProjects();
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
      state.projectRoomStreamState = "RECONNECTING";
      elements.roomHint.textContent = "Project Master reconnecting";
      renderSessionRail();
    }
  });
}

function buildGraph() {
  elements.nodeBreadcrumb.classList.add("hidden");
  // Universe map: always full tree (hub → projects → systems). Depth = dim only.
  // Timeline/Documents still use project-interior graphs. focusedNodeId dig-in
  // is for non-universe views only (not multiverse).
  if (state.view === "universe") {
    buildMultiverseGraph();
    return;
  }
  if (state.view === "implementation") {
    if (state.selectedProject) {
      buildProjectInteriorGraph({ mode: "implementation" });
      return;
    }
    buildMultiverseGraph();
    return;
  }
  if (!state.selectedProject) {
    state.graph.nodes = [];
    state.graph.edges = [];
    elements.graphEmpty.classList.remove("hidden");
    if (elements.graphEmpty) {
      elements.graphEmpty.textContent =
        "Select Multiverse Map for Universe → projects, or pick a project";
    }
    drawGraph();
    return;
  }
  if (state.view === "timeline") {
    buildTimelineGraph();
    return;
  }
  buildProjectInteriorGraph({ mode: state.view });
}

/** Fetch projections for every attached project so multiverse can stay fully expanded. */
async function loadAllProjectProjections() {
  const projects = state.projects || [];
  if (!projects.length) {
    state.projectionsByProject = {};
    return;
  }
  const results = await Promise.all(
    projects.map((project) => {
      if (!project.projection_available) return Promise.resolve(null);
      return api(
        `/v1/projects/${encodeURIComponent(project.project_id)}/projection`
      ).catch(() => null);
    })
  );
  const next = {};
  projects.forEach((project, index) => {
    const projection = results[index]?.projection;
    if (projection) next[project.project_id] = projection;
  });
  state.projectionsByProject = next;
}

function projectionForProject(projectId) {
  if (!projectId) return null;
  if (state.projectionsByProject?.[projectId]) {
    return state.projectionsByProject[projectId];
  }
  if (state.selectedProject?.project_id === projectId) {
    return state.projection;
  }
  return null;
}

/**
 * Universe map: always expanded hub → projects → systems.
 * Selection never hides nodes; drawGraph uses dim alpha for current depth.
 */
function buildMultiverseGraph() {
  const hub = {
    id: "universe:hub",
    label: "Universe",
    kind: "universe",
    depth: 0,
    projectId: null,
    parentId: null,
    data: {
      kind: "UNIVERSE_INSTANCE",
      label: "Local Universe",
      note: "Parent observation hub — projects attach around this instance",
    },
    x: 0,
    y: 0,
  };
  const graphNodes = [hub];
  const graphEdges = [];
  const projects = (state.projects || [])
    .slice()
    .sort((a, b) => projectSortKey(a).localeCompare(projectSortKey(b)));

  if (!projects.length) {
    state.graph.nodes = graphNodes;
    state.graph.edges = graphEdges;
    state.graph.scale = 1;
    state.graph.x = 0;
    state.graph.y = 0;
    if (elements.graphEmpty) {
      elements.graphEmpty.classList.add("hidden");
    }
    if (elements.graphHint) {
      elements.graphHint.classList.remove("hidden");
      elements.graphHint.textContent =
        "Universe hub · no projects attached yet (register / restart for anchors)";
    }
    drawGraph();
    return;
  }

  // Room for always-expanded system fans around each project card.
  const maxSystems = projects.reduce((max, project) => {
    const count = (projectionForProject(project.project_id)?.nodes || []).length;
    return Math.max(max, count);
  }, 0);
  const projectRadius = Math.max(230, 180 + maxSystems * 8);

  projects.forEach((project, index) => {
    const angle =
      (Math.PI * 2 * index) / Math.max(projects.length, 1) - Math.PI / 2;
    const selected =
      state.selectedProject?.project_id === project.project_id;
    const id = `project:${project.project_id}`;
    const px = Math.cos(angle) * projectRadius;
    const py = Math.sin(angle) * projectRadius;
    graphNodes.push({
      id,
      label: projectDisplayName(project),
      kind: "project",
      depth: 1,
      projectId: project.project_id,
      parentId: hub.id,
      data: project,
      x: px,
      y: py,
      selectedProject: selected,
    });
    graphEdges.push({
      from: hub.id,
      to: id,
      kind: "project-link",
    });

    // Always expand this project's functional nodes (depth 2).
    const projection = projectionForProject(project.project_id);
    const systems = projection?.nodes || [];
    const count = Math.max(systems.length, 1);
    systems.forEach((item, systemIndex) => {
      const systemAngle = (Math.PI * 2 * systemIndex) / count - Math.PI / 2;
      const r = 96 + (systemIndex % 3) * 14;
      const systemId = `node:${project.project_id}:${item.node_id}`;
      // Prefer global node id when unique; fall back to project-scoped id to avoid collisions.
      const plainId = `node:${item.node_id}`;
      const idTaken = graphNodes.some((nodeItem) => nodeItem.id === plainId);
      const nodeId = idTaken ? systemId : plainId;
      graphNodes.push({
        id: nodeId,
        label: item.title,
        kind: "system",
        depth: 2,
        projectId: project.project_id,
        parentId: id,
        data: { ...item, project_id: project.project_id },
        x: px + Math.cos(systemAngle) * r,
        y: py + Math.sin(systemAngle) * r,
      });
      graphEdges.push({
        from: id,
        to: nodeId,
        kind: "contains",
      });
    });
    for (const edge of projection?.edges || []) {
      const fromCandidates = [
        `node:${edge.from_node}`,
        `node:${project.project_id}:${edge.from_node}`,
      ];
      const toCandidates = [
        `node:${edge.to_node}`,
        `node:${project.project_id}:${edge.to_node}`,
      ];
      const fromId = fromCandidates.find((candidate) =>
        graphNodes.some((nodeItem) => nodeItem.id === candidate)
      );
      const toId = toCandidates.find((candidate) =>
        graphNodes.some((nodeItem) => nodeItem.id === candidate)
      );
      if (fromId && toId) {
        graphEdges.push({
          from: fromId,
          to: toId,
          kind: edge.kind || "related",
        });
      }
    }
  });

  state.graph.nodes = graphNodes;
  state.graph.edges = graphEdges;
  // Keep pan/zoom if user already moved; only reset when empty graph was shown.
  if (!Number.isFinite(state.graph.scale) || state.graph.scale <= 0) {
    state.graph.scale = 1;
  }
  elements.graphEmpty.classList.add("hidden");
  if (elements.graphHint) {
    const systemCount = graphNodes.filter((item) => item.kind === "system").length;
    const focusLabel =
      state.selectedNode?.label ||
      (state.selectedProject
        ? projectDisplayName(state.selectedProject)
        : "Universe");
    elements.graphHint.textContent = `Icons = projects · hover name · always expanded · ${projects.length} project(s) · ${systemCount} system(s) · focus: ${focusLabel}`;
  }
  drawGraph();
}

/**
 * Dim style by current focus depth. Nodes stay visible; alpha encodes depth.
 * - focus node: full
 * - same depth (esp. same branch): bright
 * - parent/child on path: medium-bright
 * - other depths / branches: progressively dimmer
 */
function graphDepthStyle() {
  const nodes = state.graph.nodes || [];
  const byId = new Map();
  let focus =
    (state.selectedNode &&
      nodes.find((item) => item.id === state.selectedNode.id)) ||
    null;
  if (!focus && state.selectedProject) {
    focus = nodes.find(
      (item) => item.id === `project:${state.selectedProject.project_id}`
    );
  }
  if (!focus) {
    for (const item of nodes) {
      byId.set(item.id, {
        alpha: 1,
        selected: false,
        emphasis: item.kind === "universe" || item.kind === "project",
      });
    }
    return { hasFocus: false, focus: null, byId };
  }

  const focusDepth = Number.isFinite(focus.depth) ? focus.depth : 0;
  const focusProjectId = focus.projectId || focus.data?.project_id || null;

  for (const item of nodes) {
    const depth = Number.isFinite(item.depth) ? item.depth : 0;
    const depthDelta = Math.abs(depth - focusDepth);
    const sameBranch =
      item.id === focus.id ||
      item.kind === "universe" ||
      (focus.kind === "universe" && depth <= 1) ||
      (focusProjectId &&
        (item.projectId === focusProjectId ||
          item.id === `project:${focusProjectId}`)) ||
      (focus.parentId && item.id === focus.parentId) ||
      item.parentId === focus.id;

    let alpha;
    if (item.id === focus.id) {
      alpha = 1;
    } else if (depthDelta === 0 && sameBranch) {
      alpha = 0.95;
    } else if (depthDelta === 0) {
      // Same depth, other branch — still "current depth" but quieter.
      alpha = 0.7;
    } else if (depthDelta === 1 && sameBranch) {
      alpha = 0.88;
    } else if (depthDelta === 1) {
      alpha = 0.48;
    } else if (sameBranch) {
      alpha = 0.55;
    } else {
      alpha = Math.max(0.3, 0.5 - depthDelta * 0.1);
    }

    byId.set(item.id, {
      alpha,
      selected: item.id === focus.id || Boolean(item.selectedProject && item.id === focus.id),
      emphasis: alpha >= 0.85,
    });
  }
  // Mark selected project card when focus is a system under it.
  if (focusProjectId) {
    const projectNodeId = `project:${focusProjectId}`;
    const style = byId.get(projectNodeId);
    if (style && focus.id !== projectNodeId) {
      style.emphasis = true;
      style.alpha = Math.max(style.alpha, 0.9);
    }
  }
  return { hasFocus: true, focus, byId };
}

/** @deprecated use graphDepthStyle — kept as thin adapter for any leftover calls */
function graphNeighborhood() {
  const style = graphDepthStyle();
  const focusIds = new Set();
  const neighborIds = new Set();
  const focusEdgeKeys = new Set();
  if (style.focus) focusIds.add(style.focus.id);
  for (const [id, item] of style.byId) {
    if (item.emphasis || item.selected) neighborIds.add(id);
  }
  for (const edge of state.graph.edges || []) {
    if (neighborIds.has(edge.from) && neighborIds.has(edge.to)) {
      focusEdgeKeys.add(`${edge.from}=>${edge.to}`);
    }
  }
  return { focusIds, neighborIds, focusEdgeKeys };
}

/** Single-project interior graph (timeline/documents/future/legacy). */
function buildProjectInteriorGraph({ mode }) {
  if (!state.selectedProject) {
    buildMultiverseGraph();
    return;
  }
  const graphNodes = [
    {
      id: `project:${state.selectedProject.project_id}`,
      label: projectDisplayName(state.selectedProject),
      kind: "project",
      data: state.selectedProject,
    },
  ];
  const graphEdges = [];
  const projection = state.projection;
  if (projection) {
    if (mode === "implementation") {
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
      layoutGraph(graphNodes, mode);
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
    if (mode === "documents") {
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
    if (mode === "future") {
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
  layoutGraph(graphNodes, mode);
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
  const depthStyle = graphDepthStyle();
  for (const edge of state.graph.edges) {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    if (!from || !to) continue;
    const isDocumentLink = edge.kind === "documents";
    const isPredicted = edge.kind === "predicts";
    const isImplementationLink = edge.kind === "implementation-binding";
    const isProjectLink = edge.kind === "project-link";
    const fromStyle = depthStyle.byId.get(edge.from) || { alpha: 1 };
    const toStyle = depthStyle.byId.get(edge.to) || { alpha: 1 };
    const edgeAlpha = Math.min(fromStyle.alpha, toStyle.alpha);
    const emphasized = edgeAlpha >= 0.8;
    context.globalAlpha = edgeAlpha;
    context.lineWidth = isDocumentLink
      ? 1.4
      : emphasized && isProjectLink
        ? 2.4
        : isProjectLink
          ? 1.6
          : emphasized && edge.kind === "contains"
            ? 1.8
            : 1.2;
    context.strokeStyle = isPredicted
      ? "rgba(155, 124, 255, 0.75)"
      : isDocumentLink
      ? "rgba(240, 184, 74, 0.7)"
      : isImplementationLink
        ? "rgba(122, 106, 212, 0.7)"
        : isProjectLink
          ? emphasized
            ? "rgba(90, 220, 255, 0.9)"
            : "rgba(90, 200, 255, 0.45)"
          : edge.kind === "contains" && emphasized
            ? "rgba(100, 190, 255, 0.7)"
          : "rgba(120, 180, 230, 0.4)";
    context.setLineDash(
      isPredicted
        ? [7, 6]
        : isDocumentLink
          ? [5, 4]
          : isImplementationLink
            ? [3, 3]
            : []
    );
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.lineTo(to.x, to.y);
    context.stroke();
  }
  context.setLineDash([]);
  context.globalAlpha = 1;
  for (const item of state.graph.nodes) {
    drawGraphNodeIcon(context, item, depthStyle);
  }
  context.restore();
}

/** Stable HSL accent so projects read as different icons without full labels. */
function projectAccentColor(projectId) {
  const text = String(projectId || "project");
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
  }
  const hue = hash % 360;
  return {
    fill: `hsla(${hue}, 52%, 42%, 0.92)`,
    stroke: `hsla(${hue}, 70%, 68%, 0.95)`,
    soft: `hsla(${hue}, 55%, 55%, 0.28)`,
  };
}

function nodeMonogram(item) {
  const label = String(item?.label || item?.data?.project_id || "?").trim();
  if (!label) return "?";
  if (item.kind === "universe") return "U";
  const cleaned = label.replace(/[^A-Za-z0-9가-힣]+/g, " ").trim();
  const parts = cleaned.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  }
  return cleaned.slice(0, 2).toUpperCase();
}

/** Visual + hit metrics for multiverse icon nodes. */
function graphNodeMetrics(item) {
  if (item.kind === "universe") {
    return { shape: "hub", radius: 28, hitR: 34 };
  }
  if (item.kind === "project") {
    return { shape: "project", radius: 22, hitR: 28 };
  }
  if (item.kind === "system" || item.kind === "related" || item.kind === "focus") {
    return { shape: "system", radius: 14, hitR: 18 };
  }
  return { shape: "other", radius: 16, hitR: 20 };
}

function drawGraphNodeIcon(context, item, depthStyle) {
  const style = depthStyle.byId.get(item.id) || {
    alpha: 1,
    selected: false,
    emphasis: false,
  };
  const selected =
    style.selected ||
    state.selectedNode?.id === item.id ||
    state.hoveredNodeId === item.id ||
    Boolean(item.selectedProject && depthStyle.focus?.id === item.id);
  const emphasized = style.emphasis || selected;
  const hovered = state.hoveredNodeId === item.id;
  const metrics = graphNodeMetrics(item);
  const accent = projectAccentColor(
    item.projectId || item.data?.project_id || item.id
  );
  const r = metrics.radius * (selected ? 1.08 : hovered ? 1.05 : 1);
  context.globalAlpha = style.alpha;
  if (selected || hovered) {
    context.shadowColor = "rgba(61, 224, 255, 0.8)";
    context.shadowBlur = selected ? 20 : 14;
  } else if (emphasized) {
    context.shadowColor = accent.soft;
    context.shadowBlur = 10;
  } else {
    context.shadowColor = "rgba(61, 224, 255, 0.06)";
    context.shadowBlur = 4;
  }

  if (metrics.shape === "hub") {
    // Universe hub: ringed disc with monogram.
    context.beginPath();
    context.arc(item.x, item.y, r + 6, 0, Math.PI * 2);
    context.strokeStyle = selected ? "#3de0ff" : "rgba(90, 208, 255, 0.55)";
    context.lineWidth = selected ? 2.4 : 1.5;
    context.stroke();
    context.beginPath();
    context.arc(item.x, item.y, r, 0, Math.PI * 2);
    context.fillStyle = selected ? "rgba(12, 32, 56, 0.98)" : "rgba(10, 24, 44, 0.94)";
    context.fill();
    context.strokeStyle = selected ? "#3de0ff" : "#5ad0ff";
    context.lineWidth = selected ? 2.4 : 1.8;
    context.stroke();
  } else if (metrics.shape === "project") {
    // Project: filled circle, color-coded by project id.
    context.beginPath();
    context.arc(item.x, item.y, r, 0, Math.PI * 2);
    context.fillStyle = selected ? "rgba(12, 28, 52, 0.98)" : accent.fill;
    context.fill();
    context.strokeStyle = selected ? "#3de0ff" : accent.stroke;
    context.lineWidth = selected ? 2.6 : emphasized ? 2 : 1.5;
    context.stroke();
  } else {
    // System / leaf: smaller rounded square, accent ring from parent project.
    const half = r;
    roundedRect(context, item.x - half, item.y - half, half * 2, half * 2, 6);
    context.fillStyle = selected ? "rgba(12, 28, 48, 0.96)" : "rgba(10, 22, 40, 0.92)";
    context.fill();
    context.strokeStyle = selected
      ? "#3de0ff"
      : emphasized
        ? accent.stroke
        : "rgba(120, 170, 210, 0.55)";
    context.lineWidth = selected ? 2.2 : 1.4;
    context.stroke();
  }

  context.shadowBlur = 0;
  context.fillStyle = selected || hovered ? "#f2fbff" : "#e6f2ff";
  context.font = `${metrics.shape === "system" ? "600 10px" : "700 12px"} Segoe UI`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(nodeMonogram(item), item.x, item.y + 0.5);

  const todoCount = openTodosForGraphNode(item).length;
  if (todoCount) {
    const badgeX = item.x + r * 0.72;
    const badgeY = item.y - r * 0.72;
    context.beginPath();
    context.arc(badgeX, badgeY, 8, 0, Math.PI * 2);
    context.fillStyle = "#f6c76a";
    context.fill();
    context.fillStyle = "#111827";
    context.font = "700 9px Segoe UI";
    context.fillText(String(Math.min(todoCount, 99)), badgeX, badgeY + 0.5);
  }
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

function hitTestGraphNode(point) {
  return (
    [...(state.graph.nodes || [])]
      .reverse()
      .find((item) => {
        const { hitR } = graphNodeMetrics(item);
        const dx = point.x - item.x;
        const dy = point.y - item.y;
        return dx * dx + dy * dy <= hitR * hitR;
      }) || null
  );
}

function graphNodeKindLabel(item) {
  if (!item) return "";
  if (item.kind === "universe") return "Universe";
  if (item.kind === "project") return "Project";
  if (item.kind === "system") return "System";
  return item.kind || "Node";
}

function updateGraphHoverTooltip(event, hovered) {
  const tip = elements.graphTooltip;
  if (!tip) return;
  if (!hovered) {
    tip.classList.add("hidden");
    tip.textContent = "";
    elements.canvas?.classList.remove("is-hovering-node");
    return;
  }
  elements.canvas?.classList.add("is-hovering-node");
  const kind = graphNodeKindLabel(hovered);
  const name = hovered.label || hovered.data?.project_id || hovered.id;
  tip.replaceChildren();
  const kindEl = document.createElement("span");
  kindEl.className = "graph-tooltip-kind";
  kindEl.textContent = kind;
  const nameEl = document.createElement("span");
  nameEl.textContent = name;
  tip.append(kindEl, nameEl);
  tip.classList.remove("hidden");
  const wrap = elements.canvas?.parentElement;
  if (!wrap || !event) return;
  const wrapRect = wrap.getBoundingClientRect();
  let left = event.clientX - wrapRect.left;
  let top = event.clientY - wrapRect.top;
  // Keep tooltip inside the canvas wrap.
  const tipWidth = tip.offsetWidth || 120;
  left = Math.max(tipWidth / 2 + 8, Math.min(wrapRect.width - tipWidth / 2 - 8, left));
  top = Math.max(28, top);
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function handleGraphPointerHover(event) {
  if (state.graphPan?.moved) {
    if (state.hoveredNodeId) {
      state.hoveredNodeId = null;
      updateGraphHoverTooltip(null, null);
      drawGraph();
    }
    return;
  }
  const point = graphPoint(event);
  const hovered = hitTestGraphNode(point);
  const nextId = hovered?.id || null;
  if (nextId !== state.hoveredNodeId) {
    state.hoveredNodeId = nextId;
    drawGraph();
  }
  updateGraphHoverTooltip(event, hovered);
}

function selectGraphNode(event) {
  if (state.graphPan?.moved) return;
  const point = graphPoint(event);
  const selected = hitTestGraphNode(point);
  if (!selected) return;
  state.inspectorDismissed = false;
  if (selected.kind === "universe") {
    // Depth 0 focus — tree stays fully expanded; only dim shifts.
    state.focusedNodeId = null;
    state.selectedNode = selected;
    if (state.view === "universe") {
      drawGraph();
    } else {
      buildGraph();
    }
    renderDetails();
    showInspectorTab("details");
    return;
  }
  if (selected.kind === "project") {
    // Depth 1 focus — always-expanded map; dim other depths/branches.
    state.focusedNodeId = null;
    state.selectedNode = selected;
    const projectId = selected.data?.project_id || selected.projectId;
    const afterSelect = () => {
      state.view = "universe";
      syncPrimaryNavSelection("map");
      state.focusedNodeId = null;
      state.selectedNode = {
        ...selected,
        id: `project:${projectId}`,
        kind: "project",
        depth: 1,
        projectId,
      };
      buildMultiverseGraph();
      state.selectedNode =
        state.graph.nodes.find((item) => item.id === `project:${projectId}`) ||
        state.selectedNode;
      drawGraph();
      renderDetails();
      showInspectorTab("details");
    };
    if (projectId && state.selectedProject?.project_id !== projectId) {
      selectProject(projectId, { revealInspector: true, syncAssets: false })
        .then(afterSelect)
        .catch((error) => toast(error.message, true));
      return;
    }
    afterSelect();
    return;
  }
  if (["system", "related", "focus"].includes(selected.kind)) {
    // Depth 2 focus on multiverse: never dig into interior-only graph.
    state.focusedNodeId = null;
    state.selectedNode = selected;
    const projectId =
      selected.projectId ||
      selected.data?.project_id ||
      state.selectedProject?.project_id;
    const stay = () => {
      state.view = "universe";
      state.focusedNodeId = null;
      const keepId = selected.id;
      buildMultiverseGraph();
      state.selectedNode =
        state.graph.nodes.find((item) => item.id === keepId) || selected;
      drawGraph();
      renderDetails();
      showInspectorTab("details");
    };
    if (projectId && state.selectedProject?.project_id !== projectId) {
      selectProject(projectId, { revealInspector: true, syncAssets: false })
        .then(stay)
        .catch((error) => toast(error.message, true));
      return;
    }
    if (state.view === "universe") {
      drawGraph();
      renderDetails();
      showInspectorTab("details");
      return;
    }
    stay();
    return;
  }
  state.selectedNode = selected;
  drawGraph();
  renderDetails();
  showInspectorTab("details");
}

function renderDetails() {
  elements.details.replaceChildren();
  const selected = state.selectedNode;
  if (!state.selectedProject && !selected) {
    document.body.classList.remove("inspector-open");
    elements.details.append(
      node(
        "p",
        "empty-copy",
        "Multiverse map — click Universe hub or a project node"
      )
    );
    return;
  }
  // Project / hub selection keeps inspector available unless dismissed.
  document.body.classList.toggle(
    "inspector-open",
    Boolean(state.selectedProject || selected) && !state.inspectorDismissed
  );
  const heading = node("div", "detail-group");
  heading.append(
    node(
      "h2",
      "",
      selected?.label ||
        projectDisplayName(state.selectedProject) ||
        state.selectedProject?.project_id ||
        "Universe"
    )
  );
  const grid = node("dl", "detail-grid");
  const data =
    selected?.data || state.projection?.project || state.selectedProject || {};
  addDetail(grid, "Type", selected?.kind || "project");
  addDetail(
    grid,
    "State",
    selected?.kind === "predicted" ? "USER_SELECTION_REQUIRED" : "CURRENT"
  );
  if (data.kind) addDetail(grid, "Kind", data.kind);
  if (data.role) addDetail(grid, "Role", data.role);
  if (data.network_role) addDetail(grid, "Network role", data.network_role);
  if (data.display_name) addDetail(grid, "Display", data.display_name);
  if (data.note) addDetail(grid, "Note", data.note);
  if (data.goal) addDetail(grid, "Goal", data.goal);
  if (data.technologies?.length) addDetail(grid, "Technologies", data.technologies.join(", "));
  if (data.node_ids?.length) addDetail(grid, "Related to", data.node_ids.join(", "));
  if (data.path) addDetail(grid, "Path", data.path);
  if (data.project_root) addDetail(grid, "Root", data.project_root);
  if (data.project_id) addDetail(grid, "Project id", data.project_id);
  if (data.symbol) addDetail(grid, "Symbol", data.symbol);
  if (
    selected?.kind === "project" &&
    state.projection?.source?.commit &&
    selected?.data?.project_id === state.selectedProject?.project_id
  ) {
    addDetail(grid, "Source commit", state.projection.source.commit);
  }
  if (selected?.kind === "universe") {
    addDetail(grid, "Projects", String((state.projects || []).length));
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
    // Marker is CSS ::before — do not prepend an extra empty span
    // (it steals the content column and collapses copy into a 10px vertical strip).
    row.append(copy);
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
    row.append(copy);
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
    row.append(copy);
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
    state.roomMessages = dedupeRoomMessages([
      ...state.roomMessages.filter(
        (message) => message.message_id !== result.message.message_id
      ),
      result.message,
    ]);
    if (result.status === "GOVERNANCE_APPROVAL_SELECTION_REQUIRED") {
      state.governanceProposals = result.pending_proposals || [];
      mergeGovernanceProposalInbox(
        targetProject.project_id,
        state.governanceProposals
      );
      expandConversationLayer();
      renderComposerState();
      renderRoomMessages();
      toast("Choose the Proposal to approve; nothing was approved", true);
      return;
    }
    if (
      result.status === "GOVERNANCE_PROPOSAL_APPROVED_FROM_COMMANDER_TEXT"
    ) {
      await selectProject(targetProject.project_id);
      expandConversationLayer();
      renderComposerState();
      renderRoomMessages();
      toast("Commander approval recorded and delivered to Project Master");
      return;
    }
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
        todoBelongsToProject(todo, state.selectedProject.project_id) &&
        todo.node_ref === nodeRef
    );
  }
  if (state.selectedProject) {
    return state.todos.filter((todo) =>
      todoBelongsToProject(todo, state.selectedProject.project_id)
    );
  }
  return state.todos.filter(
    (todo) =>
      todo.scope_kind === "UNIVERSE" || !normalizeTodoProjectId(todo.project_id)
  );
}

function openTodosForGraphNode(graphNode) {
  if (graphNode?.kind === "project") {
    // Count against the graph project's id, not the currently selected one.
    const projectId =
      graphNode.data?.project_id ||
      String(graphNode.id || "").replace(/^project:/, "");
    if (!projectId) return [];
    return state.todos.filter(
      (todo) =>
        todoBelongsToProject(todo, projectId) && todo.state !== "DONE"
    );
  }
  const nodeRef = graphNode?.data?.node_id;
  if (!nodeRef) return [];
  const projectId =
    graphNode.projectId ||
    graphNode.data?.project_id ||
    state.selectedProject?.project_id;
  if (!projectId) return [];
  return state.todos.filter(
    (todo) =>
      todo.scope_kind === "NODE" &&
      todoBelongsToProject(todo, projectId) &&
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
  if (elements.todoScopeFilter && state.selectedProject) {
    elements.todoScopeFilter.value = "PROJECT";
  }
  renderTodos();
  setTodoTab(prefill ? "create" : "board");
  elements.todoDialog.showModal();
  if (prefill) elements.todoTitle.focus();
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
  setTodoTab("create");
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

function normalizeTodoProjectId(value) {
  if (value == null || value === "" || value === "UNKNOWN" || value === "NONE") {
    return null;
  }
  return String(value);
}

function todoBelongsToProject(todo, projectId) {
  const want = normalizeTodoProjectId(projectId);
  const have = normalizeTodoProjectId(todo?.project_id);
  if (!want) return false;
  if (!have) return false;
  return have === want;
}

/**
 * Board visibility rules:
 * - With a selected project: never show another project's PROJECT/NODE todos.
 * - UNIVERSE-scoped todos only when filter is ALL or UNIVERSE.
 * - Without selection: UNIVERSE board only (no project dump).
 */
function visibleTodos() {
  const scope = elements.todoScopeFilter?.value || "PROJECT";
  const stateFilter = elements.todoStateFilter?.value || "OPEN";
  const priorityFilter = elements.todoPriorityFilter?.value || "ALL";
  const selectedProjectId = normalizeTodoProjectId(
    state.selectedProject?.project_id
  );
  return state.todos.filter((todo) => {
    if (stateFilter === "OPEN" && todo.state === "DONE") return false;
    if (stateFilter === "DONE" && todo.state !== "DONE") return false;
    if (priorityFilter !== "ALL" && todo.priority !== priorityFilter) {
      return false;
    }

    const kind = String(todo.scope_kind || "").toUpperCase();
    const isUniverse = kind === "UNIVERSE" || !normalizeTodoProjectId(todo.project_id);

    if (selectedProjectId) {
      // Hard isolation: foreign project-bound todos never appear.
      if (!isUniverse && !todoBelongsToProject(todo, selectedProjectId)) {
        return false;
      }
      if (scope === "UNIVERSE") return isUniverse;
      if (scope === "NODE") {
        return (
          kind === "NODE" &&
          todoBelongsToProject(todo, selectedProjectId) &&
          Boolean(selectedNodeRef()) &&
          todo.node_ref === selectedNodeRef()
        );
      }
      if (scope === "PROJECT") {
        // Selected-project board: PROJECT + NODE for this project only.
        return !isUniverse && todoBelongsToProject(todo, selectedProjectId);
      }
      // ALL (this context): this project's items + UNIVERSE items.
      if (isUniverse) return true;
      return todoBelongsToProject(todo, selectedProjectId);
    }

    // No project selected.
    if (scope === "PROJECT" || scope === "NODE") return false;
    return isUniverse;
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
  if (!todos.length) {
    const empty = node("div", "todo-empty-block");
    empty.append(
      node(
        "p",
        "empty-copy todo-empty",
        state.selectedProject
          ? "No Todo for this project yet — add one (docs only as references in detail)"
          : "No Todo matches this view"
      )
    );
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
  // Create fields live on the Create tab; open it if user submitted elsewhere.
  if (state.todoTab !== "create") setTodoTab("create");
  const form = new FormData(elements.todoForm);
  const scopeKind = String(form.get("scope_kind"));
  const title = String(form.get("title") || "").trim();
  if (!title) {
    elements.todoFormError.textContent = "Title is required";
    elements.todoTitle.focus();
    return;
  }
  const body = {
    scope_kind: scopeKind,
    title,
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
    setTodoTab("board");
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
          `${observation.outcome || "UNKNOWN"} / ${observation.skill?.skill_id || "skill"} / ${observation.execution_context?.worker_role || "UNKNOWN"} / ${observation.execution_context?.node_ref || "UNKNOWN"} / ${observation.execution_context?.failure_kind || "UNKNOWN"} / ${observation.observed_at || ""}`
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
        typeof duration === "number" ? ` / ${Math.round(duration)}ms` : "";
      list.append(
        node(
          "li",
          "",
          `${row.skill?.skill_id || "skill"} / ${row.provider_ref || "UNKNOWN"} / ${row.worker_role || "UNKNOWN"} / ${row.task_kind || "UNKNOWN"} / n=${row.observation_count || 0} / ok=${succeeded} fail=${failed} quota=${row.quota_states?.EXHAUSTED || 0}${durationText}`
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
    node("h3", "", `Worker and Skill compare (${comparisons.length})`)
  );
  if (!comparisons.length) {
    compareGroup.append(
      node(
        "p",
        "empty-copy",
        "No comparison rows yet. Comparisons aggregate redacted Skill observations by Worker, task, node, skill, model, provider, or project."
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
          ? ` / avg ${Math.round(row.avg_duration_ms)}ms`
          : "";
      const label =
        row.group_by === "worker"
          ? `${row.label?.provider_ref || "UNKNOWN"}/${compactModelRef(row.label?.model_ref)}/${row.label?.worker_role || "UNKNOWN"}`
          : row.label?.skill_id ||
            row.label?.model_ref ||
            row.label?.provider_ref ||
            row.label?.worker_role ||
            row.label?.task_kind ||
            row.label?.node_ref ||
            row.label?.project_id ||
            row.group_key ||
            "group";
      list.append(
        node(
          "li",
          "",
          `${label} / n=${row.observation_count || 0} / success=${rate} / quota=${row.quota_exhausted_count || 0}${dur}`
        )
      );
    }
    compareGroup.append(list);
    const groups = ["worker", "task", "node", "skill", "model", "provider", "project"];
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

let refreshConductorPanel = () => {};
let refreshLawStrip = () => {};

function bindEvents() {
  document
    .querySelector("#refresh-button")
    .addEventListener("click", () => refresh({ syncSelectedProject: true }));
  elements.todoButton.addEventListener("click", () => openTodoDialog(false));
  elements.todoForm.addEventListener("submit", submitTodo);
  elements.todoScope.addEventListener("change", renderTodoScopeControls);
  elements.todoProject.addEventListener("change", renderTodoScopeControls);
  elements.todoScopeFilter.addEventListener("change", renderTodos);
  elements.todoStateFilter.addEventListener("change", renderTodos);
  if (elements.todoPriorityFilter) {
    elements.todoPriorityFilter.addEventListener("change", renderTodos);
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
  if (elements.settingsDialog) {
    elements.settingsDialog.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-settings-tab]");
      if (!tab || !elements.settingsDialog.contains(tab)) return;
      event.preventDefault();
      setSettingsTab(tab.getAttribute("data-settings-tab"));
    });
  }
  const openSessionObservatory = async () => {
    try {
      await refreshSupervisorSessions();
      setObservatoryTab(state.observatoryTab || "sessions");
      elements.sessionObservatoryDialog.showModal();
    } catch (error) {
      toast(error.message, true);
    }
  };
  if (elements.sessionObservatoryDialog) {
    elements.sessionObservatoryDialog.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-observatory-tab]");
      if (!tab || !elements.sessionObservatoryDialog.contains(tab)) return;
      event.preventDefault();
      setObservatoryTab(tab.getAttribute("data-observatory-tab"));
    });
  }
  if (elements.todoDialog) {
    elements.todoDialog.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-todo-tab]");
      if (!tab || !elements.todoDialog.contains(tab)) return;
      event.preventDefault();
      setTodoTab(tab.getAttribute("data-todo-tab"));
    });
  }
  elements.sessionObservatoryButton.addEventListener(
    "click",
    openSessionObservatory
  );
  elements.sessionObservatoryTopbarButton.addEventListener(
    "click",
    openSessionObservatory
  );
  elements.refreshSessionsButton.addEventListener("click", () => {
    refreshSupervisorSessions().catch((error) => toast(error.message, true));
  });
  if (elements.sessionRailSearch) {
    elements.sessionRailSearch.addEventListener("input", () => {
      state.providerChatSearch = elements.sessionRailSearch.value;
      renderSessionRail();
    });
  }
  if (elements.sessionRailShowWorkers) {
    elements.sessionRailShowWorkers.addEventListener("change", () => {
      state.providerChatShowWorkers = elements.sessionRailShowWorkers.checked;
      renderSessionRail();
    });
  }
  if (elements.sessionRailShowHidden) {
    elements.sessionRailShowHidden.addEventListener("change", () => {
      state.providerChatShowHidden = elements.sessionRailShowHidden.checked;
      renderSessionRail();
    });
  }
  if (elements.sessionSummaryOpen) {
    elements.sessionSummaryOpen.addEventListener("click", async () => {
      const room = (state.providerChatRooms || []).find(
        (item) => item.chat_key === state.selectedProviderChatKey
      );
      const session = supervisorSessionForRoom(room);
      if (!session) return;
      try {
        elements.sessionSummaryDialog.close();
        await activateAnchorSession(session);
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  if (elements.sessionSummaryManage) {
    elements.sessionSummaryManage.addEventListener("click", async () => {
      const room = (state.providerChatRooms || []).find(
        (item) => item.chat_key === state.selectedProviderChatKey
      );
      if (!room) return;
      const session = supervisorSessionForRoom(room);
      elements.sessionSummaryDialog.close();
      if (session) {
        state.selectedSupervisorAnchorKey = anchorSessionKey(session);
        state.observatoryTab = "sessions";
        setObservatoryTab("sessions");
        renderSessionObservatory();
      } else {
        state.observatoryTab = "activity";
        setObservatoryTab("activity");
      }
      if (!elements.sessionObservatoryDialog.open) {
        elements.sessionObservatoryDialog.showModal();
      }
      if (!session) {
        try {
          await discoverProviderActivitySources();
        } catch (error) {
          toast(error.message, true);
        }
      }
    });
  }
  if (elements.discoverProviderActivity) {
    elements.discoverProviderActivity.addEventListener("click", () => {
      discoverProviderActivitySources().catch((error) => toast(error.message, true));
    });
  }
  elements.discoverHostTools.addEventListener("click", () => {
    discoverHostTools().catch((error) => toast(error.message, true));
  });
  if (elements.refreshProviderModels) {
    elements.refreshProviderModels.addEventListener("click", () => {
      refreshProviderModels().catch((error) => toast(error.message, true));
    });
  }
  elements.remoteAccessTransport.addEventListener("change", () => {
    elements.remoteConnectorFields.classList.toggle(
      "hidden",
      elements.remoteAccessTransport.value !== "SSH_REVERSE_TUNNEL"
    );
  });
  elements.startRemoteAccess.addEventListener("click", () => {
    setRemoteAccess("start").catch((error) => {
      elements.settingsError.textContent = error.message;
    });
  });
  elements.stopRemoteAccess.addEventListener("click", () => {
    setRemoteAccess("stop").catch((error) => {
      elements.settingsError.textContent = error.message;
    });
  });
  elements.createPairing.addEventListener("click", () => {
    createRemotePairing().catch((error) => {
      elements.settingsError.textContent = error.message;
    });
  });
  elements.remotePairingList.addEventListener("click", (event) => {
    const action = event.target.closest("[data-pairing-id]");
    if (!action) return;
    decideRemotePairing(action.dataset.pairingId, action.dataset.decision).catch(
      (error) => {
        elements.settingsError.textContent = error.message;
      }
    );
  });
  elements.remoteDeviceList.addEventListener("click", (event) => {
    const action = event.target.closest("[data-device-id]");
    if (!action) return;
    revokeRemoteDevice(action.dataset.deviceId).catch((error) => {
      elements.settingsError.textContent = error.message;
    });
  });
  if (elements.refreshRendezvous) {
    elements.refreshRendezvous.addEventListener("click", () => {
      refreshRendezvousSettings().catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.stopRendezvous) {
    elements.stopRendezvous.addEventListener("click", () => {
      stopRendezvousClient().catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.rendezvousPendingList) {
    elements.rendezvousPendingList.addEventListener("click", (event) => {
      const action = event.target.closest("[data-rendezvous-request-id]");
      if (!action) return;
      decideRendezvousRequest(
        action.dataset.rendezvousRequestId,
        action.dataset.decision
      ).catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.refreshRooms) {
    elements.refreshRooms.addEventListener("click", () => {
      refreshMultiRooms().catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.createMeetingRoom) {
    elements.createMeetingRoom.addEventListener("click", () => {
      createMeetingRoomThin().catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.postRoomMessage) {
    elements.postRoomMessage.addEventListener("click", () => {
      postActiveRoomAsUser().catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.callMasterButton) {
    elements.callMasterButton.addEventListener("click", () => {
      callMasterOnActiveRoom().catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.injectSessionRefButton) {
    elements.injectSessionRefButton.addEventListener("click", () => {
      injectSessionRefThin().catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
  }
  if (elements.observatoryInjectButton) {
    elements.observatoryInjectButton.addEventListener("click", () => {
      injectSessionFromObservatory().catch((error) => {
        if (elements.observatoryInjectStatus) {
          elements.observatoryInjectStatus.textContent = error.message;
        }
        toast(error.message, true);
      });
    });
  }
  if (elements.observatoryShowAllToggle) {
    elements.observatoryShowAllToggle.addEventListener("change", () => {
      state.observatoryShowAll = Boolean(
        elements.observatoryShowAllToggle.checked
      );
      renderSessionObservatory();
    });
  }
  if (elements.cleanupSessionsButton) {
    elements.cleanupSessionsButton.addEventListener("click", () => {
      cleanupSupervisorSessions().catch((error) => toast(error.message, true));
    });
  }
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
  elements.workerBindingScope.addEventListener("change", renderWorkerBindingSettings);
  elements.settingsForm.addEventListener("submit", submitProviderSettings);

  document.querySelectorAll(".ghost-action[data-primary-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.getAttribute("data-primary-view");
      const nav = elements.primaryNav?.querySelector(`[data-primary-view="${view}"]`);
      if (nav) nav.click();
      else if (["memory", "future", "bench", "activity", "details"].includes(view)) {
        openInspectorSurface(view);
      } else if (view === "map" || view === "universe") {
        showGraphView("universe");
      }
    });
  });
  const fitBtn = document.querySelector("#action-fit-map");
  if (fitBtn) {
    fitBtn.addEventListener("click", () => {
      showGraphView("universe");
      fitGraphView();
    });
  }
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
      // Graph surfaces (single place — not also on left rail / toolbar).
      if (view === "map" || view === "network" || view === "project" || view === "ecosystem") {
        showGraphView("universe");
        if (view === "ecosystem") {
          // Project list lives in the left rail — just focus map + list context.
          elements.projectList?.focus?.();
        }
        return;
      }
      if (view === "timeline" || view === "documents") {
        showGraphView(view);
        return;
      }
      // Project-context panels live only in the inspector.
      if (["memory", "future", "bench", "activity", "details"].includes(view)) {
        openInspectorSurface(view);
        return;
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

refreshConductorPanel = function () {
  const projectCount = (state.projects || []).length;
  const dispatchCount = (state.dispatches || []).length;
  // Metric: open todos for selected project only (not global GCS dump).
  const scopedTodoCount = state.selectedProject
    ? state.todos.filter(
        (todo) =>
          todoBelongsToProject(todo, state.selectedProject.project_id) &&
          todo.state !== "DONE"
      ).length
    : state.todos.filter(
        (todo) =>
          (todo.scope_kind === "UNIVERSE" || !todo.project_id) &&
          todo.state !== "DONE"
      ).length;
  if (elements.metricProjects) elements.metricProjects.textContent = String(projectCount);
  if (elements.metricTodos) {
    elements.metricTodos.textContent = String(scopedTodoCount);
  }
  if (elements.metricDispatches) elements.metricDispatches.textContent = String(dispatchCount);
  if (elements.metricService) {
    const ready = elements.serviceStatus?.dataset?.state === "ready";
    elements.metricService.textContent = ready ? "READY" : "…";
  }
  if (elements.conductorSummaryLine) {
    const service = elements.metricService?.textContent || "—";
    elements.conductorSummaryLine.textContent =
      `P ${projectCount} · T ${scopedTodoCount} · D ${dispatchCount} · ${service}`;
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
};

refreshLawStrip = function () {
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
  };
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

  // Graph mode lives on top primary nav (showGraphView). Legacy [data-view]
  // controls were removed to stop Map/Timeline/Memory duplicates.
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => {
      showGraphView(button.dataset.view);
    });
  });
  syncPrimaryNavSelection(
    state.view === "universe" ? "map" : state.view
  );
  for (const button of document.querySelectorAll("[data-tab]")) {
    button.addEventListener("click", () => {
      const tab = button.dataset.tab;
      showInspectorTab(tab);
      // Keep top nav in sync when switching inspector tabs from the panel itself.
      if (["memory", "future", "bench"].includes(tab)) {
        syncPrimaryNavSelection(tab);
      } else if (state.view === "universe") {
        syncPrimaryNavSelection("map");
      } else if (state.view === "timeline" || state.view === "documents") {
        syncPrimaryNavSelection(state.view);
      }
    });
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
    if (pan && pan.pointerId === event.pointerId) {
      const dx = event.clientX - pan.startX;
      const dy = event.clientY - pan.startY;
      if (Math.hypot(dx, dy) > 3) {
        pan.moved = true;
        elements.canvas.classList.add("is-panning");
        updateGraphHoverTooltip(null, null);
      }
      if (pan.moved) {
        state.graph.x = pan.originX + dx;
        state.graph.y = pan.originY + dy;
        drawGraph();
        return;
      }
    }
    handleGraphPointerHover(event);
  });
  elements.canvas.addEventListener("pointerleave", () => {
    if (!state.hoveredNodeId) {
      updateGraphHoverTooltip(null, null);
      return;
    }
    state.hoveredNodeId = null;
    updateGraphHoverTooltip(null, null);
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
refresh().finally(openConductorRoomStream);
window.setInterval(refreshConductorRoom, 1200);
state.providerTailTimer = window.setInterval(tailProviderSessions, 4000);
