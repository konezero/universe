# Universe UI — feature inventory & placement

Where every action/feature lives in the shell IA
(`rag/universe-shell-ia-and-galaxy-view`). Product intent reconciled 2026-09-15.
Historical implementation checkmarks below are dated observations, not proof
that the current product satisfies the target.

The current baseline is [shared human/LLM authoring and surface roles](universe-design-and-bench-flow.md#shared-human-and-llm-authoring):
Fleet exposes goals, plans, existing nodes, detailed Todos and kanban;
predictions are displayed only in Galaxy. All editable UI fields have both
human and LLM input paths over the same objects and revisions.

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
| **Fleet** | `showGoalPlanView` + integrated project/node/Todo/kanban home | Goal/program description -> plan -> existing nodes -> detailed Todos -> kanban, plus node team/session assignment | Fleet is the operating surface: bind one Master Persona to a Feature Node, create or bind Master/Worker sessions, and open their terminal tabs |
| **Galaxy** | `showGraphView("semantic")` -> `buildUnifiedGalaxyGraph` | Graph and the sole prediction/future-path display | Existing prediction UI elsewhere must be aligned |
| **Activity** | dedicated centre screen reading only the authoritative project event ledger; repeated identical session-start observations collapse into one counted row and read failure is explicit | Work-change view for planning, assignment, execution, artifact, validation, failure and recovery events linked to node/Todo/Task Frame/Session lineage | Remaining coverage is event production and contextual navigation; raw terminal/tool/polling noise stays out |
| **Docs** | `showGraphView("documents")` | ⏳ ok as a graph mode; could be a list/reader | |
| **Memory** | inspector tab (`openInspectorSurface`) | ⏳ dedicated screen like Bench, OR keep as inspector | RAG memory list + candidates + batch stages |
| **Persona** | definition CRUD and session/node assignment are mixed in one screen | Persona Library only: reusable Master/Worker/Reviewer definitions, skills, model preferences, budget/escalation rules, versions and archive state | Operational assignment moves to Fleet; Persona describes behavior and never creates authority |
| **Bench** | ✅ dedicated centre screen (`showBenchScreen`) | ✅ done | |
| **Rooms** | `openProviderSettings` (settings → rooms tab) | ⏳ own view — meeting/boss rooms are primary surfaces | currently buried in Settings |
| **+ Project** | `openFreshProjectWizard` / `#fresh-project-dialog` | Shared draft in the upper work area with conversation available | Human, LLM, and direct-entry paths; see §4 |

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
| `fresh-project-dialog` | current rail "+ Project" new | current implementation; target is the shared draft plus conversation (§4) |
| `settings-dialog` | ⚙ | yes — tabbed (§5) |
| `new-session-dialog` | context panel / conductor "New session" | yes — provider/model/effort |
| `node-session-action-dialog` | session card → actions | yes — Use / Reconnect / Call Master / stop |
| `session-observatory-dialog` | (was topbar ◎) → rail? Settings? | **decide**: fold into the context panel's SESSIONS section, or keep as a dialog reachable from there |
| `session-bus-dialog` | conductor inbox | keep |
| `session-summary-dialog` | session card → summary / resume | keep |
| `action-inbox-dialog` | current terminal dock "Actions" button | **remove** — ACP-era approval/activity inbox is redundant once user-attention state is projected on the owning terminal tab; migrate Git history to Activity |
| `release-dialog` | (was topbar ▦) | keep, launch from Settings or a Docs/Delivery view |

## 4. Fresh-project flow ("+ Project")

Current source uses `openFreshProjectWizard`, `#fresh-project-dialog`, and a
Conductor draft path. The wizard asks for a route before composition. This is
an implementation gap, not the intended universal creation flow.

The target keeps the editable project draft visible in the upper work area and
the LLM conversation available at the same time. A conversation request can open
and populate the draft; manual entry can start or continue the same draft.
Human edits are visible to the LLM, and LLM edits update the open UI. Goal,
program description, cases, structure, capabilities, and validation planning
are refined together rather than copied from chat into a separate form.

- Existing-project connection remains available and reuses registered nodes.
- A new project does not require prediction or Expected Path selection.
- Relevant Bench/RAG cases may inform planning; forecast display remains in Galaxy.
- Node creation supports manual entry, LLM operations, and Memory-derived proposals.
- Rooms can support collaborative planning without requiring every project to
  visit a Meeting Room or select a forecast before its first goal can exist.

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
- **Ghost-actions**: redundant — Multiverse Map and prediction/Future navigation
  belong to Galaxy; Dispatch Board belongs to Fleet. Retire duplicate forecast
  controls in the Inspector when aligning the UI.
- **Conversation + terminal**: this is the terminal dock (§7).

## 7. Terminal dock

- Right column (default) OR bottom (`body.terminal-bottom`, "▼/▶" toggle) — ✅ done.
- Modes: single / tabs / **grid** (all sessions) — ✅ grid done.
- Every tab represents one exact Session Anchor and displays its authoritative
  node, role/Persona, Todo or Task Frame, and Host/provider state. Required
  labels include working, waiting for input, approval required, quota blocked,
  failed, completed, disconnected and unassigned; color is supplemental only.
- User-attention state appears on the owning terminal tab. There is no separate
  terminal-header Actions inbox. Clicking the marked tab opens the real waiting
  terminal position. Current-turn cancel, stop, reconnect and handoff remain
  controls of the selected terminal and belong in its contextual overflow menu.
- Fleet is the primary assignment entry. A Feature Node offers **New session**
  and **Bind existing session**. New-session input is pre-bound to the selected
  project/node; existing-session choices are server-declared eligible sessions,
  never cross-project or inferred fallback matches.
- A Feature Node has one active Master owner and may have multiple Worker or
  Reviewer execution sessions. The node's Master Persona selects bounded Worker
  Personas, skills, provider/model/effort and Todo/Task Frame assignments. Persona
  text describes behavior; the node/Todo/Task Frame binding limits authority.
- The Persona view is the reusable Persona Library. Session and node assignment
  controls move to Fleet. The terminal shows the applied Persona and binding but
  is not the primary organization editor.
- Fleet session badges and terminal tabs are bidirectional: Fleet opens the exact
  terminal; the terminal's node label opens the exact Fleet node. Missing binding
  is shown as `UNASSIGNED` and is never guessed.

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

Activity is no longer open: it is a dedicated centre view backed by an immutable,
node-linked project event projection. Persona is a reusable registration library;
operational session assignment belongs to Fleet. The terminal-header Actions inbox
is retired in favor of status on each exact terminal tab.

1. **Memory** & **Rooms** — dedicated centre views, or keep as inspector tabs /
   graph modes?
2. **Session observatory** — fold into the Fleet context panel's SESSIONS
   section, or keep the dialog?
3. **Conductor greeting + metrics** — keep a status strip, or drop entirely?
4. **"+ Project"** chooser — one dialog with a Register/New toggle, or two rail
   entries?
