# universe-private monorepo split — plan

Date: 2026-09-06
Status: APPROVED — executing `career` first, then `design-canvas` / `rendezvous`, then `core`.
Decision (operator, 2026-09-06): split into **new repos, history dropped, fresh start**.
Scope: `career`, `design-canvas`, `rendezvous`, and `core` — four standalone repos.

Locked decisions (operator, 2026-09-06):
- §4 → **A**: `core` is a standalone repo; `career` owns the release pipeline
  (consumes the built Release DB). `core_release.py` moves to `core`
  (build-from-source lives with the source).
- §5 → target is **(d)** ship the CLI-host tooling in the Release DB; same-day
  unblock uses **(c)** absolute-path hook into `C:\workspace\universe\tools\`.
- §3 → **both `design-canvas` and `rendezvous` are kept and get full CLI-host
  bring-up.** `rendezvous` = actively used repo. `design-canvas` = the
  dogfooding project (native AI editor + Design Canvas — see
  [[dogfood-products-editor-and-canvas]]). Neither is dormant.
- Remotes → **local-only for now**; add GitHub later on request.

## 1. Why

An interactive managed Claude session (`session.new` / `POST /v1/terminals`)
cannot attach to any `universe-private/projects/*` project. The CLI is spawned
and stays alive, but the terminal pins at `MANAGED_SHELL_IDENTITY_MISSING` /
`SHELL_READY`, `channel_registered: false`, and is never a consumable Master.

Root cause chain:

1. The Release DB payload is **`.ai/**` only** — verified: 178 files, 0 non-`.ai`.
   The CLI-host / Claude-Code entry layer is **not** distributed.
2. `career` / `design-canvas` / `rendezvous` each have `.ai/` (LINKED or COPY)
   and `AGENTS.md`, but **no `CLAUDE.md`, no `.claude/settings.json`, no
   `SessionStart` hook, no `tools/`, no own `.git`**.
3. No `SessionStart` hook → `universe_session_inject_hook` never runs → no
   managed-shell **attach receipt** → `record_managed_attach` never fires →
   identity file never written → terminal stuck. (Also likely blocked on the
   folder-trust prompt.)
4. A clean per-project CLI-host install is impossible from a monorepo subdir:
   - server `setup_provider_hooks` hardcodes `repo_root = Path(__file__).parents[1]`
     (the `universe` repo) — it can only ever install into `universe`
   - `install_git_hooks` and the project-index assume `project_root == git root`
   - the hook shells `python -m tools.universe_session_inject_hook`; that `tools/`
     tree lives only in `C:\workspace\universe`

The `-p` resident Master (`prepare_project_master_session`) sidesteps all of
this — it injects its own temp `--mcp-config` and is driven by the room loop, no
PTY identity handshake — which is why that path booted `career` earlier and only
broke on the tool-permission deadlock. The interactive path is the one blocked
by the monorepo layout.

The Release DB / LINKED install is **not** implicated. `career`'s LINKED `.ai/`
(178 symlinks) boots end-to-end.

## 2. Current monorepo contents

| path | tracked files | leaves as |
|---|---|---|
| `projects/career` | 114 | repo `career` |
| `projects/design-canvas` | 6 | repo `design-canvas` |
| `projects/rendezvous` | 70 | repo `rendezvous` |
| `projects/navtive-editor` | 0 (empty, misspelled) | drop |
| `core/runtime-source/.ai/**` + `core/skill-source/**` | 353 | repo `core` |

`universe-private` remainder after the split: `AGENTS.md`, `README.md`,
`REPOSITORY_MANIFEST.md`, `universe-skills/`, `universe-node*.json`, its own
`.ai/`. Becomes an archive / can be retired separately.

## 3. Target layout

Four fresh repos as siblings of `universe` and `universe-private`:

```
C:\workspace\
  universe\            (unchanged — server, tools, UI, universe's own .ai)
  universe-private\    (archived after split)
  core\                <- core/runtime-source + core/skill-source + release build tooling
  career\              <- projects/career
  design-canvas\       <- projects/design-canvas
  rendezvous\          <- projects/rendezvous
```

Each new repo:
- copy the subtree verbatim (no filter-repo, no history) → `git init` →
  one import commit
- keep existing `AGENTS.md`
- **add `CLAUDE.md`** = `# <name>\n\n@AGENTS.md`
- **add `.claude/settings.json`** with the `SessionStart` hook
- **add `.gitignore`** (carry the `.ai/runtime/**` ignore lines the projects
  already use: `anchor_store/ task_frames/ continuity/ tmp/ state/`)
- CLI-host hook tooling — see §5
- `.ai/` runtime — see §6

## 4. Open decision: core ↔ career

Operator note: *"career manages the build source, so core goes together with the
career side."* — conflicts with "core as its own repo". Resolve one of:

- **A. `core` standalone; `career` owns the release pipeline.** `core` holds
  `runtime-source/.ai/**` + `skill-source/`. `career` (or a small `release/`
  tree in it) carries the `core_release.py build` driver and points at a local
  `core` checkout via `--source-repo`. Clean separation, matches the checkbox.
- **B. Fold `core` into `career`** as `career/runtime-source/` +
  `career/skill-source/`. One repo to clone for anyone doing runtime work.
  Matches "core goes with career". Loses core as an independently versioned unit.

Recommend **A** — the runtime source is consumed by every project and by the
Rust harness later; it should not be gated behind cloning `career`.

Also decide: does `core_release.py` (currently in `universe/tools/`) move to
`core` or to `career`? It reads `core/runtime-source/.ai/` and writes a Release
DB. Recommend it lives in `core` (build-from-source belongs with the source);
`career` consumes the built Release DB via the existing import/propose/apply
routes.

## 5. CLI-host hook tooling — the cross-repo dependency

The `SessionStart` hook needs `universe_session_inject_hook.py` and its imports
(`universe_file_index`, `universe_project_index_git_hook`, the mode-change
scripts). Today only in `universe/tools/`. Options:

- **(a) Vendor a copy** into each new repo's `tools/` (interim, pragmatic).
  Drift risk; needs a sync story.
- **(b) `universe-cli-host` package** — extract the hook + deps, `pip install`
  (editable in dev). Each repo's hook command becomes
  `python -m universe_cli_host.session_inject_hook ...`. Cleanest for many repos.
- **(c) Absolute path** into `C:\workspace\universe\tools\` from each repo's
  `.claude/settings.json`. Zero copy, but dev-machine-only and breaks if
  `universe` moves. Acceptable only as a stopgap on this one machine.
- **(d) Ship it in the Release DB** under `.ai/distribution/cli-host/` (or lift
  the "payload is `.ai/` only" rule to include a `tools/` prefix). The
  CLI-host layer then installs with the runtime. Architecturally correct,
  aligns with "the Release DB should carry the CLI host", but it is the biggest
  change and needs `plan_project_release_lifecycle` / `ReleaseRuntime` to manage
  a second managed prefix.

Recommend **(d) as the target**, **(c) as the same-day stopgap** to unblock
`career` once it is a repo, **(b)** if the split proves out and more repos need
it before (d) lands.

Also required regardless: the server's `setup_provider_hooks` must stop
hardcoding the `universe` repo root — take `project_root` + `project_id` from
the request (or a new `POST /v1/projects/{id}/cli-host/install` action) so the
control plane can provision any registered project.

## 6. `.ai/` runtime on the moved repos

- **LINKED is portable.** Symlink targets are absolute
  (`\\?\C:\Users\...\Universe\runtime-store\objects\sha256\<sha>`) and the pin's
  `store_root` is absolute. Moving the project directory does **not** break
  resolution. Verified on `career`.
- `career` = LINKED @ `core-1750028fcddd` (178 symlinks). `design-canvas` /
  `rendezvous` = COPY (from the fleet-repin side effect). All keep their `.ai/`
  as-is through the copy.
- After the move, re-point the project registration (§7) so resident-boot
  provenance (`project_release_selection`, pin `release_id`/`source_commit`)
  still resolves. The pin file itself is unchanged.
- `.ai/runtime/{state,tmp,session_store,anchor_store,continuity,task_frames}`
  stay real local dirs (gitignored) — unaffected.

## 7. Universe control-plane impact

Per moved project:

1. **Re-point `project_root`.** `project_connection.project_root` currently points
   at `C:\workspace\universe-private\projects\<name>`. There is **no supported
   "move project" route**: `register_project` raises `PROJECT_ID_ALREADY_BOUND`
   when the root differs, and `delete_project` is a bare
   `DELETE FROM project_connection` — several child tables cascade, so
   detach+re-add would drop release selections / seed assets / feature nodes.
   Safe path: **server stopped → direct
   `UPDATE project_connection SET project_root = ? WHERE project_id = ?` in
   `universe.sqlite3` → server started.** All child rows (keyed by `project_id`)
   survive. Consider adding a real `POST /v1/projects/{id}/rebind-root` route so
   this is not a manual DB edit.
2. **Mode anchors** — `.ai/runtime/state/project_runtime.sqlite3` travels with
   the `.ai/` tree (it is inside the project). No server-side migration; just
   verify `mode_current_anchor` still reads after the move.
3. **Reconnection-host registry** — `~/AppData/Local/Universe/reconnection-hosts/anchor-*.json`
   records `cwd`. Any pre-split hosts for these projects are already dead;
   nothing to migrate. New sessions register the new path.
4. **`git` hooks / project index** — only meaningful once each repo has its own
   `.git`; run the (fixed, non-hardcoded) `setup_provider_hooks` against each.
5. **Discovery / seed** — `POST /v1/projects/<id>/discovery-dispatch` still keys
   on `project_id`; unaffected by the path change. It only becomes useful once
   the project can host a live Master (the whole point of this split).

## 8. Migration sequence

Do `career` first, end to end, prove the pattern, then batch the other two.

1. **Decide §4 and §5.** No copying before that.
2. Fix server `setup_provider_hooks` to accept an arbitrary `project_root` /
   add `POST /v1/projects/{id}/cli-host/install`. Restart via the proper path
   (`python tools/universe_server.py stop` / `start`).
3. `career`:
   a. copy the subtree **preserving symlinks** (`shutil.copytree(..., symlinks=True)`
      or `robocopy /E /SL`) → `C:\workspace\career`. LINKED `.ai/` symlinks are
      absolute machine-store paths and stay valid.
   b. `cd C:\workspace\career && git init && git add -A && git commit`
   c. add `CLAUDE.md`, `.claude/settings.json` (stopgap (c): hook command =
      `python C:\workspace\universe\tools\universe_session_inject_hook.py
      --repo-root . --provider CLAUDE --from-stdin --trigger session_start`),
      `.gitignore`
   d. server stop → `UPDATE project_connection SET project_root` → server start
      (§7.1)
   e. `session.new` career Master → confirm `CLI_RUNNING` +
      `channel_registered: true` + a MASTER anchor-session bound
   f. `discovery-dispatch` → confirm the Master consumes the seed instruction
4. `design-canvas`, `rendezvous`: repeat 3a–3e (full CLI-host bring-up, both are
   active). Seed discovery for `rendezvous` too (no seed today); `design-canvas`
   as needed.
5. `core`: copy per §4, `git init`, move the release-build driver, rebuild a
   Release DB from the new `core` checkout, confirm import/propose/apply still
   works against `career`.
6. `universe-private`: drop `projects/` and `core/`, leave a `README` pointer to
   the new repos. Retire separately.

## 9. Rollback

Nothing is destructive until step 6. Through step 5 the original
`universe-private` working tree is untouched (copies only). If a moved repo
misbehaves: unregister the new path, re-register the old
`universe-private/projects/<name>` path, carry on. Keep `universe-private`
intact until all four new repos have run a Master session successfully.

## 10. Open questions

- §4 A vs B (core standalone vs folded into career) + where `core_release.py`
  lives.
- §5 which hook-tooling option, and whether the `setup_provider_hooks` fix is
  in-scope now or the stopgap (c) carries `career` until the Rust harness.
- New repo remotes — GitHub? local-only for now? (`universe-private` remote
  status unknown; `career` no-arg flows don't need one.)
- `universe-private/.ai/` + `universe-skills/` — does anything still consume
  them, or do they retire with the shell repo?
- ~~`design-canvas` / `rendezvous` active or dormant?~~ RESOLVED: both active,
  both get full bring-up.

## 11. Out of scope

- Rewriting history / preserving blame (operator chose fresh start).
- The Rust harness's own CLI-host provisioning — this plan just stops the
  Python side from being the blocker ([[rust-harness-is-the-target]]).
- Converting `universe` itself (it is already its own repo with a working
  CLI host).
- `navtive-editor` — empty, dropped.
