# Universe UI — feature inventory & placement

Where every action/feature lives in the new shell IA
(`rag/universe-shell-ia-and-galaxy-view`). Status as of this session.

Shell regions:

```
┌ TOP BAR ─────────────────────────────────────────────────────────┐
│ ● Universe   [ search ]              server · mode · ⚙ ↻ · account │
├──────┬──────────────┬──────────────────────────────┬──────────────┤
│ RAIL │ CONTEXT      │  MAIN (current view)         │ DETAIL /     │
│ view │ panel        │                              │ inspector    │
│ 8 px │ (per view)   │                              │ (follows sel)│
│ ...  │              ├──────────────────────────────┤              │
│      │              │  TERMINAL DOCK (right | bottom)              │
└──────┴──────────────┴─────────────────────────────────────────────┘
```

## 1. Views — utility rail (left, 62 px, icon + label)

| view | current | target | notes |
|---|---|---|---|
| **Fleet** | `showGoalPlanView` + board toggle | ✅ default home; kanban 6 lanes | `body.fleet-mode` strips Goal-Plan chrome |
| **Galaxy** | `showGraphView("semantic")` → `buildUnifiedGalaxyGraph` | ✅ full-screen view mode | view switcher chips + Esc |
| **Activity** | `showGraphView("timeline")` / inspector Activity tab | ⏳ needs its own centre view (immutable event log) | today it is a graph mode + an inspector tab |
| **Docs** | `showGraphView("documents")` | ⏳ ok as a graph mode; could be a list/reader | |
| **Memory** | inspector tab (`openInspectorSurface`) | ⏳ dedicated screen like Bench, OR keep as inspector | RAG memory list + candidates + batch stages |
| **Bench** | ✅ dedicated centre screen (`showBenchScreen`) | ✅ done | |
| **Rooms** | `openProviderSettings` (settings → rooms tab) | ⏳ own view — meeting/boss rooms are primary surfaces | currently buried in Settings |
| **+ Project** | `openFreshProjectWizard` / `#fresh-project-dialog` | ⏳ keep as a wizard dialog, launched from the rail | see §4 |

## 2. Top bar

| item | keep? | placement |
|---|---|---|
| ● Universe brand | keep | left, no subtitle (done) |
| global search | keep | centre, max ~420 px |
| 목록 (public universe list / rendezvous) | keep | right cluster, small text link |
| service status dot ("Universe Server · :port") | keep | right |
| mode label ("UNIVERSE / CONDUCTOR") | keep, small | right |
| ⚙ Settings | keep | right |
| ↻ Refresh | keep | right |
| account chip (UC) | keep | far right |
| ~~primary-nav (Work Spine / Timeline / …)~~ | **removed** | duplicated the rail (done) |
| ~~todo ☑ / observatory ◎ / start-project ✦ / release ▦~~ | **removed from bar** | reachable from rail / Settings (done) |

## 3. Dialogs (13) — keep as modal `<dialog>`, themed via tokens

| dialog | launched from | keep as dialog? |
|---|---|---|
| `goal-dialog` | Fleet "Add goal", `openGoalEditor` | yes — quick create/edit |
| `milestone-dialog` | goal card → add milestone | yes |
| `todo-dialog` | Fleet card / `openTodoDialog` / `openPlanTodos` | yes — create/edit todo |
| `project-dialog` | rail "+ Project" register, `openEditor` | yes — register existing project |
| `fresh-project-dialog` | rail "+ Project" new, `openFreshProjectWizard` | yes — multi-step wizard (§4) |
| `settings-dialog` | ⚙ | yes — tabbed (§5) |
| `new-session-dialog` | context panel / conductor "New session" | yes — provider/model/effort |
| `node-session-action-dialog` | session card → actions | yes — Use / Reconnect / Call Master / stop |
| `session-observatory-dialog` | (was topbar ◎) → rail? Settings? | **decide**: fold into the context panel's SESSIONS section, or keep as a dialog reachable from there |
| `session-bus-dialog` | conductor inbox | keep |
| `session-summary-dialog` | session card → summary / resume | keep |
| `action-inbox-dialog` | "Actions" button (terminal dock header) | keep |
| `release-dialog` | (was topbar ▦) | keep, launch from Settings or a Docs/Delivery view |

