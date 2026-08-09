# Universe Install Mode (fixed)

Status: **FIXED** design decision  
Schema: `universe.install-mode.v1`  
Related: `docs/universe-packaging.md`, `docs/universe-runtime-host.md`, `docs/local-universe-service.md`

This document locks how products install and boot relative to the Universe
host. Implementation may lag; behavior must not drift from these rules.

---

## 1. Boot authority (fixed)

**Default and product path = Model B**

```text
Universe Host boots
  → host runtime + career-bound core/skills (one machine / one host)
  → attach project(project_id, project_root)
  → fix write/read project scope = project_root (PWD / cwd)
  → Project Master / agent session bound to that root
```

| Role | Owns |
|------|------|
| **Universe Host** | Boot, runtime process, providers, rooms, multi-project observe |
| **Career source** | Shared core / law / reference runtime **origin** (source-bound) |
| **Project** | `project_root`, instance state, project policy, install binding |

**Not the default path (Model A):** each project boots its own OS from a full
local `.ai/core` + `.ai/runtime` copy and optionally “looks up” Universe.

Historical full trees under a project `.ai/` are **disk legacy**, not boot
authority, unless `install_mode` is explicitly standalone (below).

---

## 2. Install modes (fixed enum)

Recorded on the project (and optionally mirrored in Universe DB metadata).

```text
install_mode:
  UNIVERSE_ATTACHED   # default
  PROJECT_STANDALONE  # explicit opt-in
```

### 2.1 `UNIVERSE_ATTACHED` (default)

Install / connect **through Universe**.

**Host receives**

- Local Universe service (tray / `universe_server`)
- Host profile, state DB, loopback API
- Binding to Career source when present (`CAREER_SOURCE` anchor)

**Project receives**

- Registration: `project_id` + `project_root`
- Project-local instance surfaces only (session/anchor/inbox/memory as used)
- Project policy and Universe seed surfaces under the project as today
- **`install_binding` record** (see §3)
- **No requirement** to ship a full private core/runtime tree for boot

**Work**

```text
detect host READY → attach(cwd|project_root) → work
```

UI: select project = attach + scope bind (not mini-OS reboot).  
CLI: same host; `cwd` is the project root.

### 2.2 `PROJECT_STANDALONE` (opt-in)

Install **into the project** so work is possible **without** a running
Universe host.

**Project receives**

- `install_binding` with `install_mode: PROJECT_STANDALONE`
- Runtime **pin** (digest / release id)
- Thin boot entry that loads **pinned** runtime (machine-global cache preferred;
  embedded bundle only if the pack explicitly chose “embed runtime”)
- Instance state + project policy

**Not implied**

- Silent dual boot with a live host using a different core tree
- Treating standalone as the product default

**Work without host**

```text
standalone boot entry → pin-resolved runtime → PWD = project_root → work
```

**Work with host also present**

```text
prefer_boot: HOST | STANDALONE   # default HOST when host READY
```

Default **`prefer_boot: HOST`**: if Universe host is READY, attach to host
even for a standalone-capable project. Avoid two live authorities.

---

## 3. Install binding record (fixed shape)

Canonical path (project-owned):

```text
.ai/universe/install_binding.json
```

Schema id: `universe.install-binding.v1`

```json
{
  "schema": "universe.install-binding.v1",
  "install_mode": "UNIVERSE_ATTACHED",
  "prefer_boot": "HOST",
  "project_id": "GCS",
  "project_root": ".",
  "runtime_pin": {
    "kind": "CAREER_RELEASE_OR_HOST",
    "release_id": null,
    "manifest_digest": null,
    "note": "Filled by installer; host path may use host-current when null"
  },
  "career_source": {
    "project_id": "ai-career",
    "role": "CAREER_SOURCE"
  },
  "universe_host": {
    "discovery": "LOCAL_SERVER_JSON",
    "state_file_hint": "%LOCALAPPDATA%\\Universe\\server.json"
  },
  "standalone": {
    "enabled": false,
    "embed_runtime": false,
    "boot_entry": null
  },
  "updated_at": "2026-08-06T00:00:00Z"
}
```

