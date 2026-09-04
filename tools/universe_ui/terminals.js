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
    elements.conversationTitle.textContent = "Terminal";
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

function terminalHostForSession(session) {
  const hostRef = String(
    session?.host_session_ref || session?.reconnection_host_id || ""
  ).trim();
  const anchorRef = String(
    session?.session_anchor_ref || session?.active_session_anchor_ref || ""
  ).trim();
  const hosts = state.supervisorHosts || [];
  if (hostRef) {
    const exact = hosts.find(
      (host) => String(host.host_session_ref || host.host_id || "").trim() === hostRef
    );
    if (exact) return exact;
  }
  return hosts.find(
    (host) => String(host.session_anchor_ref || host.anchor_ref || "").trim() === anchorRef
  ) || null;
}

function terminalDockVisible(session) {
  const host = terminalHostForSession(session);
  return Boolean(
    host &&
      String(host.runtime_state || host.state || "").trim().toUpperCase() === "LIVE" &&
      host.reconnect_eligible === true &&
      ["CURRENT", "COMPATIBLE_OLD"].includes(
        String(host.compatibility || "").trim().toUpperCase()
      )
  );
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

function cloneTerminalChunk(data) {
  if (data instanceof ArrayBuffer) return new Uint8Array(data.slice(0));
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength));
  }
  return new TextEncoder().encode(String(data || ""));
}


const LIGHT_BG_MIN_CONTRAST = 4.5;
const DARK_BG_MIN_CONTRAST = 3;
const TERMINAL_SCROLLBAR_GUTTER_PX = 7;
const IME_STALE_COMPOSITION_MS = 10000;
const RETAINED_LIVE_MAX_BYTES = 2 * 1024 * 1024;
const HANGUL_PREEDIT_PATTERN = /[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-힣]/;

function channelLuminance(value) {
  const channel = value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  return channel;
}

function hexBackgroundLuminance(hex) {
  const raw = String(hex || "").trim().replace(/^#/, "");
  if (raw.length !== 6 || /[^0-9a-fA-F]/.test(raw)) return 0;
  const r = parseInt(raw.slice(0, 2), 16) / 255;
  const g = parseInt(raw.slice(2, 4), 16) / 255;
  const b = parseInt(raw.slice(4, 6), 16) / 255;
  return 0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b);
}

function terminalMinimumContrastRatio(background) {
  return hexBackgroundLuminance(background) >= 0.5
    ? LIGHT_BG_MIN_CONTRAST
    : DARK_BG_MIN_CONTRAST;
}

function releaseTerminalReplayGuard(surface) {
  const finish = () => {
    if (!surface.replayDepth) surface.replaying = false;
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(finish);
  else finish();
}

async function withTerminalReplayGuard(surface, writer) {
  surface.replayDepth = (surface.replayDepth || 0) + 1;
  surface.replaying = true;
  try {
    return await writer();
  } finally {
    surface.replayDepth = Math.max(0, (surface.replayDepth || 1) - 1);
    if (!surface.replayDepth) releaseTerminalReplayGuard(surface);
  }
}

function installGuardedLinkProviderRegistration(term) {
  if (!term || typeof term.registerLinkProvider !== "function") return;
  const register = term.registerLinkProvider.bind(term);
  let providerCount = 0;
  term.registerLinkProvider = (provider) => {
    providerCount += 1;
    const label = "provider-" + providerCount;
    return register({
      provideLinks(bufferLineNumber, callback) {
        let invoked = false;
        const tracked = (links) => {
          invoked = true;
          callback(links);
        };
        try {
          provider.provideLinks(bufferLineNumber, tracked);
        } catch (_error) {
          if (!invoked) callback(undefined);
        }
      },
    });
  };
}

function attachTerminalMouseWheelHandler(term) {
  if (!term || typeof term.attachCustomWheelEventHandler !== "function") return;
  term.attachCustomWheelEventHandler(() => {
    const mode = term.modes && term.modes.mouseTrackingMode;
    if (mode && mode !== "none") return false;
    return true;
  });
}

function disposeTerminalWebgl(surface) {
  const addon = surface?.webglAddon;
  if (!addon) return;
  try { addon.dispose(); } catch (_error) { /* already gone */ }
  surface.webglAddon = null;
}

function attachTerminalWebgl(surface) {
  if (!surface?.term || surface.webglAddon) return Boolean(surface?.webglAddon);
  if (surface.webglFailedSinceRecovery) return false;
  if (typeof window.WebglAddon?.WebglAddon !== "function") return false;
  let webgl = null;
  try {
    webgl = new window.WebglAddon.WebglAddon();
    webgl.onContextLoss(() => {
      console.warn("[terminal] WebGL context lost — falling back to DOM renderer");
      disposeTerminalWebgl(surface);
      surface.webglFailedSinceRecovery = false;
      try { surface.notifySize?.(0); } catch (_error) { /* pane may be gone */ }
    });
    surface.term.loadAddon(webgl);
    if (!surface.element?.querySelector("canvas")) {
      try { webgl.dispose(); } catch (_error) { /* nothing to undo */ }
      surface.webglFailedSinceRecovery = true;
      surface.webglAddon = null;
      console.warn("[terminal] WebGL attach produced no canvas — latching DOM renderer until next successful refit");
      return false;
    }
    surface.webglAddon = webgl;
    surface.webglFailedSinceRecovery = false;
    return true;
  } catch (_error) {
    try { webgl?.dispose(); } catch (_disposeError) { /* stay on DOM */ }
    surface.webglAddon = null;
    surface.webglFailedSinceRecovery = true;
    console.warn("[terminal] WebGL unavailable — latching DOM renderer until next successful refit:", _error);
    return false;
  }
}

function retryTerminalWebglAfterFit(surface, fitted) {
  if (!fitted || !surface) return;
  if (surface.webglAddon) return;
  surface.webglFailedSinceRecovery = false;
  attachTerminalWebgl(surface);
}


function retainLiveChunk(surface, live, displayed) {
  surface.retainedLiveChunks.push({ data: live, displayed });
  let total = 0;
  for (const entry of surface.retainedLiveChunks) total += entry.data.length;
  if (total <= RETAINED_LIVE_MAX_BYTES) return;
  const snapshot = concatTerminalChunks(surface.retainedLiveChunks.map((entry) => entry.data));
  const kept = snapshot.length > RETAINED_LIVE_MAX_BYTES
    ? snapshot.slice(snapshot.length - RETAINED_LIVE_MAX_BYTES)
    : snapshot;
  surface.retainedLiveChunks = [{ data: kept, displayed: false, overflowSnapshot: true }];
}

function concatTerminalChunks(chunks) {
  const total = chunks.reduce((size, chunk) => size + chunk.length, 0);
  const joined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.length;
  }
  return joined;
}

