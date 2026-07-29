"use strict";

const state = {
  projects: [],
  selectedProject: null,
  projection: null,
  dispatches: [],
  releases: [],
  releaseProposals: [],
  selectedNode: null,
  focusedNodeId: null,
  view: "timeline",
  roomMessages: [],
  masterBridge: null,
  freshProject: {
    intent: null,
    routes: [],
    composition: null,
    refinementRequest: null,
    adoption: null,
  },
  graph: { nodes: [], edges: [], scale: 1, x: 0, y: 0 },
};

const elements = {
  serviceStatus: document.querySelector("#service-status"),
  projectList: document.querySelector("#project-list"),
  workspaceTitle: document.querySelector("#workspace-title"),
  workspaceSubtitle: document.querySelector("#workspace-subtitle"),
  canvas: document.querySelector("#universe-graph"),
  graphEmpty: document.querySelector("#graph-empty"),
  details: document.querySelector("#details-panel"),
  activity: document.querySelector("#activity-panel"),
  dispatchForm: document.querySelector("#dispatch-form"),
  dispatchSubmit: document.querySelector("#dispatch-submit"),
  prepareProject: document.querySelector("#prepare-project-button"),
  projectDialog: document.querySelector("#project-dialog"),
  projectForm: document.querySelector("#project-form"),
  projectFormError: document.querySelector("#project-form-error"),
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
  conversationOpacity: document.querySelector("#conversation-opacity"),
  roomMessageList: document.querySelector("#room-message-list"),
  roomContext: document.querySelector("#room-context"),
  closeInspector: document.querySelector("#close-inspector"),
  nodeBreadcrumb: document.querySelector("#node-breadcrumb"),
  nodeBreadcrumbProject: document.querySelector("#node-breadcrumb-project"),
  nodeBreadcrumbNode: document.querySelector("#node-breadcrumb-node"),
  exitNodeUniverse: document.querySelector("#exit-node-universe"),
  toasts: document.querySelector("#toast-region"),
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

async function refresh() {
  try {
    const health = await fetch("/health", { cache: "no-store" }).then((response) =>
      response.json()
    );
    elements.serviceStatus.dataset.state = health.status === "READY" ? "ready" : "error";
    elements.serviceStatus.textContent =
      health.status === "READY" ? "Local service" : health.status;

    const [projectResult, releaseResult] = await Promise.all([
      api("/v1/projects"),
      api("/v1/releases"),
    ]);
    state.projects = projectResult.projects;
    state.releases = releaseResult.releases;
    renderProjects();
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
    copy.append(
      node("span", "project-name", project.project_id),
      node(
        "span",
        "project-meta",
        project.metadata.label || project.refs.mode_registry
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
  renderProjects();
  await api(`/v1/projects/${encodeURIComponent(projectId)}/sync`, {
    method: "POST",
    body: {},
  }).catch(() => null);
  const [projectionResult, dispatchResult, proposalResult, roomResult, bridgeResult] = await Promise.all([
    api(`/v1/projects/${encodeURIComponent(projectId)}/projection`).catch(
      () => null
    ),
    api(`/v1/projects/${encodeURIComponent(projectId)}/dispatches`),
    api(`/v1/projects/${encodeURIComponent(projectId)}/release-proposals`),
    api(`/v1/projects/${encodeURIComponent(projectId)}/room/messages`).catch(() => ({ messages: [] })),
    api(`/v1/projects/${encodeURIComponent(projectId)}/master-bridge`).catch(() => ({ bridge: null })),
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
  elements.workspaceTitle.textContent = project.project_id;
  elements.workspaceSubtitle.textContent =
    state.projection?.project?.goal || project.project_root;
  elements.roomContext.textContent = state.masterBridge?.status === "AVAILABLE"
    ? `${project.project_id} · Master bridge connected`
    : `${project.project_id} · Project Master · Inbox fallback`;
  buildGraph();
  renderDetails();
  renderActivity();
  renderRoomMessages();
  renderReleaseCatalog();
}

function renderRoomMessages() {
  elements.roomMessageList.replaceChildren();
  if (!state.roomMessages.length) {
    elements.roomMessageList.append(
      node("p", "empty-copy", "Messages to the Project Master appear here")
    );
    return;
  }
  for (const message of state.roomMessages.slice(-8)) {
    const item = node("article", "room-message");
    item.append(
      node("strong", "", `${message.sender} · ${message.kind}`),
      node("p", "", message.body),
      node("small", "", message.delivery_state)
    );
    elements.roomMessageList.append(item);
  }
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
      ? "#ac8bff"
      : isDocumentLink
      ? "#a15c00"
      : isImplementationLink
        ? "#6b4fa3"
        : "#aeb7c1";
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
      project: "#087f78",
      system: "#1769aa",
      focus: "#41d7c3",
      related: "#61a8ff",
      document: "#a15c00",
      implementation: "#6b4fa3",
      predicted: "#b13b36",
    }[item.kind];
    const widthValue = ["project", "focus"].includes(item.kind) ? 126 : 108;
    const heightValue = ["project", "focus"].includes(item.kind) ? 42 : 36;
    context.fillStyle = selected ? "#20262d" : item.kind === "focus" ? "#0e2528" : "#ffffff";
    context.strokeStyle = selected ? "#20262d" : color;
    context.lineWidth = selected ? 2.4 : 1.6;
    roundedRect(
      context,
      item.x - widthValue / 2,
      item.y - heightValue / 2,
      widthValue,
      heightValue,
      5
    );
    context.fill();
    context.stroke();
    context.fillStyle = selected ? "#ffffff" : "#20262d";
    context.font = `${item.kind === "project" ? "600" : "500"} 11px Segoe UI`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(
      truncate(item.label, 18),
      item.x,
      item.y,
      widthValue - 12
    );
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
  if (selected.kind === "project") {
    state.focusedNodeId = null;
    state.selectedNode = selected;
    buildGraph();
    renderDetails();
    return;
  }
  if (["system", "related", "focus"].includes(selected.kind)) {
    state.focusedNodeId =
      state.focusedNodeId === selected.data.node_id ? null : selected.data.node_id;
    state.selectedNode = selected;
    buildGraph();
    renderDetails();
    return;
  }
  state.selectedNode = selected;
  drawGraph();
  renderDetails();
}

function renderDetails() {
  elements.details.replaceChildren();
  if (!state.selectedProject) {
    elements.details.append(node("p", "empty-copy", "No project selected"));
    return;
  }
  const selected = state.selectedNode;
  document.body.classList.toggle("inspector-open", Boolean(selected));
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
  if (!state.dispatches.length && !state.roomMessages.length) {
    elements.activity.append(node("p", "empty-copy", "No dispatches"));
    return;
  }
  const timeline = node("div", "timeline");
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
  elements.roomContext.textContent = "Project Master";
  document.body.classList.remove("inspector-open");
  elements.graphEmpty.classList.remove("hidden");
  state.graph.nodes = [];
  state.graph.edges = [];
  state.focusedNodeId = null;
  elements.nodeBreadcrumb.classList.add("hidden");
  drawGraph();
  renderDetails();
  renderActivity();
}

async function submitDispatch(event) {
  event.preventDefault();
  if (!state.selectedProject) {
    toast("Select a project", true);
    return;
  }
  const form = new FormData(elements.dispatchForm);
  elements.dispatchSubmit.disabled = true;
  try {
    const result = await api(
      `/v1/projects/${encodeURIComponent(
        state.selectedProject.project_id
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
    toast(
      result.message.delivery_state === "DELIVERED_TO_MASTER"
        ? "Delivered to the registered Project Master"
        : "Project Room message recorded; Inbox fallback is available"
    );
    await selectProject(state.selectedProject.project_id);
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
    adoption: null,
  };
  elements.freshProjectForm.reset();
  elements.freshProjectRouteList.replaceChildren();
  elements.freshProjectCompositionOutput.replaceChildren();
  elements.freshProjectRefinementRef.textContent = "";
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
    toast("Assistant refinement request prepared");
  } catch (error) {
    elements.freshProjectError.textContent = error.message;
  } finally {
    elements.prepareRefinementButton.disabled = false;
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
    elements.freshProjectAdoptionRef.textContent =
      `${result.adoption.adoption_id} · Project Master handoff candidate`;
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

  document
    .querySelector("#release-button")
    .addEventListener("click", () => {
      elements.releaseFormError.textContent = "";
      renderReleaseCatalog();
      elements.releaseDialog.showModal();
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
  elements.prepareProject.addEventListener("click", prepareProjectSeed);
  elements.exitNodeUniverse.addEventListener("click", exitNodeUniverse);
  elements.closeInspector.addEventListener("click", closeInspector);
  elements.conversationToggle.addEventListener("click", () => {
    const collapsed = elements.conversationLayer.classList.toggle("collapsed");
    elements.conversationToggle.textContent = collapsed ? "+" : "-";
    elements.conversationToggle.title = collapsed ? "Expand conversation" : "Collapse conversation";
    elements.conversationToggle.setAttribute("aria-label", elements.conversationToggle.title);
  });
  elements.conversationOpacity.addEventListener("input", () => {
    elements.conversationLayer.style.setProperty(
      "--conversation-opacity",
      String(Number(elements.conversationOpacity.value) / 100)
    );
  });
  elements.projectForm.addEventListener("submit", submitProject);
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
  elements.canvas.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      state.graph.scale = Math.min(
        1.8,
        Math.max(0.55, state.graph.scale * (event.deltaY > 0 ? 0.9 : 1.1))
      );
      drawGraph();
    },
    { passive: false }
  );
  const resize = new ResizeObserver(drawGraph);
  resize.observe(elements.canvas);
}

bindEvents();
refresh();