| Field | Rule |
|-------|------|
| `install_mode` | Required. `UNIVERSE_ATTACHED` \| `PROJECT_STANDALONE` |
| `prefer_boot` | `HOST` (default) \| `STANDALONE`. Only meaningful if standalone capable |
| `runtime_pin` | Required for standalone; optional for attached (may track host-current) |
| `standalone.embed_runtime` | `true` only when pack chose embedded bundle |
| `standalone.boot_entry` | Relative path to standalone entry skill/doc when enabled |

Agents and CLI **must read this file** (when present) instead of guessing.

Missing file ⇒ treat as **`UNIVERSE_ATTACHED`** + **`prefer_boot: HOST`**
(product default; legacy repos).

---

## 4. Install pack / wizard (fixed choices)

Installer UI (or CLI flags) **must** present exactly one primary choice:

### Wizard copy (KO)

```text
이 프로젝트를 어떻게 설치할까요?

○ Universe에 연결해 설치 (권장)
  - 이 PC의 Universe 호스트가 런타임을 제공합니다
  - 프로젝트 폴더는 작업 루트(PWD)로 붙습니다
  - 코어/런타임 전체 복사 없이 업데이트는 호스트·Career 쪽

○ 이 프로젝트만 독립 설치
  - Universe 없이도 이 폴더에서 CLI 작업 가능
  - 런타임 핀(및 선택 시 임베드)을 프로젝트에 기록합니다
  - 호스트가 켜져 있으면 기본은 호스트 우선(prefer_boot=HOST)
```

### Wizard copy (EN)

```text
How should this project be installed?

○ Install via Universe (recommended)
  - This machine’s Universe host provides runtime
  - The project folder is attached as the work root (PWD)
  - Core/runtime updates stay on the host / Career source

○ Standalone project install
  - CLI work works without Universe
  - Records a runtime pin (optional embed) on the project
  - If a host is READY, prefer host boot by default
```

### CLI flags (normative names)

```text
universe install --mode universe-attached   # default
universe install --mode project-standalone [--embed-runtime]
universe install --mode project-standalone --prefer-boot standalone
```

Packaging scripts may use the same mode names as parameters.

---

## 5. Host detection (fixed, for CLI and agents)

Before project work outside the UI:

```text
1. Read %LOCALAPPDATA%\Universe\server.json (endpoint, pid, token)
2. Confirm pid still refers to a live host process when present
3. GET {endpoint}/health → status READY
```

| Result | Action |
|--------|--------|
| READY | `attach(project_root)` then work |
| Stale state file | clear/reap → ensure-host or fail clearly |
| Missing / not READY | if `install_mode=UNIVERSE_ATTACHED` → **ensure-host** (or instruct user to start tray); if `PROJECT_STANDALONE` and `prefer_boot=STANDALONE` → standalone entry; if standalone + `prefer_boot=HOST` → ensure-host first, else fallback standalone with explicit log |

**Rule:** Do not use project `.ai/core` as boot authority for
`UNIVERSE_ATTACHED`. Do not silently mix two cores when host is READY.

Target UX command (name locked for docs; implementation may alias):

```text
universe work .
  → detect/ensure host per install_binding
  → attach cwd
  → print env hints (endpoint, project_id, write_root)
  → exit 0 for shell/agent continuation
```

---

### Current preflight implementation

The local service now provides the non-mutating half of this command:

```text
python tools/universe_server.py work <project_root> [--project-id <PROJECT_ID>]
```

It reads the host state, installed Runtime marker, Project binding, and the
exact integration proposal. It never registers a Project, writes `.universe/`
or `.ai/`, starts a Runtime, or consumes an approval. Its result is one of:

- `UNIVERSE_WORK_READY` — host and local install binding agree; work may use
  the host with the returned `cwd` and `project_id`.
- `CAREER_OS_INSTALL_REQUIRED` — the Project has no local Career Runtime
  installation yet.
- `PROJECT_INTEGRATION_APPROVAL_REQUIRED` — the exact Universe integration
  proposal must be approved and applied by the Project Lifecycle Host.
- `PROJECT_REGISTRATION_REQUIRED` or `UNIVERSE_HOST_NOT_READY` — attach has
  not reached a usable host state.

The full product alias may later add explicit `ensure-host` and approved apply
steps, but it must retain this preflight boundary.