function trimHistoryCoveredLiveTail(historyChunks, retainedLiveChunks) {
  if (!historyChunks.length || !retainedLiveChunks.length) return;
  const history = concatTerminalChunks(historyChunks);
  const live = concatTerminalChunks(retainedLiveChunks.map((entry) => entry.data));
  if (!history.length || !live.length) return;
  const separator = 256;
  const liveLength = live.length;
  const total = liveLength + 1 + history.length;
  const prefix = new Uint32Array(total);
  const valueAt = (index) => {
    if (index < liveLength) return live[index];
    if (index === liveLength) return separator;
    return history[index - liveLength - 1];
  };
  for (let index = 1; index < total; index += 1) {
    let matched = prefix[index - 1];
    const value = valueAt(index);
    while (matched > 0 && value !== valueAt(matched)) {
      matched = prefix[matched - 1];
    }
    if (value === valueAt(matched)) matched += 1;
    prefix[index] = matched;
  }
  let covered = Math.min(liveLength, prefix[total - 1]);
  while (covered > 0 && retainedLiveChunks.length) {
    const entry = retainedLiveChunks[0];
    if (covered >= entry.data.length) {
      covered -= entry.data.length;
      retainedLiveChunks.shift();
    } else {
      entry.data = entry.data.slice(covered);
      covered = 0;
    }
  }
}

async function writeUndisplayedLiveTail(surface) {
  let index = 0;
  while (index < surface.retainedLiveChunks.length) {
    const entry = surface.retainedLiveChunks[index];
    if (!entry.displayed) {
      await writeTerminalChunk(surface.term, entry.data);
      entry.displayed = true;
    }
    index += 1;
  }
}

async function loadOlderTerminalHistory(surface, session) {
  if (!surface || surface.historyLoading || surface.historyExhausted) return;
  surface.historyLoading = true;
  surface.rebuildingHistory = true;
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
    trimHistoryCoveredLiveTail(ordered, surface.retainedLiveChunks);
    for (const entry of surface.retainedLiveChunks) entry.displayed = false;
    const snapshot = decodeTerminalHistoryChunk(payload.screen_snapshot_base64);
    await withTerminalReplayGuard(surface, async () => {
      try { surface.term.reset(); } catch (_error) { /* xterm not ready */ }
      for (const chunk of ordered) await writeTerminalChunk(surface.term, chunk);
      // A WebSocket attachment already paints the current screen snapshot. Use the
      // API snapshot only when no immutable history chunk is available; appending it
      // after the same latest chunks would duplicate the visible tail.
      if (!ordered.length && snapshot.length) {
        await writeTerminalChunk(surface.term, snapshot);
      }
      await writeUndisplayedLiveTail(surface);
    });
    surface.historyInitialized = true;
    const rebuilt = surface.term?.buffer?.active;
    if (rebuilt) {
      surface.term.scrollToLine(
        Math.max(0, rebuilt.baseY - distanceFromBottom)
      );
    }
  } finally {
    await withTerminalReplayGuard(surface, async () => {
      await writeUndisplayedLiveTail(surface);
    });
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
    const cliName = String(session.provider_cli || "").toUpperCase();
    if (cliName) {
      const chip = node("span", "terminal-status-chip", cliName);
      chip.classList.add(session.provider_cli_alive === false ? "is-dead" : "is-live");
      chip.title = String(session.provider_cli_process || cliName);
      tab.append(chip);
    }
    const delivery = String(session.prompt_delivery || "").toLowerCase();
    if (["delivered", "stalled", "pending", "blocked"].includes(delivery)) {
      tab.append(node("span", "terminal-delivery-chip is-" + delivery, delivery));
    }
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
  if (sessions.length > 1) {
    const gridBtn = node("button", "terminal-grid-toggle");
    gridBtn.type = "button";
    gridBtn.title = "Show all sessions in a grid";
    gridBtn.textContent = state.terminalGrid ? "▦ grid" : "▤ single";
    gridBtn.classList.toggle("is-active", Boolean(state.terminalGrid));
    gridBtn.addEventListener("click", () => setTerminalGrid(!state.terminalGrid));
    tabs.append(gridBtn);
  }
  applyTerminalGridLayout();
}