## 4. Fresh-project flow ("+ Project")

`openFreshProjectWizard` → `#fresh-project-dialog`, plus `openConductorFreshProjectDraft`
(a Conductor-room path). Design intent: **Intent → Meeting Room → Expected Paths →
adopt → Goals/Todos**. Placement:

- Rail "+ Project" opens a small chooser: **Register existing** (`project-dialog`)
  vs **New from intent** (`fresh-project-dialog` wizard).
- The wizard's later stages (meeting, path adoption) belong in the **Rooms** view,
  not a modal — the modal only captures the initial intent + project root.

## 5. Settings dialog — tabs

Current tabs incl. **rooms** (meeting-room config), provider setting, memory-batch
config, service settings. Target:

- **Rooms** → promote out of Settings into its own **Rooms** view (§1).
- Keep in Settings: providers/models catalog, memory-batch stages, service
  (loopback port, maintain interval), theme.

## 6. Conductor panel (right, above terminal)

`#conductor-panel` = greeting + metric-row (Projects / Todos / Dispatches / Service)
+ ghost-actions (Open Multiverse Map / Sync Peers / Open Future tab / Dispatch
Board) + the conversation/terminal layer.

- **Greeting + metrics**: move to the **Fleet** context panel or a small
  status strip — they are project-wide status, not chat.
- **Ghost-actions**: redundant — Multiverse Map = Galaxy, Future = inspector tab,
  Dispatch Board = Fleet. Drop them.
- **Conversation + terminal**: this is the terminal dock (§7).

## 7. Terminal dock

- Right column (default) OR bottom (`body.terminal-bottom`, "▼/▶" toggle) — ✅ done.
- Modes: single / tabs / **grid** (all sessions) — ✅ grid done.
- ⏳ hover-peek popover on a Fleet card's session badge → liveness + last PTY
  lines. Blocked: needs `task_frame_id → terminal_id / liveness` in the
  projection or a small endpoint.
- ⏳ click a Fleet card's session badge → open that PTY in the dock. Same link gap.

## 8. Per-view context panel (2nd column)

Currently the "NODE MODES" tree (projects + MASTER/CONDUCTOR + session counts —
✅ cleaned, no card dump). Target: **the panel's content changes with the view**:

| view | context panel shows |
|---|---|
| Fleet | project scope list · filters (mine / blocked / active agent / needs me) · SESSIONS summary |
| Galaxy | (nothing — full-screen) |
| Rooms | room list |
| Memory | link-state / node filters |
| Bench | group-by (skill/model/provider/project) |

The mode tree itself moves into the Fleet context panel's "scope" section.

## 9. Inspector / detail rail (right of MAIN)

- Fleet: ⏳ a **Fleet-native detail rail** — select a card → Task Frame detail
  (milestone, owner node, assignment, boss, evidence, session). Not the generic
  inspector.
- Galaxy: node inspector lives *inside* Galaxy (rollup card).
- Other views: the generic inspector (Details / Activity / Memory / Future tabs)
  as a follow-selection drawer.

## Open decisions for the user

1. **Activity** & **Memory** & **Rooms** — dedicated centre views, or keep as
   inspector tabs / graph modes? (Bench got its own screen; these three are the
   same question.)
2. **Session observatory** — fold into the Fleet context panel's SESSIONS
   section, or keep the dialog?
3. **Conductor greeting + metrics** — keep a status strip, or drop entirely?
4. **"+ Project"** chooser — one dialog with a Register/New toggle, or two rail
   entries?