### 5.1 Fresh-clone attach/install verification slice

The fresh-clone path is an explicit state machine implemented by
`tools/project_install_flow.py`:

```text
PREFLIGHT -> PLAN_READY -> APPLYING -> ARTIFACTS_VERIFIED -> READY_FOR_BOOT
                                      \-> BLOCKED
```

`plan` and `preflight` are read-only. They require a full 40-character
immutable `ai-career` source commit and inspect whether the project has no
`.ai`, a managed installation, or a partial/invalid installation. A partial
`.ai` surface produces `BLOCKED`; it is never silently overwritten.

The apply function receives a caller-supplied lifecycle adapter. Universe does
not create Runtime files. The adapter request carries the exact project root,
install mode, operation (`OS_INSTALL` or `OS_UPDATE`), and immutable source
commit. The adapter must return all of the following before the flow can claim
`READY_FOR_BOOT`:

- `result: PASS`, `repository_runtime: VERIFIED`, and the exact target root;
- the exact install mode and operation;
- the exact live `ai-career` source commit;
- `boot_handoff.status: READY_FOR_BOOT`;
- the managed path list used to protect pre-existing project files.

The verifier then checks the installed Runtime manifest, project identity,
source commit, required installation artifacts, and every pre-existing file
outside the adapter-declared managed paths. A positive or false READY response
from an adapter is rejected when any check fails. The adapter is responsible
for the actual OS lifecycle mutation and remains the only component allowed to
materialize `.ai`.

---

## 6. What lives where (fixed inventory)

### Always project-local

- Source tree under `project_root`
- `.ai/runtime/project_instance/` (and mode registry owner identity)
- Session / anchor / handoff / inbox / memory as product uses them
- Project-owned skills, adapters, policies
- `.ai/universe/install_binding.json`
- `.ai/universe/TODO_TRACKING_POLICY.md` — Universe host queue vs project board  
  (see `docs/universe-todo-tracking-policy.md`; Career plant)
- Projection seed under `.ai/universe/` when published

### Host / Career (shared)

- Reference runtime engine
- Shared common skills and core contracts
- Provider process ownership (Universe Runtime Host)
- Multi-project rooms, supervisor, UI

### Standalone-only extras

- Resolved pin material or embed under a **clearly marked** cache/bundle path
- Standalone boot entry document/script

---

## 7. Mode conversion (must remain possible)

| From → To | Meaning |
|-----------|---------|
| Standalone → Attached | Register/attach to host; set `install_mode=UNIVERSE_ATTACHED`; optional strip of embed |
| Attached → Standalone | Export pin (+ optional embed); set `install_mode=PROJECT_STANDALONE` |

No one-way lock. Conversion writes a new `install_binding` revision.

---

## 8. Network anchors vs work projects (UI/product)

Auto-registered **anchors** (`UNIVERSE_HOME`, `CAREER_SOURCE`) are
infrastructure, not default “work projects.”

- Work list / default selection: ordinary attached projects (e.g. product apps)
- Anchors: home / laws source; not the primary todo board target
- Multiverse may show anchors as icons; do not treat Career core dump as
  the user’s work tree by default

(Presentation may evolve; install_mode rules above stay fixed.)

---

## 9. Non-goals (this fix)

- Replacing Career release/OS_INSTALL mechanics
- Mandating immediate deletion of legacy project `.ai/core` trees
- Requiring remote Universe for local attach
- Giving install_mode any Execution Guard authority by itself

---

## 10. Acceptance checklist

- [ ] Installer offers **exactly** the two primary choices; default is Universe
- [ ] Attached project can work with host READY and **no** private full core boot
- [ ] Standalone project can work with host **down**
- [ ] Host READY + standalone project ⇒ **prefer_boot HOST** unless overridden
- [ ] `install_binding.json` written on install/convert
- [ ] CLI/agent path documents host detect before work
- [ ] Missing binding ⇒ default attached + prefer host

---

## 11. Summary

```text
Default:  Universe host boots → attach project PWD
Optional: Project standalone install with pin (+ optional embed)
Always:   install_mode recorded; no silent dual authority
CLI:      detect host → ensure or standalone per binding → work
```
