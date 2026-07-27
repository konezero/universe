"use strict";

const state = {
  token: "",
  projects: [],
  selectedProject: null,
  projection: null,
  dispatches: [],
  releases: [],
  releaseProposals: [],
  selectedNode: null,
  view: "system",
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
  releaseDialog: document.querySelector("#release-dialog"),
  releaseForm: document.querySelector("#release-form"),
  releaseList: document.querySelector("#release-list"),
  releaseFormError: document.querySelector("#release-form-error"),
  releaseProposalOutput: document.querySelector("#release-proposal-output"),
  accessDialog: document.querySelector("#access-dialog"),
  accessForm: document.querySelector("#access-form"),
  accessFormError: document.querySelector("#access-form-error"),
  toasts: document.querySelector("#toast-region"),
};

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}

function tokenFromLocation() {
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ""));
  const incoming = fragment.get("token");
  if (incoming) {
    sessionStorage.setItem("universe-token", incoming);
    history.replaceState(null, "", location.pathname + location.search);
  }
  return incoming || sessionStorage.getItem("universe-token") || "";
}

async function api(path, options = {}) {
  if (!state.token) throw new Error("Local access token required");
  const headers = {
    Authorization: `Bearer ${state.token}`,
    ...(options.body ? { "Content-Type": "application/json" } : {}),
  };
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
    if (!state.token) {
      elements.accessDialog.showModal();
      renderEmpty();
      return;
    }
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
  renderProjects();
  await api(`/v1/projects/${encodeURIComponent(projectId)}/sync`, {
    method: "POST",
    body: {},
  }).catch(() => null);
  const [projectionResult, dispatchResult, proposalResult] = await Promise.all([
    api(`/v1/projects/${encodeURIComponent(projectId)}/projection`).catch(
      () => null
    ),
    api(`/v1/projects/${encodeURIComponent(projectId)}/dispatches`),
    api(`/v1/projects/${encodeURIComponent(projectId)}/release-proposals`),
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
  elements.workspaceTitle.textContent = project.project_id;
  elements.workspaceSubtitle.textContent =
    state.projection?.project?.goal || project.project_root;
  buildGraph();
  renderDetails();
  renderActivity();
  renderReleaseCatalog();
}

function buildGraph() {
  if (!state.selectedProject) {
    state.graph.nodes = [];
    state.graph.edges = [];
    drawGraph();
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
    const isImplementationLink = edge.kind === "implementation-binding";
    context.lineWidth = isDocumentLink ? 1.4 : 1.2;
    context.strokeStyle = isDocumentLink
      ? "#a15c00"
      : isImplementationLink
        ? "#6b4fa3"
        : "#aeb7c1";
    context.setLineDash(isDocumentLink ? [5, 4] : isImplementationLink ? [3, 3] : []);
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
      document: "#a15c00",
      implementation: "#6b4fa3",
      predicted: "#b13b36",
    }[item.kind];
    const widthValue = item.kind === "project" ? 116 : 108;
    const heightValue = item.kind === "project" ? 42 : 36;
    context.fillStyle = selected ? "#20262d" : "#ffffff";
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
  state.selectedNode =
    [...state.graph.nodes]
      .reverse()
      .find(
        (item) =>
          Math.abs(point.x - item.x) <= 58 &&
          Math.abs(point.y - item.y) <= 24
      ) || null;
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
  if (data.node_ids?.length) addDetail(grid, "Related to", data.node_ids.join(", "));
  if (data.path) addDetail(grid, "Path", data.path);
  if (data.project_root) addDetail(grid, "Root", data.project_root);
  if (data.symbol) addDetail(grid, "Symbol", data.symbol);
  heading.append(grid);
  elements.details.append(heading);

  const relatedDocuments = (state.projection?.documents || []).filter((item) =>
    selected?.kind === "system"
      ? item.node_ids?.includes(data.node_id)
      : item.project_wide === true
  );
  if (!selected || relatedDocuments.length) {
    const contextGroup = node("div", "detail-group");
    contextGroup.append(node("h3", "", selected ? "Related documents" : "Project context"));
    if (!selected && data.summary) {
      contextGroup.append(node("p", "context-copy", data.summary));
    }
    if (!selected && data.working_rules?.length) {
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
  if (!state.dispatches.length) {
    elements.activity.append(node("p", "empty-copy", "No dispatches"));
    return;
  }
  const timeline = node("div", "timeline");
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
  elements.graphEmpty.classList.remove("hidden");
  state.graph.nodes = [];
  state.graph.edges = [];
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
    await api(
      `/v1/projects/${encodeURIComponent(
        state.selectedProject.project_id
      )}/dispatches`,
      {
        method: "POST",
        body: {
          idempotency_key: crypto.randomUUID(),
          title: form.get("title"),
          instruction: form.get("instruction"),
          requested_mode: "MASTER",
          constraints: [],
          expected_output: {},
        },
      }
    );
    elements.dispatchForm.reset();
    toast("Dispatch queued");
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

function submitAccess(event) {
  event.preventDefault();
  const form = new FormData(elements.accessForm);
  const token = String(form.get("token") || "").trim();
  if (!token) {
    elements.accessFormError.textContent = "Access token required";
    return;
  }
  state.token = token;
  sessionStorage.setItem("universe-token", token);
  elements.accessDialog.close();
  elements.accessForm.reset();
  refresh();
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
    .querySelector("#access-button")
    .addEventListener("click", () => elements.accessDialog.showModal());
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
  elements.dispatchForm.addEventListener("submit", submitDispatch);
  elements.prepareProject.addEventListener("click", prepareProjectSeed);
  elements.projectForm.addEventListener("submit", submitProject);
  elements.releaseForm.addEventListener("submit", submitRelease);
  elements.accessForm.addEventListener("submit", submitAccess);
  for (const button of document.querySelectorAll("[data-close-dialog]")) {
    button.addEventListener("click", () => button.closest("dialog").close());
  }
  document.querySelector("[data-clear-token]").addEventListener("click", () => {
    sessionStorage.removeItem("universe-token");
    state.token = "";
    elements.accessForm.reset();
    elements.accessDialog.close();
    renderEmpty();
  });
  for (const button of document.querySelectorAll("[data-view]")) {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      for (const candidate of document.querySelectorAll("[data-view]")) {
        candidate.classList.toggle("selected", candidate === button);
      }
      state.selectedNode = null;
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

state.token = tokenFromLocation();
bindEvents();
refresh();