function setTerminalGrid(on) {
  state.terminalGrid = Boolean(on);
  renderTerminalDock();
}

function applyTerminalGridLayout() {
  const stage = elements.terminalStage;
  if (!stage) return;
  const grid = Boolean(state.terminalGrid) && (state.terminals || []).length > 1;
  stage.classList.toggle("terminal-grid", grid);
  for (const [id, surface] of Object.entries(state.terminalSurfaces || {})) {
    if (!surface?.element) continue;
    surface.element.hidden = grid ? false : id !== state.activeTerminalId;
  }
  if (grid && typeof refitAllTerminals === "function") {
    setTimeout(() => refitAllTerminals(), 60);
  } else {
    setTimeout(() => refitActiveTerminal(), 60);
  }
}

function refitAllTerminals() {
  for (const surface of Object.values(state.terminalSurfaces || {})) {
    if (!surface?.element || surface.element.hidden) continue;
    const box = surface.element.getBoundingClientRect();
    if (box.width < 40 || box.height < 60) continue;
    try {
      surface.notifySize?.(0);
    } catch (_error) {
      /* surface may still be measuring */
    }
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
  const grid = Boolean(state.terminalGrid) && (state.terminals || []).length > 1;
  for (const [id, surface] of Object.entries(state.terminalSurfaces || {})) {
    if (!surface?.element) continue;
    surface.element.hidden = grid ? false : id !== terminalId;
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
  if (/\r|\n/.test(text)) watchPromptDelivery(state.activeTerminalId);
}

function watchPromptDelivery(terminalId) {
  const id = String(terminalId || "").trim();
  if (!id) return;
  const started = Date.now();
  const tick = () => {
    api("/v1/terminals").then((payload) => {
      const row = (payload.terminals || []).find((item) => item.terminal_id === id);
      if (!row) return;
      const current = (state.terminals || []).find((item) => item.terminal_id === id);
      if (current) {
        current.prompt_delivery = row.prompt_delivery;
        current.provider_cli = row.provider_cli;
        current.provider_cli_alive = row.provider_cli_alive;
        current.provider_cli_process = row.provider_cli_process;
      }
      renderTerminalDock();
      const delivery = String(row.prompt_delivery || "");
      if (["delivered", "stalled", "blocked", "stale"].includes(delivery)) return;
      if (Date.now() - started < 30000) window.setTimeout(tick, 50);
    }).catch(() => {
      if (Date.now() - started < 30000) window.setTimeout(tick, 50);
    });
  };
  window.setTimeout(tick, 50);
}

// The grid width is fixed at 120 columns; the font is scaled so those columns
// fill the pane, and the row count is whatever fits vertically at that font.
// 120x40 is the fallback when the pane cannot be measured yet.
const TERMINAL_COLS = 120;
const TERMINAL_ROWS = 40;
const TERMINAL_MIN_ROWS = 20;
const TERMINAL_MAX_ROWS = 60;
const TERMINAL_MIN_FONT = 6;
const TERMINAL_MAX_FONT = 16;

const IME_DEBUG = (() => {
  try { return new URLSearchParams(window.location.search).has("imedebug"); }
  catch (_e) { return false; }
})();

function renderImeDebugBox() {
  if (!IME_DEBUG) return;
  const trace = window.__imeTrace || [];
  let box = document.getElementById("ime-debug-box");
  if (!box) {
    box = document.createElement("div");
    box.id = "ime-debug-box";
    box.style.cssText =
      "position:fixed;right:8px;bottom:8px;width:min(360px,92vw);max-height:46vh;" +
      "z-index:99999;background:#0b1416ee;color:#d7e6ff;border:1px solid #2a4a52;" +
      "border-radius:8px;font:11px/1.45 ui-monospace,Menlo,monospace;display:flex;" +
      "flex-direction:column;box-shadow:0 8px 30px rgba(0,0,0,.5)";
    const bar = document.createElement("div");
    bar.style.cssText =
      "display:flex;gap:6px;align-items:center;padding:6px 8px;border-bottom:1px solid #2a4a52";
    bar.innerHTML = "<b style='flex:1'>IME trace</b>";
    const mk = (label, fn) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.style.cssText =
        "font:11px ui-monospace,monospace;background:#17323a;color:#d7e6ff;" +
        "border:1px solid #2a4a52;border-radius:5px;padding:2px 7px;cursor:pointer";
      b.addEventListener("click", (e) => { e.preventDefault(); fn(); });
      return b;
    };
    bar.append(
      mk("copy", () => {
        const text = JSON.stringify(window.__imeTrace || []);
        (navigator.clipboard?.writeText(text) || Promise.reject()).then(
          () => { bar.querySelector("b").textContent = "IME trace — copied ✓"; },
          () => {
            const ta = document.createElement("textarea");
            ta.value = text; document.body.append(ta); ta.select();
            try { document.execCommand("copy"); } catch (_e) {}
            ta.remove();
            bar.querySelector("b").textContent = "IME trace — copied ✓";
          }
        );
      }),
      mk("clear", () => { (window.__imeTrace || []).length = 0; renderImeDebugBox(); }),
      mk("×", () => box.remove()),
    );
    const body = document.createElement("pre");
    body.id = "ime-debug-body";
    body.style.cssText = "margin:0;padding:8px;overflow:auto;white-space:pre-wrap;word-break:break-all";
    box.append(bar, body);
    document.body.append(box);
  }
  const body = box.querySelector("#ime-debug-body");
  if (body) {
    body.textContent = trace
      .slice(-40)
      .map((e) => {
        const { t, tag, ...rest } = e;
        return `${String(t).padStart(6)} ${tag}  ${JSON.stringify(rest)}`;
      })
      .join("\n");
    body.scrollTop = body.scrollHeight;
  }
}

function bindTerminalIme(term, socket, getSurface) {
  const textarea = term.textarea || term.element?.querySelector(".xterm-helper-textarea");
  let composing = false;
  let lastComposeAt = 0;
  let lastCompositionAt = 0;
  let hangulPreedit = false;
  // IME diagnostic ring buffer. Read after reproducing:
  //   copy(JSON.stringify(window.__imeTrace))
  // or open the on-screen box with ?imedebug=1 in the URL.
  // Zero cost when nobody looks; safe to leave in.
  const imeTrace = (window.__imeTrace = window.__imeTrace || []);
  const imeLog = (tag, extra) => {
    imeTrace.push({
      t: Math.round(performance.now()),
      tag,
      composing,
      isComposingNow: (() => { try { return isComposingNow(); } catch (_e) { return "?"; } })(),
      ...extra,
    });
    if (imeTrace.length > 200) imeTrace.shift();
    renderImeDebugBox();
  };
  // Keys that end a marked IME syllable (and, on Windows, must reach xterm
  // even while an IME reports keyCode 229 for them).
  const IME_BOUNDARY_KEYS = new Set([
    "Enter", "Tab", "Escape",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown",
  ]);
  let composeWatchdog = 0;
  // macOS remote-browser Hangul does NOT fire composition events: it delivers
  // the first jamo as `input/insertText` then refines the syllable through
  // `input/insertReplacementText` in the helper textarea, and starts the next
  // syllable with a fresh `insertText`. We track that marked syllable and
  // commit it on a boundary (next syllable, Enter, blur, quiet timeout).
  let imeMarked = "";
  let lastKey229At = 0;
  let imeInputAt = 0;
  let imeFlushTimer = 0;
  const flushImeMarked = () => {
    window.clearTimeout(imeFlushTimer);
    const text = imeMarked;
    imeMarked = "";
    if (text) {
      imeInputAt = Date.now();
      imeLog("ime-flush->pty", { text });
      sendPtyText(socket, text);
    }
  };
  const endCompose = () => {
    composing = false;
    hangulPreedit = false;
    window.clearTimeout(composeWatchdog);
  };
  const markComposeActivity = (event) => {
    lastComposeAt = Date.now();
    const data = event && typeof event.data === "string" ? event.data : "";
    if (data) hangulPreedit = HANGUL_PREEDIT_PATTERN.test(data);
  };
  const isComposingNow = () => {
    if (!composing) return false;
    if (Date.now() - lastComposeAt > IME_STALE_COMPOSITION_MS) {
      endCompose();
      return false;
    }
    return true;
  };
  if (typeof term.attachCustomKeyEventHandler === "function") {
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown") return true;
      imeLog("keydown", { key: event.key, code: event.keyCode, isComposing: event.isComposing });
      if (event.keyCode === 229) lastKey229At = Date.now();

      // A line-edit / submit key ends any macOS marked syllable first.
      if (imeMarked && IME_BOUNDARY_KEYS.has(event.key)) {
        flushImeMarked();
      }
      // Backspace while a syllable is marked edits the marked text in the
      // browser — it must not reach the PTY as a destructive \x7f.
      if (imeMarked && event.key === "Backspace") {
        return false;
      }

      // Self-heal a genuinely wedged composition — but only after a quiet gap.
      if (
        isComposingNow()
        && !event.isComposing
        && event.keyCode !== 229
        && Date.now() - lastComposeAt > 400
      ) {
        endCompose();
      }
      if (event.isComposing || event.keyCode === 229) {
        // Never preventDefault an IME keydown: on macOS that cancels
        // marked-text composition and degrades it to per-jamo insertText.
        // xterm skips keyCode 229 on its own, so `true` is safe.
        return true;
      }
      return true;
    });
  }
  if (textarea) {
    textarea.setAttribute("autocapitalize", "off");
    textarea.setAttribute("autocomplete", "off");
    textarea.setAttribute("spellcheck", "false");
    textarea.addEventListener("compositionstart", (event) => {
      composing = true;
      hangulPreedit = false;
      lastCompositionAt = Date.now();
      markComposeActivity(event);
      imeLog("compositionstart", { data: event.data });
      // No real composition outlives this; if compositionend is ever missed
      // (IME quirks, focus races) the flag still clears on its own.
      window.clearTimeout(composeWatchdog);
      composeWatchdog = window.setTimeout(endCompose, IME_STALE_COMPOSITION_MS);
    }, true);
    textarea.addEventListener("compositionupdate", (event) => {
      composing = true;
      lastCompositionAt = Date.now();
      markComposeActivity(event);
      imeLog("compositionupdate", { data: event.data });
    }, true);
    textarea.addEventListener("compositionend", (event) => {
      imeLog("compositionend", { data: event.data });
      lastCompositionAt = Date.now();
      // Do NOT send here. xterm's own composition handler emits the committed
      // text through onData right after this; sending it again produced
      // 한한글글 (and the NFC/NFD-sensitive dedup could not catch it).
      endCompose();
    }, true);
    // Fallback for the degraded macOS path where the IME fires NO composition
    // events and refines the syllable through input/insertReplacementText.
    // Only engages when composition events are demonstrably absent.
    textarea.addEventListener("input", (event) => {
      if (!(event instanceof InputEvent)) return;
      const it = event.inputType;
      imeLog("input", { inputType: it, data: event.data });
      if (composing || Date.now() - lastCompositionAt < 1500) return;

      if (it === "insertReplacementText") {
        imeMarked = event.data || imeMarked;
        imeInputAt = Date.now();
        try { textarea.value = imeMarked; } catch (_e) { /* readonly race */ }
        window.clearTimeout(imeFlushTimer);
        imeFlushTimer = window.setTimeout(flushImeMarked, 700);
        return;
      }
      if (it === "insertText" && event.data && Date.now() - lastKey229At < 600) {
        // A fresh jamo after a 229 key: the previous marked syllable is final.
        if (imeMarked && event.data !== imeMarked) {
          imeLog("ime-commit->pty", { text: imeMarked });
          sendPtyText(socket, imeMarked);
        }
        imeMarked = event.data === imeMarked ? "" : event.data;
        imeInputAt = Date.now();
        try { textarea.value = imeMarked; } catch (_e) { /* readonly race */ }
        if (imeMarked) {
          window.clearTimeout(imeFlushTimer);
          imeFlushTimer = window.setTimeout(flushImeMarked, 700);
        }
        return;
      }
      if (imeMarked) flushImeMarked();
    }, true);
    // A composition abandoned by a blur/refit would otherwise wedge `composing`.
    textarea.addEventListener("blur", () => { endCompose(); flushImeMarked(); }, true);
  }
  term.onData((data) => {
    // xterm auto-answers DA1/CPR/OSC during a replay; those bytes
    // must not reach the PTY as stray input.
    if (getSurface?.()?.replaying) return;
    const isControlData =
      !data || data === "\x7f" || data.charCodeAt(0) < 0x20;
    // Degraded macOS path only: the input listener owns the send while a
    // syllable is marked or a jamo key just fired.
    if (
      !isControlData
      && Date.now() - lastCompositionAt > 1500
      && (imeMarked
        || Date.now() - imeInputAt < 400
        || Date.now() - lastKey229At < 400)
    ) {
      imeLog("onData suppressed(ime)", { data });
      return;
    }
    if (isComposingNow() && !isControlData) {
      imeLog("onData BLOCKED", { data });
      return;
    }
    imeLog("onData->pty", { data });
    sendPtyText(socket, data);
  });
}

