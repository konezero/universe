"use strict";

const localControlToken =
  new URLSearchParams(window.location.search).get("token") || "";

const state = {
  projects: [],
  todos: [],
  goals: [],
  unassignedTodos: [],
  expandedGoals: {},
  selectedProject: null,
  projection: null,
  /** Read-only Mode Anchor → Session Anchor → Task Frame projection. */
  sessionGraph: null,
  /** Read-only typed projection from existing project stores. */
  semanticGraph: null,
  /** project_id -> projection; multiverse always expands from this cache */
  projectionsByProject: {},
  dispatches: [],
  releases: [],
  releaseProposals: [],
  selectedReleaseTargetProjectId: null,
  masterHandoffs: [],
  skillPlanAdoptions: [],
  skillObservations: [],
  skillBench: [],
  skillGapSummary: null,
  skillCandidates: [],
  experienceCases: [],
  benchComparisons: [],
  experiencePatterns: [],
  contextPacks: [],
  memories: [],
  memoryProposals: [],
  memoryBatchConfigs: [],
  memoryBatchRuns: [],
  memoryCandidates: [],
  memoryCandidateFilters: { stage: "", kind: "", state: "REVIEW_REQUIRED" },
  workLoop: null,
  selectedNode: null,
  focusedNodeId: null,
  view: "universe",
  roomMessages: [],
  conductorMessages: [],
  conductorDelegations: [],
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
  /** COMMIT/PUSH milestones for the selected Actions target. */
  gitWorkHistory: [],
  masterBridge: null,
  modeContract: null,
  providerSettings: null,

  /** CLI+preset model catalog from /v1/settings/provider-models */
  providerModels: null,
  workerBindings: null,
  hostTools: null,
  runtimePreflight: null,
  runtimeAudit: null,
  supervisorRefreshPromise: null,
  supervisorRefreshedAt: 0,
  remoteAccess: null,
  accessSurface: "LOCAL_BROWSER",
  supervisorSessions: [],
  supervisorTerminals: [],
  roomSessionBindings: [],
  selectedSupervisorAnchorKey: null,
  /** A mode can expose many persistent sessions; this is its one chat selection. */
  selectedSupervisorAnchorKeysByMode: {},
  /** Cross-session work is a routed handoff, never a second direct-chat target. */
  sessionDelegationDraft: null,
  sessionDelegations: [],
  /** Expanded left-rail mode whose persistent session cards are visible. */
  selectedModeCoordinateKey: null,
  sessionBusUnread: {},
  pendingSessionBus: null,
  sessionBusProjection: "INBOX",
  observatoryShowAll: false,
  /** Expanded (node|mode) groups so operators can pick an alternate 1:1 session. */
  observatoryExpandedCoords: {},
  settingsTab: "service",
  observatoryTab: "sessions",
  todoTab: "board",
  supervisorEvents: [],
  providerActivitySources: [],
  providerActivityDiscoveries: [],
  providerChatRooms: [],
  projectAnchorSessions: [],
  terminals: [],
  activeTerminalId: null,
  terminalSurfaces: {},
  providerChatSearch: "",
  providerChatShowWorkers: false,
  providerChatShowHidden: false,
  providerChatExpandedProjects: {},
  providerChatExpandedBranches: {},
  selectedProviderChatKey: null,
  /** Ephemeral redacted provider text keyed by opaque provider chat key. */
  providerLiveDeltas: {},
  /** Last delivery mode observed for each opaque provider chat key. */
  providerLiveDelivery: {},
  multiRooms: [],
  activeMultiRoomId: null,
  activeMultiRoomSnapshot: null,
  multiRoomStream: null,
  multiRoomLiveOutput: {},
  providerTailTimer: null,
  providerTailInFlight: false,
  /** Ephemeral redacted provider-session cache keyed by opaque chat key. */
  providerSessionRoomCaches: {},
  /** Independent EventSource ownership keyed by opaque chat key. */
  providerSessionStreams: {},
  providerSessionStreamStates: {},
  providerSessionMessages: [],
  providerSessionPermissions: [],
  providerSessionConnection: null,
  providerSessionStreamState: "IDLE",
  conversationSurface: "CHAT",
  conversationTarget: {
    kind: "NONE",
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
  chatPanelWidth: 380,
  selectedGoalId: null,
};

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  modeStatus: document.querySelector("#mode-status"),
  projectList: document.querySelector("#project-list"),
  nodeModeList: document.querySelector("#node-mode-list"),
  nodeModeCount: document.querySelector("#node-mode-count"),
  workspaceTitle: document.querySelector("#workspace-title"),
  workspaceSubtitle: document.querySelector("#workspace-subtitle"),
  canvas: document.querySelector("#universe-graph"),
  graphEmpty: document.querySelector("#graph-empty"),
  graphLegend: document.querySelector("#graph-legend"),
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
  projectRootBrowse: document.querySelector("#project-root-browse"),
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
  sessionSummaryLive: document.querySelector("#session-summary-live"),
  sessionSummaryConnection: document.querySelector("#session-summary-connection"),
  sessionSummaryProvider: document.querySelector("#session-summary-provider"),
  sessionSummaryModel: document.querySelector("#session-summary-model"),
  sessionSummaryEffort: document.querySelector("#session-summary-effort"),
  sessionSummaryConnectionStatus: document.querySelector(
    "#session-summary-connection-status"
  ),
  sessionSummaryConnect: document.querySelector("#session-summary-connect"),
  sessionSummaryNew: document.querySelector("#session-summary-new"),
  sessionSummaryOpen: document.querySelector("#session-summary-open"),
  sessionSummaryManage: document.querySelector("#session-summary-manage"),
  nodeSessionActionDialog: document.querySelector("#node-session-action-dialog"),
  nodeSessionActionTitle: document.querySelector("#node-session-action-title"),
  nodeSessionActionSubtitle: document.querySelector("#node-session-action-subtitle"),
  nodeSessionInspect: document.querySelector("#node-session-inspect"),
  nodeSessionInbox: document.querySelector("#node-session-inbox"),
  nodeSessionOpen: document.querySelector("#node-session-open"),
  nodeSessionStop: document.querySelector("#node-session-stop"),
  sessionBusDialog: document.querySelector("#session-bus-dialog"),
  sessionBusTitle: document.querySelector("#session-bus-title"),
  sessionBusSubtitle: document.querySelector("#session-bus-subtitle"),
  sessionBusMessages: document.querySelector("#session-bus-messages"),
  sessionBusTabs: Array.from(document.querySelectorAll("[data-session-bus-projection]")),
  sessionBusCompose: document.querySelector("#session-bus-compose"),
  sessionBusBody: document.querySelector("#session-bus-body"),
  sessionObservatoryDetail: document.querySelector("#session-observatory-detail"),
  sessionObservatoryDetailMeta: document.querySelector(
    "#session-observatory-detail-meta"
  ),
  sessionWorkingDirectory: document.querySelector("#session-working-directory"),
  sessionWorkingDirectoryProject: document.querySelector(
    "#session-working-directory-project"
  ),
  sessionWorkingDirectoryApply: document.querySelector(
    "#session-working-directory-apply"
  ),
  sessionWorkingDirectoryStatus: document.querySelector(
    "#session-working-directory-status"
  ),
  sessionObservatoryDetailPreview: document.querySelector(
    "#session-observatory-detail-preview"
  ),
  observatoryShowAllToggle: document.querySelector("#observatory-show-all"),
  cleanupSessionsButton: document.querySelector("#cleanup-sessions-button"),
  sessionEventList: document.querySelector("#session-event-list"),
  runtimeAuditGrid: document.querySelector("#runtime-audit-grid"),
  refreshSessionsButton: document.querySelector("#refresh-sessions-button"),
  primaryNav: document.querySelector("#primary-nav"),
  goalPlanWorkspace: document.querySelector("#goal-plan-workspace"),
  goalPlanBreadcrumb: document.querySelector("#goal-plan-breadcrumb"),
  goalPlanTitle: document.querySelector("#goal-plan-title"),
  goalPlanSubtitle: document.querySelector("#goal-plan-subtitle"),
  goalPlanSummary: document.querySelector("#goal-plan-summary"),
  goalPlanList: document.querySelector("#goal-plan-list"),
  unassignedWorkList: document.querySelector("#unassigned-work-list"),
  unassignedWorkCount: document.querySelector("#unassigned-work-count"),
  addGoalButton: document.querySelector("#add-goal-button"),
  goalPlanMap: document.querySelector("#goal-plan-map"),
  editSelectedGoal: document.querySelector("#edit-selected-goal"),
  utilityRail: document.querySelector(".utility-rail"),
  addProjectRailButton: document.querySelector("#add-project-rail-button"),
  planProjectButton: document.querySelector("#plan-project-button"),
  projectSubmit: document.querySelector("#project-submit"),
  mobileWorkTabs: document.querySelector(".mobile-work-tabs"),
  mobileDelegateGoal: document.querySelector("#mobile-delegate-goal"),
  mobileEditPlan: document.querySelector("#mobile-edit-plan"),
  mobileAddMilestone: document.querySelector("#mobile-add-milestone"),
  quickNewSessionButton: document.querySelector("#quick-new-session-button"),
  quickConductorButton: document.querySelector("#quick-conductor-button"),
  quickTaskButton: document.querySelector("#quick-task-button"),
  newSessionDialog: document.querySelector("#new-session-dialog"),
  newSessionForm: document.querySelector("#new-session-form"),
  newSessionMode: document.querySelector("#new-session-mode"),
  newSessionProjectRow: document.querySelector("#new-session-project-row"),
  newSessionProject: document.querySelector("#new-session-project"),
  newSessionProvider: document.querySelector("#new-session-provider"),
  newSessionModel: document.querySelector("#new-session-model"),
  newSessionEffort: document.querySelector("#new-session-effort"),
  newSessionStatus: document.querySelector("#new-session-status"),
  newSessionError: document.querySelector("#new-session-error"),
  newSessionSubmit: document.querySelector("#new-session-submit"),
  goalDialog: document.querySelector("#goal-dialog"),
  goalForm: document.querySelector("#goal-form"),
  goalFormError: document.querySelector("#goal-form-error"),
  milestoneDialog: document.querySelector("#milestone-dialog"),
  milestoneForm: document.querySelector("#milestone-form"),
  milestoneFormError: document.querySelector("#milestone-form-error"),
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
  memoryMaintainInterval: document.querySelector("#memory-maintain-interval"),
  memoryMaintainStatus: document.querySelector("#memory-maintain-status"),
  workerBindingScope: document.querySelector("#worker-binding-scope"),
  workerBindingSettings: document.querySelector("#worker-binding-settings"),
  providerModelCatalog: document.querySelector("#provider-model-catalog"),
  refreshProviderModels: document.querySelector("#refresh-provider-models-button"),
  setupProviderHooks: document.querySelector("#setup-provider-hooks-button"),
  setupProviderHooksStatus: document.querySelector("#setup-provider-hooks-status"),
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
  freshProjectRootBrowse: document.querySelector("#fresh-project-root-browse"),
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
  releaseDatabaseBrowse: document.querySelector("#release-database-browse"),
  releaseManifestBrowse: document.querySelector("#release-manifest-browse"),
  releaseTargetProject: document.querySelector("#release-target-project"),
  releaseList: document.querySelector("#release-list"),
  releaseFormError: document.querySelector("#release-form-error"),
  releaseProposalOutput: document.querySelector("#release-proposal-output"),
  conversationLayer: document.querySelector("#conversation-layer"),
  chatResizeHandle: document.querySelector("#chat-resize-handle"),
  conversationToggle: document.querySelector("#conversation-toggle"),
  conversationExpand: document.querySelector("#conversation-expand"),
  actionInboxButton: document.querySelector("#action-inbox-button"),
  actionInboxBadge: document.querySelector("#action-inbox-badge"),
  mobileActionInboxBadge: document.querySelector("#mobile-action-inbox-badge"),
  actionInboxDialog: document.querySelector("#action-inbox-dialog"),
  actionInboxTitle: document.querySelector("#action-inbox-title"),
  actionInboxList: document.querySelector("#action-inbox-list"),
  conversationOpacity: document.querySelector("#conversation-opacity"),
  roomMessageList: document.querySelector("#room-message-list"),
  terminalTabs: document.querySelector("#terminal-tabs"),
  terminalStage: document.querySelector("#terminal-stage"),
  conversationTitle: document.querySelector("#conversation-title"),
  conversationTargetLabel: document.querySelector("#conversation-target-label"),
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

let conversationLayerHome = null;

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
  if (session?.session_anchor_ref) {
    return String(session.session_anchor_ref).trim();
  }
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
    session?.session_anchor_ref ||
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
  const currentness = String(session?.currentness || "").toUpperCase();
  const live = stateName === "LIVE" ? 3 : stateName === "STARTING" ? 2 : 1;
  // CURRENT sessions should surface above stale-but-is_default ones.
  const isCurrent = currentness === "CURRENT" ? 1 : 0;
  // is_default only counts when the session is not stale.
  const isDefault = session?.is_default && currentness !== "STALE" ? 1 : 0;
  return live * 1e15 + isCurrent * 1e14 + isDefault * 1e13 + sessionActivityMs(session);
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
      session.last_activity_at || session.updated_at || session.last_seen_at
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
  if (session) {
    const coordinateKey = nodeModeCoordinateKey(session.node, session.mode);
    state.selectedSupervisorAnchorKeysByMode = {
      ...state.selectedSupervisorAnchorKeysByMode,
      [coordinateKey]: anchorSessionKey(session),
    };
    state.selectedModeCoordinateKey = coordinateKey;
  }
  renderSessionObservatory();
  renderNodeModes();
}

function sessionAnchorRef(session) {
  return String(
    session?.session_anchor_ref ||
      session?.anchor_session?.current_anchor_ref ||
      session?.anchor_ref ||
      ""
  ).trim();
}

function supervisorSessionForAnchorRef(anchorRef) {
  const wanted = String(anchorRef || "").trim();
  if (!wanted) return null;
  return (
    (state.supervisorSessions || []).find(
      (session) => sessionAnchorRef(session) === wanted
    ) || null
  );
}

function isCompletedSessionDelegation(delegation) {
  return ["COMPLETED", "SUCCEEDED", "RESULT_READY"].includes(
    String(delegation?.state || delegation?.status || "").toUpperCase()
  );
}

function rememberSessionDelegation(delegation) {
  if (!delegation?.delegation_id) return delegation;
  state.sessionDelegations = [
    delegation,
    ...(state.sessionDelegations || []).filter(
      (item) => item.delegation_id !== delegation.delegation_id
    ),
  ].slice(0, 100);
  return delegation;
}

function normalizeSessionDelegation(delegation, fallback = {}) {
  const request = delegation?.request || {};
  return {
    ...(delegation || {}),
    project_id: delegation?.project_id || request.project_id || fallback.project_id || null,
    origin_anchor_ref:
      request.origin_session_anchor_ref || fallback.origin_anchor_ref || "",
    target_anchor_ref:
      request.target_session_anchor_ref || fallback.target_anchor_ref || "",
  };
}

function watchSessionDelegation(delegationId, fallback, attempt = 0) {
  if (!delegationId || attempt >= 120) return;
  window.setTimeout(async () => {
    try {
      const result = await api(
        `/v1/conductor/delegations/${encodeURIComponent(delegationId)}`
      );
      const delegation = rememberSessionDelegation(
        normalizeSessionDelegation(result, fallback)
      );
      renderRoomMessages();
      if (isCompletedSessionDelegation(delegation)) {
        await rejoinDelegationOrigin(delegation);
        return;
      }
      if (["FAILED", "CANCELLED"].includes(String(delegation.state || "").toUpperCase())) {
        toast(`Delegation ended with ${delegation.state}`, true);
        return;
      }
      watchSessionDelegation(delegationId, fallback, attempt + 1);
    } catch (error) {
      if (attempt < 119) watchSessionDelegation(delegationId, fallback, attempt + 1);
    }
  }, 1500);
}

async function rejoinDelegationOrigin(delegation) {
  const originAnchorRef = String(delegation?.origin_anchor_ref || "").trim();
  const originSession = supervisorSessionForAnchorRef(originAnchorRef);
  const originRoom = providerChatRoomForSupervisorSession(originSession);
  if (!originSession || !originRoom) {
    toast("Delegation result is ready, but its origin session chat is unavailable", true);
    return false;
  }
  await openProviderChatSession(originRoom, { session: originSession });
  expandConversationLayer();
  toast("Delegation result rejoined the origin session");
  return true;
}

function beginCrossSessionDelegation(targetSession) {
  const origin = state.conversationTarget;
  const originAnchorRef = String(origin?.session_anchor_ref || "").trim();
  const targetAnchorRef = sessionAnchorRef(targetSession);
  if (origin?.kind !== "PROVIDER_SESSION" || !originAnchorRef) {
    toast("Open the origin persistent session before delegating", true);
    return;
  }
  if (!targetAnchorRef || targetAnchorRef === originAnchorRef) {
    toast("Choose a different persistent session as the delegation target", true);
    return;
  }
  state.sessionDelegationDraft = {
    project_id:
      origin.projectId ||
      targetSession.current_project_id ||
      targetSession.project_id ||
      null,
    origin_session_chat_key: origin.chat_key,
    origin_anchor_ref: originAnchorRef,
    target_anchor_ref: targetAnchorRef,
    origin_label: origin.alias || origin.projectId || "Origin session",
    target_label: sessionDisplayName(targetSession),
    state: "DRAFT",
  };
  state.conversationTarget = {
    kind: "SESSION_DELEGATION",
    projectId: origin.projectId || targetSession.node || null,
    origin_anchor_ref: originAnchorRef,
    target_anchor_ref: targetAnchorRef,
  };
  renderNodeModes();
  renderComposerActions();
  renderComposerState();
  renderRoomMessages();
  expandConversationLayer();
  elements.dispatchInstruction.focus();
}

function nodeModeCoordinateKey(nodeId, mode) {
  return `${normalizeNodeModeNode(nodeId).toLowerCase()}::${String(
    mode || ""
  )
    .trim()
    .toUpperCase()}`;
}

function projectForVendorWorkspace(room) {
  const workspaceName = String(room?.workspace_name || "").trim().toLowerCase();
  if (!workspaceName) return null;
  return (
    visibleProjects().find((project) => {
      const id = String(project.project_id || "").toLowerCase();
      const root = String(project.project_root || "").replaceAll("\\", "/").toLowerCase();
      const rootName = root.split("/").filter(Boolean).pop() || "";
      return id === workspaceName || rootName === workspaceName;
    }) || null
  );
}

function unboundVendorSessionFromRoom(room, project, mode) {
  const chatKey = String(room?.chat_key || "").trim();
  return {
    provider: String(room?.provider || "UNKNOWN").toUpperCase(),
    node: project.project_id,
    mode,
    currentness: String(room?.binding?.observer_currentness || "UNKNOWN").toUpperCase(),
    observer_currentness: String(room?.binding?.observer_currentness || "UNKNOWN").toUpperCase(),
    session_anchor_ref: room?.binding?.session_anchor_ref || `vendor:${chatKey}`,
    last_seen_at: room?.last_activity_at || null,
    alias: room?.binding?.alias || `${String(room?.provider || "Vendor").toUpperCase()} ${room?.workspace_name || project.project_id}`,
    chat_key: chatKey,
    vendor_unbound: String(room?.binding?.state || "").toUpperCase() === "INDEPENDENT",
  };
}

function markdownBody(text) {
  const root = node("div", "markdown-body");
  const lines = String(text || "").replaceAll("\r\n", "\n").split("\n");
  let code = null;
  for (const line of lines) {
    if (line.startsWith("```")) {
      if (code) {
        root.append(code);
        code = null;
      } else {
        code = node("pre", "markdown-code");
      }
      continue;
    }
    if (code) {
      code.textContent += `${line}\n`;
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      root.append(node(`h${heading[1].length + 2}`, "", heading[2]));
    } else if (/^[-*]\s+/.test(line)) {
      root.append(node("div", "markdown-list-item", line.replace(/^[-*]\s+/, "")));
    } else if (line.trim()) {
      root.append(node("p", "", line));
    }
  }
  if (code) root.append(code);
  return root;
}

function renderSessionWorkingDirectory(session) {
  const panel = elements.sessionWorkingDirectory;
  const select = elements.sessionWorkingDirectoryProject;
  const apply = elements.sessionWorkingDirectoryApply;
  const status = elements.sessionWorkingDirectoryStatus;
  if (!panel || !select || !apply || !status) return;
  const movable =
    session?.mode === "MASTER" &&
    Boolean(session?.provider_session_attached) &&
    !["WORKER", "BOSS"].includes(String(session?.session_kind || "").toUpperCase());
  panel.classList.toggle("hidden", !movable);
  if (!movable) {
    select.replaceChildren();
    status.textContent = "";
    return;
  }
  const currentProject = String(
    session.current_project_id || session.current_location?.project_id || session.node || ""
  );
  const priorSelection = select.value;
  select.replaceChildren();
  for (const project of state.projects || []) {
    const option = document.createElement("option");
    option.value = project.project_id;
    option.textContent = project.project_id;
    select.append(option);
  }
  select.value =
    [...select.options].some((option) => option.value === priorSelection)
      ? priorSelection
      : currentProject;
  apply.disabled = !select.value;
  status.textContent = currentProject
    ? `Current: ${currentProject}`
    : "Choose a registered project.";
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
  renderSessionWorkingDirectory(session);
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
  } else {
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

  detail.querySelector(".session-detail-live")?.remove();
  const room = providerChatRoomForSupervisorSession(session);
  if (!room) return;

  const live = node("section", "session-detail-live");
  const heading = node("div", "session-detail-live-heading");
  heading.append(
    node("strong", "", "Live provider output"),
    node("span", "", String(room.provider || "UNKNOWN").toUpperCase())
  );
  live.append(heading);

  const feed = node("div", "session-detail-live-feed");
  const deltas = state.providerLiveDeltas[room.chat_key] || [];
  if (!deltas.length) {
    feed.append(
      node("p", "session-detail-live-empty", providerLiveDeliveryLabel(room))
    );
  } else {
    for (const delta of deltas.slice(-10)) {
      const line = node("article", "session-detail-live-line");
      line.append(
        node("small", "", String(delta.role || "UNKNOWN")),
        node("p", "", String(delta.text || ""))
      );
      feed.append(line);
    }
  }
  live.append(feed);
  detail.append(live);
}

async function rebindSelectedSessionWorkingDirectory() {
  const session = (state.supervisorSessions || []).find(
    (item) => anchorSessionKey(item) === state.selectedSupervisorAnchorKey
  );
  const projectId = String(elements.sessionWorkingDirectoryProject?.value || "");
  const sessionId = String(
    session?.session_id || session?.universe_session_id || ""
  );
  if (!session || !sessionId || session.mode !== "MASTER" || !projectId) {
    throw new Error("Choose a persistent Project Master session and project");
  }
  elements.sessionWorkingDirectoryApply.disabled = true;
  elements.sessionWorkingDirectoryStatus.textContent = `Moving to ${projectId}...`;
  try {
    await api(
      `/v1/sessions/${encodeURIComponent(sessionId)}/working-directory`,
      {
        method: "POST",
        body: {
          project_id: projectId,
          expected_version: session.row_version,
        },
      }
    );
    await selectProject(projectId);
    await openPreparedProviderSession({
      mode: "MASTER",
      projectId,
      anchorKey: anchorSessionKey(session),
    });
    toast(`Session moved to ${projectId}`);
  } finally {
    elements.sessionWorkingDirectoryApply.disabled = false;
  }
}


