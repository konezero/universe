function isRemoteBrowser() {
  return String(state.accessSurface || "LOCAL_BROWSER").toUpperCase() === "REMOTE_BROWSER";
}

function terminalLabel(session) {
  const project = String(session?.project_id || "session").trim();
  const mode = String(session?.mode || "").trim().toUpperCase();
  const provider = String(session?.provider || "").trim().toUpperCase();
  const coordinate = mode ? `${project} ${mode}` : project;
  return provider && provider !== "AUTO"
    ? `${coordinate} · ${provider}`
    : coordinate;
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

function terminalDockVisible(session) {
  const stateValue = String(session?.state || "").trim().toUpperCase();
  // Failed/closed PTYs remain in the supervisor as diagnostic history.  They
  // must not be restored into the interactive dock after a service restart:
  // doing so makes a stale provider --resume error look like a new-session
  // failure and blocks the user from opening a fresh tab.
  return ["LIVE", "STARTING", "CONNECTING"].includes(stateValue);
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

function decodeTerminalHistoryChunk(encoded) {
  const raw = window.atob(String(encoded || ""));
  return Uint8Array.from(raw, (value) => value.charCodeAt(0));
}

function writeTerminalChunk(term, data) {
  return new Promise((resolve) => {
    try {
      term.write(data, resolve);
    } catch (_error) {
      resolve();
    }
  });
}

async function loadOlderTerminalHistory(surface, session) {
  if (!surface || surface.historyLoading || surface.historyExhausted) return;
  surface.historyLoading = true;
  const buffer = surface.term?.buffer?.active;
  const distanceFromBottom = buffer
    ? Math.max(0, buffer.baseY - buffer.viewportY)
    : 0;
  try {
    const before = surface.historyBeforeCursor
      ? "&before_cursor=" + encodeURIComponent(surface.historyBeforeCursor)
      : "";
    const payload = await api(
      "/v1/terminals/" + encodeURIComponent(session.terminal_id) +
      "/history?limit=100" + before
    );
    const chunks = payload.chunks || [];
    if (!chunks.length) {
      surface.historyExhausted = true;
      return;
    }
    for (const chunk of chunks) {
      surface.historyChunks.set(
        Number(chunk.cursor),
        decodeTerminalHistoryChunk(chunk.data_base64)
      );
    }
    surface.historyBeforeCursor = payload.next_before_cursor;
    surface.historyExhausted = !payload.has_more;
    const ordered = [...surface.historyChunks.entries()]
      .sort((left, right) => left[0] - right[0])
      .map((entry) => entry[1]);
    const snapshot = decodeTerminalHistoryChunk(payload.screen_snapshot_base64);
    surface.rebuildingHistory = true;
    try { surface.term.reset(); } catch (_error) { /* xterm not ready */ }
    for (const chunk of ordered) await writeTerminalChunk(surface.term, chunk);
    // A WebSocket attachment already paints the current screen snapshot. Use the
    // API snapshot only when no immutable history chunk is available; appending it
    // after the same latest chunks would duplicate the visible tail.
    if (!ordered.length && snapshot.length) {
      await writeTerminalChunk(surface.term, snapshot);
    }
    for (const chunk of surface.pendingLiveChunks.splice(0)) {
      await writeTerminalChunk(surface.term, chunk);
    }
    const rebuilt = surface.term?.buffer?.active;
    if (rebuilt) {
      surface.term.scrollToLine(
        Math.max(0, rebuilt.baseY - distanceFromBottom)
      );
    }
  } finally {
    surface.rebuildingHistory = false;
    surface.historyLoading = false;
  }
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

function captureTerminalViewport(surface) {
  const buffer = surface?.term?.buffer?.active;
  if (!buffer) return;
  surface.savedViewport = {
    atBottom: buffer.viewportY >= buffer.baseY,
    line: buffer.viewportY,
  };
}

function restoreTerminalViewport(surface) {
  const term = surface?.term;
  const buffer = term?.buffer?.active;
  if (!term || !buffer) return;
  const saved = surface.savedViewport;
  try {
    if (!saved || saved.atBottom) term.scrollToBottom();
    else term.scrollToLine(Math.min(saved.line, buffer.baseY));
    term.refresh(0, Math.max(0, term.rows - 1));
  } catch (_error) { /* surface may still be measuring */ }
}

function selectTerminalTab(terminalId) {
  const session = (state.terminals || []).find((item) => item.terminal_id === terminalId);
  if (!session) return;
  const previousId = state.activeTerminalId;
  const switchingTabs = Boolean(previousId && previousId !== terminalId);
  if (switchingTabs) {
    captureTerminalViewport((state.terminalSurfaces || {})[previousId]);
  }
  state.activeTerminalId = terminalId;
  state.conversationSurface = "CLI";
  renderTerminalDock();
  if (typeof renderComposerState === "function") renderComposerState();
  ensureTerminalSurface(session);
  for (const [id, surface] of Object.entries(state.terminalSurfaces || {})) {
    if (!surface?.element) continue;
    surface.element.hidden = id !== terminalId;
    if (id === terminalId) {
      surface.restoreSavedViewport = switchingTabs;
      refitActiveTerminal();
    }
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

const TERMINAL_COLS = 120;
const TERMINAL_ROWS = 40;

function bindTerminalIme(term, socket) {
  const textarea = term.textarea || term.element?.querySelector(".xterm-helper-textarea");
  let composing = false;
  let lastComposed = null;
  if (typeof term.attachCustomKeyEventHandler === "function") {
    term.attachCustomKeyEventHandler((event) => {
      if (event.isComposing) return false;
      return true;
    });
  }
  if (textarea) {
    textarea.setAttribute("autocapitalize", "off");
    textarea.setAttribute("autocomplete", "off");
    textarea.setAttribute("spellcheck", "false");
    textarea.addEventListener("compositionstart", () => {
      composing = true;
      lastComposed = null;
    }, true);
    textarea.addEventListener("compositionend", (event) => {
      composing = false;
      const composed = event.data || "";
      lastComposed = composed || null;
      if (composed) sendPtyText(socket, composed);
      window.setTimeout(() => {
        lastComposed = null;
        try { textarea.value = ""; } catch (_error) { /* ignore */ }
      }, 0);
    }, true);
  }
  term.onData((data) => {
    if (composing) return;
    // Skip only if xterm echoes the exact composed char we already sent
    if (lastComposed !== null && data === lastComposed) return;
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

function fitTerminalToContainer(term, element, fitAddon) {
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (!width || !height) return false;
  if (fitAddon && typeof fitAddon.fit === "function") {
    try {
      const proposed = typeof fitAddon.proposeDimensions === "function"
        ? fitAddon.proposeDimensions()
        : null;
      if (proposed && proposed.cols === term.cols && proposed.rows === term.rows) {
        return true;
      }
      fitAddon.fit();
      return Boolean(term.cols && term.rows);
    } catch (_error) {
      // A just-mounted pane may not be measurable yet; use the fallback below.
    }
  }
  if (!scaleFontToContainer(term, element)) return false;
  try { term.resize(TERMINAL_COLS, TERMINAL_ROWS); } catch (_error) { /* ok */ }
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
  const fitAddon = (
    typeof window.FitAddon?.FitAddon === "function"
      ? new window.FitAddon.FitAddon()
      : null
  );
  const term = new Terminal({
    cols: TERMINAL_COLS,
    rows: TERMINAL_ROWS,
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'Consolas, "Cascadia Code", D2Coding, "Nanum Gothic Coding", monospace',
    unicodeVersion: "11",
    convertEol: true,
    scrollback: 5000,
    smoothScrollDuration: 100,
    theme: {
      background: "#07101d",
      foreground: "#d7e6ff",
    },
  });
  term.open(element);
  if (fitAddon) {
    try { term.loadAddon(fitAddon); } catch (_error) { /* optional addon */ }
  }
  const refreshAfterLayout = () => {
    return fitTerminalToContainer(term, element, fitAddon);
  };
  // Keep the initial PTY geometry while its bounded replay is painted. A
  // replay contains cursor-positioned TUI bytes from the old geometry; fitting
  // first would wrap those bytes into vertical fragments. Fit after the
  // initial stream quiets so the provider receives the resize and can redraw.
  let initialLayoutPending = true;
  let initialLayoutTimer = 0;
  const scheduleInitialLayout = (delay = 240) => {
    if (!initialLayoutPending) return;
    window.clearTimeout(initialLayoutTimer);
    initialLayoutTimer = window.setTimeout(() => {
      if (!initialLayoutPending || element.hidden) return;
      if (!refreshAfterLayout()) {
        scheduleInitialLayout(200);
        return;
      }
      initialLayoutPending = false;
      if (surface) surface.initialLayoutPending = false;
      sendCurrentSize();
      restoreTerminalViewport(surface);
      if (surface) surface.restoreSavedViewport = false;
    }, delay);
  };
  try { term.reset(); } catch (_error) { /* xterm not ready */ }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${protocol}://${window.location.host}/v1/terminals/${encodeURIComponent(session.terminal_id)}/stream`
  );
  socket.binaryType = "arraybuffer";
  socket.addEventListener("message", (event) => {
    if (surface?.rebuildingHistory) {
      const pending = event.data instanceof ArrayBuffer
        ? new Uint8Array(event.data.slice(0))
        : event.data;
      surface.pendingLiveChunks.push(pending);
    } else {
      writeTerminalBytes(term, event.data);
    }
    scheduleInitialLayout(240);
  });
  bindTerminalIme(term, socket);
  let resizeTimer = 0;
  const sendCurrentSize = () => {
    if (socket.readyState !== WebSocket.OPEN) return false;
    const cols = Math.max(40, Number(term.cols) || TERMINAL_COLS);
    const rows = Math.max(20, Number(term.rows) || TERMINAL_ROWS);
    const sizeKey = `${cols}x${rows}`;
    if (surface?.lastSentSizeKey === sizeKey) return false;
    socket.send(JSON.stringify({
      type: "resize",
      cols,
      rows,
    }));
    if (surface) surface.lastSentSizeKey = sizeKey;
    return true;
  };
  const notifySize = (delay = 150) => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      if (element.hidden) return;
      if (initialLayoutPending) {
        scheduleInitialLayout(240);
        return;
      }
      const restoringTab = Boolean(surface?.restoreSavedViewport);
      if (!restoringTab) captureTerminalViewport(surface);
      const previousCols = term.cols;
      const previousRows = term.rows;
      fitTerminalToContainer(term, element, fitAddon);
      const geometryChanged = term.cols !== previousCols || term.rows !== previousRows;
      sendCurrentSize();
      if (restoringTab || geometryChanged) restoreTerminalViewport(surface);
      else if (surface) surface.savedViewport = null;
      if (surface) surface.restoreSavedViewport = false;
    }, delay);
  };
  socket.addEventListener("open", () => {
    scheduleInitialLayout(360);
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
  surface = {
    element,
    term,
    socket,
    notifySize,
    resizeObserver,
    fitAddon,
    initialLayoutPending: true,
    scheduleInitialLayout,
    lastSentSizeKey: null,
    savedViewport: null,
    restoreSavedViewport: false,
    historyBeforeCursor: null,
    historyChunks: new Map(),
    historyLoading: false,
    historyExhausted: false,
    rebuildingHistory: false,
    pendingLiveChunks: [],
  };
  term.onScroll((viewportY) => {
    if (viewportY > 2 || surface.rebuildingHistory) return;
    loadOlderTerminalHistory(surface, session).catch((error) =>
      toast(error.message, true)
    );
  });
  state.terminalSurfaces[session.terminal_id] = surface;
  return surface;
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

function terminalProviderFor(_coordinate, session) {
  const selectedProvider = String(_coordinate?.provider || "").toUpperCase();
  if (["GROK", "CODEX", "CLAUDE"].includes(selectedProvider)) {
    return selectedProvider;
  }
  const boundProvider = String(
    session?.provider || session?.current_provider || ""
  ).toUpperCase();
  if (["GROK", "CODEX", "CLAUDE"].includes(boundProvider)) {
    return boundProvider;
  }
  const fromObserver = observerProvider(session);
  if (fromObserver) return fromObserver;
  throw new Error("Choose a provider for this session");
}

function terminalSupervisorSessionId(session) {
  const candidates = [session?.universe_session_id, session?.session_id]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  if (!candidates.length) return "";
  const supervised = (state.supervisorSessions || []).find(
    (item) =>
      candidates.includes(
        String(item?.universe_session_id || item?.session_id || "").trim()
      )
  );
  return supervised
    ? String(supervised.universe_session_id || supervised.session_id || "").trim()
    : "";
}

function terminalResumeRef(coordinate, session) {
  if (!session) return "";
  const provider = terminalProviderFor(coordinate, session);
  const raw = String(session.provider_session_id || "").trim();
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
      model_ref: String(coordinate?.modelRef || coordinate?.model_ref || "").trim(),
      effort: String(coordinate?.effort || "AUTO").toUpperCase(),
      supervisor_session_id: terminalSupervisorSessionId(session),
      pty_binding_anchor_ref: String(session?.session_anchor_ref || "").trim(),
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
  if (surface.element?.hidden) return;
  const box = surface.element.getBoundingClientRect();
  if (box.width < 40 || box.height < 80) return;
  if (surface.initialLayoutPending) {
    surface.scheduleInitialLayout?.(240);
    return;
  }
  surface.notifySize?.(0);
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
    state.conversationSurface = "CHAT";
    applyCliDockTitle(null);
    renderTerminalDock();
  }
  if (typeof renderComposerState === "function") renderComposerState();
  if (typeof renderRoomMessages === "function") renderRoomMessages();
}

function focusTerminalForSession(coordinate, session) {
  const wantedId = String(session?.terminal_id || "").trim();
  if (wantedId) {
    const exact = (state.terminals || []).find((item) => item.terminal_id === wantedId);
    if (exact) {
      selectTerminalTab(exact.terminal_id);
      return true;
    }
    const live = (state.supervisorTerminals || []).find(
      (item) =>
        item.terminal_id === wantedId &&
        terminalDockVisible(item)
    );
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
  const supervisorSessionId = String(
    session?.session_id || session?.universe_session_id || ""
  ).trim();
  const match = (state.terminals || []).find(
    (item) =>
      item.project_id === projectId &&
      item.mode === mode &&
      (!provider || provider === "AUTO" || item.provider === provider) &&
      supervisorSessionId &&
      String(item.supervisor_session_id || "") === supervisorSessionId
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
    const visible = incoming.filter(
      (item) => !dismissed[item.terminal_id] && terminalDockVisible(item)
    );
    if (
      state.activeTerminalId &&
      !visible.some((item) => item.terminal_id === state.activeTerminalId)
    ) {
      state.activeTerminalId = null;
      state.conversationSurface = "CHAT";
    }
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