function scaleFontToContainer(term, element) {
  const width = element.clientWidth;
  if (!width) return false;
  // Char cell width ~= 0.6 * fontSize; size the font so 120 columns span the pane.
  const size = Math.max(
    TERMINAL_MIN_FONT,
    Math.min(TERMINAL_MAX_FONT, Math.floor(width / (TERMINAL_COLS * 0.6)))
  );
  if (term.options.fontSize !== size) term.options.fontSize = size;
  return true;
}

function fitTerminalToContainer(term, element, fitAddon) {
  const width = element.clientWidth;
  const height = element.clientHeight;
  if (!width || !height) return false;
  if (fitAddon && typeof fitAddon.proposeDimensions === "function") {
    try {
      // The grid is a fixed 120 columns. Measure how many columns a reference
      // font yields, then (char width scales ~linearly with font size) estimate
      // the font that fits 120 columns — floor, never round up, or the last
      // column spills past the pane edge and the TUI looks clipped.
      const ref = TERMINAL_MAX_FONT;
      if (term.options.fontSize !== ref) term.options.fontSize = ref;
      const at = fitAddon.proposeDimensions();
      let font = ref;
      if (at && at.cols) {
        font = Math.floor((ref * at.cols) / TERMINAL_COLS);
        font = Math.max(TERMINAL_MIN_FONT, Math.min(TERMINAL_MAX_FONT, font));
      }
      if (term.options.fontSize !== font) term.options.fontSize = font;
      let after = fitAddon.proposeDimensions();
      // Char metrics are not perfectly linear (sub-pixel rounding); step the
      // font down until 120 columns actually fit.
      while (
        after && after.cols && after.cols < TERMINAL_COLS &&
        font > TERMINAL_MIN_FONT
      ) {
        font -= 1;
        term.options.fontSize = font;
        after = fitAddon.proposeDimensions();
      }
      const rows = Math.max(
        TERMINAL_MIN_ROWS,
        Math.min(TERMINAL_MAX_ROWS, (after && after.rows) || TERMINAL_ROWS)
      );
      if (term.cols !== TERMINAL_COLS || term.rows !== rows) {
        term.resize(TERMINAL_COLS, rows);
      }
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
  const termTheme = {
    background: "#07101d",
    foreground: "#d7e6ff",
  };
  const term = new Terminal({
    cols: TERMINAL_COLS,
    rows: TERMINAL_ROWS,
    allowProposedApi: true,
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'Consolas, "Cascadia Code", D2Coding, "Nanum Gothic Coding", monospace',
    unicodeVersion: "11",
    convertEol: true,
    scrollback: 5000,
    smoothScrollDuration: 0,
    scrollSensitivity: 1.15,
    fastScrollSensitivity: 5,
    macOptionIsMeta: false,
    minimumContrastRatio: terminalMinimumContrastRatio(termTheme.background),
    scrollbar: { width: TERMINAL_SCROLLBAR_GUTTER_PX },
    vtExtensions: { kittyKeyboard: true },
    theme: termTheme,
  });
  term.open(element);
  installGuardedLinkProviderRegistration(term);
  attachTerminalMouseWheelHandler(term);
  if (fitAddon) {
    try { term.loadAddon(fitAddon); } catch (_error) { /* optional addon */ }
  }
  // A click in the pane must land keyboard focus in xterm's hidden textarea.
  // When the running TUI turns on mouse tracking, xterm forwards the click as
  // a mouse report and does NOT move focus on its own — so pointerdown here
  // (capture, before xterm consumes it) keeps typing working after any click.
  element.addEventListener(
    "pointerdown",
    () => { try { term.focus(); } catch (_error) { /* pane not ready */ } },
    true
  );
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
      const fitted = refreshAfterLayout();
      if (!fitted) {
        scheduleInitialLayout(200);
        return;
      }
      retryTerminalWebglAfterFit(surface, fitted);
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
    const live = cloneTerminalChunk(event.data);
    if (surface && (surface.rebuildingHistory || surface.historyInitialized)) {
      retainLiveChunk(surface, live, !surface.rebuildingHistory);
    }
    if (!surface?.rebuildingHistory) writeTerminalBytes(term, live);
    scheduleInitialLayout(240);
  });
  bindTerminalIme(term, socket, () => surface);
  let resizeTimer = 0;
  const sendCurrentSize = () => {
    if (socket.readyState !== WebSocket.OPEN) return false;
    const cols = Number(term.cols) || TERMINAL_COLS;
    const rows = Math.max(TERMINAL_MIN_ROWS, Number(term.rows) || TERMINAL_ROWS);
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
      const fitted = fitTerminalToContainer(term, element, fitAddon);
      retryTerminalWebglAfterFit(surface, fitted);
      const geometryChanged = term.cols !== previousCols || term.rows !== previousRows;
      if (!geometryChanged && !restoringTab) {
        if (surface) surface.savedViewport = null;
        return;
      }
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
    historyInitialized: false,
    retainedLiveChunks: [],
    replaying: false,
    replayDepth: 0,
    webglAddon: null,
    webglFailedSinceRecovery: false,
  };
  attachTerminalWebgl(surface);
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
    : candidates[0] || "";
}

