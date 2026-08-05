# Universe UI Design (merged)

> **Career defines the laws. Universe distributes and instantiates them.
> Projects live under those laws, and Universe instances connect to form the Multiverse.**

Status: active product UI design source  
Sources: `C:\workspace\image\b\DESIGN.md` (IA/product rules primary),
`C:\workspace\image\a\DESIGN.md` (visual tokens/surface recipes)  
Scope: web SPA (`tools/universe_ui`) and future Tauri shell  
Not: Career as central node; Project ownership by Universe

---

## 1. Product model (from b)

```text
Career              = laws / contracts / protocols (status, not a UI node)
Universe Distribution = portable/installer packages
Universe Instance   = local independent world (this app process)
Project             = independent repo/runtime; Universe observes via Projection
Multiverse          = peer Universe Instances (no master Universe)
```

Absolute rules:

- Do not draw Career as sun/core/server/product node.
- Do not draw Project as owned/absorbed by Universe.
- "Connect project" means discover → compatibility → projection → registry → observe.
- Selected focus is viewport convenience only, not authority.

---

## 2. Information architecture (from b)

Primary views:

```text
NETWORK | ECOSYSTEM | PROJECT | EXPERIENCE
```

Secondary (implemented surfaces):

```text
MEMORY | FUTURE | BENCH | DISPATCH | SETTINGS
```

Multi-room chat product model (Project / Boss / Meeting rooms, dashboard,
function-first UI before major redesign) lives in
`docs/multi-room-chat-architecture.md`. Session Observatory remains one session
entry; session-ref inject and room attach are first-class and must not depend
on Observatory discoverability alone.

Current SPA mapping:

| Design view | Current surface |
|-------------|-----------------|
| ECOSYSTEM | Project rail + project list |
| PROJECT | Graph workspace (timeline/universe/documents) |
| EXPERIENCE / BENCH | Inspector Bench tab |
| MEMORY | Inspector Memory tab |
| FUTURE | Inspector Future tab |
| DISPATCH | Conductor / Project Room composer |
| NETWORK | Future: peer map (placeholder nav for now) |

---

## 3. Application shell (desktop ≥ 1280px)

```text
┌─────────────────────────────────────────────────────────────┐
│ Top bar: brand · primary nav · law strip · status · actions │
├────────────┬──────────────────────────────┬─────────────────┤
│ Left rail  │ Main workspace (canvas)      │ Inspector       │
│ Projects   │ Topology / timeline / docs   │ Details tabs    │
│ Views      │                              │                 │
│ Local meta │ Conductor dock (composer)    │                 │
└────────────┴──────────────────────────────┴─────────────────┘
```

Widths:

```text
Left rail     240–280px
Inspector     320–360px
Top bar       56px
Main          remaining
```

Tablet/mobile: keep existing collapse rules; mobile prefers list/sheet over dense topology.

---

## 4. Visual tokens (from a, adapted)

```css
:root {
  --bg-0: #020711;
  --bg-1: #06101d;
  --bg-2: #091727;
  --panel: rgba(7, 20, 34, 0.82);
  --panel-strong: rgba(8, 25, 42, 0.94);
  --line-soft: rgba(126, 180, 220, 0.10);
  --line-default: rgba(126, 180, 220, 0.18);
  --line-strong: rgba(126, 220, 255, 0.34);
  --text-1: #eaf6ff;
  --text-2: #afc5d8;
  --text-3: #6f879b;
  --cyan: #3ee7e3;
  --blue: #55a7ff;
  --violet: #9a6bff;
  --amber: #f2b94b;
  --green: #43d98b;
  --red: #ff657a;
  --focus-ring: rgba(62, 231, 227, 0.45);
  --panel-radius: 12px;
  --control-height: 36px;
}
```

Semantic colors: cyan=selection/local active; blue=info/remote; violet=memory/candidate;
amber=pending/degraded; green=verified; red=blocked/violation only.

Avoid: neon everywhere, game launcher CTAs, Career-as-sun, perpetual particles.

---

## 5. Interaction and state

- One primary selection; inspector follows selection.
- Status uses color + label (+ icon when possible).
- Conductor is command surface, not authority.
- Law strip shows Career contract compatibility as status chips, not a nav item.

---

## 6. Implementation phase 1 (this change)

1. Land this document in `docs/universe-ui-design.md`.
2. Restyle SPA shell to desktop grid (rail · workspace · inspector).
3. Apply token set and panel surfaces.
4. Add top primary nav stubs wired to existing surfaces where possible.
5. Keep graph canvas and APIs unchanged.

Later: Network peer map, full Experience pipeline view, Tauri chrome.

## 7. Visual reference (ChatGPT mockup)

Primary visual target (2026-07-31):

```text
C:\workspace\image\ChatGPT Image 2026년 7월 31일 오후 11_35_21.png
```

Implemented toward mockup:

- deep starfield canvas (navy, not green)
- left Universes rail + Views + Session
- top Network/Ecosystem/Timeline/Memory/Future + search
- floating Conductor glass panel with metrics + message dock
- right Inspector hero + laws strip
- bottom status bar
- cyan/blue/violet glow accents; selected controls use dark fills (no white-on-white)