async function api(path, options = {}) {
  const headers = options.body ? { "Content-Type": "application/json" } : {};
  if (options.controlToken) {
    if (!localControlToken) throw new Error("Local operator token is unavailable");
    headers.Authorization = `Bearer ${localControlToken}`;
  }
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

async function refreshSupervisorSessions({ maxAgeMs = 0 } = {}) {
  if (state.supervisorRefreshPromise) return state.supervisorRefreshPromise;
  const maxAge = Math.max(0, Number(maxAgeMs) || 0);
  if (
    maxAge > 0 &&
    state.supervisorRefreshedAt > 0 &&
    Date.now() - state.supervisorRefreshedAt < maxAge
  ) {
    return state.runtimeAudit;
  }

  const refreshPromise = (async () => {
    const [audit, activity, chatCatalog, sessionGraph, busUnread] = await Promise.all([
      api("/v1/runtime/audit"),
      api("/v1/session-observer/sources"),
      api("/v1/session-observer/chat-rooms"),
      api("/v1/session-graph").catch(() => null),
      api("/v1/session-bus/unread").catch(() => ({ counts: {} })),
    ]);
    state.sessionBusUnread = busUnread?.counts || {};
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
    state.providerActivitySources = activity.sources || [];
    state.providerChatRooms = chatCatalog.rooms || [];
    state.projectAnchorSessions = chatCatalog.anchor_sessions || [];
    state.sessionGraph = sessionGraph?.graph || state.sessionGraph;
    syncProviderSessionSubscriptions();
    prefillsObservatoryInjectForm();
    renderRuntimePreflight();
    renderSessionObservatory();
    renderSessionRail();
    renderNodeModes();
    renderProviderActivitySources();
    if (state.view === "sessions") buildSessionGraph();
    state.supervisorRefreshedAt = Date.now();
    return audit;
  })();
  state.supervisorRefreshPromise = refreshPromise;
  try {
    return await refreshPromise;
  } finally {
    if (state.supervisorRefreshPromise === refreshPromise) {
      state.supervisorRefreshPromise = null;
    }
  }
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
    if (result.catalog?.anchor_sessions) {
      state.projectAnchorSessions = result.catalog.anchor_sessions;
    }
    syncProviderSessionSubscriptions();
    for (const delta of result.deltas || []) {
      const sourceId = String(delta.source?.source_id || "");
      const room = state.providerChatRooms.find(
        (item) => String(item.source_id || "") === sourceId
      );
      if (!room) continue;
      state.providerLiveDelivery[room.chat_key] = String(
        delta.delivery || "UNKNOWN"
      );
      const existing = state.providerLiveDeltas[room.chat_key] || [];
      const known = new Set(existing.map((item) => item.excerpt_id));
      const fresh = (delta.deltas || []).filter(
        (item) => item && !known.has(item.excerpt_id)
      );
      if (fresh.length) {
        state.providerLiveDeltas[room.chat_key] = [...existing, ...fresh].slice(-80);
        mergeProviderLiveDeltasIntoRoom(room.chat_key);
      }
    }
    if (typeof loadTerminalTabs === "function") {
      await loadTerminalTabs();
    }
    renderSessionRail();
    renderNodeModes();
    renderProviderChatSummary();
    renderSelectedSessionDetail();
    if (state.conversationTarget.kind === "PROVIDER_SESSION") {
      renderRoomMessages();
      renderComposerState();
    }
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
  const projectId = (elements.observatoryInjectProject?.value || "").trim();
  if (projectId === "CONDUCTOR") return true;
  return (state.projects || []).some(
    (project) =>
      project.project_id === projectId &&
      String(project.metadata?.node_kind || "").toUpperCase() === "INSTANCE"
  );
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

async function activateAnchorSession(session, room = null) {
  if (session) {
    selectSupervisorSession(session);
    renderSessionRail();
    const exactRoom = room || providerChatRoomForSupervisorSession(session);
    if (exactRoom) {
      await openProviderChatSession(exactRoom, { session });
      expandConversationLayer();
      return;
    }
    if (session.mode === "MASTER") {
      await attachSelectedMasterSession(session);
      await refreshSupervisorSessions();
      expandConversationLayer();
      return;
    }
    if (session.mode === "CONDUCTOR") {
      await attachSelectedConductorSession(session);
      await refreshSupervisorSessions();
      expandConversationLayer();
      return;
    }
  }
  if (room) {
    await openProviderChatSession(room);
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

function providerChatRoomForSupervisorSession(session) {
  if (!session) return null;
  const chatKey = String(session.chat_key || "").trim();
  if (chatKey) {
    const byKey = providerSessionRoomForChatKey(chatKey);
    if (byKey) return byKey;
  }
  const sessionKey = anchorSessionKey(session);
  const anchorRef = sessionAnchorRef(session);
  const matches = (state.providerChatRooms || []).filter((room) => {
    const binding = room.binding || {};
    const boundSession = supervisorSessionForRoom(room);
    return (
      (boundSession && anchorSessionKey(boundSession) === sessionKey) ||
      (anchorRef &&
        String(binding.session_anchor_ref || binding.current_anchor_ref || "").trim() ===
          anchorRef)
    );
  });
  return (
    matches.find(
      (room) =>
        anchorRef &&
        String(
          room.binding?.session_anchor_ref || room.binding?.current_anchor_ref || ""
        ).trim() === anchorRef
    ) || matches[0] || null
  );
}

function providerLiveDeliveryLabel(room) {
  const delivery = state.providerLiveDelivery[room.chat_key] || "WAITING";
  if (delivery === "TRANSIENT_REDACTED") return "Live tail active";
  if (delivery === "ACTIVITY_ONLY") {
    return "Activity observed; this provider exposes no text delta";
  }
  if (delivery === "NO_NEW_ACTIVITY") return "Watching for new provider output";
  return "Watching for provider activity";
}

function sessionRailProjectIdentity(room) {
  const binding = room?.binding || {};
  const anchored = ["BOUND", "ANCHOR_OBSERVED", "INDEPENDENT"].includes(
    binding.state
  );
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

function projectProviderSetting(projectId) {
  return (
    state.providerSettings?.project_masters?.find(
      (item) => item.scope_id === projectId
    ) || null
  );
}

function fillSessionSummaryModelSelect(provider, selectedValue) {
  if (!elements.sessionSummaryModel) return;
  const key = String(provider || "").toUpperCase();
  const catalogModels = providerCatalogModels(key);
  const capabilityModel = providerCapability(key)?.model;
  const models = [
    ...new Set(
      [
        ...catalogModels,
        capabilityModel,
        state.providerModels?.providers?.[key]?.default,
      ].filter(Boolean)
    ),
  ];
  const selected = String(selectedValue || "");
  const validSelected = selected && models.includes(selected) ? selected : "";
  elements.sessionSummaryModel.replaceChildren();
  for (const modelId of models) {
    const option = node("option", "", modelId);
    option.value = modelId;
    elements.sessionSummaryModel.append(option);
  }
  if (!models.length) {
    const option = node("option", "", "Host default");
    option.value = "";
    elements.sessionSummaryModel.append(option);
  }
  elements.sessionSummaryModel.value = validSelected || models[0] || "";
  return elements.sessionSummaryModel.value;
}

function renderSessionSummaryConnection(room, project, boundSession) {
  const section = elements.sessionSummaryConnection;
  if (!section) return;
  const binding = room?.binding || {};
  const mode = String(binding.mode || "").toUpperCase();
  const isAnchored = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state);
  const canChoose = Boolean(
    isAnchored &&
      ["MASTER", "CONDUCTOR"].includes(mode) &&
      (mode === "CONDUCTOR" || project.projectId)
  );
  section.hidden = !canChoose;
  if (!canChoose) return;

  const setting =
    mode === "CONDUCTOR"
      ? state.providerSettings?.universe_conductor || {}
      : projectProviderSetting(project.projectId) || {};
  const currentProvider = String(
    room.provider || setting.resolved_provider || setting.provider || "AUTO"
  ).toUpperCase();
  const configuredModel =
    setting.model_ref ||
    setting.resolved_model ||
    providerCapability(currentProvider)?.model ||
    "";
  const currentEffort = String(
    setting.effort || setting.resolved_effort || "AUTO"
  ).toUpperCase();
  const providers = state.providerSettings?.available_providers || [];
  elements.sessionSummaryProvider.replaceChildren();
  for (const provider of providers) {
    const key = String(provider.provider || "").toUpperCase();
    if (!key) continue;
    const option = node(
      "option",
      "",
      key === "CODEX" ? "Codex" : key === "CLAUDE" ? "Claude" : "Grok"
    );
    option.value = key;
    option.disabled = provider.status === "UNAVAILABLE";
    if (provider.reason) option.title = provider.reason;
    elements.sessionSummaryProvider.append(option);
  }
  if (!elements.sessionSummaryProvider.options.length) {
    for (const key of ["CODEX", "CLAUDE", "GROK"]) {
      const option = node("option", "", key);
      option.value = key;
      elements.sessionSummaryProvider.append(option);
    }
  }
  elements.sessionSummaryProvider.value = currentProvider;
  if (elements.sessionSummaryProvider.value !== currentProvider) {
    elements.sessionSummaryProvider.selectedIndex = 0;
  }
  const currentModel = fillSessionSummaryModelSelect(
    elements.sessionSummaryProvider.value,
    configuredModel
  );
  elements.sessionSummaryProvider.onchange = () => {
    fillSessionSummaryModelSelect(elements.sessionSummaryProvider.value, "");
  };
  if (elements.sessionSummaryEffort) {
    elements.sessionSummaryEffort.value = currentEffort;
  }
  if (elements.sessionSummaryConnectionStatus) {
    elements.sessionSummaryConnectionStatus.textContent =
      `Current: ${currentProvider} / ${currentModel || "host default"} / ${currentEffort}`;
  }
  if (elements.sessionSummaryConnect) {
    elements.sessionSummaryConnect.textContent = "Continue with profile";
  }
  if (elements.sessionSummaryNew) {
    elements.sessionSummaryNew.textContent = "Start new session";
  }
}

function applySessionSummaryInspectOnly() {
  if (!state.sessionSummaryInspectOnly) return;
  if (elements.sessionSummaryOpen) elements.sessionSummaryOpen.hidden = true;
  if (elements.sessionSummaryManage) elements.sessionSummaryManage.hidden = true;
  if (elements.sessionSummaryConnect) elements.sessionSummaryConnect.hidden = true;
  if (elements.sessionSummaryNew) elements.sessionSummaryNew.hidden = true;
  if (elements.sessionSummaryConnection) elements.sessionSummaryConnection.hidden = true;
}

function renderProviderChatSummary() {
  if (!elements.sessionSummaryDialog) return;
  const room = (state.providerChatRooms || []).find(
    (item) => item.chat_key === state.selectedProviderChatKey
  );
  if (!room) {
    // Keep the dialog open when it was explicitly opened for a new session
    // (selectedProviderChatKey is null but pendingNewSessionCoordinate is set).
    if (elements.sessionSummaryDialog.open && !state.sessionSummaryInspectOnly && !state.pendingNewSessionCoordinate) {
      elements.sessionSummaryDialog.close();
    }
    return;
  }
  const binding = room.binding || { state: "UNBOUND" };
  if (elements.sessionSummaryOpen) elements.sessionSummaryOpen.hidden = false;
  if (elements.sessionSummaryManage) elements.sessionSummaryManage.hidden = false;
  if (elements.sessionSummaryConnect) elements.sessionSummaryConnect.hidden = false;
  if (elements.sessionSummaryLive) elements.sessionSummaryLive.hidden = false;
  const project = sessionRailProjectIdentity(room);
  const boundSession = supervisorSessionForRoom(room);
  const mode = String(binding.mode || "").toUpperCase();
  const isAnchored = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state);
  const temporality = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state)
    ? binding.is_default === true && binding.observer_currentness === "CURRENT"
      ? "Current"
      : "Past"
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
  const projectSetting = projectProviderSetting(project.projectId);
  const usageLabel = sessionUsageLabel(projectSetting?.session_connection);
  if (usageLabel) facts.push(["Usage", usageLabel]);
  const historyCount = Number(room.provider_history_count || 1);
  if (historyCount > 1) facts.push(["Provider history", String(historyCount)]);
  for (const [label, value] of facts) {
    const fact = node("div", "session-summary-fact");
    fact.append(node("span", "", label), node("strong", "", value));
    elements.sessionSummaryFacts.append(fact);
  }
  renderSessionSummaryConnection(room, project, boundSession);
  if (elements.sessionSummaryLive) {
    const deltas = state.providerLiveDeltas[room.chat_key] || [];
    elements.sessionSummaryLive.replaceChildren();
    if (!deltas.length) {
      elements.sessionSummaryLive.append(
        node("p", "session-summary-live-empty", "No new live output")
      );
    } else {
      for (const delta of deltas) {
        const line = node("article", "session-summary-live-line");
        line.append(
          node("small", "", String(delta.role || "UNKNOWN")),
          node("p", "", String(delta.text || ""))
        );
        elements.sessionSummaryLive.append(line);
      }
      elements.sessionSummaryLive.scrollTop = elements.sessionSummaryLive.scrollHeight;
    }
  }
  const canOpenDirect =
    ["BOUND", "ANCHOR_OBSERVED", "INDEPENDENT"].includes(binding.state) &&
    room.session_kind !== "WORKER";
  elements.sessionSummaryOpen.disabled = !canOpenDirect;
  elements.sessionSummaryOpen.textContent = canOpenDirect
    ? binding.mode === "MASTER"
      ? temporality === "Past"
        ? "Continue live Master"
        : "Open live Master"
      : "Open live session"
    : "Not attached";
  elements.sessionSummaryManage.textContent = boundSession
    ? "View in Observatory"
    : isAnchored && ["MASTER", "CONDUCTOR"].includes(mode)
      ? "View activity"
      : "Register session";
  applySessionSummaryInspectOnly();
}

function openProviderChatSummary(room, options = {}) {
  state.sessionSummaryInspectOnly = options.inspectOnly === true;
  state.selectedProviderChatKey = room.chat_key;
  state.pendingNewSessionCoordinate = null;
  markProviderSessionRead(room.chat_key);
  renderSessionRail();
  renderNodeModes();
  renderProviderChatSummary();
  if (!elements.sessionSummaryDialog.open) {
    elements.sessionSummaryDialog.showModal();
  }
}

function openSessionSummaryForNew(coordinate) {
  state.sessionSummaryInspectOnly = false;
  const mode = String(coordinate?.mode || "").toUpperCase();
  const project = coordinate?.project;
  const projectId = String(project?.project_id || "").trim();
  if (!["MASTER", "CONDUCTOR"].includes(mode)) return;
  if (mode === "MASTER" && (!projectId || !project?.project_root)) {
    toast("Project is not registered", true);
    return;
  }
  state.pendingNewSessionCoordinate = coordinate;
  state.selectedProviderChatKey = null;
  elements.sessionSummaryTitle.textContent =
    mode === "CONDUCTOR" ? "New Conductor session" : `New session · ${projectId}`;
  elements.sessionSummarySubtitle.textContent =
    mode === "CONDUCTOR" ? "Universe Conductor" : `${projectId} · Master`;
  if (elements.sessionSummaryFacts) elements.sessionSummaryFacts.replaceChildren();
  if (elements.sessionSummaryLive) elements.sessionSummaryLive.hidden = true;
  if (elements.sessionSummaryOpen) elements.sessionSummaryOpen.hidden = true;
  if (elements.sessionSummaryManage) elements.sessionSummaryManage.hidden = true;
  const section = elements.sessionSummaryConnection;
  if (section) {
    section.hidden = false;
    const providers = state.providerSettings?.available_providers || [];
    elements.sessionSummaryProvider.replaceChildren();
    const placeholder = node("option", "", "Choose provider");
    placeholder.value = "";
    placeholder.disabled = true;
    placeholder.selected = true;
    elements.sessionSummaryProvider.append(placeholder);
    for (const provider of providers) {
      const key = String(provider.provider || "").toUpperCase();
      if (!key) continue;
      const option = node(
        "option",
        "",
        key === "CODEX" ? "Codex" : key === "CLAUDE" ? "Claude" : "Grok"
      );
      option.value = key;
      option.disabled = provider.status === "UNAVAILABLE";
      if (provider.reason) option.title = provider.reason;
      elements.sessionSummaryProvider.append(option);
    }
    if (elements.sessionSummaryProvider.options.length === 1) {
      for (const key of ["CODEX", "CLAUDE", "GROK"]) {
        const option = node("option", "", key);
        option.value = key;
        elements.sessionSummaryProvider.append(option);
      }
    }
    elements.sessionSummaryProvider.value = "";
    fillSessionSummaryModelSelect("", "");
    elements.sessionSummaryProvider.onchange = () => {
      fillSessionSummaryModelSelect(elements.sessionSummaryProvider.value, "");
    };
    if (elements.sessionSummaryEffort) elements.sessionSummaryEffort.value = "AUTO";
    if (elements.sessionSummaryConnectionStatus) {
      elements.sessionSummaryConnectionStatus.textContent =
        "Choose provider settings for the new session";
    }
    if (elements.sessionSummaryConnect) elements.sessionSummaryConnect.hidden = true;
    if (elements.sessionSummaryNew) {
      elements.sessionSummaryNew.hidden = false;
      elements.sessionSummaryNew.textContent = "Start new session";
    }
  }
  if (!elements.sessionSummaryDialog.open) {
    elements.sessionSummaryDialog.showModal();
  }
}

function normalizeNodeModeNode(nodeId) {
  const rawNode = String(nodeId || "").trim();
  return rawNode.toUpperCase() === "CONDUCTOR" ? "universe" : rawNode;
}

function nodeModeCatalog(project) {
  const projectId = String(project?.project_id || "").toLowerCase();
  const isUniverseHome =
    String(project?.metadata?.network_role || "").toUpperCase() === "UNIVERSE_HOME" ||
    projectId === "universe";
  const modes = isUniverseHome ? ["MASTER", "CONDUCTOR"] : ["MASTER"];
  const observedModes = [
    ...(state.supervisorSessions || [])
      .filter(
        (session) =>
          normalizeNodeModeNode(session.node).toLowerCase() === projectId
      )
      .map((session) => String(session.mode || "").trim().toUpperCase()),
    ...(state.providerChatRooms || [])
      .filter((room) => {
        const binding = room.binding || {};
        return (
          ["BOUND", "ANCHOR_OBSERVED", "INDEPENDENT"].includes(binding.state) &&
          normalizeNodeModeNode(
            binding.current_project_id || binding.node
          ).toLowerCase() === projectId
        );
      })
      .map((room) => String(room.binding?.mode || "").trim().toUpperCase()),
  ];
  return [...new Set([...modes, ...observedModes])]
    .filter(Boolean)
    .sort((left, right) => {
      if (left === "MASTER") return -1;
      if (right === "MASTER") return 1;
      return left.localeCompare(right);
    });
}

function nodeModeSessionIsCurrent(session) {
  const currentness = String(
    session?.currentness ||
      session?.observer_currentness ||
      session?.anchor_session?.currentness ||
      ""
  ).toUpperCase();
  if (currentness === "CURRENT") return true;
  return session?.is_default === true && currentness !== "STALE";
}

function vendorStreamStateForSession(session) {
  const terminalId = String(session?.terminal_id || "").trim();
  if (terminalId) {
    const live = (state.supervisorTerminals || state.terminals || []).find(
      (item) => item.terminal_id === terminalId && String(item.state || "").toUpperCase() === "LIVE"
    );
    if (live) return "LIVE";
  }
  const room =
    providerSessionRoomForChatKey(session?.chat_key) ||
    providerChatRoomForSupervisorSession(session);
  const key = String(room?.chat_key || session?.chat_key || "").trim();
  if (!key) return "NO_VENDOR";
  const cache = providerSessionRoomCacheFor(key);
  return String(
    state.providerSessionStreamStates[key] || cache?.streamState || "IDLE"
  ).toUpperCase();
}

const NODE_MODE_WORKING_ACTIVITY_STATES = new Set([
  "ACTIVE",
  "STARTING",
  "RUNNING",
  "WORKING",
  "EXECUTING",
  "BOOTING",
  "INDEXING",
  "INSTALLING",
  "VALIDATING",
  "SYNCING",
  "CHECKPOINTING",
  "REBUILDING",
  "WAITING_USER",
  "WAITING_COMMANDER",
]);

function nodeModeSessionActivityState(session) {
  return String(
    session?.current_activity_state ||
      (session?.active_ing === true ? session?.state : "") ||
      "IDLE"
  ).trim().toUpperCase();
}

function nodeModeSessionIsWorking(session) {
  return (
    session?.active_ing === true ||
    NODE_MODE_WORKING_ACTIVITY_STATES.has(nodeModeSessionActivityState(session))
  );
}

function nodeModeSessionIsActive(session) {
  return nodeModeSessionIsWorking(session);
}

function nodeModeRoomIsActive(room) {
  const key = String(room?.chat_key || "").trim();
  if (!key) return false;
  const cache = providerSessionRoomCacheFor(key);
  return (
    String(
      state.providerSessionStreamStates[key] || cache?.streamState || ""
    ).toUpperCase() === "LIVE"
  );
}

function nodeModeCoordinates() {
  const projects = visibleProjects()
    .sort((left, right) =>
      projectSortKey(left).localeCompare(projectSortKey(right))
    );
  const projectsById = new Map(
    projects.map((project) => [String(project.project_id).toLowerCase(), project])
  );
  const coordinates = new Map();
  const record = (nodeId, mode, source = {}) => {
    const normalizedNode = normalizeNodeModeNode(nodeId);
    const normalizedMode = String(mode || "").trim().toUpperCase();
    const project = projectsById.get(normalizedNode.toLowerCase());
    if (!project || !normalizedMode) return;
    const key = nodeModeCoordinateKey(normalizedNode, normalizedMode);
    const coordinate = coordinates.get(key) || {
      key,
      nodeId: project.project_id,
      project,
      mode: normalizedMode,
      active: false,
      current: false,
      hasSession: false,
      room: null,
      session: null,
      rooms: [],
      sessions: [],
    };
    coordinate.active = coordinate.active || source.active === true;
    coordinate.current = coordinate.current || source.current === true;
    coordinate.hasSession = coordinate.hasSession || source.hasSession === true;
    if (source.room && (!coordinate.room || source.active || source.current)) {
      coordinate.room = source.room;
    }
    if (source.session && (!coordinate.session || source.active || source.current)) {
      coordinate.session = source.session;
    }
    if (
      source.room &&
      !coordinate.rooms.some(
        (room) => String(room.chat_key || "") === String(source.room.chat_key || "")
      )
    ) {
      coordinate.rooms.push(source.room);
    }
    if (
      source.session &&
      !coordinate.sessions.some(
        (session) => anchorSessionKey(session) === anchorSessionKey(source.session)
      )
    ) {
      coordinate.sessions.push(source.session);
    }
    coordinates.set(key, coordinate);
  };

  for (const terminal of state.supervisorTerminals || state.terminals || []) {
    if (String(terminal.state || "").toUpperCase() !== "LIVE") continue;
    const terminalSession = sessionFromPtyTerminal(terminal);
    record(terminal.project_id, terminal.mode, {
      hasSession: true,
      active: nodeModeSessionIsWorking(terminalSession),
    });
  }
  for (const session of [
    ...(state.projectAnchorSessions || []),
    ...(state.supervisorSessions || []),
  ]) {
    const projectId = String(session.project_id || session.node || "").trim();
    const mode = String(session.mode || "").trim().toUpperCase();
    if (!projectId || !mode) continue;
    const room =
      providerSessionRoomForChatKey(session.chat_key) ||
      providerChatRoomForSupervisorSession(session);
    record(projectId, mode, {
      session,
      room,
      hasSession: true,
      current: nodeModeSessionIsCurrent(session),
      active: room ? nodeModeRoomIsActive(room) : false,
    });
  }
  const groups = projects.map((project) => {
    const nodeId = project.project_id;
    const modes = nodeModeCatalog(project).map((mode) =>
      coordinates.get(nodeModeCoordinateKey(nodeId, mode)) || {
        key: nodeModeCoordinateKey(nodeId, mode),
        nodeId,
        project,
        mode,
        active: false,
        current: false,
        hasSession: false,
        room: null,
        session: null,
        rooms: [],
        sessions: [],
      }
    );
    return {
      nodeId,
      project,
      modes,
      parentProjectId: String(project.metadata?.parent_project_id || ""),
    };
  });
  return groups;
}

function nodeModeStatusLabel(coordinate) {
  const buckets = nodeModePanelSessionBuckets(coordinate);
  if (coordinate.current && buckets.working.length) return "CURRENT · WORKING";
  if (buckets.working.length) return "WORKING";
  if (coordinate.current) return "CURRENT";
  if (buckets.idle.length) return "IDLE";
  return coordinate.hasSession ? "SAVED" : "NO SESSION";
}

function nodeModeSelectedSession(coordinate) {
  const sessions = coordinate?.sessions || [];
  const selectedKey = state.selectedSupervisorAnchorKeysByMode[coordinate?.key];
  // A default/current/active observation may be shown, but never chooses chat.
  return sessions.find((session) => anchorSessionKey(session) === selectedKey) || null;
}

async function attachProviderChatRoom(room, coordinate) {
  const key = String(room?.chat_key || "").trim();
  if (!key) throw new Error("This vendor session has no chat key");
  const result = await api(
    "/v1/session-observer/chat-rooms/" + encodeURIComponent(key) + "/attach",
    {
      method: "POST",
      body: {
        project_id: coordinate?.project?.project_id || coordinate?.nodeId,
        mode: coordinate?.mode || "MASTER",
        make_default: true,
      },
    }
  );
  if (result.catalog?.rooms) state.providerChatRooms = result.catalog.rooms;
  if (result.catalog?.anchor_sessions) {
    state.projectAnchorSessions = result.catalog.anchor_sessions;
  }
  await refreshSupervisorSessions();
  return providerSessionRoomForChatKey(key);
}

async function bindNodeModeSessionPty(coordinate, session) {
  if (focusTerminalForSession(coordinate, session)) {
    expandConversationLayer();
    return;
  }
  await createTerminalTab(coordinate, session);
  await refreshSupervisorSessions();
  expandConversationLayer();
}

async function startNewNodeModeSession(coordinate) {
  const project = coordinate?.project;
  const mode = String(coordinate?.mode || "").toUpperCase();
  const projectId = String(project?.project_id || "").trim();
  const cwd = String(project?.project_root || "").trim();
  if (!projectId || !cwd || !mode) {
    throw new Error("New sessions require a registered project and Mode");
  }
  const selectedProvider = String(coordinate?.provider || "").trim().toUpperCase();
  if (selectedProvider) coordinate.provider = selectedProvider;
  await createTerminalTab(coordinate);
  await refreshSupervisorSessions();
  expandConversationLayer();
}

function openNodeModeSessionActions(coordinate, session) {
  state.pendingNodeSessionAction = { coordinate, session };
  if (elements.nodeSessionActionTitle) {
    elements.nodeSessionActionTitle.textContent = sessionDisplayName(session);
  }
  if (elements.nodeSessionActionSubtitle) {
    const live = session.terminal_id
      ? `${String(session.provider || "UNKNOWN").toUpperCase()} / PTY${session.pid ? ` ${session.pid}` : ""}`
      : `${String(session.provider || "UNKNOWN").toUpperCase()} / ${currentAnchorLabel(session)}`;
    elements.nodeSessionActionSubtitle.textContent = live;
  }
  if (elements.nodeSessionStop) {
    elements.nodeSessionStop.disabled = !String(session.terminal_id || "").trim();
  }
  if (elements.nodeSessionInbox) {
    elements.nodeSessionInbox.disabled = !String(session.terminal_id || "").trim();
  }
  if (elements.nodeSessionOpen) {
    elements.nodeSessionOpen.textContent = session.terminal_id
      ? "Open PTY"
      : "PTY Binding";
  }
  if (elements.nodeSessionActionDialog && !elements.nodeSessionActionDialog.open) {
    elements.nodeSessionActionDialog.showModal();
  }
}

function sessionBusTarget(session, coordinate) {
  const terminalId = String(session?.terminal_id || "").trim();
  const anchorRef = sessionAnchorRef(session);
  if (terminalId || anchorRef) {
    return {
      ...(terminalId ? { terminal_id: terminalId } : {}),
      ...(anchorRef ? { session_anchor_ref: anchorRef } : {}),
    };
  }
  return {
    project_id: String(session?.project_id || coordinate?.project?.project_id || ""),
    mode: String(session?.mode || coordinate?.mode || "").toUpperCase(),
    provider: String(session?.provider || "").toUpperCase(),
  };
}

function sessionBusEvidenceRows(message) {
  const context = message?.event_context || {};
  const provenance = message?.provenance || {};
  const lifecycle = message?.lifecycle || {};
  const target = message?.to || {};
  const artifactRefs = Array.isArray(context.artifact_refs)
    ? context.artifact_refs
    : [
        ...(Array.isArray(provenance.artifact_refs) ? provenance.artifact_refs : []),
        lifecycle.result_ref,
      ];
  return [
    ["Event", message?.message_id],
    ["Source event", context.source_event_id || message?.in_reply_to || message?.message_id],
    ["Session Anchor", context.session_anchor_ref || message?.recipient_anchor_ref || message?.session_anchor_ref],
    ["Thread", context.thread_id || message?.thread_id],
    ["Room", context.room_id || message?.room_id],
    ["Task Frame", context.task_frame_ref || provenance.task_frame_ref || lifecycle.task_frame_ref],
    ["Node", context.node_ref || target.node_ref || target.project_id],
    ["State", context.projection_state || message?.lifecycle_state],
    ["Artifacts", [...new Set(artifactRefs.map((value) => String(value || "").trim()).filter(Boolean))].join(", ")],
  ].filter(([, value]) => String(value || "").trim());
}

function renderSessionBusEvidence(message) {
  const details = node("details", "session-bus-evidence");
  details.append(node("summary", "", "Event coordinates"));
  for (const [label, value] of sessionBusEvidenceRows(message)) {
    const row = node("div", "session-bus-evidence-row");
    row.append(node("span", "", label), node("code", "", String(value)));
    details.append(row);
  }
  return details;
}

async function refreshSessionBusMessages() {
  const pending = state.pendingSessionBus;
  if (!pending || !elements.sessionBusMessages) return;
  const terminalId = String(pending.session?.terminal_id || "").trim();
  const anchorRef = sessionAnchorRef(pending.session);
  const projection = String(state.sessionBusProjection || "INBOX").toUpperCase();
  const coordinateQuery = anchorRef
    ? "?session_anchor_ref=" + encodeURIComponent(anchorRef)
    : terminalId
      ? "?terminal_id=" + encodeURIComponent(terminalId)
    : "?project_id=" +
      encodeURIComponent(pending.session?.project_id || pending.coordinate?.project?.project_id || "") +
      "&mode=" +
      encodeURIComponent(pending.session?.mode || pending.coordinate?.mode || "") +
      "&provider=" +
      encodeURIComponent(pending.session?.provider || "");
  const payload = await api(
    "/v1/session-bus/inbox" + coordinateQuery + "&projection=" + encodeURIComponent(projection)
  );
  const rows = payload.messages || [];
  elements.sessionBusMessages.replaceChildren();
  if (!rows.length) {
    const emptyLabel = projection === "INBOX" ? "No inbox events" : `No ${projection.toLowerCase()} events`;
    elements.sessionBusMessages.append(node("p", "session-bus-empty", emptyLabel));
    return;
  }
  for (const message of rows) {
    const item = node("article", "session-bus-item");
    const from = message.from || {};
    const heading = node(
      "header",
      "session-bus-item-head",
      `${message.kind || "NOTE"} · ${message.lifecycle_state || message.delivery_state || "UNKNOWN"} · ${from.project_id || "unknown"}/${from.mode || "UNKNOWN"}/${from.provider || "UNKNOWN"}`
    );
    const body = node("pre", "session-bus-item-body", String(message.body_text || ""));
    item.append(heading, body, renderSessionBusEvidence(message));
    const messageTerminalId = String(message.terminal_id || terminalId).trim();
    if (projection === "INBOX" && messageTerminalId) {
      const ack = node("button", "secondary-button", "Ack");
      ack.type = "button";
      ack.addEventListener("click", () => {
        ackSessionBusMessage(message.message_id, messageTerminalId).catch((error) =>
          toast(error.message, true)
        );
      });
      item.append(ack);
    }
    elements.sessionBusMessages.append(item);
  }
}

async function openSessionBusInbox(coordinate, session) {
  state.pendingSessionBus = { coordinate, session };
  state.sessionBusProjection = "INBOX";
  for (const tab of elements.sessionBusTabs || []) {
    const active = tab.dataset.sessionBusProjection === "INBOX";
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  if (elements.sessionBusTitle) {
    elements.sessionBusTitle.textContent = sessionDisplayName(session);
  }
  if (elements.sessionBusSubtitle) {
    elements.sessionBusSubtitle.textContent = `${session.project_id || coordinate?.project?.project_id || "session"} / ${String(session.mode || coordinate?.mode || "").toUpperCase()} / ${String(session.provider || "UNKNOWN").toUpperCase()}`;
  }
  if (elements.sessionBusBody) elements.sessionBusBody.value = "";
  await refreshSessionBusMessages();
  if (elements.sessionBusDialog && !elements.sessionBusDialog.open) {
    elements.sessionBusDialog.showModal();
  }
}

async function ackSessionBusMessage(messageId, terminalId) {
  await api("/v1/session-bus/messages/" + encodeURIComponent(messageId) + "/ack", {
    method: "POST",
    body: { terminal_id: terminalId },
  });
  await refreshSupervisorSessions();
  renderNodeModes();
  await refreshSessionBusMessages();
}

async function sendSessionBusCompose(event) {
  event.preventDefault();
  const pending = state.pendingSessionBus;
  const body = String(elements.sessionBusBody?.value || "").trim();
  if (!pending || !body) return;
  await api("/v1/session-bus/messages", {
    method: "POST",
    body: {
      to: sessionBusTarget(pending.session, pending.coordinate),
      from: {
        project_id: pending.coordinate?.project?.project_id || pending.session?.project_id || "",
        mode: pending.coordinate?.mode || pending.session?.mode || "",
        provider: "UI",
      },
      kind: "INSTRUCTION",
      notify: "HEADER",
      body_text: body,
    },
  });
  if (elements.sessionBusBody) elements.sessionBusBody.value = "";
  toast("Sent on the session bus");
  await refreshSupervisorSessions();
  renderNodeModes();
  await refreshSessionBusMessages();
}

function openPtySessionInspectSummary(coordinate, session) {
  state.sessionSummaryInspectOnly = true;
  state.selectedProviderChatKey = null;
  if (elements.sessionSummaryTitle) {
    elements.sessionSummaryTitle.textContent = sessionDisplayName(session);
  }
  if (elements.sessionSummarySubtitle) {
    elements.sessionSummarySubtitle.textContent = `${coordinate?.project?.project_id || session.project_id || "session"} · inspect`;
  }
  if (elements.sessionSummaryFacts) {
    elements.sessionSummaryFacts.replaceChildren();
    const facts = [
      ["Project", String(session.project_id || session.node || coordinate?.project?.project_id || "")],
      ["Mode", String(session.mode || coordinate?.mode || "").toUpperCase()],
      ["Provider", String(session.provider || "UNKNOWN").toUpperCase()],
      ["PTY", session.terminal_id ? `${session.terminal_id}${session.pid ? ` / ${session.pid}` : ""}` : "none"],
      ["State", String(session.state || vendorStreamStateForSession(session) || "")],
    ];
    for (const [label, value] of facts) {
      const fact = node("div", "session-summary-fact");
      fact.append(node("span", "", label), node("strong", "", value || "—"));
      elements.sessionSummaryFacts.append(fact);
    }
  }
  if (elements.sessionSummaryLive) elements.sessionSummaryLive.hidden = true;
  applySessionSummaryInspectOnly();
  if (elements.sessionSummaryDialog && !elements.sessionSummaryDialog.open) {
    elements.sessionSummaryDialog.showModal();
  }
}

async function inspectNodeModeSession(coordinate, session) {
  const room = providerChatRoomForSupervisorSession(session);
  if (room) {
    openProviderChatSummary(room, { inspectOnly: true });
    return;
  }
  openPtySessionInspectSummary(coordinate, session);
}

async function endNodeModePtySession(session) {
  const terminalId = String(session?.terminal_id || "").trim();
  if (!terminalId) throw new Error("No live PTY session to end");
  if (typeof stopTerminalSession === "function") {
    await stopTerminalSession(terminalId);
  } else {
    await api("/v1/terminals/" + encodeURIComponent(terminalId), { method: "DELETE" });
  }
  await refreshSupervisorSessions();
  if (typeof loadTerminalTabs === "function") await loadTerminalTabs();
  renderNodeModes();
}

async function selectNodeModeSession(coordinate, session) {
  if (!coordinate || !session) return;
  state.selectedModeCoordinateKey = coordinate.key;
  state.selectedSupervisorAnchorKey = anchorSessionKey(session);
  state.selectedSupervisorAnchorKeysByMode = {
    ...state.selectedSupervisorAnchorKeysByMode,
    [coordinate.key]: anchorSessionKey(session),
  };
  renderNodeModes();
  renderSessionRail();
  renderSessionObservatory();
  openNodeModeSessionActions(coordinate, session);
}

function ptyLiveTerminalsForCoordinate(coordinate) {
  const projectId = String(coordinate?.project?.project_id || coordinate?.nodeId || "").trim();
  const mode = String(coordinate?.mode || "").trim().toUpperCase();
  if (!projectId || !mode) return [];
  return (state.supervisorTerminals || state.terminals || []).filter(
    (item) =>
      String(item.state || "").toUpperCase() === "LIVE" &&
      String(item.project_id || "") === projectId &&
      String(item.mode || "").toUpperCase() === mode
  );
}

function sessionFromPtyTerminal(terminal) {
  const supervisorSessionId = String(terminal?.supervisor_session_id || "").trim();
  const supervised = (state.supervisorSessions || []).find(
    (session) => String(session.session_id || "") === supervisorSessionId
  );
  return {
    ...(supervised || {}),
    terminal_id: terminal.terminal_id,
    supervisor_session_id: supervisorSessionId,
    project_id: terminal.project_id,
    node: terminal.project_id,
    mode: terminal.mode,
    provider: terminal.provider,
    alias: supervised?.alias || `${terminal.project_id} ${terminal.mode}`,
    terminal_state: terminal.state || "LIVE",
    state: supervised?.state || "UNKNOWN",
    current_activity_state: supervised?.current_activity_state || "IDLE",
    pid: terminal.pid,
    executable: terminal.executable,
    cwd: terminal.cwd,
    session_kind: "PTY_LIVE",
    last_seen_at: supervised?.last_seen_at || terminal.created_at,
    session_anchor_ref:
      supervised?.session_anchor_ref ||
      terminal.session_anchor_ref ||
      `pty:${terminal.terminal_id}`,
  };
}

const NODE_MODE_RECENT_SESSION_LIMIT = 5;

function recentAnchorSessionsForCoordinate(coordinate) {
  const projectId = String(
    coordinate?.project?.project_id || coordinate?.nodeId || ""
  ).trim();
  const mode = String(coordinate?.mode || "").trim().toUpperCase();
  if (!projectId || !mode) return [];
  return (state.projectAnchorSessions || [])
    .filter(
      (session) =>
        String(session.project_id || session.node || "").trim() === projectId &&
        String(session.mode || "").trim().toUpperCase() === mode
    )
    .sort((left, right) => {
      const leftCurrent = nodeModeSessionIsCurrent(left) ? 0 : 1;
      const rightCurrent = nodeModeSessionIsCurrent(right) ? 0 : 1;
      if (leftCurrent !== rightCurrent) return leftCurrent - rightCurrent;
      return String(right.last_seen_at || "").localeCompare(
        String(left.last_seen_at || "")
      );
    });
}

function nodeModePanelSessionBuckets(coordinate) {
  // LIVE PTYs always win. Durable anchors fill the remaining recent slots.
  const live = ptyLiveTerminalsForCoordinate(coordinate).map(sessionFromPtyTerminal);
  const seenAnchorKeys = new Set(live.map(anchorSessionKey).filter(Boolean));
  const recentCandidates = recentAnchorSessionsForCoordinate(coordinate).filter(
    (session) => {
      const anchorKey = anchorSessionKey(session);
      if (!anchorKey || seenAnchorKeys.has(anchorKey)) return false;
      seenAnchorKeys.add(anchorKey);
      return true;
    }
  );
  const recentSlots = Math.max(0, NODE_MODE_RECENT_SESSION_LIMIT - live.length);
  const recent = recentCandidates.slice(0, recentSlots);
  return {
    working: live.filter(nodeModeSessionIsWorking),
    idle: live.filter((session) => !nodeModeSessionIsWorking(session)),
    recent,
    sessions: [...live, ...recent],
  };
}

function nodeModePanelSessions(coordinate) {
  return nodeModePanelSessionBuckets(coordinate).sessions;
}

function renderNodeModeSessionCards(coordinate) {
  const cards = node("div", "node-mode-session-cards");
  cards.dataset.coordinateKey = coordinate.key;
  const buckets = nodeModePanelSessionBuckets(coordinate);
  const sessions = nodeModePanelSessions(coordinate);
  const selected = nodeModeSelectedSession(coordinate);
  const ordered = [...sessions].sort((left, right) => {
    const leftLive = left.terminal_id ? 0 : 1;
    const rightLive = right.terminal_id ? 0 : 1;
    if (leftLive !== rightLive) return leftLive - rightLive;
    const leftCurrent = nodeModeSessionIsCurrent(left) ? 0 : 1;
    const rightCurrent = nodeModeSessionIsCurrent(right) ? 0 : 1;
    if (leftCurrent !== rightCurrent) return leftCurrent - rightCurrent;
    return String(right.last_seen_at || right.updated_at || "").localeCompare(
      String(left.last_seen_at || left.updated_at || "")
    );
  });
  const create = node("button", "node-mode-session-new", "New session");
  create.type = "button";
  create.title = "Start a new vendor session for this Mode";
  create.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openSessionSummaryForNew(coordinate);
  });
  cards.append(create);
  if (!sessions.length) {
    cards.append(
      node("p", "node-mode-session-empty", "No live or recent sessions in this mode")
    );
    return cards;
  }
  for (const session of ordered) {
    const room = providerChatRoomForSupervisorSession(session);
    const row = node("div", "node-mode-session-row");
    const card = node("button", "node-mode-session-card");
    card.type = "button";
    card.dataset.anchorKey = anchorSessionKey(session);
    card.dataset.selected = String(
      Boolean(selected && anchorSessionKey(selected) === anchorSessionKey(session))
    );
    card.dataset.attached = String(Boolean(room));
    card.setAttribute(
      "aria-pressed",
      String(Boolean(selected && anchorSessionKey(selected) === anchorSessionKey(session)))
    );
    const label = node("span", "node-mode-session-copy");
    const detail = session.terminal_id
      ? `${String(session.provider || "UNKNOWN").toUpperCase()} / PTY${session.pid ? ` ${session.pid}` : ""}`
      : `${String(session.provider || "UNKNOWN").toUpperCase()} / ${currentAnchorLabel(session)}`;
    label.append(
      node("strong", "", sessionDisplayName(session)),
      node("small", "", detail)
    );
    const activityState = nodeModeSessionActivityState(session);
    const current = nodeModeSessionIsCurrent(session);
    const livePty = Boolean(session.terminal_id);
    let visualState = "RECENT";
    let statusLabel = "RECENT";
    if (livePty) {
      visualState = nodeModeSessionIsWorking(session) ? activityState : "IDLE";
      statusLabel = visualState === "ACTIVE" ? "WORKING" : visualState;
    } else if (session.active_ing === true) {
      const offlineState = String(session.state || "ING").toUpperCase();
      visualState = "OFFLINE";
      statusLabel = `${offlineState} · OFFLINE`;
    } else if (current) {
      visualState = "OFFLINE";
      statusLabel = "CURRENT · OFFLINE";
    }
    const status = node("span", "node-mode-session-status", statusLabel);
    status.dataset.state = visualState;
    status.dataset.current = String(current);
    const unread = Number(state.sessionBusUnread?.[session.terminal_id] || 0);
    card.append(label, status);
    if (unread > 0) {
      card.append(node("span", "node-mode-session-bus-unread", String(Math.min(unread, 99))));
    }
    card.title = livePty
      ? current
        ? "Inspect the DB Current connected session"
        : "Inspect this connected session"
      : current
        ? "Inspect the DB Current session anchor (PTY offline)"
        : "Inspect this recent session anchor";
    card.dataset.current = String(nodeModeSessionIsCurrent(session));
    card.addEventListener("click", () => {
      selectNodeModeSession(coordinate, session).catch((error) =>
        toast(error.message, true)
      );
    });
    row.append(card);
    cards.append(row);
  }
  return cards;
}

function selectNodeModeNode(nodeId) {
  const same =
    String(state.selectedProject?.project_id || "").toLowerCase() ===
    String(nodeId || "").toLowerCase();
  selectProject(nodeId, {
    revealInspector: same,
  })
    .then(() => renderNodeModes())
    .catch((error) => toast(error.message, true));
}

function openNodeModeCoordinate(coordinate) {
  // ACTIVE/attached is observed state. It must not select or route a chat.
  if (state.selectedModeCoordinateKey === coordinate.key) {
    state.selectedModeCoordinateKey = null;
    renderNodeModes();
    return;
  }
  state.selectedModeCoordinateKey = coordinate.key;
  selectNodeModeNode(coordinate.nodeId);
  renderNodeModes();
}

function renderNodeModeGroup(group, { nested = false } = {}) {
    const section = node(
      "section",
      nested ? "node-mode-group node-mode-group-nested" : "node-mode-group"
    );
    section.dataset.nodeId = group.nodeId;
    section.dataset.nodeKind = String(group.project.metadata?.node_kind || "PROJECT");
    const currentCount = group.modes.filter((mode) => mode.current).length;
    const heading = node("button", "node-mode-group-heading node-mode-node");
    heading.type = "button";
    heading.dataset.nodeId = group.nodeId;
    heading.ariaSelected = String(
      state.selectedProject?.project_id === group.nodeId
    );
    heading.title = `Select ${projectDisplayName(group.project)} node`;
    heading.append(
      node("strong", "", projectDisplayName(group.project)),
      node("small", "", `${currentCount}/${group.modes.length} current`)
    );
    heading.addEventListener("click", () => selectNodeModeNode(group.nodeId));
    section.append(heading);
    const list = node("div", "node-mode-group-list");
    for (const coordinate of group.modes) {
      const item = node("button", "node-mode-item");
      item.type = "button";
      item.role = "option";
      item.dataset.nodeId = coordinate.nodeId;
      item.dataset.mode = coordinate.mode;
      item.dataset.active = String(coordinate.active);
      item.dataset.current = String(coordinate.current);
      const modeSelected = state.selectedModeCoordinateKey === coordinate.key;
      item.ariaSelected = String(modeSelected);
      item.title = `${projectDisplayName(group.project)} / ${coordinate.mode} · ${nodeModeStatusLabel(coordinate)}`;
      const copy = node("span", "node-mode-copy");
      copy.append(
        node("strong", "", coordinate.mode),
        node("small", "", nodeModeStatusLabel(coordinate))
      );
      item.append(node("span", "node-mode-mark", coordinate.mode.slice(0, 1)), copy);
      item.addEventListener("click", () => openNodeModeCoordinate(coordinate));
      list.append(item);
      const sessionCount = nodeModePanelSessions(coordinate).length;
      // Collapsed modes keep LIVE and bounded recent sessions discoverable.
      if (modeSelected || sessionCount) {
        list.append(renderNodeModeSessionCards(coordinate));
      }
    }
    section.append(list);
    return section;
}

function renderNodeModes() {
  if (!elements.nodeModeList) return;
  const groups = nodeModeCoordinates();
  const modeCount = groups.reduce((total, group) => total + group.modes.length, 0);
  const currentModeCount = groups.reduce(
    (total, group) => total + group.modes.filter((mode) => mode.current).length,
    0
  );
  elements.nodeModeList.replaceChildren();
  if (elements.nodeModeCount) {
    elements.nodeModeCount.textContent = `${currentModeCount}/${modeCount}`;
    elements.nodeModeCount.title = `${currentModeCount} DB Current modes / ${modeCount} node modes`;
  }
  if (!groups.length) {
    elements.nodeModeList.append(
      node("p", "node-mode-empty", "No nodes registered")
    );
    return;
  }

  const groupsById = new Map(groups.map((group) => [group.nodeId, group]));
  const childrenByParent = new Map();
  for (const group of groups) {
    if (!group.parentProjectId || !groupsById.has(group.parentProjectId)) continue;
    const children = childrenByParent.get(group.parentProjectId) || [];
    children.push(group);
    childrenByParent.set(group.parentProjectId, children);
  }
  const roots = groups.filter(
    (group) => !group.parentProjectId || !groupsById.has(group.parentProjectId)
  );
  const appendGroup = (group, nested = false) => {
    elements.nodeModeList.append(renderNodeModeGroup(group, { nested }));
    for (const child of childrenByParent.get(group.nodeId) || []) {
      appendGroup(child, true);
    }
  };
  for (const group of roots) {
    appendGroup(group);
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
    } else if (
      (binding.is_default === true &&
        binding.observer_currentness === "CURRENT") ||
      (state.supervisorTerminals || []).some(
        (t) =>
          String(t.state || "").toUpperCase() === "LIVE" &&
          String(t.project_id || "") === String(binding.node || binding.current_project_id || "") &&
          String(t.mode || "").toUpperCase() === String(binding.mode || "").toUpperCase() &&
          String(t.provider || "").toUpperCase() === String(room.provider || "").toUpperCase()
      )
    ) {
      group.current.push(room);
    } else {
      group.past.push(room);
    }
  }

  const appendRoom = (target, room) => {
    const binding = room.binding || { state: "UNBOUND" };
    const isAnchored = ["BOUND", "ANCHOR_OBSERVED"].includes(binding.state);
    const row = node("div", "session-rail-row");
    const item = node("button", "session-rail-item provider-chat-item");
    item.type = "button";
    item.dataset.bound = String(isAnchored);
    item.dataset.kind = room.session_kind || "CHAT";
    item.classList.toggle(
      "selected",
      providerSessionRoomIsSelected(room.chat_key)
    );
    const activityState = providerSessionActivityState(room);
    item.dataset.state = activityState;
    const copy = node("span", "session-rail-copy");
    const anchorLabel =
      isAnchored && binding.current_anchor_ref !== "UNKNOWN"
        ? `${binding.is_default === true && binding.observer_currentness === "CURRENT" ? "" : "Past 쨌 "}${binding.current_anchor_ref}`
        : isAnchored
          ? binding.alias || `${binding.node} ${binding.mode}`
          : `${room.provider} origin`;
    const roomLabel =
      binding.alias || room.display_name || "Untitled session";
    copy.append(
      node("strong", "", roomLabel),
      node("small", "", `${sessionRailActivityLabel(room)} 쨌 ${anchorLabel}`)
    );
    const status = node(
      "span",
      "session-rail-status",
      room.session_kind === "WORKER" ? "WORKER" : activityState
    );
    status.dataset.state = activityState;
    const unread = providerSessionUnreadCount(room);
    if (unread > 0) {
      status.textContent = `${status.textContent} / ${unread} new`;
      item.dataset.unread = String(unread);
    }
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
      (room) => providerSessionRoomIsSelected(room.chat_key)
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
        if ((project && session.mode === "MASTER") || session.mode === "CONDUCTOR") {
          await activateAnchorSession(
            session,
            providerChatRoomForSupervisorSession(session)
          );
        } else {
          state.conversationSurface = "CHAT";
          state.conversationTarget = { kind: "NONE", projectId: null };
          renderComposerState();
          renderRoomMessages();
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

function activeProviderSessionReply() {
  if (state.conversationTarget.kind !== "PROVIDER_SESSION") return null;
  return [...state.providerSessionMessages]
    .reverse()
    .find((message) =>
      ["STARTING", "STREAMING", "CANCELLATION_REQUESTED"].includes(
        String(message.state || "")
      )
    ) || null;
}

async function cancelProviderSessionTurn() {
  const target = state.conversationTarget;
  if (target.kind !== "PROVIDER_SESSION" || !activeProviderSessionReply()) return;
  try {
    const result = await api(
      `/v1/provider-sessions/${encodeURIComponent(target.chat_key)}/cancel`,
      { method: "POST", body: {} }
    );
    if (result.message) {
      const cache = providerSessionCache(target.chat_key);
      cache.messages = dedupeProviderSessionMessages([
        ...cache.messages,
        result.message,
      ]);
      syncSelectedProviderSessionState(target.chat_key);
    }
    renderComposerActions();
    renderComposerState();
    renderRoomMessages();
    toast(
      result.status === "PROVIDER_SESSION_CANCELLATION_ALREADY_REQUESTED"
        ? "Cancellation already requested"
        : "Provider reply cancellation requested"
    );
  } catch (error) {
    toast(error.message, true);
  }
}

function renderComposerActions() {
  elements.projectMasterActions.replaceChildren();
  if (state.conversationTarget.kind === "SESSION_DELEGATION") {
    // Delegation has its own explicit target anchors; do not offer room routing here.
    elements.returnToConductor.classList.toggle("selected", false);
    return;
  }
  if (state.conversationTarget.kind === "PROVIDER_SESSION") {
    const activeReply = activeProviderSessionReply();
    if (activeReply) {
      const action = node("button", "composer-menu-item");
      action.type = "button";
      action.role = "menuitem";
      const pending = activeReply.state === "CANCELLATION_REQUESTED";
      action.disabled = pending;
      action.append(
        node("span", "", pending ? "Cancellation requested" : "Cancel reply"),
        node("small", "", "Keep the Provider process running; ignore its final result")
      );
      action.addEventListener("click", cancelProviderSessionTurn);
      elements.projectMasterActions.append(action);
    }
  }
  for (const project of operableProjects()) {
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
            ? "Master session selected"
            : "Prepare Master session"
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
  showSessionSelection("");
}

function applyNewSessionCoordinates(
  prepareBody,
  options,
  fallbackProjectId,
  fallbackMode
) {
  if (options.sessionAction !== "NEW") return;
  const projectId = String(options.projectId || fallbackProjectId || "").trim();
  const cwd = String(options.cwd || "").trim();
  const requestedMode = String(
    options.requestedMode || fallbackMode || ""
  ).trim().toUpperCase();
  if (!projectId || !cwd || !requestedMode) {
    throw new Error("New sessions require project, cwd, and Mode coordinates");
  }
  prepareBody.project_id = projectId;
  prepareBody.cwd = cwd;
  prepareBody.requested_mode = requestedMode;
}

function preparedSupervisorSession({ mode, projectId = "", connection = {}, anchorKey = "" }) {
  const normalizedMode = String(mode || "").toUpperCase();
  const normalizedProject = String(projectId || "").toLowerCase();
  const expectedProvider = String(connection.last_provider || "").toUpperCase();
  const expectedRef = String(connection.last_session_ref || "").trim();
  const sessions = (state.supervisorSessions || []).filter((session) => {
    if (String(session.mode || "").toUpperCase() !== normalizedMode) return false;
    return !normalizedProject || String(session.node || "").toLowerCase() === normalizedProject;
  });
  const sessionRefMatches = (session) => [
    session.provider_session_ref,
    session.provider_session_id,
    session.observer_session_ref,
    session.session_ref,
  ].some((value) => String(value || "").trim() === expectedRef);
  return (
    sessions.find((session) => anchorKey && anchorSessionKey(session) === anchorKey) ||
    sessions.find((session) => expectedRef && sessionRefMatches(session)) ||
    sessions.find((session) => session.is_default && (!expectedProvider || String(session.provider || "").toUpperCase() === expectedProvider)) ||
    sessions.find((session) => session.is_default) ||
    sessions.find((session) => !expectedProvider || String(session.provider || "").toUpperCase() === expectedProvider) ||
    sessions[0] ||
    null
  );
}

function showSessionSelection(message = "Select a session to chat.") {
  state.conversationSurface = "CHAT";
  closeProjectRoomStream();
  state.conversationTarget = { kind: "NONE", projectId: null };
  closeComposerActionMenu();
  renderComposerActions();
  renderComposerState();
  renderRoomMessages();
  if (message) toast(message);
}

async function openPreparedProviderSession({ mode, projectId = "", connection = {}, anchorKey = "" }) {
  try {
    await refreshSupervisorSessions();
  } catch (error) {
    console.warn("Provider session refresh after prepare failed", error);
  }
  const session = preparedSupervisorSession({ mode, projectId, connection, anchorKey });
  const room = providerChatRoomForSupervisorSession(session);
  if (session && room) {
    await openProviderChatSession(room, { session });
    return true;
  }
  showSessionSelection("Session is ready. Select it from the session card.");
  return false;
}

async function callUniverseConductor(options = {}) {
  closeComposerActionMenu();
  const prepareBody = {};
  if (options.provider) prepareBody.provider = options.provider;
  if (Object.prototype.hasOwnProperty.call(options, "modelRef")) {
    prepareBody.model_ref = options.modelRef || "";
  }
  if (Object.prototype.hasOwnProperty.call(options, "effort")) {
    prepareBody.effort = options.effort || "AUTO";
  }
  if (options.sessionAction) {
    prepareBody.session_action = options.sessionAction;
  }
  applyNewSessionCoordinates(
    prepareBody,
    options,
    options.projectId,
    "CONDUCTOR"
  );
  const prepared = await api("/v1/conductor-session/prepare", {
    method: "POST",
    body: prepareBody,
  });
  const connection = prepared.session_connection || {};
  if (connection.error_code) {
    throw new Error(connection.reason || connection.error_code);
  }
  if (
    options.expectedProvider &&
    String(connection.last_provider || "").toUpperCase() !==
      String(options.expectedProvider).toUpperCase()
  ) {
    throw new Error("Selected provider did not become the active Conductor connection");
  }
  if (
    options.expectedModel &&
    connection.model_ref !== options.expectedModel
  ) {
    throw new Error("Selected model did not become the active Conductor connection");
  }
  if (
    options.expectedEffort &&
    String(connection.effort || "AUTO").toUpperCase() !==
      String(options.expectedEffort).toUpperCase()
  ) {
    throw new Error("Selected effort did not become the active Conductor connection");
  }
  state.providerSettings = await api("/v1/settings/providers");
  await openPreparedProviderSession({
    mode: "CONDUCTOR",
    projectId: options.projectId,
    connection,
    anchorKey: options.anchorKey,
  });
  return connection;
}

async function callProjectMaster(projectId, options = {}) {
  closeComposerActionMenu();
  if (state.selectedProject?.project_id !== projectId) {
    await selectProject(projectId);
  }
  const prepareBody = {};
  if (options.provider) prepareBody.provider = options.provider;
  if (Object.prototype.hasOwnProperty.call(options, "modelRef")) {
    prepareBody.model_ref = options.modelRef || "";
  }
  if (Object.prototype.hasOwnProperty.call(options, "effort")) {
    prepareBody.effort = options.effort || "AUTO";
  }
  if (options.sessionAction) {
    prepareBody.session_action = options.sessionAction;
  }
  applyNewSessionCoordinates(
    prepareBody,
    options,
    projectId,
    "MASTER"
  );
  const prepared = await api(
    `/v1/projects/${encodeURIComponent(projectId)}/master-session/prepare`,
    {
      method: "POST",
      body: prepareBody,
    }
  );
  const connection = prepared.session_connection || {};
  if (
    options.expectedProvider &&
    String(connection.last_provider || "").toUpperCase() !==
      String(options.expectedProvider).toUpperCase()
  ) {
    throw new Error("Selected provider did not become the active Master connection");
  }
  if (
    options.expectedSessionRef &&
    connection.last_session_ref !== options.expectedSessionRef
  ) {
    throw new Error("Selected Master session could not be resumed");
  }
  if (
    options.expectedModel &&
    connection.model_ref !== options.expectedModel
  ) {
    throw new Error("Selected model did not become the active Master connection");
  }
  if (
    options.expectedEffort &&
    String(connection.effort || "AUTO").toUpperCase() !==
      String(options.expectedEffort).toUpperCase()
  ) {
    throw new Error("Selected effort did not become the active Master connection");
  }
  state.providerSettings = await api("/v1/settings/providers");
  await selectProject(projectId);
  await openPreparedProviderSession({
    mode: "MASTER",
    projectId,
    connection,
    anchorKey: options.anchorKey,
  });
}

async function attachSelectedMasterSession(session) {
  const provider = String(session?.provider || "").toUpperCase();
  const projectId = String(session?.node || "").trim();
  if (session?.mode !== "MASTER" || !provider || !projectId) {
    throw new Error("This session cannot open a Project Master");
  }
  await callProjectMaster(projectId, {
    provider,
    anchorKey: anchorSessionKey(session),
  });
  return session;
}

async function attachSelectedConductorSession(session) {
  const provider = String(session?.provider || "").toUpperCase();
  if (session?.mode !== "CONDUCTOR" || !provider) {
    throw new Error("This session cannot open the Universe Conductor");
  }
  await callUniverseConductor({
    provider,
    anchorKey: anchorSessionKey(session),
  });
  return session;
}

async function connectSessionSummaryProviderModel(sessionAction = "RESUME") {
  const room = (state.providerChatRooms || []).find(
    (item) => item.chat_key === state.selectedProviderChatKey
  );
  const pendingCoord = state.pendingNewSessionCoordinate;
  const session = supervisorSessionForRoom(room);
  const project = room
    ? sessionRailProjectIdentity(room)
    : { projectId: String(pendingCoord?.project?.project_id || ""), label: "" };
  const mode = String(room?.binding?.mode || pendingCoord?.mode || "").toUpperCase();
  const registeredProject = room
    ? (state.projects || []).find(
        (item) =>
          String(item.project_id || "").toLowerCase() ===
          String(project.projectId || "").toLowerCase()
      )
    : (pendingCoord?.project?.project_root
        ? { project_id: pendingCoord.project.project_id, project_root: pendingCoord.project.project_root }
        : null);
  if (
    (!room && !pendingCoord) ||
    !["MASTER", "CONDUCTOR"].includes(mode) ||
    (sessionAction !== "NEW" && !session) ||
    (mode === "MASTER" && !project.projectId) ||
    (sessionAction === "NEW" && !registeredProject?.project_root)
  ) {
    throw new Error("Only an anchored Master or Conductor room can choose a provider and model");
  }
  const provider = String(elements.sessionSummaryProvider?.value || "").toUpperCase();
  const modelRef = String(elements.sessionSummaryModel?.value || "").trim();
  const effort = String(elements.sessionSummaryEffort?.value || "AUTO").toUpperCase();
  if (!provider) throw new Error("Choose a provider first");
  if (elements.sessionSummaryConnect) {
    elements.sessionSummaryConnect.disabled = true;
  }
  if (elements.sessionSummaryConnectionStatus) {
    elements.sessionSummaryConnectionStatus.textContent =
      `Connecting ${provider} / ${modelRef || "host default"} / ${effort}...`;
  }
  try {
    // For NEW sessions the Claude process hasn't reported its session id yet
    // when prepare returns, so last_provider is still UNKNOWN. Skip the
    // provider/model/effort assertions — they only make sense on RESUME.
    const isNew = String(sessionAction || "").toUpperCase() === "NEW";
    const options = {
      provider,
      modelRef,
      effort,
      sessionAction,
      projectId: registeredProject?.project_id,
      cwd: registeredProject?.project_root,
      requestedMode: mode,
      expectedProvider: isNew ? undefined : provider,
      expectedModel: isNew ? undefined : modelRef,
      expectedEffort: isNew ? undefined : effort,
    };
    if (isNew) {
      // New sessions must first create the PTY-backed CLI surface.  Provider
      // session identity is then observed and bound by the terminal Hook.
      await startNewNodeModeSession({
        ...(pendingCoord || {}),
        project: registeredProject || pendingCoord?.project,
        nodeId: registeredProject?.project_id || pendingCoord?.nodeId || project.projectId,
        mode,
        provider,
        modelRef,
        effort,
      });
    } else if (mode === "CONDUCTOR") {
      await callUniverseConductor(options);
    } else {
      await callProjectMaster(project.projectId, options);
    }
    elements.sessionSummaryDialog.close();
    state.pendingNewSessionCoordinate = null;
    const targetLabel = mode === "CONDUCTOR" ? "Conductor" : "Project Master";
    const actionLabel = sessionAction === "NEW" ? "new session started" : "connected";
    toast(
      `${targetLabel} ${actionLabel}: ${provider} / ${modelRef || "host default"} / ${effort}`
    );
  } finally {
    if (elements.sessionSummaryConnect) {
      elements.sessionSummaryConnect.disabled = false;
    }
  }
}

function sessionConnectionText(connection, fallbackMode) {
  const provider = connection?.last_provider || "UNKNOWN";
  const model = connection?.model_ref || "model unknown";
  const effort = connection?.effort || "AUTO";
  const connectionState = connection?.connection_state || "NOT_OPENED";
  const mode = connection?.requested_mode || fallbackMode;
  return `${provider} / ${model} / ${effort} / ${connectionState} / ${mode}`;
}

function sessionUsageLabel(connection) {
  const observation = connection?.runtime_observation;
  if (!observation || typeof observation !== "object") return "";
  const quota = String(observation.quota_state || "").trim().toUpperCase();
  const usage = observation.usage;
  const tokenTotal =
    usage && typeof usage === "object"
      ? ["input_tokens", "output_tokens"].reduce((total, key) => {
          const value = Number(usage[key]);
          return Number.isFinite(value) ? total + value : total;
        }, 0)
      : 0;
  const parts = [];
  if (tokenTotal > 0) parts.push(String(tokenTotal.toLocaleString()) + " tokens");
  if (quota && quota !== "UNKNOWN") parts.push("quota " + quota);
  return parts.join(" / ");
}

function renderComposerState() {
  const activeTerminal = typeof activeTerminalSession === "function"
    ? activeTerminalSession()
    : (state.terminals || []).find((item) => item.terminal_id === state.activeTerminalId);
  const showTerminal =
    state.conversationSurface === "CLI" && Boolean(activeTerminal);
  const showConversation =
    !showTerminal && state.conversationTarget.kind !== "NONE";
  elements.roomMessageList.classList.toggle("hidden", !showConversation);
  elements.dispatchForm.classList.toggle("hidden", !showConversation);
  elements.terminalTabs.classList.toggle("hidden", !showTerminal);
  elements.terminalStage.classList.toggle("hidden", !showTerminal);
  const dockLabel = elements.conversationToggle?.querySelector(".chat-dock-label");
  if (dockLabel) {
    dockLabel.textContent = showTerminal
      ? "CLI"
      : state.conversationTarget.kind === "PROVIDER_SESSION"
        ? "Chat"
        : state.conversationTarget.kind === "NONE"
          ? "Chat"
          : "Project Room";
  }
  if (typeof applyCliDockTitle === "function") {
    applyCliDockTitle(showTerminal ? activeTerminal : null);
  }
  if (showTerminal) return;
  if (!showConversation) {
    elements.conversationTitle.textContent = "Chat";
    elements.conversationTargetLabel.textContent = "Select a session";
    return;
  }
  elements.conversationTitle.textContent =
    state.conversationTarget.kind === "PROVIDER_SESSION" ? "Session Chat" : "Project Room";
  elements.conversationTargetLabel.textContent =
    state.conversationTarget.kind === "PROVIDER_SESSION"
      ? "Anchor-bound provider session"
      : "Anchor-bound room conversation";
  if (state.conversationTarget.kind === "SESSION_DELEGATION") {
    const draft = state.sessionDelegationDraft || state.conversationTarget;
    const originAnchorRef = String(draft.origin_anchor_ref || "UNKNOWN");
    const targetAnchorRef = String(draft.target_anchor_ref || "UNKNOWN");
    elements.roomContext.textContent = `Delegation / ${originAnchorRef} → ${targetAnchorRef}`;
    elements.roomHint.textContent =
      "Cross-session delegation / origin and target anchors are explicit / not direct chat";
    elements.dispatchInstruction.placeholder = "Instruction for the target session";
    return;
  }
  if (state.conversationTarget.kind === "PROVIDER_SESSION") {
    const target = state.conversationTarget;
    const provider = target.provider || "UNKNOWN";
    const model = target.model_ref || "host default";
    const connection = state.providerSessionConnection || {};
    elements.roomContext.textContent =
      `${target.alias || target.projectId} / ${provider} / ${model}`;
    elements.roomHint.textContent =
      `Direct Provider Session / ${connection.connection_state || state.providerSessionStreamState}`;
    elements.dispatchInstruction.placeholder =
      `Message ${target.alias || target.projectId}`;
    return;
  }
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    const setting = state.providerSettings?.universe_conductor || null;
    const provider = setting?.resolved_provider || "UNAVAILABLE";
    const autoApprove =
      providerCapability(provider)?.cli_auto_approve || "UNKNOWN";
    const session = setting?.session_connection || null;
    const usage = sessionUsageLabel(session);
    const residentSessionReady = Boolean(
      session?.resident === true &&
        !["NOT_OPENED", "UNAVAILABLE"].includes(
          String(session?.connection_state || "").toUpperCase()
        )
    );
    const sessionUnavailable =
      String(session?.connection_state || "").toUpperCase() === "UNAVAILABLE";
    const sessionError = String(
      session?.reason || session?.error_code || ""
    ).trim();
    const conductorChatReady =
      state.conductorRuntimeBinding?.status === "BOUND" || residentSessionReady;
    elements.roomContext.textContent =
      `Universe Conductor / ${sessionConnectionText(session, "CONDUCTOR")}`;
    elements.roomHint.textContent = sessionUnavailable
      ? "Provider setup required" + (sessionError ? ": " + sessionError : "")
      : conductorChatReady
        ? "LLM connected / Auto-approve " + autoApprove + (usage ? " / " + usage : "")
        : "Waiting for Runtime binding" + (usage ? " / " + usage : "");
    elements.dispatchInstruction.placeholder = "Message Universe Conductor";
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
  const usage = sessionUsageLabel(session);
  elements.roomContext.textContent =
    `${projectId} / Project Master / ${sessionConnectionText(session, "MASTER")}`;
  elements.roomHint.textContent = directBridge
    ? "Direct bridge connected / Auto-approve " + autoApprove + (usage ? " / " + usage : "")
    : registeredBridge
      ? "Bridge registered / awaiting first delivery" + (usage ? " / " + usage : "")
      : "Project Room only" + (usage ? " / " + usage : "");
  elements.dispatchInstruction.placeholder = `Message ${projectId} Master`;
}

function setGraphScale(nextScale) {
  state.graph.scale = Math.min(2.2, Math.max(0.4, nextScale));
  drawGraph();
}

/** Graph canvas modes only (not inspector tabs). */
function showGraphView(view) {
  const allowed = new Set(["universe", "semantic", "sessions", "timeline", "documents", "implementation"]);
  if (!allowed.has(view)) view = "universe";
  state.view = view;
  document.body.classList.add("graph-mode");
  state.selectedNode = null;
  state.focusedNodeId = null;
  elements.nodeBreadcrumb?.classList.add("hidden");
  syncPrimaryNavSelection(
    ["universe", "semantic"].includes(view) ? "map" : view
  );
  if (view !== "sessions") {
    setGraphLegend([
      { kind: "project", label: "Project" },
      { kind: "system", label: "Project Seed node" },
      { kind: "predicted", label: "Predicted" },
      { kind: "document", label: "Document" },
    ]);
  }
  buildGraph();
  if (view === "sessions") fitGraphView();
  renderDetails();
}

function showGoalPlanView() {
  state.view = "work";
  document.body.classList.remove("graph-mode");
  syncPrimaryNavSelection("work");
  renderGoalPlan();
}

/** Highlight top nav without toast placeholders. */
function syncPrimaryNavSelection(primaryView) {
  for (const root of [elements.primaryNav, elements.utilityRail]) {
    if (!root) continue;
    for (const item of root.querySelectorAll("[data-primary-view]")) {
      item.classList.toggle(
        "selected",
        item.getAttribute("data-primary-view") === primaryView
      );
    }
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
  const minimumScale = state.view === "sessions" ? 0.12 : 0.5;
  const scale = Math.min(
    1.6,
    Math.max(minimumScale, Math.min(viewportWidth / spanX, viewportHeight / spanY))
  );
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

    // Project navigation is the first usable UI surface after a server
    // restart.  Do not hold its first render behind optional catalog,
    // provider, and room requests: one slow request used to display an empty
    // Node Modes panel for several seconds even though the project DB had
    // already responded.
    const projectResultPromise = api("/v1/projects");
    const todoResultPromise = api("/v1/todos");
    const releaseResultPromise = api("/v1/releases");
    const conductorRoomResultPromise = api("/v1/conductor-room/messages");
    const governanceProposalInboxResultPromise = api("/v1/governance-proposals");
    const providerSettingsPromise = api("/v1/settings/providers");
    const hostToolsPromise = api("/v1/settings/host-tools");
    const providerModelsResultPromise = api("/v1/settings/provider-models").catch(
      () => null
    );
    const projectResult = await projectResultPromise;
    state.projects = projectResult.projects;
    renderProjects();
    renderNodeModes();
    const [
      todoResult,
      releaseResult,
      conductorRoomResult,
      governanceProposalInboxResult,
      providerSettings,
      hostTools,
      providerModelsResult,
    ] = await Promise.all([
      todoResultPromise,
      releaseResultPromise,
      conductorRoomResultPromise,
      governanceProposalInboxResultPromise,
      providerSettingsPromise,
      hostToolsPromise,
      providerModelsResultPromise,
    ]);
    state.todos = todoResult.todos;
    state.releases = releaseResult.releases;
    state.conductorMessages = conductorRoomResult.messages || [];
    state.conductorDelegations = conductorRoomResult.delegations || [];
    state.conductorPermissions = conductorRoomResult.permissions || [];
    state.conductorRuntimeBinding =
      conductorRoomResult.runtime_binding || null;
    state.governanceProposalInbox =
      governanceProposalInboxResult.proposals || [];
    state.providerSettings = providerSettings;
    state.providerModels = providerModelsResult?.catalog || providerModelsResult;
    state.hostTools = hostTools;
    // Core project navigation must become interactive without waiting for the
    // slower Session Observatory catalog and Runtime audit.
    renderProjects();
    renderNodeModes();
    renderComposerActions();
    renderReleaseCatalog();
    void refreshSupervisorSessions().catch((error) => {
      state.supervisorSessions = [];
      state.supervisorEvents = [];
      renderSessionObservatory();
      console.warn("Session Supervisor refresh failed", error);
    });
    // Multiverse keeps every project tree expanded — load all projections first.
    await loadAllProjectProjections();
    renderProjects();
    renderNodeModes();
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
      const initialProject =
        state.projects.find((project) => project.project_id === "universe") ||
        state.projects[0];
      await selectProject(initialProject.project_id, {
        revealInspector: false,
        syncAssets: syncSelectedProject,
      });
    } else {
      state.selectedProject = null;
      state.projection = null;
      state.dispatches = [];
      state.masterBridge = null;
      renderEmpty();
    }
    if (typeof loadTerminalTabs === "function") {
      void loadTerminalTabs();
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
  const kind = String(project?.metadata?.node_kind || "PROJECT").toUpperCase();
  const order = kind === "INSTANCE" ? "0" : kind === "CONTAINER" ? "1" : "2";
  return `${order}:${project?.metadata?.node_tag || project?.project_id || ""}`;
}

function isLegacyProject(project) {
  return project?.metadata?.visibility === "MIGRATED_LEGACY";
}

function isProjectContainer(project) {
  return project?.metadata?.node_kind === "CONTAINER";
}

function visibleProjects() {
  return (state.projects || []).filter((project) => !isLegacyProject(project));
}

function operableProjects() {
  return visibleProjects().filter((project) => !isProjectContainer(project));
}

function projectButton(project, { nested = false } = {}) {
    const button = node(
      "button",
      nested ? "project-item project-item-nested" : "project-item"
    );
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
    const kind = String(project.metadata?.node_kind || "").toLowerCase();
    const role = String(project.metadata?.network_role || "");
    const roleTag =
      kind === "instance"
        ? "instance"
        : kind === "container"
          ? "container"
          : role.endsWith("_SOURCE")
            ? "source"
            : "";
    copy.append(
      node("span", "project-name", label),
      node(
        "span",
        "project-meta",
        `${roleTag ? `${roleTag} · ` : ""}${
          project.metadata.node_tag || project.metadata.label || project.project_id
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
    button.addEventListener("click", () =>
      selectProject(project.project_id, {
        revealInspector: false,
      })
    );
    return button;
}

function renderProjects() {
  elements.projectList.replaceChildren();
  const projects = visibleProjects()
    .slice()
    .sort((a, b) => projectSortKey(a).localeCompare(projectSortKey(b)));
  const projectIds = new Set(projects.map((project) => project.project_id));
  const childrenByParent = new Map();
  for (const project of projects) {
    const parentId = String(project.metadata?.parent_project_id || "");
    if (!parentId || !projectIds.has(parentId)) continue;
    const children = childrenByParent.get(parentId) || [];
    children.push(project);
    childrenByParent.set(parentId, children);
  }
  const roots = projects.filter((project) => {
    const parentId = String(project.metadata?.parent_project_id || "");
    return !parentId || !projectIds.has(parentId);
  });
  for (const project of roots) {
    if (!isProjectContainer(project)) {
      elements.projectList.append(projectButton(project));
      continue;
    }
    const group = node("section", "project-group");
    const header = node("div", "project-group-label", projectDisplayName(project));
    header.title = project.metadata?.label || project.project_id;
    group.append(header);
    const children = (childrenByParent.get(project.project_id) || [])
      .slice()
      .sort((a, b) => projectSortKey(a).localeCompare(projectSortKey(b)));
    for (const child of children) {
      group.append(projectButton(child, { nested: true }));
    }
    elements.projectList.append(group);
  }
}

function renderReleaseCatalog() {
  const previousTarget =
    state.selectedReleaseTargetProjectId ||
    elements.releaseTargetProject.value ||
    "";
  elements.releaseTargetProject.replaceChildren(
    new Option("Select a project", "")
  );
  const targetProjects = visibleProjects();
  for (const project of targetProjects) {
    elements.releaseTargetProject.append(
      new Option(
        `${projectDisplayName(project)} · ${project.project_root}`,
        project.project_id
      )
    );
  }
  const targetExists = targetProjects.some(
    (project) => project.project_id === previousTarget
  );
  elements.releaseTargetProject.value = targetExists ? previousTarget : "";
  state.selectedReleaseTargetProjectId = elements.releaseTargetProject.value || null;

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
      state.selectedReleaseTargetProjectId
        ? "Plan project update"
        : "Select a target project"
    );
    action.type = "button";
    action.disabled = !state.selectedReleaseTargetProjectId;
    action.addEventListener("click", () =>
      proposeProjectRelease(release.release_id, action)
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
  const plan = proposal.plan && typeof proposal.plan === "object"
    ? proposal.plan
    : {};
  const actions = Array.isArray(plan.actions) ? plan.actions : [];
  const collisions = Array.isArray(plan.collisions) ? plan.collisions : [];
  const actionCounts = {};
  for (const action of actions) {
    actionCounts[action.action] = (actionCounts[action.action] || 0) + 1;
  }
  const legacySummary = actions.length || collisions.length
    ? `collisions ${collisions.length} / actions ${Object.entries(actionCounts)
        .map(([name, count]) => `${name}:${count}`)
        .join(", ") || "none"}`
    : `installed Runtime ${plan.installed_runtime?.state || "UNKNOWN"} / Host preflight ${
        plan.project_host_preflight || "UNKNOWN"
      }`;
  elements.releaseProposalOutput.append(
    node("h3", "", proposal.status),
    node(
      "p",
      "",
      `${proposal.release_id} → ${proposal.project_id} / ${
        plan.operation || "UNKNOWN"
      }`
    ),
    node(
      "p",
      "",
      `Plan ${plan.plan_digest || "UNKNOWN"} / ${legacySummary}`
    ),
    node(
      "p",
      "",
      "No project files were changed. Apply runs the displayed Runtime lifecycle plan."
    )
  );
  if (proposal.status !== "PROJECT_RELEASE_PROPOSAL_BLOCKED") {
    const applyButton = node(
      "button",
      "primary-button release-apply-button",
      `Apply ${plan.operation || "project update"}`
    );
    applyButton.type = "button";
    applyButton.addEventListener("click", () =>
      applyProjectRelease(proposal, applyButton)
    );
    elements.releaseProposalOutput.append(applyButton);
  }
}

function releasePlanErrorMessage(error) {
  const detail = error && typeof error.message === "string"
    ? error.message.trim()
    : "";
  return detail || "Project update plan could not be created";
}

async function proposeProjectRelease(releaseId, button = null) {
  const projectId = state.selectedReleaseTargetProjectId;
  if (!projectId) {
    toast("Select a target project", true);
    return;
  }
  elements.releaseFormError.textContent = "";
  if (button) button.disabled = true;
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        projectId
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
    const detail = releasePlanErrorMessage(error);
    elements.releaseFormError.textContent = detail;
    toast(detail, true);
  } finally {
    if (button) button.disabled = false;
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
    skillGapResult,
    skillCandidateResult,
    benchResult,
    benchCompareResult,
    experienceResult,
    patternResult,
    contextPackResult,
    memoryResult,
    memoryProposalResult,
    memoryBatchConfigResult,
    memoryBatchRunResult,
    memoryCandidateResult,
    workLoopResult,
    semanticGraphResult,
  ] = await Promise.all([
    state.projectionsByProject?.[projectId]
      ? Promise.resolve({ projection: state.projectionsByProject[projectId] })
      : project.projection_available === false
        ? Promise.resolve(null)
        : api(`/v1/projects/${encodeURIComponent(projectId)}/projection`).catch(
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
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/skill-gap-summary`
    ).catch(() => ({ summary: { groups: [], observation_count: 0 } })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/skill-candidates`
    ).catch(() => ({ candidates: [] })),
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
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/memory-batch-config`
    ).catch(() => ({ configs: [] })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/memory-batches/runs`
    ).catch(() => ({ runs: [] })),
    api(
      `/v1/projects/${encodeURIComponent(projectId)}/memory-candidates?limit=200`
    ).catch(() => ({ candidates: [] })),
    api(`/v1/projects/${encodeURIComponent(projectId)}/work-loop`).catch(
      () => ({
        predictions: [],
        result_fanouts: [],
        review_candidates: [],
        memory_schedules: [],
        document_automation: null,
      })
    ),
    api(`/v1/projects/${encodeURIComponent(projectId)}/semantic-graph`).catch(
      () => ({ nodes: [], edges: [], invariants: { projection_only: true } })
    ),
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
  state.skillGapSummary = skillGapResult.summary || { groups: [], observation_count: 0 };
  state.skillCandidates = skillCandidateResult.candidates || [];
  state.skillBench = benchResult.bench || [];
  state.benchComparisons = benchCompareResult.comparisons || [];
  state.experienceCases = experienceResult.cases || [];
  state.experiencePatterns = patternResult.proposals || [];
  state.contextPacks = contextPackResult.context_packs || [];
  state.memories = memoryResult.memories || [];
  state.memoryProposals = memoryProposalResult.proposals || [];
  state.memoryBatchConfigs = memoryBatchConfigResult.configs || [];
  state.memoryBatchRuns = memoryBatchRunResult.runs || [];
  state.memoryCandidates = memoryCandidateResult.candidates || [];
  state.workLoop = workLoopResult || null;
  state.semanticGraph = semanticGraphResult || null;
  const universeGoalResult = await api("/v1/universe-goals").catch(() => ({ goals: [] }));
  const goalPlanResult = await api(
    `/v1/projects/${encodeURIComponent(projectId)}/goals`
  ).catch(() => ({ goals: [], unassigned_todos: [] }));
  state.goals = goalPlanResult.goals || [];
  state.universeGoals = universeGoalResult.goals || [];
  state.unassignedTodos = (goalPlanResult.unassigned_todos || []).filter(
    (todo) => todo.state !== "DONE"
  );
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
  renderGoalPlan();
}

function mergeGovernanceProposalInbox(projectId, proposals) {
  state.governanceProposalInbox = [
    ...(proposals || []),
    ...state.governanceProposalInbox.filter(
      (item) => item.project_id !== projectId
    ),
  ];
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
    if (typeof refitActiveTerminal === "function") refitActiveTerminal();
  }
}

const CHAT_PANEL_MIN_WIDTH = 300;
const CHAT_PANEL_MAX_WIDTH = 900;

function clampChatPanelWidth(width) {
  const requested = Number(width);
  const safeWidth = Number.isFinite(requested) ? requested : state.chatPanelWidth;
  const viewportMax = Math.max(
    CHAT_PANEL_MIN_WIDTH,
    window.innerWidth - 420
  );
  return Math.round(
    Math.min(
      CHAT_PANEL_MAX_WIDTH,
      viewportMax,
      Math.max(CHAT_PANEL_MIN_WIDTH, safeWidth)
    )
  );
}

function setChatPanelWidth(width, { persist = false } = {}) {
  const shell = document.querySelector(".app-shell.mockup-shell");
  if (!shell || window.innerWidth <= 720) return;
  const next = clampChatPanelWidth(width);
  state.chatPanelWidth = next;
  shell.style.setProperty("--chat-panel-width", String(next) + "px");
  if (elements.chatResizeHandle) {
    elements.chatResizeHandle.setAttribute("aria-valuenow", String(next));
  }
  if (persist) {
    try {
      window.localStorage.setItem("universe.chatPanelWidth", String(next));
    } catch (_error) {
      // Local preference storage is optional.
    }
  }
}

async function applyProjectRelease(proposal, button) {
  const projectId = String(proposal.project_id || "");
  if (!projectId) {
    toast("Release proposal has no target project", true);
    return;
  }
  button.disabled = true;
  elements.releaseFormError.textContent = "";
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(projectId)}/release-proposals/apply`,
      {
        method: "POST",
        body: {
          approval: "APPROVED",
          proposal_id: proposal.proposal_id,
          proposal_digest: proposal.proposal_digest,
        },
      }
    );
    const receipt = result.receipt || {};
    elements.releaseProposalOutput.replaceChildren(
      node("h3", "", result.status || "PROJECT_RELEASE_APPLICATION_COMPLETED"),
      node("p", "", `${proposal.release_id} → ${projectId}`),
      node(
        "p",
        "",
        `Release ${receipt.release_id || proposal.release_id} / DB ${
          receipt.release_database_sha256 ||
          proposal.release_database_sha256 ||
          "UNKNOWN"
        }`
      ),
      node(
        "p",
        "",
        `Project Host ${result.master_host?.status || "UNKNOWN"}`
      )
    );
    toast(`Runtime update applied to ${projectId}`);
    await refresh({ syncSelectedProject: false });
  } catch (error) {
    const detail = releasePlanErrorMessage(error);
    elements.releaseFormError.textContent = detail;
    toast(detail, true);
    button.disabled = false;
  }
}

function initChatPanelResize() {
  const handle = elements.chatResizeHandle;
  if (!handle) return;
  let storedWidth = null;
  try {
    storedWidth = Number(window.localStorage.getItem("universe.chatPanelWidth"));
  } catch (_error) {
    storedWidth = null;
  }
  setChatPanelWidth(
    Number.isFinite(storedWidth) && storedWidth > 0
      ? storedWidth
      : state.chatPanelWidth
  );

  let dragging = false;
  const move = (event) => {
    if (!dragging) return;
    setChatPanelWidth(window.innerWidth - event.clientX);
  };
  const finish = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("chat-resizing");
    setChatPanelWidth(state.chatPanelWidth, { persist: true });
  };

  handle.addEventListener("pointerdown", (event) => {
    if (window.innerWidth <= 720) return;
    event.preventDefault();
    dragging = true;
    document.body.classList.add("chat-resizing");
    handle.setPointerCapture?.(event.pointerId);
  });
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);
  handle.addEventListener("keydown", (event) => {
    let next = null;
    if (event.key === "ArrowLeft") next = state.chatPanelWidth + 20;
    if (event.key === "ArrowRight") next = state.chatPanelWidth - 20;
    if (event.key === "Home") next = CHAT_PANEL_MAX_WIDTH;
    if (event.key === "End") next = CHAT_PANEL_MIN_WIDTH;
    if (next === null) return;
    event.preventDefault();
    setChatPanelWidth(next, { persist: true });
  });
  window.addEventListener("resize", () => {
    setChatPanelWidth(state.chatPanelWidth);
  });
}

function pendingConversationPermissions() {
  if (state.conversationTarget.kind === "PROVIDER_SESSION") {
    return (state.providerSessionPermissions || []).filter(
      (item) => item.state === "PENDING"
    );
  }
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    return (state.conductorPermissions || []).filter(
      (item) => item.state === "PENDING"
    );
  }
  if (state.conversationTarget.kind === "PROJECT_MASTER") {
    return (state.projectPermissions || []).filter(
      (item) => item.state === "PENDING"
    );
  }
  return [];
}

function pendingActionItems() {
  if (state.conversationTarget.kind === "SESSION_DELEGATION") {
    return { delegations: [], activities: [] };
  }
  if (state.conversationTarget.kind === "PROVIDER_SESSION") {
    return {
      delegations: [],
      activeReply: activeProviderSessionReply(),
      activities: [],
    };
  }
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    return {
      // Cross-session delivery is internal automation state.  A user chats
      // with the selected Session Card; it is not a generic work queue.
      delegations: [],
      history: [],
      activities: [],
    };
  }
  return { delegations: [], activities: [] };
}

function actionInboxCount() {
  const items = pendingActionItems();
  return (
    items.delegations.length +
    (items.activeReply ? 1 : 0) +
    items.activities.length
  );
}

function updateActionInboxBadge() {
  const count = actionInboxCount();
  const label = count > 99 ? "99+" : String(count);
  for (const badge of [elements.actionInboxBadge, elements.mobileActionInboxBadge]) {
    if (!badge) continue;
    badge.textContent = label;
    badge.classList.toggle("hidden", count === 0);
  }
}

function renderDelegationActionCard(delegation) {
  const item = node("article", "action-inbox-card delegation-action-card");
  const stateValue = String(delegation.state || "UNKNOWN").toUpperCase();
  item.dataset.state = stateValue;
  const summary = String(
    delegation.request?.summary || "Bounded delegated work"
  ).replace(/\s+/g, " " ).trim();
  const preview = summary.length > 240 ? `${summary.slice(0, 237)}...` : summary;
  const progress = delegation.progress?.summary || "Awaiting coordinator progress";
  item.append(
    node(
      "strong",
      "",
      `DELEGATION / ${delegation.request?.worker_role || "WORKER"}`
    ),
    node("p", "delegation-action-summary", preview),
    node("small", "delegation-action-state", stateValue),
    node("p", "delegation-action-progress", `Last update: ${progress}`)
  );
  if (summary.length > 240) {
    const disclosure = node("details", "action-inbox-disclosure");
    disclosure.append(
      node("summary", "", "View full request"),
      node("p", "delegation-action-full", summary)
    );
    item.append(disclosure);
  }
  if (["QUEUED", "RUNNING"].includes(stateValue)) {
    const actions = node("div", "proposal-actions");
    const cancel = node("button", "secondary-button", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => cancelConductorDelegation(delegation));
    actions.append(cancel);
    item.append(actions);
  }
  return item;
}

async function cancelConductorDelegation(delegation) {
  try {
    const result = await api(
      `/v1/conductor-room/delegations/${encodeURIComponent(
        delegation.delegation_id
      )}/cancel`,
      {
        method: "POST",
        body: { reason: "Cancelled from Universe Inbox" },
      }
    );
    state.conductorDelegations = (state.conductorDelegations || []).map((item) =>
      item.delegation_id === result.delegation.delegation_id
        ? result.delegation
        : item
    );
    renderRoomMessages();
    toast(
      result.delegation.state === "CANCELLATION_REQUESTED"
        ? "Cancellation requested; provider completion will be tracked"
        : "Queued delegation cancelled"
    );
  } catch (error) {
    toast(error.message, true);
  }
}

function renderProviderReplyActionCard(reply) {
  const item = node("article", "action-inbox-card provider-reply-action-card");
  const stateValue = String(reply?.state || "STARTING").toUpperCase();
  item.append(
    node("strong", "", "PROVIDER REPLY / ACTIVE"),
    node("p", "", "A Provider reply is still running for this room."),
    node("small", "", stateValue.replaceAll("_", " "))
  );
  const actions = node("div", "proposal-actions");
  const cancel = node("button", "secondary-button",
    stateValue === "CANCELLATION_REQUESTED" ? "Cancellation requested" : "Cancel reply"
  );
  cancel.type = "button";
  cancel.disabled = stateValue === "CANCELLATION_REQUESTED";
  cancel.addEventListener("click", cancelProviderSessionTurn);
  actions.append(cancel);
  item.append(actions);
  return item;
}

function actionInboxTitle() {
  const target = state.conversationTarget || {};
  if (target.kind === "UNIVERSE_CONDUCTOR") return "Universe actions";
  if (target.kind === "SESSION_DELEGATION") return "Delegation actions";
  // A redacted provider-session target can arrive without an alias or a
  // project id.  Fall back through the remaining coordinates instead of
  // interpolating a missing value into the heading.
  const label = [target.alias, target.projectId, target.node, target.mode]
    .map((value) => (typeof value === "string" ? value.trim() : ""))
    .find((value) => value && value !== "null" && value !== "undefined");
  return label ? `${label} actions` : "Actions";
}

function actionInboxApprovals() {
  // Approvals are this session's own pending permission prompts.  Governance
  // Proposals stay out of the Actions surface; it is not a generic work queue.
  return pendingConversationPermissions();
}

function actionInboxHistory() {
  return Array.isArray(state.gitWorkHistory) ? state.gitWorkHistory : [];
}

async function loadActionInboxHistory() {
  const target = state.conversationTarget || {};
  const projectId = target.projectId || target.node;
  if (!projectId) {
    state.gitWorkHistory = [];
    return;
  }
  const anchorRef = target.session_anchor_ref || target.current_anchor_ref || "";
  const query = anchorRef
    ? `?session_anchor_ref=${encodeURIComponent(anchorRef)}`
    : "";
  try {
    const payload = await api(
      `/v1/projects/${encodeURIComponent(projectId)}/git-work-history${query}`
    );
    state.gitWorkHistory = Array.isArray(payload.entries) ? payload.entries : [];
  } catch (error) {
    // History is observational.  A failed read must not break the dialog.
    state.gitWorkHistory = [];
  }
}

function renderGitHistoryActionCard(entry) {
  const item = node("article", "action-inbox-card git-history-action-card");
  const operation = String(entry.operation || "GIT").toUpperCase();
  const stateValue = String(entry.state || "OBSERVED").toUpperCase();
  item.dataset.state = stateValue;
  item.append(node("strong", "", `${operation} · ${stateValue}`));
  const detail = [entry.short_sha || entry.commit_sha, entry.branch, entry.remote]
    .filter(Boolean)
    .join(" · ");
  if (detail) item.append(node("p", "action-inbox-card-detail", detail));
  item.append(
    node(
      "small",
      "action-inbox-card-attribution",
      entry.attribution === "EXACT"
        ? `${entry.session_anchor_ref} · ${entry.terminal_id || "terminal UNKNOWN"}`
        : "UNATTRIBUTED · no exact Session Anchor recorded"
    )
  );
  if (entry.created_at) {
    item.append(node("small", "action-inbox-card-time", entry.created_at));
  }
  return item;
}

function renderApprovalActionCard(permission) {
  const item = node("article", "action-inbox-card approval-action-card");
  item.dataset.state = String(permission.state || "PENDING").toUpperCase();
  item.append(
    node(
      "strong",
      "",
      `${permission.provider || "UNKNOWN"} / APPROVAL REQUIRED`
    )
  );
  if (permission.title) {
    item.append(node("p", "action-inbox-card-detail", permission.title));
  }
  item.append(
    node(
      "small",
      "action-inbox-card-attribution",
      String(permission.state || "PENDING").toUpperCase()
    )
  );
  return item;
}

function renderActionInbox() {
  updateActionInboxBadge();
  if (!elements.actionInboxList) return;
  const items = pendingActionItems();
  elements.actionInboxList.replaceChildren();
  if (elements.actionInboxTitle) {
    elements.actionInboxTitle.textContent = actionInboxTitle();
  }

  const appendSection = (label, cards) => {
    if (!cards.length) return;
    const section = node("section", "action-inbox-section");
    section.setAttribute("aria-label", label);
    section.append(node("h3", "action-inbox-section-title", label));
    const list = node("div", "action-inbox-section-list");
    list.append(...cards);
    section.append(list);
    elements.actionInboxList.append(section);
  };

  const activeWork = items.delegations.map((delegation) =>
    renderDelegationActionCard(delegation)
  );
  if (items.activeReply) activeWork.push(renderProviderReplyActionCard(items.activeReply));
  appendSection("Active work", activeWork);
  appendSection(
    "History",
    actionInboxHistory().map((entry) => renderGitHistoryActionCard(entry))
  );
  appendSection(
    "Approvals",
    actionInboxApprovals().map((proposal) => renderApprovalActionCard(proposal))
  );
  if (!elements.actionInboxList.childElementCount) {
    elements.actionInboxList.append(
      node("p", "empty-copy", "No active work.")
    );
  }
}

async function openActionInbox() {
  renderActionInbox();
  if (elements.actionInboxDialog && !elements.actionInboxDialog.open) {
    elements.actionInboxDialog.showModal();
  }
  await loadActionInboxHistory();
  renderActionInbox();
}

function finishRoomMessageRender(previousScrollTop, stickToBottom = false) {
  elements.roomMessageList.scrollTop = stickToBottom
    ? elements.roomMessageList.scrollHeight
    : previousScrollTop;
  renderActionInbox();
}

function renderRoomMessages() {
  const previousScrollTop = elements.roomMessageList.scrollTop;
  const stickToBottom =
    elements.roomMessageList.scrollHeight -
      elements.roomMessageList.clientHeight -
      previousScrollTop < 48;
  const transcriptExpanded = Boolean(
    elements.conversationLayer?.classList.contains("expanded") ||
      elements.conversationExpand?.checked
  );
  elements.roomMessageList.replaceChildren();
  if (state.conversationTarget.kind === "NONE") {
    elements.roomMessageList.append(
      node("p", "empty-copy", "Select a session to chat.")
    );
    finishRoomMessageRender(previousScrollTop, stickToBottom);
    return;
  }
  for (const permission of pendingConversationPermissions()) {
    elements.roomMessageList.append(renderPermissionCard(permission));
  }
  if (state.conversationTarget.kind === "SESSION_DELEGATION") {
    const draft = state.sessionDelegationDraft || state.conversationTarget;
    const delegation = (state.sessionDelegations || []).find(
      (item) =>
        String(item.origin_anchor_ref || "") === String(draft.origin_anchor_ref || "") &&
        String(item.target_anchor_ref || "") === String(draft.target_anchor_ref || "")
    );
    const item = node("article", "room-message session-delegation-message");
    item.append(
      node("strong", "", "CROSS-SESSION DELEGATION / NOT DIRECT CHAT"),
      node("p", "", `${draft.origin_label || "Origin"} → ${draft.target_label || "Target"}`),
      node("small", "", `origin_anchor_ref: ${draft.origin_anchor_ref || "UNKNOWN"}`),
      node("small", "", `target_anchor_ref: ${draft.target_anchor_ref || "UNKNOWN"}`),
      node("small", "", `status: ${delegation?.state || draft.state || "DRAFT"}`)
    );
    if (delegation && isCompletedSessionDelegation(delegation)) {
      const rejoin = node("button", "secondary-button", "Open origin session");
      rejoin.type = "button";
      rejoin.addEventListener("click", () => {
        rejoinDelegationOrigin(delegation).catch((error) => toast(error.message, true));
      });
      item.append(rejoin);
    }
    elements.roomMessageList.append(item);
    finishRoomMessageRender(previousScrollTop, stickToBottom);
    return;
  }
  if (state.conversationTarget.kind === "PROVIDER_SESSION") {
    if (!state.providerSessionMessages.length) {
      elements.roomMessageList.append(
        node("p", "empty-copy", "No messages observed in this live session yet")
      );
      finishRoomMessageRender(previousScrollTop, stickToBottom);
      return;
    }
    const providerMessages = transcriptExpanded
      ? state.providerSessionMessages
      : state.providerSessionMessages.slice(-20);
    for (const message of providerMessages) {
      const item = node(
        "article",
        `room-message provider-session-message ${String(message.role || "").toLowerCase()}`
      );
      item.append(
        node("strong", "", String(message.role || "UNKNOWN")),
        markdownBody(message.body),
        node(
          "small",
          "",
          message.state === "FAILED"
            ? `${message.state} / ${message.error_code || "PROVIDER_ERROR"}`
            : String(message.state || "UNKNOWN")
        )
      );
      elements.roomMessageList.append(item);
    }
    finishRoomMessageRender(previousScrollTop, stickToBottom);
    return;
  }
  if (state.conversationTarget.kind === "UNIVERSE_CONDUCTOR") {
    if (!state.conductorMessages.length) {
      const item = node("article", "room-message conductor-message");
      item.append(
        node("strong", "", "UNIVERSE / CONDUCTOR"),
        node("p", "", "Universe control room is active."),
        node("small", "", "Send a message here or use + to call a Project Master.")
      );
      elements.roomMessageList.append(item);
    }
    const conductorMessages = transcriptExpanded
      ? state.conductorMessages
      : state.conductorMessages.slice(-8);
    for (const message of conductorMessages) {
      const item = node("article", "room-message conductor-message");
      const failure = message.failure?.reason
        ? ` / ${message.failure.reason}`
        : "";
      const provider = message.provider ? ` / ${message.provider}` : "";
      item.append(
        node("strong", "", `${message.sender} / ${message.kind}`),
        markdownBody(message.body),
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
        markdownBody(stream.body || stream.state || "Thinking"),
        node("small", "", stream.state || "Responding")
      );
      elements.roomMessageList.append(item);
    }
    finishRoomMessageRender(previousScrollTop, stickToBottom);
    return;
  }
  if (!state.roomMessages.length) {
    elements.roomMessageList.append(
      node(
        "p",
        "empty-copy",
        `No messages for ${state.conversationTarget.projectId} Master`
      )
    );
    finishRoomMessageRender(previousScrollTop, stickToBottom);
    return;
  }
  const roomMessages = transcriptExpanded
    ? state.roomMessages
    : state.roomMessages.slice(-8);
  for (const message of roomMessages) {
    const item = node("article", "room-message");
    item.append(
      node("strong", "", `${message.sender} / ${message.kind}`),
      markdownBody(message.body),
      node("small", "", message.delivery_state)
    );
    elements.roomMessageList.append(item);
  }
  for (const reply of Object.values(state.projectStreamReplies)) {
    const item = node("article", "room-message streaming");
    item.append(
      node("strong", "", "PROJECT_MASTER / LIVE"),
      markdownBody(reply.body || "Thinking..."),
      node("small", "", reply.state)
    );
    elements.roomMessageList.append(item);
  }
  finishRoomMessageRender(previousScrollTop, stickToBottom);
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
    const isRoomParticipant = permission.scope_kind === "ROOM_PARTICIPANT";
    const isProviderSession = permission.scope_kind === "PROVIDER_SESSION";
    const endpoint = isProviderSession
      ? `/v1/provider-sessions/${encodeURIComponent(
          permission.chat_key
        )}/permissions/${encodeURIComponent(
          permission.request_id
        )}/decision`
      : isConductor
      ? `/v1/conductor-room/agent-session/permissions/${encodeURIComponent(
          permission.request_id
        )}/decision`
      : isRoomParticipant
        ? `/v1/rooms/${encodeURIComponent(
            permission.room_id
          )}/bindings/${encodeURIComponent(
            permission.binding_id
          )}/permissions/${encodeURIComponent(
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
    if (isProviderSession) {
      const cache = providerSessionRoomCacheFor(permission.chat_key);
      const updatedPermission =
        redactProviderSessionPermission(result.permission) || result.permission;
      if (cache && updatedPermission) {
        cache.permissions = cache.permissions.map((item) =>
          item.request_id === permission.request_id
            ? updatedPermission
            : item
        );
      }
      syncSelectedProviderSessionState(permission.chat_key);
      renderRoomMessages();
      toast("Agent permission decision delivered");
      return;
    }
    if (isRoomParticipant) {
      const snapshot = state.activeMultiRoomSnapshot;
      if (snapshot?.room?.room_id === permission.room_id) {
        snapshot.permissions = (snapshot.permissions || []).map((item) =>
          item.request_id === permission.request_id ? result.permission : item
        );
        renderActiveMultiRoom();
      }
      toast("Agent permission decision delivered");
      return;
    }
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
  const model = setting?.model_ref || setting?.resolved_model || "host default";
  const effort = setting?.effort || setting?.resolved_effort || "AUTO";
  const mode =
    setting?.scope_kind === "UNIVERSE_CONDUCTOR" ? "CONDUCTOR" : "MASTER";
  if (configured === "AUTO") {
    const providerState = resolved === "UNAVAILABLE"
      ? "Auto / no CLI available"
      : `Auto / currently ${resolved}`;
    return `${providerState} / ${model} / ${effort} / ${sessionConnectionText(
      setting?.session_connection,
      mode
    )}`;
  }
  const capability = providerCapability(configured);
  const providerState = capability?.status === "AVAILABLE"
    ? `${configured} available`
    : `${configured} unavailable / ${capability?.reason || "CLI unavailable"}`;
  return `${providerState} / ${model} / ${effort} / ${sessionConnectionText(
    setting?.session_connection,
    mode
  )}`;
}

function providerCatalogModels(provider) {
  const key = String(provider || "").toUpperCase();
  if (!key || key === "AUTO") return [];
  const entry = state.providerModels?.providers?.[key];
  return Array.isArray(entry?.models) ? entry.models : [];
}

function openNewSessionDialog() {
  if (!elements.newSessionDialog) return;
  const providers = state.providerSettings?.available_providers || [];
  elements.newSessionProvider.replaceChildren();
  for (const p of providers) {
    const key = String(p.provider || "").toUpperCase();
    if (!key) continue;
    const option = node("option", "", key === "CODEX" ? "Codex" : key === "CLAUDE" ? "Claude" : "Grok");
    option.value = key;
    option.disabled = p.status === "UNAVAILABLE";
    elements.newSessionProvider.append(option);
  }
  if (!elements.newSessionProvider.options.length) {
    for (const key of ["CODEX", "CLAUDE", "GROK"]) {
      const option = node("option", "", key);
      option.value = key;
      elements.newSessionProvider.append(option);
    }
  }
  const fillNewSessionModelSelect = (provider) => {
    const key = String(provider || "").toUpperCase();
    const models = [
      ...new Set(
        [
          ...providerCatalogModels(key),
          providerCapability(key)?.model,
          state.providerModels?.providers?.[key]?.default,
        ].filter(Boolean)
      ),
    ];
    elements.newSessionModel.replaceChildren();
    for (const modelId of models) {
      const option = node("option", "", modelId);
      option.value = modelId;
      elements.newSessionModel.append(option);
    }
    if (!models.length) {
      const option = node("option", "", "Host default");
      option.value = "";
      elements.newSessionModel.append(option);
    }
    elements.newSessionModel.value = models[0] || "";
  };
  fillNewSessionModelSelect(elements.newSessionProvider.value);
  elements.newSessionProvider.onchange = () => fillNewSessionModelSelect(elements.newSessionProvider.value);

  const projects = state.projects || [];
  elements.newSessionProject.replaceChildren();
  for (const project of projects) {
    const option = node("option", "", project.project_id);
    option.value = project.project_id;
    elements.newSessionProject.append(option);
  }
  const currentProjectId = state.selectedProject?.project_id || projects[0]?.project_id || "";
  elements.newSessionProject.value = currentProjectId;

  const updateProjectRowVisibility = () => {
    if (elements.newSessionProjectRow) {
      elements.newSessionProjectRow.hidden = elements.newSessionMode.value === "CONDUCTOR";
    }
  };
  elements.newSessionMode.value = "CONDUCTOR";
  updateProjectRowVisibility();
  elements.newSessionMode.onchange = updateProjectRowVisibility;

  if (elements.newSessionStatus) elements.newSessionStatus.textContent = "";
  if (elements.newSessionError) elements.newSessionError.textContent = "";
  if (elements.newSessionSubmit) elements.newSessionSubmit.disabled = false;
  elements.newSessionDialog.showModal();
}

async function submitNewSession(event) {
  event.preventDefault();
  if (!elements.newSessionSubmit) return;
  const mode = String(elements.newSessionMode?.value || "CONDUCTOR").toUpperCase();
  const projectId = String(elements.newSessionProject?.value || "").trim();
  const provider = String(elements.newSessionProvider?.value || "").toUpperCase();
  const modelRef = String(elements.newSessionModel?.value || "").trim();
  const effort = String(elements.newSessionEffort?.value || "AUTO").toUpperCase();
  if (!provider) {
    if (elements.newSessionError) elements.newSessionError.textContent = "Choose a provider first";
    return;
  }
  if (mode === "MASTER" && !projectId) {
    if (elements.newSessionError) elements.newSessionError.textContent = "Choose a project first";
    return;
  }
  const project = (state.projects || []).find(
    (item) => String(item.project_id || "").toLowerCase() === projectId.toLowerCase()
  );
  const universeProject = (state.projects || []).find((item) =>
    String(item.project_id || "").toLowerCase() === "universe" ||
    String(item.metadata?.network_role || "").toUpperCase() === "UNIVERSE_HOME"
  );
  if (mode === "MASTER" && !project?.project_root) {
    if (elements.newSessionError) elements.newSessionError.textContent = "Project has no registered root path";
    return;
  }
  elements.newSessionSubmit.disabled = true;
  if (elements.newSessionStatus) {
    elements.newSessionStatus.textContent = `Starting ${provider} / ${modelRef || "host default"} / ${effort}...`;
  }
  if (elements.newSessionError) elements.newSessionError.textContent = "";
  try {
    const terminalProject = mode === "CONDUCTOR" ? universeProject : project;
    if (!terminalProject?.project_root) {
      throw new Error("Universe home has no registered root path");
    }
    await startNewNodeModeSession({
      project: terminalProject,
      nodeId: terminalProject.project_id,
      mode,
      provider,
      modelRef,
      effort,
    });
    elements.newSessionDialog.close();
    toast(`New ${mode === "CONDUCTOR" ? "Conductor" : "Master"} session started: ${provider} / ${modelRef || "host default"} / ${effort}`);
  } catch (error) {
    if (elements.newSessionError) elements.newSessionError.textContent = error.message;
  } finally {
    if (elements.newSessionSubmit) elements.newSessionSubmit.disabled = false;
    if (elements.newSessionStatus) elements.newSessionStatus.textContent = "";
  }
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
    ...operableProjects().map((project) => ({
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
    fillWorkerBindingModelSelect(model, provider.value, profile?.model_ref || "");
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

async function setupProviderHooks(opts = {}) {
  const button = elements.setupProviderHooks;
  const status = elements.setupProviderHooksStatus;
  if (elements.settingsError) elements.settingsError.textContent = "";
  if (status) status.textContent = "Writing hook files…";
  if (button) button.disabled = true;
  try {
    const result = await api("/v1/settings/setup-provider-hooks", {
      method: "POST",
      body: { providers: ["CODEX", "GROK", "CLAUDE"], global: true, ...opts },
    });
    const lines = Object.entries(result.providers || {})
      .map(([p, r]) => `${p}: ${r.status || r}`)
      .join(", ");
    const repaired = (result.repairs || []).filter((item) => item.status === "REPAIRED");
    const suffix = repaired.length
      ? `; repaired ${repaired.map((item) => item.mode || "mode").join(", ")} to GROK`
      : "";
    const message = `CLI hooks: ${lines || "done"}${suffix}`;
    if (status) status.textContent = message;
    toast(message);
  } catch (error) {
    const message = error.message || "CLI hook setup failed";
    if (status) status.textContent = message;
    if (elements.settingsError) elements.settingsError.textContent = message;
    toast(message, true);
  } finally {
    if (button) button.disabled = false;
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
  const showAll = state.multiRoomShowAll || false;
  const emptyMeeting = state.multiRooms.filter(
    (r) => r.room_type === "MEETING" && (r.participant_count || 0) === 0
  );
  const visible = showAll
    ? state.multiRooms
    : state.multiRooms.filter(
        (r) => r.room_type !== "MEETING" || (r.participant_count || 0) > 0
      );
  elements.multiRoomList.replaceChildren();
  const header = node("div", "multi-room-list-header");
  const toggle = node("button", "secondary-button compact-action");
  toggle.type = "button";
  toggle.textContent = showAll
    ? "Hide empty rooms"
    : `Show empty (${emptyMeeting.length})`;
  toggle.style.display = emptyMeeting.length || showAll ? "" : "none";
  toggle.addEventListener("click", () => {
    state.multiRoomShowAll = !showAll;
    refreshMultiRooms().catch(() => {});
  });
  header.append(toggle);
  elements.multiRoomList.append(header);
  if (!visible.length) {
    elements.multiRoomList.append(node("p", "empty-copy", showAll ? "No rooms yet" : "No active rooms"));
    return;
  }
  for (const room of visible) {
    const isLive = (room.participant_count || 0) > 0;
    const row = node("div", "remote-access-row");
    const copy = node("div", "remote-access-copy");
    copy.append(
      node("strong", "", `${room.room_type} · ${room.title}`),
      node("small", "", `${isLive ? `${room.participant_count} live` : "empty"} · ${room.room_id}`)
    );
    const open = node("button", "secondary-button compact-action", "Open");
    open.type = "button";
    open.addEventListener("click", () => {
      openMultiRoom(room.room_id).catch((error) => {
        elements.settingsError.textContent = error.message;
      });
    });
    const del = node("button", "secondary-button compact-action danger-action", "Delete");
    del.type = "button";
    del.disabled = isLive;
    del.title = isLive ? "Cannot delete a room with active participants" : "Delete this room";
    del.addEventListener("click", async () => {
      try {
        await api(`/v1/rooms/${encodeURIComponent(room.room_id)}`, { method: "DELETE" });
        await refreshMultiRooms();
      } catch (error) {
        toast(error.message, true);
      }
    });
    row.append(copy, open, del);
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
  const pendingPermissions = (snap.permissions || []).filter(
    (permission) => permission.state === "PENDING"
  );
  const permissionList = node("div", "remote-access-list");
  for (const permission of pendingPermissions) {
    permissionList.append(renderPermissionCard(permission));
  }
  const transcript = node("pre", "remote-access-endpoint");
  transcript.textContent = (snap.messages || [])
    .slice(-5)
    .map((message) => `${message.author_role}: ${message.body_text}`)
    .join("\n");
  elements.multiRoomDetail.replaceChildren(
    summary,
    participantList,
    permissionList,
    transcript
  );
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
      permissions:
        payload.permissions || state.activeMultiRoomSnapshot?.permissions || [],
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
      payload.type === "AGENT_PERMISSION" ||
      payload.type === "AGENT_PERMISSION_RESOLVED"
    ) {
      const permission = payload.permission;
      const snapshot = state.activeMultiRoomSnapshot;
      if (permission?.request_id && snapshot) {
        snapshot.permissions = [
          ...(snapshot.permissions || []).filter(
            (item) => item.request_id !== permission.request_id
          ),
          permission,
        ];
        renderActiveMultiRoom();
      }
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
  renderProviderModelCatalog();
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
  const tabs = Array.from(root.querySelectorAll(`[${tabAttr}]`));
  const panels = Array.from(root.querySelectorAll(`[${panelAttr}]`));
  const panelsById = new Map(
    panels.map((panel) => [panel.getAttribute(panelAttr), panel])
  );
  for (const tab of tabs) {
    const tabId = tab.getAttribute(tabAttr);
    const panel = panelsById.get(tabId);
    if (root.id && tabId && panel) {
      if (!tab.id) tab.id = `${root.id}-${tabId}-tab`;
      if (!panel.id) panel.id = `${root.id}-${tabId}-panel`;
      tab.setAttribute("aria-controls", panel.id);
      panel.setAttribute("aria-labelledby", tab.id);
    }
    const active = tabId === activeId;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const panel of panels) {
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
    const requests = [];
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
    state.conductorDelegations = result.delegations || [];
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
      state.conductorDelegations = payload.delegations || [];
      state.conductorPermissions = payload.permissions || [];
      state.conductorRuntimeBinding = payload.runtime_binding || null;
      renderComposerState();
      renderRoomMessages();
      return;
    }
    if (payload.type === "CONDUCTOR_DELEGATION") {
      const delegation = payload.delegation;
      if (delegation?.delegation_id) {
        state.conductorDelegations = [
          delegation,
          ...state.conductorDelegations.filter(
            (item) => item.delegation_id !== delegation.delegation_id
          ),
        ].slice(0, 100);
        renderRoomMessages();
      }
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

function redactProviderSessionObject(value) {
  if (Array.isArray(value)) {
    return value.map((item) => redactProviderSessionObject(item));
  }
  if (!value || typeof value !== "object") return value;
  const redacted = {};
  const blockedSessionRef = ["session", "ref"].join("_");
  const blockedSourcePath = ["source", "path"].join("_");
  for (const [key, item] of Object.entries(value)) {
    const normalizedKey = String(key).toLowerCase();
    if (
      normalizedKey.includes(blockedSessionRef) ||
      normalizedKey === blockedSourcePath ||
      normalizedKey.includes("transcript")
    ) {
      continue;
    }
    redacted[key] = redactProviderSessionObject(item);
  }
  return redacted;
}

function redactProviderSessionMessage(message) {
  const messageId = String(message?.message_id || "").trim();
  if (!messageId) return null;
  const safe = { message_id: messageId };
  for (const key of [
    "role",
    "body",
    "state",
    "created_at",
    "updated_at",
    "error_code",
  ]) {
    if (message[key] === undefined || message[key] === null) continue;
    safe[key] = key === "body" ? String(message[key]) : message[key];
  }
  return safe;
}

function redactProviderSessionPermission(permission) {
  const requestId = String(permission?.request_id || "").trim();
  if (!requestId) return null;
  const safe = {};
  for (const key of [
    "request_id",
    "chat_key",
    "scope_kind",
    "state",
    "provider",
    "project_id",
    "room_id",
    "binding_id",
  ]) {
    if (permission[key] !== undefined && permission[key] !== null) {
      safe[key] = permission[key];
    }
  }
  if (Array.isArray(permission.options)) {
    safe.options = permission.options.map((option) => ({
      kind: option?.kind,
      name: option?.name,
      optionId: option?.optionId,
    }));
  }
  if (permission.tool_call && typeof permission.tool_call === "object") {
    safe.tool_call = redactProviderSessionObject(permission.tool_call);
  }
  return safe;
}

function redactProviderSessionTarget(target) {
  if (!target || typeof target !== "object") return null;
  const safe = {};
  const fields = [
    ["chat_key", "chat_key"],
    ["provider", "provider"],
    ["project_id", "projectId"],
    ["projectId", "projectId"],
    ["node", "node"],
    ["mode", "mode"],
    ["alias", "alias"],
    ["current_anchor_ref", "current_anchor_ref"],
    ["model_ref", "model_ref"],
  ];
  for (const [source, destination] of fields) {
    if (
      safe[destination] === undefined &&
      target[source] !== undefined &&
      target[source] !== null
    ) {
      safe[destination] = target[source];
    }
  }
  return safe;
}

function dedupeProviderSessionMessages(messages) {
  const byId = new Map();
  for (const message of Array.isArray(messages) ? messages : []) {
    const safe = redactProviderSessionMessage(message);
    if (!safe) continue;
    byId.set(safe.message_id, safe);
  }
  return [...byId.values()].sort((left, right) =>
    String(left.created_at || "").localeCompare(String(right.created_at || ""))
  );
}

function providerSessionRoomForChatKey(chatKey) {
  const key = String(chatKey || "").trim();
  if (!key) return null;
  return (state.providerChatRooms || []).find(
    (room) => String(room?.chat_key || "").trim() === key
  ) || null;
}

function providerSessionObservedProjectId(room) {
  const binding = room?.binding || {};
  const boundProject = String(
    binding.current_project_id || binding.node || ""
  ).trim();
  if (boundProject) return boundProject;
  return String(projectForVendorWorkspace(room)?.project_id || "").trim();
}

function providerSessionRoomIsEligible(room) {
  const chatKey = String(room?.chat_key || "").trim();
  const sessionKind = String(room?.session_kind || "CHAT").toUpperCase();
  const bindingState = String(room?.binding?.state || "").toUpperCase();
  const currentness = String(
    room?.binding?.observer_currentness || ""
  ).toUpperCase();
  const identityState = String(room?.identity_state || "").toUpperCase();
  const projectId = providerSessionObservedProjectId(room);
  const identityAttached = providerSessionRoomIdentityIsAttached(room);
  return Boolean(
    chatKey &&
      projectId &&
      sessionKind !== "WORKER" &&
      identityAttached &&
      ["BOUND", "ANCHOR_OBSERVED"].includes(bindingState) &&
      currentness === "CURRENT"
  );
}

function providerSessionRoomIdentityIsAttached(room) {
  const identityState = String(room?.identity_state || "").toUpperCase();
  if (identityState === "VERIFIED") return true;
  if (identityState !== "SUPERVISOR_OBSERVED") return false;
  const binding = room?.binding || {};
  const bindingState = String(binding.state || "").toUpperCase();
  return Boolean(
    ["BOUND", "ANCHOR_OBSERVED"].includes(bindingState) &&
      String(binding.universe_session_id || "").trim() &&
      String(binding.session_anchor_ref || "").trim()
  );
}

function providerSessionRoomCacheFor(chatKey) {
  const key = String(chatKey || "").trim();
  if (!key) return null;
  if (!state.providerSessionRoomCaches[key]) {
    const room = providerSessionRoomForChatKey(key);
    state.providerSessionRoomCaches[key] = {
      chat_key: key,
      messages: [],
      permissions: [],
      workStatus: null,
      actions: [],
      connection: null,
      target: null,
      streamState: "IDLE",
      unread: 0,
      activityState: String(room?.activity_state || "UNKNOWN").toUpperCase(),
      lastEventAt: null,
      lastEventType: null,
    };
  }
  return state.providerSessionRoomCaches[key];
}

function providerSessionCache(chatKey) {
  return providerSessionRoomCacheFor(chatKey);
}

function providerSessionRoomIsSelected(chatKey) {
  return (
    state.conversationTarget.kind === "PROVIDER_SESSION" &&
    String(state.conversationTarget.chat_key || "").trim() ===
      String(chatKey || "").trim()
  );
}

function syncSelectedProviderSessionState(chatKey = null) {
  const selectedKey =
    state.conversationTarget.kind === "PROVIDER_SESSION"
      ? String(state.conversationTarget.chat_key || "").trim()
      : "";
  const requestedKey = String(chatKey || "").trim();
  if (requestedKey && requestedKey !== selectedKey) return;
  const cache = providerSessionRoomCacheFor(selectedKey);
  state.providerSessionMessages = cache ? cache.messages : [];
  state.providerSessionPermissions = cache ? cache.permissions : [];
  state.providerSessionConnection = cache ? cache.connection : null;
  state.providerSessionStreamState = selectedKey
    ? state.providerSessionStreamStates[selectedKey] ||
      cache?.streamState ||
      "IDLE"
    : "IDLE";
}

function mergeProviderSessionMessages(chatKey, messages) {
  const cache = providerSessionRoomCacheFor(chatKey);
  if (!cache) return;
  cache.messages = dedupeProviderSessionMessages([
    ...cache.messages,
    ...(Array.isArray(messages) ? messages : [messages]),
  ]);
  syncSelectedProviderSessionState(chatKey);
}

function mergeProviderLiveDeltasIntoRoom(chatKey) {
  const key = String(chatKey || "").trim();
  const deltas = state.providerLiveDeltas[key] || [];
  if (!key || !deltas.length) return;
  mergeProviderSessionMessages(
    key,
    deltas.map((delta) => ({
      message_id: delta.excerpt_id,
      role: delta.role,
      body: delta.text,
      state: "COMPLETED",
    }))
  );
}

function mergeProviderSessionPermission(chatKey, permission) {
  const safe = redactProviderSessionPermission(permission);
  const cache = providerSessionRoomCacheFor(chatKey);
  if (!cache || !safe) return;
  cache.permissions = [
    ...cache.permissions.filter((item) => item.request_id !== safe.request_id),
    safe,
  ];
  syncSelectedProviderSessionState(chatKey);
}

function clearProviderSessionUnread(chatKey) {
  const cache = providerSessionRoomCacheFor(chatKey);
  if (!cache) return;
  cache.unread = 0;
  renderSessionRail();
}

function markProviderSessionRead(chatKey) {
  clearProviderSessionUnread(chatKey);
}

function markProviderSessionActivity(chatKey, type, envelope) {
  const cache = providerSessionRoomCacheFor(chatKey);
  if (!cache) return;
  cache.lastEventType = type;
  cache.lastEventAt = String(
    envelope?.emitted_at || new Date().toISOString()
  );
  cache.activityState = "LIVE";
  if (!providerSessionRoomIsSelected(chatKey)) {
    cache.unread = Math.min(99, Number(cache.unread || 0) + 1);
  }
}

function providerSessionActivityState(room) {
  const key = String(room?.chat_key || "").trim();
  const cache = key ? state.providerSessionRoomCaches[key] : null;
  return String(
    cache?.activityState || room?.activity_state || "UNKNOWN"
  ).toUpperCase();
}

function providerSessionRoomIsOpenable(room) {
  const chatKey = String(room?.chat_key || "").trim();
  const sessionKind = String(room?.session_kind || "CHAT").toUpperCase();
  const projectId = providerSessionObservedProjectId(room);
  return Boolean(
    chatKey &&
      projectId &&
      sessionKind !== "WORKER" &&
      providerSessionRoomIdentityIsAttached(room)
  );
}

function providerSessionUnreadCount(room) {
  const key = String(room?.chat_key || "").trim();
  const cache = key ? state.providerSessionRoomCaches[key] : null;
  return Math.max(0, Number(cache?.unread || 0));
}

function applyProviderSessionSnapshot(snapshot, chatKey = null) {
  const key = String(
    chatKey ||
      snapshot?.chat_key ||
      state.conversationTarget.chat_key ||
      ""
  ).trim();
  const cache = providerSessionRoomCacheFor(key);
  if (!cache) return false;
  cache.messages = dedupeProviderSessionMessages(snapshot.messages);
  cache.permissions = (
    Array.isArray(snapshot.permissions) ? snapshot.permissions : []
  )
    .map((permission) => redactProviderSessionPermission(permission))
    .filter(Boolean);
  cache.connection = redactProviderSessionObject(snapshot.connection || null);
  cache.workStatus = redactProviderSessionObject(snapshot.work_status || null);
  cache.actions = (Array.isArray(snapshot.actions) ? snapshot.actions : [])
    .map((action) => redactProviderSessionObject(action))
    .filter(Boolean);
  cache.target = redactProviderSessionTarget(snapshot.target) || cache.target;
  if (providerSessionRoomIsSelected(key) && cache.target) {
    state.conversationTarget = {
      ...state.conversationTarget,
      ...cache.target,
      kind: "PROVIDER_SESSION",
    };
  }
  if (!cache.messages.length) mergeProviderLiveDeltasIntoRoom(key);
  syncSelectedProviderSessionState(key);
  return true;
}

function workStatusNotificationText(workStatus) {
  const stateValue = String(workStatus?.state || "UNKNOWN").toUpperCase();
  const operation = String(workStatus?.operation || "WORK").replaceAll("_", " ");
  if (stateValue === "STARTED") return `${operation} started`;
  if (stateValue === "COMPLETED") {
    const shortSha = String(workStatus?.details?.short_sha || "").trim();
    return `${operation} completed${shortSha ? ` · ${shortSha}` : ""}`;
  }
  if (stateValue === "CANCELLED") return `${operation} cancelled`;
  if (stateValue === "FAILED") {
    const code = String(workStatus?.error_code || "UNKNOWN");
    return `${operation} failed (${code})`;
  }
  return `${operation} ${stateValue.toLowerCase()}`;
}

function applyProviderSessionPayload(chatKey, payload, envelope) {
  const key = String(chatKey || "").trim();
  const cache = providerSessionRoomCacheFor(key);
  const type = String(payload?.type || "").toUpperCase();
  if (!cache || !type) return false;
  let handled = false;
  if (type === "SNAPSHOT") {
    handled = applyProviderSessionSnapshot(payload, key);
  } else if (type === "PROVIDER_SESSION_MESSAGE") {
    const message = redactProviderSessionMessage(payload.message);
    if (message) mergeProviderSessionMessages(key, message);
    handled = true;
  } else if (type === "PROVIDER_SESSION_DELTA") {
    const messageId = String(payload.message_id || "");
    const delta = String(payload.delta || "");
    cache.messages = cache.messages.map((message) =>
      message.message_id === messageId
        ? {
            ...message,
            body: String(message.body || "") + delta,
            state: "STREAMING",
          }
        : message
    );
    syncSelectedProviderSessionState(key);
    handled = true;
  } else if (type === "PROVIDER_SESSION_WORK_STATUS") {
    cache.workStatus = redactProviderSessionObject(payload.work_status || null);
    if (cache.workStatus) {
      toast(
        workStatusNotificationText(cache.workStatus),
        String(cache.workStatus.state || "").toUpperCase() === "FAILED"
      );
    }
    handled = true;
  } else if (type === "PROVIDER_SESSION_ACTION") {
    const action = redactProviderSessionObject(payload.action || null);
    if (action) {
      cache.actions = [
        ...(cache.actions || []).filter((item) => item.action_id !== action.action_id),
        action,
      ];
    }
    handled = true;
  } else if (type === "PROVIDER_SESSION_ACTION_DELETED") {
    const actionId = String(payload.action_id || "").trim();
    cache.actions = (cache.actions || []).filter(
      (item) => item.action_id !== actionId
    );
    handled = true;
  } else if (
    type === "PROVIDER_SESSION_PERMISSION" ||
    type === "PROVIDER_SESSION_PERMISSION_RESOLVED"
  ) {
    mergeProviderSessionPermission(key, payload.permission);
    handled = true;
  }
  if (!handled) return false;
  cache.streamState = "LIVE";
  state.providerSessionStreamStates[key] = "LIVE";
  syncSelectedProviderSessionState(key);
  if (type !== "SNAPSHOT") {
    markProviderSessionActivity(key, type, envelope);
  }
  return true;
}

function closeProviderSessionStream(chatKey) {
  const key = String(chatKey || "").trim();
  if (!key) return;
  const source = state.providerSessionStreams[key];
  if (source) source.close();
  delete state.providerSessionStreams[key];
  state.providerSessionStreamStates[key] = "IDLE";
  const cache = providerSessionRoomCacheFor(key);
  if (cache) cache.streamState = "IDLE";
  if (providerSessionRoomIsSelected(key)) {
    state.providerSessionStreamState = "IDLE";
    renderComposerState();
  }
  renderSessionRail();
}

function closeAllProviderSessionStreams() {
  for (const key of Object.keys(state.providerSessionStreams)) {
    closeProviderSessionStream(key);
  }
}

function syncProviderSessionSubscriptions() {
  const eligible = new Set(
    (state.providerChatRooms || [])
      .filter((room) => providerSessionRoomIsEligible(room))
      .map((room) => String(room.chat_key || "").trim())
      .filter(Boolean)
  );
  for (const key of Object.keys(state.providerSessionStreams)) {
    if (eligible.has(key)) continue;
    closeProviderSessionStream(key);
    if (!providerSessionRoomIsSelected(key)) {
      delete state.providerSessionRoomCaches[key];
      delete state.providerSessionStreamStates[key];
    }
  }
  for (const key of Object.keys(state.providerSessionRoomCaches)) {
    if (eligible.has(key) || providerSessionRoomIsSelected(key)) continue;
    delete state.providerSessionRoomCaches[key];
    delete state.providerSessionStreamStates[key];
  }
  for (const key of eligible) {
    openProviderSessionStream(key);
  }
  syncSelectedProviderSessionState();
}

function reconcileProviderSessionStreams() {
  syncProviderSessionSubscriptions();
}

async function refreshProviderSession(chatKey) {
  const key = String(chatKey || "").trim();
  if (!providerSessionRoomIsOpenable(providerSessionRoomForChatKey(key))) {
    throw new Error("This Provider Session is not an attached persistent session");
  }
  const snapshot = await api(
    "/v1/provider-sessions/" + encodeURIComponent(key)
  );
  applyProviderSessionSnapshot(snapshot, key);
  clearProviderSessionUnread(key);
  return snapshot;
}

function openProviderSessionStream(chatKey) {
  const key = String(chatKey || "").trim();
  const room = providerSessionRoomForChatKey(key);
  // Background subscriptions remain currentness-gated by
  // providerSessionRoomIsEligible().  An explicitly selected attached
  // Supervisor session may have no provider source-file observation (and thus
  // UNKNOWN observer currentness), but it is still streamable after the
  // private resolver attests its persistent target.
  if (!providerSessionRoomIsOpenable(room)) return null;
  if (state.providerSessionStreams[key]) {
    syncSelectedProviderSessionState(key);
    return state.providerSessionStreams[key];
  }
  const cache = providerSessionRoomCacheFor(key);
  const source = new EventSource(
    "/v1/provider-sessions/" + encodeURIComponent(key) + "/stream"
  );
  state.providerSessionStreams[key] = source;
  state.providerSessionStreamStates[key] = "CONNECTING";
  if (cache) cache.streamState = "CONNECTING";
  syncSelectedProviderSessionState(key);
  renderSessionRail();
  source.addEventListener("open", () => {
    if (state.providerSessionStreams[key] !== source) return;
    state.providerSessionStreamStates[key] = "LIVE";
    if (cache) cache.streamState = "LIVE";
    syncSelectedProviderSessionState(key);
    renderSessionRail();
  });
  source.addEventListener("provider-session", (event) => {
    let envelope;
    try {
      envelope = JSON.parse(event.data);
    } catch (error) {
      console.warn("Provider Session stream payload is invalid", error);
      return;
    }
    if (String(envelope.chat_key || key).trim() !== key) return;
    if (state.providerSessionStreams[key] !== source) return;
    const payload = envelope.payload || {};
    if (!applyProviderSessionPayload(key, payload, envelope)) return;
    if (providerSessionRoomIsSelected(key)) {
      renderComposerState();
      renderComposerActions();
      renderRoomMessages();
    } else {
      renderSessionRail();
      renderNodeModes();
    }
  });
  source.addEventListener("error", () => {
    if (state.providerSessionStreams[key] !== source) return;
    state.providerSessionStreamStates[key] = "RECONNECTING";
    if (cache) cache.streamState = "RECONNECTING";
    if (providerSessionRoomIsSelected(key)) {
      state.providerSessionStreamState = "RECONNECTING";
      renderComposerState();
    }
    renderSessionRail();
  });
  return source;
}

async function openProviderChatSession(room, options = {}) {
  state.conversationSurface = "CHAT";
  const isCurrent =
    typeof options.isCurrent === "function" ? options.isCurrent : () => true;
  if (!isCurrent()) return false;
  const binding = room?.binding || {};
  const projectId = String(
    binding.current_project_id || binding.node || ""
  ).trim();
  const chatKey = String(room?.chat_key || "").trim();
  if (!projectId || !providerSessionRoomIsOpenable(room)) {
    throw new Error("This Provider Session is not an attached persistent session");
  }
  if (state.selectedProject?.project_id !== projectId) {
    await selectProject(projectId);
  }
  if (!isCurrent()) return false;
  closeProjectRoomStream();
  state.selectedProviderChatKey = chatKey;
  const selectedSession = options.session || supervisorSessionForRoom(room);
  const selectedSessionAnchorRef = sessionAnchorRef(selectedSession);
  if (selectedSession && !selectedSessionAnchorRef) {
    throw new Error("This persistent session has no Session Anchor coordinate");
  }
  if (selectedSession) {
    const coordinateKey = nodeModeCoordinateKey(
      selectedSession.node,
      selectedSession.mode
    );
    state.selectedSupervisorAnchorKey = anchorSessionKey(selectedSession);
    state.selectedSupervisorAnchorKeysByMode = {
      ...state.selectedSupervisorAnchorKeysByMode,
      [coordinateKey]: anchorSessionKey(selectedSession),
    };
    state.selectedModeCoordinateKey = coordinateKey;
  }
  const setting =
    binding.mode === "CONDUCTOR"
      ? state.providerSettings?.universe_conductor
      : projectProviderSetting(projectId);
  state.conversationTarget = {
    kind: "PROVIDER_SESSION",
    chat_key: chatKey,
    session_anchor_ref:
      selectedSessionAnchorRef || String(binding.session_anchor_ref || "").trim(),
    vendor_session_id: String(
      binding.vendor_session_id ||
        binding.provider_session_id ||
        room.provider_session_id ||
        room.provider_session_ref ||
        chatKey
    ).trim(),
    projectId,
    node: binding.node || projectId,
    mode: binding.mode || "MASTER",
    provider: String(room.provider || "UNKNOWN").toUpperCase(),
    model_ref: setting?.model_ref || setting?.resolved_model || "",
    alias: binding.alias || room.display_name || projectId + " session",
  };
  clearProviderSessionUnread(chatKey);
  syncSelectedProviderSessionState(chatKey);
  await refreshProviderSession(chatKey);
  mergeProviderLiveDeltasIntoRoom(chatKey);
  openProviderSessionStream(chatKey);
  closeComposerActionMenu();
  renderComposerActions();
  renderComposerState();
  renderRoomMessages();
  elements.dispatchInstruction.focus();
  return true;
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
      } else {
        const active = payload.active_master_stream;
        state.projectStreamReplies = active?.in_reply_to
          ? {
              [active.in_reply_to]: {
                body: String(active.body || ""),
                state: String(active.state || "Thinking"),
                sequence: Number.isFinite(Number(active.sequence))
                  ? Number(active.sequence)
                  : -1,
              },
            }
          : {};
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
    const incomingSequence = Number(payload.sequence);
    const current = state.projectStreamReplies[key];
    if (
      current &&
      Number.isFinite(incomingSequence) &&
      incomingSequence >= 0 &&
      Number(current.sequence) >= incomingSequence
    ) {
      return;
    }
    if (payload.event === "COMPLETED") {
      delete state.projectStreamReplies[key];
    } else if (payload.event === "FAILED") {
      state.projectStreamReplies[key] = {
        body: current?.body || "",
        state: payload.detail || "Failed",
        sequence: incomingSequence,
      };
    } else {
      const next = current || {
        body: "",
        state: "Thinking",
        sequence: -1,
      };
      if (payload.event === "DELTA") {
        next.body += payload.delta || "";
        next.state = "Responding";
      }
      next.sequence = incomingSequence;
      state.projectStreamReplies[key] = next;
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
  if (state.view === "sessions") {
    buildSessionGraph();
    return;
  }
  if (state.view === "semantic") {
    buildSemanticProjectGraph();
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

function setGraphLegend(items) {
  if (!elements.graphLegend) return;
  elements.graphLegend.replaceChildren();
  for (const item of items) {
    const row = node("span");
    row.append(node("i", `node-key ${item.kind}`), document.createTextNode(item.label));
    elements.graphLegend.append(row);
  }
}

function buildSemanticProjectGraph() {
  const projection = state.semanticGraph || {};
  const sourceNodes = projection.nodes || [];
  const layerByType = {
    PROJECT: 0,
    GOAL: 1,
    MILESTONE: 2,
    TODO: 3,
    PREDICTION: 2,
    MEMORY: 2,
    BENCH: 2,
  };
  const kindByType = {
    PROJECT: "project",
    GOAL: "goal",
    MILESTONE: "milestone",
    TODO: "todo",
    PREDICTION: "predicted",
    MEMORY: "memory",
    BENCH: "bench",
  };
  setGraphLegend([
    { kind: "project", label: "Project" },
    { kind: "goal", label: "Goal" },
    { kind: "milestone", label: "Milestone" },
    { kind: "todo", label: "Todo" },
    { kind: "predicted", label: "Prediction" },
    { kind: "memory", label: "Memory" },
    { kind: "bench", label: "Bench candidate" },
  ]);
  const graphNodes = sourceNodes.map((item) => ({
    id: item.id,
    label: item.label,
    kind: kindByType[item.entity_type] || "related",
    depth: layerByType[item.entity_type] ?? 4,
    data: item,
    x: 0,
    y: 0,
  }));
  const layers = [...new Set(graphNodes.map((item) => item.depth))].sort((a, b) => a - b);
  for (const depth of layers) {
    const layer = graphNodes.filter((item) => item.depth === depth);
    layer.forEach((item, index) => {
      item.x = (index - (layer.length - 1) / 2) * 150;
      item.y = (depth - 1.5) * 130;
    });
  }
  const visible = new Set(graphNodes.map((item) => item.id));
  state.graph.nodes = graphNodes;
  state.graph.edges = (projection.edges || [])
    .filter((edge) => visible.has(edge.from) && visible.has(edge.to))
    .map((edge) => ({ from: edge.from, to: edge.to, kind: edge.edge_type, data: edge }));
  state.graph.scale = 1;
  state.graph.x = 0;
  state.graph.y = 0;
  elements.graphEmpty.classList.toggle("hidden", graphNodes.length > 0);
  if (elements.graphHint) {
    elements.graphHint.classList.toggle("hidden", !graphNodes.length);
    elements.graphHint.textContent = `Project Graph · ${state.selectedProject?.project_id || "Unknown"} · ${graphNodes.length} typed node(s) · projection only`;
  }
  drawGraph();
}

function sessionGraphNodeLabel(item) {
  const fallback = String(item?.label || item?.project_id || "Session").trim();
  if (String(item?.entity_type || "").toUpperCase() !== "SESSION_ANCHOR") {
    return fallback;
  }
  const provider = String(item?.provider || "").trim().toUpperCase();
  if (!provider) return fallback;
  const currentness = String(item?.currentness || "").trim().toUpperCase();
  return currentness === "CURRENT" ? `${provider} CURRENT` : provider;
}

function buildSessionGraph() {
  setGraphLegend([
    { kind: "mode", label: "Mode" },
    { kind: "mode-anchor", label: "Mode Anchor" },
    { kind: "session-anchor", label: "Session Anchor" },
    { kind: "task-frame", label: "Task Frame" },
  ]);
  const projection = state.sessionGraph || { nodes: [], edges: [] };
  const selectedProjectId = String(state.selectedProject?.project_id || "").trim();
  const projectedNodes = projection.nodes || [];
  const sourceNodes = selectedProjectId
    ? projectedNodes.filter((item) => item.project_id === selectedProjectId)
    : projectedNodes;
  const visibleNodeIds = new Set(sourceNodes.map((item) => item.id));
  const projectIds = [...new Set(sourceNodes.map((item) => item.project_id).filter(Boolean))].sort();
  const layerByType = {
    PROJECT: 0,
    MODE: 1,
    MODE_ANCHOR: 2,
    SESSION_ANCHOR: 3,
    TASK_FRAME: 4,
  };
  const graphNodes = sourceNodes.map((item) => ({
    id: item.id,
    label: sessionGraphNodeLabel(item),
    kind: String(item.entity_type || "UNKNOWN").toLowerCase(),
    depth: layerByType[item.entity_type] ?? 5,
    projectId: item.project_id || null,
    data: item,
    x: 0,
    y: 0,
  }));
  projectIds.forEach((projectId, projectIndex) => {
    const group = graphNodes.filter((item) => item.projectId === projectId);
    const maxLayerCount = Math.max(
      1,
      ...Object.values(layerByType).map(
        (depth) => group.filter((item) => item.depth === depth).length
      )
    );
    const groupWidth = Math.max(360, maxLayerCount * 86);
    const centerX =
      (projectIndex - (projectIds.length - 1) / 2) * (groupWidth + 90);
    for (const depth of Object.values(layerByType)) {
      const layer = group
        .filter((item) => item.depth === depth)
        .sort((left, right) => left.id.localeCompare(right.id));
      layer.forEach((item, index) => {
        item.x = centerX + (index - (layer.length - 1) / 2) * 82;
        item.y = -270 + depth * 85;
      });
    }
  });
  state.graph.nodes = graphNodes;
  state.graph.edges = (projection.edges || [])
    .filter((edge) => visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to))
    .map((edge) => ({
      from: edge.from,
      to: edge.to,
      kind: String(edge.edge_type || "related").toLowerCase().replaceAll("_", "-"),
      data: edge,
    }));
  state.graph.scale = 1;
  state.graph.x = 0;
  state.graph.y = 0;
  elements.graphEmpty.classList.toggle("hidden", graphNodes.length > 0);
  if (elements.graphEmpty && !graphNodes.length) {
    elements.graphEmpty.textContent = "No Mode or Session Anchor lineage is available";
  }
  if (elements.graphHint) {
    const sessions = graphNodes.filter((item) => item.kind === "session_anchor").length;
    const frames = graphNodes.filter((item) => item.kind === "task_frame").length;
    elements.graphHint.classList.toggle("hidden", !graphNodes.length);
    const scopeLabel = selectedProjectId || "All projects";
    elements.graphHint.textContent =
      `Session Graph · ${scopeLabel} · ${sessions} Session Anchor(s) · ${frames} Task Frame(s) · click a Session Anchor to bind its exact chat`;
  }
  drawGraph();
}

/** Fetch projections for every attached project so multiverse can stay fully expanded. */
async function loadAllProjectProjections() {
  const projects = visibleProjects().filter(
    (project) => !isProjectContainer(project)
  );
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
  setGraphLegend([
    { kind: "project", label: "Project" },
    { kind: "system", label: "Project Seed node" },
    { kind: "predicted", label: "Predicted" },
    { kind: "document", label: "Document" },
  ]);
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
  const registeredProjects = visibleProjects()
    .slice()
    .sort((a, b) => projectSortKey(a).localeCompare(projectSortKey(b)));
  // Containers retain registry/tree ownership but are not product nodes on the
  // map. Render their descendants directly under the nearest visible parent.
  const projects = registeredProjects.filter(
    (project) => !isProjectContainer(project)
  );

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

  const projectIds = new Set(projects.map((project) => project.project_id));
  const registeredById = new Map(
    registeredProjects.map((project) => [project.project_id, project])
  );
  function visibleParentId(project) {
    let parentId = String(project.metadata?.parent_project_id || "");
    const visited = new Set();
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId);
      if (projectIds.has(parentId)) return parentId;
      const parent = registeredById.get(parentId);
      if (!parent) return "";
      parentId = String(parent.metadata?.parent_project_id || "");
    }
    return "";
  }
  const childrenByParent = new Map();
  for (const project of projects) {
    const parentId = visibleParentId(project);
    if (!parentId) continue;
    const children = childrenByParent.get(parentId) || [];
    children.push(project);
    childrenByParent.set(parentId, children);
  }
  const topLevelProjects = projects.filter((project) => {
    return !visibleParentId(project);
  });

  // Room for always-expanded system fans around each product leaf.
  const maxSystems = projects
    .filter((project) => !isProjectContainer(project))
    .reduce((max, project) => {
    const count = (projectionForProject(project.project_id)?.nodes || []).length;
    return Math.max(max, count);
  }, 0);
  const projectRadius = Math.max(230, 180 + maxSystems * 8);

  function appendProject(project, { parentId, px, py, depth, systemRadius }) {
    const selected =
      state.selectedProject?.project_id === project.project_id;
    const id = `project:${project.project_id}`;
    graphNodes.push({
      id,
      label: projectDisplayName(project),
      kind: "project",
      depth,
      projectId: project.project_id,
      parentId,
      data: project,
      x: px,
      y: py,
      selectedProject: selected,
    });
    graphEdges.push({
      from: parentId,
      to: id,
      kind: "project-link",
    });

    // Always expand this product's functional nodes.
    const projection = projectionForProject(project.project_id);
    const systems = projection?.nodes || [];
    const count = Math.max(systems.length, 1);
    systems.forEach((item, systemIndex) => {
      const systemAngle = (Math.PI * 2 * systemIndex) / count - Math.PI / 2;
      const r = systemRadius + (systemIndex % 3) * 14;
      const systemId = `node:${project.project_id}:${item.node_id}`;
      // Prefer global node id when unique; fall back to project-scoped id to avoid collisions.
      const plainId = `node:${item.node_id}`;
      const idTaken = graphNodes.some((nodeItem) => nodeItem.id === plainId);
      const nodeId = idTaken ? systemId : plainId;
      graphNodes.push({
        id: nodeId,
        label: item.title,
        kind: "system",
        depth: depth + 1,
        projectId: project.project_id,
        parentId: id,
        data: {
          ...item,
          project_id: project.project_id,
          projection_origin: "PROJECT_SEED",
          projection_seed_id: projection?.seed_id || "",
          projection_source_ref: projection?.source?.ref || "",
          projection_source_commit: projection?.source?.commit || "",
        },
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
    return id;
  }

  topLevelProjects.forEach((project, index) => {
    const angle =
      (Math.PI * 2 * index) / Math.max(topLevelProjects.length, 1) - Math.PI / 2;
    const px = Math.cos(angle) * projectRadius;
    const py = Math.sin(angle) * projectRadius;
    const projectId = appendProject(project, {
      parentId: hub.id,
      px,
      py,
      depth: 1,
      systemRadius: 96,
    });
    const children = (childrenByParent.get(project.project_id) || [])
      .slice()
      .sort((a, b) => projectSortKey(a).localeCompare(projectSortKey(b)));
    children.forEach((child, childIndex) => {
      const childAngle =
        (Math.PI * 2 * childIndex) / Math.max(children.length, 1) - Math.PI / 2;
      const childRadius = Math.max(122, 98 + children.length * 12);
      appendProject(child, {
        parentId: projectId,
        px: px + Math.cos(childAngle) * childRadius,
        py: py + Math.sin(childAngle) * childRadius,
        depth: 2,
        systemRadius: 78,
      });
    });
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
    nodes.push({
      id: "runtime:uninitialized",
      label: "Project runtime not installed",
      kind: "setup",
      data: {
        kind: "PROJECT_RUNTIME_UNINITIALIZED",
        project_id: project.project_id,
        note: "Registered product without a current project projection.",
      },
      x: -80,
      y: 0,
    });
    edges.push({
      from: `project:${project.project_id}`,
      to: "runtime:uninitialized",
      kind: "setup-required",
    });
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

/** Colors express node meaning. Do not derive semantic UI state from an ID hash. */
function graphAccentColor(item) {
  const networkRole = String(item?.data?.network_role || "");
  if (item.kind === "predicted") {
    return {
      fill: "rgba(74, 48, 112, 0.92)",
      stroke: "#bd9cff",
      soft: "rgba(177, 139, 255, 0.28)",
    };
  }
  if (item.kind === "mode") {
    return { fill: "rgba(56, 45, 96, 0.94)", stroke: "#b9a3ff", soft: "rgba(185, 163, 255, 0.24)" };
  }
  if (item.kind === "mode_anchor") {
    return { fill: "rgba(24, 63, 105, 0.94)", stroke: "#73b9ff", soft: "rgba(115, 185, 255, 0.24)" };
  }
  if (item.kind === "session_anchor") {
    return { fill: "rgba(18, 82, 72, 0.94)", stroke: "#67ddc3", soft: "rgba(103, 221, 195, 0.24)" };
  }
  if (item.kind === "task_frame") {
    return { fill: "rgba(91, 55, 24, 0.94)", stroke: "#f1ae66", soft: "rgba(241, 174, 102, 0.24)" };
  }
  if (item.kind === "setup") {
    return {
      fill: "rgba(82, 61, 24, 0.92)",
      stroke: "#f0c46d",
      soft: "rgba(240, 196, 109, 0.24)",
    };
  }
  if (networkRole.endsWith("_SOURCE")) {
    return {
      fill: "rgba(25, 60, 104, 0.92)",
      stroke: "#78b8ff",
      soft: "rgba(120, 184, 255, 0.24)",
    };
  }
  return {
    fill: "rgba(20, 73, 79, 0.92)",
    stroke: "#61d2ca",
    soft: "rgba(97, 210, 202, 0.24)",
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
  if (["mode", "mode_anchor", "session_anchor", "task_frame"].includes(item.kind)) {
    return { shape: "system", radius: item.kind === "session_anchor" ? 17 : 15, hitR: 22 };
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
  const accent = graphAccentColor(item);
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
  if (item.kind === "mode") return "Mode";
  if (item.kind === "mode_anchor") return "Mode Anchor";
  if (item.kind === "session_anchor") return "Session Anchor";
  if (item.kind === "task_frame") return "Task Frame";
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
  if (state.view === "sessions") {
    state.selectedNode = selected;
    drawGraph();
    renderDetails();
    showInspectorTab("details");
    if (selected.kind === "session_anchor") {
      const anchorRef = String(selected.data?.ref || "");
      const session = (state.supervisorSessions || []).find(
        (item) => sessionAnchorRef(item) === anchorRef
      );
      if (!session) {
        toast("This Session Anchor has no observable provider session", true);
        return;
      }
      const coordinate = nodeModeCoordinates()
        .flatMap((group) => group.modes)
        .find((item) => item.sessions.some(
          (candidate) => anchorSessionKey(candidate) === anchorSessionKey(session)
        ));
      if (!coordinate) {
        toast("This Session Anchor has no visible Mode coordinate", true);
        return;
      }
      selectNodeModeSession(coordinate, session).catch((error) =>
        toast(error.message, true)
      );
    }
    return;
  }
  if (selected.kind === "universe") {
    // Depth 0 focus — tree stays fully expanded; only dim shifts.
    state.focusedNodeId = null;
    state.inspectorDismissed = true;
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

async function refreshSelectedWorkLoop() {
  const projectId = state.selectedProject?.project_id;
  if (!projectId) return;
  state.workLoop = await api(
    `/v1/projects/${encodeURIComponent(projectId)}/work-loop`
  );
  renderDetails();
}

async function generateWorkLoopPrediction() {
  const projectId = state.selectedProject?.project_id;
  if (!projectId) return;
  const result = await api(
    `/v1/projects/${encodeURIComponent(projectId)}/work-loop/predictions`,
    { method: "POST", body: {} }
  );
  await refreshSelectedWorkLoop();
  toast(
    result.status === "WORK_LOOP_PREDICTION_RECORDED"
      ? "Prediction proposal recorded"
      : "Prediction proposal already current"
  );
}

async function reviewWorkLoopPrediction(proposalId, decision) {
  const projectId = state.selectedProject?.project_id;
  if (!projectId) return;
  await api(
    `/v1/projects/${encodeURIComponent(projectId)}/work-loop/predictions/review`,
    { method: "POST", body: { proposal_id: proposalId, decision } }
  );
  await refreshSelectedWorkLoop();
  toast(`Prediction ${decision === "KEEP" ? "kept" : "rejected"}`);
}

async function reviewWorkLoopCandidate(candidateId, decision) {
  const projectId = state.selectedProject?.project_id;
  if (!projectId) return;
  await api(
    `/v1/projects/${encodeURIComponent(projectId)}/work-loop/review-candidates/review`,
    { method: "POST", body: { candidate_id: candidateId, decision } }
  );
  await refreshSelectedWorkLoop();
  toast(`Result candidate ${decision === "KEEP" ? "kept" : "rejected"}`);
}

async function recoverWorkLoopTodos() {
  const projectId = state.selectedProject?.project_id;
  if (!projectId) return;
  const result = await api(
    `/v1/projects/${encodeURIComponent(projectId)}/work-loop/recover`,
    { method: "POST", body: {} }
  );
  await refresh();
  if (state.selectedProject?.project_id === projectId) {
    await selectProject(projectId, { revealInspector: true });
  }
  toast(`Recovered ${(result.recovered || []).length} interrupted Todo`);
}

function renderWorkLoopDetails() {
  const workLoop = state.workLoop || {};
  const predictions = workLoop.predictions || [];
  const group = node("div", "detail-group");
  const heading = node("div", "detail-heading-row");
  heading.append(node("h3", "", `Work Loop (${predictions.length})`));
  const actions = node("div", "detail-heading-actions");
  const generate = node("button", "secondary-button compact-action", "Predict");
  generate.type = "button";
  generate.title = "Generate a review-only Goal / Plan / Milestone / risk proposal";
  generate.addEventListener("click", () =>
    generateWorkLoopPrediction().catch((error) => toast(error.message, true))
  );
  const recover = node("button", "secondary-button compact-action", "Recover");
  recover.type = "button";
  recover.title = "Return interrupted Todo work to READY when recovery evidence matches";
  recover.addEventListener("click", () =>
    recoverWorkLoopTodos().catch((error) => toast(error.message, true))
  );
  actions.append(generate, recover);
  heading.append(actions);
  group.append(heading);
  group.append(
    node(
      "p",
      "empty-copy",
      "Predictions are evidence-backed proposals only. They never auto-adopt Goals or Todos."
    )
  );
  if (!predictions.length) {
    group.append(node("p", "empty-copy", "No prediction proposal yet."));
  }
  for (const prediction of predictions.slice(0, 5)) {
    const card = node("div", "context-card");
    card.append(
      node(
        "strong",
        "",
        `${prediction.review_state || "PROPOSAL_ONLY"} · ${prediction.proposal_id}`
      )
    );
    const list = node("ul", "context-list");
    for (const suggestion of (prediction.suggestions || []).slice(0, 8)) {
      const provenance = (suggestion.provenance || [])
        .map((item) => `${item.kind}:${item.ref}`)
        .join(", ");
      list.append(
        node(
          "li",
          "",
          `${suggestion.kind} · ${Math.round(Number(suggestion.confidence || 0) * 100)}% · ${suggestion.title}${provenance ? ` · ${provenance}` : ""}`
        )
      );
    }
    for (const rejected of (prediction.rejected || []).slice(0, 5)) {
      list.append(
        node(
          "li",
          "",
          `REJECTED · ${rejected.reason} · ${rejected.title || "No supported candidate"}`
        )
      );
    }
    card.append(list);
    if ((prediction.review_state || "PROPOSAL_ONLY") === "PROPOSAL_ONLY") {
      const reviewActions = node("div", "detail-heading-actions");
      for (const decision of ["KEEP", "REJECT"]) {
        const button = node(
          "button",
          "secondary-button compact-action",
          decision === "KEEP" ? "Keep" : "Reject"
        );
        button.type = "button";
        button.addEventListener("click", () =>
          reviewWorkLoopPrediction(prediction.proposal_id, decision).catch((error) =>
            toast(error.message, true)
          )
        );
        reviewActions.append(button);
      }
      card.append(reviewActions);
    }
    group.append(card);
  }
  const reviewCandidates = workLoop.review_candidates || [];
  if (reviewCandidates.length) {
    group.append(node("h4", "", `Result Review (${reviewCandidates.length})`));
  }
  for (const candidate of reviewCandidates.slice(0, 10)) {
    const card = node("div", "context-card");
    card.append(
      node(
        "strong",
        "",
        `${candidate.sink_kind} · ${candidate.review_state || "PENDING_REVIEW"}`
      ),
      node(
        "p",
        "empty-copy",
        `${candidate.outcome || "UNKNOWN"} · Todo ${candidate.todo_id || "unknown"}`
      )
    );
    if ((candidate.review_state || "PENDING_REVIEW") === "PENDING_REVIEW") {
      const reviewActions = node("div", "detail-heading-actions");
      for (const decision of ["KEEP", "REJECT"]) {
        const button = node(
          "button",
          "secondary-button compact-action",
          decision === "KEEP" ? "Keep" : "Reject"
        );
        button.type = "button";
        button.addEventListener("click", () =>
          reviewWorkLoopCandidate(candidate.candidate_id, decision).catch((error) =>
            toast(error.message, true)
          )
        );
        reviewActions.append(button);
      }
      card.append(reviewActions);
    }
    group.append(card);
  }
  const memorySchedules = workLoop.memory_schedules || [];
  if (memorySchedules.length) {
    group.append(node("h4", "", `Memory Scheduler (${memorySchedules.length})`));
  }
  for (const schedule of memorySchedules.slice(0, 8)) {
    group.append(
      node(
        "p",
        "empty-copy",
        `${schedule.stage || "MEMORY"} · ${schedule.state || "UNKNOWN"} · last ${schedule.last_outcome || "NONE"} · next ${schedule.next_due_at || "not scheduled"} · attempts ${Number(schedule.attempt_count || 0)}/${Number(schedule.max_attempts || 0)}`
      )
    );
  }
  const fanoutCount = (workLoop.result_fanouts || []).length;
  const pendingReviewCount = reviewCandidates.filter(
    (candidate) => (candidate.review_state || "PENDING_REVIEW") === "PENDING_REVIEW"
  ).length;
  const documentCount = Number(workLoop.document_automation?.proposal_count || 0);
  group.append(
    node(
      "p",
      "empty-copy",
      `Result fan-out ${fanoutCount} · Pending reviews ${pendingReviewCount} · Memory schedules ${memorySchedules.length} · Document proposals ${documentCount} · auto-apply off`
    )
  );
  return group;
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
    selected?.kind === "predicted"
      ? "USER_SELECTION_REQUIRED"
      : selected?.kind === "setup"
        ? "PROJECT_RUNTIME_UNINITIALIZED"
        : "CURRENT"
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
  if (data.projection_origin === "PROJECT_SEED") {
    addDetail(grid, "Origin", "Project Seed");
    if (data.projection_seed_id) addDetail(grid, "Seed", data.projection_seed_id);
    if (data.projection_source_ref) {
      addDetail(grid, "Seed source", data.projection_source_ref);
    }
    if (data.projection_source_commit) {
      addDetail(grid, "Source commit", data.projection_source_commit);
    }
  }
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
    elements.details.append(renderWorkLoopDetails());
  }

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
  if (state.conversationTarget.kind === "SESSION_DELEGATION") {
    const instruction = String(form.get("instruction") || "").trim();
    const draft = state.sessionDelegationDraft || state.conversationTarget;
    const originAnchorRef = String(draft.origin_anchor_ref || "").trim();
    const targetAnchorRef = String(draft.target_anchor_ref || "").trim();
    if (!instruction || !originAnchorRef || !targetAnchorRef) {
      toast("Delegation needs an instruction plus exact origin and target anchor refs", true);
      return;
    }
    elements.dispatchSubmit.disabled = true;
    try {
      const projectId = String(draft.project_id || draft.projectId || "").trim();
      if (!projectId) {
        throw new Error("Delegation needs an exact project id");
      }
      // Internal delegation uses exact Session Anchors, never a Project/Meeting Room queue.
      const result = await api("/v1/conductor/delegations", {
        method: "POST",
        controlToken: true,
        body: {
          project_id: projectId,
          summary: instruction,
          idempotency_key: crypto.randomUUID(),
          origin_session_anchor_ref: originAnchorRef,
          target_session_anchor_ref: targetAnchorRef,
          origin_session_chat_key: String(draft.origin_session_chat_key || "").trim(),
          provider: "AUTO",
        },
      });
      const delegation = normalizeSessionDelegation(result.delegation, {
        project_id: projectId,
        origin_anchor_ref: originAnchorRef,
        target_anchor_ref: targetAnchorRef,
      });
      rememberSessionDelegation(delegation);
      state.sessionDelegationDraft = { ...draft, state: delegation.state || "QUEUED" };
      elements.dispatchForm.reset();
      renderComposerState();
      renderRoomMessages();
      if (isCompletedSessionDelegation(delegation)) {
        await rejoinDelegationOrigin(delegation);
      } else {
        toast("Delegation queued with explicit origin and target anchors");
        watchSessionDelegation(delegation.delegation_id, {
          project_id: projectId,
          origin_anchor_ref: originAnchorRef,
          target_anchor_ref: targetAnchorRef,
        });
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      elements.dispatchSubmit.disabled = false;
    }
    return;
  }
  if (state.conversationTarget.kind === "PROVIDER_SESSION") {
    const instruction = String(form.get("instruction") || "").trim();
    if (!instruction) return;
    elements.dispatchSubmit.disabled = true;
    try {
      const result = await api(
        `/v1/provider-sessions/${encodeURIComponent(
          state.conversationTarget.chat_key
        )}/messages`,
        {
          method: "POST",
          body: {
            body: instruction,
            idempotency_key: crypto.randomUUID(),
          },
        }
      );
      elements.dispatchForm.reset();
      const cache = providerSessionCache(state.conversationTarget.chat_key);
      cache.messages = dedupeProviderSessionMessages([
        ...cache.messages,
        result.message,
        result.reply,
      ]);
      markProviderSessionRead(state.conversationTarget.chat_key);
      syncSelectedProviderSessionState(state.conversationTarget.chat_key);
      expandConversationLayer();
      renderComposerState();
      renderRoomMessages();
      toast("Sent directly to the selected Provider Session");
    } catch (error) {
      toast(error.message, true);
    } finally {
      elements.dispatchSubmit.disabled = false;
    }
    return;
  }
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
    if (result.release_proposal) {
      state.releaseProposals = [
        result.release_proposal,
        ...state.releaseProposals.filter(
          (item) => item.proposal_id !== result.release_proposal.proposal_id
        ),
      ];
      showReleaseProposal(result.release_proposal);
      renderComposerState();
      renderRoomMessages();
      toast("Project update plan recorded; approval is required to apply it");
      showInspectorTab("activity");
      return;
    }
    if (result.status === "PROJECT_RELEASE_SELECTION_REQUIRED") {
      renderReleaseCatalog();
      renderComposerState();
      renderRoomMessages();
      toast("Choose one imported Release DB before applying the project runtime", true);
      showInspectorTab("activity");
      return;
    }
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
    const deliveryState = result.message.delivery_state;
    toast(
      deliveryState === "ACCEPTED_BY_MASTER"
        ? "Accepted by the registered Project Master"
        : deliveryState === "QUEUED_FOR_MASTER"
          ? "Queued for the registered Project Master"
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

function planStateLabel(value) {
  return String(value || "PLANNED")
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (letter) => letter.toUpperCase());
}

function goalProgress(goal) {
  const todos = [
    ...(goal.todos || []),
    ...(goal.milestones || []).flatMap((milestone) => milestone.todos || []),
  ];
  if (!todos.length) return 0;
  return Math.round((todos.filter((todo) => todo.state === "DONE").length / todos.length) * 100);
}

function goalsForSelectedContext() {
  const nodeRef = selectedNodeRef();
  if (nodeRef && state.selectedProject) {
    return state.goals.filter(
      (goal) => goal.scope_kind === "NODE" && goal.node_ref === nodeRef
    );
  }
  return state.goals.filter((goal) => (goal.scope_kind || "PROJECT") === "PROJECT");
}


async function refreshGoalPlan() {
  if (!state.selectedProject) {
    state.goals = [];
    state.universeGoals = [];
    state.unassignedTodos = [];
    renderGoalPlan();
    return;
  }
  const result = await api(
    `/v1/projects/${encodeURIComponent(state.selectedProject.project_id)}/goals`
  );
  const universeResult = await api("/v1/universe-goals").catch(() => ({ goals: [] }));
  state.goals = result.goals || [];
  state.universeGoals = universeResult.goals || [];
  state.unassignedTodos = (result.unassigned_todos || []).filter(
    (todo) => todo.state !== "DONE"
  );
  const contextualGoals = goalsForSelectedContext();
  if (!contextualGoals.some((goal) => goal.goal_id === state.selectedGoalId)) {
    state.selectedGoalId = contextualGoals[0]?.goal_id || null;
  }
  renderGoalPlan();
}

function planTodoRow(todo) {
  const row = node("article", "plan-todo-row");
  const priority = todoSelect(
    [["P0", "P0"], ["P1", "P1"], ["P2", "P2"], ["P3", "P3"]],
    todo.priority,
    "plan-todo-priority"
  );
  priority.title = "Change priority";
  priority.addEventListener("change", async () => {
    await updateTodo(todo, { ...todo, priority: priority.value });
    await refreshGoalPlan();
  });
  const recommendation = todo.priority_recommendation || null;
  const suggested = recommendation && recommendation.priority !== todo.priority
    ? node("button", "secondary-button compact", `Use ${recommendation.priority}`)
    : null;
  if (suggested) {
    suggested.type = "button";
    suggested.title = `${recommendation.method}: ${(recommendation.reasons || []).join(", ")}`;
    suggested.addEventListener("click", async () => {
      await updateTodo(todo, { ...todo, priority: recommendation.priority });
      await refreshGoalPlan();
    });
  }
  row.append(
    node("span", `todo-priority ${String(todo.priority).toLowerCase()}`, todo.priority),
    node("span", "plan-todo-title", todo.title),
    node("span", `plan-state ${String(todo.state).toLowerCase()}`, planStateLabel(todo.state)),
    priority
  );
  if (suggested) row.append(suggested);
  return row;
}

function openPlanTodos(todos) {
  return (todos || []).filter((todo) => todo.state !== "DONE");
}

function renderUniverseGoalCard(goal) {
  const card = node("article", "goal-card universe-goal-card");
  const header = node("header", "goal-card-header");
  header.append(
    node("span", "goal-index", "U"),
    node("h2", "", goal.title),
    node("span", `plan-state ${String(goal.state).toLowerCase()}`, planStateLabel(goal.state))
  );
  card.append(header, node("p", "goal-card-description", goal.description || "No global outcome yet."));
  const linked = node("div", "milestone-list");
  for (const todo of openPlanTodos(goal.todos)) linked.append(planTodoRow(todo));
  for (const projectGoal of goal.project_goals || []) {
    const item = node("div", "milestone-block");
    item.append(
      node("strong", "", `${projectGoal.project_id} — ${projectGoal.title}`),
      node("span", `plan-state ${String(projectGoal.state).toLowerCase()}`, planStateLabel(projectGoal.state))
    );
    linked.append(item);
  }
  const globalCandidates = (state.todos || []).filter(
    (todo) => todo.scope_kind === "UNIVERSE" && !todo.universe_goal_id && todo.state !== "DONE"
  );
  if (globalCandidates.length) {
    const assign = node("select", "goal-assign-select");
    assign.append(new Option("Attach global Todo...", ""));
    for (const todo of globalCandidates) assign.append(new Option(todo.title, todo.todo_id));
    assign.addEventListener("change", async () => {
      const todo = globalCandidates.find((item) => item.todo_id === assign.value);
      if (!todo) return;
      await updateTodo(todo, { ...todo, universe_goal_id: goal.universe_goal_id });
      await refreshGoalPlan();
    });
    linked.append(assign);
  }
  if (!linked.childElementCount) linked.append(node("small", "empty-copy", "Connect global or project work to this goal."));
  card.append(linked);
  return card;
}

function renderGoalInspector(goal, index) {
  if (!goal || !elements.details) return;
  const progress = goalProgress(goal);
  const todos = [
    ...(goal.todos || []),
    ...(goal.milestones || []).flatMap((item) => item.todos || []),
  ];
  const blocked = todos.filter((todo) => todo.state === "BLOCKED").length;
  elements.inspectorTitle.textContent = `${index + 1}. ${goal.title}`;
  elements.inspectorSubtitle.textContent = `${planStateLabel(goal.state)} / Goal details`;
  elements.details.replaceChildren();
  const actions = node("div", "goal-inspector-actions");
  const delegate = node("button", "primary-button", "Delegate goal");
  delegate.type = "button";
  delegate.addEventListener("click", () => {
    elements.dispatchInstruction.value = `Delegate goal "${goal.title}" to the Project Master.`;
    elements.dispatchInstruction.focus();
  });
  const edit = node("button", "secondary-button", "Edit plan");
  edit.type = "button";
  edit.addEventListener("click", () => openGoalEditor(goal));
  actions.append(delegate, edit);
  const description = node("section", "goal-detail-section");
  description.append(node("h3", "", "Description"), node("p", "", goal.description || "No description yet."));
  const metrics = node("section", "goal-detail-section goal-detail-metrics");
  for (const [label, value, tone] of [
    ["Design readiness", goal.state === "DESIGNING" ? 35 : goal.state === "READY" ? 80 : 100, "readiness"],
    ["Progress", progress, "progress"],
  ]) {
    const item = node("div", `detail-meter ${tone}`);
    const heading = node("div", "detail-meter-heading");
    heading.append(node("span", "", label), node("strong", "", `${value}%`));
    const track = node("div", "detail-meter-track");
    const fill = node("i", "");
    fill.style.width = `${value}%`;
    track.append(fill);
    item.append(heading, track);
    metrics.append(item);
  }
  const facts = node("dl", "goal-detail-facts");
  for (const [label, value] of [
    ["Owner / Agent", goal.owner || "Project Master"],
    ["Milestones", String((goal.milestones || []).length)],
    ["Needs attention", String(blocked)],
    ["Started", goal.created_at || "Unknown"],
    ["Last updated", goal.updated_at || "Unknown"],
  ]) addDetail(facts, label, value);
  const activity = node("section", "goal-detail-section");
  activity.append(node("h3", "", "Activity"));
  const timeline = node("div", "goal-activity-list");
  const recent = todos.slice().sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 5);
  if (!recent.length) timeline.append(node("p", "empty-copy", "No activity yet."));
  for (const todo of recent) {
    const row = node("div", "goal-activity-item");
    row.append(node("i", ""), node("span", "", `${todo.title} / ${planStateLabel(todo.state)}`), node("time", "", todo.updated_at || ""));
    timeline.append(row);
  }
  activity.append(timeline);
  elements.details.append(actions, description, metrics, facts, activity);
}

function openGoalEditor(goal) {
  if (!goal) return;
  elements.goalForm.dataset.goalId = goal.goal_id;
  elements.goalForm.elements.title.value = goal.title || "";
  elements.goalForm.elements.description.value = goal.description || "";
  elements.goalForm.elements.owner.value = goal.owner || "Project Master";
  elements.goalForm.elements.state.value = goal.state || "DESIGNING";
  prepareGoalHierarchyFields({
    scopeKind: goal.scope_kind || "PROJECT",
    universeGoalId: goal.universe_goal_id || "",
  });
  elements.goalDialog.querySelector("h2").textContent = "Edit goal";
  elements.goalDialog.querySelector('[type="submit"]').textContent = "Save goal";
  elements.goalDialog.showModal();
}

function renderGoalPlan() {
  if (!elements.goalPlanList) return;
  if (!document.body.classList.contains("graph-mode")) {
    syncPrimaryNavSelection("work");
  }
  const project = state.selectedProject;
  const projectGoals = goalsForSelectedContext();
  elements.goalPlanTitle.textContent = "Goal Plan";
  if (elements.goalPlanBreadcrumb) {
    elements.goalPlanBreadcrumb.textContent = project
      ? `${projectDisplayName(project)} > Goal Plan`
      : "Project > Goal Plan";
  }
  elements.goalPlanSubtitle.textContent = project
    ? "Universe Goal -> Project Goal -> Milestone / Phase -> Todo"
    : "Select a project to shape its delivery plan.";
  elements.addGoalButton.disabled = !project;
  const todos = projectGoals.flatMap((goal) => [
    ...(goal.todos || []),
    ...(goal.milestones || []).flatMap((milestone) => milestone.todos || []),
  ]);
  const done = todos.filter((todo) => todo.state === "DONE").length;
  const progress = todos.length ? Math.round((done / todos.length) * 100) : 0;
  const readyGoals = projectGoals.filter((goal) => ["READY", "ACTIVE", "DONE"].includes(goal.state)).length;
  const readiness = projectGoals.length ? Math.round((readyGoals / projectGoals.length) * 100) : 0;
  const milestoneCount = projectGoals.reduce((count, goal) => count + (goal.milestones || []).length, 0);
  const owner = projectGoals[0]?.owner || "Project Master";
  elements.goalPlanSummary.replaceChildren();
  const needsYou = state.unassignedTodos.length + todos.filter((todo) => todo.state === "BLOCKED").length;
  for (const [label, value] of [
    ["Design readiness", `${readiness}%`],
    ["Progress", `${progress}%`],
    ["Needs you", needsYou],
    ["Milestones", milestoneCount],
  ]) {
    const metric = node("article", "goal-summary-item");
    metric.append(node("span", "", label), node("strong", "", String(value)));
    elements.goalPlanSummary.append(metric);
  }
  elements.goalPlanList.replaceChildren();
  if (!project) {
    elements.goalPlanList.append(node("div", "goal-plan-empty", "Choose a project to begin planning."));
  } else {
    for (const universeGoal of state.universeGoals || []) {
      elements.goalPlanList.append(renderUniverseGoalCard(universeGoal));
    }
  }
  if (project && !projectGoals.length) {
    const empty = node("div", "goal-plan-empty");
    empty.append(
      node("strong", "", "No goals yet"),
      node("p", "", "Start with the outcome you want, then break it into milestones and Todo."),
    );
    const add = node("button", "primary-button", "Create first goal");
    add.type = "button";
    add.addEventListener("click", () => elements.goalDialog.showModal());
    empty.append(add);
    elements.goalPlanList.append(empty);
  }
  projectGoals.forEach((goal, goalIndex) => {
    const card = node("article", "goal-card");
    card.tabIndex = 0;
    card.classList.toggle("selected", state.selectedGoalId === goal.goal_id);
    card.addEventListener("click", () => {
      state.selectedGoalId = goal.goal_id;
      renderGoalPlan();
    });
    const header = node("header", "goal-card-header");
    const titleWrap = node("div", "goal-card-title");
    titleWrap.append(
      node("span", "goal-index", String(goalIndex + 1).padStart(2, "0")),
      node("h2", "", goal.title),
      node("p", "", goal.description || "No outcome description yet.")
    );
    const progress = goalProgress(goal);
    const actions = node("div", "goal-card-actions");
    const delegate = node("button", "secondary-button", "Delegate Goal");
    delegate.type = "button";
    delegate.addEventListener("click", () => {
      elements.dispatchInstruction.value = `Delegate goal \"${goal.title}\" to the Project Master. Use this goal plan as the planning reference and do not start execution until the plan is confirmed.`;
      elements.dispatchInstruction.focus();
      toast("Delegation draft is ready. Review it before sending.");
    });
    const addMilestone = node("button", "icon-button compact", "+");
    addMilestone.type = "button";
    addMilestone.title = "Add milestone";
    addMilestone.addEventListener("click", () => {
      elements.milestoneForm.elements.goal_id.value = goal.goal_id;
      elements.milestoneDialog.showModal();
    });
    const collapse = node("button", "icon-button compact goal-collapse", state.expandedGoals[goal.goal_id] === false ? "+" : "−");
    collapse.type = "button";
    collapse.title = "Expand or collapse goal";
    collapse.addEventListener("click", (event) => {
      event.stopPropagation();
      state.expandedGoals[goal.goal_id] = state.expandedGoals[goal.goal_id] === false;
      renderGoalPlan();
    });
    actions.append(node("span", `plan-state ${goal.state.toLowerCase()}`, planStateLabel(goal.state)), delegate, addMilestone, collapse);
    header.append(titleWrap, actions);
    const progressBar = node("div", "goal-progress");
    const fill = node("span", "goal-progress-fill");
    fill.style.width = `${progress}%`;
    progressBar.append(fill);
    const progressLabel = node("small", "goal-progress-label", `${progress}% complete`);
    const milestones = node("div", "milestone-list");
    for (const milestone of goal.milestones || []) {
      const item = node("section", "milestone-block");
      const itemHeader = node("header", "milestone-header");
      itemHeader.append(
        node("span", "milestone-marker", ""),
        node("strong", "", milestone.title),
        node("span", `plan-state ${milestone.state.toLowerCase()}`, planStateLabel(milestone.state))
      );
      item.append(itemHeader);
      if (milestone.description) item.append(node("p", "milestone-description", milestone.description));
      const list = node("div", "milestone-todos");
      for (const todo of openPlanTodos(milestone.todos)) list.append(planTodoRow(todo));
      if (!list.childElementCount) list.append(node("small", "empty-copy", "No work assigned to this milestone."));
      item.append(list);
      milestones.append(item);
    }
    for (const todo of openPlanTodos(goal.todos)) milestones.append(planTodoRow(todo));
    if (!milestones.childElementCount) milestones.append(node("div", "milestone-empty", "Add a milestone to shape the delivery path."));
    milestones.hidden = state.expandedGoals[goal.goal_id] === false;
    card.append(header, progressBar, progressLabel, milestones);
    elements.goalPlanList.append(card);
    if (state.selectedGoalId === goal.goal_id) renderGoalInspector(goal, goalIndex);
  });

  elements.unassignedWorkCount.textContent = String(state.unassignedTodos.length);
  elements.unassignedWorkList.replaceChildren();
  if (!state.unassignedTodos.length) {
    elements.unassignedWorkList.append(node("div", "goal-plan-empty compact", "Everything is connected to a goal."));
  }
  for (const todo of state.unassignedTodos) {
    const row = planTodoRow(todo);
    const select = node("select", "goal-assign-select");
    select.append(new Option("Add to goal...", ""));
    for (const goal of projectGoals) select.append(new Option(goal.title, goal.goal_id));
    select.disabled = !projectGoals.length;
    select.addEventListener("change", async () => {
      const goalId = select.value;
      if (!goalId) return;
      await updateTodo(todo, { ...todo, goal_id: goalId, milestone_id: null });
      await refreshGoalPlan();
    });
    row.append(select);
    elements.unassignedWorkList.append(row);
  }
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
    universe_goal_id: changes.universe_goal_id ?? todo.universe_goal_id ?? null,
    title: changes.title.trim(),
    detail: changes.detail,
    priority: changes.priority,
    state: changes.state,
    source_kind: todo.source_kind,
    sort_order: todo.sort_order,
    goal_id: changes.goal_id ?? todo.goal_id ?? null,
    milestone_id: changes.milestone_id ?? todo.milestone_id ?? null,
    revision: todo.revision,
  };
  if (body.project_id === null) delete body.project_id;
  if (body.node_ref === null) delete body.node_ref;
  if (body.universe_goal_id === null) delete body.universe_goal_id;
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
    project_root: String(form.get("project_root") || "").trim(),
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
  } else if (project) {
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
    const request = {
      project_id: form.get("project_id"),
      project_root: form.get("project_root"),
      release_id: state.releases[0]?.release_id || null,
    };
    if (!state.projectConnectionPlan) {
      const planned = await api("/v1/project-connections/prepare", {
        method: "POST",
        body: request,
      });
      state.projectConnectionPlan = planned;
      elements.projectSubmit.textContent = planned.action_label;
      elements.projectFormError.textContent = planned.detail;
      return;
    }
    await api("/v1/project-connections/apply", {
      method: "POST",
      body: {
        ...request,
        plan_digest: state.projectConnectionPlan.plan_digest,
        command: "CONNECT_PROJECT",
      },
    });
    state.projectConnectionPlan = null;
    elements.projectDialog.close();
    elements.projectForm.reset();
    elements.projectSubmit.textContent = "Inspect project";
    toast("Project connected");
    await refresh();
    await selectProject(String(form.get("project_id")));
  } catch (error) {
    elements.projectFormError.textContent = error.message;
  }
}

