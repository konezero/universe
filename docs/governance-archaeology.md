# Governance Archaeology — where each rule came from

Status: REFERENCE
Date: 2026-09-04
Source: excavated from the legacy `ai-career` repo (`.ai/diary/`,
`.ai/incident/`, `.ai/journal/`, `.ai/governance/`, `.ai/core/`).

Purpose: Universe's governance (the managed `AGENTS.md` / `CLAUDE.md`
blocks, execution-guard, Work Receipts, Authority / Assignment, the
reserved-command fast path, source-backed status) was **not designed
top-down**. Every cluster is an accreted response to a specific incident
or a repeated user correction while building GCS with GPT/Codex. This
document maps rule → origin so the [[rust-harness-is-the-target]] rebuild
can tell load-bearing principle from incident scar tissue.

## The substrate: four chat-environment constraints

From `ai-career/README.md` "Runtime Environment Assumptions" — treated as
runtime constraints, not user mistakes:

1. Chat title is not durable project identity.
2. Active context is finite and may compress or fall out of scope.
3. Long conversations drift toward the strongest recent work thread.
4. Role, authority, and task boundaries weaken over time unless re-aligned.

Almost every governance rule is a defense against one of these four.

## Origin events → governance clusters

### 1. "확인부터 해야지" — procedure over answer
**Origin:** 2026-06-29 diaries. The user kept interrupting mid-answer:
*"아니지. 그건 추론했잖아. 확인부터 해야지."* The realization: users don't
trust the result, they trust *the procedure that produced it*.
**Became:** Source Grounding · Intent-first Commands · pre-execution
verification · no-forced-inference proposal gate · "source-backed status
or `UNKNOWN`" (`RUNTIME_SOURCE_VERIFICATION`, `RUNTIME_STATUS_SOURCE_RULE`,
`RUNTIME_STATE_TRUST_GATE`, `PRE_EXECUTION_VERIFICATION`,
`NO_FORCED_INFERENCE_PROPOSAL_GATE`, `INTENT_FIRST_ROUTING_GATE`).
**In Universe now:** execution-guard's "immediate pre-execution
verification"; `OS_STATUS` reporting `UNKNOWN` / `NOT_PERFORMED` without
current Host evidence.

### 2. GCS AGENTS.md patch incident
**Origin:** 2026-06-30 (`.ai/diary/2026-06-30-gcs-agents-patch-incident.md`).
Read `AGENTS.md` through a GitHub connector, got **truncated** output,
used it as the whole file, replaced — silently dropped the trailing
operating rules. User caught it via commit diff.
**Became:** "big files are not whole-file-replaced from truncated
connector output"; large-file edits go through local Git or a Codex
patch workflow.
**In Universe now:** the Read-before-Edit / file-state discipline; why
edits are surgical and never blind rewrites.

### 3. INC-0001 — direct main write
**Origin:** 2026-07-01 (`.ai/incident/INC-0001-direct-main-write.md`).
The assistant committed **8 architecture-level Core changes straight to
`main`** instead of branch → PR. The user had said only *"진행"*, and the
assistant treated that as authorization for a direct main write.
**Became:** "architecture-level changes require branch + PR unless the
user explicitly authorizes a direct main write"; write-scope
classification (small note / Core policy / project source →
different rules); the incident archive itself
(`Incident → repeated pattern → policy candidate → user approval →
adopted policy`).
**In Universe now:** "if on the default branch, branch first"; "commit or
push only when the user asks"; the entire Work Receipt contract
(`INSTRUCTION_WORK_RECEIPT_CONTRACT`); the `prepare`/`consume` receipt
lifecycle.

### 4. Local Codex boundary incident
**Origin:** 2026-07-04 (`.ai/diary/2026-07-04-local-codex-boundary-incident.md`).
Local Codex attached the repo directly and treated the live discussion
context as execution authority, making Core-adjacent edits too early.
*"Recognition was mistaken for authority."* There was no root entrypoint
forcing a local agent to load the contracts before editing.
**Became:** a root `AGENTS.md` as the **local control-plane router** —
forces Boot Guard · Runtime Command Guard · Core Write Guard · Candidate
First · Source-Backed Status before repo work counts as executable.
"Remote connector safety ≠ local filesystem agent safety" — separate
hosts, separate pre-execution risks (`APP_RUNTIME_BOUNDARY_RULE`).
**In Universe now:** the whole managed `AGENTS.md` / `CLAUDE.md` binding
block; **"Mode and Role do not create authority"**; the Execution Guard
requiring a current scoped assignment + pre-execution verification;
`ROLE_MODE_AUTHORITY_GATE`, `RUNTIME_AUTHORITY_EXECUTION_BINDING`.

### 5. `@GitHub 메모싱크` misrouted
**Origin:** `.ai/governance/COMMAND_GOVERNANCE_POLICY.md`. A governance
command was resolved as a direct checkpoint write — skipping the
candidate list and user selection — because recent task context hijacked
it.
**Became:** the Global Command Resolver runs **before** Role / Mode / Task
resolution (`GLOBAL_COMMAND_RUNTIME_RULE`); `메모싱크` is
candidate-extraction-only (`extract → Candidate List → User Selection →
write only selected`).
**In Universe now:** the "Reserved Universe Command Fast Path" in
`AGENTS.md` (`#마스터모드` / `#컨덕터모드` / `#메모싱크` inspected on the
first input line before the Entry Order); memory-sync as passive
candidate extraction.