async function createTerminalTab(coordinate, session) {
  const project = coordinate?.project || {};
  const projectId = String(project.project_id || coordinate?.nodeId || "").trim();
  const mode = String(coordinate?.mode || "MASTER").toUpperCase();
  const cwd = String(project.project_root || "").trim();
  if (!projectId || !cwd) {
    throw new Error("A registered project root is required to open a CLI tab");
  }
  const provider = terminalProviderFor(coordinate, session);
  const modelRef = String(
    coordinate?.modelRef || coordinate?.model_ref || ""
  ).trim();
  const effort = String(coordinate?.effort || "AUTO").toUpperCase();
  const isResume = Boolean(session);
  const actionId = isResume ? "session.resume" : "session.new";
  const supervisorSessionId = terminalSupervisorSessionId(session);
  const anchorRef = typeof sessionAnchorRef === "function"
    ? sessionAnchorRef(session)
    : String(session?.session_anchor_ref || "").trim();
  const hostRef = typeof hostSessionRef === "function" ? hostSessionRef(session) : "";
  const request = isResume
    ? {
        target: "CLI_TERMINAL",
        provider,
        supervisor_session_id: supervisorSessionId,
        pty_binding_anchor_ref: anchorRef,
        host_session_ref: hostRef,
      }
    : mode === "CONDUCTOR"
      ? {
          target: "UNIVERSE_CONDUCTOR",
          provider,
          model_ref: modelRef,
          effort,
        }
      : {
          target: "PROJECT_MASTER",
          project_id: projectId,
          provider,
          model_ref: modelRef,
          effort,
        };
  const created = await invokeServerAction(actionId, request);
  const tab = created.terminal || created;
  state.dismissedTerminalIds = state.dismissedTerminalIds || {};
  delete state.dismissedTerminalIds[tab.terminal_id];
  await loadTerminalTabs();
  const visible = (state.terminals || []).find(
    (item) => item.terminal_id === tab.terminal_id
  );
  if (!visible) throw new Error("Managed Host is not available for this session");
  selectTerminalTab(visible.terminal_id);
  return visible;
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
  if (!id) throw new Error("No managed Host session");
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
  renderReattachBanner();
  renderTerminalNewMenu();
}