async function selectHostDirectory(input, button, errorOutput) {
  button.disabled = true;
  errorOutput.textContent = "";
  try {
    const result = await api("/v1/host/select-directory", {
      method: "POST",
      body: {},
    });
    if (result.status === "DIRECTORY_SELECTED" && result.directory) {
      input.value = result.directory;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
  } catch (error) {
    errorOutput.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function selectProjectRoot() {
  return selectHostDirectory(
    elements.projectForm.elements.namedItem("project_root"),
    elements.projectRootBrowse,
    elements.projectFormError
  );
}

function selectFreshProjectRoot() {
  return selectHostDirectory(
    elements.freshProjectForm.elements.namedItem("project_root"),
    elements.freshProjectRootBrowse,
    elements.freshProjectError
  );
}

async function selectHostFile(input, button, kind, errorOutput) {
  button.disabled = true;
  errorOutput.textContent = "";
  try {
    const result = await api("/v1/host/select-file", {
      method: "POST",
      body: { kind },
    });
    if (result.status === "FILE_SELECTED" && result.file) {
      input.value = result.file;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
  } catch (error) {
    errorOutput.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function selectReleaseDatabase() {
  return selectHostFile(
    elements.releaseForm.elements.namedItem("database_path"),
    elements.releaseDatabaseBrowse,
    "RELEASE_DATABASE",
    elements.releaseFormError
  );
}

function selectReleaseManifest() {
  return selectHostFile(
    elements.releaseForm.elements.namedItem("manifest_path"),
    elements.releaseManifestBrowse,
    "RELEASE_MANIFEST",
    elements.releaseFormError
  );
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

  const gapGroup = node("div", "detail-group");
  const gapSummary = state.skillGapSummary || { groups: [], observation_count: 0 };
  gapGroup.append(
    node("h3", "", `Fallback gaps (${gapSummary.observation_count || 0})`)
  );
  if (!(gapSummary.groups || []).length) {
    gapGroup.append(
      node(
        "p",
        "empty-copy",
        "No redacted fallback gaps yet. Dedicated Skill misses remain separate from installed Skill observations."
      )
    );
  } else {
    const list = node("ul", "context-list bench-list");
    for (const row of gapSummary.groups.slice(0, 8)) {
      list.append(
        node(
          "li",
          "",
          `${row.capability || "CAPABILITY"} / ${row.effect_class || "NONE"} / n=${row.observation_count || 0} / validated=${row.validated_success_count || 0} / failed=${row.failed_count || 0} / contexts=${row.distinct_context_count || 0}`
        )
      );
    }
    gapGroup.append(list);
  }
  const candidates = state.skillCandidates || [];
  gapGroup.append(node("h3", "", `Skill candidates (${candidates.length})`));
  if (!candidates.length) {
    gapGroup.append(
      node("p", "empty-copy", "No Candidate has been derived from threshold-bound fallback evidence.")
    );
  } else {
    const list = node("ul", "context-list bench-list");
    for (const candidate of candidates.slice(0, 8)) {
      list.append(
        node(
          "li",
          "",
          `${candidate.candidate_state || "OBSERVED"} / ${candidate.capability || "CAPABILITY"} / support=${candidate.evidence?.observation_count || 0} / installed=${candidate.installation_state || "NOT_INSTALLED"}`
        )
      );
    }
    gapGroup.append(list);
  }
  elements.benchPanel.append(gapGroup);

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

function appendSelectOption(select, value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  select.append(option);
}

function memoryBatchField(label, control) {
  const wrapper = node("label", "memory-batch-field");
  wrapper.append(node("span", "field-label", label), control);
  return wrapper;
}

function renderMemoryBatchStages() {
  const group = node("div", "detail-group memory-batch-config");
  group.append(
    node("h3", "", "Memory batch stages"),
    node(
      "p",
      "context-copy",
      "Four bounded stages. Provider/model selection is checked against the current catalog before saving or running."
    )
  );
  const configs = Array.isArray(state.memoryBatchConfigs)
    ? state.memoryBatchConfigs
    : [];
  if (!configs.length) {
    group.append(node("p", "empty-copy", "No stage configuration is available"));
    return group;
  }
  for (const config of configs) {
    const row = node("div", "memory-batch-row");
    const heading = node("div", "memory-batch-row-heading");
    const status = config.resolution?.status || "UNRESOLVED";
    heading.append(
      node("strong", "", config.stage),
      node("span", `memory-batch-status status-${String(status).toLowerCase()}`, status)
    );
    row.append(heading);

    const provider = document.createElement("select");
    for (const option of ["AUTO", "GROK", "CODEX", "CLAUDE"]) {
      appendSelectOption(provider, option, option);
    }
    provider.value = config.provider || "AUTO";
    const model = document.createElement("input");
    model.type = "text";
    model.value = config.model_ref || "";
    model.placeholder = "Catalog model or blank for default";
    model.maxLength = 256;
    const effort = document.createElement("select");
    for (const option of ["AUTO", "LOW", "MEDIUM", "HIGH", "MAX"]) {
      appendSelectOption(effort, option, option);
    }
    effort.value = config.effort || "AUTO";
    const schedule = document.createElement("select");
    for (const option of ["MANUAL", "HOURLY", "DAILY", "WEEKLY"]) {
      appendSelectOption(schedule, option, option);
    }
    schedule.value = config.schedule?.kind || "MANUAL";
    const fallback = document.createElement("select");
    for (const option of [
      "DETERMINISTIC",
      "DISABLE",
      "NONE",
      "AUTO",
      "GROK",
      "CODEX",
      "CLAUDE",
    ]) {
      appendSelectOption(fallback, option, option);
    }
    fallback.value = config.fallback || "DETERMINISTIC";
    const quota = document.createElement("input");
    quota.type = "number";
    quota.min = "0";
    quota.max = "1000000";
    quota.step = "1";
    quota.placeholder = "Unlimited";
    quota.value = config.quota_or_budget?.max_runs ?? "";
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = config.enabled !== false;
    const dryRun = document.createElement("input");
    dryRun.type = "checkbox";
    dryRun.checked = Boolean(config.dry_run);
    const toggles = node("div", "memory-batch-toggles");
    const enabledLabel = node("label", "memory-batch-toggle");
    enabledLabel.append(enabled, node("span", "", "Enabled"));
    const dryRunLabel = node("label", "memory-batch-toggle");
    dryRunLabel.append(dryRun, node("span", "", "Dry run"));
    toggles.append(enabledLabel, dryRunLabel);
    const fields = node("div", "memory-batch-fields");
    fields.append(
      memoryBatchField("Provider", provider),
      memoryBatchField("Model", model),
      memoryBatchField("Effort", effort),
      memoryBatchField("Schedule", schedule),
      memoryBatchField("Max runs", quota),
      memoryBatchField("Fallback", fallback),
      toggles
    );
    row.append(fields);
    const actions = node("div", "memory-batch-actions");
    const save = node("button", "secondary-button compact-action", "Save stage");
    save.type = "button";
    save.addEventListener("click", async () => {
      try {
        const maxRuns = quota.value === "" ? null : Number(quota.value);
        const result = await api(
          `/v1/projects/${encodeURIComponent(
            state.selectedProject.project_id
          )}/memory-batch-config`,
          {
            method: "POST",
            body: {
              stage: config.stage,
              provider: provider.value,
              model_ref: model.value.trim(),
              effort: effort.value,
              schedule: { kind: schedule.value },
              quota_or_budget:
                maxRuns === null ? null : { max_runs: maxRuns },
              fallback: fallback.value,
              enabled: enabled.checked,
              dry_run: dryRun.checked,
            },
          }
        );
        state.memoryBatchConfigs = state.memoryBatchConfigs.map((item) =>
          item.stage === config.stage ? result.config : item
        );
        renderMemory();
        toast(`${config.stage} configuration saved`);
      } catch (error) {
        toast(error.message, true);
      }
    });
    const run = node("button", "primary-button compact-action", "Run stage");
    run.type = "button";
    run.disabled = config.enabled === false;
    run.addEventListener("click", async () => {
      try {
        const result = await api(
          `/v1/projects/${encodeURIComponent(
            state.selectedProject.project_id
          )}/memory-batches/run`,
          { method: "POST", body: { stage: config.stage } }
        );
        state.memoryBatchRuns = [result.run, ...state.memoryBatchRuns];
        const candidates = await api(
          `/v1/projects/${encodeURIComponent(
            state.selectedProject.project_id
          )}/memory-candidates?limit=200`
        );
        state.memoryCandidates = candidates.candidates || [];
        renderMemory();
        toast(`${config.stage} completed`);
      } catch (error) {
        toast(error.message, true);
      }
    });
    actions.append(save, run);
    row.append(actions);
    group.append(row);
  }
  return group;
}

function renderMemoryCandidateReview() {
  const group = node("div", "detail-group memory-candidate-review");
  group.append(
    node("h3", "", "Candidate review"),
    node(
      "p",
      "context-copy",
      "Review-only candidates retain summaries, digests, ranges, and relations. Decisions never write Seed, facts, anchors, authority, or source."
    )
  );
  const filters = node("div", "memory-candidate-filters");
  const filterOptions = [
    ["stage", "Stage", ["", "FAST_EXTRACT", "CONSOLIDATE", "SYNTHESIZE", "INDEPENDENT_CHECK"]],
    ["kind", "Kind", ["", "MEMORY", "IDEA", "HYPOTHESIS", "PRODUCT"]],
    ["state", "State", ["", "REVIEW_REQUIRED", "KEEP", "IGNORE", "EXPLORE", "START_PRODUCT_DESIGN", "SUPERSEDED", "CONFLICTED"]],
  ];
  for (const [key, label, options] of filterOptions) {
    const select = document.createElement("select");
    for (const option of options) appendSelectOption(select, option, option || `All ${label}`);
    select.value = state.memoryCandidateFilters[key] || "";
    select.addEventListener("change", () => {
      state.memoryCandidateFilters[key] = select.value;
      renderMemory();
    });
    filters.append(memoryBatchField(label, select));
  }
  group.append(filters);
  const filtersState = state.memoryCandidateFilters;
  const candidates = (state.memoryCandidates || []).filter((candidate) =>
    (!filtersState.stage || candidate.stage === filtersState.stage) &&
    (!filtersState.kind || candidate.kind === filtersState.kind) &&
    (!filtersState.state || candidate.state === filtersState.state)
  );
  if (!candidates.length) {
    group.append(node("p", "empty-copy", "No candidates match the current filters"));
    return group;
  }
  const list = node("div", "memory-candidate-list");
  for (const candidate of candidates.slice(0, 50)) {
    const card = node("article", "memory-candidate-row");
    const provenance = candidate.provenance || {};
    const range = provenance.source_range || {};
    const refs = Array.isArray(provenance.ref_digests)
      ? provenance.ref_digests.length
      : 0;
    card.append(
      node("strong", "", `${candidate.kind} / ${candidate.stage}`),
      node("span", "memory-candidate-state", candidate.state),
      node("p", "", candidate.summary || "No summary"),
      node(
        "small",
        "",
        `session ${String(provenance.source_session || "UNKNOWN").slice(0, 16)} / range ${range.start ?? "-"}-${range.end ?? "-"} / refs ${refs} / repeated ${candidate.relevance?.repetition_count || 1}`
      )
    );
    if (candidate.state === "REVIEW_REQUIRED") {
      const actions = node("div", "memory-candidate-actions");
      for (const decision of ["IGNORE", "KEEP", "EXPLORE", "START_PRODUCT_DESIGN"]) {
        const button = node("button", "secondary-button compact-action", decision);
        button.type = "button";
        button.addEventListener("click", async () => {
          try {
            const result = await api(
              `/v1/memory-candidates/${encodeURIComponent(
                candidate.candidate_id
              )}/review`,
              { method: "POST", body: { decision } }
            );
            state.memoryCandidates = state.memoryCandidates.map((item) =>
              item.candidate_id === result.candidate.candidate_id
                ? result.candidate
                : item
            );
            renderMemory();
            toast(`Candidate ${decision.toLowerCase()}`);
          } catch (error) {
            toast(error.message, true);
          }
        });
        actions.append(button);
      }
      card.append(actions);
    }
    list.append(card);
  }
  group.append(list);
  return group;
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
  elements.memoryPanel.append(renderMemoryBatchStages());
  elements.memoryPanel.append(renderMemoryCandidateReview());

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
  initChatPanelResize();
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
  elements.releaseTargetProject.addEventListener("change", () => {
    state.selectedReleaseTargetProjectId =
      elements.releaseTargetProject.value || null;
    elements.releaseProposalOutput.replaceChildren();
    elements.releaseProposalOutput.classList.add("hidden");
    renderReleaseCatalog();
  });
  elements.settingsButton.addEventListener("click", () => {
    openProviderSettings().catch((error) => toast(error.message, true));
  });
  if (elements.actionInboxButton && elements.actionInboxDialog) {
    elements.actionInboxButton.addEventListener("click", openActionInbox);
    elements.actionInboxDialog.addEventListener("close", () => {
      if (!elements.mobileWorkTabs || window.innerWidth > 720) return;
      for (const item of elements.mobileWorkTabs.querySelectorAll("button")) {
        item.classList.toggle("selected", item.dataset.mobileWorkView === "goals");
      }
    });
  }
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
      await refreshSupervisorSessions({ maxAgeMs: 10_000 });
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
      if (!room) return;
      try {
        await activateAnchorSession(session, room);
        elements.sessionSummaryDialog.close();
      } catch (error) {
        toast(error.message, true);
      }
    });
  }
  if (elements.sessionSummaryConnect) {
    elements.sessionSummaryConnect.addEventListener("click", () => {
      connectSessionSummaryProviderModel().catch((error) => {
        if (elements.sessionSummaryConnectionStatus) {
          elements.sessionSummaryConnectionStatus.textContent = error.message;
        }
        toast(error.message, true);
      });
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
  if (elements.sessionWorkingDirectoryApply) {
    elements.sessionWorkingDirectoryApply.addEventListener("click", () => {
      rebindSelectedSessionWorkingDirectory().catch((error) => {
        if (elements.sessionWorkingDirectoryStatus) {
          elements.sessionWorkingDirectoryStatus.textContent = error.message;
        }
        toast(error.message, true);
      });
    });
  }
  if (elements.sessionSummaryNew) {
    elements.sessionSummaryNew.addEventListener("click", () => {
      connectSessionSummaryProviderModel("NEW").catch((error) => {
        if (elements.sessionSummaryConnectionStatus) {
          elements.sessionSummaryConnectionStatus.textContent = error.message;
        }
        toast(error.message, true);
      });
    });
  }
  if (elements.nodeSessionInbox) {
    elements.nodeSessionInbox.addEventListener("click", () => {
      const pending = state.pendingNodeSessionAction;
      if (!pending) return;
      elements.nodeSessionActionDialog?.close();
      openSessionBusInbox(pending.coordinate, pending.session).catch((error) =>
        toast(error.message, true)
      );
    });
  }
  if (elements.sessionBusCompose) {
    elements.sessionBusCompose.addEventListener("submit", (event) => {
      sendSessionBusCompose(event).catch((error) => toast(error.message, true));
    });
  }
  for (const tab of elements.sessionBusTabs || []) {
    tab.addEventListener("click", () => {
      state.sessionBusProjection = String(tab.dataset.sessionBusProjection || "INBOX").toUpperCase();
      for (const peer of elements.sessionBusTabs || []) {
        const active = peer === tab;
        peer.classList.toggle("active", active);
        peer.setAttribute("aria-selected", String(active));
      }
      refreshSessionBusMessages().catch((error) => toast(error.message, true));
    });
  }
  if (elements.sessionBusBody) {
    elements.sessionBusBody.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      if (event.isComposing || event.keyCode === 229) { event.preventDefault(); return; }
      if (event.shiftKey) return;
      event.preventDefault();
      elements.sessionBusCompose?.requestSubmit();
    });
  }
  if (elements.nodeSessionInspect) {
    elements.nodeSessionInspect.addEventListener("click", () => {
      const pending = state.pendingNodeSessionAction;
      if (!pending) return;
      elements.nodeSessionActionDialog?.close();
      inspectNodeModeSession(pending.coordinate, pending.session).catch((error) =>
        toast(error.message, true)
      );
    });
  }
  if (elements.nodeSessionOpen) {
    elements.nodeSessionOpen.addEventListener("click", () => {
      const pending = state.pendingNodeSessionAction;
      if (!pending) return;
      elements.nodeSessionActionDialog?.close();
      bindNodeModeSessionPty(pending.coordinate, pending.session).catch((error) =>
        toast(error.message, true)
      );
    });
  }
  if (elements.nodeSessionStop) {
    elements.nodeSessionStop.addEventListener("click", () => {
      const pending = state.pendingNodeSessionAction;
      if (!pending) return;
      elements.nodeSessionActionDialog?.close();
      endNodeModePtySession(pending.session).catch((error) => toast(error.message, true));
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
  if (elements.setupProviderHooks) {
    elements.setupProviderHooks.addEventListener("click", () => {
      setupProviderHooks().catch((error) => toast(error.message, true));
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
  elements.addProjectRailButton.addEventListener("click", () => {
    state.projectConnectionPlan = null;
    elements.projectSubmit.textContent = "Inspect project";
    elements.projectDialog.showModal();
  });
  elements.planProjectButton.addEventListener("click", () => {
    if (!state.selectedProject) return toast("Select a project first", true);
    openFreshProjectWizard();
  });
  elements.dispatchForm.addEventListener("submit", submitDispatch);
  elements.dispatchInstruction.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    if (event.isComposing || event.keyCode === 229) { event.preventDefault(); return; }
    if (event.shiftKey) return;
    event.preventDefault();
    elements.dispatchForm.requestSubmit();
  });
  elements.composerActionButton.addEventListener("click", () =>
    toggleComposerActionMenu()
  );
  elements.returnToConductor.addEventListener("click", async () => {
    try {
      await callUniverseConductor();
    } catch (error) {
      toast(error.message, true);
    }
  });
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
    if (!collapsed && typeof refitActiveTerminal === "function") refitActiveTerminal();
  });
  elements.conversationOpacity.addEventListener("input", () => {
    elements.conversationLayer.style.setProperty(
      "--conversation-opacity",
      String(Number(elements.conversationOpacity.value) / 100)
    );
  });
  elements.projectForm.addEventListener("submit", submitProject);
  elements.projectRootBrowse.addEventListener("click", selectProjectRoot);
  elements.freshProjectRootBrowse.addEventListener("click", selectFreshProjectRoot);
  elements.releaseDatabaseBrowse.addEventListener("click", selectReleaseDatabase);
  elements.releaseManifestBrowse.addEventListener("click", selectReleaseManifest);
  elements.workerBindingScope.addEventListener("change", renderWorkerBindingSettings);
  elements.settingsForm.addEventListener("submit", submitProviderSettings);

  document.querySelectorAll(".ghost-action[data-primary-view]").forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.getAttribute("data-primary-view");
      if (view === "work") {
        showGoalPlanView();
        return;
      }
      const nav = elements.primaryNav?.querySelector(`[data-primary-view="${view}"]`);
      if (nav) nav.click();
      else if (["memory", "future", "bench", "activity", "details"].includes(view)) {
        openInspectorSurface(view);
      } else if (view === "map" || view === "universe") {
        showGraphView(state.selectedProject ? "semantic" : "universe");
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
      if (view === "work") {
        showGoalPlanView();
        return;
      }
      if (view === "map" || view === "network" || view === "project" || view === "ecosystem") {
        showGraphView(state.selectedProject ? "semantic" : "universe");
        if (view === "ecosystem") {
          // Project list lives in the left rail — just focus map + list context.
          elements.projectList?.focus?.();
        }
        return;
      }
      if (view === "sessions" || view === "timeline" || view === "documents") {
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

function prepareGoalHierarchyFields({ scopeKind = "UNIVERSE", universeGoalId = "" } = {}) {
  const form = elements.goalForm;
  if (!form) return;
  const scope = form.elements.scope_kind;
  const parent = form.elements.universe_goal_id;
  const nodeOption = [...scope.options].find((option) => option.value === "NODE");
  if (nodeOption) nodeOption.disabled = !selectedNodeRef();
  scope.value = scopeKind === "NODE" && !selectedNodeRef() ? "PROJECT" : scopeKind;
  parent.replaceChildren(new Option("No global parent", ""));
  for (const goal of state.universeGoals || []) {
    parent.append(new Option(goal.title, goal.universe_goal_id));
  }
  parent.value = universeGoalId || "";
  parent.disabled = scope.value === "UNIVERSE";
}

function bindGoalPlanEvents() {
  elements.addGoalButton?.addEventListener("click", () => {
    if (!state.selectedProject) return;
    elements.goalForm.reset();
    delete elements.goalForm.dataset.goalId;
    elements.goalDialog.querySelector("h2").textContent = "New goal";
    elements.goalDialog.querySelector('[type="submit"]').textContent = "Create goal";
    elements.goalForm.elements.owner.value = "Project Master";
    prepareGoalHierarchyFields({ scopeKind: selectedNodeRef() ? "NODE" : "PROJECT" });
    elements.goalFormError.textContent = "";
    elements.goalDialog.showModal();
  });
  elements.goalForm?.elements.scope_kind?.addEventListener("change", () => {
    prepareGoalHierarchyFields({
      scopeKind: elements.goalForm.elements.scope_kind.value,
      universeGoalId: elements.goalForm.elements.universe_goal_id.value,
    });
  });
  if (elements.conversationExpand) {
    let parent = elements.conversationLayer.parentElement;
    let nextSibling = elements.conversationLayer.nextElementSibling;
    elements.conversationExpand.addEventListener("change", () => {
      const expanded = elements.conversationExpand.checked;
      elements.conversationLayer.classList.toggle("expanded", expanded);
      elements.conversationLayer.classList.remove("collapsed");
      syncConversationToggle(false);
      elements.conversationExpand.setAttribute(
        "aria-label",
        expanded ? "Close expanded conversation" : "Expand conversation"
      );
      if (expanded) {
        parent = elements.conversationLayer.parentElement;
        nextSibling = elements.conversationLayer.nextElementSibling;
        document.body.append(elements.conversationLayer);
      } else if (parent) {
        parent.insertBefore(elements.conversationLayer, nextSibling);
      }
      renderRoomMessages();
    });
  }
  elements.goalPlanMap?.addEventListener("click", () => showGraphView("semantic"));
  elements.editSelectedGoal?.addEventListener("click", () => {
    const goal = state.goals.find((item) => item.goal_id === state.selectedGoalId);
    if (goal) openGoalEditor(goal);
  });
  const handleWorkspaceNav = (event) => {
    const button = event.target.closest("[data-primary-view]");
    if (!button) return;
    const view = button.getAttribute("data-primary-view");
    if (view === "work") showGoalPlanView();
    else if (view === "map") showGraphView(state.selectedProject ? "semantic" : "universe");
    else if (view === "documents") showGraphView("documents");
    else if (view === "sessions") showGraphView("sessions");
    else if (view === "meeting") {
      state.settingsTab = "rooms";
      openProviderSettings().catch((error) => toast(error.message, true));
    }
    else if (["memory", "bench", "activity", "details"].includes(view)) openInspectorSurface(view);
  };
  elements.utilityRail?.addEventListener("click", handleWorkspaceNav);
  elements.quickNewSessionButton?.addEventListener("click", openNewSessionDialog);
  elements.newSessionForm?.addEventListener("submit", submitNewSession);
  elements.quickConductorButton?.addEventListener("click", () => elements.dispatchInstruction?.focus());
  elements.quickTaskButton?.addEventListener("click", () => openTodoDialog(true));
  elements.mobileWorkTabs?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mobile-work-view]");
    if (!button) return;
    for (const item of elements.mobileWorkTabs.querySelectorAll("button")) item.classList.toggle("selected", item === button);
    const view = button.getAttribute("data-mobile-work-view");
    if (view === "goals") showGoalPlanView();
    else if (view === "sessions") showGraphView("sessions");
    // Bench is project context, not a graph canvas mode. On mobile the
    // Inspector becomes the focused surface so the comparison data remains
    // reachable instead of silently falling back to the Universe graph.
    else if (view === "bench") openInspectorSurface("bench");
    else if (view === "actions") openActionInbox();
  });
  const activeGoal = () => state.goals.find((goal) => goal.goal_id === state.selectedGoalId) || state.goals[0];
  elements.mobileDelegateGoal?.addEventListener("click", () => {
    const goal = activeGoal();
    if (!goal) return;
    elements.dispatchInstruction.value = `Delegate goal \"${goal.title}\" to the Project Master. Use this goal plan as the planning reference.`;
    elements.dispatchInstruction.focus();
  });
  elements.mobileEditPlan?.addEventListener("click", () => elements.addGoalButton?.click());
  elements.mobileAddMilestone?.addEventListener("click", () => {
    const goal = activeGoal();
    if (!goal) return;
    elements.milestoneForm.elements.goal_id.value = goal.goal_id;
    elements.milestoneDialog.showModal();
  });
  elements.goalForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.selectedProject) return;
    const data = new FormData(elements.goalForm);
    try {
      const goalId = elements.goalForm.dataset.goalId;
      const scopeKind = String(data.get("scope_kind") || "UNIVERSE");
      const globalGoal = scopeKind === "UNIVERSE";
      if (goalId && globalGoal) throw new Error("Existing project goals stay in the selected project scope.");
      const parentGoalId = String(data.get("universe_goal_id") || "");
      if (scopeKind === "NODE" && !selectedNodeRef()) {
        throw new Error("Select a graph node before creating a Node goal.");
      }
      await api(
        globalGoal
          ? "/v1/universe-goals"
          : goalId
          ? `/v1/goals/${encodeURIComponent(goalId)}`
          : `/v1/projects/${encodeURIComponent(state.selectedProject.project_id)}/goals`, {
        method: goalId ? "PATCH" : "POST",
        body: {
          title: String(data.get("title") || "").trim(),
          description: String(data.get("description") || "").trim(),
          owner: String(data.get("owner") || "").trim(),
          state: String(data.get("state") || "DESIGNING"),
          sort_order: globalGoal
            ? (state.universeGoals || []).length
            : goalId
            ? state.goals.find((goal) => goal.goal_id === goalId)?.sort_order || 0
            : state.goals.length,
          ...(!globalGoal ? {
            scope_kind: scopeKind,
            ...(scopeKind === "NODE" ? { node_ref: selectedNodeRef() } : {}),
          } : {}),
          ...(!globalGoal && parentGoalId ? { universe_goal_id: parentGoalId } : {}),
          ...(goalId ? { revision: state.goals.find((goal) => goal.goal_id === goalId)?.revision } : {}),
        },
      });
      elements.goalDialog.close();
      delete elements.goalForm.dataset.goalId;
      await refreshGoalPlan();
      toast(goalId ? "Goal updated" : "Goal created");
    } catch (error) {
      elements.goalFormError.textContent = error.message;
    }
  });
  elements.milestoneForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(elements.milestoneForm);
    const goalId = String(data.get("goal_id") || "");
    const goal = state.goals.find((item) => item.goal_id === goalId);
    if (!goal) return;
    try {
      await api(`/v1/goals/${encodeURIComponent(goalId)}/milestones`, {
        method: "POST",
        body: {
          title: String(data.get("title") || "").trim(),
          description: String(data.get("description") || "").trim(),
          state: "PLANNED",
          sort_order: (goal.milestones || []).length,
        },
      });
      elements.milestoneDialog.close();
      elements.milestoneForm.reset();
      await refreshGoalPlan();
      toast("Milestone added");
    } catch (error) {
      elements.milestoneFormError.textContent = error.message;
    }
  });
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
  bindGoalPlanEvents();
}

bindEvents();
window.addEventListener("beforeunload", closeAllProviderSessionStreams);
refresh().finally(() => {
  openConductorRoomStream();
  void tailProviderSessions();
});
window.setInterval(refreshConductorRoom, 1200);
state.providerTailTimer = window.setInterval(tailProviderSessions, 4000);
