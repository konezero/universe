function terminalLabel(session) {
  const project = String(session?.project_id || "session").trim();
  const mode = String(session?.mode || "").trim().toUpperCase();
  return mode ? `${project} ${mode}` : project;
}

function applyCliDockTitle(session) {
  if (elements.conversationTitle) {
    elements.conversationTitle.textContent = "CLI";
  }
  if (elements.conversationTargetLabel) {
    elements.conversationTargetLabel.textContent = session
      ? terminalLabel(session)
      : "No terminal tab";
  }
}

function activeTerminalSession() {
  return (state.terminals || []).find((item) => item.terminal_id === state.activeTerminalId) || null;
}

function sendTerminalInput(text) {
  const surface = (state.terminalSurfaces || {})[state.activeTerminalId];
  if (!surface?.socket || surface.socket.readyState !== WebSocket.OPEN) {
    throw new Error("CLI tab is not connected");
  }
  const payload = String(text || "");
  if (!payload) return;
  const body = payload.endsWith("\n") || payload.endsWith("\r") ? payload : payload + "\r";
  surface.socket.send(new TextEncoder().encode(body));
}

function bindTerminalInput() {
  const form = document.querySelector("#terminal-input-form");
  const input = document.querySelector("#terminal-input");
  if (!form || !input || form.dataset.bound === "true") return;
  form.dataset.bound = "true";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = String(input.value || "");
    if (!value.trim()) return;
    try {
      sendTerminalInput(value);
      input.value = "";
    } catch (error) {
      toast(error.message, true);
    }
    input.focus();
  });
}

function focusTerminalInput() {
  const surface = (state.terminalSurfaces || {})[state.activeTerminalId];
  const form = document.querySelector("#terminal-input-form");
  if (surface?.term) {
    if (form) form.hidden = true;
    try { surface.term.focus(); } catch (_e) { /* pane not ready */ }
    return;
  }
  if (form) form.hidden = false;
  const input = document.querySelector("#terminal-input");
  if (input) {
    input.disabled = !surface;
    input.focus();
  }
}

function writeTerminalBytes(term, data) {
  if (data instanceof Blob) {
    data.arrayBuffer().then((buffer) => term.write(new Uint8Array(buffer)));
    return;
  }
  if (data instanceof ArrayBuffer) {
    term.write(new Uint8Array(data));
    return;
  }
  if (ArrayBuffer.isView(data)) {
    term.write(data);
    return;
  }
  term.write(String(data || ""));
}

function renderTerminalDock() {
  const tabs = elements.terminalTabs;
  const stage = elements.terminalStage;
  if (!tabs || !stage) return;
  bindTerminalInput();
  const sessions = state.terminals || [];
  tabs.replaceChildren();
  if (!sessions.length) {
    tabs.append(node("p", "terminal-empty", "Open a node mode session to start a CLI tab"));
    applyCliDockTitle(null);
    focusTerminalInput();
    return;
  }
  for (const session of sessions) {
    const active = session.terminal_id === state.activeTerminalId;
    const tab = node("button", "terminal-tab");
    tab.type = "button";
    tab.role = "tab";
    tab.ariaSelected = String(active);
    tab.dataset.terminalId = session.terminal_id;
    tab.append(node("span", "", terminalLabel(session)));
    const close = node("span", "terminal-tab-close", "×");
    close.title = "Close tab";
    close.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      closeTerminalTab(session.terminal_id).catch((error) => toast(error.message, true));
    });
    tab.append(close);
    tab.addEventListener("click", () => selectTerminalTab(session.terminal_id));
    tabs.append(tab);
  }
}

function autoWidenForTerminal() {
  const shell = document.querySelector(".app-shell");
  if (!shell) return;
  const current = parseInt(getComputedStyle(shell).getPropertyValue("--chat-panel-width")) || 380;
  if (current < 600) {
    shell.style.setProperty("--chat-panel-width", "680px");
    const handle = document.querySelector("#chat-resize-handle");
    if (handle) handle.setAttribute("aria-valuenow", "680");
  }
}

function selectTerminalTab(terminalId) {
  const session = (state.terminals || []).find((item) => item.terminal_id === terminalId);
  if (!session) return;
  state.activeTerminalId = terminalId;
  renderTerminalDock();
  ensureTerminalSurface(session);
  autoWidenForTerminal();
  for (const [id, surface] of Object.entries(state.terminalSurfaces || {})) {
    if (!surface?.element) continue;
    surface.element.hidden = id !== terminalId;
    if (id === terminalId && surface.fit) {
      try {
        surface.fit.fit();
        surface.notifySize?.();
      } catch (_error) {
        /* ignore fit until the tab is visible */
      }
    }
  }
  applyCliDockTitle(session);
  focusTerminalInput();
}