### 6. Conductor Wait — the bare "야"
**Origin:** 2026-07-09
(`.ai/diary/2026-07-09-chatgpt-conductor-wait-runtime-handoff-reflection.md`).
The user sent a bare *"야"*; the assistant inferred intent and answered
too early. The user was sending two separate messages.
**Became:** `COMMANDER_WAIT_BUFFER_RULE`,
`HEARTBEAT_WAITING_PURPOSE_GATE`, `USER_INTERRUPT_RUNTIME_RULE` —
"partial fragment is not a complete command; buffered context is not
authority".
**In Universe now:** mostly handled by Claude Code's turn model, but the
"wait for a clear yes before irreversible actions" discipline descends
from here. Candidate to *cut* in the Rust harness if the surface has
real turn boundaries.

### 7. Context contamination / compaction  ← the big one
**Origin:** `.ai/core/CONTEXT_MANAGEMENT_RUNTIME_FREEZE.md` + README
assumptions. *"Previous role, task, connector, and execution frames
remained active after they should have been released."* Failure modes
named directly: premature inference, context mixing, compressed state
loss, stale memory, topic bleed, role confusion. The user's two platform
asks: **(1) a non-compressed session-context area for runtime-critical
state; (2) a chat-title API so the visible surface shows the active
node/mode.**
**Became:** ai-career redefined as a *Context Management Runtime* — context
layers with different lifetimes (Session / Role / Mode / Task /
Execution); the Anchor model (`ANCHOR_TEMPORAL_COORDINATE`,
`ANCHOR_RESOLUTION`, `PROJECT_ANCHOR`); `AUTOMATIC_CONTINUITY_CONTRACT`;
the local continuity SQLite.
**In Universe now:** this is the **direct ancestor of Pull Forward
Anchor** — see [[universe-pull-forward-anchor-vision]] and
[[universe-origin-and-lineage]]. The Session Anchor, the Rust
reconnection host, the anchor-first session binding, the todo system —
all downstream of "don't make me repeat the context every time the window
compacts".

### 8. Runtime Overlay observation
**Origin:** 2026-06-29 journal
(`cross-session-context-alignment-observation.md`). A **fresh session**
given only the ai-career repo and a neutral question independently
described it as a governance repo and adopted its vocabulary and
boundaries. *"The repository provides a working interpretation frame, not
merely project information."*
**Became:** the deliberate choice to write governance as **prose
contracts** in `AGENTS.md` / `.ai/core/` rather than only as code — the
documents shape cold-boot behavior. (Explicitly framed as observable
behavior, never a platform-internal claim.)
**In Universe now:** the managed `CLAUDE.md` / `AGENTS.md` blocks, the
`.ai/skills/` packs, the "read the entry chain first" discipline, the
session-inject hook — see [[session-inject-hook-unmanaged-silent]].

### 9. Career / project memory boundary
**Origin:** 2026-06-29 GCS runtime reflection +
`.ai/governance/GOVERNANCE.md`. *"GCS owns project memory; ai-career only
receives reusable cross-project candidates."*
**Became:** Candidate → Verified → Deprecated promotion; "Career stores
reusable patterns only, not project-specific implementation"; the Memory
Hardpoint Policy (`memo → hypothesis → candidate → adopted`, user
approval required).
**In Universe now:** the memory candidate/adopted distinction;
`rag.record-decision` as a governed Action Gateway call;
`PROJECT_PROFILE_PURITY_RULE`.

### 10. The collaboration pattern itself
**Origin:** 2026-06-30
(`.ai/diary/2026-06-30-user-ai-collaboration-pattern.md`). *"User detects
direction and truth; AI turns it into structure; User corrects when it
drifts."* Design turns from one-line corrections:
- "That is Governance, not Runtime." → Governance / Runtime / Project
  separation
- "Memory is global. Role only changes triggers." → global contract vs
  role-specific trigger
- "Resume is role-scoped." → Resume as role-scoped restore
- "Facts only. Do not waste tokens." → Runtime Console output principle
**In Universe now:** the Governance / Runtime / Project layering in the
managed block; the "Facts only" console discipline; Conductor / Master /
Worker roles.

## Classification for the Rust harness diet

| Keep — substrate defenses, domain-general | Redesign lean — tool/incident scar tissue | Re-evaluate — environment-specific |
|---|---|---|
| Anchor model + context-layer lifetimes (#7) | branch+PR ceremony (#3 — a GitHub-connector artifact; local git differs) | remote-connector vs local-codex split (#4 — Claude Code is one surface) |
| source grounding / "UNKNOWN not inference" (#1) | whole-file-replace guard (#2 — connector truncation; N/A with real file tools) | chat-title-as-coordinate (#7 — no equivalent API surface) |
| candidate → verified promotion (#9) | the heavy `prepare`/`consume` Work Receipt (#3 — defense against connector writes) | Commander Wait "야" buffering (#6 — real turn boundaries handle it) |
| global-command-before-role resolution (#5) | | |
| "loaded context ≠ authority" (#4) | | |
| governance-as-prose for cold boot (#8) | | |

The keep column is the actual invariant core. Everything in the other two
columns earned its place against a specific failure that a from-scratch
Rust harness with proper tools and a single execution surface may not
reproduce.
