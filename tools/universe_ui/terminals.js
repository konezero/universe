function isRemoteBrowser() {
  return String(state.accessSurface || "LOCAL_BROWSER").toUpperCase() === "REMOTE_BROWSER";
}

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

function focusTerminalInput() {
  const surface = (state.terminalSurfaces || {})[state.activeTerminalId];
  if (surface?.term) {
    try { surface.term.focus(); } catch (_e) { /* pane not ready */ }
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
    if (id === terminalId) refitActiveTerminal();
  }
  applyCliDockTitle(session);
  focusTerminalInput();
}

function sendPtyText(socket, data) {
  if (socket.readyState !== WebSocket.OPEN) return;
  const text = typeof data === "string" ? data.normalize("NFC") : String(data || "");
  if (!text) return;
  socket.send(new TextEncoder().encode(text));
}

const TERMINAL_COLS = 80;
const TERMINAL_ROWS = 50;

function bindTerminalIme(term, socket) {
  const textarea = term.textarea || term.element?.querySelector(".xterm-helper-textarea");
  let composing = false;
  let justComposed = false;
  if (typeof term.attachCustomKeyEventHandler === "function") {
    term.attachCustomKeyEventHandler((event) => {
      if (event.isComposing || event.keyCode === 229 || justComposed) return false;
      return true;
    });
  }
  if (textarea) {
    textarea.setAttribute("autocapitalize", "off");
    textarea.setAttribute("autocomplete", "off");
    textarea.setAttribute("spellcheck", "false");
    textarea.addEventListener("compositionstart", () => {
      composing = true;
      justComposed = false;
    }, true);
    textarea.addEventListener("compositionend", () => {
      composing = false;
      justComposed = true;
      window.setTimeout(() => {
        justComposed = false;
        try { textarea.value = ""; } catch (_error) { /* ignore */ }
      }, 0);
    }, true);
  }
  term.onData((data) => {
    if (composing) return;
    sendPtyText(socket, data);
  });
}

function scaleFontToContainer(term, element) {
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (!width || !height) return false;
  const fontFromWidth = width / (TERMINAL_COLS * 0.65);
  const fontFromHeight = height / (TERMINAL_ROWS * 1.35);
  const size = Math.max(8, Math.min(20, Math.floor(Math.min(fontFromWidth, fontFromHeight))));
  if (term.options.fontSize !== size) {
    term.options.fontSize = size;
  }
  return true;
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
    cols: TERMINAL_COLS,
    rows: TERMINAL_ROWS,
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'D2Coding, "Nanum Gothic Coding", "Cascadia Code", Consolas, monospace',
    unicodeVersion: "11",
    convertEol: true,
    smoothScrollDuration: 100,
    theme: {
      background: "#07101d",
      foreground: "#d7e6ff",
    },
  });
  term.open(element);
  const refreshAfterLayout = () => {
    if (!scaleFontToContainer(term, element)) return false;
    // Re-measure cell dimensions so the canvas is sized correctly.
    try { term.resize(TERMINAL_COLS, TERMINAL_ROWS); } catch (_e) { /* ok */ }
    return true;
  };
  if (!refreshAfterLayout()) {
    // element not yet laid out — retry after paint
    window.requestAnimationFrame(() => {
      if (!refreshAfterLayout()) {
        window.setTimeout(refreshAfterLayout, 200);
      }
    });
  }
  try { term.reset(); } catch (_error) { /* xterm not ready */ }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${protocol}://${window.location.host}/v1/terminals/${encodeURIComponent(session.terminal_id)}/stream`
  );
  socket.binaryType = "arraybuffer";
  socket.addEventListener("message", (event) => writeTerminalBytes(term, event.data));
  bindTerminalIme(term, socket);
  let resizeTimer = 0;
  const sendFixedSize = () => {
    if (socket.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "resize", cols: TERMINAL_COLS, rows: TERMINAL_ROWS }));
  };
  const notifySize = () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      if (element.hidden) return;
      scaleFontToContainer(term, element);
    }, 150);
  };
  socket.addEventListener("open", () => {
    sendFixedSize();
  });
  socket.addEventListener("close", (event) => {
    const detail = event.reason ? ` ${event.code}: ${event.reason}` : ` code=${event.code}`;
    term.write(`\r\n\x1b[90m[session closed${detail}]\x1b[0m\r\n`);
  });
  window.addEventListener("resize", notifySize);
  const resizeObserver = new ResizeObserver(() => {
    if (element.hidden) return;
    notifySize();
  });
  resizeObserver.observe(element);
  surface = { element, term, socket, notifySize, resizeObserver };
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
  const provider = terminalProviderFor(coordinate, session);
  const raw = String(
    session.provider_session_id ||
      session.observer_session_ref ||
      session.session_id ||
      ""
  ).trim();
  const sessionProvider = String(session.provider || "").toUpperCase();
  if (sessionProvider && sessionProvider !== "AUTO" && sessionProvider !== provider) return "";
  if (/^grok-/i.test(raw) && provider !== "GROK") return "";
  if (/^claude-/i.test(raw) && provider !== "CLAUDE") return "";
  if (/^codex/i.test(raw) && provider !== "CODEX") return "";
  const stripped = raw.replace(
    /^(grok-acp:|grok-cli:|claude-code:|codex-app-server:|codex-app:|codex:|cli-terminal:)/i,
    ""
  );
  if (!stripped || /^(UNKNOWN|UNASSIGNED|NONE)$/i.test(stripped)) return "";
  if (/^[A-Z]+-CURRENT-/i.test(stripped)) return "";
  if (stripped.startsWith("UNIVERSE-")) return "";
  if (stripped.startsWith("term_")) return "";
  if (stripped.startsWith("pty:")) return "";
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
  state.dismissedTerminalIds = state.dismissedTerminalIds || {};
  delete state.dismissedTerminalIds[tab.terminal_id];
  state.supervisorTerminals = [
    ...(state.supervisorTerminals || []).filter((item) => item.terminal_id !== tab.terminal_id),
    tab,
  ];
  state.terminals = [...(state.terminals || []).filter((item) => item.terminal_id !== tab.terminal_id), tab];
  selectTerminalTab(tab.terminal_id);
  return tab;
}

