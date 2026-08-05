---
name: session-ref-inject
description: Push provider session_ref into Universe Supervisor + multi-room slot without Session Observatory (harness boot path).
---

# Session-ref inject

Invocation class: `UNIVERSE_PRODUCT_SESSION`

Harness boot path for **meaning continuity**: register a provider session
coordinate in the Local Session Supervisor, optionally set it as the
`(node, mode)` default, and attach it to a multi-room slot. Does **not** create
authority, Execution Assignment, write scope, or Current Anchor.

Observatory remains one UX entry; this Skill is first-class.

## When to use

- External harness (Claude / Codex / Grok CLI or Desktop) already has a live or
  resumable `session_ref` / thread id.
- Operator wants Universe Project/Boss/Meeting room to resume that conversation
  without hunting Session Observatory.
- **After MODE_CHANGE** or harness `SessionStart` (automatic best-effort path).

## Automatic path (MODE_CHANGE / SessionStart hook)

Preferred for other-harness boot: do **not** require Observatory.

```powershell
python tools/universe_session_inject_hook.py `
  --repo-root . `
  --trigger mode_change
```

Claude SessionStart (stdin JSON with `session_id`):

```powershell
python tools/universe_session_inject_hook.py --repo-root . --from-stdin --trigger session_start
```

Codex with env:

```powershell
# CODEX_THREAD_ID already set by Desktop/CLI
python tools/universe_session_inject_hook.py --repo-root . --provider CODEX --trigger mode_change
```

Hook outcomes (JSON on stdout): `INJECTED` | `SKIPPED` | `OFFLINE` | `DRY_RUN` |
`INJECT_FAILED`. Default exit code is **0** so Mode/boot is never blocked.
Use `--strict` only in CI. Optional `--update-session-md` patches observation
lines (`Last Provider*`) without granting authority.

Project wiring: `.claude/settings.json` → `SessionStart` command hook.

## Manual route

Local Universe service must be running.

### HTTP

```text
POST /v1/sessions/inject
{
  "project_id": "<project id>",
  "room_type": "PROJECT",
  "slot_role": "MASTER",
  "provider": "CODEX",
  "session_ref": "<provider thread or session id>",
  "make_default": true
}
```

Aliases: `provider_session_ref` == `session_ref`. Optional `room_id` instead of
`project_id`. Optional `node` / `mode` (default node=`project_id`, mode=`MASTER`).

`make_default` defaults to true only for PROJECT + MASTER; otherwise false.

### CLI

```powershell
python tools/universe_server.py inject-session `
  --project-id <project_id> `
  --provider CODEX `
  --provider-session-ref <thread_id>
```

Codex Desktop may omit the ref and supply `CODEX_THREAD_ID` in the environment.

Seed file (optional):

```powershell
python tools/universe_server.py inject-session --seed-file path\to\seed.json
```

```json
{
  "project_id": "proj_x",
  "provider": "CLAUDE",
  "session_ref": "sess-..."
}
```

### What the server does

1. Stable Supervisor `session_id` = `session_` + sha256(node, mode, provider, ref)[:24]
2. `register_session` (idempotent on same identity)
3. Optional `set_default` for the `(node, mode)` pointer
4. Multi-room `inject_session_ref` → room slot attach + bridge_line

Response includes `supervisor_session`, `binding`, `bridge_line`, and
`resident_runtime_reload` (`REQUIRED` when default pointer moved).

## After inject

- `GET /v1/supervisor/sessions?node=<project_id>&mode=MASTER`
- `GET /v1/rooms/<room_id>` — bindings + bridge_line
- Project Master prepare/send should honor attach (Host session_ref invalidate
  is a separate S1 follow-up if still partial)

## Non-goals

- Full provider transcript import
- Authority or write scope from inject alone
- Replacing RESUME compressed_context (meaning vs timeline are different layers)
- Blocking MODE_CHANGE or harness start when Universe is offline