function focusTerminalForSession(coordinate, session) {
  const wantedId = typeof sessionTerminalId === "function"
    ? sessionTerminalId(session)
    : String(session?.terminal_id || session?.pty_binding?.terminal_id || "").trim();
  if (wantedId) {
    const exact = (state.terminals || []).find((item) => item.terminal_id === wantedId);
    if (exact && terminalDockVisible(exact)) {
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
    state.supervisorHosts = payload.hosts || [];
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
    if (typeof renderSessionObservatory === "function") renderSessionObservatory();
    if (typeof renderTodos === "function") renderTodos();
    if (typeof renderDetails === "function") renderDetails();
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
    await loadResumableSessions();
    renderReattachBanner();
    renderTerminalNewMenu();
  } catch (_error) {
    state.terminals = state.terminals || [];
  }
}

function hostSessionRefOf(item) {
  return String(
    item?.host_session_ref || item?.host_id || item?.reconnection_host_id || ""
  ).trim();
}

function hostRuntimeLive(host) {
  return String(host?.runtime_state || host?.state || "").trim().toUpperCase() === "LIVE";
}

function hostCompatibilityOk(host) {
  return ["CURRENT", "COMPATIBLE_OLD"].includes(
    String(host?.compatibility || host?.host_compatibility || "").trim().toUpperCase()
  );
}

function eligibleReattachHosts() {
  const openHosts = new Set((state.terminals || []).map(hostSessionRefOf).filter(Boolean));
  const openAnchors = new Set(
    (state.terminals || [])
      .map((item) => String(item.session_anchor_ref || item.active_session_anchor_ref || "").trim())
      .filter(Boolean)
  );
  return (state.supervisorHosts || []).filter((host) => {
    const href = hostSessionRefOf(host);
    const anchor = String(host.session_anchor_ref || host.anchor_ref || "").trim();
    return hostRuntimeLive(host)
      && host.reconnect_eligible === true
      && hostCompatibilityOk(host)
      && href
      && !openHosts.has(href)
      && !(anchor && openAnchors.has(anchor));
  });
}

function reattachHostLabel(host) {
  const project = String(host.project_id || host.node || "session").trim();
  const mode = String(host.mode || "").trim().toUpperCase();
  const provider = String(host.provider || "").trim().toUpperCase();
  const parts = [project, mode, provider].filter(Boolean);
  return parts.length ? parts.join(" ") : hostSessionRefOf(host);
}

function joinReattachHost(host, catalog) {
  const row = { ...host };
  const href = hostSessionRefOf(host);
  const anchor = String(host.session_anchor_ref || host.anchor_ref || "").trim();
  const match = (catalog || []).find((item) => {
    if (item.kind === "REATTACH" && hostSessionRefOf(item) === href) return true;
    return String(item.session_anchor_ref || "").trim() === anchor && Boolean(anchor);
  });
  if (match) {
    row.project_id = row.project_id || match.project_id;
    row.mode = row.mode || match.mode;
    row.provider = row.provider || match.provider;
    row.supervisor_session_id = row.supervisor_session_id || match.supervisor_session_id;
    row.label = match.label || row.label;
    row.last_seen_at = row.last_seen_at || match.last_seen_at;
  }
  const session = (state.projectAnchorSessions || []).find(
    (item) => String(item.session_anchor_ref || "").trim() === anchor && Boolean(anchor)
  );
  if (session) {
    row.project_id = row.project_id || session.project_id || session.node;
    row.mode = row.mode || session.mode;
    row.provider = row.provider || session.provider;
    row.supervisor_session_id =
      row.supervisor_session_id || session.universe_session_id || session.session_id;
    row.last_seen_at = row.last_seen_at || session.last_seen_at;
  }
  const live = (state.supervisorTerminals || []).find(
    (item) => hostSessionRefOf(item) === href && Boolean(href)
  );
  if (live) {
    row.project_id = row.project_id || live.project_id;
    row.mode = row.mode || live.mode;
    row.provider = row.provider || live.provider;
    row.supervisor_session_id =
      row.supervisor_session_id || live.supervisor_session_id || live.session_id;
    row.session_anchor_ref =
      row.session_anchor_ref || live.session_anchor_ref || live.active_session_anchor_ref;
  }
  if (!row.label) row.label = reattachHostLabel(row);
  return row;
}

function currentReattachHosts() {
  const catalog = state.resumableSessions?.reattach || [];
  const hosts = eligibleReattachHosts().map((host) => joinReattachHost(host, catalog));
  const seen = new Set(hosts.map(hostSessionRefOf).filter(Boolean));
  for (const item of catalog) {
    const href = hostSessionRefOf(item);
    if (href && seen.has(href)) continue;
    hosts.push(item);
    if (href) seen.add(href);
  }
  return hosts;
}

async function loadResumableSessions() {
  try {
    const payload = await api("/v1/sessions/resumable?limit=7");
    state.resumableSessions = payload;
    return payload;
  } catch (_error) {
    state.resumableSessions = { reattach: [], resume: [], incompatible: [] };
    return state.resumableSessions;
  }
}

function renderReattachBanner() {
  const banner = document.querySelector("#terminal-reattach-banner");
  const text = document.querySelector("#terminal-reattach-banner-text");
  if (!banner || !text) return;
  const hosts = currentReattachHosts();
  if (!hosts.length) {
    state.reattachBannerDismissed = false;
    banner.hidden = true;
    banner.classList.add("hidden");
    return;
  }
  const show = state.reattachBannerDismissed !== true;
  banner.hidden = !show;
  banner.classList.toggle("hidden", !show);
  text.textContent = `재기동됨 — 재접속 가능한 세션 ${hosts.length}개`;
}

function hideReattachBanner() {
  state.reattachBannerDismissed = true;
  renderReattachBanner();
}

function renderTerminalNewMenu() {
  const menu = document.querySelector("#terminal-new-menu");
  if (!menu) return;
  const hosts = currentReattachHosts();
  menu.replaceChildren();
  const neu = document.createElement("button");
  neu.type = "button";
  neu.className = "terminal-new-menu-item";
  neu.setAttribute("role", "menuitem");
  neu.textContent = "New session";
  neu.addEventListener("click", () => {
    closeTerminalNewMenu();
    const homeNode = typeof homeSelectedNode === "function" ? homeSelectedNode() : null;
    openNewSessionDialog({
      projectId: state.selectedProject?.project_id,
      nodeHint: homeNode?.label,
    });
  });
  menu.append(neu);
  for (const host of hosts) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "terminal-new-menu-item";
    item.setAttribute("role", "menuitem");
    const label = host.label || reattachHostLabel(host);
    item.textContent = `Re-attach ${label}`;
    item.title = "Live PTY — re-attach immediately";
    item.addEventListener("click", () => {
      closeTerminalNewMenu();
      reattachLiveHost(host).catch((error) => toast(error.message, true));
    });
    menu.append(item);
  }
}

