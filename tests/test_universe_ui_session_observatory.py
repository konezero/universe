from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tools" / "universe_ui" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "tools" / "universe_ui" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "universe_ui" / "styles.css").read_text(encoding="utf-8")
TERM = (ROOT / "tools" / "universe_ui" / "terminals.js").read_text(encoding="utf-8")


class SessionObservatoryUiContractTests(unittest.TestCase):
    def test_conversation_expansion_escapes_and_restores_the_dock(self) -> None:
        self.assertIn('id="conversation-expand"', HTML)
        expand_slice = APP[
            APP.index('elements.conversationExpand.addEventListener("change"') :
            APP.index("elements.goalPlanMap?.addEventListener")
        ]
        self.assertIn("document.body.append(elements.conversationLayer)", expand_slice)
        self.assertIn("parent.insertBefore(", expand_slice)
        self.assertIn('classList.toggle("expanded", expanded)', expand_slice)

    def test_goal_plan_editor_preserves_revision_and_selection(self) -> None:
        self.assertIn('class="goal-plan-toolbar"', HTML)
        self.assertIn('id="edit-selected-goal"', HTML)
        self.assertIn('state.selectedGoalId = contextualGoals[0]?.goal_id || null;', APP)
        self.assertIn(
            'revision: state.goals.find((goal) => goal.goal_id === goalId)?.revision',
            APP,
        )
        goal_dialog = HTML[HTML.index('id="goal-dialog"') : HTML.index('id="milestone-dialog"')]
        self.assertIn('value="ACTIVE"', goal_dialog)
        self.assertNotIn('value="IN_PROGRESS"', goal_dialog)

    def test_sessions_group_into_collapsible_project_tree(self) -> None:
        self.assertIn("binding.current_project_id", APP)
        self.assertIn("sessionRailProjectIdentity", APP)
        self.assertIn("providerChatExpandedProjects", APP)
        self.assertIn("providerChatExpandedBranches", APP)
        self.assertIn("session-project-tree", APP)
        self.assertIn("session-project-toggle", APP)
        self.assertIn("session-project-branch-toggle", APP)
        self.assertIn('key: "unassigned"', APP)
        self.assertIn('label: "Unassigned sessions"', APP)
        self.assertIn('appendBranch("Current", group.current)', APP)
        self.assertIn('appendBranch("Past", group.past)', APP)
        self.assertIn('appendBranch("Unbound", group.unbound)', APP)
        self.assertIn("room.workspace_name", APP)
        self.assertLess(
            APP.index('appendBranch("Current", group.current)'),
            APP.index('appendBranch("Past", group.past)'),
        )

    def test_left_rail_renders_nodes_with_fixed_modes_and_session_state(self) -> None:
        self.assertIn('id="node-mode-list"', HTML)
        self.assertIn('id="node-mode-count"', HTML)
        self.assertIn('class="section-heading legacy-rail-surface"', HTML)
        self.assertIn('class="project-list legacy-rail-surface"', HTML)
        self.assertIn("function normalizeNodeModeNode(nodeId)", APP)
        self.assertIn("function nodeModeCatalog(project)", APP)
        self.assertIn('const modes = isUniverseHome ? ["MASTER", "CONDUCTOR"] : ["MASTER"];', APP)
        self.assertIn("const terminalProject = mode === \"CONDUCTOR\" ? universeProject : project;", APP)
        self.assertIn("await startNewNodeModeSession({", APP)
        self.assertIn("function nodeModeSessionIsActive(session)", APP)
        self.assertIn("function nodeModeSessionIsCurrent(session)", APP)
        self.assertIn("function vendorStreamStateForSession(session)", APP)
        self.assertIn("function startNewNodeModeSession(coordinate)", APP)
        self.assertIn("function openNodeModeSessionHost(coordinate, session)", APP)
        self.assertIn("function openNodeModeSessionActions(coordinate, session)", APP)
        self.assertIn("function inspectNodeModeSession(coordinate, session)", APP)
        self.assertIn("function endNodeModeSession(session)", APP)
        self.assertIn('id="node-session-action-dialog"', HTML)
        self.assertIn(">Inspector<", HTML)
        self.assertIn(">Inbox<", HTML)
        self.assertIn(">Open Host<", HTML)
        self.assertNotIn(">PTY Binding<", HTML)
        self.assertIn(">End session<", HTML)
        self.assertIn("function openSessionBusInbox", APP)
        self.assertIn("/v1/session-bus/messages", APP)
        self.assertIn('data-session-bus-projection="INBOX"', HTML)
        self.assertIn('data-session-bus-projection="ACTIVITY"', HTML)
        self.assertIn('data-session-bus-projection="RESULTS"', HTML)
        self.assertIn('state.sessionBusProjection || "INBOX"', APP)
        self.assertIn("function sessionBusEvidenceRows", APP)
        self.assertIn("function renderSessionBusEvidence", APP)
        self.assertIn("sessionBusWorking", APP)
        self.assertIn('return "WORKING";', APP)
        self.assertIn("message?.event_context", APP)
        self.assertIn("context.projection_state", APP)
        self.assertIn('"Source event"', APP)
        self.assertIn('"Session Anchor"', APP)
        self.assertIn('"Task Frame"', APP)
        self.assertIn("lifecycle.result_ref", APP)
        self.assertIn("session_anchor_ref", APP[APP.index("function sessionBusTarget") : APP.index("async function refreshSessionBusMessages")])
        self.assertIn("openSessionSummaryForNew(coordinate);", APP)
        self.assertIn("sessionSummaryInspectOnly", APP)
        self.assertIn("function openSessionInspectSummary(coordinate, session)", APP)
        self.assertIn("revealInspector: same", APP)
        self.assertIn("createTerminalTab(coordinate)", APP)
        self.assertIn("/v1/terminals", TERM)
        self.assertIn('id="terminal-tabs"', HTML)
        self.assertNotIn("terminal-input-form", HTML)
        self.assertNotIn("sendRemoteTerminalChat", TERM)
        self.assertNotIn("function sendTerminalInput(text)", TERM)
        self.assertNotIn("term.options.disableStdin = true", TERM)
        self.assertIn("function applyCliDockTitle(session)", TERM)
        self.assertIn("function terminalDockVisible(session)", TERM)
        self.assertIn("terminalDockVisible(item)", TERM)
        self.assertIn('state.activeTerminalId = null;', TERM)
        terminal_label = TERM[
            TERM.index("function terminalLabel(session)") : TERM.index(
                "function applyCliDockTitle(session)"
            )
        ]
        self.assertIn('session?.provider || ""', terminal_label)
        self.assertIn('provider !== "AUTO"', terminal_label)
        self.assertIn("function focusTerminalForSession(coordinate, session)", TERM)
        focus_slice = TERM[
            TERM.index("function focusTerminalForSession(coordinate, session)") : TERM.index(
                "async function loadTerminalTabs()"
            )
        ]
        self.assertIn("terminalDockVisible(item)", focus_slice)
        self.assertIn("dismissedTerminalIds", TERM)
        self.assertIn("function refitActiveTerminal()", TERM)
        self.assertNotIn("window.setTimeout(run, 220)", TERM)
        self.assertIn("function captureTerminalViewport(surface)", TERM)
        self.assertIn("function restoreTerminalViewport(surface)", TERM)
        self.assertIn("async function loadOlderTerminalHistory(surface, session)", TERM)
        self.assertIn('"/history?limit=100"', TERM)
        self.assertIn("historyBeforeCursor", TERM)
        self.assertIn("screen_snapshot_base64", TERM)
        self.assertIn("rebuilt.baseY - distanceFromBottom", TERM)
        self.assertIn(
            "surface.historyLoading = true;\n  surface.rebuildingHistory = true;",
            TERM,
        )
        self.assertIn("function trimHistoryCoveredLiveTail(historyChunks, retainedLiveChunks)", TERM)
        self.assertIn("surface.retainedLiveChunks.push({", TERM)
        self.assertIn("await writeUndisplayedLiveTail(surface)", TERM)
        self.assertIn("fitAddon.proposeDimensions()", TERM)
        # Fixed 120-column grid; the font scales, the column count does not.
        self.assertIn("const TERMINAL_COLS = 120;", TERM)
        self.assertIn("(ref * at.cols) / TERMINAL_COLS", TERM)
        self.assertIn("term.resize(TERMINAL_COLS, rows)", TERM)
        # GPU renderer, kept only if activate() actually installed a canvas.
        self.assertIn("window.WebglAddon?.WebglAddon", TERM)
        self.assertIn('!surface.element?.querySelector("canvas")', TERM)
        self.assertIn("webglFailedSinceRecovery", TERM)
        self.assertIn("function attachTerminalWebgl(surface)", TERM)
        self.assertIn("webgl.onContextLoss(", TERM)
        self.assertIn("surface?.lastSentSizeKey === sizeKey", TERM)
        self.assertIn("surface.notifySize?.(0)", TERM)
        self.assertIn("attachCustomKeyEventHandler", TERM)
        self.assertIn("event.isComposing", TERM)
        self.assertIn("function bindTerminalIme(term, socket, getSurface)", TERM)
        self.assertIn("IME_STALE_COMPOSITION_MS", TERM)
        self.assertIn("HANGUL_PREEDIT_PATTERN", TERM)
        # A click anywhere in the pane must land keyboard focus in xterm, even
        # when the running TUI has grabbed the mouse (mouse-tracking mode) and
        # so does not move focus on its own.
        surface_slice = TERM[
            TERM.index("function ensureTerminalSurface") : TERM.rindex(
                "bindTerminalIme(term, socket, () => surface)"
            )
        ]
        self.assertIn('element.addEventListener(', surface_slice)
        self.assertIn('"pointerdown",', surface_slice)
        self.assertIn("try { term.focus(); }", surface_slice)
        self.assertIn('data.normalize("NFC")', TERM)
        self.assertIn("compositionend", TERM)
        self.assertNotIn("function HangulBuffer()", TERM)
        # An IME keydown must never be preventDefault-ed (returning false) —
        # that cancels macOS marked-text composition. keyCode 229 returns true.
        self.assertIn("event.keyCode !== 229", TERM)
        self.assertIn("if (event.keyCode === 229) lastKey229At", TERM)
        self.assertIn("composeWatchdog = window.setTimeout", TERM)
        self.assertIn("isComposingNow() && !isControlData", TERM)
        # compositionend must NOT re-send the composed text — xterm's own
        # composition handler emits it through onData (double = 한한글글).
        self.assertNotIn("commitComposedText", TERM)
        self.assertIn("Do NOT send here", TERM)
        # Degraded macOS path (no composition events): the marked syllable is
        # tracked from input/insertReplacementText and flushed on a boundary,
        # and only while composition events are demonstrably absent.
        self.assertIn("Date.now() - lastCompositionAt < 1500", TERM)
        self.assertIn("Date.now() - lastCompositionAt > 1500", TERM)
        self.assertIn("insertReplacementText", TERM)
        self.assertIn("const flushImeMarked", TERM)
        self.assertIn("imeFlushTimer = window.setTimeout(flushImeMarked", TERM)
        self.assertIn("IME_BOUNDARY_KEYS", TERM)
        self.assertIn('textarea.addEventListener("blur"', TERM)
        self.assertIn("flushImeMarked()", TERM)
        # The IME/input timing trace is strictly opt-in behind ?imedebug=1
        # and a no-op otherwise (no console spam, no DOM overlay, by default).
        self.assertNotIn("__imeTrace", TERM)
        self.assertIn('.get("imedebug") === "1"', TERM)
        self.assertIn("if (!IME_DEBUG) return;", TERM)
        # iOS Safari fires no composition events; the `input` stream is mirrored
        # directly (deleteContentBackward -> \x7f, insertText -> text) and
        # xterm's own printable/DEL onData is dropped.
        self.assertIn("const IS_IOS", TERM)
        self.assertIn("if (IS_IOS) {", TERM)
        self.assertIn('it === "deleteContentBackward"', TERM)
        self.assertIn('sendPtyText(socket, "\\x7f")', TERM)
        self.assertIn('if (IS_IOS && (!isControlData || data === "\\x7f"))', TERM)
        self.assertIn("withTerminalReplayGuard", TERM)
        self.assertIn("attachTerminalMouseWheelHandler", TERM)
        # The wheel handler must let xterm process every event (scrollback +
        # SGR mouse-wheel reports) and contain any leftover on the pane, not
        # cancel xterm processing under mouse tracking.
        self.assertIn("attachCustomWheelEventHandler(() => true)", TERM)
        self.assertIn('if (!event.defaultPrevented) event.preventDefault();', TERM)
        self.assertIn(".xterm-helper-textarea", CSS)
        self.assertIn("async function stopTerminalSession(terminalId)", TERM)
        self.assertIn('method: "DELETE"', TERM)
        self.assertIn("item.provider === provider", TERM)
        self.assertIn("const tab = created.terminal || created;", TERM)
        self.assertNotIn("const session = created.terminal || created;", TERM)
        self.assertIn("pty_binding_anchor_ref", TERM)
        self.assertIn("supervisor_session_id", TERM)
        self.assertIn("function terminalSupervisorSessionId(session)", TERM)
        supervisor_slice = TERM[
            TERM.index("function terminalSupervisorSessionId") : TERM.index(
                "async function createTerminalTab"
            )
        ]
        self.assertIn("state.supervisorSessions", supervisor_slice)
        self.assertIn("session?.universe_session_id", supervisor_slice)
        self.assertIn("item?.universe_session_id || item?.session_id", supervisor_slice)
        self.assertIn("supervised.universe_session_id || supervised.session_id", supervisor_slice)
        create_slice = TERM[
            TERM.index("async function createTerminalTab") : TERM.index(
                "function refitActiveTerminal"
            )
        ]
        self.assertIn(
            'target: "CLI_TERMINAL"',
            create_slice,
        )
        self.assertIn('"session.new"', create_slice)
        self.assertIn('"session.resume"', create_slice)
        self.assertIn("invokeServerAction(actionId, request)", create_slice)
        self.assertIn('target: "UNIVERSE_CONDUCTOR"', create_slice)
        self.assertIn('target: "PROJECT_MASTER"', create_slice)
        self.assertNotIn("resume_session_ref", create_slice)
        self.assertIn("createTerminalTab(coordinate, session)", APP)
        mobile_session_nav = APP[
            APP.index('elements.mobileWorkTabs?.addEventListener("click"') : APP.index(
                "const activeGoal ="
            )
        ]
        self.assertIn(
            'else if (view === "sessions") showGraphView("sessions");',
            mobile_session_nav,
        )
        self.assertNotIn("sessionObservatoryDialog?.showModal()", mobile_session_nav)
        self.assertNotIn("function projectMasterSetting(projectId)", TERM)
        self.assertIn("session?.provider || session?.current_provider", TERM)
        self.assertIn('throw new Error("Choose a provider for this session")', TERM)
        self.assertIn("function observerProvider(session)", TERM)
        self.assertIn("session?.observer_session_ref", TERM)
        self.assertNotIn("last_session_ref", TERM)
        self.assertNotIn("last_provider", TERM)
        self.assertIn("if (showTerminal) return;", APP)
        self.assertNotIn(".terminal-input-form", CSS)
        self.assertIn("function projectForVendorWorkspace(room)", APP)
        self.assertIn("function unboundVendorSessionFromRoom(room, project, mode)", APP)
        self.assertIn("async function attachProviderChatRoom(room, coordinate)", APP)
        self.assertIn("/v1/session-observer/chat-rooms/", APP)
        self.assertIn("vendor_unbound:", APP)
        self.assertIn('=== "INDEPENDENT"', APP)
        self.assertIn('currentness === "CURRENT"', APP)
        self.assertIn("state.projectAnchorSessions", APP)
        self.assertIn("chatCatalog.anchor_sessions", APP)
        self.assertIn("...(state.projectAnchorSessions || [])", APP)
        self.assertIn("...(state.supervisorSessions || [])", APP)
        self.assertIn("function providerSessionObservedProjectId(room)", APP)
        self.assertIn("coordinate.sessions.push(source.session)", APP)
        coords = APP[
            APP.index("function nodeModeCoordinates") : APP.index("function nodeModeStatusLabel")
        ]
        self.assertNotIn("unboundByCoordinate", coords)
        self.assertNotIn('["BOUND", "ANCHOR_OBSERVED", "INDEPENDENT"]', coords)
        self.assertIn("nodeModeSessionIsCurrent(session)", coords)
        self.assertIn("nodeModeSessionIsWorking(session)", APP)
        self.assertIn("node-mode-item", CSS)
        self.assertIn(".node-mode-node", CSS)
        self.assertIn('data-active="false"', CSS)
        self.assertNotIn(
            ".filter((project) => !isProjectContainer(project))",
            APP[APP.index("function nodeModeCoordinates") : APP.index("function nodeModeStatusLabel")],
        )
        self.assertIn("function renderNodeModeGroup", APP)
        self.assertIn("node-mode-group-nested", APP)
        self.assertIn("childrenByParent.get(group.nodeId)", APP)
        self.assertIn(".node-mode-group-nested", CSS)

    def test_mode_cards_use_anchor_sessions_and_host_only_reconnect_projection(self) -> None:
        self.assertIn("selectedModeCoordinateKey: null", APP)
        self.assertIn("function renderNodeModeSessionCards(coordinate)", APP)
        self.assertIn("node-mode-session-card", APP)
        # Session cards render only for the selected mode — the mode tree is a
        # navigator, not a session dump (rag/universe-shell-ia-and-galaxy-view).
        self.assertIn("if (modeSelected) {\n        list.append(renderNodeModeSessionCards(coordinate));", APP)
        self.assertIn("function nodeModePanelSessionBuckets", APP)
        self.assertIn("working: sessions.filter(nodeModeSessionIsWorking)", APP)
        self.assertIn("Session and Anchor records are authoritative", APP)
        self.assertIn("No current or recent sessions in this mode", APP)
        self.assertIn("const NODE_MODE_DEFAULT_SESSION_LIMIT = 1", APP)
        self.assertIn("const NODE_MODE_EXPANDED_SESSION_LIMIT = 7", APP)
        self.assertIn("function recentAnchorSessionsForCoordinate", APP)
        self.assertIn("all.slice(", APP)
        self.assertIn("hostReconnectEligible(session)", APP)
        self.assertIn('statusLabel = "HOST INCOMPATIBLE"', APP)
        self.assertIn('statusLabel = "CURRENT"', APP)
        self.assertNotIn("idle PTY session", APP)
        self.assertNotIn("function ptyLiveTerminalsForCoordinate", APP)
        self.assertIn("function hostForSession(session)", APP)
        self.assertIn("state.supervisorHosts", APP)
        self.assertIn("hostIsUsable(hostForSession(session))", APP)
        self.assertNotIn("session?.pty_binding?.host_reconnect_eligible", APP)
        self.assertIn("state.supervisorTerminals", TERM)
        self.assertIn("function terminalHostForSession(session)", TERM)
        self.assertIn("state.supervisorHosts", TERM)
        self.assertIn("function nodeModePanelSessions", APP)
        self.assertNotIn('session_kind: "PTY_LIVE"', APP)
        self.assertNotIn("function sessionFromPtyTerminal", APP)
        self.assertIn('api("/v1/terminals")', TERM)
        open_slice = APP[
            APP.index("function openNodeModeCoordinate") : APP.index(
                "function renderNodeModes"
            )
        ]
        self.assertIn("state.selectedModeCoordinateKey = coordinate.key", open_slice)
        self.assertIn("state.selectedModeCoordinateKey = null", open_slice)
        self.assertNotIn("openProviderChatSummary", open_slice)
        self.assertIn("node-mode-session-cards", CSS)
        self.assertIn("node-mode-session-card", CSS)
        self.assertIn("createTerminalTab(coordinate)", APP)
        self.assertIn("focusTerminalForSession(coordinate, session)", APP)
        self.assertIn("openNodeModeSessionHost(pending.coordinate, pending.session)", APP)
        self.assertNotIn("await openNodeModeSessionHost(coordinate, session)", APP)
        self.assertIn("New session", APP)
        card_slice = APP[APP.index("function renderNodeModeSessionCards") : APP.index("function selectNodeModeNode")]
        self.assertNotIn("Delegate here", card_slice)
        self.assertIn("Re-attach", card_slice)
        self.assertIn("Resume", card_slice)
        self.assertIn("런타임 바뀜 — 종료 후 재생성", card_slice)
        self.assertIn("더 보기", card_slice)

    def test_dock_reattach_menu_and_reconnect_banner(self) -> None:
        self.assertIn('id="terminal-reattach-banner"', HTML)
        self.assertIn('id="terminal-reattach-all"', HTML)
        self.assertIn(">모두 re-attach<", HTML)
        self.assertIn(">선택<", HTML)
        self.assertIn('id="terminal-new-menu"', HTML)
        self.assertIn("function eligibleReattachHosts()", TERM)
        self.assertIn("function renderReattachBanner()", TERM)
        self.assertIn("function toggleTerminalNewMenu()", TERM)
        self.assertIn("function reattachLiveHost(host)", TERM)
        self.assertIn("function reattachAllLiveHosts()", TERM)
        self.assertIn("async function noteServiceReconnect()", TERM)
        close_slice = TERM[
            TERM.index("async function closeTerminalTab") : TERM.index(
                "function focusTerminalForSession"
            )
        ]
        self.assertIn("renderReattachBanner()", close_slice)
        self.assertIn('api("/v1/sessions/resumable?limit=7")', TERM)
        self.assertIn('"session.resume"', TERM)
        self.assertIn("hostRuntimeLive(host)", TERM)
        self.assertIn('host.reconnect_eligible === true', TERM)
        self.assertIn('"CURRENT", "COMPATIBLE_OLD"', TERM)
        self.assertIn("toggleTerminalNewMenu()", APP)
        self.assertIn("noteServiceReconnect", APP)
        self.assertIn("state.lastServiceReady === false && ready", APP)
        self.assertIn(".terminal-reattach-banner", CSS)
        self.assertIn(".terminal-new-menu", CSS)

    def test_goal_plan_hides_done_todos_but_keeps_completion_metrics(self) -> None:
        # The Plan/Board toggle is gone; the integrated home is the only layout.
        plan_slice = APP[APP.index("function openPlanTodos") : APP.index("function renderTodos")]
        self.assertIn('todo.state !== "DONE"', plan_slice)
        self.assertIn("openPlanTodos(goal.todos)", plan_slice)
        home_slice = APP[APP.index("function renderIntegratedHome") : APP.index("function renderHomeProjects")]
        self.assertIn("homeSelectedNode()", home_slice)
        self.assertIn("renderHomeKanban(selNode, nodeTodos, selTodo)", home_slice)

    def test_sessions_are_diagnostics_not_primary_navigation(self) -> None:
        self.assertNotIn('data-primary-view="sessions"', HTML)
        self.assertIn('data-primary-view="fleet"', HTML)
        self.assertIn('id="session-observatory-topbar-button"', HTML)
        self.assertIn('api("/v1/session-graph")', APP)
        self.assertIn("function buildSessionGraph()", APP)
        self.assertIn("function sessionGraphNodeLabel(item)", APP)
        self.assertIn('item?.entity_type || ""', APP)
        self.assertIn('item?.provider || ""', APP)
        self.assertIn('currentness === "CURRENT"', APP)
        self.assertIn("label: sessionGraphNodeLabel(item)", APP)
        self.assertIn("item.y = -270 + depth * 85;", APP)
        self.assertIn('state.view === "sessions"', APP)
        self.assertIn('item.kind === "session_anchor"', APP)
        self.assertIn("selectNodeModeSession(coordinate, session)", APP)
        self.assertIn("MODE_ANCHOR", APP)
        self.assertIn("SESSION_ANCHOR", APP)
        self.assertIn("TASK_FRAME", APP)
        self.assertIn('id="graph-legend"', HTML)
        self.assertIn('if (view === "sessions") fitGraphView();', APP)
        self.assertIn('state.view === "sessions" ? 0.12 : 0.5', APP)
        self.assertIn('state.selectedProject?.project_id', APP)
        self.assertIn('visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to)', APP)
        self.assertIn(".node-key.session-anchor", CSS)

    def test_chat_toggle_has_no_message_count_badge(self) -> None:
        self.assertNotIn('id="conversation-badge"', HTML)
        self.assertNotIn("conversationBadge:", APP)
        self.assertNotIn("function updateConversationBadge", APP)

    def test_project_entry_is_unified_and_planning_is_project_scoped(self) -> None:
        self.assertIn('id="add-project-rail-button"', HTML)
        self.assertNotIn('id="fresh-project-rail-button"', HTML)
        self.assertNotIn('id="import-project-rail-button"', HTML)
        self.assertIn('id="plan-project-button"', HTML)
        self.assertIn("addProjectRailButton:", APP)
        self.assertIn("planProjectButton:", APP)
        self.assertIn('/v1/project-connections/prepare', APP)
        self.assertIn('/v1/project-connections/apply', APP)
        self.assertIn("elements.projectDialog.showModal()", APP)

    def test_import_project_root_uses_native_directory_picker(self) -> None:
        self.assertIn('id="project-root-browse"', HTML)
        self.assertIn('class="project-root-picker"', HTML)
        self.assertIn("projectRootBrowse:", APP)
        self.assertIn('api("/v1/host/select-directory"', APP)
        self.assertIn('elements.projectForm.elements.namedItem("project_root")', APP)
        self.assertIn('result.status === "DIRECTORY_SELECTED"', APP)
        self.assertIn(".project-root-picker", CSS)

    def test_fresh_project_directory_uses_native_directory_picker(self) -> None:
        fresh_dialog = HTML[HTML.index('id="fresh-project-dialog"') :]
        self.assertIn('name="project_root"', fresh_dialog)
        self.assertIn('id="fresh-project-root-browse"', fresh_dialog)
        self.assertIn("freshProjectRootBrowse:", APP)
        self.assertIn("function selectFreshProjectRoot()", APP)
        self.assertIn(
            'elements.freshProjectForm.elements.namedItem("project_root")', APP
        )
        self.assertIn(
            'project_root: String(form.get("project_root") || "").trim()', APP
        )

    def test_release_import_uses_native_file_pickers(self) -> None:
        release_dialog = HTML[HTML.index('id="release-dialog"') :]
        self.assertIn('id="release-database-browse"', release_dialog)
        self.assertIn('id="release-manifest-browse"', release_dialog)
        self.assertIn('class="host-path-picker"', release_dialog)
        self.assertIn('api("/v1/host/select-file"', APP)
        self.assertIn('"RELEASE_DATABASE"', APP)
        self.assertIn('"RELEASE_MANIFEST"', APP)
        self.assertIn(".host-path-picker", CSS)

    def test_release_proposal_renders_current_lifecycle_plan_shape(self) -> None:
        start = APP.index("function showReleaseProposal(proposal)")
        end = APP.index("async function proposeProjectRelease", start)
        renderer = APP[start:end]
        self.assertIn("Array.isArray(plan.actions)", renderer)
        self.assertIn("Array.isArray(plan.collisions)", renderer)
        self.assertIn("plan.installed_runtime?.state", renderer)
        self.assertIn("plan.project_host_preflight", renderer)
        self.assertNotIn("for (const action of proposal.plan.actions)", renderer)

    def test_release_catalog_selects_target_and_exposes_apply(self) -> None:
        release_dialog = HTML[HTML.index('id="release-dialog"') :]
        self.assertIn('id="release-target-project"', release_dialog)
        catalog_start = APP.index("function renderReleaseCatalog()")
        catalog_end = APP.index("function showReleaseProposal", catalog_start)
        catalog = APP[catalog_start:catalog_end]
        self.assertIn("state.selectedReleaseTargetProjectId", catalog)
        self.assertIn("visibleProjects()", catalog)
        self.assertIn("project.project_root", catalog)
        self.assertNotIn("action.disabled = !state.selectedProject", catalog)
        self.assertNotIn("state.selectedProject?.project_id", catalog)
        proposal_start = APP.index("function showReleaseProposal(proposal)")
        proposal_end = APP.index("async function proposeProjectRelease", proposal_start)
        proposal_renderer = APP[proposal_start:proposal_end]
        self.assertIn("applyProjectRelease(proposal, applyButton)", proposal_renderer)
        self.assertIn("async function applyProjectRelease", APP)
        self.assertIn("/release-proposals/apply`,", APP)
        self.assertIn('approval: "APPROVED"', APP)
        self.assertIn("proposal.release_database_sha256", APP)

    def test_right_chat_dock_and_sliding_inspector_contract(self) -> None:
        self.assertIn('class="conductor-panel glass-panel"', HTML)
        self.assertIn('id="conversation-layer"', HTML)
        self.assertIn('id="action-inbox-button"', HTML)
        self.assertNotIn('id="project-room-button"', HTML)
        self.assertIn('kind: "NONE"', APP)
        self.assertIn('state.conversationSurface === "CLI"', APP)
        self.assertIn("async function openPreparedProviderSession", APP)
        self.assertNotIn("function openProjectRoomSurface()", APP)
        self.assertIn('state.conversationSurface = "CLI"', TERM)
        self.assertIn('id="close-inspector"', HTML)
        header_start = HTML.index('<header class="conversation-layer-header">')
        self.assertLess(
            HTML.index('id="action-inbox-button"', header_start),
            HTML.index('id="room-message-list"', header_start),
        )
        self.assertIn(
            ".app-shell.mockup-shell > .graph-workspace > .conductor-panel",
            CSS,
        )
        self.assertIn(
            "var(--inspector-track-width)",
            CSS,
        )
        self.assertIn(
            "body.inspector-open .app-shell.mockup-shell > .inspector",
            CSS,
        )
        self.assertIn('id="chat-resize-handle"', HTML)
        self.assertIn("function renderNodeModeSessionCards(coordinate)", APP)
        self.assertIn("id=\"terminal-tabs\"", HTML)
        self.assertIn("createTerminalTab(coordinate)", APP)
        self.assertIn("initChatPanelResize()", APP)
        self.assertIn("--chat-panel-width: 380px", CSS)
        self.assertIn("chat-resize-handle", CSS)
        self.assertIn("--inspector-track-width: 0px", CSS)
        self.assertIn("grid-column: 3", CSS)
        self.assertIn("grid-column: 4", CSS)
        self.assertIn("grid-template-columns 220ms ease", CSS)
        self.assertIn("transform: translateX(-100%)", CSS)
        self.assertIn("transition:\n    transform 220ms ease", CSS)

    def test_project_and_universe_selection_do_not_auto_open_inspector(self) -> None:
        refresh_slice = APP[
            APP.index("async function refresh(") : APP.index("function projectDisplayName")
        ]
        self.assertIn("revealInspector: false,", refresh_slice)
        self.assertIn("syncAssets: syncSelectedProject", refresh_slice)

        project_slice = APP[
            APP.index("function projectButton") : APP.index("function renderProjects")
        ]
        self.assertIn("revealInspector: false", project_slice)

        node_mode_slice = APP[
            APP.index("function selectNodeModeNode") : APP.index(
                "function openNodeModeCoordinate"
            )
        ]
        self.assertIn("revealInspector: same", node_mode_slice)

        universe_slice = APP[
            APP.index('if (selected.kind === "universe")') : APP.index(
                'if (selected.kind === "project")'
            )
        ]
        self.assertIn("state.inspectorDismissed = true;", universe_slice)
        self.assertIn("renderDetails();", universe_slice)

        graph_node_slice = APP[
            APP.index('if (selected.kind === "project")') : APP.index(
                'if (["system", "related", "focus"].includes(selected.kind))'
            )
        ]
        self.assertIn("revealInspector: true", graph_node_slice)
        self.assertIn('showInspectorTab("details")', graph_node_slice)

        explicit_surface = APP[
            APP.index("function openInspectorSurface") : APP.index("function fitGraphView")
        ]
        self.assertIn("state.inspectorDismissed = false;", explicit_surface)
        self.assertIn('document.body.classList.add("inspector-open")', explicit_surface)

    def test_session_click_opens_summary_before_activation(self) -> None:
        self.assertIn('id="session-summary-dialog"', HTML)
        self.assertIn('id="session-summary-facts"', HTML)
        self.assertIn('id="session-summary-provider"', HTML)
        self.assertIn('id="session-summary-model"', HTML)
        self.assertIn('id="session-summary-effort"', HTML)
        self.assertIn('id="session-summary-connect"', HTML)
        self.assertIn('id="session-summary-new"', HTML)
        self.assertIn('id="session-summary-open"', HTML)
        self.assertIn("openProviderChatSummary(room)", APP)
        self.assertIn("renderProviderChatSummary", APP)
        rail_slice = APP[
            APP.index("function renderSessionRail") :
            APP.index("function renderSessionObservatory")
        ]
        self.assertNotIn("await activateAnchorSession(boundSession)", rail_slice)
        self.assertIn("session-summary-facts", CSS)

    def test_master_popup_opens_current_project_master_without_provider_ref(self) -> None:
        attach_slice = APP[
            APP.index("async function attachSelectedMasterSession(session)") : APP.index(
                "async function connectSessionSummaryProviderModel"
            )
        ]
        self.assertIn("await callProjectMaster(projectId", attach_slice)
        self.assertIn("provider,", attach_slice)
        self.assertIn("anchorKey: anchorSessionKey(session)", attach_slice)
        self.assertNotIn("/v1/sessions/inject", attach_slice)
        self.assertNotIn("providerSessionRef", attach_slice)
        self.assertNotIn("provider_session_ref", attach_slice)
        activate_slice = APP[
            APP.index("async function activateAnchorSession") : APP.index(
                "function sessionRailActivityLabel"
            )
        ]
        master_branch_start = activate_slice.index("if (session.mode === \"MASTER\")")
        room_branch_start = activate_slice.index("if (room)")
        master_branch = activate_slice[master_branch_start:room_branch_start]
        self.assertIn("await attachSelectedMasterSession(session)", master_branch)
        self.assertIn("await refreshSupervisorSessions()", master_branch)
        self.assertIn("expandConversationLayer();", master_branch)
        self.assertIn("return;", master_branch)
        self.assertNotIn("openProviderChatSession(room)", master_branch)
        connect_slice = APP[
            APP.index("async function connectSessionSummaryProviderModel") : APP.index(
                "function sessionConnectionText"
            )
        ]
        self.assertLess(
            connect_slice.index("await callProjectMaster"),
            connect_slice.index("elements.sessionSummaryDialog.close()"),
        )
        self.assertIn("session-summary-connection", CSS)

    def test_prepared_master_and_conductor_open_provider_sessions_not_room_chat(self) -> None:
        helper = APP[
            APP.index("async function openPreparedProviderSession") : APP.index(
                "async function callUniverseConductor"
            )
        ]
        self.assertIn("providerChatRoomForSupervisorSession(session)", helper)
        self.assertIn("await openProviderChatSession(room, { session })", helper)
        self.assertIn("showSessionSelection(\"Session is ready. Select it from the session card.\")", helper)
        conductor = APP[
            APP.index("async function callUniverseConductor") : APP.index(
                "async function callProjectMaster"
            )
        ]
        master = APP[
            APP.index("async function callProjectMaster") : APP.index(
                "async function attachSelectedMasterSession"
            )
        ]
        for route in (conductor, master):
            self.assertIn("await openPreparedProviderSession({", route)
            self.assertNotIn("openProjectRoomStream", route)
            self.assertNotIn('kind: \"PROJECT_MASTER\"', route)
            self.assertNotIn("returnToUniverseConductor", route)
        self.assertEqual(1, APP.count("openProjectRoomStream(projectId)"))

    def test_session_popup_separates_resume_and_new_provider_session(self) -> None:
        connection_slice = APP[
            APP.index("function renderSessionSummaryConnection") : APP.index(
                "function renderProviderChatSummary"
            )
        ]
        self.assertIn('["MASTER", "CONDUCTOR"].includes(mode)', connection_slice)
        self.assertIn('"Continue with profile"', connection_slice)
        self.assertIn('"Start new session"', connection_slice)
        self.assertIn('sessionAction = "RESUME"', APP)
        self.assertIn('connectSessionSummaryProviderModel("NEW")', APP)
        self.assertIn('invokeServerAction("session.resume"', APP)
        self.assertIn(
            'const validSelected = selected && models.includes(selected) ? selected : ""',
            APP,
        )

    def test_conductor_session_uses_the_same_lazy_prepare_attach_route(self) -> None:
        self.assertIn('async function callUniverseConductor(options = {})', APP)
        self.assertIn('target: "UNIVERSE_CONDUCTOR"', APP)
        self.assertIn("async function attachSelectedConductorSession(session)", APP)
        activate_slice = APP[
            APP.index("async function activateAnchorSession") : APP.index(
                "function sessionRailActivityLabel"
            )
        ]
        conductor_branch_start = activate_slice.index(
            'if (session.mode === "CONDUCTOR")'
        )
        conductor_branch = activate_slice[conductor_branch_start:]
        self.assertIn("await attachSelectedConductorSession(session)", conductor_branch)
        self.assertIn("await refreshSupervisorSessions()", conductor_branch)
        self.assertIn("expandConversationLayer();", conductor_branch)
        self.assertIn(
            'elements.returnToConductor.addEventListener("click", async () =>',
            APP,
        )

    def test_conductor_room_uses_resident_session_before_planning_binding(self) -> None:
        self.assertIn("const residentSessionReady = Boolean(", APP)
        self.assertIn('session?.resident === true', APP)
        self.assertIn(
            'state.conductorRuntimeBinding?.status === "BOUND" || residentSessionReady',
            APP,
        )
        self.assertIn('"Provider setup required"', APP)
        self.assertIn('"LLM connected / Auto-approve " + autoApprove', APP)

    def test_provider_settings_expose_models_and_worker_bindings(self) -> None:
        providers_panel = HTML[
            HTML.index('data-settings-panel="providers"') :
            HTML.index('data-settings-panel="host"')
        ]
        self.assertIn("<strong>Provider models</strong>", providers_panel)
        self.assertIn("<strong>Worker bindings</strong>", providers_panel)
        self.assertIn('id="worker-binding-scope"', providers_panel)
        self.assertIn('id="worker-binding-settings"', providers_panel)
        self.assertNotIn("Universe Conductor", providers_panel)
        self.assertNotIn("Project Masters", providers_panel)
        self.assertNotIn('id="project-provider-settings"', HTML)
        self.assertNotIn('id="provider-profile-dialog"', HTML)

    def test_hidden_view_and_bounded_tail_are_wired(self) -> None:
        self.assertIn('id="session-rail-show-hidden"', HTML)
        self.assertIn("providerChatShowHidden", APP)
        self.assertIn('/v1/session-observer/tail', APP)
        self.assertIn("window.setInterval(tailProviderSessions, 4000)", APP)
        self.assertIn("session-rail-row", CSS)

    def test_selected_anchor_session_receives_ephemeral_provider_tail(self) -> None:
        self.assertIn("providerChatRoomForSupervisorSession", APP)
        self.assertIn("providerLiveDelivery", APP)
        detail_slice = APP[
            APP.index("function renderSelectedSessionDetail") : APP.index(
                "async function api"
            )
        ]
        self.assertIn("Live provider output", detail_slice)
        self.assertIn("state.providerLiveDeltas[room.chat_key]", detail_slice)
        self.assertIn("session-detail-live", detail_slice)
        tail_slice = APP[
            APP.index("async function tailProviderSessions") : APP.index(
                "async function discoverProviderActivitySources"
            )
        ]
        self.assertIn("state.providerLiveDelivery[room.chat_key]", tail_slice)
        self.assertIn("mergeProviderLiveDeltasIntoRoom(room.chat_key)", tail_slice)
        self.assertIn("renderRoomMessages()", tail_slice)
        self.assertIn("renderSelectedSessionDetail();", tail_slice)
        self.assertIn("function mergeProviderLiveDeltasIntoRoom(chatKey)", APP)
        self.assertIn("session-detail-live-feed", CSS)
        self.assertLess(
            HTML.index('id="session-observatory-detail"'),
            HTML.index('id="session-observatory-list"'),
        )

    def test_master_session_can_rebind_to_registered_project(self) -> None:
        self.assertIn('id="session-working-directory-project"', HTML)
        self.assertIn('id="session-working-directory-apply"', HTML)
        self.assertIn("function renderSessionWorkingDirectory", APP)
        self.assertIn("async function rebindSelectedSessionWorkingDirectory", APP)
        self.assertIn("/working-directory`,", APP)
        rebind_slice = APP[
            APP.index("async function rebindSelectedSessionWorkingDirectory") : APP.index(
                "async function api"
            )
        ]
        self.assertIn("expected_version: session.row_version", rebind_slice)
        self.assertIn("await openPreparedProviderSession({", rebind_slice)
        self.assertNotIn("openProjectRoomStream", rebind_slice)
        self.assertIn("session?.provider_session_attached", APP)
        self.assertIn("session-working-directory", CSS)
        self.assertIn(".session-working-directory.hidden", CSS)

    def test_action_inbox_excludes_governance_approval_controls(self) -> None:
        self.assertNotIn("/v1/governance/proposals/", APP)
        self.assertNotIn("async function decideGovernanceProposal", APP)
        self.assertNotIn('decideGovernanceProposal(proposal, "APPROVE")', APP)
        self.assertNotIn('decideGovernanceProposal(proposal, "CANCEL")', APP)
        self.assertNotIn('"proposal-cancel", "Cancel"', APP)

    def test_actions_are_separate_from_chat_and_keep_scroll_stable(self) -> None:
        self.assertIn('id="action-inbox-button"', HTML)
        self.assertIn('id="action-inbox-dialog"', HTML)
        self.assertIn('aria-labelledby="action-inbox-title"', HTML)
        self.assertIn('id="action-inbox-list"', HTML)
        self.assertIn('aria-label="Actions by category"', HTML)
        self.assertIn('data-mobile-work-view="actions"', HTML)
        self.assertIn('id="mobile-action-inbox-badge"', HTML)
        self.assertIn("function renderActionInbox", APP)
        self.assertIn("function openActionInbox", APP)
        self.assertIn("function pendingActionItems", APP)
        self.assertIn("function pendingConversationPermissions", APP)
        self.assertIn("pendingConversationPermissions())", APP)
        self.assertIn("function finishRoomMessageRender", APP)
        self.assertNotIn('"Pending approvals"', APP)
        self.assertIn('"Active work"', APP)
        self.assertNotIn('"Recent activity"', APP)
        self.assertIn('"No active work."', APP)
        self.assertIn("Cross-session delivery is internal automation state", APP)
        self.assertIn("delegations: []", APP)
        self.assertIn("history: []", APP)
        self.assertIn("function renderProviderReplyActionCard", APP)
        self.assertIn("cancelProviderSessionTurn", APP)
        self.assertIn("CANCELLATION_REQUESTED", APP)
        self.assertIn("/v1/conductor-room/delegations/", APP)
        mobile_slice = APP[
            APP.index('elements.mobileWorkTabs?.addEventListener("click"') :
            APP.index("const activeGoal")
        ]
        self.assertIn('view === "actions"', mobile_slice)
        self.assertIn("openActionInbox()", mobile_slice)
        self.assertNotIn('openInspectorSurface("activity")', mobile_slice)
        provider_reply_slice = APP[
            APP.index("function renderProviderReplyActionCard") :
            APP.index("function renderActionInbox")
        ]
        self.assertNotIn("reply.content", provider_reply_slice)
        message_slice = APP[
            APP.index("function renderRoomMessages") : APP.index(
                "function renderComposerState"
            )
        ]
        self.assertNotIn("renderGovernanceProposalCard(proposal)", message_slice)
        self.assertNotIn("renderPermissionCard(permission)", message_slice)
        self.assertNotIn("scrollRoomToPendingAction", APP)
        self.assertIn("action-inbox-dialog[open]", CSS)
        self.assertIn("width: 100vw", CSS)
        self.assertIn("height: 100dvh", CSS)
        self.assertIn("overscroll-behavior-y: contain", CSS)

    def test_project_master_delivery_labels_distinguish_queue_and_acceptance(
        self,
    ) -> None:
        self.assertIn('deliveryState === "QUEUED_FOR_MASTER"', APP)
        self.assertIn('deliveryState === "ACCEPTED_BY_MASTER"', APP)
        self.assertNotIn("DELIVERED_TO_MASTER", APP)


    def test_meeting_room_feature_expected_path_ui_is_explicit_and_non_authoritative(
        self,
    ) -> None:
        self.assertIn('id="meeting-feature-controls"', HTML)
        self.assertIn('id="meeting-feature-select"', HTML)
        self.assertIn('id="meeting-feature-rationale"', HTML)
        self.assertIn("async function createFeatureForActiveMeeting", APP)
        self.assertIn("async function addArtifactAsExpectedPath", APP)
        self.assertIn("async function startExpectedPathGoal", APP)
        self.assertIn("async function materializeFeatureGoal", APP)
        self.assertIn("async function runActiveGoalWorkPlans", APP)
        self.assertIn("async function adoptGoalWorkPlan", APP)
        self.assertIn("async function applyGoalWorkPlan", APP)
        self.assertIn("async function attachMeetingProviderSession", APP)
        self.assertIn("async function runActiveFeatureMeeting", APP)
        self.assertIn("async function cancelActiveFeatureMeeting", APP)
        self.assertIn('id="meeting-provider-session-select"', HTML)
        self.assertIn('id="start-meeting-run-button"', HTML)
        self.assertIn('/provider-sessions', APP)
        self.assertIn('/meeting-runs', APP)
        self.assertIn('/feature-nodes', APP)
        self.assertIn('/expected-paths', APP)
        self.assertIn('/v1/actions', APP)
        self.assertIn('async function invokeServerAction', APP)
        self.assertIn('"feature.goal.start"', APP)
        feature_slice = APP[
            APP.index("async function refreshActiveRoomFeatures") : APP.index(
                "async function openMultiRoom"
            )
        ]
        self.assertIn("expected_feature_revision: feature.revision", feature_slice)
        self.assertIn("expected_path_digest: path.route_digest", feature_slice)
        self.assertIn('push_policy: "PUSH_PROHIBITED"', feature_slice)
        goal_start_slice = APP[
            APP.index("async function startExpectedPathGoal") : APP.index(
                "async function materializeFeatureGoal"
            )
        ]
        self.assertIn('invokeServerAction("feature.goal.start"', goal_start_slice)
        self.assertIn("feature_id: feature.feature_id", goal_start_slice)
        self.assertNotIn("actor:", goal_start_slice)
        self.assertNotIn("context:", goal_start_slice)
        self.assertNotIn("authority:", goal_start_slice)
        self.assertNotIn("mode:", goal_start_slice)
        self.assertNotIn("role:", goal_start_slice)
        self.assertNotIn("approval:", goal_start_slice)
        self.assertIn("Adopt + Start Goal", feature_slice)
        self.assertIn("result.automation?.surface?.automation_state", feature_slice)
        self.assertIn("path.artifact_revision", APP)
        self.assertIn("path.route?.steps?.length", APP)
        self.assertIn('"EXPECTED_PATH_STEP"', APP)
        self.assertIn('kind: "route-step"', APP)
        self.assertIn(".feature-path-route", CSS)
        self.assertIn("Generate Work Plans", feature_slice)
        self.assertIn("Apply Adopted Plan", feature_slice)
        self.assertIn("/work-plan-runs", feature_slice)
        self.assertIn("/work-plan-adoptions", feature_slice)
        self.assertIn("/work-plan-applications", feature_slice)
        self.assertIn("PLANNED Milestones and BACKLOG Todos", APP)
        self.assertIn("It never runs a Task Frame or changes Todo state.", APP)
        self.assertIn('/goals', feature_slice)
        self.assertIn('Create Goal', feature_slice)
        self.assertNotIn('/todos', feature_slice)
        self.assertIn(".meeting-feature-controls", CSS)

    def test_boss_room_history_and_task_frame_conversation_are_visible(self) -> None:
        self.assertIn('<small>Rooms</small>', HTML)
        self.assertIn('api("/v1/rooms?state=ALL")', APP)
        self.assertIn('multiRoomStateFilter: "OPEN"', APP)
        self.assertIn('function renderTaskFrameTimeline', APP)
        self.assertIn('source.addEventListener("task-frame"', APP)
        self.assertIn('snap.task_frame_timeline', APP)
        self.assertIn('.task-frame-timeline', CSS)

    def test_semantic_project_graph_is_separate_from_session_graph(self) -> None:
        self.assertIn('/semantic-graph', APP)
        self.assertIn('function buildSemanticProjectGraph', APP)
        self.assertIn('const galaxyEntityTypes = new Set([', APP)
        self.assertIn('galaxyEntityTypes.has(String(item.entity_type', APP)
        self.assertIn('elements.graphHint.textContent = `Galaxy ·', APP)
        self.assertIn('projection only', APP)
        self.assertIn('if (state.view === "semantic")', APP)

    def test_galaxy_and_fleet_project_shared_lineage_state(self) -> None:
        self.assertIn("function semanticDescendantIds", APP)
        self.assertIn("function semanticTodoIdsForGraphNode", APP)
        self.assertIn("const todoIds = semanticTodoIdsForGraphNode(state.selectedNode)", APP)
        self.assertIn("function todoProjectionForGraphNode", APP)
        self.assertIn("item.dataset.state = todo.state", APP)
        self.assertIn("todoLineageLabel(todo)", APP)
        self.assertIn('todo-item[data-state="IN_PROGRESS"]', CSS)
        self.assertIn("function semanticActivityItemsForGraphNode", APP)
        self.assertIn("Project activity ledger · filtered by semantic lineage", APP)
        self.assertIn("renderSemanticNodeActivity(state.selectedNode)", APP)
        self.assertIn(".activity-context", CSS)
        self.assertIn("...state.goalAutomationSurfaces", APP)
        self.assertIn("function todoOwnershipProjection", APP)
        self.assertIn("TASK_FRAME_TARGETS_SESSION_ANCHOR", APP)
        self.assertIn("renderTodoOwnership(todo)", APP)
        self.assertIn('row.dataset.bound = "false"', APP)
        self.assertIn("unbound_in_progress_count", APP)
        self.assertIn("Work ownership / ${ownershipSummary.task_frame_count}", APP)
        self.assertIn("live_host_count", APP)
        self.assertNotIn("live_pty_count", APP)
        self.assertNotIn("PTY unbound", APP)
        self.assertNotIn("PTY ${ownership.pty_state}", APP)
        self.assertIn("function todoAutomationControlProjection", APP)
        self.assertIn("GOAL_LINK_REQUIRED", APP)
        self.assertIn("WORK_PLAN_SCOPE_MISMATCH", APP)
        self.assertIn('surface.automation_state === "TASK_FRAME_READY"', APP)
        self.assertIn("A live Conductor Rust Host bridge is required", APP)
        self.assertIn("/automation/todo-selection", APP)
        self.assertIn('approval: "SELECT_TODOS"', APP)
        self.assertIn("renderTodoAutomationControl(todo)", APP)
        self.assertIn(".todo-ownership", CSS)
        self.assertIn(".todo-automation-control", CSS)

    def test_project_graph_labels_functional_nodes_with_seed_provenance(self) -> None:
        self.assertIn("Project Seed node", HTML)
        self.assertIn('projection_origin: "PROJECT_SEED"', APP)
        self.assertIn("projection_seed_id: projection?.seed_id", APP)
        self.assertIn("projection_source_ref: projection?.source?.ref", APP)
        self.assertIn('addDetail(grid, "Origin", "Project Seed")', APP)
        self.assertIn('addDetail(grid, "Seed source", data.projection_source_ref)', APP)

    def test_browser_does_not_render_raw_provider_paths(self) -> None:
        activity_slice = APP[
            APP.index("function renderProviderActivitySources") :
            APP.index("function prefillsObservatoryInjectForm")
        ]
        self.assertNotIn("source.source_path", activity_slice)
        rail_slice = APP[
            APP.index("function renderSessionRail") :
            APP.index("function renderSessionObservatory")
        ]
        self.assertNotIn("provider_session_ref", rail_slice)
        self.assertIn("sessionRailActivityLabel(room)", rail_slice)
        self.assertIn("room.last_activity_at", APP)
        self.assertIn('["BOUND", "ANCHOR_OBSERVED"]', rail_slice)
        self.assertIn('binding.is_default === true', rail_slice)
        self.assertIn('binding.observer_currentness === "CURRENT"', rail_slice)
        self.assertNotIn("binding.anchor_temporality", rail_slice)


    def test_provider_session_stream_requires_attached_identity(self) -> None:
        eligible_slice = APP[
            APP.index("function providerSessionRoomIsEligible") : APP.index(
                "function providerSessionRoomCacheFor"
            )
        ]
        openable_slice = APP[
            APP.index("function providerSessionRoomIsOpenable") : APP.index(
                "function providerSessionUnreadCount"
            )
        ]
        self.assertIn('identityState === "VERIFIED"', eligible_slice)
        self.assertIn("function providerSessionRoomIdentityIsAttached", eligible_slice)
        self.assertIn('identityState !== "SUPERVISOR_OBSERVED"', eligible_slice)
        self.assertIn('currentness === "CURRENT"', eligible_slice)
        self.assertIn("providerSessionRoomIdentityIsAttached(room)", openable_slice)

    def test_provider_session_background_events_keep_selected_transcript_focused(self) -> None:
        selection_slice = APP[
            APP.index("function syncSelectedProviderSessionState") : APP.index(
                "function mergeProviderSessionMessages"
            )
        ]
        self.assertIn("requestedKey && requestedKey !== selectedKey", selection_slice)
        self.assertIn("providerSessionRoomCacheFor(selectedKey)", selection_slice)

        payload_slice = APP[
            APP.index("function applyProviderSessionPayload") : APP.index(
                "function closeProviderSessionStream"
            )
        ]
        self.assertIn("markProviderSessionActivity(key, type, envelope)", payload_slice)
        self.assertIn("PROVIDER_SESSION_DELTA", payload_slice)
        self.assertIn("PROVIDER_SESSION_PERMISSION_RESOLVED", payload_slice)

        stream_slice = APP[
            APP.index("function openProviderSessionStream") : APP.index(
                "function openProviderChatSession"
            )
        ]
        self.assertIn("if (providerSessionRoomIsSelected(key))", stream_slice)
        self.assertIn("renderRoomMessages()", stream_slice)
        self.assertIn("renderSessionRail()", stream_slice)
        self.assertIn("providerSessionRoomIsOpenable(room)", stream_slice)
        self.assertNotIn(
            "state.providerSessionMessages = dedupeProviderSessionMessages",
            stream_slice,
        )
        self.assertIn("syncProviderSessionSubscriptions();", APP)
        self.assertIn("providerSessionActivityState(room)", APP)
        self.assertIn("providerSessionUnreadCount(room)", APP)

    def test_initial_refresh_does_not_wait_for_observatory_or_duplicate_terminal_discovery(
        self,
    ) -> None:
        refresh_slice = APP[
            APP.index("async function refresh(") : APP.index("function projectDisplayName")
        ]
        self.assertLess(
            refresh_slice.index("renderProjects();"),
            refresh_slice.index("void refreshSupervisorSessions().catch"),
        )
        self.assertNotIn("await refreshSupervisorSessions()", refresh_slice)
        self.assertEqual(refresh_slice.count("void loadTerminalTabs();"), 1)

        supervisor_slice = APP[
            APP.index("async function refreshSupervisorSessions") : APP.index(
                "async function tailProviderSessions"
            )
        ]
        self.assertNotIn('api("/v1/terminals")', supervisor_slice)
        # Only the initial refresh().finally() block is the startup path. The
        # service-health poll that follows may call loadTerminalTabs() on a
        # proven error->ready recovery, which is a distinct, non-startup trigger.
        startup_slice = APP[
            APP.index("refresh().finally(() => {") : APP.index(
                "window.setInterval(refreshConductorRoom"
            )
        ]
        self.assertNotIn("loadTerminalTabs()", startup_slice)

    def test_selected_projection_is_reused_and_completed_todos_leave_planning_inbox(
        self,
    ) -> None:
        select_slice = APP[
            APP.index("async function selectProject(") : APP.index(
                "function mergeGovernanceProposalInbox"
            )
        ]
        self.assertIn("state.projectionsByProject?.[projectId]", select_slice)
        self.assertIn(
            '(goalPlanResult.unassigned_todos || []).filter(',
            select_slice,
        )
        refresh_goal_slice = APP[
            APP.index("async function refreshGoalPlan()") : APP.index(
                "function planTodoRow"
            )
        ]
        self.assertIn('(result.unassigned_todos || []).filter(', refresh_goal_slice)
        self.assertIn('todo.state !== "DONE"', select_slice)
        self.assertIn('todo.state !== "DONE"', refresh_goal_slice)

    def test_mobile_conversation_launcher_stays_inside_the_composer_dock(
        self,
    ) -> None:
        selector = (
            "  .app-shell.mockup-shell > .graph-workspace > .conductor-panel "
            "> .conversation-layer {"
        )
        start = CSS.rindex(selector)
        block = CSS[start : CSS.index("  }", start)]
        self.assertIn("position: relative;", block)
        self.assertIn("inset: auto;", block)
        self.assertIn("width: 100%;", block)
        self.assertIn("min-width: 0;", block)
        self.assertIn("max-width: 100%;", block)

    def test_terminal_selection_preserves_the_explicit_chat_panel_width(self) -> None:
        self.assertNotIn("autoWidenForTerminal", TERM)
        self.assertNotIn('--chat-panel-width", "680px', TERM)
        self.assertIn("initChatPanelResize()", APP)

    def test_supervisor_refreshes_are_coalesced_and_recent_open_reuses_cache(
        self,
    ) -> None:
        refresh_slice = APP[
            APP.index("async function refreshSupervisorSessions") : APP.index(
                "async function tailProviderSessions"
            )
        ]
        self.assertIn(
            "if (state.supervisorRefreshPromise) return state.supervisorRefreshPromise",
            refresh_slice,
        )
        self.assertIn("state.supervisorRefreshedAt = Date.now();", refresh_slice)
        self.assertIn("state.supervisorRefreshPromise = refreshPromise;", refresh_slice)
        self.assertIn("state.supervisorRefreshPromise = null;", refresh_slice)
        open_start = APP.index("const openSessionObservatory = async () =>")
        open_slice = APP[open_start : APP.index("if (elements.sessionObservatoryDialog)", open_start)]
        self.assertIn(
            "refreshSupervisorSessions({ maxAgeMs: 10_000 })",
            open_slice,
        )

    def test_rooms_dialog_opens_before_settings_requests_finish(self) -> None:
        helper = APP[
            APP.index("async function openProviderSettings") : APP.index(
                "function setDialogCategoryTab"
            )
        ]
        self.assertLess(
            helper.index("elements.settingsDialog.showModal()"),
            helper.index("await Promise.all"),
        )
        self.assertIn("if (!elements.settingsDialog.open)", helper)

    def test_mobile_observatory_is_labeled_opaque_and_single_scroll(self) -> None:
        self.assertIn(
            'id="session-observatory-dialog" class="session-observatory-dialog wide-dialog" aria-labelledby="session-observatory-title"',
            HTML,
        )
        self.assertIn('id="session-observatory-title"', HTML)
        tab_helper = APP[
            APP.index("function setDialogCategoryTab") : APP.index(
                "function setSettingsTab"
            )
        ]
        self.assertIn('tab.setAttribute("aria-controls", panel.id)', tab_helper)
        self.assertIn('panel.setAttribute("aria-labelledby", tab.id)', tab_helper)

        open_selector = ".session-observatory-dialog[open] {"
        open_start = CSS.index(open_selector)
        open_block = CSS[open_start : CSS.index("}", open_start)]
        self.assertIn("display: flex;", open_block)
        self.assertIn("overflow: hidden;", open_block)
        self.assertIn("background: #17181b;", CSS)

        panels_selector = ".session-observatory-dialog > .observatory-tab-panels {"
        panels_start = CSS.index(panels_selector)
        panels_block = CSS[panels_start : CSS.index("}", panels_start)]
        self.assertIn("min-height: 0;", panels_block)
        self.assertIn("max-height: none;", panels_block)
        self.assertIn("overflow-y: auto;", panels_block)

        list_start = CSS.index(".session-observatory-list {")
        list_block = CSS[list_start : CSS.index("}", list_start)]
        self.assertIn("max-height: none;", list_block)
        self.assertIn("overflow: visible;", list_block)

        mobile_dialog_start = CSS.index("  .session-observatory-dialog[open] {")
        mobile_dialog_block = CSS[
            mobile_dialog_start : CSS.index("  }", mobile_dialog_start)
        ]
        self.assertIn("width: 100vw;", mobile_dialog_block)
        self.assertIn("height: 100dvh;", mobile_dialog_block)
        mobile_tabs_start = CSS.index(
            "  .session-observatory-dialog > .dialog-tabs {",
            mobile_dialog_start,
        )
        mobile_tabs_block = CSS[
            mobile_tabs_start : CSS.index("  }", mobile_tabs_start)
        ]
        self.assertIn("flex-wrap: nowrap;", mobile_tabs_block)
        self.assertIn("overflow-x: auto;", mobile_tabs_block)

    def test_memory_candidate_keep_has_explicit_rag_adoption_action(self) -> None:
        self.assertIn('async function adoptMemoryCandidate(candidate)', APP)
        self.assertIn('invokeServerAction("rag.adopt", {', APP)
        self.assertIn("expected_candidate_digest: candidate.candidate_digest", APP)
        review_start = APP.index("function renderMemoryCandidateReview()")
        review_end = APP.index("function renderMemory()", review_start)
        review = APP[review_start:review_end]
        self.assertIn("KEEP marks a candidate only", review)
        self.assertIn('candidate.state === "KEEP" && candidate.kind === "MEMORY"', review)
        self.assertIn('"Adopt to RAG"', review)

    def test_direct_decision_registration_uses_the_action_gateway(self) -> None:
        render_start = APP.index("function renderMemory()")
        render = APP[render_start:]
        self.assertIn('invokeServerAction("rag.record-decision", {', render)
        self.assertIn("project_id: state.selectedProject.project_id", render)
        self.assertIn("decision_ref: decisionRef.value.trim()", render)
        self.assertIn("node_ref: nodeRef", render)
        self.assertIn('state: "BRAINSTORM"', render)
        self.assertIn("RAG_DECISION_RECORDED", render)

    def test_memory_batch_run_uses_the_common_action_surface(self) -> None:
        run_start = APP.index('const run = node("button", "primary-button compact-action", "Run stage")')
        run_end = APP.index("actions.append(save, run);", run_start)
        run_slice = APP[run_start:run_end]
        self.assertIn('invokeServerAction("memory.batch.run", {', run_slice)
        self.assertIn("project_id: state.selectedProject.project_id", run_slice)
        self.assertNotIn("memory-batches/run`", run_slice)

if __name__ == "__main__":
    unittest.main()