function refitActiveTerminal() {
  const surface = (state.terminalSurfaces || {})[state.activeTerminalId];
  if (!surface) return;
  const run = () => {
    if (surface.element?.hidden) return false;
    const box = surface.element.getBoundingClientRect();
    if (box.width < 40 || box.height < 80) return false;
    scaleFontToContainer(surface.term, surface.element);
    // Resize forces xterm to re-measure cell dimensions and recreate the canvas.
    // This fixes the case where open() was called while the container had 0 dimensions.
    try { surface.term?.resize(TERMINAL_COLS, TERMINAL_ROWS); } catch (_e) { /* ok */ }
    surface.notifySize?.();
    return true;
  };
  run();
  window.requestAnimationFrame(() => {
    run();
    window.setTimeout(run, 220);
  });
}

async function stopTerminalSession(terminalId) {
  const id = String(terminalId || "").trim();
  if (!id) throw new Error("No live PTY session");
  await api("/v1/terminals/" + encodeURIComponent(id), { method: "DELETE" });
  if (state.dismissedTerminalIds) delete state.dismissedTerminalIds[id];
  state.supervisorTerminals = (state.supervisorTerminals || []).filter(
    (item) => item.terminal_id !== id
  );
  await closeTerminalTab(id);
}

async function closeTerminalTab(terminalId) {
  state.dismissedTerminalIds = state.dismissedTerminalIds || {};
  state.dismissedTerminalIds[terminalId] = true;
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
  const wantedId = String(session?.terminal_id || "").trim();
  if (wantedId) {
    const exact = (state.terminals || []).find((item) => item.terminal_id === wantedId);
    if (exact) {
      selectTerminalTab(exact.terminal_id);
      return true;
    }
    const live = (state.supervisorTerminals || []).find((item) => item.terminal_id === wantedId);
    if (live) {
      state.dismissedTerminalIds = state.dismissedTerminalIds || {};
      delete state.dismissedTerminalIds[wantedId];
      state.terminals = [...(state.terminals || []).filter((item) => item.terminal_id !== wantedId), live];
      selectTerminalTab(wantedId);
      return true;
    }
  }
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
    const incoming = payload.terminals || [];
    state.supervisorTerminals = incoming;
    const liveIds = new Set(incoming.map((item) => item.terminal_id));
    state.dismissedTerminalIds = state.dismissedTerminalIds || {};
    for (const id of Object.keys(state.dismissedTerminalIds)) {
      if (!liveIds.has(id)) delete state.dismissedTerminalIds[id];
    }
    const dismissed = state.dismissedTerminalIds;
    const previous = new Set((state.terminals || []).map((item) => item.terminal_id));
    const visible = incoming.filter((item) => !dismissed[item.terminal_id]);
    const opened = visible.filter((item) => !previous.has(item.terminal_id));
    state.terminals = visible;
    renderTerminalDock();
    if (typeof renderNodeModes === "function") renderNodeModes();
    if (!state.activeTerminalId && visible[0]) {
      selectTerminalTab(visible[0].terminal_id);
      return;
    }
    if (!isRemoteBrowser() && opened[0]) {
      selectTerminalTab(opened[0].terminal_id);
      return;
    }
    if (state.activeTerminalId) applyCliDockTitle(activeTerminalSession());
    else applyCliDockTitle(null);
  } catch (_error) {
    state.terminals = state.terminals || [];
  }
}