function closeTerminalNewMenu() {
  const menu = document.querySelector("#terminal-new-menu");
  const button = document.querySelector("#terminal-new-session");
  if (!menu) return;
  menu.hidden = true;
  menu.classList.add("hidden");
  if (button) button.setAttribute("aria-expanded", "false");
}

function toggleTerminalNewMenu() {
  const menu = document.querySelector("#terminal-new-menu");
  const button = document.querySelector("#terminal-new-session");
  if (!menu) return;
  renderTerminalNewMenu();
  const open = menu.hidden;
  menu.hidden = !open;
  menu.classList.toggle("hidden", !open);
  if (button) button.setAttribute("aria-expanded", String(open));
}

async function reattachLiveHost(host) {
  const projectId = String(host.project_id || host.node || "universe").trim() || "universe";
  const project = (state.projects || []).find(
    (item) => String(item.project_id || "").trim() === projectId
  ) || { project_id: projectId, project_root: "" };
  if (!project.project_root) {
    const universe = (state.projects || []).find(
      (item) => String(item.project_id || "").trim() === "universe"
    );
    if (universe) {
      project.project_id = project.project_id || universe.project_id;
      project.project_root = universe.project_root;
    }
  }
  const session = {
    host_session_ref: hostSessionRefOf(host),
    session_anchor_ref: String(host.session_anchor_ref || host.anchor_ref || "").trim(),
    project_id: project.project_id || projectId,
    mode: String(host.mode || "MASTER").toUpperCase(),
    provider: String(host.provider || "AUTO").toUpperCase(),
    session_id: host.supervisor_session_id || host.session_id,
    universe_session_id: host.supervisor_session_id || host.session_id,
  };
  await createTerminalTab(
    { project, nodeId: project.project_id || projectId, mode: session.mode, modelRef: "", effort: "AUTO" },
    session
  );
  await loadResumableSessions();
  renderReattachBanner();
  renderTerminalNewMenu();
}

async function reattachAllLiveHosts() {
  const hosts = currentReattachHosts();
  for (const host of hosts) {
    await reattachLiveHost(host);
  }
}

async function noteServiceReconnect() {
  state.reattachBannerDismissed = false;
  await loadTerminalTabs();
  await loadResumableSessions();
  renderReattachBanner();
}