function ensureTerminalSurface(session) {
  if (!elements.terminalStage || typeof Terminal !== "function") return;
  state.terminalSurfaces = state.terminalSurfaces || {};
  let surface = state.terminalSurfaces[session.terminal_id];
  if (surface) return surface;
  const element = node("div", "terminal-pane");
  element.dataset.terminalId = session.terminal_id;
  elements.terminalStage.append(element);
  const term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    convertEol: true,
    theme: {
      background: "#07101d",
      foreground: "#d7e6ff",
    },
  });
  const fit = typeof FitAddon === "function"
    ? new FitAddon()
    : window.FitAddon
      ? new window.FitAddon.FitAddon()
      : null;
  if (fit) term.loadAddon(fit);
  term.open(element);
  if (fit) fit.fit();
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${protocol}://${window.location.host}/v1/terminals/${encodeURIComponent(session.terminal_id)}/stream`
  );
  socket.binaryType = "arraybuffer";
  socket.addEventListener("message", (event) => writeTerminalBytes(term, event.data));
  term.onData((data) => {
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(new TextEncoder().encode(data));
    }
  });
  const notifySize = () => {
    if (socket.readyState !== WebSocket.OPEN) return;
    if (fit) {
      try {
        fit.fit();
      } catch (_error) {
        /* ignore until the pane has a box */
      }
    }
    const cols = Math.max(80, Number(term.cols) || 80);
    const rows = Math.max(24, Number(term.rows) || 24);
    socket.send(JSON.stringify({ type: "resize", cols, rows }));
  };
  socket.addEventListener("open", notifySize);
  socket.addEventListener("close", () => {
    term.write("\r\n\x1b[90m[session closed]\x1b[0m\r\n");
  });
  window.addEventListener("resize", notifySize);
  const resizeObserver = new ResizeObserver(() => {
    if (element.hidden) return;
    notifySize();
  });
  resizeObserver.observe(element);
  surface = { element, term, fit, socket, notifySize, resizeObserver };
  state.terminalSurfaces[session.terminal_id] = surface;
  return surface;
}

function projectMasterSetting(projectId) {
  return ((state.providerSettings || {}).project_masters || []).find(
    (item) => item.scope_id === projectId
  );
}

function observerProvider(session) {
  const raw = String(
    session?.observer_session_ref || session?.session_id || ""
  ).toLowerCase();
  if (raw.startsWith("grok-acp:") || raw.startsWith("grok-cli:")) return "GROK";
  if (raw.startsWith("claude-code:")) return "CLAUDE";
  if (raw.startsWith("codex-app-server:") || raw.startsWith("codex:")) return "CODEX";
  return "";
}

function terminalProviderFor(coordinate, session) {
  const fromObserver = observerProvider(session);
  if (fromObserver) return fromObserver;
  const projectId = String(
    coordinate?.project?.project_id ||
      coordinate?.nodeId ||
      session?.project_id ||
      session?.node ||
      ""
  ).trim();
  const setting = projectMasterSetting(projectId);
  const configured = String(setting?.provider || "").toUpperCase();
  if (configured && configured !== "AUTO") return configured;
  return String(setting?.resolved_provider || "AUTO").toUpperCase();
}

function terminalResumeRef(coordinate, session) {
  if (!session) return "";
  const raw = String(
    session.provider_session_id ||
      session.observer_session_ref ||
      session.session_id ||
      ""
  ).trim();
  const stripped = raw.replace(
    /^(grok-acp:|grok-cli:|claude-code:|codex-app-server:|codex:)/i,
    ""
  );
  if (!stripped || /^(UNKNOWN|UNASSIGNED|NONE)$/i.test(stripped)) return "";
  if (/^[A-Z]+-CURRENT-/i.test(stripped)) return "";
  if (stripped.startsWith("UNIVERSE-")) return "";
  return stripped;
}

async function createTerminalTab(coordinate, session) {
  const project = coordinate?.project || {};
  const projectId = String(project.project_id || coordinate?.nodeId || "").trim();
  const mode = String(coordinate?.mode || "MASTER").toUpperCase();
  const cwd = String(project.project_root || "").trim();
  if (!projectId || !cwd) {
    throw new Error("A registered project root is required to open a CLI tab");
  }
  const created = await api("/v1/terminals", {
    method: "POST",
    body: {
      project_id: projectId,
      mode,
      cwd,
      provider: terminalProviderFor(coordinate, session),
      resume_session_ref: terminalResumeRef(coordinate, session),
    },
  });
  const tab = created.terminal || created;
  state.terminals = [...(state.terminals || []).filter((item) => item.terminal_id !== tab.terminal_id), tab];
  selectTerminalTab(tab.terminal_id);
  return tab;
}

async function closeTerminalTab(terminalId) {
  await api("/v1/terminals/" + encodeURIComponent(terminalId), { method: "DELETE" });
  const surface = (state.terminalSurfaces || {})[terminalId];
  if (surface) {
    try { surface.resizeObserver?.disconnect(); } catch (_e) { /* ok */ }
    try { surface.socket.close(); } catch (_e) { /* already closed */ }
    try { surface.term.dispose(); } catch (_e) { /* ok */ }
    surface.element.remove();
    delete state.terminalSurfaces[terminalId];
  }
  state.terminals = (state.terminals || []).filter((item) => item.terminal_id !== terminalId);
  if (state.activeTerminalId === terminalId) {
    state.activeTerminalId = state.terminals[0]?.terminal_id || null;
  }
  if (state.activeTerminalId) selectTerminalTab(state.activeTerminalId);
  else {
    applyCliDockTitle(null);
    renderTerminalDock();
  }
}

function focusTerminalForSession(coordinate, session) {
  const projectId = String(
    session?.project_id || session?.node || coordinate?.project?.project_id || ""
  ).trim();
  const mode = String(session?.mode || coordinate?.mode || "").toUpperCase();
  const provider = terminalProviderFor(coordinate, session);
  const match = (state.terminals || []).find(
    (item) =>
      item.project_id === projectId &&
      item.mode === mode &&
      (!provider || provider === "AUTO" || item.provider === provider)
  );
  if (match) {
    selectTerminalTab(match.terminal_id);
    return true;
  }
  return false;
}

async function loadTerminalTabs() {
  try {
    const payload = await api("/v1/terminals");
    state.terminals = payload.terminals || [];
    if (!state.activeTerminalId && state.terminals[0]) {
      state.activeTerminalId = state.terminals[0].terminal_id;
    }
    renderTerminalDock();
    if (state.activeTerminalId) selectTerminalTab(state.activeTerminalId);
    else applyCliDockTitle(null);
  } catch (_error) {
    state.terminals = state.terminals || [];
  }
}
